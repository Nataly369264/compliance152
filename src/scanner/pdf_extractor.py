"""PDF text extraction for privacy policy documents.

Used when the privacy policy URL returns application/pdf instead of HTML.
pdfplumber works only with machine-generated PDFs; scanned (image-only) PDFs
will produce an empty string, which is treated as None (unreadable).
"""

from __future__ import annotations

import logging
from io import BytesIO

logger = logging.getLogger(__name__)

# Minimum character count to consider extracted text usable
_MIN_USABLE_TEXT_LEN = 50


def extract_text_from_pdf(content: bytes) -> str | None:
    """Extract plain text from PDF bytes using pdfplumber.

    Returns:
        Extracted text (stripped, up to 20 000 chars) if readable,
        or None if the PDF is empty, scanned, corrupted, or unreadable.
    """
    try:
        import pdfplumber
    except ImportError:
        logger.warning("pdfplumber not installed — PDF policy extraction unavailable")
        return None

    try:
        with pdfplumber.open(BytesIO(content)) as pdf:
            parts: list[str] = []
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    parts.append(page_text)
            text = "\n".join(parts).strip()
    except Exception as e:
        logger.warning("pdfplumber failed to open PDF: %s", e)
        return None

    if len(text) < _MIN_USABLE_TEXT_LEN:
        logger.info(
            "PDF text too short (%d chars) — likely scanned or empty document", len(text)
        )
        return None

    return text[:20000]  # Same limit as HTML policy text


def is_pdf_content_type(content_type: str) -> bool:
    """Return True if the Content-Type header indicates a PDF response."""
    return "application/pdf" in content_type.lower()


def is_pdf_url(url: str) -> bool:
    """Return True if the URL path ends with .pdf (case-insensitive)."""
    from urllib.parse import urlparse
    path = urlparse(url).path.lower()
    return path.endswith(".pdf")
