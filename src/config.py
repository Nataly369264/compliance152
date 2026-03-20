from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default)


# ── LLM Provider ────────────────────────────────────────────
# "openrouter" | "anthropic"
LLM_PROVIDER = _get("LLM_PROVIDER", "openrouter")

# OpenRouter (основной и резервный ключи)
OPENROUTER_API_KEY = _get("OPENROUTER_API_KEY", "")
OPENROUTER_BACKUP_KEY = _get("OPENROUTER_BACKUP_KEY", "")
OPENROUTER_BASE_URL = _get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL = _get("OPENROUTER_MODEL", "google/gemini-2.5-pro")

# Anthropic (напрямую)
ANTHROPIC_API_KEY = _get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = _get("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")

# Обратная совместимость: CLAUDE_MODEL
CLAUDE_MODEL = _get("CLAUDE_MODEL", "")

# API Server
API_HOST = _get("API_HOST", "0.0.0.0")
API_PORT = int(_get("API_PORT", "8000"))

# Database
DB_PATH = _get("DB_PATH", "data/compliance.db")

# Scanner
MAX_PAGES = int(_get("MAX_PAGES", "100"))
SCAN_TIMEOUT = int(_get("SCAN_TIMEOUT", "30"))
CRAWL_DELAY = float(_get("CRAWL_DELAY", "1.0"))
USE_PLAYWRIGHT = _get("USE_PLAYWRIGHT", "false").lower() == "true"

# Web Search
SEARCH_BACKEND = _get("SEARCH_BACKEND", "duckduckgo")  # tavily | yandex | duckduckgo
TAVILY_API_KEY = _get("TAVILY_API_KEY", "")
YANDEX_XML_USER = _get("YANDEX_XML_USER", "")
YANDEX_XML_KEY = _get("YANDEX_XML_KEY", "")
WEB_CONTEXT_CACHE_TTL_HOURS = int(_get("WEB_CONTEXT_CACHE_TTL_HOURS", "24"))

# Logging
LOG_LEVEL = _get("LOG_LEVEL", "INFO")

# ── Competitor Intelligence Monitor ─────────────────────────────
COMPETITOR_MIN_DELAY = float(_get("COMPETITOR_MIN_DELAY", "3.0"))
COMPETITOR_MAX_DELAY = float(_get("COMPETITOR_MAX_DELAY", "8.0"))
COMPETITOR_FETCH_TIMEOUT = int(_get("COMPETITOR_FETCH_TIMEOUT", "25"))
TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = _get("TELEGRAM_CHAT_ID", "")
