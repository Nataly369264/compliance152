#!/usr/bin/env python3
"""
update_gsheets.py — добавляет прогон из golden run JSON в Google Sheets.

Использование:
    python scripts/update_gsheets.py <golden_run.json>

Требует:
    config/token.json      — OAuth-токен (создаётся scripts/auth_google.py)
    config/credentials.json — OAuth-ключи
    .env                   — GOOGLE_SHEET_ID=...
"""

import sys, io, json, pathlib, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import gspread
from google.oauth2.credentials import Credentials
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# =============================================
# КОНСТАНТЫ: маппинг эталон 1-37 → CHECK_ID
# =============================================

CHECKLIST_MAP = [
    ('1',  'Согласие на обработку ПД в форме',          'FORM_001',   False),
    ('2',  'Чекбокс согласия не предотмечен',           'FORM_002',   False),
    ('3',  'Ссылка на политику рядом с формой',         'FORM_003',   False),
    ('4',  'Отдельный чекбокс для маркетинга',          'FORM_006',   False),
    ('5',  'Минимальность собираемых данных',           'FORM_007',   False),
    ('6',  'Cookie-баннер присутствует',                'COOKIE_001', False),
    ('7',  'Кнопка «Отклонить» в баннере',              'COOKIE_002', False),
    ('8',  'Выбор категорий cookie',                    'COOKIE_003', False),
    ('9',  'Аналитика не грузится до согласия',         'COOKIE_005', False),
    ('10', 'Политика опубликована',                     'POLICY_001', False),
    ('11', 'Ссылка на политику в футере',               'POLICY_002', False),
    ('12', 'Полное наименование оператора',             'POLICY_003', False),
    ('13', 'ИНН / ОГРН оператора',                     'POLICY_004', False),
    ('14', 'Контакт ответственного за ПДн',             'POLICY_005', False),
    ('15', 'Категории персональных данных',             'POLICY_006', False),
    ('16', 'Цели обработки',                            'POLICY_007', False),
    ('17', 'Правовые основания обработки',              'POLICY_008', False),
    ('18', 'Сроки хранения данных',                     'POLICY_009', False),
    ('19', 'Права субъектов ПДн',                      'POLICY_010', False),
    ('20', 'Порядок реализации прав (10 р. дней)',      'POLICY_011', False),
    ('21', 'Информация о трансграничной передаче',      'POLICY_012', False),
    ('22', 'Описание мер безопасности',                 'POLICY_013', False),
    ('23', 'Информация о cookies в политике',           'POLICY_014', False),
    ('24', 'Локализация данных в РФ',                   'POLICY_015', False),
    ('25', 'Дата публикации и обновления',              'POLICY_016', False),
    ('26', 'Документ на русском языке',                 'POLICY_017', False),
    ('27', 'SSL / HTTPS',                               'TECH_001',   False),
    ('28', 'Google Fonts',                              'TECH_002',   True),
    ('29', 'Google Analytics',                          'TECH_003',   True),
    ('30', 'Facebook Pixel / VK Pixel',                 'TECH_004',   True),
    ('31', 'Google reCAPTCHA',                          'TECH_005',   True),
    ('32', 'Google Tag Manager',                        'TECH_006',   True),
    ('33', 'Трекеры упомянуты в политике ПДн',         'TRACKER_001',False),
    ('34', 'Трансграничная передача раскрыта',          None,         False),
    ('35', 'Оператор в реестре РКН',                    'REG_001',    False),
    ('36', 'Уведомление подано в РКН',                  'REG_002',    False),
    ('37', 'Хостинг на территории РФ',                  'REG_003',    False),
]

EXTRA_CHECKS = ['CONSENT_001','CONSENT_002','CONSENT_003','CONSENT_004','CONSENT_005','TRACKER_002']
EXTRA_NAMES  = {
    'CONSENT_001': 'Наличие согласия на обработку ПДн',
    'CONSENT_002': 'Согласие отделено от оферты',
    'CONSENT_003': 'Обязательные реквизиты в тексте согласия',
    'CONSENT_004': 'Возможность отзыва согласия',
    'CONSENT_005': 'Раздельные согласия для разных целей',
    'TRACKER_002': 'Трекер без согласия пользователя',
}

# =============================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =============================================

def normalize_ods_value(raw: str, item_id: str) -> str:
    r = raw.strip()
    if item_id in ('28','29','30','31','32'):
        if '✅' in r: return 'Обнаружен'
        if '❌' in r: return 'Не обнаружен'
        return r or 'нет данных'
    if '✅' in r:                             return 'Да'
    if '❌' in r:                             return 'Нет'
    if '⚠' in r:                             return 'Частично(неявное)'
    if r in ('Предотмечен',):                 return 'Нет'
    if r in ('Грузится',):                    return 'Нет'
    if r.startswith('Не указан'):             return 'Нет'
    if r.startswith('Указано, что не осуществляется'): return 'Частично(неявное)'
    return r or 'нет данных'


def scanner_to_result(status: str) -> str:
    s = status.lower()
    if s == 'pass':               return 'PASS'
    if s in ('fail', 'warning'):  return 'FAIL'
    return 'не проверялось'


def compare(scanner_result: str, etalon: str) -> str:
    if scanner_result == 'не проверялось' or etalon in ('нет данных', ''):
        return 'НУЖНА ПРОВЕРКА'
    s, e = scanner_result, etalon
    if s == 'PASS' and e in ('Да', 'Частично(неявное)', 'Не обнаружен'): return '✓'
    if s == 'FAIL' and e in ('Нет', 'Обнаружен'):                        return '✓'
    return 'РАСХОЖДЕНИЕ'


def classify_gap(scanner_result: str, etalon: str):
    s, e = scanner_result, etalon
    if s == 'PASS' and e in ('Нет', 'Обнаружен'):             return 'false negative', 'критично'
    if s == 'FAIL' and e in ('Да','Частично(неявное)','Не обнаружен'): return 'false positive', 'важно'
    return 'расхождение', 'важно'


def gap_hypothesis(gap_type: str, item_name: str) -> str:
    if gap_type == 'false negative':
        return (f'Сканер не распознаёт признак нарушения для пункта «{item_name}». '
                'Возможно: неверный селектор, логика проверки не покрывает этот кейс.')
    if gap_type == 'false positive':
        return (f'Сканер ложно срабатывает на пункте «{item_name}». '
                'Возможно: слишком широкое условие, неверная интерпретация элемента.')
    return 'Требует ручного анализа.'




# =============================================
# ЧИТАЕМ АРГУМЕНТЫ
# =============================================

args = sys.argv[1:]
if not args:
    print('Использование: python scripts/update_gsheets.py <golden_run.json>')
    sys.exit(1)

golden_path = pathlib.Path(args[0])
ods_path    = pathlib.Path('tests/fixtures/golden_set_v1.ods')
token_path  = pathlib.Path(os.getenv('GOOGLE_TOKEN_PATH', 'config/token.json'))
creds_path  = pathlib.Path(os.getenv('GOOGLE_CREDENTIALS_PATH', 'config/credentials.json'))
sheet_id    = os.getenv('GOOGLE_SHEET_ID', '1ag4869fMutJAB0zL4BZSvjdAX0G4QltRe4iLSpOrULU')

# =============================================
# ЧИТАЕМ GOLDEN RUN JSON
# =============================================

data    = json.loads(golden_path.read_text(encoding='utf-8'))
ri      = data.get('run_info', {})
report  = data['compliance_report']

site_url    = ri.get('url', '').replace('https://','').replace('http://','').rstrip('/')
score       = report.get('overall_score', 0)
scanner_ver = ri.get('scanner', 'SiteScanner')
run_at_str  = ri.get('run_at', '')

try:
    run_dt = datetime.fromisoformat(run_at_str.replace('Z','+00:00'))
    dt_str = run_dt.strftime('%Y-%m-%d %H:%M')
except Exception:
    dt_str = run_at_str[:16]

checklist_raw   = {item['id']: item['status'] for item in report.get('checklist', [])}
scanner_results = {cid: scanner_to_result(st) for cid, st in checklist_raw.items()}

# =============================================
# ЧИТАЕМ ODS-ЭТАЛОН (el-ed.ru — fallback для новых сайтов)
# =============================================

ods_etalon = {}
ods_date   = '2025-04-27'
ods_author = 'Наталья'

try:
    import odf.opendocument as oo, odf.table as ot, odf.text as ox
    doc  = oo.load(str(ods_path))
    sh_o = doc.spreadsheet.getElementsByType(ot.Table)[0]
    rows_o = sh_o.getElementsByType(ot.TableRow)
    def cell_val(c):
        ps = c.getElementsByType(ox.P)
        return ps[0].firstChild.data if ps and ps[0].firstChild else ''
    for row in rows_o:
        cells = row.getElementsByType(ot.TableCell)
        vals  = [cell_val(c) for c in cells]
        if len(vals) >= 5 and vals[1].strip().isdigit():
            iid = vals[1].strip()
            ods_etalon[iid] = normalize_ods_value(vals[4].strip() if len(vals) > 4 else '', iid)
except Exception as e:
    print(f'⚠  ODS не прочитан: {e}')

# =============================================
# ПОДКЛЮЧАЕМСЯ К GOOGLE SHEETS
# =============================================

SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive.file']
creds  = Credentials.from_authorized_user_file(str(token_path), SCOPES)
gc     = gspread.authorize(creds)
sh     = gc.open_by_key(sheet_id)

ws_runs   = sh.worksheet('Прогоны')
ws_res    = sh.worksheet('Результаты')
ws_tasks  = sh.worksheet('Задачи')
ws_etalon = sh.worksheet('Эталон')

print(f'Подключились к: {sh.title}')

# =============================================
# ЧИТАЕМ СУЩЕСТВУЮЩИЕ ДАННЫЕ ИЗ ТАБЛИЦЫ
# =============================================

etalon_rows  = ws_etalon.get_all_values()
existing_keys = set()
sheet_etalon  = {}
for row in etalon_rows[1:]:
    if row and row[0]:
        existing_keys.add(row[0])
    if len(row) >= 5 and row[1] and row[2] and row[4]:
        sheet_etalon[(row[1], row[2])] = row[4]

def get_etalon(site: str, iid: str) -> str:
    return sheet_etalon.get((site, iid), ods_etalon.get(iid, 'нет данных'))

# Следующий run_id
runs_rows = ws_runs.get_all_values()
max_run = max(
    (int(r[0][4:]) for r in runs_rows[1:] if r and r[0].startswith('RUN_')),
    default=0
)
run_id = f'RUN_{max_run + 1:03d}'

# Следующий task_id
tasks_rows = ws_tasks.get_all_values()
max_task = max(
    (int(r[0][4:]) for r in tasks_rows[1:] if r and r[0].startswith('BUG_')),
    default=0
)
task_id_base = max_task + 1

open_tasks = set()
for r in tasks_rows[1:]:
    if len(r) >= 10 and r[9] == 'открыта' and r[2] and r[3]:
        open_tasks.add((r[2], r[3]))

# =============================================
# ШАГ 1: Обновляем Эталон (новые строки если нужно)
# =============================================

new_etalon = []
for iid, iname, cid, _ in CHECKLIST_MAP:
    key = f'{site_url}|{iid}'
    if key not in existing_keys:
        val = ods_etalon.get(iid, 'нет данных')
        new_etalon.append([key, site_url, iid, iname, val, ods_date, ods_author])
        sheet_etalon[(site_url, iid)] = val
        existing_keys.add(key)

if new_etalon:
    ws_etalon.append_rows(new_etalon, value_input_option='USER_ENTERED')
    print(f'  Эталон: добавлено {len(new_etalon)} строк для {site_url}')

# =============================================
# ШАГ 2: Добавляем строку в Прогоны
# =============================================

# Считаем точность в Python (избегаем зависимости от локали Google Sheets)
_pre_comps = [compare(
    scanner_results.get(cid, 'не проверялось') if cid else 'не проверялось',
    get_etalon(site_url, iid)
) for iid, _, cid, _ in CHECKLIST_MAP]
_ok  = _pre_comps.count('✓')
_gap = _pre_comps.count('РАСХОЖДЕНИЕ')
pct_val = round(_ok / (_ok + _gap) * 100, 1) if (_ok + _gap) > 0 else 0

ws_runs.append_row([
    run_id, dt_str, site_url, f'{score}%',
    pct_val, '—', scanner_ver,
    f'golden run {golden_path.name}'
], value_input_option='USER_ENTERED')

# =============================================
# ШАГ 3: Добавляем строки в Результаты
# =============================================

res_rows  = ws_res.get_all_values()
rn        = len(res_rows) + 1
расхождения = []
new_res   = []

for iid, iname, cid, _ in CHECKLIST_MAP:
    scanner_res = scanner_results.get(cid, 'не проверялось') if cid else 'не проверялось'
    etalon_val  = get_etalon(site_url, iid)
    comp        = compare(scanner_res, etalon_val)
    disc_type, severity, comment = '', '', ''

    if comp == 'РАСХОЖДЕНИЕ':
        disc_type, severity = classify_gap(scanner_res, etalon_val)
        comment = f'Сканер: {scanner_res}, эталон: {etalon_val}'
        расхождения.append((iid, iname, disc_type, severity, scanner_res, etalon_val))

    new_res.append([
        run_id, site_url, iid, iname, scanner_res,
        etalon_val, comp,
        disc_type, severity, comment
    ])
    rn += 1

for cid in EXTRA_CHECKS:
    if cid in scanner_results:
        scanner_res = scanner_results[cid]
        iname       = EXTRA_NAMES.get(cid, cid)
        etalon_val  = get_etalon(site_url, cid)
        comp        = compare(scanner_res, etalon_val)
        disc_type   = 'новая находка' if scanner_res == 'FAIL' else ''
        severity    = 'минор' if scanner_res == 'FAIL' else ''
        new_res.append([
            run_id, site_url, cid, iname, scanner_res,
            etalon_val, comp,
            disc_type, severity,
            'Пункт отсутствует в эталоне 1–37, требует ручной верификации' if disc_type else ''
        ])
        rn += 1

ws_res.append_rows(new_res, value_input_option='USER_ENTERED')

# =============================================
# ШАГ 4: Создаём задачи для расхождений
# =============================================

today_str  = datetime.now().strftime('%Y-%m-%d')
new_tasks  = 0
skipped    = 0
new_task_rows = []

for iid, iname, disc_type, severity, scanner_res, etalon_val in расхождения:
    key = (site_url, iid)
    if key in open_tasks:
        skipped += 1
        continue
    tid  = f'BUG_{task_id_base:03d}'
    task_id_base += 1
    desc = (f'Сканер выдал {scanner_res}, эталон ожидает {etalon_val}. '
            f'Пункт: {iname}. Сайт: {site_url}')
    hyp  = gap_hypothesis(disc_type, iname)
    new_task_rows.append([
        tid, run_id, site_url, iid, iname,
        disc_type, severity, desc, hyp,
        'открыта', today_str, '', ''
    ])
    open_tasks.add(key)
    new_tasks += 1

if new_task_rows:
    ws_tasks.append_rows(new_task_rows, value_input_option='USER_ENTERED')

# =============================================
# ИТОГОВАЯ СВОДКА
# =============================================

all_comps = [compare(
    scanner_results.get(cid, 'не проверялось') if cid else 'не проверялось',
    get_etalon(site_url, iid)
) for iid, _, cid, _ in CHECKLIST_MAP]

cnt_ok    = all_comps.count('✓')
cnt_gap   = all_comps.count('РАСХОЖДЕНИЕ')
cnt_check = all_comps.count('НУЖНА ПРОВЕРКА')

# Считаем открытые задачи из Google Sheets
all_tasks = ws_tasks.get_all_values()
total_open = sum(1 for r in all_tasks[1:] if len(r) >= 10 and r[9] == 'открыта')
crit_open  = sum(1 for r in all_tasks[1:] if len(r) >= 10 and r[9] == 'открыта' and r[6] == 'критично')
imp_open   = sum(1 for r in all_tasks[1:] if len(r) >= 10 and r[9] == 'открыта' and r[6] == 'важно')

print()
print(f'✓ Прогон {run_id} записан в Google Sheets')
print(f'  Сайт: {site_url}   Score: {score}%   Дата: {dt_str}')
print()
print(f'  Результаты по эталону (37 пунктов):')
print(f'    ✓  совпадений:         {cnt_ok}')
print(f'    РАСХОЖДЕНИЕ:           {cnt_gap}')
print(f'    НУЖНА ПРОВЕРКА:        {cnt_check}')
if cnt_ok + cnt_gap > 0:
    print(f'    Точность (✓/сравн.):  {round(cnt_ok/(cnt_ok+cnt_gap)*100,1)}%')
print()
print(f'  Задачи:')
print(f'    Создано новых:         {new_tasks}')
print(f'    Пропущено (дубли):     {skipped}')
print(f'    Всего открытых:        {total_open} '
      f'(🔴 критичных: {crit_open}, 🟡 важных: {imp_open})')

if расхождения:
    print()
    print('  Расхождения:')
    for iid, iname, disc_type, severity, sr, ev in расхождения:
        icon = '🔴' if severity == 'критично' else '🟡'
        print(f'    {icon} [{iid}] {iname}')
        print(f'       сканер={sr}, эталон={ev} → {disc_type}')

print()
print(f'  Таблица: https://docs.google.com/spreadsheets/d/{sheet_id}')
