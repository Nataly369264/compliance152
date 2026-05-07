#!/usr/bin/env python3
"""
recalc.py — пересчёт формул в scanner_test_log.xlsx через Excel (xlwings).
Если xlwings не установлен — выводит инструкцию ручного пересчёта.

Использование:
    python scripts/recalc.py scanner_test_log.xlsx
"""

import sys
import os

filepath = sys.argv[1] if len(sys.argv) > 1 else 'scanner_test_log.xlsx'
abs_path = os.path.abspath(filepath)

if not os.path.exists(abs_path):
    print(f'Файл не найден: {abs_path}')
    sys.exit(1)

try:
    import xlwings as xw
    print(f'Открываю {abs_path} для пересчёта формул...')
    app = xw.App(visible=False)
    try:
        wb = app.books.open(abs_path)
        wb.app.calculate()
        wb.save()
        wb.close()
        print(f'✓ Пересчёт выполнен: {filepath}')
    finally:
        app.quit()
except ImportError:
    print('xlwings не установлен.')
    print('Варианты пересчёта формул:')
    print()
    print('  А) Установи xlwings и повтори:')
    print('       pip install xlwings')
    print('       python scripts/recalc.py scanner_test_log.xlsx')
    print()
    print('  Б) Открой scanner_test_log.xlsx в Excel → Ctrl+Alt+F9 → Сохрани.')
    print()
    print('  В) Формулы пересчитаются автоматически при открытии файла в Excel.')
except Exception as e:
    print(f'Ошибка при пересчёте: {e}')
    sys.exit(1)
