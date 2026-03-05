from __future__ import annotations

import json
import logging
import os
from datetime import datetime

import aiosqlite

from src.config import DB_PATH

logger = logging.getLogger(__name__)

DDL = """
CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY,
    legal_name TEXT NOT NULL,
    short_name TEXT DEFAULT '',
    inn TEXT DEFAULT '',
    ogrn TEXT DEFAULT '',
    legal_address TEXT DEFAULT '',
    actual_address TEXT DEFAULT '',
    ceo_name TEXT DEFAULT '',
    ceo_position TEXT DEFAULT 'Генеральный директор',
    responsible_person TEXT DEFAULT '',
    responsible_contact TEXT DEFAULT '',
    website_url TEXT NOT NULL,
    email TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    data_categories TEXT DEFAULT '[]',
    processing_purposes TEXT DEFAULT '[]',
    data_subjects TEXT DEFAULT '[]',
    third_parties TEXT DEFAULT '[]',
    cross_border INTEGER DEFAULT 0,
    cross_border_countries TEXT DEFAULT '[]',
    hosting_location TEXT DEFAULT 'Российская Федерация',
    info_systems TEXT DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scans (
    id TEXT PRIMARY KEY,
    organization_id TEXT,
    site_url TEXT NOT NULL,
    scan_date TEXT NOT NULL,
    result_json TEXT NOT NULL,
    pages_scanned INTEGER DEFAULT 0,
    FOREIGN KEY (organization_id) REFERENCES organizations(id)
);

CREATE TABLE IF NOT EXISTS compliance_reports (
    id TEXT PRIMARY KEY,
    scan_id TEXT NOT NULL,
    organization_id TEXT,
    site_url TEXT NOT NULL,
    report_date TEXT NOT NULL,
    overall_score INTEGER DEFAULT 0,
    risk_level TEXT DEFAULT 'high',
    total_checks INTEGER DEFAULT 0,
    passed_checks INTEGER DEFAULT 0,
    failed_checks INTEGER DEFAULT 0,
    report_json TEXT NOT NULL,
    summary TEXT DEFAULT '',
    FOREIGN KEY (scan_id) REFERENCES scans(id),
    FOREIGN KEY (organization_id) REFERENCES organizations(id)
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    title TEXT NOT NULL,
    version INTEGER DEFAULT 1,
    content_md TEXT NOT NULL,
    content_html TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (organization_id) REFERENCES organizations(id)
);

CREATE TABLE IF NOT EXISTS document_versions (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    content_md TEXT NOT NULL,
    change_reason TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (document_id) REFERENCES documents(id)
);

CREATE INDEX IF NOT EXISTS idx_scans_org ON scans(organization_id);
CREATE INDEX IF NOT EXISTS idx_reports_org ON compliance_reports(organization_id);
CREATE INDEX IF NOT EXISTS idx_documents_org ON documents(organization_id);
CREATE INDEX IF NOT EXISTS idx_doc_versions_doc ON document_versions(document_id);

-- ── Competitor Intelligence Monitor ──────────────────────────────

CREATE TABLE IF NOT EXISTS competitor_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id    TEXT NOT NULL,
    url          TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    raw_text     TEXT,
    fetch_status TEXT DEFAULT 'ok',
    captured_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS competitor_changes (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id          TEXT NOT NULL,
    url                TEXT NOT NULL,
    diff_summary       TEXT,
    change_type        TEXT,
    threat_score       INTEGER,
    npa_critical       INTEGER DEFAULT 0,
    detected_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    included_in_digest INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS digests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start DATE,
    period_end   DATE,
    content_md   TEXT,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_snapshots_source ON competitor_snapshots(source_id, url);
CREATE INDEX IF NOT EXISTS idx_changes_digest ON competitor_changes(included_in_digest, detected_at);
"""


class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(DDL)
        await self._db.commit()
        logger.info("Database initialized at %s", self.db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database not initialized. Call init() first.")
        return self._db

    # ── Organizations ────────────────────────────────────────────

    async def save_organization(self, org: dict) -> str:
        await self.db.execute(
            """INSERT OR REPLACE INTO organizations
            (id, legal_name, short_name, inn, ogrn, legal_address, actual_address,
             ceo_name, ceo_position, responsible_person, responsible_contact,
             website_url, email, phone, data_categories, processing_purposes,
             data_subjects, third_parties, cross_border, cross_border_countries,
             hosting_location, info_systems, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                org["id"], org["legal_name"], org.get("short_name", ""),
                org.get("inn", ""), org.get("ogrn", ""),
                org.get("legal_address", ""), org.get("actual_address", ""),
                org.get("ceo_name", ""), org.get("ceo_position", "Генеральный директор"),
                org.get("responsible_person", ""), org.get("responsible_contact", ""),
                org["website_url"], org.get("email", ""), org.get("phone", ""),
                json.dumps(org.get("data_categories", []), ensure_ascii=False),
                json.dumps(org.get("processing_purposes", []), ensure_ascii=False),
                json.dumps(org.get("data_subjects", []), ensure_ascii=False),
                json.dumps(org.get("third_parties", []), ensure_ascii=False),
                1 if org.get("cross_border") else 0,
                json.dumps(org.get("cross_border_countries", []), ensure_ascii=False),
                org.get("hosting_location", "Российская Федерация"),
                json.dumps(org.get("info_systems", []), ensure_ascii=False),
                org.get("created_at", datetime.utcnow().isoformat()),
                datetime.utcnow().isoformat(),
            ),
        )
        await self.db.commit()
        return org["id"]

    async def get_organization(self, org_id: str) -> dict | None:
        cursor = await self.db.execute(
            "SELECT * FROM organizations WHERE id = ?", (org_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_org(row)

    async def list_organizations(self, limit: int = 50) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM organizations ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_org(row) for row in rows]

    # ── Scans ────────────────────────────────────────────────────

    async def save_scan(self, scan_id: str, org_id: str | None, url: str, result_json: str, pages: int) -> None:
        await self.db.execute(
            """INSERT INTO scans (id, organization_id, site_url, scan_date, result_json, pages_scanned)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (scan_id, org_id, url, datetime.utcnow().isoformat(), result_json, pages),
        )
        await self.db.commit()

    async def get_scan(self, scan_id: str) -> dict | None:
        cursor = await self.db.execute("SELECT * FROM scans WHERE id = ?", (scan_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    # ── Reports ──────────────────────────────────────────────────

    async def save_report(self, report: dict) -> None:
        await self.db.execute(
            """INSERT INTO compliance_reports
            (id, scan_id, organization_id, site_url, report_date,
             overall_score, risk_level, total_checks, passed_checks, failed_checks,
             report_json, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                report["id"], report.get("scan_id", ""), report.get("organization_id"),
                report["site_url"], report.get("report_date", datetime.utcnow().isoformat()),
                report.get("overall_score", 0), report.get("risk_level", "high"),
                report.get("total_checks", 0), report.get("passed_checks", 0),
                report.get("failed_checks", 0),
                json.dumps(report, ensure_ascii=False, default=str),
                report.get("summary", ""),
            ),
        )
        await self.db.commit()

    async def list_reports(self, org_id: str | None = None, limit: int = 20) -> list[dict]:
        if org_id:
            cursor = await self.db.execute(
                "SELECT * FROM compliance_reports WHERE organization_id = ? ORDER BY report_date DESC LIMIT ?",
                (org_id, limit),
            )
        else:
            cursor = await self.db.execute(
                "SELECT * FROM compliance_reports ORDER BY report_date DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ── Documents ────────────────────────────────────────────────

    async def save_document(self, doc: dict) -> str:
        await self.db.execute(
            """INSERT OR REPLACE INTO documents
            (id, organization_id, doc_type, title, version, content_md, content_html, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                doc["id"], doc["organization_id"], doc["doc_type"], doc["title"],
                doc.get("version", 1), doc["content_md"], doc.get("content_html", ""),
                doc.get("created_at", datetime.utcnow().isoformat()),
                datetime.utcnow().isoformat(),
            ),
        )
        await self.db.commit()
        return doc["id"]

    async def get_documents(self, org_id: str) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM documents WHERE organization_id = ? ORDER BY doc_type",
            (org_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ── Competitor Intelligence Monitor ──────────────────────────

    async def get_last_snapshot(self, source_id: str, url: str) -> dict | None:
        """Return the most recent snapshot for a given source_id + url."""
        cursor = await self.db.execute(
            """SELECT * FROM competitor_snapshots
               WHERE source_id = ? AND url = ?
               ORDER BY captured_at DESC LIMIT 1""",
            (source_id, url),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def save_snapshot(
        self,
        source_id: str,
        url: str,
        content_hash: str,
        raw_text: str,
        fetch_status: str = "ok",
    ) -> int:
        """Insert a new snapshot row and return its id."""
        cursor = await self.db.execute(
            """INSERT INTO competitor_snapshots
               (source_id, url, content_hash, raw_text, fetch_status)
               VALUES (?, ?, ?, ?, ?)""",
            (source_id, url, content_hash, raw_text[:10240], fetch_status),
        )
        await self.db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def save_change(
        self,
        source_id: str,
        url: str,
        diff_summary: str | None,
        change_type: str,
        threat_score: int | None = None,
        npa_critical: bool = False,
    ) -> int:
        """Record a detected change and return its id."""
        cursor = await self.db.execute(
            """INSERT INTO competitor_changes
               (source_id, url, diff_summary, change_type, threat_score, npa_critical)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (source_id, url, diff_summary, change_type, threat_score, 1 if npa_critical else 0),
        )
        await self.db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def list_pending_changes(self, limit: int = 100) -> list[dict]:
        """Return changes not yet included in a digest, sorted by threat_score DESC."""
        cursor = await self.db.execute(
            """SELECT * FROM competitor_changes
               WHERE included_in_digest = 0
               ORDER BY threat_score DESC NULLS LAST, detected_at DESC
               LIMIT ?""",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def mark_changes_digested(self, change_ids: list[int]) -> None:
        """Mark the given change ids as included in a digest."""
        if not change_ids:
            return
        placeholders = ",".join("?" * len(change_ids))
        await self.db.execute(
            f"UPDATE competitor_changes SET included_in_digest = 1 WHERE id IN ({placeholders})",
            change_ids,
        )
        await self.db.commit()

    async def save_digest(self, period_start: str, period_end: str, content_md: str) -> int:
        """Save a new digest and return its id."""
        cursor = await self.db.execute(
            """INSERT INTO digests (period_start, period_end, content_md)
               VALUES (?, ?, ?)""",
            (period_start, period_end, content_md),
        )
        await self.db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_latest_digest(self) -> dict | None:
        """Return the most recently created digest."""
        cursor = await self.db.execute(
            "SELECT * FROM digests ORDER BY created_at DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_digests(self, limit: int = 20) -> list[dict]:
        """Return digests ordered by creation date, newest first."""
        cursor = await self.db.execute(
            "SELECT * FROM digests ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _row_to_org(row) -> dict:
        d = dict(row)
        for field in ("data_categories", "processing_purposes", "data_subjects",
                      "third_parties", "cross_border_countries", "info_systems"):
            if isinstance(d.get(field), str):
                d[field] = json.loads(d[field])
        d["cross_border"] = bool(d.get("cross_border"))
        return d


_db: Database | None = None


async def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
        await _db.init()
    return _db
