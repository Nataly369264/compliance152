"""Async website crawler for 152-FZ compliance scanning."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from src.knowledge.loader import get_prohibited_service_by_domain
from src.models.scan import (
    CookieInfo,
    FormInfo,
    PageInfo,
    PrivacyPolicyInfo,
    SSLInfo,
    ScanResult,
)
from src.scanner.detectors import (
    detect_cookie_banner,
    detect_external_scripts,
    detect_footer_privacy_link,
    extract_banner_policy_links,
    extract_forms,
    is_privacy_policy_page,
)
from src.scanner.pdf_extractor import (
    is_pdf_content_type,
    is_pdf_url,
)
from src.scanner.pdf_extractors import _is_russian as _is_russian_text, extract_pdf_text
from src.scanner.utils import (
    FALLBACK_PRIVACY_PATHS,
    is_same_domain,
    is_valid_policy_text,
    normalize_url,
    should_skip,
)

logger = logging.getLogger(__name__)


class SiteScanner:
    """Crawls a website and extracts 152-FZ relevant data."""

    def __init__(
        self,
        max_pages: int = 50,
        timeout: int = 30,
        crawl_delay: float = 1.0,
    ):
        self.max_pages = max_pages
        self.timeout = timeout
        self.crawl_delay = crawl_delay

    async def scan(self, url: str) -> ScanResult:
        """Scan a website and return structured results."""
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        parsed = urlparse(url)
        base_domain = parsed.netloc.lower()

        visited: set[str] = set()
        to_visit: list[str] = [url]
        queued: set[str] = {normalize_url(url)}  # tracks what's already in to_visit

        pages: list[PageInfo] = []
        all_forms: list[FormInfo] = []
        all_scripts: list[dict] = []
        all_cookies: list[CookieInfo] = []
        cookie_banner_info = None
        pp_candidates: list[PrivacyPolicyInfo] = []
        ssl_info = SSLInfo()
        errors: list[str] = []

        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            verify=True,
            headers={"User-Agent": "Compliance152Bot/0.1 (+https://compliance152.ru)"},
        ) as client:
            # Check SSL
            ssl_info = await self._check_ssl(client, url)

            while to_visit and len(visited) < self.max_pages:
                current_url = to_visit.pop(0)
                normalized = normalize_url(current_url)

                if normalized in visited:
                    continue
                if not is_same_domain(normalized, base_domain):
                    continue
                # Privacy policy PDFs bypass the skip filter (Bug A fix)
                if should_skip(normalized) and not is_privacy_policy_page(current_url):
                    continue

                visited.add(normalized)

                try:
                    resp = await client.get(current_url)

                    if 400 <= resp.status_code < 500:
                        errors.append(f"{current_url}: HTTP {resp.status_code}")
                        continue

                    content_type = resp.headers.get("content-type", "")

                    # PDF branch: policy document served as PDF
                    if is_pdf_content_type(content_type) or is_pdf_url(current_url):
                        if is_privacy_policy_page(current_url):
                            candidate = self._extract_privacy_policy_from_pdf(
                                resp.content, current_url,
                            )
                            if candidate.found:
                                pp_candidates.append(candidate)
                        continue

                    if "text/html" not in content_type:
                        continue

                    html = resp.text
                    soup = BeautifulSoup(html, "lxml")
                    title = soup.title.string.strip() if soup.title and soup.title.string else None

                    # Collect cookies
                    for name, value in resp.cookies.items():
                        cookie = CookieInfo(
                            name=name,
                            domain=base_domain,
                            secure=current_url.startswith("https"),
                        )
                        if not any(c.name == name for c in all_cookies):
                            all_cookies.append(cookie)

                    # Check footer for privacy link
                    has_footer_link, footer_link_url = detect_footer_privacy_link(soup)

                    # Extract forms
                    page_forms = self._extract_forms(soup, current_url)
                    all_forms.extend(page_forms)

                    # External scripts
                    ext_scripts = detect_external_scripts(soup, current_url)
                    # Mark prohibited
                    for script in ext_scripts:
                        svc = get_prohibited_service_by_domain(script.domain)
                        if svc:
                            script.is_prohibited = True
                            script.service_name = svc["name"]

                    all_scripts.extend(ext_scripts)

                    # Cookie banner (detect once)
                    if cookie_banner_info is None:
                        banner = detect_cookie_banner(soup)
                        if banner.found:
                            # Check if analytics loads before consent
                            analytics_re = re.compile(
                                r"(google-analytics|googletagmanager|mc\.yandex\.ru|metrika)",
                                re.IGNORECASE)
                            analytics_before = any(
                                analytics_re.search(s.url) for s in ext_scripts
                            )
                            banner.analytics_before_consent = analytics_before
                            cookie_banner_info = banner

                            # Follow privacy policy links from the banner
                            for bl in extract_banner_policy_links(soup, current_url):
                                norm_bl = normalize_url(bl)
                                if (norm_bl not in visited and norm_bl not in queued
                                        and is_same_domain(norm_bl, base_domain)):
                                    queued.add(norm_bl)
                                    to_visit.insert(0, bl)

                    # Privacy policy detection — collect all candidates
                    if is_privacy_policy_page(current_url, title):
                        candidate = self._extract_privacy_policy(
                            soup, current_url, has_footer_link)
                        if candidate.found:
                            pp_candidates.append(candidate)

                    # Page info
                    pages.append(PageInfo(
                        url=current_url,
                        title=title,
                        status_code=resp.status_code,
                        has_privacy_link_in_footer=has_footer_link,
                        forms_count=len(page_forms),
                        external_scripts_count=len(ext_scripts),
                    ))

                    # Discover new links
                    for a in soup.find_all("a", href=True):
                        href = a["href"]
                        abs_url = urljoin(current_url, href)
                        norm = normalize_url(abs_url)
                        if (
                            norm not in visited
                            and norm not in queued
                            and is_same_domain(norm, base_domain)
                        ):
                            queued.add(norm)
                            # Prioritize privacy policy pages
                            link_text = a.get_text(separator=" ", strip=True)
                            if is_privacy_policy_page(abs_url, link_text):
                                to_visit.insert(0, abs_url)
                            else:
                                to_visit.append(abs_url)

                except Exception as e:
                    logger.warning("Error scanning %s: %s", current_url, e)
                    errors.append(f"{current_url}: {type(e).__name__}: {e}")

                if self.crawl_delay > 0 and to_visit:
                    await asyncio.sleep(self.crawl_delay)

            # Fallback: try well-known paths if crawl found nothing
            if not pp_candidates:
                fallback = await self._try_fallback_privacy_urls(
                    client, base_domain, visited, pages,
                )
                if fallback:
                    pp_candidates.extend(fallback)

        # Select best candidate or fall back to URL-only marker
        if pp_candidates:
            privacy_policy_info = self._select_best_policy(pp_candidates)
        else:
            privacy_policy_info = PrivacyPolicyInfo()
            # 1) Pages successfully crawled (have title info)
            for page in pages:
                if is_privacy_policy_page(page.url, page.title):
                    privacy_policy_info.found = True
                    privacy_policy_info.url = page.url
                    break
            # 2) Visited URLs that errored during crawl (no text, URL-only)
            if not privacy_policy_info.found:
                policy_visited = [
                    u for u in visited if is_privacy_policy_page(u)
                ]
                if policy_visited:
                    best_url = max(policy_visited, key=self._url_priority)
                    privacy_policy_info.found = True
                    privacy_policy_info.url = best_url

        # Deduplicate external scripts
        seen_urls: set[str] = set()
        unique_scripts = []
        for s in all_scripts:
            if s.url not in seen_urls:
                seen_urls.add(s.url)
                unique_scripts.append(s)

        return ScanResult(
            url=url,
            pages=pages,
            forms=all_forms,
            cookies=all_cookies,
            external_scripts=unique_scripts,
            privacy_policy=privacy_policy_info,
            ssl_info=ssl_info,
            cookie_banner=cookie_banner_info or detect_cookie_banner(BeautifulSoup("", "lxml")),
            pages_scanned=len(pages),
            errors=errors,
        )

    async def _try_fallback_privacy_urls(
        self,
        client: httpx.AsyncClient,
        base_domain: str,
        visited: set[str],
        pages: list[PageInfo],
    ) -> list[PrivacyPolicyInfo]:
        """Try well-known privacy policy URL paths and return all found candidates."""
        candidates: list[PrivacyPolicyInfo] = []
        for path in FALLBACK_PRIVACY_PATHS:
            url = f"https://{base_domain}{path}"
            norm = normalize_url(url)
            if norm in visited:
                continue
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    continue
                content_type = resp.headers.get("content-type", "")

                # PDF fallback: well-known path returned a PDF document
                if is_pdf_content_type(content_type) or is_pdf_url(url):
                    result = self._extract_privacy_policy_from_pdf(resp.content, url)
                    if result.found:
                        pages.append(PageInfo(
                            url=url,
                            title="Политика обработки ПДн (PDF)",
                            status_code=resp.status_code,
                        ))
                        candidates.append(result)
                    continue

                if "text/html" not in content_type:
                    continue
                soup = BeautifulSoup(resp.text, "lxml")
                title = soup.title.string.strip() if soup.title and soup.title.string else None
                if is_privacy_policy_page(url, title):
                    has_footer_link, _ = detect_footer_privacy_link(soup)
                    pages.append(PageInfo(
                        url=url,
                        title=title,
                        status_code=resp.status_code,
                        has_privacy_link_in_footer=has_footer_link,
                    ))
                    candidates.append(self._extract_privacy_policy(soup, url, has_footer_link))
            except Exception as e:
                logger.debug("Fallback privacy URL %s failed: %s", url, e)
        return candidates

    def _extract_forms(self, soup: BeautifulSoup, page_url: str) -> list[FormInfo]:
        """Delegate to detectors.extract_forms (shared with PlaywrightCrawler)."""
        return extract_forms(soup, page_url)

    def _extract_privacy_policy(
        self, soup: BeautifulSoup, url: str, has_footer_link: bool,
    ) -> PrivacyPolicyInfo:
        """Extract and analyze privacy policy content from HTML."""
        text = soup.get_text(separator="\n", strip=True)
        return self._extract_privacy_policy_from_text(text, url, has_footer_link)

    def _extract_privacy_policy_from_pdf(
        self, content: bytes, url: str,
    ) -> PrivacyPolicyInfo:
        """Extract privacy policy from a PDF document.

        If pdfplumber cannot extract usable text (scanned/empty PDF), returns
        PrivacyPolicyInfo(found=True, text=None) so the analyzer can mark
        content checks as NOT_APPLICABLE instead of generating false violations.
        The scan_limitations field is populated by the analyzer._build_scan_limitations().
        """
        extraction = extract_pdf_text(content)
        if extraction.text is None:
            logger.info("PDF policy at %s: text not extractable (scanned or empty)", url)
            return PrivacyPolicyInfo(
                found=True,
                url=url,
                text=None,
                is_separate_page=True,
                extraction_method=extraction.method,
            )
        # Reuse HTML extraction logic on the extracted text
        result = self._extract_privacy_policy_from_text(extraction.text, url, has_footer_link=False)
        result.extraction_method = extraction.method
        return result

    def _extract_privacy_policy_from_text(
        self, text: str, url: str, has_footer_link: bool,
    ) -> PrivacyPolicyInfo:
        """Build PrivacyPolicyInfo from plain text (shared by HTML and PDF paths)."""
        text_lower = text.lower()
        text_hash = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        content_length = len(text)
        return PrivacyPolicyInfo(
            found=True,
            url=url,
            text=text[:100000],
            text_hash=text_hash,
            fetched_at=datetime.utcnow(),
            content_length=content_length,
            in_footer=has_footer_link,
            has_operator_name=bool(re.search(
                r"(общество с ограниченной|акционерное общество|индивидуальный предприниматель|ООО|АО|ИП)",
                text)),
            has_inn_ogrn=bool(re.search(
                r"(инн\s*[:\-]?\s*\d{10,12}|огрн\s*[:\-]?\s*\d{13,15}|"
                r"inn\s*[:\-]?\s*\d{10,12}|ogrn\s*[:\-]?\s*\d{13,15})",
                text_lower)),
            has_responsible_person=bool(
                re.search(
                    r"(ответственн.{0,30}(обработк|организац|персональн)|"
                    r"dpo|data.?protection.?officer|"
                    r"(обращени|запрос).{0,30}(данн|персональн))",
                    text_lower,
                ) and re.search(
                    r"([a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}|"
                    r"тел[\s.:]*[\d\s\-\+\(\)]{7,}|"
                    r"phone[\s.:]*[\d\s\-\+\(\)]{7,})",
                    text_lower,
                )
            ),
            has_data_categories=bool(re.search(
                r"(категори.{0,20}данн|перечень.{0,20}данн|обрабатыва.{0,30}данн)",
                text_lower)),
            has_purposes=bool(re.search(r"(цел.{0,20}обработк|purpose)", text_lower)),
            has_legal_basis=bool(re.search(
                r"(правов.{0,20}основан|закон.{0,20}основан|legal.?basis|на основании"
                r"|основан.{0,20}обработк)",
                text_lower)),
            has_retention_periods=bool(re.search(
                r"(срок.{0,20}хранен|срок.{0,20}обработк|период.{0,20}хранен)",
                text_lower)),
            has_subject_rights=bool(re.search(
                r"(прав.{0,20}субъект|прав.{0,20}пользовател|right.{0,10}data.?subject)",
                text_lower)),
            has_rights_procedure=bool(re.search(
                r"(порядок.{0,20}реализац|порядок.{0,20}обращен|10.{0,10}рабочих|направить.{0,20}запрос"
                r"|направить.{0,20}(обращен|заявлен)|требова.{0,30}(уточнени|блокировани|уничтожени))",
                text_lower)),
            has_cross_border_info=bool(re.search(
                r"(трансграничн|cross.?border|передач.{0,20}за рубеж|иностранн.{0,20}государств)",
                text_lower)),
            has_security_measures=bool(re.search(
                r"(мер.{0,20}безопасност|мер.{0,20}защит|security.?measure|шифрован|encrypt"
                r"|режим.{0,20}защит|защит.{0,10}конфиденциальн)",
                text_lower)),
            has_cookie_info=bool(re.search(r"(cookie|куки|файл.{0,10}cookie)", text_lower)),
            has_localization_statement=bool(re.search(
                r"(территори.{0,30}российской\s+федерации|"
                r"территори.{0,20}(росс|рф)|"
                r"серверах.{0,10}в\s+росс|"
                r"хранятся.{0,10}(в\s+рф|в\s+росс)|"
                r"российские.{0,10}серверы|"
                r"server.{0,20}russia|локализац)",
                text_lower)),
            has_date=bool(re.search(
                r"(\d{2}\.\d{2}\.\d{4}|дата.{0,20}(публикац|обновлен|утвержден))",
                text_lower)),
            is_russian=_is_russian_text(text),
            is_separate_page=True,
        )

    async def _check_ssl(self, client: httpx.AsyncClient, url: str) -> SSLInfo:
        """Check if the site uses HTTPS."""
        parsed = urlparse(url)
        https_url = url.replace("http://", "https://") if parsed.scheme == "http" else url
        try:
            await client.head(https_url)
            return SSLInfo(
                has_ssl=True,
                certificate_valid=True,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return SSLInfo(has_ssl=False, certificate_valid=False)
        except Exception:
            return SSLInfo(has_ssl=parsed.scheme == "https")

    @staticmethod
    def _normalize_url(url: str) -> str:
        return normalize_url(url)

    @staticmethod
    def _is_same_domain(url: str, base_domain: str) -> bool:
        return is_same_domain(url, base_domain)

    @staticmethod
    def _should_skip(url: str) -> bool:
        return should_skip(url)

    @staticmethod
    def _url_priority(url: str) -> int:
        """Score URL by policy-keyword specificity (higher = better)."""
        path = urlparse(url).path.lower()
        if "politika" in path:
            return 4
        if "policy" in path:
            return 3
        if "privacy" in path:
            return 2
        if "personal" in path:
            return 1
        return 0

    @classmethod
    def _select_best_policy(cls, candidates: list[PrivacyPolicyInfo]) -> PrivacyPolicyInfo:
        """Pick the best privacy policy candidate.

        Priority:
        1. Longest text (most complete document)
        2. URL keyword score: politika > policy > privacy > personal
        3. First found (stable tie-break)

        All candidates are validated first; only those passing is_valid_policy_text
        are considered. This ensures a long-but-invalid candidate (e.g. a WAF
        challenge page or a non-Russian OCR result) does not block a shorter but
        valid candidate from being selected.
        """
        valid = [c for c in candidates if is_valid_policy_text(c.text, c.is_russian)]
        if not valid:
            return PrivacyPolicyInfo()
        return max(
            valid,
            key=lambda p: (len(p.text or ""), cls._url_priority(p.url or "")),
        )
