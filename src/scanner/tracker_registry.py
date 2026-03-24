"""Registry of known trackers for 152-FZ compliance checks.

Each entry describes a third-party service that may process personal data.
Used by ComplianceAnalyzer for TRACKER_001 and TRACKER_002 checks.

Fields:
    name        — human-readable service name
    domains     — list of base domains (without leading dot)
    keywords    — lowercase keywords to search in privacy policy text
    is_foreign  — True if the service stores/processes data outside Russia
"""

from __future__ import annotations

TRACKER_REGISTRY: list[dict] = [
    # ── High risk: foreign services ───────────────────────────────
    {
        "name": "Google Analytics",
        "domains": ["google-analytics.com", "analytics.google.com"],
        "keywords": ["google analytics", "гугл аналитика", "google anal"],
        "is_foreign": True,
    },
    {
        "name": "Google Tag Manager",
        "domains": ["googletagmanager.com"],
        "keywords": ["google tag manager", "gtm", "googletagmanager"],
        "is_foreign": True,
    },
    {
        "name": "Meta Pixel",
        "domains": ["connect.facebook.net", "facebook.com", "fbcdn.net"],
        "keywords": ["facebook", "meta pixel", "fb pixel", "фейсбук", "мета пиксель"],
        "is_foreign": True,
    },
    {
        "name": "TikTok Pixel",
        "domains": ["analytics.tiktok.com", "tiktok.com"],
        "keywords": ["tiktok", "тикток"],
        "is_foreign": True,
    },
    {
        "name": "Tawk.to",
        "domains": ["tawk.to", "embed.tawk.to"],
        "keywords": ["tawk", "tawk.to"],
        "is_foreign": True,
    },
    {
        "name": "Zendesk",
        "domains": ["zendesk.com", "zdassets.com", "zopim.com"],
        "keywords": ["zendesk", "зендеск"],
        "is_foreign": True,
    },
    {
        "name": "LiveChat",
        "domains": ["livechatinc.com", "cdn.livechatinc.com"],
        "keywords": ["livechat", "лайвчат", "livechatinc"],
        "is_foreign": True,
    },
    {
        "name": "WhatsApp Business",
        "domains": ["whatsapp.com", "whatsapp.net"],
        "keywords": ["whatsapp", "вотсап", "ватсап"],
        "is_foreign": True,
    },
    {
        "name": "Typeform",
        "domains": ["typeform.com", "embed.typeform.com"],
        "keywords": ["typeform", "тайпформ"],
        "is_foreign": True,
    },
    {
        "name": "Jotform",
        "domains": ["jotform.com"],
        "keywords": ["jotform", "джотформ"],
        "is_foreign": True,
    },
    # ── Medium risk: Russian services (must be disclosed, not foreign) ──
    {
        "name": "Яндекс.Метрика",
        "domains": ["mc.yandex.ru", "mc.yandex.com", "metrika.yandex.ru"],
        "keywords": ["яндекс.метрика", "яндекс метрика", "yandex metrika", "yandex.metrika"],
        "is_foreign": False,
    },
    {
        "name": "VK Pixel",
        "domains": ["vk.com", "vkontakte.ru"],
        "keywords": ["вконтакте", "vk pixel", "вк пиксель", "myTarget", "mytarget"],
        "is_foreign": False,
    },
    {
        "name": "JivoChat",
        "domains": ["jivosite.com", "jivo.ru"],
        "keywords": ["jivosite", "jivochat", "jivosite", "живосайт", "jivo"],
        "is_foreign": False,
    },
]


def _domain_matches(script_domain: str, registry_domains: list[str]) -> bool:
    """Return True if script_domain matches any registry domain.

    Handles subdomains: 'mc.yandex.ru' matches 'yandex.ru'.
    Avoids false positives: 'not-google-analytics.com' does NOT match 'google-analytics.com'.
    """
    script_domain = script_domain.lower()
    for d in registry_domains:
        d = d.lower()
        if script_domain == d or script_domain.endswith("." + d):
            return True
    return False


def find_trackers_in_scripts(external_scripts_domains: list[str]) -> list[dict]:
    """Return list of registry entries whose domains appear in external_scripts_domains."""
    found: list[dict] = []
    for tracker in TRACKER_REGISTRY:
        if any(_domain_matches(sd, tracker["domains"]) for sd in external_scripts_domains):
            found.append(tracker)
    return found
