from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"
    NOT_APPLICABLE = "not_applicable"
    MANUAL_CHECK = "manual_check"


class CheckCategory(str, Enum):
    FORMS = "forms"
    COOKIES = "cookies"
    PRIVACY_POLICY = "privacy_policy"
    TECHNICAL = "technical"
    REGULATORY = "regulatory"


class CheckItem(BaseModel):
    id: str
    category: CheckCategory
    title: str
    description: str
    status: CheckStatus = CheckStatus.MANUAL_CHECK
    severity: Severity = Severity.MEDIUM
    details: str | None = None
    law_reference: str | None = None
    recommendation: str | None = None


class Violation(BaseModel):
    check_id: str
    title: str
    description: str
    severity: Severity
    category: CheckCategory
    page_url: str | None = None
    law_reference: str | None = None
    fine_range: str | None = None
    recommendation: str


class FineEstimate(BaseModel):
    min_total: int = 0
    max_total: int = 0
    breakdown: list[FineItem] = []


class FineItem(BaseModel):
    violation: str
    min_fine: int
    max_fine: int
    law_reference: str
    repeat_offense_max: int | None = None


class ComplianceReport(BaseModel):
    id: str = ""
    site_url: str
    scan_date: datetime = Field(default_factory=datetime.utcnow)
    overall_score: int = 0  # 0-100
    risk_level: Severity = Severity.HIGH
    total_checks: int = 0
    passed_checks: int = 0
    failed_checks: int = 0
    warnings: int = 0
    violations: list[Violation] = []
    checklist: list[CheckItem] = []
    fine_estimate: FineEstimate = FineEstimate()
    llm_analysis: str | None = None  # Глубокий анализ политики от LLM
    summary: str = ""
