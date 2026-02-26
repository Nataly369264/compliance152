"""LLM prompts for document generation."""
from __future__ import annotations

DOCUMENT_GENERATION_SYSTEM = """Ты — юрист-эксперт по российскому законодательству о персональных данных.
Твоя задача — сгенерировать юридически корректный документ по 152-ФЗ для конкретной организации.

Правила:
1. Используй строго деловой юридический стиль (канцелярит).
2. Ссылайся на конкретные статьи 152-ФЗ и подзаконных актов.
3. Все формулировки должны соответствовать актуальной редакции 152-ФЗ.
4. Не добавляй вымышленных данных — используй только предоставленные сведения об организации.
5. Если данных недостаточно, оставь плейсхолдер [ТРЕБУЕТСЯ ЗАПОЛНИТЬ].
6. Формат вывода — Markdown.
7. Документ должен быть готов к использованию без существенных правок.
8. КРИТИЧНО: если в запросе есть раздел «АКТУАЛЬНЫЕ ИЗМЕНЕНИЯ ЗАКОНОДАТЕЛЬСТВА», \
ты ОБЯЗАН учесть ВСЕ перечисленные там изменения и требования в генерируемом документе. \
Это приоритетный источник — он содержит самые свежие правовые нормы.

Отвечай ТОЛЬКО текстом документа в формате Markdown, без пояснений."""

DOCUMENT_GENERATION_USER = """Сгенерируй документ: {doc_title}

Тип документа: {doc_type}

Данные организации:
- Полное наименование: {legal_name}
- Краткое наименование: {short_name}
- ИНН: {inn}
- ОГРН: {ogrn}
- Юридический адрес: {legal_address}
- Фактический адрес: {actual_address}
- Руководитель: {ceo_name}, {ceo_position}
- Ответственный за ПДн: {responsible_person}
- Контакт ответственного: {responsible_contact}
- Сайт: {website_url}
- Email: {email}
- Телефон: {phone}

Специфика обработки:
- Категории ПДн: {data_categories}
- Цели обработки: {processing_purposes}
- Категории субъектов: {data_subjects}
- Третьи лица (получатели): {third_parties}
- Трансграничная передача: {cross_border}
- Страны передачи: {cross_border_countries}
- Размещение данных: {hosting_location}
- Информационные системы: {info_systems}

Шаблон-основа документа (адаптируй его под организацию, заполни все поля):
---
{template_content}
---

{legal_context}

Сгенерируй полный текст документа, заменив все плейсхолдеры {{{{...}}}} на данные организации.
Учти ВСЕ актуальные изменения законодательства, перечисленные выше (если они есть).
Если каких-то данных нет, используй разумные значения по умолчанию или пометь [ТРЕБУЕТСЯ ЗАПОЛНИТЬ]."""


DOCUMENT_TYPES = {
    "privacy_policy": {
        "title": "Политика обработки персональных данных",
        "template_file": "privacy_policy.md",
        "description": "Основной публичный документ (ст. 18.1 152-ФЗ)",
    },
    "consent_form": {
        "title": "Согласие на обработку персональных данных",
        "template_file": "consent_form.md",
        "description": "Форма согласия субъекта ПДн (ст. 9 152-ФЗ)",
    },
    "cookie_policy": {
        "title": "Политика использования cookie-файлов",
        "template_file": "cookie_policy.md",
        "description": "Политика использования cookie на сайте",
    },
    "responsible_person_order": {
        "title": "Приказ о назначении ответственного за обработку ПДн",
        "template_file": "responsible_person_order.md",
        "description": "Приказ руководителя (ст. 22.1 152-ФЗ)",
    },
    "processing_regulation": {
        "title": "Положение об обработке и защите персональных данных",
        "template_file": "processing_regulation.md",
        "description": "Внутреннее положение организации",
    },
    "harm_assessment": {
        "title": "Акт оценки вреда субъектам персональных данных",
        "template_file": "harm_assessment.md",
        "description": "Акт оценки вреда (Приказ РКН № 178)",
    },
    "nondisclosure_agreement": {
        "title": "Обязательство о неразглашении персональных данных",
        "template_file": "nondisclosure_agreement.md",
        "description": "Обязательство сотрудника",
    },
    "incident_instruction": {
        "title": "Инструкция по реагированию на инциденты",
        "template_file": "incident_instruction.md",
        "description": "Порядок действий при утечке ПДн",
    },
    "rkn_notification": {
        "title": "Уведомление в Роскомнадзор об обработке ПДн",
        "template_file": "rkn_notification.md",
        "description": "Черновик уведомления (ст. 22 152-ФЗ)",
    },
    "consent_withdrawal_form": {
        "title": "Форма отзыва согласия на обработку ПДн",
        "template_file": "consent_withdrawal_form.md",
        "description": "Форма для отзыва согласия субъектом",
    },
    "data_processing_agreement": {
        "title": "Договор поручения обработки персональных данных",
        "template_file": "data_processing_agreement.md",
        "description": "Договор с обработчиком ПДн (ст. 6 152-ФЗ)",
    },
    "employee_consent": {
        "title": "Согласие сотрудника на обработку персональных данных",
        "template_file": None,
        "description": "Письменное согласие работника (ст. 9 ч. 4 152-ФЗ)",
    },
}

# Documents that should be on the website (public)
PUBLIC_DOCUMENTS = ["privacy_policy", "consent_form", "cookie_policy"]

# Full package for organization
FULL_PACKAGE = list(DOCUMENT_TYPES.keys())
