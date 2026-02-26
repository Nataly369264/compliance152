"""Detection helpers for 152-FZ compliance scanning."""
from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

from bs4 import Tag

from src.models.scan import CookieBannerInfo, ExternalScript, FormField

logger = logging.getLogger(__name__)

# Personal-data field name/id patterns (Russian + English)
_PD_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("name", re.compile(
        r"(^name$|^fio$|имя|фамилия|отчество|firstname|lastname|"
        r"middlename|surname|full.?name)", re.IGNORECASE)),
    ("email", re.compile(r"(e[\-_.]?mail|почта|электронн)", re.IGNORECASE)),
    ("phone", re.compile(r"(phone|tel(ephone)?|mobile|телефон|моб)", re.IGNORECASE)),
    ("address", re.compile(
        r"(address|адрес|город|city|street|улица|индекс|zip|postal)", re.IGNORECASE)),
    ("passport", re.compile(r"(passport|паспорт|серия|номер.?документ)", re.IGNORECASE)),
    ("inn", re.compile(r"(^inn$|^инн$|taxpayer)", re.IGNORECASE)),
    ("snils", re.compile(r"(^snils$|^снилс$)", re.IGNORECASE)),
    ("birthday", re.compile(
        r"(birth|дата.?рожден|birthday|date.?of.?birth|дата.?р)", re.IGNORECASE)),
]

_CONSENT_TEXT_RE = re.compile(
    r"(согласи|персональн|обработк|пдн|consent|personal\s+data)", re.IGNORECASE)

_PRIVACY_LINK_TEXT_RE = re.compile(
    r"(полити|конфиденциальн|персональн|privacy|обработк.{0,20}данн|пдн)", re.IGNORECASE)

_COOKIE_BANNER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"cookie[\-_]?(banner|consent|notice|popup|bar|modal|overlay)", re.IGNORECASE),
    re.compile(r"cookiebot", re.IGNORECASE),
    re.compile(r"cc[\-_]?(banner|window|dialog)", re.IGNORECASE),
    re.compile(r"gdpr[\-_]?(banner|consent|notice)", re.IGNORECASE),
    re.compile(r"cookie[\-_]?law", re.IGNORECASE),
    re.compile(r"CybotCookiebot", re.IGNORECASE),
]


def detect_personal_data_fields(fields: list[FormField]) -> list[str]:
    """Return PD category labels for fields matching PD name patterns."""
    found: list[str] = []
    seen: set[str] = set()
    for field in fields:
        combined = " ".join(filter(None, [field.name, field.label or "", field.placeholder or ""]))
        if not combined.strip():
            continue
        for category, pattern in _PD_PATTERNS:
            if category not in seen and pattern.search(combined):
                found.append(category)
                seen.add(category)
                break
    return found


def detect_consent_checkbox(form_soup: Tag) -> tuple[bool, bool, str | None]:
    """Detect consent checkbox. Returns (has_consent, is_prechecked, consent_text)."""
    checkboxes = form_soup.find_all("input", attrs={"type": "checkbox"})
    for cb in checkboxes:
        text = _get_checkbox_context(cb, form_soup)
        if _CONSENT_TEXT_RE.search(text):
            return True, cb.has_attr("checked"), text.strip()
    return False, False, None


def detect_privacy_link(form_soup: Tag, page_soup: Tag) -> tuple[bool, str | None]:
    """Detect privacy policy link near form or on page."""
    for scope in (form_soup, page_soup):
        for a in scope.find_all("a", href=True):
            text = a.get_text(separator=" ", strip=True)
            href = a["href"]
            if _PRIVACY_LINK_TEXT_RE.search(text) or _PRIVACY_LINK_TEXT_RE.search(href):
                return True, href
    return False, None


def detect_footer_privacy_link(soup: Tag) -> tuple[bool, str | None]:
    """Detect privacy policy link in footer."""
    footer = soup.find("footer")
    if footer:
        for a in footer.find_all("a", href=True):
            text = a.get_text(separator=" ", strip=True)
            href = a["href"]
            if _PRIVACY_LINK_TEXT_RE.search(text) or _PRIVACY_LINK_TEXT_RE.search(href):
                return True, href
    # Fallback: last 25% of all links
    all_links = soup.find_all("a", href=True)
    if all_links:
        for a in all_links[len(all_links) * 3 // 4:]:
            text = a.get_text(separator=" ", strip=True)
            href = a["href"]
            if _PRIVACY_LINK_TEXT_RE.search(text) or _PRIVACY_LINK_TEXT_RE.search(href):
                return True, href
    return False, None


def detect_cookie_banner(soup: Tag) -> CookieBannerInfo:
    """Detect cookie consent banner on the page."""
    for pattern in _COOKIE_BANNER_PATTERNS:
        for el in soup.find_all(id=pattern):
            return _parse_banner(el)
        for el in soup.find_all(class_=pattern):
            return _parse_banner(el)

    # Check for cookie-consent JS libraries
    cookie_script_re = re.compile(
        r"(cookiebot|cookie[\-_]?consent|onetrust|trustarc|complianz)", re.IGNORECASE)
    for script in soup.find_all("script", src=True):
        if cookie_script_re.search(script.get("src", "")):
            return CookieBannerInfo(found=True)

    return CookieBannerInfo(found=False)


def detect_external_scripts(soup: Tag, page_url: str) -> list[ExternalScript]:
    """Find all external scripts/stylesheets from other domains."""
    page_domain = urlparse(page_url).netloc.lower()
    results: list[ExternalScript] = []
    seen: set[str] = set()

    # Scripts
    for tag in soup.find_all("script", src=True):
        abs_url = urljoin(page_url, tag["src"])
        domain = urlparse(abs_url).netloc.lower()
        if domain and domain != page_domain and abs_url not in seen:
            seen.add(abs_url)
            results.append(ExternalScript(
                url=abs_url, page_url=page_url, script_type="js", domain=domain))

    # Stylesheets
    for tag in soup.find_all("link", href=True):
        rel = " ".join(tag.get("rel", []))
        if "stylesheet" not in rel.lower():
            continue
        abs_url = urljoin(page_url, tag["href"])
        domain = urlparse(abs_url).netloc.lower()
        if domain and domain != page_domain and abs_url not in seen:
            seen.add(abs_url)
            results.append(ExternalScript(
                url=abs_url, page_url=page_url, script_type="css", domain=domain))

    # Fonts (preconnect/preload to external domains)
    for tag in soup.find_all("link", href=True):
        rel = " ".join(tag.get("rel", []))
        if "font" in rel.lower() or "preconnect" in rel.lower():
            abs_url = urljoin(page_url, tag["href"])
            domain = urlparse(abs_url).netloc.lower()
            if domain and domain != page_domain and abs_url not in seen:
                seen.add(abs_url)
                results.append(ExternalScript(
                    url=abs_url, page_url=page_url, script_type="font", domain=domain))

    # Iframes
    for tag in soup.find_all("iframe", src=True):
        abs_url = urljoin(page_url, tag["src"])
        domain = urlparse(abs_url).netloc.lower()
        if domain and domain != page_domain and abs_url not in seen:
            seen.add(abs_url)
            results.append(ExternalScript(
                url=abs_url, page_url=page_url, script_type="iframe", domain=domain))

    # Tracking pixels (1x1 images)
    for tag in soup.find_all("img", src=True):
        abs_url = urljoin(page_url, tag["src"])
        domain = urlparse(abs_url).netloc.lower()
        if domain and domain != page_domain and abs_url not in seen:
            w = tag.get("width", "")
            h = tag.get("height", "")
            if _is_tracking_pixel(w, h):
                seen.add(abs_url)
                results.append(ExternalScript(
                    url=abs_url, page_url=page_url, script_type="pixel", domain=domain))

    return results


def is_privacy_policy_page(url: str, title: str | None = None) -> bool:
    """Heuristic: is this URL/title a privacy policy page?"""
    pp_re = re.compile(
        r"(privacy|policy|politika|конфиденциальн|персональн|"
        r"personal[\-_]?data|soglashenie|pdn|обработк.{0,10}данн)", re.IGNORECASE)
    if pp_re.search(url):
        return True
    if title and pp_re.search(title):
        return True
    return False


# ── Internal helpers ─────────────────────────────────────────────

def _get_checkbox_context(cb: Tag, form_soup: Tag) -> str:
    parts: list[str] = []
    parent = cb.parent
    if parent and parent.name in ("label", "div", "span", "p", "li"):
        parts.append(parent.get_text(separator=" ", strip=True))
    cb_id = cb.get("id")
    if cb_id:
        label = form_soup.find("label", attrs={"for": cb_id})
        if label:
            parts.append(label.get_text(separator=" ", strip=True))
    sibling = cb.next_sibling
    if sibling:
        if isinstance(sibling, str):
            parts.append(sibling.strip())
        elif hasattr(sibling, "get_text"):
            parts.append(sibling.get_text(separator=" ", strip=True))
    return " ".join(parts)


def _parse_banner(el: Tag) -> CookieBannerInfo:
    accept_re = re.compile(r"(accept|принять|принимаю|agree|согласен|ок|ok|понятно|allow)", re.IGNORECASE)
    reject_re = re.compile(r"(reject|отклонить|отказа|decline|deny|запретить|refuse)", re.IGNORECASE)
    settings_re = re.compile(r"(settings|настройк|manage|управлени|preferences|подробнее|выбрать)", re.IGNORECASE)

    has_accept = has_reject = has_settings = False
    for btn in el.find_all(["button", "a", "input"]):
        text = btn.get_text(separator=" ", strip=True) + " " + btn.get("value", "")
        if accept_re.search(text):
            has_accept = True
        if reject_re.search(text):
            has_reject = True
        if settings_re.search(text):
            has_settings = True

    html = str(el)[:2000]
    return CookieBannerInfo(
        found=True,
        has_accept_button=has_accept,
        has_decline_button=has_reject,
        has_category_choice=has_settings,
        has_cookie_policy_link=bool(el.find("a", href=True)),
        banner_html=html,
    )


def _is_tracking_pixel(width: str, height: str) -> bool:
    try:
        w = int(re.sub(r"\D", "", width)) if width else -1
        h = int(re.sub(r"\D", "", height)) if height else -1
    except ValueError:
        return False
    return (0 <= w <= 2) and (0 <= h <= 2)
