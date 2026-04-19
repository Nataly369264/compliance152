"""Tests for scanner detectors."""
from bs4 import BeautifulSoup

from src.models.scan import FormField
from src.scanner.detectors import (
    detect_consent_checkbox,
    detect_cookie_banner,
    detect_external_scripts,
    detect_footer_privacy_link,
    detect_personal_data_fields,
    detect_privacy_link,
    extract_forms,
    is_privacy_policy_page,
)


def test_detect_personal_data_fields():
    fields = [
        FormField(name="firstname", field_type="text"),
        FormField(name="email", field_type="email"),
        FormField(name="phone", field_type="tel"),
        FormField(name="message", field_type="textarea"),
    ]
    result = detect_personal_data_fields(fields)
    assert "name" in result
    assert "email" in result
    assert "phone" in result
    assert len(result) == 3


def test_detect_personal_data_fields_russian():
    fields = [
        FormField(name="имя", field_type="text"),
        FormField(name="телефон", field_type="tel"),
        FormField(name="адрес", field_type="text"),
    ]
    result = detect_personal_data_fields(fields)
    assert "name" in result
    assert "phone" in result
    assert "address" in result


def test_detect_consent_checkbox_found():
    html = """
    <form>
        <input type="text" name="email">
        <label>
            <input type="checkbox" name="consent">
            Даю согласие на обработку персональных данных
        </label>
        <button type="submit">Отправить</button>
    </form>
    """
    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form")
    has_consent, prechecked, text = detect_consent_checkbox(form)
    assert has_consent is True
    assert prechecked is False
    assert "согласие" in text.lower()


def test_detect_consent_checkbox_prechecked():
    html = """
    <form>
        <label>
            <input type="checkbox" name="consent" checked>
            Согласие на обработку ПДн
        </label>
    </form>
    """
    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form")
    has_consent, prechecked, _ = detect_consent_checkbox(form)
    assert has_consent is True
    assert prechecked is True


def test_detect_consent_checkbox_not_found():
    html = """
    <form>
        <input type="text" name="email">
        <button type="submit">Send</button>
    </form>
    """
    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form")
    has_consent, _, _ = detect_consent_checkbox(form)
    assert has_consent is False


def test_detect_privacy_link():
    html = """
    <form>
        <input type="text" name="email">
        <a href="/privacy-policy">Политика конфиденциальности</a>
    </form>
    """
    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form")
    has_link, url = detect_privacy_link(form, soup)
    assert has_link is True
    assert url == "/privacy-policy"


def test_detect_footer_privacy_link():
    html = """
    <html><body>
        <main>Content</main>
        <footer>
            <a href="/privacy">Политика обработки ПДн</a>
        </footer>
    </body></html>
    """
    soup = BeautifulSoup(html, "lxml")
    has_link, url = detect_footer_privacy_link(soup)
    assert has_link is True
    assert url == "/privacy"


def test_detect_cookie_banner():
    html = """
    <div id="cookie-banner">
        <p>Мы используем cookie</p>
        <button>Принять</button>
        <button>Отклонить</button>
    </div>
    """
    soup = BeautifulSoup(html, "lxml")
    banner = detect_cookie_banner(soup)
    assert banner.found is True
    assert banner.has_accept_button is True
    assert banner.has_decline_button is True


def test_detect_external_scripts():
    html = """
    <html><head>
        <script src="https://www.googletagmanager.com/gtag.js"></script>
        <link rel="stylesheet" href="https://fonts.googleapis.com/css?family=Roboto">
        <script src="/local/script.js"></script>
    </head><body></body></html>
    """
    soup = BeautifulSoup(html, "lxml")
    scripts = detect_external_scripts(soup, "https://example.com/page")
    assert len(scripts) == 2
    domains = [s.domain for s in scripts]
    assert "www.googletagmanager.com" in domains
    assert "fonts.googleapis.com" in domains


def test_is_privacy_policy_page():
    assert is_privacy_policy_page("https://example.com/privacy-policy") is True
    assert is_privacy_policy_page("https://example.com/politika-konfidencialnosti") is True
    assert is_privacy_policy_page("https://example.com/about") is False
    assert is_privacy_policy_page("https://example.com/page", "Политика обработки персональных данных") is True


# ── JS-платформы: баннеры (6.4) ──────────────────────────────────

def test_detect_cookie_banner_onetrust():
    """OneTrust монтирует баннер с id="onetrust-banner-sdk"."""
    html = """
    <div id="onetrust-banner-sdk">
        <p>Мы используем файлы cookie</p>
        <button id="onetrust-accept-btn-handler">Принять все</button>
    </div>
    """
    soup = BeautifulSoup(html, "lxml")
    banner = detect_cookie_banner(soup)
    assert banner.found is True
    assert banner.has_accept_button is True


def test_detect_cookie_banner_cookiebot():
    """Cookiebot монтирует баннер с id="CybotCookiebotDialog"."""
    html = """
    <div id="CybotCookiebotDialog">
        <p>This website uses cookies</p>
        <button id="CybotCookiebotDialogBodyButtonAccept">Allow all</button>
        <button id="CybotCookiebotDialogBodyButtonDecline">Decline</button>
    </div>
    """
    soup = BeautifulSoup(html, "lxml")
    banner = detect_cookie_banner(soup)
    assert banner.found is True
    assert banner.has_accept_button is True
    assert banner.has_decline_button is True


def test_detect_cookie_banner_cookieyes():
    """CookieYes монтирует баннер с class="cky-consent-container"."""
    html = """
    <div class="cky-consent-container">
        <p>We use cookies to enhance your experience</p>
        <button class="cky-btn-accept">Accept All</button>
        <button class="cky-btn-reject">Reject All</button>
    </div>
    """
    soup = BeautifulSoup(html, "lxml")
    banner = detect_cookie_banner(soup)
    assert banner.found is True
    assert banner.has_accept_button is True
    assert banner.has_decline_button is True


# ── JS-платформы: формы (6.4) ─────────────────────────────────────

def test_extract_forms_tilda():
    """Tilda монтирует форму внутри <div class="t-form"> — find_all("form") находит её."""
    html = """
    <div class="t-form">
        <form action="/submit" method="post">
            <input type="text" name="name" placeholder="Ваше имя">
            <input type="email" name="email" placeholder="Email">
            <button type="submit">Отправить</button>
        </form>
    </div>
    """
    soup = BeautifulSoup(html, "lxml")
    forms = extract_forms(soup, "https://example.tilda.ws/")
    assert len(forms) == 1
    field_names = [f.name for f in forms[0].fields]
    assert "name" in field_names
    assert "email" in field_names


def test_extract_forms_bitrix():
    """Bitrix монтирует форму внутри <div class="b24-form"> — find_all("form") находит её."""
    html = """
    <div class="b24-form">
        <form action="/bitrix/tools/crm_form.php" method="post">
            <input type="text" name="phone" placeholder="Телефон">
            <input type="email" name="email" placeholder="Email">
            <button type="submit">Отправить</button>
        </form>
    </div>
    """
    soup = BeautifulSoup(html, "lxml")
    forms = extract_forms(soup, "https://example.ru/")
    assert len(forms) == 1
    field_names = [f.name for f in forms[0].fields]
    assert "phone" in field_names
    assert "email" in field_names


def test_extract_forms_static_regression():
    """Регрессия: старые статические формы по-прежнему находятся корректно."""
    html = """
    <form action="/contact" method="post">
        <input type="text" name="name" placeholder="Имя">
        <input type="email" name="email">
        <label for="cb">Согласен на обработку персональных данных</label>
        <input type="checkbox" id="cb" name="consent">
        <button type="submit">Отправить</button>
    </form>
    """
    soup = BeautifulSoup(html, "lxml")
    forms = extract_forms(soup, "https://example.com/contact")
    assert len(forms) == 1
    assert forms[0].collects_personal_data is True
    assert forms[0].has_consent_checkbox is True
