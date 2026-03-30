#!/usr/bin/env python3
"""
app.py — Main orchestration script for NBN biography extraction.

Usage:
    # Process a single volume:
    python app.py NewBioPdf/NouvelleBiographieNationale_Volume1.pdf

    # Process a single volume with JSON index validation:
    python app.py NewBioPdf/NouvelleBiographieNationale_Volume1.pdf --index index_nouvelles_biographies.json

    # Process all PDFs in a directory:
    python app.py NewBioPdf/

    # Generate the JSON index from PDFs (no text export):
    python app.py NewBioPdf/ --build-index

    # Specify output directory:
    python app.py NewBioPdf/NouvelleBiographieNationale_Volume1.pdf -o output/
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
) -> tuple[VolumeInfo, list[BiographyEntry], dict | None]:
    """
    Process a single PDF volume: extract, clean, and export biographies.

    Args:
        pdf_path: Path to the PDF file.
        output_dir: Directory for .txt output files.
        index_entries: Optional JSON index entries for validation.
        verbose: Print progress information.

    Returns:
        Tuple of (VolumeInfo, list of entries, validation report or None)
    """
    if verbose:
        print(f"\n{'─'*60}")
        print(f"Traitement de : {os.path.basename(pdf_path)}")
        print(f"{'─'*60}")

    start_time = time.time()

    # Extract entries from PDF
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
  python app.py NewBioPdf/NouvelleBiographieNationale_Volume1.pdf
  python app.py NewBioPdf/ --index index_nouvelles_biographies.json
  python app.py NewBioPdf/ --build-index
  python app.py NewBioPdf/ -o output/
        """,
    )
    parser.add_argument(
        "input",
        help="Fichier PDF ou répertoire contenant les PDFs NBN",
    )
    parser.add_argument(
        "-o", "--output",
        default="output",
        help="Répertoire de sortie pour les fichiers .txt (défaut: output/)",
    )
    parser.add_argument(
        "--index",
        help="Chemin vers le fichier JSON de référence (index_nouvelles_biographies.json)",
    )
    parser.add_argument(
        "--build-index",
        action="store_true",
        help="Générer le fichier index JSON à partir des PDFs (sans export texte)",
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

    # Load index if provided
    index_entries = None
    if args.index and os.path.exists(args.index):
        index_entries = load_index(args.index)
        if verbose:
            print(f"\nIndex JSON chargé : {len(index_entries)} entrées depuis {args.index}")

    # Process each volume
    all_results = []
    all_reports = []

    for pdf_path in pdf_files:
        if args.build_index:
            # Just extract, don't export text files
            from engine import scan_volume_precise
            vol_info, entries = scan_volume_precise(pdf_path)
            all_results.append((vol_info, entries))
            if verbose:
                print(f"\n  Volume {vol_info.volume_number}: {len(entries)} notices détectées")
        else:
            vol_info, entries, report = process_volume(
                pdf_path, args.output, index_entries, verbose
            )
            all_results.append((vol_info, entries))
            if report:
                all_reports.append(report)

    # Build and save index if requested
    if args.build_index:
        index = build_index_from_volumes(all_results)
        index_path = args.index or "index_nouvelles_biographies.json"
        save_index(index, index_path)
        if verbose:
            print(f"\nIndex JSON généré : {len(index)} entrées → {index_path}")

    # Print validation reports
    for report in all_reports:
        print_validation_report(report)

    # Final summary
    if verbose and not args.build_index:
        total_entries = sum(len(entries) for _, entries in all_results)
        print(f"\n{'═'*60}")
        print(f"TOTAL : {total_entries} notices extraites de {len(pdf_files)} volume(s)")
        print(f"Fichiers exportés dans : {os.path.abspath(args.output)}/")
        print(f"{'═'*60}")


if __name__ == "__main__":
    main()
