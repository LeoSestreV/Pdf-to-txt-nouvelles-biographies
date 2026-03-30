"""
index_builder.py — Build and manage the JSON reference index.

Creates index_nouvelles_biographies.json from extracted data and/or
validates extractions against an existing index.
"""

import json
import os
import re
import unicodedata
from typing import Optional

from engine import BiographyEntry, VolumeInfo


def normalize_name(name: str) -> str:
    """Normalize a name for comparison (lowercase, no accents, no extra spaces)."""
    # Remove accents
    nfkd = unicodedata.normalize('NFKD', name)
    no_accents = ''.join(c for c in nfkd if not unicodedata.combining(c))
    # Lowercase, strip, collapse spaces
    result = re.sub(r'\s+', ' ', no_accents.lower().strip())
    return result


def build_index_entry(entry: BiographyEntry, volume_number: int) -> dict:
    """Build a JSON index entry from a BiographyEntry."""
    # Extract description from the header line
    # The header typically contains: SURNAME, FirstName, ..., description
    desc = entry.full_header
    # Remove the name parts to get the description
    if ',' in desc:
        parts = desc.split(',')
        # Skip name parts (typically first 2-3 comma-separated items are names)
        # The description starts when we hit a non-name part (lowercase word)
        desc_parts = []
        found_desc = False
        for i, part in enumerate(parts):
            part_stripped = part.strip()
            if i == 0:
                continue  # skip surname
            if not found_desc:
                # Check if this looks like a name (starts with uppercase, short)
                words = part_stripped.split()
                if words and words[0][0:1].isupper() and len(part_stripped) < 30:
                    # Likely a first/middle name or "surnom de ..."
                    if any(kw in part_stripped.lower() for kw in [
                        'pseudonyme', 'surnom', 'inscrit', 'prénoms'
                    ]):
                        found_desc = False
                        continue
                    continue
                found_desc = True
            if found_desc:
                desc_parts.append(part_stripped)

        description = ', '.join(desc_parts).strip().rstrip(',.')
        if not description:
            description = entry.full_header
    else:
        description = ""

    return {
        "nom": entry.surname,
        "prenom": entry.first_name,
        "description": description,
        "reference": {
            "tome": volume_number,
            "page": entry.printed_page_start,
        }
    }


def build_index_from_volumes(
    volumes: list[tuple[VolumeInfo, list[BiographyEntry]]],
) -> list[dict]:
    """Build the complete index from all extracted volumes."""
    index = []
    for vol_info, entries in volumes:
        for entry in entries:
            index_entry = build_index_entry(entry, vol_info.volume_number)
            index.append(index_entry)
    return index


def save_index(index: list[dict], output_path: str) -> None:
    """Save index to JSON file."""
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def load_index(index_path: str) -> list[dict]:
    """Load index from JSON file."""
    with open(index_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def filter_index_by_volume(index: list[dict], volume_number: int) -> list[dict]:
    """Filter index entries for a specific volume."""
    return [
        entry for entry in index
        if entry.get("reference", {}).get("tome") == volume_number
    ]


def validate_extraction(
    extracted_entries: list[BiographyEntry],
    index_entries: list[dict],
    volume_number: int,
    page_offset_tolerance: int = 3,
) -> dict:
    """
    Validate extracted biographies against the JSON index.

    Returns a validation report with:
    - matched: entries found in both extraction and index
    - missing: entries in index but not extracted
    - extra: entries extracted but not in index
    - page_mismatches: entries where the page number differs
    """
    # Normalize index entries for comparison
    index_lookup = {}
    for idx_entry in index_entries:
        key = normalize_name(idx_entry["nom"])
        if key not in index_lookup:
            index_lookup[key] = []
        index_lookup[key].append(idx_entry)

    matched = []
    extra = []
    page_mismatches = []

    extracted_keys = set()

    for entry in extracted_entries:
        key = normalize_name(entry.surname)
        extracted_keys.add(key)

        if key in index_lookup:
            idx_entries_for_key = index_lookup[key]
            # Find best match by page number
            best_match = None
            best_diff = float('inf')
            for idx_entry in idx_entries_for_key:
                ref_page = idx_entry.get("reference", {}).get("page", 0)
                diff = abs(entry.printed_page_start - ref_page)
                if diff < best_diff:
                    best_diff = diff
                    best_match = idx_entry

            if best_match:
                matched.append({
                    "surname": entry.surname,
                    "extracted_page": entry.printed_page_start,
                    "index_page": best_match.get("reference", {}).get("page", 0),
                })
                if best_diff > page_offset_tolerance:
                    page_mismatches.append({
                        "surname": entry.surname,
                        "extracted_page": entry.printed_page_start,
                        "index_page": best_match.get("reference", {}).get("page", 0),
                        "difference": best_diff,
                    })
        else:
            extra.append({
                "surname": entry.surname,
                "page": entry.printed_page_start,
            })

    # Find missing entries
    missing = []
    for key, idx_entries in index_lookup.items():
        if key not in extracted_keys:
            for idx_entry in idx_entries:
                missing.append({
                    "nom": idx_entry["nom"],
                    "page": idx_entry.get("reference", {}).get("page", 0),
                })

    return {
        "volume": volume_number,
        "total_in_index": len(index_entries),
        "total_extracted": len(extracted_entries),
        "matched": len(matched),
        "missing": missing,
        "extra": extra,
        "page_mismatches": page_mismatches,
    }


def print_validation_report(report: dict) -> None:
    """Print a human-readable validation report."""
    print(f"\n{'='*60}")
    print(f"RAPPORT DE VALIDATION — Tome {report['volume']}")
    print(f"{'='*60}")
    print(f"Notices dans l'index JSON : {report['total_in_index']}")
    print(f"Notices extraites du PDF  : {report['total_extracted']}")
    print(f"Correspondances trouvées  : {report['matched']}")

    if report['missing']:
        print(f"\n⚠ Notices MANQUANTES ({len(report['missing'])}) :")
        for m in report['missing']:
            print(f"  - {m['nom']} (page {m['page']})")

    if report['extra']:
        print(f"\n⚠ Notices SUPPLÉMENTAIRES ({len(report['extra'])}) :")
        for e in report['extra']:
            print(f"  - {e['surname']} (page {e['page']})")

    if report['page_mismatches']:
        print(f"\n⚠ Décalages de page ({len(report['page_mismatches'])}) :")
        for pm in report['page_mismatches']:
            print(f"  - {pm['surname']}: extrait p.{pm['extracted_page']} vs index p.{pm['index_page']} (Δ{pm['difference']})")

    if not report['missing'] and not report['extra']:
        print(f"\n✓ Toutes les notices correspondent !")

    print(f"{'='*60}\n")
