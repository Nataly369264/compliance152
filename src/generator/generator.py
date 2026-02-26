"""Document generator: creates 152-FZ documents using templates + LLM + web verification."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from pathlib import Path

from src.generator.prompts import (
    DOCUMENT_GENERATION_SYSTEM,
    DOCUMENT_GENERATION_USER,
    DOCUMENT_TYPES,
    FULL_PACKAGE,
    PUBLIC_DOCUMENTS,
)
from src.knowledge.loader import format_legal_context, get_updates_for_document_active
from src.llm.cache import get_web_context_cached
from src.llm.client import call_llm
from src.models.organization import OrganizationData

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "knowledge_base" / "templates"


class DocumentGenerator:
    """Generates 152-FZ compliance documents for an organization.

    Before generating each document, performs a web search to gather
    the latest legal requirements and injects them into the LLM prompt.
    """

    def __init__(self, organization: OrganizationData, enable_web_verification: bool = True):
        self.org = organization
        self.enable_web_verification = enable_web_verification

    async def generate_document(self, doc_type: str) -> dict:
        """Generate a single document by type.

        Steps:
        1. Load template from knowledge_base/templates/
        2. Load legal updates from static JSON
        3. (NEW) Gather web context via search + LLM summarization (cached)
        4. Call Claude with org data + template + legal context + web context
        5. Return generated document

        Returns dict with keys: id, doc_type, title, content_md, created_at, web_verified.
        """
        if doc_type not in DOCUMENT_TYPES:
            raise ValueError(f"Unknown document type: {doc_type}")

        doc_info = DOCUMENT_TYPES[doc_type]
        template_content = self._load_template(doc_info.get("template_file"))

        # Build dynamic legal context from the updates knowledge base
        updates = get_updates_for_document_active(doc_type)
        legal_context = format_legal_context(updates)

        # NEW: Gather web context (with caching)
        web_context = ""
        if self.enable_web_verification:
            try:
                web_context = await get_web_context_cached(
                    doc_type=doc_type,
                    doc_title=doc_info["title"],
                )
                if web_context:
                    logger.info(
                        "Web context gathered for %s: %d chars",
                        doc_type, len(web_context),
                    )
            except Exception as e:
                logger.warning("Web verification failed for %s: %s", doc_type, e)
                web_context = ""

        # Combine all legal context sources
        full_legal_context = self._build_legal_context(legal_context, web_context)

        llm_generated = False
        try:
            content = await call_llm(
                system_prompt=DOCUMENT_GENERATION_SYSTEM,
                user_prompt=DOCUMENT_GENERATION_USER.format(
                    doc_title=doc_info["title"],
                    doc_type=doc_type,
                    legal_name=self.org.legal_name,
                    short_name=self.org.short_name or self.org.legal_name,
                    inn=self.org.inn or "[ТРЕБУЕТСЯ ЗАПОЛНИТЬ]",
                    ogrn=self.org.ogrn or "[ТРЕБУЕТСЯ ЗАПОЛНИТЬ]",
                    legal_address=self.org.legal_address or "[ТРЕБУЕТСЯ ЗАПОЛНИТЬ]",
                    actual_address=self.org.actual_address or self.org.legal_address or "",
                    ceo_name=self.org.ceo_name or "[ТРЕБУЕТСЯ ЗАПОЛНИТЬ]",
                    ceo_position=self.org.ceo_position,
                    responsible_person=self.org.responsible_person or "[ТРЕБУЕТСЯ ЗАПОЛНИТЬ]",
                    responsible_contact=self.org.responsible_contact or self.org.email or "",
                    website_url=self.org.website_url,
                    email=self.org.email or "[ТРЕБУЕТСЯ ЗАПОЛНИТЬ]",
                    phone=self.org.phone or "[ТРЕБУЕТСЯ ЗАПОЛНИТЬ]",
                    data_categories=", ".join(self.org.data_categories) or "не указаны",
                    processing_purposes=", ".join(self.org.processing_purposes) or "не указаны",
                    data_subjects=", ".join(self.org.data_subjects) or "не указаны",
                    third_parties=", ".join(self.org.third_parties) or "не указаны",
                    cross_border="Да" if self.org.cross_border else "Нет",
                    cross_border_countries=", ".join(self.org.cross_border_countries) or "нет",
                    hosting_location=self.org.hosting_location,
                    info_systems=", ".join(self.org.info_systems) or "не указаны",
                    template_content=template_content,
                    legal_context=full_legal_context,
                ),
                max_tokens=8192,
                temperature=0.2,
            )
            llm_generated = True
        except Exception as llm_err:
            logger.warning(
                "LLM unavailable for %s, falling back to template: %s",
                doc_type, llm_err,
            )
            content = self._fill_template(template_content, doc_type)


        return {
            "id": str(uuid.uuid4()),
            "organization_id": self.org.id,
            "doc_type": doc_type,
            "title": doc_info["title"],
            "content_md": content,
            "version": 1,
            "created_at": datetime.utcnow().isoformat(),
            "web_verified": bool(web_context),
            "llm_generated": llm_generated,
        }

    async def generate_public_documents(self) -> list[dict]:
        """Generate documents required on the website."""
        results = []
        for doc_type in PUBLIC_DOCUMENTS:
            try:
                doc = await self.generate_document(doc_type)
                results.append(doc)
                logger.info("Generated %s for %s", doc_type, self.org.legal_name)
            except Exception as e:
                logger.error("Failed to generate %s: %s", doc_type, e)
                results.append({
                    "doc_type": doc_type,
                    "error": str(e),
                })
        return results

    async def generate_full_package(self) -> list[dict]:
        """Generate the complete document package (~12 documents)."""
        results = []
        for doc_type in FULL_PACKAGE:
            try:
                doc = await self.generate_document(doc_type)
                results.append(doc)
                logger.info("Generated %s for %s", doc_type, self.org.legal_name)
            except Exception as e:
                logger.error("Failed to generate %s: %s", doc_type, e)
                results.append({
                    "doc_type": doc_type,
                    "title": DOCUMENT_TYPES[doc_type]["title"],
                    "error": str(e),
                })
        return results

    def _fill_template(self, template_content: str, doc_type: str) -> str:
        """Fill template placeholders with org data (LLM fallback)."""
        from datetime import date as _date
        today = _date.today().strftime("%d.%m.%Y")

        if self.org.cross_border and self.org.cross_border_countries:
            cross_border_info = (
                "Осуществляется трансграничная передача данных в следующие страны: "
                + ", ".join(self.org.cross_border_countries)
            )
        elif self.org.cross_border:
            cross_border_info = "Осуществляется трансграничная передача персональных данных."
        else:
            cross_border_info = "Трансграничная передача персональных данных не осуществляется."

        R = "[ТРЕБУЕТСЯ ЗАПОЛНИТЬ]"
        replacements = {
            "LEGAL_NAME": self.org.legal_name,
            "SHORT_NAME": self.org.short_name or self.org.legal_name,
            "INN": self.org.inn or R,
            "OGRN": self.org.ogrn or R,
            "LEGAL_ADDRESS": self.org.legal_address or R,
            "ACTUAL_ADDRESS": self.org.actual_address or self.org.legal_address or R,
            "CEO_NAME": self.org.ceo_name or R,
            "CEO_POSITION": self.org.ceo_position,
            "RESPONSIBLE_PERSON": self.org.responsible_person or R,
            "RESPONSIBLE_CONTACT": self.org.responsible_contact or self.org.email or R,
            "WEBSITE_URL": self.org.website_url,
            "EMAIL": self.org.email or R,
            "PHONE": self.org.phone or R,
            "DATA_CATEGORIES": ", ".join(self.org.data_categories) if self.org.data_categories else R,
            "PROCESSING_PURPOSES": ", ".join(self.org.processing_purposes) if self.org.processing_purposes else R,
            "DATA_SUBJECTS": ", ".join(self.org.data_subjects) if self.org.data_subjects else R,
            "THIRD_PARTIES": ", ".join(self.org.third_parties) if self.org.third_parties else "не передаются",
            "CROSS_BORDER_INFO": cross_border_info,
            "HOSTING_LOCATION": self.org.hosting_location,
            "INFO_SYSTEMS": ", ".join(self.org.info_systems) if self.org.info_systems else R,
            "PUBLICATION_DATE": today,
            "UPDATE_DATE": today,
            "RETENTION_PERIODS": R,
        }

        result = template_content
        for key, value in replacements.items():
            result = result.replace("{{" + key + "}}", value)

        disclaimer = "> ⚠️ **Документ сформирован по шаблону** (LLM недоступен). Рекомендуется проверить и дополнить вручную.\n\n"

        return disclaimer + result

    def _load_template(self, template_file: str | None) -> str:
        """Load a template file, or return a minimal instruction if none exists."""
        if not template_file:
            return (
                "Шаблон отсутствует. Сгенерируй документ с нуля, основываясь на "
                "требованиях 152-ФЗ и данных организации. Используй стандартную "
                "структуру для данного типа документа."
            )

        path = TEMPLATES_DIR / template_file
        if not path.exists():
            logger.warning("Template not found: %s", path)
            return (
                f"Шаблон {template_file} не найден. Сгенерируй документ с нуля, "
                "основываясь на требованиях 152-ФЗ."
            )

        return path.read_text(encoding="utf-8")

    @staticmethod
    def _build_legal_context(db_context: str, web_context: str) -> str:
        """Combine static (DB) and dynamic (web) legal contexts into one block."""
        parts = []

        if db_context:
            parts.append(db_context)

        if web_context:
            parts.append(
                "РЕЗУЛЬТАТЫ ОНЛАЙН-ВЕРИФИКАЦИИ ЗАКОНОДАТЕЛЬСТВА "
                "(актуальные данные из интернета, обязательно учесть):\n\n"
                + web_context
            )

        return "\n\n".join(parts)


async def generate_documents(
    organization: OrganizationData,
    doc_types: list[str] | None = None,
    enable_web_verification: bool = True,
) -> list[dict]:
    """Convenience function to generate documents for an organization.

    If doc_types is None, generates the full package.
    """
    generator = DocumentGenerator(organization, enable_web_verification=enable_web_verification)

    if doc_types is None:
        return await generator.generate_full_package()

    results = []
    for dt in doc_types:
        try:
            doc = await generator.generate_document(dt)
            results.append(doc)
        except Exception as e:
            results.append({"doc_type": dt, "error": str(e)})
    return results
