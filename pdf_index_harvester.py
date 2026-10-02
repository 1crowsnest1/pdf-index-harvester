#!/usr/bin/env python3
"""
PDF Index Harvester
===================
Harvest PDFs linked from any HTML index page, then extract embedded images
and generate 8×8 contact-sheet thumbnails for quick visual browsing.

Respect the source: this tool is intended for personal research and
archival use.  Always check the site's terms of service and use
reasonable rate limiting.
"""

from __future__ import annotations

import argparse
import glob
import math
import os
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from PIL import Image

try:
    import pymupdf as fitz
except ImportError:
    import fitz  # older package name


# ---------------------------------------------------------------------------
# Defaults (placeholders – override via CLI)
# ---------------------------------------------------------------------------
DEFAULT_INDEX_URL = "https://example.com/path/to/index.html"  # <-- replace
DEFAULT_PDF_DIR = "./pdf_archive"
DEFAULT_IMAGE_DIR = "./visual_catalog/extracted_images"
DEFAULT_GRID_DIR = "./visual_catalog/contact_sheets"
DEFAULT_DELAY = 1.0  # seconds between downloads (be polite)
USER_AGENT = "PDF-Index-Harvester/1.0 (research)"


# ---------------------------------------------------------------------------
# Phase 1 – Mirror
# ---------------------------------------------------------------------------
def fetch_pdf_links(index_url: str, session: requests.Session) -> list[str]:
    """Scrape an HTML index page and return sorted unique PDF URLs."""
    print(f"Fetching index: {index_url}")
    resp = session.get(index_url, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    links: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".pdf"):
            links.add(urljoin(index_url, href))
    return sorted(links)


def download_pdfs(
    pdf_urls: list[str],
    dest_dir: str,
    session: requests.Session,
    delay: float,
) -> None:
    """Download missing PDFs into dest_dir, with a polite delay."""
    os.makedirs(dest_dir, exist_ok=True)
    total = len(pdf_urls)

    for idx, url in enumerate(pdf_urls, 1):
        name = os.path.basename(url.split("?")[0])
        path = os.path.join(dest_dir, name)

        if os.path.exists(path) and os.path.getsize(path) > 0:
            print(f"[{idx}/{total}] Exists: {name}")
            continue

        print(f"[{idx}/{total}] Downloading: {name}...")
        try:
            with session.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
        except Exception as exc:
            print(f"    ERROR: {exc}")
            if os.path.exists(path):
                os.remove(path)

        if delay > 0:
            time.sleep(delay)

    print("\nArchive download complete.")


# ---------------------------------------------------------------------------
# Phase 2 – Extract images & contact sheets
# ---------------------------------------------------------------------------
def process_pdfs(
    pdf_dir: str,
    image_dir: str,
    grid_dir: str,
) -> None:
    """Extract embedded images and build 8×8 contact sheets."""
    os.makedirs(image_dir, exist_ok=True)
    os.makedirs(grid_dir, exist_ok=True)

    pdf_files = sorted(glob.glob(os.path.join(pdf_dir, "*.pdf")))
    if not pdf_files:
        print(f"No PDFs found in {pdf_dir}")
        return

    for pdf_path in pdf_files:
        pdf_name = Path(pdf_path).stem
        print(f"Processing: {pdf_name}")

        try:
            doc = fitz.open(pdf_path)
        except Exception as exc:
            print(f"  Could not open: {exc}")
            continue

        thumbs: list[Image.Image] = []

        for page_num in range(len(doc)):
            page = doc[page_num]

            # Extract every embedded raster image
            for img_idx, img in enumerate(page.get_images(full=True)):
                xref = img[0]
                try:
                    base = doc.extract_image(xref)
                    ext = base.get("ext", "png")
                    fname = f"{pdf_name}_p{page_num+1}_fig{img_idx}.{ext}"
                    with open(os.path.join(image_dir, fname), "wb") as f:
                        f.write(base["image"])
                except Exception:
                    pass  # skip corrupt / unsupported images

            # Low-res thumbnail for contact sheet
            pix = page.get_pixmap(dpi=50)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            img.thumbnail((200, 260))
            thumbs.append(img)

        # Build 8×8 contact sheets
        for sheet_idx in range(math.ceil(len(thumbs) / 64)):
            chunk = thumbs[sheet_idx * 64 : (sheet_idx + 1) * 64]
            sheet = Image.new("RGB", (200 * 8, 260 * 8), (15, 0, 15))
            for i, thumb in enumerate(chunk):
                sheet.paste(thumb, ((i % 8) * 200, (i // 8) * 260))
                thumb.close()
            out = os.path.join(
                grid_dir, f"{pdf_name}_contact_sheet_{sheet_idx+1}.jpg"
            )
            sheet.save(out, quality=85)

        doc.close()

    print("\nVisual extraction complete.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Mirror PDFs from an HTML index and build a visual catalog.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--index-url",
        default=DEFAULT_INDEX_URL,
        help="URL of the HTML index page that links to PDFs",
    )
    p.add_argument(
        "--pdf-dir", "-p",
        default=DEFAULT_PDF_DIR,
        help="Directory to store downloaded PDFs",
    )
    p.add_argument(
        "--image-dir", "-o",
        default=DEFAULT_IMAGE_DIR,
        help="Directory for extracted embedded images",
    )
    p.add_argument(
        "--grid-dir", "-g",
        default=DEFAULT_GRID_DIR,
        help="Directory for contact-sheet JPEGs",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help="Seconds to wait between PDF downloads (be polite)",
    )
    p.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip Phase 1 (assume PDFs already exist in --pdf-dir)",
    )
    p.add_argument(
        "--skip-extract",
        action="store_true",
        help="Skip Phase 2 (only download PDFs)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    if not args.skip_download:
        print("=== Phase 1: Mirroring archive ===")
        try:
            urls = fetch_pdf_links(args.index_url, session)
        except Exception as exc:
            print(f"Failed to fetch index: {exc}", file=sys.stderr)
            return 1
        print(f"Found {len(urls)} unique PDF links.")
        download_pdfs(urls, args.pdf_dir, session, args.delay)

    if not args.skip_extract:
        print("\n=== Phase 2: Extracting images & contact sheets ===")
        process_pdfs(args.pdf_dir, args.image_dir, args.grid_dir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
