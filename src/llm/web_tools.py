"""Web search and page fetching tools for legal context gathering.

Supports multiple search backends:
- Tavily (primary, optimized for LLM)
- Yandex XML (best for Russian legal content)
- DuckDuckGo HTML (free fallback, no API key needed)

Pages are fetched and cleaned to extract text content for LLM consumption.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import quote_plus, urlparse

import httpx
from bs4 import BeautifulSoup

from src.config import SEARCH_BACKEND, TAVILY_API_KEY, YANDEX_XML_USER, YANDEX_XML_KEY

logger = logging.getLogger(__name__)

# Domains trusted for legal content
LEGAL_DOMAINS = [
    "consultant.ru",
    "garant.ru",
    "rkn.gov.ru",
    "pd.rkn.gov.ru",
    "pravo.gov.ru",
    "publication.pravo.gov.ru",
    "sozd.duma.gov.ru",
    "base.garant.ru",
    "www.consultant.ru",
    "www.garant.ru",
]

# Domains allowed for page fetching (broader set)
ALLOWED_FETCH_DOMAINS = LEGAL_DOMAINS + [
    "habr.com",
    "vc.ru",
    "tadviser.ru",
    "zakon.ru",
    "klerk.ru",
    "b152.ru",
    "kdelo.ru",
    "cntd.ru",
    "legalacts.ru",
]

MAX_PAGE_TEXT = 12000  # Max characters to return from a fetched page
SEARCH_TIMEOUT = 15
FETCH_TIMEOUT = 20


# ── Search backends ──────────────────────────────────────────────


async def search_tavily(query: str, max_results: int = 5) -> list[dict]:
    """Search using Tavily API (optimized for LLM consumption).

    Returns list of dicts with keys: url, title, content.
    """
    if not TAVILY_API_KEY:
        logger.warning("Tavily API key not set, skipping Tavily search")
        return []

    try:
        async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "search_depth": "advanced",
                    "include_domains": LEGAL_DOMAINS,
                    "max_results": max_results,
                },
            )
            response.raise_for_status()
            data = response.json()

            results = []
            for r in data.get("results", []):
                results.append({
                    "url": r.get("url", ""),
                    "title": r.get("title", ""),
                    "content": r.get("content", "")[:3000],
                })
            return results

    except Exception as e:
        logger.error("Tavily search failed: %s", e)
        return []


async def search_yandex_xml(query: str, max_results: int = 5) -> list[dict]:
    """Search using Yandex XML API (best for Russian content).

    Returns list of dicts with keys: url, title, content.
    """
    if not YANDEX_XML_USER or not YANDEX_XML_KEY:
        logger.warning("Yandex XML credentials not set, skipping Yandex search")
        return []

    try:
        encoded_query = quote_plus(query)
        url = (
            f"https://yandex.com/search/xml"
            f"?user={YANDEX_XML_USER}"
            f"&key={YANDEX_XML_KEY}"
            f"&query={encoded_query}"
            f"&l10n=ru"
            f"&sortby=rlv"
            f"&filter=none"
            f"&maxpassages=3"
            f"&groupby=attr%3D%22%22.mode%3Dflat.groups-on-page%3D{max_results}"
        )

        async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT) as client:
            response = await client.get(url)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "lxml-xml")
            results = []

            for group in soup.find_all("group"):
                doc = group.find("doc")
                if not doc:
                    continue

                doc_url = doc.find("url")
                doc_title = doc.find("title")
                passages = doc.find_all("passage")

                content_parts = []
                for p in passages:
                    text = p.get_text(strip=True)
                    if text:
                        content_parts.append(text)

                results.append({
                    "url": doc_url.get_text(strip=True) if doc_url else "",
                    "title": doc_title.get_text(strip=True) if doc_title else "",
                    "content": " ".join(content_parts)[:3000],
                })

            return results

    except Exception as e:
        logger.error("Yandex XML search failed: %s", e)
        return []


async def search_duckduckgo(query: str, max_results: int = 5) -> list[dict]:
    """Search using DuckDuckGo HTML scraping (free fallback, no API key).

    Returns list of dicts with keys: url, title, content.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; Compliance152Bot/1.0)",
        }
        encoded_query = quote_plus(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded_query}"

        async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "lxml")
            results = []

            for result_div in soup.select(".result")[:max_results]:
                link = result_div.select_one(".result__a")
                snippet = result_div.select_one(".result__snippet")

                if not link:
                    continue

                href = link.get("href", "")
                # DuckDuckGo wraps URLs in redirects
                if "uddg=" in href:
                    from urllib.parse import parse_qs, urlparse as _urlparse
                    parsed = _urlparse(href)
                    qs = parse_qs(parsed.query)
                    href = qs.get("uddg", [href])[0]

                results.append({
                    "url": href,
                    "title": link.get_text(strip=True),
                    "content": snippet.get_text(strip=True) if snippet else "",
                })

            return results

    except Exception as e:
        logger.error("DuckDuckGo search failed: %s", e)
        return []


# ── Unified search interface ─────────────────────────────────────


async def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Perform a web search using the configured backend with fallback chain.

    Config SEARCH_BACKEND controls primary backend: "tavily", "yandex", "duckduckgo".
    Falls back through the chain if primary fails.

    Returns list of dicts with keys: url, title, content.
    """
    backends = {
        "tavily": search_tavily,
        "yandex": search_yandex_xml,
        "duckduckgo": search_duckduckgo,
    }

    # Build ordered chain: configured backend first, then others
    primary = SEARCH_BACKEND.lower()
    chain = [primary]
    for name in backends:
        if name not in chain:
            chain.append(name)

    for backend_name in chain:
        search_fn = backends.get(backend_name)
        if not search_fn:
            continue

        logger.info("Trying search backend: %s for query: '%s'", backend_name, query[:80])
        results = await search_fn(query, max_results)

        if results:
            logger.info("Search backend %s returned %d results", backend_name, len(results))
            return results

        logger.warning("Search backend %s returned no results, trying next", backend_name)

    logger.error("All search backends failed for query: '%s'", query[:80])
    return []


# ── Page fetching ────────────────────────────────────────────────


def _is_allowed_domain(url: str) -> bool:
    """Check if a URL's domain is in the allowed list."""
    try:
        domain = urlparse(url).netloc.lower()
        return any(d in domain for d in ALLOWED_FETCH_DOMAINS)
    except Exception:
        return False


def _clean_html_text(html: str) -> str:
    """Extract clean text from HTML, removing scripts/styles/navigation."""
    soup = BeautifulSoup(html, "lxml")

    # Remove non-content elements
    for tag in soup(["script", "style", "nav", "header", "footer", "aside",
                     "iframe", "noscript", "svg", "form"]):
        tag.decompose()

    # Try to find main content area
    main = (
        soup.find("article")
        or soup.find("main")
        or soup.find(class_=re.compile(r"content|article|post|entry", re.I))
        or soup.find(id=re.compile(r"content|article|main", re.I))
        or soup.body
        or soup
    )

    text = main.get_text(separator="\n", strip=True)

    # Clean up excessive whitespace
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    text = "\n".join(lines)

    return text[:MAX_PAGE_TEXT]


async def fetch_page(url: str, allow_any_domain: bool = False) -> str:
    """Fetch a web page and extract clean text content.

    By default only allows fetching from ALLOWED_FETCH_DOMAINS.
    Set allow_any_domain=True to bypass (use with caution).

    Returns extracted text or error message.
    """
    if not allow_any_domain and not _is_allowed_domain(url):
        domain = urlparse(url).netloc
        return f"[Домен {domain} не в списке разрешённых для загрузки]"

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; Compliance152Bot/1.0)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
        }

        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                return f"[Страница не является HTML: {content_type}]"

            text = _clean_html_text(response.text)
            if not text or len(text) < 50:
                return "[Не удалось извлечь текстовое содержимое со страницы]"

            return text

    except httpx.TimeoutException:
        logger.warning("Timeout fetching %s", url)
        return f"[Таймаут при загрузке {url}]"
    except httpx.HTTPStatusError as e:
        logger.warning("HTTP error fetching %s: %s", url, e.response.status_code)
        return f"[Ошибка HTTP {e.response.status_code} при загрузке {url}]"
    except Exception as e:
        logger.error("Failed to fetch %s: %s", url, e)
        return f"[Ошибка при загрузке {url}: {e}]"


# ── Format search results for LLM ───────────────────────────────


def format_search_results(results: list[dict]) -> str:
    """Format search results into a text block for LLM prompt injection."""
    if not results:
        return "[Результаты поиска не найдены]"

    parts = []
    for i, r in enumerate(results, 1):
        parts.append(
            f"--- Результат {i} ---\n"
            f"Источник: {r.get('url', 'N/A')}\n"
            f"Заголовок: {r.get('title', 'N/A')}\n"
            f"Содержание: {r.get('content', 'N/A')}\n"
        )

    return "\n".join(parts)
