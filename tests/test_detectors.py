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
