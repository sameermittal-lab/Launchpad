"""Extract text from PDF files using pdfplumber (primary) and pymupdf (fallback)."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def extract_text_from_pdf(pdf_path: Path) -> str:
    """Extract all text from a PDF file.

    Tries pdfplumber first (usually cleaner layout), falls back to pymupdf
    if extraction is empty or fails.
    """
    # Try pdfplumber first
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            chunks = []
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    chunks.append(text)
        result = "\n\n".join(chunks).strip()
        if result and len(result) > 100:
            return result
        logger.info(f"pdfplumber extracted too little ({len(result)} chars), trying pymupdf")
    except Exception as exc:
        logger.warning(f"pdfplumber failed for {pdf_path}: {exc}")

    # Fallback to pymupdf (fitz)
    try:
        import fitz  # pymupdf
        doc = fitz.open(pdf_path)
        chunks = []
        for page in doc:
            text = page.get_text()
            if text:
                chunks.append(text)
        doc.close()
        return "\n\n".join(chunks).strip()
    except Exception as exc:
        logger.error(f"pymupdf also failed for {pdf_path}: {exc}")
        raise ValueError(f"Could not extract text from PDF: {exc}")
