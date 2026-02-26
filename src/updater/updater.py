"""Document auto-updater: regenerates documents when legislation changes.

When Monitor finds new legal updates, Updater determines which organizations
and documents are affected, and uses Generator to create new versions.
"""
from __future__ import annotations

import difflib
import logging
import uuid
from datetime import datetime

from src.generator.generator import DocumentGenerator
from src.llm.client import call_llm
from src.models.legal_update import LegalUpdate
from src.models.organization import OrganizationData
from src.storage.database import get_db

logger = logging.getLogger(__name__)

# ── Prompts for document update ──────────────────────────────────

UPDATE_SYSTEM_PROMPT = """Ты — юрист-эксперт по 152-ФЗ.

Тебе будет предоставлен текущий юридический документ и описание изменений в законодательстве.
Твоя задача — обновить документ с учётом этих изменений.

Правила:
1. Сохрани общую структуру документа.
2. Измени ТОЛЬКО те разделы, которые затронуты изменениями.
3. Добавь новые разделы, если изменения этого требуют.
4. Обнови ссылки на статьи закона.
5. Обнови дату документа.
6. Сохрани стиль и оформление оригинала.
7. Выведи ПОЛНЫЙ текст обновлённого документа (не только изменения).

Формат вывода — Markdown."""

UPDATE_USER_PROMPT = """Обнови следующий документ с учётом изменений в законодательстве.

Тип документа: {doc_type} — {doc_title}

Текущий текст документа:
---
{current_content}
---

Изменения в законодательстве, которые нужно учесть:
{legal_changes}

Выведи ПОЛНЫЙ текст обновлённого документа."""


class DocumentUpdater:
    """Updates existing documents based on new legal changes."""

    async def process_update(
        self,
        update: LegalUpdate,
        mode: str = "confirm",  # "auto" | "confirm"
    ) -> list[dict]:
        """Process a legal update: find affected documents and update them.

        Args:
            update: The legal update to process.
            mode: "auto" — update immediately, "confirm" — create drafts.

        Returns list of update results, one per affected document.
        """
        db = await get_db()
        results = []

        logger.info(
            "Processing legal update %s: %s (affects: %s)",
            update.id, update.title, ", ".join(update.affected_documents),
        )

        # Find all organizations that have affected documents
        for doc_type in update.affected_documents:
            # Get all documents of this type across organizations
            orgs_docs = await self._find_affected_documents(doc_type)

            for org_id, doc_data in orgs_docs:
                try:
                    result = await self._update_single_document(
                        org_id=org_id,
                        doc_data=doc_data,
                        update=update,
                        mode=mode,
                    )
                    results.append(result)
                except Exception as e:
                    logger.error(
                        "Failed to update %s for org %s: %s",
                        doc_type, org_id, e,
                    )
                    results.append({
                        "organization_id": org_id,
                        "doc_type": doc_type,
                        "status": "error",
                        "error": str(e),
                    })

        return results

    async def _find_affected_documents(
        self, doc_type: str,
    ) -> list[tuple[str, dict]]:
        """Find all existing documents of a given type across all organizations.

        Returns list of (org_id, doc_data) tuples.
        """
        db = await get_db()
        # We need to get all unique org IDs that have documents
        # This is a simplified approach — in production, use a proper DB query
        all_reports = await db.list_reports(limit=1000)
        org_ids = set()
        for r in all_reports:
            oid = r.get("organization_id")
            if oid:
                org_ids.add(oid)

        results = []
        for org_id in org_ids:
            docs = await db.get_documents(org_id)
            for doc in docs:
                if doc.get("doc_type") == doc_type:
                    results.append((org_id, doc))

        return results

    async def _update_single_document(
        self,
        org_id: str,
        doc_data: dict,
        update: LegalUpdate,
        mode: str,
    ) -> dict:
        """Update a single document based on a legal change.

        Returns a result dict with status, diff, and optionally new content.
        """
        db = await get_db()
        doc_type = doc_data["doc_type"]
        doc_title = doc_data.get("title", doc_type)
        current_content = doc_data.get("content_md", "")

        if not current_content:
            return {
                "organization_id": org_id,
                "doc_type": doc_type,
                "status": "skipped",
                "reason": "Document has no content",
            }

        # Format legal changes for LLM
        legal_changes = self._format_update_for_prompt(update)

        # Generate updated document via LLM
        new_content = await call_llm(
            system_prompt=UPDATE_SYSTEM_PROMPT,
            user_prompt=UPDATE_USER_PROMPT.format(
                doc_type=doc_type,
                doc_title=doc_title,
                current_content=current_content[:12000],
                legal_changes=legal_changes,
            ),
            max_tokens=8192,
            temperature=0.2,
        )

        # Generate diff
        diff = self._generate_diff(current_content, new_content)

        # Determine new version number
        current_version = doc_data.get("version", 1)
        new_version = current_version + 1

        result = {
            "organization_id": org_id,
            "doc_type": doc_type,
            "doc_title": doc_title,
            "old_version": current_version,
            "new_version": new_version,
            "diff": diff,
            "update_id": update.id,
            "update_title": update.title,
        }

        if mode == "auto":
            # Save new version immediately
            doc_id = doc_data.get("id", str(uuid.uuid4()))
            await db.save_document({
                "id": str(uuid.uuid4()),
                "organization_id": org_id,
                "doc_type": doc_type,
                "title": doc_title,
                "content_md": new_content,
                "version": new_version,
                "created_at": datetime.utcnow().isoformat(),
            })
            result["status"] = "updated"
            result["new_content"] = new_content
            logger.info(
                "Auto-updated %s for org %s: v%d → v%d",
                doc_type, org_id, current_version, new_version,
            )
        else:
            # Draft mode — return for confirmation
            result["status"] = "draft"
            result["draft_content"] = new_content
            logger.info(
                "Draft created for %s for org %s: v%d → v%d",
                doc_type, org_id, current_version, new_version,
            )

        return result

    @staticmethod
    def _format_update_for_prompt(update: LegalUpdate) -> str:
        """Format a LegalUpdate into a text block for the LLM prompt."""
        lines = [
            f"Изменение: {update.title}",
            f"Источник: {update.source}",
            f"Дата вступления в силу: {update.effective_date}",
            f"Затронутые статьи: {', '.join(update.articles)}",
            f"Описание: {update.summary}",
        ]
        if update.requirements:
            lines.append("Новые требования:")
            for req in update.requirements:
                lines.append(f"  - {req}")
        return "\n".join(lines)

    @staticmethod
    def _generate_diff(old_text: str, new_text: str) -> str:
        """Generate a human-readable diff between old and new document versions."""
        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)

        diff = difflib.unified_diff(
            old_lines, new_lines,
            fromfile="Текущая версия",
            tofile="Обновлённая версия",
            lineterm="",
        )

        return "".join(diff)

    async def regenerate_document(
        self,
        org_id: str,
        doc_type: str,
    ) -> dict:
        """Fully regenerate a document from scratch with current templates and web context.

        Uses Generator with web verification enabled.
        """
        db = await get_db()
        org_data = await db.get_organization(org_id)
        if not org_data:
            raise ValueError(f"Organization not found: {org_id}")

        org = OrganizationData(**org_data)
        generator = DocumentGenerator(org, enable_web_verification=True)
        doc = await generator.generate_document(doc_type)

        await db.save_document(doc)
        return doc


async def process_legal_updates(
    updates: list[LegalUpdate],
    mode: str = "confirm",
) -> list[dict]:
    """Process multiple legal updates.

    Convenience function for API/CLI use.
    """
    updater = DocumentUpdater()
    all_results = []

    for update in updates:
        results = await updater.process_update(update, mode=mode)
        all_results.extend(results)

    return all_results
