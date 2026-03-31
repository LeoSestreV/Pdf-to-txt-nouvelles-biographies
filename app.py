#!/usr/bin/env python3
"""
app.py — Main orchestration script for NBN biography extraction.

Usage:
    # Tout faire (extraction + index + validation) :
    python app.py

    # Traiter un seul volume :
    python app.py NewBioPdf/NouvelleBiographieNationale_Volume1.pdf

    # Spécifier le répertoire de PDFs et la sortie :
    python app.py -i NewBioPdf/ -o output/
"""

import argparse
import glob
import json
import os
import sys
import time

from engine import scan_volume_precise, BiographyEntry, VolumeInfo
from cleaner import clean_biography_text, detect_author_signature, format_filename
from index_builder import (
    build_index_from_volumes,
    save_index,
    load_index,
    filter_index_by_volume,
    validate_extraction,
    print_validation_report,
)


def process_volume(
    pdf_path: str,
    output_dir: str,
    index_entries: list[dict] | None = None,
    verbose: bool = True,
    precomputed: tuple[VolumeInfo, list[BiographyEntry]] | None = None,
) -> tuple[VolumeInfo, list[BiographyEntry], dict | None]:
    """
    Process a single PDF volume: extract, clean, and export biographies.

    Args:
        pdf_path: Path to the PDF file.
        output_dir: Directory for .txt output files.
        index_entries: Optional JSON index entries for validation.
        verbose: Print progress information.
        precomputed: Skip PDF scan if results already available from a prior pass.

    Returns:
        Tuple of (VolumeInfo, list of entries, validation report or None)
    """
    if verbose:
        print(f"\n{'─'*60}")
        print(f"Traitement de : {os.path.basename(pdf_path)}")
        print(f"{'─'*60}")

    start_time = time.time()

    if precomputed:
        vol_info, entries = precomputed
    else:
        if verbose:
            print("  [1/4] Extraction des notices depuis le PDF...")
        vol_info, entries = scan_volume_precise(pdf_path)

    if verbose:
        print(f"        → {len(entries)} notices détectées")
        print(f"        → Pages PDF {vol_info.first_bio_page}-{vol_info.last_bio_page}")
        print(f"        → Décalage pagination : {vol_info.page_offset}")

    # Clean and export
    if verbose:
        print("  [2/4] Nettoyage et export des fichiers texte...")

    vol_output_dir = os.path.join(output_dir, f"Volume_{vol_info.volume_number}")
    os.makedirs(vol_output_dir, exist_ok=True)

    exported_count = 0
    duplicates = {}

    for entry in entries:
        # Clean the text
        cleaned_text = clean_biography_text(entry.raw_text)

        # Detect and separate author signature
        bio_text, signature = detect_author_signature(cleaned_text)
        entry.author_signature = signature

        # Generate filename
        filename = format_filename(entry.surname, entry.first_name)

        # Handle duplicate filenames
        if filename in duplicates:
            duplicates[filename] += 1
            base, ext = os.path.splitext(filename)
            filename = f"{base}_{duplicates[filename]}{ext}"
        else:
            duplicates[filename] = 1

        filepath = os.path.join(vol_output_dir, filename)

        # Write the biography text
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(bio_text)
            if signature:
                f.write(f"\n\n— {signature}")
            f.write('\n')

        exported_count += 1

    if verbose:
        print(f"        → {exported_count} fichiers exportés dans {vol_output_dir}/")

    # Validate against index if provided
    report = None
    if index_entries is not None:
        if verbose:
            print("  [3/4] Validation contre l'index JSON...")

        vol_index = filter_index_by_volume(index_entries, vol_info.volume_number)
        if vol_index:
            report = validate_extraction(entries, vol_index, vol_info.volume_number)
        else:
            if verbose:
                print(f"        ⚠ Aucune entrée trouvée dans l'index pour le tome {vol_info.volume_number}")
    else:
        if verbose:
            print("  [3/4] Pas d'index JSON fourni, validation ignorée.")

    # Summary
    elapsed = time.time() - start_time
    if verbose:
        print(f"  [4/4] Terminé en {elapsed:.1f}s")

        # Print quick summary of entries
        print(f"\n  Résumé des notices extraites :")
        for i, entry in enumerate(entries):
            marker = "✓" if entry.raw_text.strip() else "⚠"
            name_display = f"{entry.surname} ({entry.first_name})" if entry.first_name else entry.surname
            text_len = len(entry.raw_text)
            print(f"    {marker} {name_display:<45s} p.{entry.printed_page_start:>4d}  [{text_len:>6d} car.]")

    return vol_info, entries, report


def find_pdf_files(path: str) -> list[str]:
    """Find all NBN PDF files in a path (file or directory)."""
    if os.path.isfile(path) and path.lower().endswith('.pdf'):
        return [path]
    elif os.path.isdir(path):
        pattern = os.path.join(path, "NouvelleBiographieNationale_Volume*.pdf")
        files = sorted(glob.glob(pattern))
        if not files:
            # Try any PDF
            files = sorted(glob.glob(os.path.join(path, "*.pdf")))
        return files
    else:
        print(f"Erreur : {path} n'est ni un fichier PDF ni un répertoire valide.")
        return []


def main():
    parser = argparse.ArgumentParser(
        description="Extraction des notices biographiques de la Nouvelle Biographie Nationale (NBN)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  python app.py                  # Tout faire automatiquement
  python app.py -i NewBioPdf/NouvelleBiographieNationale_Volume1.pdf
  python app.py -i NewBioPdf/ -o sortie/
        """,
    )
    parser.add_argument(
        "-i", "--input",
        default="NewBioPdf",
        help="Fichier PDF ou répertoire contenant les PDFs NBN (défaut: NewBioPdf/)",
    )
    parser.add_argument(
        "-o", "--output",
        default="output",
        help="Répertoire de sortie pour les fichiers .txt (défaut: output/)",
    )
    parser.add_argument(
        "--index",
        default="index_nouvelles_biographies.json",
        help="Chemin du fichier JSON de référence (défaut: index_nouvelles_biographies.json)",
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Mode silencieux (pas de sortie console)",
    )

    args = parser.parse_args()
    verbose = not args.quiet

    # Find PDF files
    pdf_files = find_pdf_files(args.input)
    if not pdf_files:
        print("Aucun fichier PDF trouvé.")
        sys.exit(1)

    if verbose:
        print(f"╔{'═'*58}╗")
        print(f"║  NBN — Extraction des Notices Biographiques              ║")
        print(f"╚{'═'*58}╝")
        print(f"\nFichiers à traiter : {len(pdf_files)}")
        for f in pdf_files:
            print(f"  • {os.path.basename(f)}")

    # ── Étape 1 : Extraction (scan une seule fois par PDF) ──────
    if verbose:
        print(f"\n{'─'*60}")
        print(f"  ÉTAPE 1/3 — Extraction des notices depuis les PDFs")
        print(f"{'─'*60}")

    scan_results: list[tuple[VolumeInfo, list[BiographyEntry]]] = []
    for pdf_path in pdf_files:
        vol_info, entries = scan_volume_precise(pdf_path)
        scan_results.append((vol_info, entries))
        if verbose:
            print(f"  Volume {vol_info.volume_number}: {len(entries)} notices détectées")

    # ── Étape 2 : Index JSON + export .txt ─────────────────────
    if verbose:
        print(f"\n{'─'*60}")
        print(f"  ÉTAPE 2/3 — Index JSON + export des fichiers .txt")
        print(f"{'─'*60}")

    index = build_index_from_volumes(scan_results)
    save_index(index, args.index)
    if verbose:
        print(f"  → Index JSON généré : {len(index)} entrées → {args.index}")

    index_entries = load_index(args.index)

    all_results = []
    all_reports = []

    for vol_info, entries in scan_results:
        _, _, report = process_volume(
            vol_info.file_path, args.output, index_entries, verbose,
            precomputed=(vol_info, entries),
        )
        all_results.append((vol_info, entries))
        if report:
            all_reports.append(report)

    # ── Étape 3 : Rapport de validation ─────────────────────────
    if verbose:
        print(f"\n{'─'*60}")
        print(f"  ÉTAPE 3/3 — Validation")
        print(f"{'─'*60}")

    for report in all_reports:
        print_validation_report(report)

    # Résumé final
    if verbose:
        total_entries = sum(len(entries) for _, entries in all_results)
        print(f"{'═'*60}")
        print(f"TOTAL : {total_entries} notices extraites de {len(pdf_files)} volume(s)")
        print(f"Index JSON        : {os.path.abspath(args.index)}")
        print(f"Fichiers exportés : {os.path.abspath(args.output)}/")
        print(f"{'═'*60}")


if __name__ == "__main__":
    main()
