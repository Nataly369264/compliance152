"""Playwright-based crawler for JS-rendered sites (152-FZ compliance scanning).

Usage: only when USE_PLAYWRIGHT=true or use_playwright=True is passed explicitly.
Falls back to httpx+BS4 (SiteScanner) on any Playwright error.

Requires: playwright install chromium  (one-time setup after pip install)
"""
from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urljoin, urlparse

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

logger = logging.getLogger(__name__)

_FALLBACK_PRIVACY_PATHS = [
    "/privacy-policy", "/privacy_policy", "/privacy",
    "/documents/privacy-policy", "/documents/privacy_policy",
    "/legal/privacy", "/legal/privacy-policy",
    "/info/privacy", "/pages/privacy-policy",
    "/personal-data", "/personalnyye-dannyye",
    "/politika-konfidencialnosti", "/obrabotka-personalnyh-dannyh",
]

_SKIP_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".zip", ".rar", ".tar", ".gz",
    ".mp3", ".mp4", ".avi", ".mov", ".wmv",
    ".woff", ".woff2", ".ttf", ".eot",
    ".css", ".js", ".json", ".xml",
})


class PlaywrightCrawler:
    """Crawls JS-rendered websites via headless Chromium.

    Returns the same ScanResult format as SiteScanner (crawler.py),
    so it can be used as a drop-in replacement at the call site.

    Limits vs SiteScanner:
    - max_pages=20 (not 50) — JS rendering is slower
    - 30 s timeout per page (networkidle wait)
    - On any Playwright error → falls back to SiteScanner and records
      the limitation in ScanResult.scan_limitations
    """

    def __init__(
        self,
        max_pages: int = 20,
        timeout: int = 30,
        crawl_delay: float = 1.0,
    ):
        self.max_pages = max_pages
        self.timeout = timeout
        self.crawl_delay = crawl_delay

    async def scan(self, url: str) -> ScanResult:
        """Scan a JS-rendered website and return structured ScanResult."""
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        try:
            from playwright.async_api import async_playwright, Error as PlaywrightError
        except ImportError:
            logger.warning("playwright not installed — falling back to static scanner")
            return await self._fallback(url, "Playwright не установлен")

        parsed = urlparse(url)
        base_domain = parsed.netloc.lower()

        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent="Compliance152Bot/0.1 (+https://compliance152.ru)",
                    ignore_https_errors=False,
                )
                try:
                    result = await self._crawl(context, url, base_domain)
                finally:
                    await browser.close()
            return result

        except Exception as exc:
            logger.warning("PlaywrightCrawler failed (%s) — falling back to static", exc)
            return await self._fallback(url, f"Playwright недоступен: {exc}")

    # ── Core crawl loop ───────────────────────────────────────────

    async def _crawl(self, context, url: str, base_domain: str) -> ScanResult:
        from playwright.async_api import Error as PlaywrightError

        visited: set[str] = set()
        to_visit: list[str] = [url]
        queued: set[str] = {self._normalize_url(url)}

        pages: list[PageInfo] = []
        all_forms: list[FormInfo] = []
        all_scripts: list = []
        all_cookies: list[CookieInfo] = []
        cookie_banner_info = None
        privacy_policy_info = PrivacyPolicyInfo()
        errors: list[str] = []

        # SSL: headless Chromium follows HTTPS natively; we just note scheme
        ssl_info = SSLInfo(
            has_ssl=url.startswith("https"),
            certificate_valid=url.startswith("https"),
        )

        while to_visit and len(visited) < self.max_pages:
            current_url = to_visit.pop(0)
            normalized = self._normalize_url(current_url)

            if normalized in visited:
                continue
            if not self._is_same_domain(normalized, base_domain):
                continue
            if self._should_skip(normalized):
                continue

            visited.add(normalized)

            try:
                page = await context.new_page()
                try:
                    await page.goto(
                        current_url,
                        timeout=self.timeout * 1000,
                        wait_until="networkidle",
                    )
                    html = await page.content()
                    soup = BeautifulSoup(html, "lxml")
                    title = soup.title.string.strip() if soup.title and soup.title.string else None

                    # Collect cookies from browser context (includes JS-set cookies)
                    pw_cookies = await context.cookies([current_url])
                    for c in pw_cookies:
                        cookie = CookieInfo(
                            name=c["name"],
                            domain=c.get("domain", base_domain).lstrip("."),
                            secure=c.get("secure", False),
                        )
                        if not any(existing.name == c["name"] for existing in all_cookies):
                            all_cookies.append(cookie)

                    # Footer privacy link
                    has_footer_link, footer_link_url = detect_footer_privacy_link(soup)

                    # Forms
                    page_forms = extract_forms(soup, current_url)
                    all_forms.extend(page_forms)

                    # External scripts
                    ext_scripts = detect_external_scripts(soup, current_url)
                    for script in ext_scripts:
                        svc = get_prohibited_service_by_domain(script.domain)
                        if svc:
                            script.is_prohibited = True
                            script.service_name = svc["name"]
                    all_scripts.extend(ext_scripts)

                    # Cookie banner (once)
                    if cookie_banner_info is None:
                        banner = detect_cookie_banner(soup)
                        if banner.found:
                            analytics_re = re.compile(
                                r"(google-analytics|googletagmanager|mc\.yandex\.ru|metrika)",
                                re.IGNORECASE,
                            )
                            banner.analytics_before_consent = any(
                                analytics_re.search(s.url) for s in ext_scripts
                            )
                            cookie_banner_info = banner

                            for bl in extract_banner_policy_links(soup, current_url):
                                norm_bl = self._normalize_url(bl)
                                if (norm_bl not in visited and norm_bl not in queued
                                        and self._is_same_domain(norm_bl, base_domain)):
                                    queued.add(norm_bl)
                                    to_visit.insert(0, bl)

                    # Privacy policy
                    if not privacy_policy_info.found and is_privacy_policy_page(current_url, title):
                        privacy_policy_info = self._extract_privacy_policy(
                            soup, current_url, has_footer_link,
                        )

                    pages.append(PageInfo(
                        url=current_url,
                        title=title,
                        status_code=200,  # Playwright navigated successfully
                        has_privacy_link_in_footer=has_footer_link,
                        forms_count=len(page_forms),
                        external_scripts_count=len(ext_scripts),
                    ))

                    # Discover links
                    for a in soup.find_all("a", href=True):
                        abs_url = urljoin(current_url, a["href"])
                        norm = self._normalize_url(abs_url)
                        if (norm not in visited and norm not in queued
                                and self._is_same_domain(norm, base_domain)):
                            queued.add(norm)
                            link_text = a.get_text(separator=" ", strip=True)
                            if is_privacy_policy_page(abs_url, link_text):
                                to_visit.insert(0, abs_url)
                            else:
                                to_visit.append(abs_url)

                except PlaywrightError as page_err:
                    logger.warning("Playwright page error %s: %s", current_url, page_err)
                    errors.append(f"{current_url}: {page_err}")
                finally:
                    await page.close()

            except Exception as e:
                logger.warning("Error scanning %s: %s", current_url, e)
                errors.append(f"{current_url}: {e}")

            if self.crawl_delay > 0 and to_visit:
                await asyncio.sleep(self.crawl_delay)

        # Fallback privacy policy paths
        if not privacy_policy_info.found:
            privacy_policy_info = await self._try_fallback_privacy_urls(
                context, base_domain, visited, pages,
            )

        # Last-resort: find from already-visited pages
        if not privacy_policy_info.found:
            for page in pages:
                if is_privacy_policy_page(page.url, page.title):
                    privacy_policy_info.found = True
                    privacy_policy_info.url = page.url
                    break

        # Deduplicate scripts
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

    # ── Fallback privacy paths ────────────────────────────────────

    async def _try_fallback_privacy_urls(
        self,
        context,
        base_domain: str,
        visited: set[str],
        pages: list[PageInfo],
    ) -> PrivacyPolicyInfo:
        """Try well-known privacy policy URL paths as last resort."""
        for path in _FALLBACK_PRIVACY_PATHS:
            candidate = f"https://{base_domain}{path}"
            norm = self._normalize_url(candidate)
            if norm in visited:
                continue
            try:
                page = await context.new_page()
                try:
                    resp = await page.goto(
                        candidate,
                        timeout=self.timeout * 1000,
                        wait_until="networkidle",
                    )
                    if not resp or resp.status != 200:
                        continue
                    html = await page.content()
                    soup = BeautifulSoup(html, "lxml")
                    title = soup.title.string.strip() if soup.title and soup.title.string else None
                    if is_privacy_policy_page(candidate, title):
                        has_footer_link, _ = detect_footer_privacy_link(soup)
                        pages.append(PageInfo(
                            url=candidate,
                            title=title,
                            status_code=200,
                            has_privacy_link_in_footer=has_footer_link,
                        ))
                        return self._extract_privacy_policy(soup, candidate, has_footer_link)
                finally:
                    await page.close()
            except Exception as e:
                logger.debug("Fallback privacy URL %s failed: %s", candidate, e)
        return PrivacyPolicyInfo()

    # ── Fallback to static scanner ────────────────────────────────

    async def _fallback(self, url: str, reason: str) -> ScanResult:
        """Run SiteScanner as fallback and append limitation note."""
        from src.scanner.crawler import SiteScanner
        logger.info("Falling back to SiteScanner for %s (%s)", url, reason)
        result = await SiteScanner(
            max_pages=self.max_pages,
            timeout=self.timeout,
            crawl_delay=self.crawl_delay,
        ).scan(url)
        result.scan_limitations.append(
            f"JS-рендеринг: {reason}, использован статичный режим (httpx+BS4). "
            "Формы и контент, отрисованные через JavaScript, могут быть не обнаружены."
        )
        return result

    # ── Privacy policy extraction (identical to crawler.py) ──────

    def _extract_privacy_policy(
        self, soup: BeautifulSoup, url: str, has_footer_link: bool,
    ) -> PrivacyPolicyInfo:
        """Extract and analyze privacy policy content."""
        text = soup.get_text(separator="\n", strip=True)
        text_lower = text.lower()

        return PrivacyPolicyInfo(
            found=True,
            url=url,
            text=text[:20000],
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
                r"(правов.{0,20}основан|закон.{0,20}основан|legal.?basis|на основании)",
                text_lower)),
            has_retention_periods=bool(re.search(
                r"(срок.{0,20}хранен|срок.{0,20}обработк|период.{0,20}хранен)",
                text_lower)),
            has_subject_rights=bool(re.search(
                r"(прав.{0,20}субъект|прав.{0,20}пользовател|right.{0,10}data.?subject)",
                text_lower)),
            has_rights_procedure=bool(re.search(
                r"(порядок.{0,20}реализац|порядок.{0,20}обращен|10.{0,10}рабочих|направить.{0,20}запрос)",
                text_lower)),
            has_cross_border_info=bool(re.search(
                r"(трансграничн|cross.?border|передач.{0,20}за рубеж|иностранн.{0,20}государств)",
                text_lower)),
            has_security_measures=bool(re.search(
                r"(мер.{0,20}безопасност|мер.{0,20}защит|security.?measure|шифрован|encrypt)",
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
            is_russian=bool(re.search(r"[а-яА-ЯёЁ]{20,}", text)),
            is_separate_page=True,
        )

    # ── URL helpers (identical to crawler.py) ────────────────────

    @staticmethod
    def _normalize_url(url: str) -> str:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/") or "/"
        return f"{parsed.scheme}://{parsed.netloc}{path}"

    @staticmethod
    def _is_same_domain(url: str, base_domain: str) -> bool:
        def _strip_www(domain: str) -> str:
            return domain[4:] if domain.startswith("www.") else domain
        netloc = urlparse(url).netloc.lower()
        return _strip_www(netloc) == _strip_www(base_domain)

    @staticmethod
    def _should_skip(url: str) -> bool:
        path = urlparse(url).path.lower()
        return any(path.endswith(ext) for ext in _SKIP_EXTENSIONS)
