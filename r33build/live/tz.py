#!/usr/bin/env python3
"""Устаревшие имена часовых зон, которые присылает IBKR.

IBKR помечает время исполнения зоной вида 'US/Central'. В tzdata Ubuntu 24.04 такие
ссылки вынесены в отдельный пакет tzdata-legacy, которого в системе нет, и разбор отчёта
об исполнении падал ZoneInfoNotFoundError — подтверждение СОСТОЯВШЕЙСЯ сделки терялось, а
контур считал исход неизвестным и шёл восстанавливать книгу. Дефект проявляется только на
живом брокере и ни одним модельным тестом не ловится.

Своя база зон лежит вне каталога кода (~/.addfut/zoneinfo): она относится к машине, а не
к копии пакета. Модуль импортируют И адаптер брокера, И сборщик входов — обоим достаётся
разбор дат, и забыть подключение нельзя.
"""
import os
import zoneinfo
from pathlib import Path

LEGACY = {'US/Central': 'America/Chicago', 'US/Eastern': 'America/New_York',
          'US/Pacific': 'America/Los_Angeles', 'US/Mountain': 'America/Denver',
          'US/Arizona': 'America/Phoenix', 'US/Alaska': 'America/Anchorage',
          'US/Hawaii': 'Pacific/Honolulu', 'Japan': 'Asia/Tokyo',
          'Hongkong': 'Asia/Hong_Kong', 'Singapore': 'Asia/Singapore',
          'GB': 'Europe/London', 'GB-Eire': 'Europe/London'}
DIR = Path(os.path.expanduser('~/.addfut/zoneinfo'))


def install():
    """Создать недостающие зоны копией из системной базы и подключить каталог."""
    made = []
    for legacy, real in LEGACY.items():
        src = Path('/usr/share/zoneinfo') / real
        dst = DIR / legacy
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes()); made.append(legacy)
    if DIR.is_dir() and str(DIR) not in zoneinfo.TZPATH:
        zoneinfo.reset_tzpath([str(DIR)] + list(zoneinfo.TZPATH))
    return made


def missing():
    """Проверка ДО подключения: иначе дефект виден только на первой сделке."""
    out = []
    for k in LEGACY:
        try:
            zoneinfo.ZoneInfo(k)
        except Exception:
            out.append(k)
    return out


install()

if __name__ == '__main__':
    m = missing()
    print('зоны на месте' if not m else f'НЕТ ЗОН: {m}')
    raise SystemExit(1 if m else 0)
