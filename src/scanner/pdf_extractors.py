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


_YANDEX_OCR_URL = "https://ocr.api.cloud.yandex.net/ocr/v1/recognizeFile"
_RETRY_ATTEMPTS = 2
_RETRY_BASE_DELAY = 2.0  # seconds; doubles each retry


class YandexVisionExtractor:
    """OCR-based PDF extraction via Yandex Cloud OCR API.

    Sends the PDF as base64 content to Yandex Vision OCR and extracts the
    full text from the response. See DEC-004 for rationale.

    Requires env vars: YANDEX_VISION_API_KEY, YANDEX_FOLDER_ID.
    If missing → ExtractionResult(text=None, error="missing_credentials").

    Retry policy:
        - 2 attempts total, exponential backoff (2s between retries)
        - Retries on network errors and HTTP 5xx
        - No retry on HTTP 4xx (configuration problem, retrying won't help)
        - No retry if Vision returns empty text (deterministic result)
    """

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

        content_b64 = base64.b64encode(pdf_bytes).decode("ascii")
        payload = {
            "mimeType": "application/pdf",
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
        for attempt in range(_RETRY_ATTEMPTS):
            if attempt > 0:
                time.sleep(_RETRY_BASE_DELAY * (2 ** (attempt - 1)))
            try:
                with httpx.Client(timeout=30.0, trust_env=False) as client:
                    resp = client.post(_YANDEX_OCR_URL, json=payload, headers=headers)

                if 400 <= resp.status_code < 500:
                    logger.warning(
                        "YandexVisionExtractor: HTTP %d (no retry)", resp.status_code
                    )
                    return ExtractionResult(
                        text=None,
                        method="yandex_vision",
                        error=f"http_{resp.status_code}",
                    )

                if resp.status_code != 200:
                    last_error = f"http_{resp.status_code}"
                    logger.warning(
                        "YandexVisionExtractor: HTTP %d, attempt %d/%d",
                        resp.status_code, attempt + 1, _RETRY_ATTEMPTS,
                    )
                    continue

                data = resp.json()
                full_text: str = (
                    (data.get("result") or {})
                    .get("textAnnotation", {})
                    .get("fullText", "")
                ) or ""

                if len(full_text.strip()) < _MIN_USABLE_TEXT_LEN:
                    logger.info(
                        "YandexVisionExtractor: Vision returned empty/short text (%d chars)",
                        len(full_text),
                    )
                    return ExtractionResult(
                        text=None, method="yandex_vision", error="empty_text"
                    )

                logger.info(
                    "YandexVisionExtractor: extracted %d chars via Yandex Vision OCR",
                    len(full_text),
                )
                return ExtractionResult(
                    text=full_text[:20000], method="yandex_vision", error=None
                )

            except Exception as e:
                last_error = f"network_error: {type(e).__name__}"
                logger.warning(
                    "YandexVisionExtractor: %s, attempt %d/%d",
                    last_error, attempt + 1, _RETRY_ATTEMPTS,
                )

        return ExtractionResult(text=None, method="yandex_vision", error=last_error)


def _is_russian(text: str) -> bool:
    """Return True if text contains a sequence of 20+ consecutive Cyrillic characters."""
    return bool(re.search(r"[а-яА-ЯёЁ]{20,}", text))


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
