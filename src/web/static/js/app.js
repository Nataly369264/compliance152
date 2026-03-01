/* ── Compliance 152-ФЗ — Web UI ──────────────────────────── */

// ── Utilities ───────────────────────────────────────────────

function formatRubles(amount) {
    if (!amount && amount !== 0) return '—';
    return new Intl.NumberFormat('ru-RU').format(amount);
}

function showEl(id) { document.getElementById(id).hidden = false; }
function hideEl(id) { document.getElementById(id).hidden = true; }

function showError(msg) {
    var box = document.getElementById('error-box');
    if (box) { box.textContent = msg; box.hidden = false; }
}
function hideError() {
    var box = document.getElementById('error-box');
    if (box) { box.hidden = true; }
}

function riskClass(level) {
    if (!level) return '';
    var l = level.toLowerCase();
    if (l === 'critical') return 'critical';
    if (l === 'high') return 'high';
    if (l === 'medium') return 'medium';
    if (l === 'low') return 'low';
    return 'info';
}

var RISK_LABELS = {
    critical: 'Критический',
    high: 'Высокий',
    medium: 'Средний',
    low: 'Низкий',
    info: 'Информация',
};

var SEVERITY_ORDER = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };

async function fetchAndRenderViolations(reportId) {
    try {
        var res = await fetch('/api/v1/report/' + reportId, {
            headers: { 'Authorization': 'Bearer dev' },
        });
        if (!res.ok) return;
        var report = await res.json();
        renderTopViolations(report.violations || []);
    } catch (e) { /* блок остаётся скрытым */ }
}

function renderTopViolations(violations) {
    var block = document.getElementById('top-violations');
    var list = document.getElementById('violations-list');
    if (!violations || violations.length === 0) { block.hidden = true; return; }

    var sorted = violations.slice().sort(function (a, b) {
        return (SEVERITY_ORDER[a.severity] || 9) - (SEVERITY_ORDER[b.severity] || 9);
    });

    list.innerHTML = '';
    sorted.slice(0, 3).forEach(function (v) {
        var li = document.createElement('li');
        li.className = 'violation-preview-item';
        var fine = v.fine_range
            ? '<span class="violation-fine">' + escapeHtml(v.fine_range) + '</span>'
            : '';
        li.innerHTML =
            '<span class="severity-dot risk-' + riskClass(v.severity) + '"></span>' +
            '<span class="violation-preview-title" title="' + escapeHtml(v.recommendation || '') + '">' +
            escapeHtml(v.title) + '</span>' +
            fine;
        list.appendChild(li);
    });
    block.hidden = false;
}

// ── countUp animation ────────────────────────────────────────

function countUp(el, target, duration) {
    var startTime = null;
    target = parseInt(target, 10) || 0;
    function step(timestamp) {
        if (!startTime) startTime = timestamp;
        var progress = Math.min((timestamp - startTime) / duration, 1);
        var ease = 1 - Math.pow(1 - progress, 3);
        el.textContent = Math.round(ease * target);
        if (progress < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
}

// ── Navigation toggle ───────────────────────────────────────

function initNav() {
    var toggle = document.getElementById('nav-toggle');
    var links = document.getElementById('nav-links');
    if (toggle && links) {
        toggle.addEventListener('click', function () {
            links.classList.toggle('open');
        });
    }
}

// ── Phase progress ───────────────────────────────────────────

var _phaseTimers = [];

function startPhaseProgress() {
    var el = document.querySelector('.loading-text');
    if (!el) return;
    el.firstChild.textContent = 'Сканирование страниц...';
    _phaseTimers.push(setTimeout(function () {
        el.firstChild.textContent = 'Анализ нарушений...';
    }, 8000));
    _phaseTimers.push(setTimeout(function () {
        el.firstChild.textContent = 'Формируем отчёт...';
    }, 20000));
}

function stopPhaseProgress() {
    _phaseTimers.forEach(function (t) { clearTimeout(t); });
    _phaseTimers = [];
    var el = document.querySelector('.loading-text');
    if (el) el.firstChild.textContent = 'Сканирование и анализ сайта...';
}

// ── URL Check page ──────────────────────────────────────────

function initCheckPage() {
    var form = document.getElementById('check-form');
    if (!form) return;

    form.addEventListener('submit', async function (e) {
        e.preventDefault();
        var url = document.getElementById('site-url').value.trim();
        if (!url) return;

        hideError();
        hideEl('results');
        showEl('loading');
        startPhaseProgress();
        document.getElementById('check-btn').disabled = true;

        try {
            var res = await fetch('/api/v1/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer dev' },
                body: JSON.stringify({ url: url }),
            });
            if (!res.ok) {
                var err = await res.json().catch(function () { return {}; });
                throw new Error(err.detail || 'Ошибка анализа (код ' + res.status + ')');
            }
            var data = await res.json();
            renderCheckResults(data);
            await fetchAndRenderViolations(data.report_id);
        } catch (err) {
            showError(err.message);
        } finally {
            stopPhaseProgress();
            hideEl('loading');
            document.getElementById('check-btn').disabled = false;
        }
    });
}

function renderCheckResults(data) {
    showEl('results');

    var rc = riskClass(data.risk_level);
    var circle = document.getElementById('score-circle');
    circle.className = 'score-circle risk-' + rc;
    countUp(document.getElementById('score-value'), data.overall_score, 800);

    document.getElementById('result-url').textContent = data.site_url;
    document.getElementById('result-summary').innerHTML = marked.parse(data.summary || '');
    document.getElementById('checks-passed').textContent = data.passed_checks;
    document.getElementById('checks-failed').textContent = data.failed_checks;
    document.getElementById('violations-count').textContent = data.violations_count;

    var fineText = '—';
    if (data.estimated_fine_min || data.estimated_fine_max) {
        fineText = formatRubles(data.estimated_fine_min) + ' – ' + formatRubles(data.estimated_fine_max);
    }
    document.getElementById('fine-range').textContent = fineText;

    var badge = document.getElementById('risk-badge');
    badge.className = 'risk-label ' + rc;
    badge.textContent = RISK_LABELS[rc] || data.risk_level;

    document.getElementById('full-report-link').href = '/reports/' + data.report_id;
    document.getElementById('all-violations-link').href = '/reports/' + data.report_id;
}

// ── Organization form ───────────────────────────────────────

function initOrgForm() {
    var form = document.getElementById('org-form');
    if (!form) return;

    // Tag input fields
    var tagFields = ['data_categories', 'processing_purposes', 'data_subjects',
                     'third_parties', 'cross_border_countries', 'info_systems'];
    var tagData = {};
    tagFields.forEach(function (field) {
        tagData[field] = [];
        initTagInput(field, tagData);
    });

    form.addEventListener('submit', async function (e) {
        e.preventDefault();
        hideError();

        var body = {
            legal_name: val('legal_name'),
            website_url: val('website_url'),
            short_name: val('short_name'),
            inn: val('inn'),
            ogrn: val('ogrn'),
            legal_address: val('legal_address'),
            actual_address: val('actual_address'),
            ceo_name: val('ceo_name'),
            ceo_position: val('ceo_position') || 'Генеральный директор',
            responsible_person: val('responsible_person'),
            responsible_contact: val('responsible_contact'),
            email: val('email'),
            phone: val('phone'),
            cross_border: document.getElementById('cross_border').checked,
            hosting_location: val('hosting_location') || 'Российская Федерация',
        };
        tagFields.forEach(function (f) { body[f] = tagData[f]; });

        var btn = form.querySelector('button[type="submit"]');
        btn.disabled = true;
        btn.textContent = 'Сохранение...';

        try {
            var res = await fetch('/api/v1/organizations', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer dev' },
                body: JSON.stringify(body),
            });
            if (!res.ok) {
                var err = await res.json().catch(function () { return {}; });
                throw new Error(err.detail || 'Ошибка сохранения');
            }
            var data = await res.json();
            window.location.href = '/organizations/' + data.id;
        } catch (err) {
            showError(err.message);
            btn.disabled = false;
            btn.textContent = 'Сохранить организацию';
        }
    });
}

function val(id) {
    var el = document.getElementById(id);
    return el ? el.value.trim() : '';
}

function initTagInput(field, tagData) {
    var addBtn = document.getElementById(field + '_add');
    var input = document.getElementById(field + '_input');
    var container = document.getElementById(field + '_tags');
    if (!addBtn || !input || !container) return;

    function addTag() {
        var v = input.value.trim();
        if (!v || tagData[field].includes(v)) return;
        tagData[field].push(v);
        renderTags(field, tagData, container);
        input.value = '';
    }

    addBtn.addEventListener('click', addTag);
    input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); addTag(); }
    });
}

function renderTags(field, tagData, container) {
    container.innerHTML = '';
    tagData[field].forEach(function (tag, idx) {
        var span = document.createElement('span');
        span.className = 'tag';
        span.textContent = tag;
        var rm = document.createElement('span');
        rm.className = 'tag-remove';
        rm.textContent = '\u00d7';
        rm.addEventListener('click', function () {
            tagData[field].splice(idx, 1);
            renderTags(field, tagData, container);
        });
        span.appendChild(rm);
        container.appendChild(span);
    });
}

// ── Documents page ──────────────────────────────────────────

function initDocumentsPage() {
    var orgSelect = document.getElementById('org-select');
    if (!orgSelect) return;

    var typesContainer = document.getElementById('doc-types-list');
    var generateBtn = document.getElementById('generate-btn');
    var resultsDiv = document.getElementById('gen-results');
    var loadingDiv = document.getElementById('gen-loading');
    var docTypes = {};

    // Load document types
    fetch('/api/v1/documents/types', { headers: { 'Authorization': 'Bearer dev' } })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            docTypes = data;
            renderDocTypes(data, typesContainer);
        });

    // Quick select buttons
    var selectAll = document.getElementById('select-all');
    var selectPublic = document.getElementById('select-public');
    if (selectAll) {
        selectAll.addEventListener('click', function () {
            typesContainer.querySelectorAll('input[type="checkbox"]').forEach(function (cb) {
                cb.checked = true;
            });
        });
    }
    if (selectPublic) {
        selectPublic.addEventListener('click', function () {
            var pub = docTypes.public_documents || [];
            typesContainer.querySelectorAll('input[type="checkbox"]').forEach(function (cb) {
                cb.checked = pub.includes(cb.value);
            });
        });
    }

    // Generate
    if (generateBtn) {
        generateBtn.addEventListener('click', async function () {
            var orgId = orgSelect.value;
            if (!orgId) { showError('Выберите организацию'); return; }

            var selected = [];
            typesContainer.querySelectorAll('input[type="checkbox"]:checked').forEach(function (cb) {
                selected.push(cb.value);
            });
            if (selected.length === 0) { showError('Выберите хотя бы один тип документа'); return; }

            hideError();
            resultsDiv.hidden = true;
            loadingDiv.hidden = false;
            generateBtn.disabled = true;

            try {
                var res = await fetch('/api/v1/documents/generate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer dev' },
                    body: JSON.stringify({
                        organization_id: orgId,
                        doc_types: selected,
                    }),
                });
                if (!res.ok) {
                    var err = await res.json().catch(function () { return {}; });
                    throw new Error(err.detail || 'Ошибка генерации');
                }
                var data = await res.json();
                renderGenResults(data, orgId, resultsDiv);
            } catch (err) {
                showError(err.message);
            } finally {
                loadingDiv.hidden = true;
                generateBtn.disabled = false;
            }
        });
    }
}

function renderDocTypes(data, container) {
    container.innerHTML = '';
    var types = data.types || {};
    var keys = Object.keys(types);
    keys.forEach(function (key) {
        var t = types[key];
        var li = document.createElement('li');
        li.className = 'checkbox-item';
        li.innerHTML =
            '<input type="checkbox" value="' + key + '" id="dt_' + key + '">' +
            '<label for="dt_' + key + '">' +
            '<span class="checkbox-label-title">' + escapeHtml(t.title) + '</span><br>' +
            '<span class="checkbox-label-desc">' + escapeHtml(t.description) + '</span>' +
            '</label>';
        container.appendChild(li);
    });
}

function renderGenResults(data, orgId, container) {
    container.hidden = false;
    var html = '<h3>Документы сгенерированы (' + data.successful + ' из ' + data.total + ')</h3>';
    html += '<a href="/api/v1/documents/' + orgId + '/export/pdf" class="btn btn-success" style="margin:12px 0">' +
            'Скачать все (PDF)</a>';
    html += '<ul class="doc-list">';
    (data.documents || []).forEach(function (doc) {
        if (doc.error) return;
        html += '<li class="doc-item">' +
                '<div><span class="doc-title">' + escapeHtml(doc.title || doc.doc_type) + '</span>' +
                '<br><span class="doc-type">' + doc.doc_type + '</span></div>' +
                '<a href="/api/v1/documents/' + orgId + '/' + doc.doc_type + '/export/pdf" class="btn btn-sm">PDF</a> ' +
                '<a href="/api/v1/documents/' + orgId + '/' + doc.doc_type + '/export/docx" class="btn btn-sm">DOCX</a>' +
                '</li>';
    });
    html += '</ul>';
    container.innerHTML = html;
}

function escapeHtml(str) {
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(str || ''));
    return div.innerHTML;
}

// ── Init ────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', function () {
    initNav();
    initCheckPage();
    initOrgForm();
    initDocumentsPage();
});
