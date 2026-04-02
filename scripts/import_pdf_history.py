#!/usr/bin/env python3
"""Import Q&A history from PDF files into History RAG.

Usage:
    python scripts/import_pdf_history.py              # Import all PDFs
    python scripts/import_pdf_history.py --file X    # Import specific file
    python scripts/import_pdf_history.py --force     # Force re-import
    python scripts/import_pdf_history.py --dry-run   # Test without saving
"""

import sys
import asyncio
import argparse
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logger import logger


async def import_single_file(file_path: str, force: bool = False, dry_run: bool = False):
    """Import a single PDF file.

    Args:
        file_path: Path to PDF file
        force: Force re-import even if already imported
        dry_run: Parse but don't save to RAG
    """
    from src.knowledge.pdf_importer import PDFHistoryImporter

    pdf_path = Path(file_path)

    if not pdf_path.exists():
        logger.error(f"File not found: {pdf_path}")
        return

    if not pdf_path.suffix.lower() == '.pdf':
        logger.error(f"Not a PDF file: {pdf_path}")
        return

    importer = PDFHistoryImporter(pdf_path.parent)

    if dry_run:
        logger.info(f"[DRY-RUN] Parsing PDF: {pdf_path}")

        # Extract and parse without saving
        text, images = importer._extract_text_and_images(pdf_path)
        logger.info(f"Extracted {len(text)} chars, {len(images)} images")

        if text:
            logger.info(f"Text preview:\n{text[:500]}...")

            # Analyze images
            image_analyses = await importer._analyze_images_with_vision(images)
            logger.info(f"Analyzed {len(image_analyses)} images")

            # Parse with Claude
            result = await importer._parse_with_claude(text, image_analyses)

            logger.info("\n=== Parsed Result ===")
            logger.info(f"Title: {result.title}")
            logger.info(f"Customer: {result.customer}")
            logger.info(f"Category: {result.category}")
            logger.info(f"Query Summary: {result.query_summary[:200]}...")
            logger.info(f"Solution: {result.solution[:200]}...")
            logger.info(f"Slack URL: {result.slack_url}")
            logger.info(f"Author: {result.author}")
            logger.info(f"Referenced Docs: {result.referenced_docs}")
            logger.info(f"Confidence: {result.confidence}")
            logger.info(f"Image Analyses: {len(result.image_analyses)}")
            logger.info("[DRY-RUN] Not saving to RAG")
    else:
        entry_id = await importer.import_pdf(pdf_path, force=force)

        if entry_id:
            logger.info(f"Successfully imported: {pdf_path.name} -> {entry_id}")
        else:
            logger.warning(f"Failed to import or already imported: {pdf_path.name}")


async def import_all_files(pdf_dir: str = "Q&A history", force: bool = False, dry_run: bool = False):
    """Import all PDF files from directory.

    Args:
        pdf_dir: Directory containing PDF files
        force: Force re-import all files
        dry_run: Parse but don't save to RAG
    """
    from src.knowledge.pdf_importer import PDFHistoryImporter

    pdf_path = Path(pdf_dir)

    if not pdf_path.exists():
        logger.error(f"Directory not found: {pdf_path}")
        return

    pdf_files = list(pdf_path.glob("*.pdf"))

    if not pdf_files:
        logger.warning(f"No PDF files found in: {pdf_path}")
        return

    logger.info(f"Found {len(pdf_files)} PDF files in {pdf_path}")

    if dry_run:
        logger.info("[DRY-RUN] Will parse files without saving")

        for pdf_file in pdf_files:
            logger.info(f"\n{'='*50}")
            await import_single_file(str(pdf_file), force=force, dry_run=True)
    else:
        importer = PDFHistoryImporter(pdf_dir)
        results = await importer.import_all(force=force)

        logger.info("\n=== Import Summary ===")
        logger.info(f"Success: {results['success']}")
        logger.info(f"Skipped (already imported): {results['skipped']}")
        logger.info(f"Failed: {results['failed']}")

        if results['imported_entries']:
            logger.info("\nImported entries:")
            for entry in results['imported_entries']:
                logger.info(f"  - {entry['filename']} -> {entry['entry_id']}")

        if results['errors']:
            logger.info("\nErrors:")
            for error in results['errors']:
                logger.error(f"  - {error['filename']}: {error['error']}")


async def list_imported():
    """List already imported PDF files."""
    from src.knowledge.pdf_importer import PDFHistoryImporter

    importer = PDFHistoryImporter()
    tracker = importer._tracker

    if not tracker.get("imported_files"):
        logger.info("No files have been imported yet.")
        return

    logger.info("=== Imported PDF Files ===")
    for filename, info in tracker["imported_files"].items():
        logger.info(f"  - {filename}")
        logger.info(f"    Hash: {info.get('hash', 'N/A')}")
        logger.info(f"    Imported: {info.get('imported_at', 'N/A')}")
        logger.info(f"    Entry ID: {info.get('entry_id', 'N/A')}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Import Q&A history from PDF files into History RAG"
    )
    parser.add_argument(
        "--file", "-f",
        type=str,
        help="Import a specific PDF file"
    )
    parser.add_argument(
        "--dir", "-d",
        type=str,
        default="Q&A history",
        help="Directory containing PDF files (default: 'Q&A history')"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-import even if already imported"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and show results without saving to RAG"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List already imported files (local tracker)"
    )

    args = parser.parse_args()

    logger.info("PDF History Importer")
    logger.info("-" * 40)

    if args.list:
        asyncio.run(list_imported())
    elif args.file:
        asyncio.run(import_single_file(args.file, force=args.force, dry_run=args.dry_run))
    else:
        asyncio.run(import_all_files(args.dir, force=args.force, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
