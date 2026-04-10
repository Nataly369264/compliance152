from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class FormField(BaseModel):
    name: str
    field_type: str  # text, email, tel, hidden, etc.
    label: str | None = None
    required: bool = False
    placeholder: str | None = None


class FormInfo(BaseModel):
    page_url: str
    action: str | None = None
    method: str = "GET"
    fields: list[FormField] = []
    has_consent_checkbox: bool = False
    consent_checkbox_prechecked: bool = False
    consent_text: str | None = None
    has_privacy_link: bool = False
    privacy_link_url: str | None = None
    has_marketing_checkbox: bool = False
    collects_personal_data: bool = False
    personal_data_fields: list[str] = []


class CookieInfo(BaseModel):
    name: str
    domain: str
    path: str = "/"
    secure: bool = False
    http_only: bool = False
    same_site: str | None = None
    expires: str | None = None
    category: str = "unknown"  # necessary, analytical, marketing, unknown


class CookieBannerInfo(BaseModel):
    found: bool = False
    has_accept_button: bool = False
    has_decline_button: bool = False
    has_category_choice: bool = False
    has_cookie_policy_link: bool = False
    analytics_before_consent: bool = False
    banner_html: str | None = None


class ExternalScript(BaseModel):
    url: str
    page_url: str
    script_type: str = "js"  # js, css, font, pixel, iframe
    domain: str = ""
    is_prohibited: bool = False
    service_name: str | None = None  # "Google Analytics", "Google Fonts", etc.


class PrivacyPolicyInfo(BaseModel):
    found: bool = False
    url: str | None = None
    text: str | None = None
    in_footer: bool = False
    accessible_without_auth: bool = True
    has_operator_name: bool = False
    has_inn_ogrn: bool = False
    has_responsible_person: bool = False
    has_data_categories: bool = False
    has_purposes: bool = False
    has_legal_basis: bool = False
    has_retention_periods: bool = False
    has_subject_rights: bool = False
    has_rights_procedure: bool = False
    has_cross_border_info: bool = False
    has_security_measures: bool = False
    has_cookie_info: bool = False
    has_localization_statement: bool = False
    has_date: bool = False
    is_russian: bool = True
    is_separate_page: bool = False
    text_hash: str | None = None
    fetched_at: datetime | None = None
    content_length: int | None = None
    extraction_method: str = "pdfplumber"


class SSLInfo(BaseModel):
    has_ssl: bool = False
    certificate_valid: bool = False
    issuer: str | None = None
    expires: str | None = None


class PageInfo(BaseModel):
    url: str
    title: str | None = None
    status_code: int = 200
    has_privacy_link_in_footer: bool = False
    forms_count: int = 0
    external_scripts_count: int = 0


class ScanResult(BaseModel):
    url: str
    pages: list[PageInfo] = []
    forms: list[FormInfo] = []
    cookies: list[CookieInfo] = []
    external_scripts: list[ExternalScript] = []
    privacy_policy: PrivacyPolicyInfo = PrivacyPolicyInfo()
    ssl_info: SSLInfo = SSLInfo()
    cookie_banner: CookieBannerInfo = CookieBannerInfo()
    pages_scanned: int = 0
    errors: list[str] = []
    scan_limitations: list[str] = []  # crawler-level notes (e.g. Playwright fallback)
