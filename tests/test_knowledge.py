"""Tests for knowledge base loading."""

from src.knowledge.loader import (
    load_fine_schedule,
    load_prohibited_services,
    load_website_checklist,
    get_prohibited_domains,
    get_prohibited_service_by_domain,
    estimate_fines,
)


def test_load_website_checklist():
    items = load_website_checklist()
    assert len(items) == 40
    ids = [item["id"] for item in items]
    assert "FORM_001" in ids
    assert "COOKIE_001" in ids
    assert "POLICY_001" in ids
    assert "TECH_001" in ids
    assert "REG_001" in ids


def test_load_prohibited_services():
    services = load_prohibited_services()
    assert len(services) >= 10
    names = [s["name"] for s in services]
    assert "Google Analytics" in names
    assert "Google Fonts" in names
    assert "Facebook Pixel" in names


def test_load_fine_schedule():
    fines = load_fine_schedule()
    assert len(fines) == 9
    for fine in fines:
        assert fine["first_offense_min"] > 0
        assert fine["first_offense_max"] >= fine["first_offense_min"]


def test_get_prohibited_domains():
    domains = get_prohibited_domains()
    assert "google-analytics.com" in domains
    assert "fonts.googleapis.com" in domains
    assert "connect.facebook.net" in domains


def test_get_prohibited_service_by_domain():
    svc = get_prohibited_service_by_domain("google-analytics.com")
    assert svc is not None
    assert svc["name"] == "Google Analytics"

    svc2 = get_prohibited_service_by_domain("example.com")
    assert svc2 is None


def test_estimate_fines():
    result = estimate_fines(["FORM_001", "COOKIE_001", "TECH_003"])
    assert result["min_total"] > 0
    assert result["max_total"] > result["min_total"]
    assert len(result["breakdown"]) > 0
