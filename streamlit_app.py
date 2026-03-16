import httpx
import streamlit as st

API_BASE = "http://127.0.0.1:8000"
API_HEADERS = {"Authorization": "Bearer local-dev"}

st.set_page_config(
    page_title="Compliance 152-ФЗ",
    page_icon="🔒",
    layout="centered",
)

# ── Заголовок ──────────────────────────────────────────────────────────────────
st.title("🔒 Сканер соответствия 152-ФЗ")
st.caption("Проверка сайтов на соответствие требованиям закона о персональных данных")

# ── Статус бэкенда ─────────────────────────────────────────────────────────────
_transport = httpx.HTTPTransport()  # без системного прокси


@st.cache_data(ttl=10)
def check_backend():
    try:
        with httpx.Client(transport=_transport) as client:
            r = client.get(f"{API_BASE}/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False

backend_ok = check_backend()
if backend_ok:
    st.success("Бэкенд: работает", icon="✅")
else:
    st.error("Бэкенд недоступен — запустите сервер на порту 8000", icon="❌")

st.divider()

# ── Форма ──────────────────────────────────────────────────────────────────────
url = st.text_input(
    "URL сайта",
    placeholder="https://example.ru",
    help="Введите полный URL включая https://",
)

mode = st.radio(
    "Режим проверки",
    ["Быстрое сканирование", "Полный анализ (с LLM)"],
    horizontal=True,
    help="Быстрое сканирование — только техническая проверка. Полный анализ — сканирование + оценка нарушений Claude AI.",
)

max_pages = st.slider("Максимум страниц для обхода", 1, 100, 20)

run = st.button("Проверить сайт", type="primary", disabled=not backend_ok)

# ── Результаты ─────────────────────────────────────────────────────────────────
if run:
    if not url.strip():
        st.warning("Введите URL сайта")
    else:
        url = url.strip()
        if not url.startswith("http"):
            url = "https://" + url

        if mode == "Быстрое сканирование":
            with st.spinner(f"Сканирую {url}…"):
                try:
                    with httpx.Client(transport=_transport) as client:
                        resp = client.post(
                            f"{API_BASE}/api/v1/scan",
                            json={"url": url, "max_pages": max_pages},
                            headers=API_HEADERS,
                            timeout=120,
                        )
                    resp.raise_for_status()
                    data = resp.json()
                except httpx.HTTPStatusError as e:
                    st.error(f"Ошибка API {e.response.status_code}: {e.response.text}")
                    st.stop()
                except Exception as e:
                    st.error(f"Ошибка соединения: {e}")
                    st.stop()

            st.success("Сканирование завершено")
            st.subheader("Результаты сканирования")

            col1, col2, col3 = st.columns(3)
            col1.metric("Страниц проверено", data["pages_scanned"])
            col2.metric("Форм найдено", data["forms_found"])
            col3.metric("Внешних скриптов", data["external_scripts_found"])

            col4, col5 = st.columns(2)
            col4.metric(
                "Политика конфиденциальности",
                "Найдена ✅" if data["privacy_policy_found"] else "Не найдена ❌",
            )
            col5.metric(
                "Cookie-баннер",
                "Найден ✅" if data["cookie_banner_found"] else "Не найден ❌",
            )

            st.caption(f"ID сканирования: `{data['scan_id']}`")

        else:  # Полный анализ
            with st.spinner(f"Анализирую {url}… (может занять 1–2 минуты)"):
                try:
                    with httpx.Client(transport=_transport) as client:
                        resp = client.post(
                            f"{API_BASE}/api/v1/analyze",
                            json={"url": url, "max_pages": max_pages},
                            headers=API_HEADERS,
                            timeout=300,
                        )
                    resp.raise_for_status()
                    data = resp.json()
                except httpx.HTTPStatusError as e:
                    st.error(f"Ошибка API {e.response.status_code}: {e.response.text}")
                    st.stop()
                except Exception as e:
                    st.error(f"Ошибка соединения: {e}")
                    st.stop()

            # Уровень риска → цвет
            risk = data["risk_level"]
            risk_color = {"low": "🟢", "medium": "🟡", "high": "🔴", "critical": "🔴"}.get(risk, "⚪")

            st.success("Анализ завершён")
            st.subheader("Результаты анализа")

            col1, col2, col3 = st.columns(3)
            col1.metric("Оценка соответствия", f"{data['overall_score']} / 100")
            col2.metric("Уровень риска", f"{risk_color} {risk.upper()}")
            col3.metric("Нарушений", data["violations_count"])

            col4, col5, col6 = st.columns(3)
            col4.metric("Проверок пройдено", data["passed_checks"])
            col5.metric("Проверок провалено", data["failed_checks"])
            col6.metric("Критических нарушений", data["critical_violations"])

            if data["estimated_fine_min"] or data["estimated_fine_max"]:
                st.warning(
                    f"Возможный штраф: {data['estimated_fine_min']:,} — {data['estimated_fine_max']:,} ₽".replace(",", " ")
                )

            if data.get("summary"):
                with st.expander("Подробное резюме", expanded=True):
                    st.markdown(data["summary"])

            st.caption(f"ID отчёта: `{data['report_id']}`")

# ── Ссылка на LawGlance ────────────────────────────────────────────────────────
st.divider()
st.markdown(
    "**Нужна консультация по 152-ФЗ?** — "
    "[Открыть LawGlance](http://localhost:8502)",
    help="RAG-ассистент по законодательству о персональных данных",
)
