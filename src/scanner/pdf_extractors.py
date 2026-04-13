"""Cascading PDF text extraction for privacy policy documents.

Architecture:
    PdfplumberExtractor   → local extraction (machine-generated PDFs)
    YandexVisionExtractor → cloud OCR via Yandex Cloud OCR API (DEC-004)
    extract_pdf_text()    → tries extractors in cascade order, returns best result

Design principle: extractors never raise — they return ExtractionResult with
error set. The caller decides what to do with failed extractions.
"""
from __future__ import annotations

import base64
import logging
import os
import re
import time
from dataclasses import dataclass
from io import BytesIO
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)

_MIN_USABLE_TEXT_LEN = 50


@dataclass
class ExtractionResult:
    """Result of a single PDF text extraction attempt."""

    text: str | None
    method: str  # "pdfplumber", "yandex_vision", "failed"
    error: str | None = None


class PdfExtractor(Protocol):
    """Protocol for PDF text extractors.

    Each extractor must implement extract() and never raise — errors are
    captured in ExtractionResult.error instead.
    """

    def extract(self, pdf_bytes: bytes) -> ExtractionResult:
        ...


class PdfplumberExtractor:
    """Extract text from machine-generated PDFs using pdfplumber.

    Works only with PDFs that have embedded text (ToUnicode mapping).
    Scanned or image-only PDFs will produce empty/short text → error set.
    """

    def extract(self, pdf_bytes: bytes) -> ExtractionResult:
        try:
            import pdfplumber
        except ImportError:
            logger.warning("pdfplumber not installed — PDF policy extraction unavailable")
            return ExtractionResult(
                text=None, method="pdfplumber", error="pdfplumber_not_installed"
            )

        try:
            with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
                parts: list[str] = []
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        parts.append(page_text)
                text = "\n".join(parts).strip()
        except Exception as e:
            logger.warning("pdfplumber failed to open PDF: %s", e)
            return ExtractionResult(
                text=None, method="pdfplumber", error=f"pdfplumber_error: {e}"
            )

        if len(text) < _MIN_USABLE_TEXT_LEN:
            logger.info(
                "PDF text too short (%d chars) — likely scanned or empty document", len(text)
            )
            return ExtractionResult(text=None, method="pdfplumber", error="text_too_short")

        return ExtractionResult(text=text[:20000], method="pdfplumber", error=None)


_YANDEX_OCR_URL = "https://ocr.api.cloud.yandex.net/ocr/v1/recognizeText"
_RETRY_ATTEMPTS = 2          # HTTP 5xx: max attempts
_RETRY_ATTEMPTS_NETWORK = 3  # network errors (ConnectTimeout, ReadTimeout): max attempts
_RETRY_BASE_DELAY = 2.0      # seconds; doubles each retry
_PAGE_DELAY = 0.3            # seconds between pages to avoid rate limiting


class YandexVisionExtractor:
    """OCR-based PDF extraction via Yandex Cloud OCR API.

    Splits the PDF into individual pages, renders each page to a PNG image
    at resolution=200, and sends one image per API request. Required because
    the API enforces a hard limit of 1 page per request.

    See DEC-004 for rationale and CASE-008 for the el-ed.ru 400 diagnosis.

    Requires env vars: YANDEX_VISION_API_KEY, YANDEX_FOLDER_ID.
    If missing → ExtractionResult(text=None, error="missing_credentials").

    Retry policy (per page):
        - HTTP 5xx: 2 attempts total, exponential backoff (2s between retries)
        - Network errors (ConnectTimeout, ReadTimeout): 3 attempts total
        - No retry on HTTP 4xx (configuration problem, abort immediately)
        - 0.3s pause between pages to avoid rate limiting
    """

    MAX_PAGES = 8  # process at most this many pages per PDF

    def extract(self, pdf_bytes: bytes) -> ExtractionResult:
        api_key = os.environ.get("YANDEX_VISION_API_KEY", "").strip()
        folder_id = os.environ.get("YANDEX_FOLDER_ID", "").strip()

        if not api_key or not folder_id:
            logger.warning(
                "YandexVisionExtractor: YANDEX_VISION_API_KEY or YANDEX_FOLDER_ID not set"
            )
            return ExtractionResult(
                text=None, method="yandex_vision", error="missing_credentials"
            )

        # Render PDF pages → PNG images (one per page, API limit = 1 page/request)
        try:
            import pdfplumber
            with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
                pages = pdf.pages[: self.MAX_PAGES]
                total = len(pages)
                page_images: list[bytes] = []
                for page in pages:
                    buf = BytesIO()
                    page.to_image(resolution=200).save(buf, format="PNG")
                    page_images.append(buf.getvalue())
        except ImportError:
            logger.warning("YandexVisionExtractor: pdfplumber not installed")
            return ExtractionResult(
                text=None, method="yandex_vision", error="pdfplumber_not_installed"
            )
        except Exception as e:
            logger.warning("YandexVisionExtractor: failed to render PDF pages: %s", e)
            return ExtractionResult(
                text=None, method="yandex_vision", error=f"pdf_render_error: {e}"
            )

        if not page_images:
            return ExtractionResult(text=None, method="yandex_vision", error="empty_pdf")

        # Recognize each page via OCR API
        page_texts: list[str] = []
        last_error: str = "unknown"

        for page_num, png_bytes in enumerate(page_images, 1):
            if page_num > 1:
                time.sleep(_PAGE_DELAY)

            text, error = self._recognize_page(
                png_bytes, api_key, folder_id, page_num, total
            )
            if error is not None and error.startswith("http_4"):
                # 4xx = configuration/auth problem → abort immediately, no point continuing
                return ExtractionResult(text=None, method="yandex_vision", error=error)
            if text is not None:
                page_texts.append(text)
            else:
                last_error = error or "unknown"

        if not page_texts:
            return ExtractionResult(text=None, method="yandex_vision", error=last_error)

        full_text = "\n".join(page_texts).strip()
        if len(full_text) < _MIN_USABLE_TEXT_LEN:
            return ExtractionResult(text=None, method="yandex_vision", error="empty_text")

        logger.info(
            "YandexVisionExtractor: extracted %d chars from %d/%d pages",
            len(full_text), len(page_texts), total,
        )
        return ExtractionResult(text=full_text[:20000], method="yandex_vision", error=None)

    def _recognize_page(
        self,
        png_bytes: bytes,
        api_key: str,
        folder_id: str,
        page_num: int,
        total: int,
    ) -> tuple[str | None, str | None]:
        """Send one page PNG to Yandex OCR API. Returns (text, error)."""
        content_b64 = base64.b64encode(png_bytes).decode("ascii")
        payload = {
            "mimeType": "image/png",
            "languageCodes": ["ru", "en"],
            "model": "page",
            "content": content_b64,
        }
        headers = {
            "Authorization": f"Api-Key {api_key}",
            "x-folder-id": folder_id,
            "Content-Type": "application/json",
        }

        last_error: str = "unknown"
        for attempt in range(_RETRY_ATTEMPTS_NETWORK):
            if attempt > 0:
                time.sleep(_RETRY_BASE_DELAY * (2 ** (attempt - 1)))
            try:
                with httpx.Client(timeout=30.0, trust_env=False) as client:
                    resp = client.post(_YANDEX_OCR_URL, json=payload, headers=headers)

                if 400 <= resp.status_code < 500:
                    logger.warning(
                        "YandexVisionExtractor: page %d/%d HTTP %d (no retry)",
                        page_num, total, resp.status_code,
                    )
                    return None, f"http_{resp.status_code}"

                if resp.status_code != 200:
                    last_error = f"http_{resp.status_code}"
                    logger.warning(
                        "YandexVisionExtractor: page %d/%d HTTP %d, attempt %d/%d",
                        page_num, total, resp.status_code, attempt + 1, _RETRY_ATTEMPTS,
                    )
                    if attempt + 1 >= _RETRY_ATTEMPTS:
                        break  # HTTP 5xx: stop after _RETRY_ATTEMPTS tries
                    continue

                data = resp.json()
                text: str = (
                    (data.get("result") or {})
                    .get("textAnnotation", {})
                    .get("fullText", "")
                ) or ""

                logger.info(
                    "YandexOCR: page %d/%d OK (%d chars)", page_num, total, len(text)
                )
                return text, None

            except Exception as e:
                last_error = f"network_error: {type(e).__name__}"
                logger.warning(
                    "YandexVisionExtractor: page %d/%d %s, attempt %d/%d",
                    page_num, total, last_error, attempt + 1, _RETRY_ATTEMPTS_NETWORK,
                )

        logger.warning(
            "YandexVisionExtractor: page %d/%d failed after %d attempts: %s",
            page_num, total, _RETRY_ATTEMPTS, last_error,
        )
        return None, last_error


def _is_russian(text: str) -> bool:
    """Return True if the text is predominantly Russian (Cyrillic).

    Counts the share of Cyrillic letters among all alphabetic characters.
    Requires at least 50% Cyrillic and at least 50 Cyrillic characters total
    (the latter guards against short fragments that pass the ratio check by chance).
    Works correctly for OCR output where words are separated by spaces and newlines.
    """
    cyrillic = sum(1 for c in text if "\u0400" <= c <= "\u04FF")
    alpha = sum(1 for c in text if c.isalpha())
    return alpha > 0 and cyrillic / alpha >= 0.5 and cyrillic >= 50


def extract_pdf_text(pdf_bytes: bytes) -> ExtractionResult:
    """Try extractors in cascade order and return the first usable result.

    Cascade:
        1. PdfplumberExtractor  — local extraction (fast, no network)
        2. YandexVisionExtractor — cloud OCR (stub in session 1.1)
        3. All failed → ExtractionResult(text=None, method="failed",
                                         error="all_extractors_failed")

    An extractor result is accepted when:
        - result.text is not None
        - is_valid_policy_text(result.text, _is_russian(result.text)) is True
    """
    from src.scanner.utils import is_valid_policy_text

    extractors: list[PdfExtractor] = [
        PdfplumberExtractor(),
        YandexVisionExtractor(),
    ]

    for extractor in extractors:
        result = extractor.extract(pdf_bytes)
        if result.text is not None and is_valid_policy_text(
            result.text, _is_russian(result.text)
        ):
            return result

    return ExtractionResult(text=None, method="failed", error="all_extractors_failed")
