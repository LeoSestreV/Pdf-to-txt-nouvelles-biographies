"""
engine.py — PDF extraction engine for Nouvelle Biographie Nationale volumes.

Extracts individual biography entries from NBN PDF volumes using font/layout
analysis with PyMuPDF (fitz). Handles:
- Detection of biography headers (NAME in ALL CAPS, with or without particles)
- Page header/footer removal (using font size + position)
- Section letter detection (A, B, C...)
- Page offset calculation (PDF index vs. printed page number)
- Entries with lowercase particles (d', de, du, van, von, etc.)
- Names split across multiple lines within a block
"""

import re
import fitz
from dataclasses import dataclass

NAME_PARTICLES = {'d', 'de', 'du', 'van', 'von', 'der', 'den', 'la', 'le',
                  'les', 'het', 'ten', 'ter', 'dit', 'des'}
# Words allowed between name variants: "LOETS ou LOOTS", "HERMANS ou HERMANN"
NAME_CONNECTORS = {'ou', 'dit', 'dite', 'née', 'alias'}

# Running headers: small font at top of page
RUNNING_HEADER_MAX_SIZE = 8.0
RUNNING_HEADER_MAX_Y = 45

# Minimum font size for biography header text
BIO_HEADER_MIN_SIZE = 7.5


@dataclass
class BiographyEntry:
    surname: str
    full_header: str
    first_name: str
    pdf_page_start: int
    pdf_page_end: int = -1
    printed_page_start: int = 0
    block_y: float = 0.0
    raw_text: str = ""
    author_signature: str = ""


@dataclass
class VolumeInfo:
    file_path: str
    total_pages: int
    first_bio_page: int
    last_bio_page: int
    page_offset: int
    volume_number: int = 0


def detect_page_offset(doc: fitz.Document, first_bio_page: int) -> int:
    """Detect offset between PDF page index and printed page number."""
    for page_num in range(first_bio_page, min(first_bio_page + 15, len(doc))):
        page = doc[page_num]
        text = page.get_text().strip()
        lines = text.split('\n')
        # Check first and last lines for a page number
        for candidate in [lines[-1].strip() if lines else "",
                          lines[0].strip() if lines else ""]:
            if candidate.isdigit():
                printed = int(candidate)
                if 1 <= printed <= page_num + 50:
                    return page_num - printed
    return 0


def detect_first_bio_page(doc: fitz.Document) -> int:
    # For short volumes (like Vol 10, only 2 pages), start from page 0.
    # For longer volumes, skip front matter by starting at page 3.
    start = 0 if len(doc) <= 15 else 3
    for page_num in range(start, min(30, len(doc))):
        page = doc[page_num]
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            if "lines" not in block:
                continue
            for span in block["lines"][0]["spans"]:
                text = span["text"].strip()
                if span["size"] > 40 and len(text) <= 2 and any(c.isalpha() for c in text):
                    return page_num
    # Fallback: find the first page with a detectable biography header
    for page_num in range(len(doc)):
        page = doc[page_num]
        page_height = page.rect.height
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            is_header, _, _ = is_biography_header(block, page_height)
            if is_header:
                return page_num
    return 0


_BACK_MATTER_MARKERS = [
    "LISTE DES AUTEURS",
    "TABLE DES ILLUSTRATIONS",
    "TABLE DES MATIÈRES",
    "TABLE ALPHABÉTIQUE",
]
_REF_PATTERN = re.compile(r'[IVX]+\s*,\s*\d+')


def detect_last_bio_page(doc: fitz.Document) -> int:
    """Find the last biography page by scanning forward for back-matter markers."""
    start = max(0, (len(doc) * 2) // 3)
    for page_num in range(start, len(doc)):
        page = doc[page_num]
        text = page.get_text().strip()
        if any(marker in text for marker in _BACK_MATTER_MARKERS):
            return page_num - 1
        ref_count = sum(1 for line in text.split('\n') if _REF_PATTERN.search(line))
        if ref_count > 5:
            return page_num - 1
    return len(doc) - 1


def is_running_header(block: dict, page_height: float) -> bool:
    """Running headers: small font at top of page."""
    if "lines" not in block:
        return True
    bbox = block["bbox"]
    if bbox[1] > RUNNING_HEADER_MAX_Y:
        return False
    # Check if the FIRST meaningful span of the first line has small font.
    # Don't check all spans — biography headers near the top may contain
    # occasional small spans (superscripts, etc.) that shouldn't trigger this.
    first_line = block["lines"][0]
    for span in first_line["spans"]:
        if span["text"].strip():
            return span["size"] <= RUNNING_HEADER_MAX_SIZE
    return False


def is_page_number(block: dict, page_height: float) -> bool:
    if "lines" not in block:
        return True
    bbox = block["bbox"]
    if bbox[3] > page_height - 35:
        text = "".join(s["text"] for line in block["lines"]
                       for s in line["spans"]).strip()
        if text.isdigit() or len(text) <= 4:
            return True
    return False


def is_section_letter_block(block: dict) -> bool:
    if "lines" not in block:
        return False
    for line in block["lines"]:
        for span in line["spans"]:
            if span["size"] > 30 and len(span["text"].strip()) <= 3:
                return True
    return False


def _is_upper_word(word: str) -> bool:
    alpha = [c for c in word if c.isalpha()]
    return bool(alpha) and all(c.isupper() for c in alpha)


def _extract_surname(text_with_comma: str) -> tuple[str, bool]:
    """
    Extract surname from text that contains at least one comma.
    Returns: (surname, is_valid)
    """
    if ',' not in text_with_comma:
        return "", False

    name_part = text_with_comma.split(',')[0].strip()
    if not name_part or len(name_part) < 3:
        return "", False

    words = name_part.split()
    if not words:
        return "", False

    # Handle apostrophe-prefixed: d'ARTIGUES
    first_word = words[0]
    if "\u2019" in first_word or "'" in first_word:
        apo_parts = re.split(r"['\u2019]", first_word, maxsplit=1)
        if len(apo_parts) == 2:
            prefix, main = apo_parts
            if prefix.lower() in NAME_PARTICLES and main and _is_upper_word(main):
                return name_part, True

    has_upper_main = False
    for word in words:
        clean = word.rstrip('.,;:')
        if not clean:
            continue
        if _is_upper_word(clean) and len(clean) >= 2:
            has_upper_main = True
        elif clean.lower() in NAME_PARTICLES:
            continue
        elif clean.lower() in NAME_CONNECTORS:
            continue
        elif clean in ('I', 'II', 'III', 'IV', 'V'):
            continue
        elif "\u2019" in clean or "'" in clean:
            apo_parts = re.split(r"['\u2019]", clean, maxsplit=1)
            if len(apo_parts) == 2 and _is_upper_word(apo_parts[1]):
                has_upper_main = True
        else:
            return "", False

    if not has_upper_main:
        return "", False

    # Reject if all substantial words are just particles (e.g., "VAN" alone)
    substantial_words = [
        w for w in words
        if _is_upper_word(w.rstrip('.,;:')) and
        w.lower() not in NAME_PARTICLES and
        len(w.rstrip('.,;:')) >= 3
    ]
    if not substantial_words:
        return "", False

    # Reject abbreviations with dots (e.g., "R.D.I.L.C.")
    if '.' in name_part and not any("'" in w for w in words):
        return "", False

    return name_part, True


def _get_block_text_joined(block: dict, max_lines: int = 5) -> str:
    """Join first N lines of a block into a single string."""
    parts = []
    for i, line in enumerate(block["lines"][:max_lines]):
        line_text = "".join(s["text"] for s in line["spans"]).strip()
        parts.append(line_text)
    return " ".join(parts)


def is_biography_header(block: dict, page_height: float) -> tuple[bool, str, str]:
    """
    Check if a text block starts a new biography entry.
    Returns: (is_header, surname, full_header_text)
    """
    if "lines" not in block:
        return False, "", ""

    bbox = block["bbox"]

    # Exclude page footers
    if bbox[1] > page_height - 35:
        return False, "", ""

    # Exclude running headers (small font at top)
    if is_running_header(block, page_height):
        return False, "", ""

    # Get first line
    first_line_spans = block["lines"][0]["spans"]
    first_line_text = "".join(s["text"] for s in first_line_spans).strip()

    if not first_line_text:
        return False, "", ""

    # Check font size
    first_span = next((s for s in first_line_spans if s["text"].strip()), None)
    if not first_span or not (BIO_HEADER_MIN_SIZE < first_span["size"] < 12.5):
        return False, "", ""

    # Strategy 1: First line contains NAME, comma (normal case)
    if ',' in first_line_text:
        surname, valid = _extract_surname(first_line_text)
        if valid:
            full_header = "".join(
                "".join(s["text"] for s in line["spans"])
                for line in block["lines"]
            ).strip()
            return True, surname, full_header

    # Strategy 2: Name on first line, comma on subsequent line
    # (handles MALENGREAU, VAN AELST III, GOUDRIAAN etc.)
    joined = _get_block_text_joined(block, max_lines=4)
    if ',' in joined:
        # Check if the first line looks like a name (ALL CAPS or particle + CAPS)
        words = first_line_text.rstrip(',').split()
        if words:
            has_upper = any(_is_upper_word(w) and len(w) >= 2 for w in words)
            all_name_words = all(
                _is_upper_word(w.rstrip('.,;:')) or
                w.lower() in NAME_PARTICLES or
                w.lower() in NAME_CONNECTORS or
                w in ('I', 'II', 'III', 'IV', 'V') or
                ("'" in w and _is_upper_word(re.split(r"['\u2019]", w)[-1]))
                for w in words
                if w.strip()
            )
            if has_upper and all_name_words:
                # Build surname from consecutive name-like lines
                # (handles "VAN\nAELST\nIII," split across lines)
                surname_parts = [first_line_text.rstrip(',').strip()]
                for extra_line in block["lines"][1:]:
                    extra_text = "".join(
                        s["text"] for s in extra_line["spans"]
                    ).strip().rstrip(',')
                    extra_words = extra_text.split()
                    if extra_words and all(
                        _is_upper_word(w.rstrip('.,;:')) or
                        w.lower() in NAME_PARTICLES or
                        w.lower() in NAME_CONNECTORS or
                        w in ('I', 'II', 'III', 'IV', 'V')
                        for w in extra_words
                    ):
                        surname_parts.append(extra_text)
                    else:
                        break
                surname = ' '.join(surname_parts)
                # Surname must have a substantial word (not just particles)
                substantial = [
                    w for w in surname.split()
                    if _is_upper_word(w.rstrip('.,;:')) and
                    w.lower() not in NAME_PARTICLES and
                    len(w.rstrip('.,;:')) >= 3
                ]
                if substantial:
                    _, valid = _extract_surname(surname + ", dummy")
                    if valid:
                        full_header = "".join(
                            "".join(s["text"] for s in line["spans"])
                            for line in block["lines"]
                        ).strip()
                        return True, surname, full_header

    return False, "", ""


def extract_block_text(block: dict) -> str:
    if "lines" not in block:
        return ""
    lines = []
    for line in block["lines"]:
        line_text = "".join(s["text"] for s in line["spans"])
        lines.append(line_text)
    return "\n".join(lines)


def parse_first_name(full_header: str, surname: str) -> str:
    """Extract the first given name from the header."""
    rest = full_header[len(surname):].strip()
    if rest.startswith(','):
        rest = rest[1:].strip()

    if not rest:
        return ""

    first_token = rest.split(',')[0].strip()

    skip_keywords = ['pseudonyme', 'surnom', 'voir', 'dit ', 'inscrit',
                     'prénoms', 'sainte', 'saint', 'abbé', 'moine',
                     'surnom de', 'ou ']
    if any(kw in first_token.lower() for kw in skip_keywords):
        return ""

    first_name = first_token.split()[0] if first_token else ""
    first_name = first_name.rstrip('.,;:')

    return first_name


def scan_volume_precise(file_path: str) -> tuple[VolumeInfo, list[BiographyEntry]]:
    doc = fitz.open(file_path)

    first_bio_page = detect_first_bio_page(doc)
    last_bio_page = detect_last_bio_page(doc)
    page_offset = detect_page_offset(doc, first_bio_page)

    vol_num = 0
    match = re.search(r'Volume\s*(\d+)', file_path)
    if match:
        vol_num = int(match.group(1))

    vol_info = VolumeInfo(
        file_path=file_path,
        total_pages=len(doc),
        first_bio_page=first_bio_page,
        last_bio_page=last_bio_page,
        page_offset=page_offset,
        volume_number=vol_num,
    )

    # Pass 1: Find all biography headers
    raw_entries: list[tuple[BiographyEntry, int, float]] = []

    for page_num in range(first_bio_page, last_bio_page + 1):
        page = doc[page_num]
        page_height = page.rect.height
        blocks = page.get_text("dict")["blocks"]

        for block in sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0])):
            is_header, surname, full_header = is_biography_header(block, page_height)
            if is_header:
                first_name = parse_first_name(full_header, surname)
                printed_page = page_num - page_offset

                entry = BiographyEntry(
                    surname=surname,
                    full_header=full_header,
                    first_name=first_name,
                    pdf_page_start=page_num,
                    printed_page_start=printed_page,
                    block_y=block["bbox"][1],
                )
                raw_entries.append((entry, page_num, block["bbox"][1]))

    # Pass 1.5: Filter false positives
    entries = _filter_false_positives(raw_entries)

    # Pass 2: Set page boundaries
    for i in range(len(entries)):
        if i + 1 < len(entries):
            entries[i].pdf_page_end = entries[i + 1].pdf_page_start + 1
        else:
            entries[i].pdf_page_end = last_bio_page + 1

    # Pass 3: Extract text (with page text cache to avoid reprocessing shared pages)
    page_text_cache: dict[int, str] = {}
    for i, entry in enumerate(entries):
        next_entry = entries[i + 1] if i + 1 < len(entries) else None
        entry.raw_text = _extract_entry_text_precise(doc, entry, next_entry, page_text_cache)

    doc.close()
    return vol_info, entries


def _filter_false_positives(
    raw_entries: list[tuple[BiographyEntry, int, float]]
) -> list[BiographyEntry]:
    """
    Filter false positive headers.

    Merges alias entries that appear right after a main biography header
    (e.g., GERMANUS after de MIDDELBOURG; D'ENGHIEN III after VAN AELST III).

    Detection: two entries on the same page within close Y proximity,
    where the second is likely an alias/alternate name.
    """
    if not raw_entries:
        return []

    filtered = []
    skip_indices = set()

    for i in range(len(raw_entries)):
        if i in skip_indices:
            continue

        entry, page, y = raw_entries[i]

        if i + 1 < len(raw_entries):
            next_entry, next_page, next_y = raw_entries[i + 1]

            if next_page == page and abs(next_y - y) < 25:
                # Check if it's an alias relationship
                is_alias = _is_alias_entry(entry, next_entry)
                if is_alias:
                    entry.full_header = entry.full_header + " " + next_entry.full_header
                    filtered.append(entry)
                    skip_indices.add(i + 1)
                    continue

        filtered.append(entry)

    return filtered


def _is_alias_entry(first: BiographyEntry, second: BiographyEntry) -> bool:
    """
    Check if the second entry is an alias/alternate name for the first.

    Heuristics:
    - The second name appears in the first's header text
    - The first's header contains alias keywords (dit, également, surnom, ou)
      near the second's name
    - The first's header ends with a comma (indicating continuation)
    """
    second_name = second.surname.upper()
    first_header = first.full_header.upper()

    # Direct: second name appears in first header
    if second_name in first_header:
        return True

    # Check if first header contains alias keywords
    alias_keywords = ['DIT', 'ÉGALEMENT', 'SURNOM', 'ALIAS', 'OU ', 'AUSSI',
                      'APPELÉ', 'CONNU SOUS']
    first_has_alias = any(kw in first_header for kw in alias_keywords)

    # If first header has alias keywords, the next close entry is likely an alias
    if first_has_alias:
        return True

    # If first header ends with comma (indicating continuation)
    if first.full_header.rstrip().endswith(','):
        return True

    # Check if second entry's header contains alias-like patterns
    second_header = second.full_header.upper()
    if any(kw in second_header for kw in ['DIT ', 'SURNOM', 'ALIAS']):
        return True

    return False


def _get_page_content_text(page: fitz.Page) -> str:
    """
    Get page text in reading order, with running headers/footers removed.

    Uses page.get_text() for correct reading order, then removes
    header/footer lines detected via block analysis.
    """
    page_height = page.rect.height
    blocks = page.get_text("dict")["blocks"]

    # Identify text from running headers, page numbers, and section letters
    skip_texts = set()
    for block in blocks:
        if "lines" not in block:
            continue
        if is_running_header(block, page_height) or \
           is_page_number(block, page_height) or \
           is_section_letter_block(block):
            for line in block["lines"]:
                text = "".join(s["text"] for s in line["spans"]).strip()
                if text:
                    skip_texts.add(text)

    # Get full text in reading order
    full_text = page.get_text("text")
    lines = full_text.split('\n')

    # Remove header/footer lines
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and stripped in skip_texts:
            continue
        cleaned_lines.append(line)

    return '\n'.join(cleaned_lines)


def _find_header_in_text(text: str, surname: str) -> int:
    """
    Find the position of a biography header in page text.
    Returns the character index where the header starts, or -1.
    """
    # Try exact match: "SURNAME," or "SURNAME " with particles
    patterns = [
        re.escape(surname) + r'\s*,',
        re.escape(surname) + r'\s+\w',
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return m.start()
    return -1


def _extract_entry_text_precise(
    doc: fitz.Document,
    entry: BiographyEntry,
    next_entry: BiographyEntry | None,
    page_text_cache: dict[int, str],
) -> str:
    """
    Extract text for a biography entry using page.get_text() for correct
    reading order, with header-based boundary detection.
    """
    text_parts = []

    for page_num in range(entry.pdf_page_start, entry.pdf_page_end):
        if page_num >= len(doc):
            break

        if page_num not in page_text_cache:
            page_text_cache[page_num] = _get_page_content_text(doc[page_num])
        page_text = page_text_cache[page_num]

        if page_num == entry.pdf_page_start:
            # Find where this entry's header starts in the page text
            start_pos = _find_header_in_text(page_text, entry.surname)
            if start_pos >= 0:
                page_text = page_text[start_pos:]

        if next_entry and page_num == next_entry.pdf_page_start:
            # Find where the next entry's header starts and cut before it
            # If this is also the start page, search in the already-trimmed text
            end_pos = _find_header_in_text(page_text, next_entry.surname)
            if end_pos >= 0:
                # Don't cut at position 0 if that's our own header
                if end_pos == 0 and page_num == entry.pdf_page_start:
                    # The next entry might be before our text already got trimmed
                    pass
                else:
                    page_text = page_text[:end_pos]

        text_parts.append(page_text)

    return '\n'.join(text_parts)
