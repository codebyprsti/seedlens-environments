#!/usr/bin/env python3
"""
Process KML files: match DB file_name -> location_id, rewrite <name> crop tokens, save as <location_id>.kml.

Requires env: DB_NAME, DB_USER, DB_PASS, DB_HOST; optional DB_PORT (default 5432).

Optional: if python-dotenv is installed, loads `.env` from the repo root (same as core.config).

KML parsing: stdlib first; on failure reads **raw bytes** into **lxml** so the XML
`encoding="..."` declaration is honored (avoids UTF-8 mis-read as cp1252 / mojibake in
`<name>`). Then sanitizes illegal chars and unescaped `&` (outside CDATA); last resort
lxml recover on Unicode text. Requires **lxml** (see requirements.txt).

Optional paths: KML_INPUT_DIR, KML_OUTPUT_DIR override the defaults below.

Run:
  python process_kml_and_rename.py
"""

from __future__ import annotations

import codecs
import io
import logging
import os
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

import psycopg2

# Encoding in XML declaration (ASCII-only value; scan first bytes only)
_XML_ENCODING_RE = re.compile(br'encoding\s*=\s*["\']([^"\']+)["\']', re.I)

# XML 1.0 disallows these control chars (except tab, LF, CR).
_ILLEGAL_XML_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
# Bare & must be an entity; do not touch valid &#...; &copy; &amp; etc.
_UNESCAPED_AMP_RE = re.compile(
    r"&(?!(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]*);)",
    re.IGNORECASE,
)
_CDATA_SPLIT_RE = re.compile(r"(<!\[CDATA\[.*?\]\]>)", re.DOTALL)

# ---------------------------------------------------------------------------
# Paths (Windows-friendly; override with env if needed)
# ---------------------------------------------------------------------------
INPUT_DIR = os.environ.get(
    "KML_INPUT_DIR",
    r"C:\Users\madan\Downloads\InGeoTech_100",
)
OUTPUT_DIR = os.environ.get(
    "KML_OUTPUT_DIR",
    r"C:\Users\madan\Downloads\InGeoTech_100\output",
)

KML_NS = "http://www.opengis.net/kml/2.2"
# Stable serialization for common KML default namespace
ET.register_namespace("", KML_NS)

# Letter-bearing tokens first (longest first). UNICODE: \b respects Unicode letters so e.g. "Ž"
# touching ASCII digits does not create a false boundary for standalone "24".
_CROP_LETTER_TOKENS = re.compile(
    r"\busrh-24\b|\bus-24\b|\busrh24\b|\bus24\b|\bu24\b",
    re.IGNORECASE | re.UNICODE,
)

# Standalone ASCII "24" only when not adjacent to Unicode "word" chars OR common
# superscript / modifier digits (¹²³, ⁰–⁹, ₀–₉) so names like "ŽšÍ ¹¾²ªÍª" are untouched.
_SUP_DIGITS = (
    "\u00BC\u00BD\u00BE"  # ¼ ½ ¾
    "\u00B9\u00B2\u00B3"  # ¹ ² ³
    "\u2070\u2071\u2074\u2075\u2076\u2077\u2078\u2079"  # superscript digits
    "\u2080\u2081\u2082\u2083\u2084\u2085\u2086\u2087\u2088\u2089"  # subscript digits
)
_CROP_STANDALONE_24 = re.compile(
    rf"(?<![\w{_SUP_DIGITS}])24(?![\w{_SUP_DIGITS}])",
    re.UNICODE,
)

REPLACE_WITH = "Hybrid-xxxx"

SQL_DISTINCT_LOCATION = """
SELECT DISTINCT location_id
FROM operations.crop_indices
WHERE file_name = %s
"""


def _local_tag(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def normalize_file_name_for_db(basename: str) -> str:
    return basename.strip().replace("+", " ")


def connect_db():
    required = ("DB_NAME", "DB_USER", "DB_PASS", "DB_HOST")
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise RuntimeError(
            "Missing required environment variables: "
            + ", ".join(missing)
            + ". Set DB_NAME, DB_USER, DB_PASS, DB_HOST (and optional DB_PORT)."
        )
    port = os.environ.get("DB_PORT", "5432")
    return psycopg2.connect(
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASS"],
        host=os.environ["DB_HOST"],
        port=port,
    )


def fetch_distinct_location_ids(conn, file_name: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(SQL_DISTINCT_LOCATION, (file_name,))
        rows = cur.fetchall()
    return [str(r[0]) for r in rows if r[0] is not None]


def _clean_placemark_name_text(text: str) -> str:
    """
    Remove mojibake-like junk in <name>: orphan combining marks (e.g. U+036A ͪ, U+036E ͮ),
    invisible format chars, tidy whitespace. Keeps real letters + marks that follow letters.
    """
    if not text:
        return text
    out: list[str] = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat == "Cf":
            continue
        if cat in ("Mn", "Mc", "Me"):
            prev = out[-1] if out else None
            if prev is None:
                continue
            pcat = unicodedata.category(prev)
            if pcat[0] not in ("L", "N", "M"):
                continue
            out.append(ch)
            continue
        out.append(ch)
    s = "".join(out)
    s = re.sub(r"\s+", " ", s).strip()
    while s.startswith(","):
        s = s[1:].lstrip()
    while s.endswith(","):
        s = s[:-1].rstrip()
    return s


def replace_in_name_text(text: str | None) -> tuple[str | None, bool]:
    if text is None:
        return None, False
    # No Unicode normalization — preserves composed characters (Ž, Í, superscripts, etc.).
    new = _CROP_LETTER_TOKENS.sub(REPLACE_WITH, text)
    new = _CROP_STANDALONE_24.sub(REPLACE_WITH, new)
    return new, new != text


def _codec_canonical(name: str) -> str | None:
    n = (name or "").strip()
    if not n:
        return None
    for candidate in (n, n.lower(), n.replace("_", "-")):
        try:
            return codecs.lookup(candidate).name
        except LookupError:
            continue
    return None


def _xml_declared_encoding_bytes(data: bytes) -> str | None:
    head = data[:4000]
    m = _XML_ENCODING_RE.search(head)
    if not m:
        return None
    try:
        raw = m.group(1).decode("ascii")
    except UnicodeDecodeError:
        return None
    return _codec_canonical(raw)


def _read_kml_text_from_bytes(data: bytes) -> str:
    """
    Decode KML bytes without mis-reading UTF-8 as cp1252 (classic mojibake: ¬¿, ª, etc.).

    Never use cp1252/latin-1 before a permissive UTF-8 pass: any invalid UTF-8 byte
    used to make the old code fall through to cp1252 and corrupt the whole file.
    """
    if not data:
        return ""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")

    for enc in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass

    decl = _xml_declared_encoding_bytes(data)
    if decl:
        try:
            return data.decode(decl)
        except (UnicodeDecodeError, LookupError):
            pass

    # Prefer UTF-8 with replacements over interpreting the whole blob as Windows-1252
    return data.decode("utf-8", errors="replace")


def _read_kml_text(path: str) -> str:
    with open(path, "rb") as f:
        return _read_kml_text_from_bytes(f.read())


def _fix_unescaped_ampersands_outside_cdata(text: str) -> str:
    parts = _CDATA_SPLIT_RE.split(text)
    out: list[str] = []
    for part in parts:
        if part.startswith("<![CDATA[") and part.endswith("]]>"):
            out.append(part)
        else:
            out.append(_UNESCAPED_AMP_RE.sub("&amp;", part))
    return "".join(out)


def _sanitize_kml_xml_text(text: str) -> str:
    text = _ILLEGAL_XML_CHARS_RE.sub("", text)
    text = _fix_unescaped_ampersands_outside_cdata(text)
    return text


def _lxml_bytes_to_elementtree(data: bytes) -> ET.ElementTree:
    """Parse raw file bytes so lxml applies the XML declaration encoding (avoids wrong manual decode)."""
    from lxml import etree as letree

    parser = letree.XMLParser(
        recover=True,
        huge_tree=True,
        remove_blank_text=False,
        resolve_entities=False,
    )
    doc = letree.parse(io.BytesIO(data), parser)
    root_l = doc.getroot()
    if root_l is None:
        raise ValueError("empty XML tree")
    out_b = letree.tostring(
        root_l,
        encoding="utf-8",
        xml_declaration=False,
        pretty_print=False,
    )
    return ET.ElementTree(ET.fromstring(out_b))


def _parse_kml_with_lxml_recover_unicode(text: str) -> ET.ElementTree:
    from lxml import etree as letree

    parser = letree.XMLParser(recover=True, huge_tree=True, remove_blank_text=False)
    root_l = letree.parse(io.BytesIO(text.encode("utf-8")), parser).getroot()
    out_b = letree.tostring(
        root_l,
        encoding="utf-8",
        xml_declaration=False,
        pretty_print=False,
    )
    root = ET.fromstring(out_b)
    return ET.ElementTree(root)


def parse_kml_path(input_path: str) -> ET.ElementTree:
    """
    Parse KML with stdlib first; on failure read raw bytes and let lxml decode via
    the XML declaration, then sanitize/repair paths. Avoids UTF-8 text mis-decoded as cp1252.
    """
    try:
        return ET.parse(input_path)
    except (ET.ParseError, UnicodeDecodeError):
        pass

    with open(input_path, "rb") as f:
        data = f.read()

    try:
        return _lxml_bytes_to_elementtree(data)
    except ImportError:
        pass
    except Exception:
        pass

    text = _read_kml_text_from_bytes(data)
    text = _sanitize_kml_xml_text(text)
    try:
        return ET.ElementTree(ET.fromstring(text))
    except ET.ParseError as sanitized_err:
        try:
            return _parse_kml_with_lxml_recover_unicode(text)
        except ImportError:
            raise sanitized_err
        except Exception as lxml_err:
            raise sanitized_err from lxml_err


def modify_kml_name_tags(root: ET.Element) -> int:
    """Return count of <name> elements whose text was modified (cleanup and/or crop codes)."""
    changed = 0
    for elem in root.iter():
        if _local_tag(elem.tag) != "name":
            continue
        # Mixed content (<name> with child tags) — do not rewrite; avoids corrupting structure.
        if len(elem):
            continue
        if elem.text is None:
            continue
        raw = elem.text
        cleaned = _clean_placemark_name_text(raw)
        new_text, _ = replace_in_name_text(cleaned)
        final = new_text if new_text is not None else cleaned
        if final != raw:
            elem.text = final
            changed += 1
    return changed


def output_kml_path(output_dir: str, location_id: str) -> str:
    """Always `<location_id>.kml` — overwrites if it already exists (no _1, _2 suffixes)."""
    return os.path.join(output_dir, f"{location_id}.kml")


def process_one_file(
    conn,
    input_path: str,
    output_dir: str,
    stats: dict,
) -> None:
    basename = os.path.basename(input_path)
    normalized = normalize_file_name_for_db(basename)

    location_ids = fetch_distinct_location_ids(conn, normalized)
    if len(location_ids) == 0:
        stats["skipped_no_location"] += 1
        logging.debug("No location_id for file_name=%r (%s)", normalized, input_path)
        return

    if len(location_ids) > 1:
        stats["skipped_multiple"] += 1
        logging.warning(
            "Multiple location_ids for file_name=%r: %s — skipping %s",
            normalized,
            location_ids,
            input_path,
        )
        return

    stats["matched_db"] += 1
    location_id = location_ids[0]

    tree = parse_kml_path(input_path)
    root = tree.getroot()
    n_modified = modify_kml_name_tags(root)
    if n_modified:
        stats["modified_names"] += n_modified

    out_path = output_kml_path(output_dir, location_id)
    _write_kml_tree(tree, out_path)
    stats["renamed"] += 1
    logging.info("Wrote %s <- %s", out_path, input_path)


def _write_kml_tree(tree: ET.ElementTree, out_path: str) -> None:
    """Write KML; avoid default_namespace when the tree mixes qualified/unqualified tags."""
    kwargs = dict(encoding="utf-8", xml_declaration=True, method="xml")
    try:
        tree.write(out_path, default_namespace=KML_NS, **kwargs)
    except ValueError as e:
        if "default_namespace" not in str(e) and "non-qualified" not in str(e).lower():
            raise
        tree.write(out_path, **kwargs)


def _load_dotenv_if_present() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.is_file():
        load_dotenv(env_path)


def main() -> int:
    _load_dotenv_if_present()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    in_dir = INPUT_DIR
    out_dir = OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)

    if not os.path.isdir(in_dir):
        logging.error("Input directory does not exist: %s", in_dir)
        return 1

    kml_files = sorted(
        f
        for f in os.listdir(in_dir)
        if f.lower().endswith(".kml") and os.path.isfile(os.path.join(in_dir, f))
    )

    stats = {
        "processed": 0,
        "matched_db": 0,
        "renamed": 0,
        "skipped_no_location": 0,
        "skipped_multiple": 0,
        "modified_names": 0,
    }

    try:
        conn = connect_db()
    except Exception as e:
        logging.error("Database connection failed: %s", e)
        return 1

    try:
        for name in kml_files:
            stats["processed"] += 1
            path = os.path.join(in_dir, name)
            try:
                process_one_file(conn, path, out_dir, stats)
            except Exception as e:
                logging.exception("Failed processing %s: %s", path, e)
    finally:
        conn.close()

    print()
    print("Summary")
    print("-------")
    print(f"Processed files: {stats['processed']}")
    print(f"Matched in DB: {stats['matched_db']}")
    print(f"Renamed: {stats['renamed']}")
    print(f"Skipped (no location): {stats['skipped_no_location']}")
    print(f"Skipped (multiple location_ids): {stats['skipped_multiple']}")
    print(f"Modified <name> tags: {stats['modified_names']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
