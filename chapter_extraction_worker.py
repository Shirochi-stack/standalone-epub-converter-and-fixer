#!/usr/bin/env python3
"""
Chapter Extraction Worker - Runs chapter extraction in a separate process to prevent GUI freezing
"""

import sys
import os
import io
import json
import re
import zipfile
import time
import traceback
from pathlib import Path

def _configure_stream(stream):
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
            return stream
        except Exception:
            pass
    if hasattr(stream, "buffer"):
        return io.TextIOWrapper(stream.buffer, encoding="utf-8", errors="replace")
    return stream


sys.stdout = _configure_stream(sys.stdout)
sys.stderr = _configure_stream(sys.stderr)

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

BEAUTIFULSOUP_ENGINE = "beautifulsoup"


def _local_name(tag):
    return tag.rsplit('}', 1)[-1] if '}' in tag else tag


def _decode_text(data):
    for encoding in ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', errors='replace')


def _safe_filename(name, fallback='file'):
    import re
    stem, ext = os.path.splitext(os.path.basename(name or fallback))
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '_', stem).strip(' ._')
    ext = re.sub(r'[^A-Za-z0-9.]', '', ext or '')
    if not stem:
        stem = fallback
    return f"{stem[:120]}{ext[:20]}"


def _unique_name(existing, name):
    candidate = name
    stem, ext = os.path.splitext(name)
    counter = 2
    while candidate.lower() in existing:
        candidate = f"{stem}_{counter}{ext}"
        counter += 1
    existing.add(candidate.lower())
    return candidate


def _zip_join(base, href):
    import posixpath
    return posixpath.normpath(posixpath.join(base, href.replace('\\', '/')))


def _clean_metadata_text(value):
    if value is None:
        return ""
    return " ".join(str(value).split())


def _append_unique(values, value):
    value = _clean_metadata_text(value)
    if value and value not in values:
        values.append(value)


def _extract_text_metadata(root):
    metadata = {}
    field_map = {
        'title': 'title',
        'creator': 'creator',
        'language': 'language',
        'identifier': 'identifier',
        'publisher': 'publisher',
        'description': 'description',
        'subject': 'subject',
        'date': 'date',
        'rights': 'rights',
        'contributor': 'contributor',
        'coverage': 'coverage',
        'format': 'format',
        'relation': 'relation',
        'source': 'source',
        'type': 'type',
    }
    multi_value_keys = {
        'creator': 'creators',
        'contributor': 'contributors',
        'identifier': 'identifiers',
        'publisher': 'publishers',
        'subject': 'subject',
        'date': 'dates',
        'language': 'languages',
    }
    identifier_by_id = {}

    for elem in root.iter():
        local = _local_name(elem.tag)
        if local in field_map and elem.text and elem.text.strip():
            key = field_map[local]
            value = _clean_metadata_text(elem.text)
            attr_id = elem.attrib.get('id') or elem.attrib.get('{http://www.w3.org/XML/1998/namespace}id')
            if local == 'identifier' and attr_id:
                identifier_by_id[attr_id] = value
            list_key = multi_value_keys.get(key)
            if list_key:
                if list_key == key:
                    current = metadata.get(key)
                    if current is None:
                        metadata[key] = [value]
                    elif isinstance(current, list):
                        _append_unique(current, value)
                    elif current != value:
                        metadata[key] = [current, value]
                else:
                    values = metadata.setdefault(list_key, [])
                    _append_unique(values, value)
            metadata.setdefault(key, value)

        if local == 'meta':
            name = (elem.attrib.get('name') or '').strip().lower()
            prop = (elem.attrib.get('property') or '').strip().lower()
            content = _clean_metadata_text(elem.attrib.get('content') or elem.text)
            if not content:
                continue
            if name == 'cover':
                metadata['_cover_item_id'] = content
            elif name == 'calibre:series':
                metadata.setdefault('series', content)
            elif name == 'calibre:series_index':
                metadata.setdefault('series_index', content)
            elif prop == 'dcterms:modified':
                metadata.setdefault('modified', content)
            elif prop == 'belongs-to-collection':
                metadata.setdefault('series', content)
            elif prop == 'group-position':
                metadata.setdefault('series_index', content)

    unique_identifier = root.attrib.get('unique-identifier')
    if unique_identifier and identifier_by_id.get(unique_identifier):
        metadata['identifier'] = identifier_by_id[unique_identifier]

    identifiers = metadata.get('identifiers')
    if isinstance(identifiers, list):
        for identifier in identifiers:
            if identifier.upper().startswith('ISBN'):
                metadata.setdefault('isbn', identifier.split(':', 1)[-1].strip())
            elif re_match := re.search(r'\b(?:97[89][-\s]?)?\d(?:[-\s]?\d){8,12}[\dXx]\b', identifier):
                metadata.setdefault('isbn', re_match.group(0))
    return metadata


def _find_opf_path(zf):
    try:
        from xml.etree import ElementTree as ET
        container = ET.fromstring(zf.read('META-INF/container.xml'))
        for elem in container.iter():
            if _local_name(elem.tag) == 'rootfile':
                full_path = elem.attrib.get('full-path')
                if full_path:
                    return full_path
    except Exception:
        pass

    for name in zf.namelist():
        if name.lower().endswith('.opf'):
            return name
    raise RuntimeError('content.opf was not found in the EPUB')


def _manifest_and_spine(opf_bytes):
    from xml.etree import ElementTree as ET
    root = ET.fromstring(opf_bytes)
    manifest = {}
    spine = []

    for elem in root.iter():
        local = _local_name(elem.tag)
        if local == 'item':
            item_id = elem.attrib.get('id')
            href = elem.attrib.get('href')
            if item_id and href:
                manifest[item_id] = {
                    'href': href,
                    'media_type': elem.attrib.get('media-type', ''),
                    'properties': elem.attrib.get('properties', ''),
                }
        elif local == 'itemref':
            idref = elem.attrib.get('idref')
            if idref:
                spine.append(idref)

    return root, manifest, spine


def _resource_kind(media_type, zip_path):
    ext = os.path.splitext(zip_path.lower())[1]
    if media_type.startswith('image/') or ext in {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp'}:
        return 'images'
    if media_type == 'text/css' or ext == '.css':
        return 'css'
    if 'font' in media_type or ext in {'.ttf', '.otf', '.woff', '.woff2'}:
        return 'fonts'
    return None


def _is_document_item(item):
    media_type = item.get('media_type', '').lower()
    href = item.get('href', '').lower()
    return (
        media_type in {'application/xhtml+xml', 'text/html'}
        or href.endswith(('.html', '.htm', '.xhtml'))
    )


def _is_navigation_document_item(item):
    properties = set((item.get('properties') or '').lower().split())
    href_name = os.path.basename(item.get('href', '')).lower()
    return 'nav' in properties or href_name in {'nav.html', 'nav.htm', 'nav.xhtml'}


def _is_chapter_document_item(item):
    return _is_document_item(item) and not _is_navigation_document_item(item)


def _map_package_ref(value, source_zip_path, resource_map):
    import posixpath

    if not value or value.startswith(('#', 'data:', 'http://', 'https://', 'mailto:', 'tel:')):
        return value
    split_at = len(value)
    for marker in ('#', '?'):
        pos = value.find(marker)
        if pos != -1:
            split_at = min(split_at, pos)
    target = value[:split_at]
    suffix = value[split_at:]
    source_dir = posixpath.dirname(source_zip_path)
    absolute = posixpath.normpath(posixpath.join(source_dir, target.replace('\\', '/')))
    return resource_map.get(absolute, target) + suffix if absolute in resource_map else value


def _rewrite_references(html_content, doc_zip_path, resource_map):
    import re

    def map_ref(value):
        return _map_package_ref(value, doc_zip_path, resource_map)

    def repl(match):
        prefix, value, suffix = match.group(1), match.group(2), match.group(3)
        return f"{prefix}{map_ref(value)}{suffix}"

    return re.sub(r'((?:src|href)\s*=\s*["\'])([^"\']+)(["\'])', repl, html_content, flags=re.IGNORECASE)


def _rewrite_css_references(css_content, css_zip_path, resource_map):
    import re

    def repl(match):
        quote = match.group(1) or ''
        value = match.group(2).strip()
        mapped = _map_package_ref(value, css_zip_path, resource_map)
        return f"url({quote}{mapped}{quote})"

    return re.sub(r'url\(\s*([\'"]?)([^)\'"]+)\1\s*\)', repl, css_content, flags=re.IGNORECASE)


def _extract_title(html_content, fallback):
    import re
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_content, 'html.parser')
        for selector in ('title', 'h1', 'h2'):
            tag = soup.find(selector)
            if tag:
                title = ' '.join(tag.get_text(" ", strip=True).split())
                if title:
                    return title
    except Exception:
        pass

    for pattern in (
        r'<title[^>]*>(.*?)</title>',
        r'<h1[^>]*>(.*?)</h1>',
        r'<h2[^>]*>(.*?)</h2>',
    ):
        match = re.search(pattern, html_content, flags=re.IGNORECASE | re.DOTALL)
        if match:
            title = re.sub(r'<[^>]+>', '', match.group(1))
            title = ' '.join(title.split())
            if title:
                return title
    return fallback


def _fallback_extract_epub(epub_path, output_dir, progress_callback=None):
    import json
    import posixpath
    import shutil
    import zipfile

    os.makedirs(output_dir, exist_ok=True)
    source_sidecar = os.path.join(output_dir, 'source_epub.txt')
    with open(source_sidecar, 'w', encoding='utf-8') as f:
        f.write(os.path.abspath(epub_path))

    with zipfile.ZipFile(epub_path, 'r') as zf:
        opf_path = _find_opf_path(zf)
        opf_dir = posixpath.dirname(opf_path)
        opf_bytes = zf.read(opf_path)
        root, manifest, spine = _manifest_and_spine(opf_bytes)
        metadata = _extract_text_metadata(root)
        metadata.setdefault('title', os.path.splitext(os.path.basename(epub_path))[0])

        with open(os.path.join(output_dir, 'content.opf'), 'wb') as f:
            f.write(opf_bytes)

        resource_map = {}
        used_names = {'images': set(), 'css': set(), 'fonts': set()}
        resource_items = list(manifest.values())
        resources_to_copy = []
        for index, item in enumerate(resource_items, 1):
            href = item.get('href', '')
            zip_path = _zip_join(opf_dir, href)
            if zip_path not in zf.namelist():
                continue
            kind = _resource_kind(item.get('media_type', '').lower(), zip_path)
            if not kind:
                continue
            if progress_callback:
                progress_callback(f"Extracting resources {index}/{len(resource_items)}")
            out_name = _unique_name(used_names[kind], _safe_filename(zip_path, kind))
            rel_name = f"{kind}/{out_name}"
            resource_map[zip_path] = rel_name
            resources_to_copy.append((kind, zip_path, out_name, rel_name))

        for kind, zip_path, out_name, rel_name in resources_to_copy:
            out_dir = os.path.join(output_dir, kind)
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, out_name)
            if kind == 'css':
                css_text = _decode_text(zf.read(zip_path))
                css_text = _rewrite_css_references(css_text, zip_path, resource_map)
                with open(out_path, 'w', encoding='utf-8') as dst:
                    dst.write(css_text)
            else:
                with zf.open(zip_path) as src, open(out_path, 'wb') as dst:
                    shutil.copyfileobj(src, dst)

        cover_item_id = metadata.get('_cover_item_id')
        if not cover_item_id:
            for item_id, item in manifest.items():
                if 'cover-image' in (item.get('properties') or '').split():
                    cover_item_id = item_id
                    break
        if not cover_item_id:
            for item_id, item in manifest.items():
                href = item.get('href', '')
                if _resource_kind(item.get('media_type', '').lower(), href) == 'images' and os.path.basename(href).lower().startswith(('cover', 'front')):
                    cover_item_id = item_id
                    break
        if cover_item_id in manifest:
            cover_zip_path = _zip_join(opf_dir, manifest[cover_item_id].get('href', ''))
            cover_rel = resource_map.get(cover_zip_path, '')
            if cover_rel:
                metadata['cover_image'] = os.path.basename(cover_rel)
                metadata['cover_href'] = manifest[cover_item_id].get('href', '')
                metadata['cover_item_id'] = cover_item_id
        metadata.pop('_cover_item_id', None)

        doc_ids = [item_id for item_id in spine if item_id in manifest and _is_chapter_document_item(manifest[item_id])]
        if not doc_ids:
            doc_ids = [item_id for item_id, item in manifest.items() if _is_chapter_document_item(item)]

        chapters = []
        used_docs = set()
        total_docs = len(doc_ids)
        for number, item_id in enumerate(doc_ids, 1):
            item = manifest[item_id]
            href = item.get('href', '')
            zip_path = _zip_join(opf_dir, href)
            if zip_path not in zf.namelist():
                continue
            if progress_callback:
                progress_callback(f"Processing chapters {number}/{total_docs}")
            content = _decode_text(zf.read(zip_path))
            content = _rewrite_references(content, zip_path, resource_map)

            source_name = _safe_filename(zip_path, f'chapter_{number:04d}.html')
            if not source_name.lower().endswith(('.html', '.htm', '.xhtml')):
                source_name += '.html'
            out_name = _unique_name(used_docs, source_name)
            out_path = os.path.join(output_dir, out_name)
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(content)

            chapters.append({
                'num': number,
                'title': _extract_title(content, f"Chapter {number}"),
                'filename': href,
                'file_size': len(content.encode('utf-8')),
                'has_images': 'src=' in content.lower(),
                'content_hash': '',
            })

        with open(os.path.join(output_dir, 'metadata.json'), 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        with open(os.path.join(output_dir, 'chapters_full.json'), 'w', encoding='utf-8') as f:
            json.dump(chapters, f, ensure_ascii=False)

        return metadata, chapters

def run_chapter_extraction(epub_path, output_dir, progress_callback=None):
    """
    Run chapter extraction in this worker process
    
    Args:
        epub_path: Path to EPUB file
        output_dir: Output directory for extracted content
        progress_callback: Callback function for progress updates (uses print for IPC)
    
    Returns:
        dict: Extraction results including chapters and metadata
    """
    try:
        # Honor OUTPUT_DIRECTORY override (keep leaf folder)
        try:
            override_dir = os.getenv("OUTPUT_DIRECTORY")
            if override_dir:
                override_dir = os.path.abspath(override_dir)
                leaf = os.path.basename(os.path.abspath(output_dir)) or "output"
                abs_output = os.path.abspath(output_dir)
                if not os.path.commonpath([abs_output, override_dir]).startswith(override_dir):
                    output_dir = os.path.join(override_dir, leaf)
                else:
                    output_dir = abs_output
        except Exception as e:
            print(f"[WARNING] OUTPUT_DIRECTORY override failed: {e}", flush=True)
        # Suppress XML parsing warnings when BeautifulSoup is available.
        try:
            import warnings
            from bs4 import XMLParsedAsHTMLWarning
            warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
        except ImportError:
            pass
        
        # Import here to avoid loading heavy modules until needed. If the
        # original extractor module is not present, use the built-in fallback
        # so this folder remains a true standalone bundle.
        try:
            import Chapter_Extractor
        except ImportError:
            Chapter_Extractor = None
        
        # Create progress callback that prints to stdout for IPC
        def worker_progress_callback(message):
            # Use special prefix for progress messages
            print(f"[PROGRESS] {message}", flush=True)
        
        os.environ["EXTRACTION" + "_MODE"] = BEAUTIFULSOUP_ENGINE
        
        # Open EPUB and extract chapters
        print(f"[INFO] Starting extraction of: {epub_path}", flush=True)
        print(f"[INFO] Output directory: {output_dir}", flush=True)
        print("[INFO] Extraction engine: BeautifulSoup", flush=True)
        
        # Create output directory early (after override)
        os.makedirs(output_dir, exist_ok=True)

        if Chapter_Extractor is None:
            print("[INFO] Chapter_Extractor module not found; using standalone fallback extractor", flush=True)
            metadata, chapters = _fallback_extract_epub(
                epub_path,
                output_dir,
                progress_callback=worker_progress_callback,
            )
            print(f"[INFO] Extracted metadata: {list(metadata.keys())}", flush=True)
        else:
            with zipfile.ZipFile(epub_path, 'r') as zf:
                # Extract metadata first
                metadata = Chapter_Extractor._extract_epub_metadata(zf)
                print(f"[INFO] Extracted metadata: {list(metadata.keys())}", flush=True)
                
                # Extract chapters using module-level function
                chapters = Chapter_Extractor.extract_chapters(zf, output_dir, progress_callback=worker_progress_callback)
            
                print(f"[INFO] Extracted {len(chapters)} chapters", flush=True)
            
                # The extract_chapters method already handles OPF sorting internally
                # Just log if OPF was used
                opf_path = os.path.join(output_dir, 'content.opf')
                if os.path.exists(opf_path):
                    print(f"[INFO] OPF file available for chapter ordering", flush=True)
            
                # CRITICAL: Save the full chapters with body content!
                # This is what the main process needs to load
                chapters_full_path = os.path.join(output_dir, "chapters_full.json")
                try:
                    with open(chapters_full_path, 'w', encoding='utf-8') as f:
                        json.dump(chapters, f, ensure_ascii=False)
                    print(f"[INFO] Saved full chapters data to: {chapters_full_path}", flush=True)
                except Exception as e:
                    print(f"[WARNING] Could not save full chapters: {e}", flush=True)
                    # Fall back to saving individual files
                    for chapter in chapters:
                        try:
                            chapter_file = f"chapter_{chapter['num']:04d}_{chapter.get('filename', 'content').replace('/', '_')}.html"
                            chapter_path = os.path.join(output_dir, chapter_file)
                            with open(chapter_path, 'w', encoding='utf-8') as f:
                                f.write(chapter.get('body', ''))
                            print(f"[INFO] Saved chapter {chapter['num']} to {chapter_file}", flush=True)
                        except Exception as ce:
                            print(f"[WARNING] Could not save chapter {chapter.get('num')}: {ce}", flush=True)
            
        print(f"[INFO] Extracted {len(chapters)} chapters", flush=True)
        
        # Return results as JSON for IPC
        result = {
            "success": True,
            "chapters": len(chapters),
            "metadata": metadata,
            "chapter_info": [
                {
                    "num": ch.get("num"),
                    "title": ch.get("title"),
                    "has_images": ch.get("has_images", False),
                    "file_size": ch.get("file_size", 0),
                    "content_hash": ch.get("content_hash", "")
                }
                for ch in chapters
            ]
        }
            
        # Output result as JSON
        print(f"[RESULT] {json.dumps(result)}", flush=True)
        return result
            
    except Exception as e:
        # Send error information
        error_info = {
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }
        print(f"[ERROR] {str(e)}", flush=True)
        print(f"[RESULT] {json.dumps(error_info)}", flush=True)
        return error_info


def main():
    """Main entry point for worker process"""
    
    # Parse command line arguments
    if len(sys.argv) < 3:
        print("[ERROR] Usage: chapter_extraction_worker.py <epub_path> <output_dir>", flush=True)
        sys.exit(1)
    
    epub_path = sys.argv[1]
    output_dir = sys.argv[2]
    
    # Validate inputs
    if not os.path.exists(epub_path):
        print(f"[ERROR] EPUB file not found: {epub_path}", flush=True)
        sys.exit(1)
    
    # Honor OUTPUT_DIRECTORY override for CLI entry as well
    try:
        override_dir = os.getenv("OUTPUT_DIRECTORY")
        if override_dir:
            override_dir = os.path.abspath(override_dir)
            leaf = os.path.basename(os.path.abspath(output_dir)) or "output"
            abs_output = os.path.abspath(output_dir)
            if not os.path.commonpath([abs_output, override_dir]).startswith(override_dir):
                output_dir = os.path.join(override_dir, leaf)
            else:
                output_dir = abs_output
    except Exception as e:
        print(f"[WARNING] OUTPUT_DIRECTORY override failed: {e}", flush=True)

    # Create output directory if needed
    os.makedirs(output_dir, exist_ok=True)
    
    # Run extraction
    result = run_chapter_extraction(epub_path, output_dir)
    
    # Exit with appropriate code
    sys.exit(0 if result.get("success", False) else 1)


if __name__ == "__main__":
    try:
        from shutdown_utils import run_cli_main
    except ImportError:
        def run_cli_main(func):
            return func()
    def _main():
        # Ensure freeze support for Windows frozen exe
        try:
            import multiprocessing
            multiprocessing.freeze_support()
        except Exception:
            pass
        main()
        return 0
    run_cli_main(_main)
