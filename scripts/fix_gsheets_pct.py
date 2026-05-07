#!/usr/bin/env python3
"""
fix_gsheets_pct.py — патч для исправления #ERROR! в Результаты (F,G) и Прогоны (E).

Для каждой строки RUN_005+ с #ERROR! в колонке F (эталон):
  - берёт значение эталона из листа Эталон по ключу site|item_id
  - вычисляет сравнение через compare()
  - записывает плоские строки обратно

Затем пересчитывает точность в листе Прогоны (колонка E).
"""
import sys, io, os, pathlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import gspread
from google.oauth2.credentials import Credentials
from dotenv import load_dotenv

load_dotenv()

token_path = pathlib.Path(os.getenv('GOOGLE_TOKEN_PATH', 'config/token.json'))
sheet_id   = os.getenv('GOOGLE_SHEET_ID', '1ag4869fMutJAB0zL4BZSvjdAX0G4QltRe4iLSpOrULU')
SCOPES     = ['https://www.googleapis.com/auth/spreadsheets',
              'https://www.googleapis.com/auth/drive.file']

creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
gc    = gspread.authorize(creds)
sh    = gc.open_by_key(sheet_id)

ws_runs   = sh.worksheet('Прогоны')
ws_res    = sh.worksheet('Результаты')
ws_etalon = sh.worksheet('Эталон')

print(f'Подключились к: {sh.title}')

# --- Загружаем эталон из листа ---
etalon_rows = ws_etalon.get_all_values()
sheet_etalon = {}
for row in etalon_rows[1:]:
    if len(row) >= 5 and row[1] and row[2] and row[4]:
        sheet_etalon[(row[1], row[2])] = row[4]

def get_etalon(site: str, iid: str) -> str:
    return sheet_etalon.get((site, iid), 'нет данных')

def compare(scanner_result: str, etalon: str) -> str:
    if scanner_result == 'не проверялось' or etalon in ('нет данных', ''):
        return 'НУЖНА ПРОВЕРКА'
    s, e = scanner_result, etalon
    if s == 'PASS' and e in ('Да', 'Частично(неявное)', 'Не обнаружен'): return '✓'
    if s == 'FAIL' and e in ('Нет', 'Обнаружен'):                        return '✓'
    return 'РАСХОЖДЕНИЕ'

# --- Читаем Результаты и ищем строки с #ERROR! в F ---
res_rows = ws_res.get_all_values()
res_updates = []

for i, row in enumerate(res_rows[1:], start=2):
    if not row or not row[0].startswith('RUN_'):
        continue
    f_val = row[5] if len(row) > 5 else ''
    g_val = row[6] if len(row) > 6 else ''
    if '#ERROR' not in f_val and '#ERROR' not in g_val:
        continue

    site = row[1] if len(row) > 1 else ''
    iid  = row[2] if len(row) > 2 else ''
    scanner_res = row[4] if len(row) > 4 else 'не проверялось'

    etalon_val = get_etalon(site, iid)
    comp       = compare(scanner_res, etalon_val)

    res_updates.append({'range': f'F{i}:G{i}', 'values': [[etalon_val, comp]]})

if res_updates:
    ws_res.batch_update(res_updates, value_input_option='USER_ENTERED')
    print(f'Результаты: исправлено {len(res_updates)} строк (F+G)')
else:
    print('Результаты: ошибок не найдено')

# --- Пересчитываем точность в Прогоны (колонка E) ---
# Читаем свежие данные после исправления
from collections import defaultdict
import time
time.sleep(1)  # дать Sheets обновиться

res_rows2 = ws_res.get_all_values()
ok_cnt  = defaultdict(int)
gap_cnt = defaultdict(int)
for row in res_rows2[1:]:
    if not row or not row[0]:
        continue
    run = row[0]
    val = row[6] if len(row) > 6 else ''
    if val == '✓':         ok_cnt[run]  += 1
    elif val == 'РАСХОЖДЕНИЕ': gap_cnt[run] += 1

runs_rows = ws_runs.get_all_values()
run_updates = []
for i, row in enumerate(runs_rows[1:], start=2):
    if not row or not row[0].startswith('RUN_'):
        continue
    cell_e = row[4] if len(row) > 4 else ''
    run    = row[0]
    ok  = ok_cnt[run]
    gap = gap_cnt[run]
    pct = round(ok / (ok + gap) * 100, 1) if (ok + gap) > 0 else 0
    # Обновляем если было #ERROR! или стало доступным новое значение
    if '#ERROR' in cell_e or cell_e.strip() == '':
        run_updates.append({'range': f'E{i}', 'values': [[pct]]})
        print(f'  Прогоны {run}: ✓={ok}, РАСХОЖДЕНИЕ={gap} → {pct}%')

if run_updates:
    ws_runs.batch_update(run_updates, value_input_option='USER_ENTERED')
    print(f'Прогоны: исправлено {len(run_updates)} строк (E)')
else:
    print('Прогоны: ошибок не найдено')

print('\nГотово.')
