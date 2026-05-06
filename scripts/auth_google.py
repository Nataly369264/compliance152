#!/usr/bin/env python3
"""
auth_google.py — однократная авторизация через Google OAuth.
Запусти один раз: python scripts/auth_google.py
Откроется браузер → войди в Google → токен сохранится в config/token.json.
После этого все скрипты будут работать без браузера.
"""
import sys, io, pathlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from google_auth_oauthlib.flow import InstalledAppFlow
import json

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive.file',
]

CREDS_PATH = pathlib.Path('config/credentials.json')
TOKEN_PATH = pathlib.Path('config/token.json')

if not CREDS_PATH.exists():
    print(f'Файл не найден: {CREDS_PATH}')
    print('Положи credentials.json в папку config/')
    sys.exit(1)

print('Открываю браузер для авторизации...')
flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), SCOPES)
creds = flow.run_local_server(port=0, open_browser=True)

TOKEN_PATH.write_text(creds.to_json(), encoding='utf-8')
print(f'✓ Токен сохранён: {TOKEN_PATH}')
print('Теперь можно запускать скрипты записи в Google Sheets.')
