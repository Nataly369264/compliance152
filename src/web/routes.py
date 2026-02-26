from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from src.storage.database import get_db

_templates_dir = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))

web_router = APIRouter(tags=["web"], default_response_class=HTMLResponse)


@web_router.get("/")
async def dashboard(request: Request):
    db = await get_db()
    reports = await db.list_reports(limit=5)
    orgs = await db.list_organizations(limit=5)
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "recent_reports": reports,
        "recent_orgs": orgs,
    })


@web_router.get("/check")
async def check_page(request: Request):
    return templates.TemplateResponse("check.html", {
        "request": request,
    })


@web_router.get("/organizations/new")
async def organization_form(request: Request):
    return templates.TemplateResponse("organization_form.html", {
        "request": request,
    })


@web_router.get("/organizations/{org_id}")
async def organization_view(request: Request, org_id: str):
    db = await get_db()
    org = await db.get_organization(org_id)
    if not org:
        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "recent_reports": [],
            "recent_orgs": [],
            "error": "Организация не найдена",
        })
    docs = await db.get_documents(org_id)
    return templates.TemplateResponse("organization_view.html", {
        "request": request,
        "org": org,
        "documents": docs,
    })


@web_router.get("/documents")
async def documents_page(request: Request):
    db = await get_db()
    orgs = await db.list_organizations()
    return templates.TemplateResponse("documents.html", {
        "request": request,
        "organizations": orgs,
    })


@web_router.get("/reports")
async def reports_list(request: Request):
    db = await get_db()
    reports = await db.list_reports(limit=50)
    return templates.TemplateResponse("reports_list.html", {
        "request": request,
        "reports": reports,
    })


@web_router.get("/reports/{report_id}")
async def report_view(request: Request, report_id: str):
    db = await get_db()
    reports = await db.list_reports()
    report_data = None
    for r in reports:
        if r.get("id") == report_id:
            report_json = r.get("report_json")
            if report_json:
                report_data = json.loads(report_json)
            else:
                report_data = r
            break
    return templates.TemplateResponse("report.html", {
        "request": request,
        "report": report_data,
    })
