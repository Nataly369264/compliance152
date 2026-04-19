from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path

from src.models.legal_update import LegalUpdate

KB_DIR = Path(__file__).resolve().parent.parent.parent / "knowledge_base"


@lru_cache(maxsize=1)
def load_website_checklist() -> list[dict]:
    path = KB_DIR / "checklists" / "website_checklist.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_prohibited_services() -> list[dict]:
    path = KB_DIR / "checklists" / "prohibited_services.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_fine_schedule() -> list[dict]:
    path = KB_DIR / "checklists" / "fine_schedule.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _checklist_by_id() -> dict[str, dict]:
    return {entry["id"]: entry for entry in load_website_checklist()}


def get_check_by_id(check_id: str) -> dict | None:
    return _checklist_by_id().get(check_id)


@lru_cache(maxsize=1)
def _fine_schedule_by_id() -> dict[str, dict]:
    return {entry["id"]: entry for entry in load_fine_schedule()}


def get_fine_by_id(fine_id: str) -> dict | None:
    return _fine_schedule_by_id().get(fine_id)


def get_checks_by_category(category: str) -> list[dict]:
    return [c for c in load_website_checklist() if c.get("category") == category]


def get_prohibited_domains() -> set[str]:
    """Return a flat set of all prohibited domains for quick lookup."""
    domains = set()
    for service in load_prohibited_services():
        for domain in service.get("domains", []):
            domains.add(domain.lower())
    return domains


def get_prohibited_service_by_domain(domain: str) -> dict | None:
    """Find a prohibited service entry by domain match."""
    domain = domain.lower()
    for service in load_prohibited_services():
        for d in service.get("domains", []):
            if d.lower() in domain or domain.endswith("." + d.lower()):
                return service
    return None


def estimate_fines(violation_ids: list[str]) -> dict:
    """Estimate total fines based on violation IDs using fine_reference from checklist.

    Returns dict with min_total, max_total, and breakdown list.
    One fine_id is counted only once even if multiple violations reference it (КоАП logic).
    """
    seen_fine_ids: set[str] = set()
    min_total = 0
    max_total = 0
    breakdown = []

    for vid in violation_ids:
        check = get_check_by_id(vid)
        if not check:
            continue
        fine_ref = check.get("fine_reference")
        if not fine_ref or fine_ref in seen_fine_ids:
            continue  # штраф уже учтён (один состав = один штраф)
        seen_fine_ids.add(fine_ref)

        fine = get_fine_by_id(fine_ref)
        if not fine:
            continue

        min_total += fine["first_offense_min"]
        max_total += fine["first_offense_max"]
        breakdown.append({
            "violation": fine["violation"],
            "fine_id": fine["id"],
            "min_fine": fine["first_offense_min"],
            "max_fine": fine["first_offense_max"],
            "law_reference": fine["law_reference"],
            "repeat_offense_max": fine.get("repeat_offense_max"),
        })

    return {
        "min_total": min_total,
        "max_total": max_total,
        "breakdown": breakdown,
    }


# ── Legal updates ─────────────────────────────────────────────────


def load_legal_updates() -> list[LegalUpdate]:
    """Load all legal updates from JSON file."""
    path = KB_DIR / "legal_updates" / "updates.json"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [LegalUpdate(**item) for item in raw]


def get_updates_for_document(doc_type: str) -> list[LegalUpdate]:
    """Return legal updates relevant to a specific document type."""
    return [
        u for u in load_legal_updates()
        if doc_type in u.affected_documents
    ]


def get_active_updates(as_of: date | None = None) -> list[LegalUpdate]:
    """Return updates whose effective_date <= as_of (default: today)."""
    if as_of is None:
        as_of = date.today()
    return [
        u for u in load_legal_updates()
        if date.fromisoformat(u.effective_date) <= as_of
    ]


def get_updates_for_document_active(
    doc_type: str, as_of: date | None = None,
) -> list[LegalUpdate]:
    """Return active legal updates relevant to a specific document type."""
    if as_of is None:
        as_of = date.today()
    return [
        u for u in load_legal_updates()
        if doc_type in u.affected_documents
        and date.fromisoformat(u.effective_date) <= as_of
    ]


def format_legal_context(updates: list[LegalUpdate]) -> str:
    """Format legal updates into a text block for injection into LLM prompt."""
    if not updates:
        return ""

    lines = ["АКТУАЛЬНЫЕ ИЗМЕНЕНИЯ ЗАКОНОДАТЕЛЬСТВА (обязательно учесть при генерации):", ""]
    for u in sorted(updates, key=lambda x: x.effective_date, reverse=True):
        lines.append(f"### {u.title}")
        lines.append(f"Вступило в силу: {u.effective_date}")
        lines.append(f"Источник: {u.source}")
        lines.append(f"Статьи: {', '.join(u.articles)}")
        lines.append(f"Суть: {u.summary}")
        if u.requirements:
            lines.append("Требования:")
            for req in u.requirements:
                lines.append(f"  - {req}")
        lines.append("")

    return "\n".join(lines)
