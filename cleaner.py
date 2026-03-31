"""
cleaner.py — Text cleaning utilities for NBN biography extraction.

Handles:
- End-of-line hyphenation (re-joining split words)
- Page header/footer removal from raw text
- Author signature detection and extraction
- Whitespace normalization
- OCR artifact cleanup
"""

import re
import unicodedata

from engine import NAME_PARTICLES


def fix_hyphenation(text: str) -> str:
    """
    Rejoin words split by end-of-line hyphens.

    Handles cases like:
        "islamolo-\\ngue" -> "islamologue"
        "profes-\\nseur" -> "professeur"

    But preserves intentional hyphens in compound words
    (where the next line starts with a capital letter or the
    hyphen is between two complete words).
    """
    # Pattern: word fragment + hyphen + newline + optional spaces + lowercase continuation
    # This handles the common OCR/typesetting line-break hyphenation
    result = re.sub(
        r'(\w)-\n\s*([a-zàâäéèêëïîôùûüÿçæœ])',
        r'\1\2',
        text,
    )
    return result


def fix_soft_hyphens(text: str) -> str:
    """Remove soft hyphens (U+00AD) and fix the word."""
    # Soft hyphen followed by newline + lowercase = rejoin
    result = re.sub(
        r'(\w)\u00AD\n\s*([a-zàâäéèêëïîôùûüÿçæœ])',
        r'\1\2',
        text,
    )
    # Remaining soft hyphens (mid-word): just remove them
    result = result.replace('\u00AD', '')
    return result


def normalize_whitespace(text: str) -> str:
    """Normalize whitespace while preserving paragraph structure."""
    # Replace multiple spaces with single space
    text = re.sub(r'[ \t]+', ' ', text)

    # Normalize line endings
    text = text.replace('\r\n', '\n').replace('\r', '\n')

    # Remove trailing spaces on each line
    text = re.sub(r' +\n', '\n', text)

    # Collapse 3+ newlines into 2 (paragraph break)
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


def remove_page_numbers(text: str) -> str:
    """Remove standalone page numbers (digits alone on a line)."""
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # Skip lines that are just a page number
        if stripped.isdigit() and len(stripped) <= 4:
            continue
        cleaned.append(line)
    return '\n'.join(cleaned)


def detect_author_signature(text: str) -> tuple[str, str]:
    """
    Detect and separate the author signature from the biography text.

    The author signature is typically the last line(s) of the biography,
    containing a person's name (often with title). It appears after the
    bibliography section.

    Returns: (biography_text_without_signature, author_signature)
    """
    lines = text.rstrip().split('\n')
    if not lines:
        return text, ""

    # The signature is usually the last non-empty line
    # It's a short line with a person's name
    last_lines = []
    idx = len(lines) - 1

    while idx >= 0 and not lines[idx].strip():
        idx -= 1

    if idx < 0:
        return text, ""

    candidate = lines[idx].strip()

    # Signature patterns: typically a name, possibly with title
    # e.g. "Annette Destrée", "Jean-Pierre Devroey", "Georges Despy"
    # Could also be multi-line like "Sophie Schneebalg-Perelman\net Viviane Baesens"
    name_pattern = re.compile(
        r'^(?:et\s+)?'  # optional "et" prefix
        r'[A-ZÀÂÄÉÈÊËÏÎÔÙÛÜŸÇÆŒ]'  # starts with capital
        r'[a-zàâäéèêëïîôùûüÿçæœA-ZÀÂÄÉÈÊËÏÎÔÙÛÜŸÇÆŒ\s\-\'.]+$'  # name chars
    )

    if name_pattern.match(candidate) and len(candidate) < 60:
        signature = candidate

        # Check if the previous line is also part of the signature (co-authors)
        if idx > 0:
            prev = lines[idx - 1].strip()
            if prev.startswith('et ') or (name_pattern.match(prev) and len(prev) < 60):
                signature = prev + '\n' + signature
                idx -= 1

        bio_text = '\n'.join(lines[:idx])
        return bio_text.rstrip(), signature

    return text, ""


def join_paragraph_lines(text: str) -> str:
    """
    Join lines within the same paragraph into continuous text.

    Single newlines (mid-paragraph line breaks from PDF layout) are replaced
    by a space. Double newlines (paragraph breaks) are preserved.
    """
    # Normalize line endings
    text = text.replace('\r\n', '\n').replace('\r', '\n')

    # Split into paragraphs (separated by blank lines)
    paragraphs = re.split(r'\n\s*\n', text)

    joined = []
    for para in paragraphs:
        # Within a paragraph, replace single newlines with spaces
        # but preserve leading tabs (paragraph indentation markers like \t)
        lines = para.split('\n')
        merged_parts = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            # If line starts with \t it's a new sub-paragraph within the entry
            if line.startswith('\t') and merged_parts:
                # Flush current paragraph, start new one
                joined.append(' '.join(merged_parts))
                merged_parts = [stripped]
            else:
                merged_parts.append(stripped)
        if merged_parts:
            joined.append(' '.join(merged_parts))

    return '\n\n'.join(joined)


def clean_biography_text(raw_text: str) -> str:
    """
    Full cleaning pipeline for a biography's raw text.

    Steps:
    1. Fix soft hyphens
    2. Fix end-of-line hyphenation
    3. Remove page numbers
    4. Join paragraph lines into continuous text
    5. Normalize whitespace
    """
    text = raw_text

    # Fix soft hyphens first
    text = fix_soft_hyphens(text)

    # Fix regular hyphenation
    text = fix_hyphenation(text)

    # Remove standalone page numbers
    text = remove_page_numbers(text)

    # Join lines within paragraphs into continuous text
    text = join_paragraph_lines(text)

    # Normalize whitespace
    text = normalize_whitespace(text)

    return text


def format_filename(surname: str, first_name: str) -> str:
    """
    Format the output filename as: NOM (Prénom).txt

    Handles compound surnames like "VAN DER MEERSCH" and
    special characters in names.
    """
    # Clean the surname: title case
    # "VAN DER MEERSCH" -> "Van der Meersch"
    # But keep particles lowercase: de, du, van, von, der, den, la, le, les
    particles = NAME_PARTICLES | {'ou'}
    roman_numerals = {'I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X'}

    words = surname.split()
    formatted_words = []
    for i, word in enumerate(words):
        lower = word.lower()
        upper = word.upper()
        if upper in roman_numerals:
            # Keep Roman numerals uppercase
            formatted_words.append(upper)
        elif i == 0:
            # First word is always capitalized
            formatted_words.append(word.capitalize())
        elif lower in particles:
            formatted_words.append(lower)
        else:
            formatted_words.append(word.capitalize())

    formatted_surname = ' '.join(formatted_words)

    # Clean first name: remove parentheses, trailing punctuation, fix hyphenation
    first_name = first_name.strip().rstrip('.,;:')
    first_name = first_name.strip('()')
    # Fix hyphenation artifacts: "Eu-gène" -> "Eugène"
    first_name = re.sub(r'(\w)-\n?\s*([a-zàâäéèêëïîôùûüÿçæœ])', r'\1\2', first_name)
    if first_name:
        first_name = first_name[0].upper() + first_name[1:] if len(first_name) > 1 else first_name.upper()

    # Build filename
    if first_name:
        filename = f"{formatted_surname} ({first_name}).txt"
    else:
        filename = f"{formatted_surname}.txt"

    # Sanitize for filesystem: remove/replace problematic characters
    filename = filename.replace('/', '-').replace('\\', '-')
    filename = re.sub(r'[<>:"|?*]', '', filename)

    return filename
