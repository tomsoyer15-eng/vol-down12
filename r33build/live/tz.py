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
        if not src.exists():
            continue
        # «ФАЙЛ ЕСТЬ» НЕ ЗНАЧИТ «ФАЙЛ ЦЕЛ» (27.08.2026, находка №32 сплошного аудита).
        # Условие было `not dst.exists()`, а запись шла прямо в целевой файл. Обрыв на
        # записи — нехватка места, снятие процесса, отказ питания — оставлял ОБРЕЗАННУЮ
        # зону, и с этого мгновения дефект становился ВЕЧНЫМ: файл существует, значит
        # больше не переписывается никогда. Дальше одно из двух, и оба плохи: либо разбор
        # отчёта об исполнении падает и состоявшаяся сделка считается неизвестной, либо
        # обрезок разбирается и даёт ДРУГОЕ смещение — время исполнения уезжает на часы,
        # а торговое окно и замыкание считаются по Чикаго.
        # Лечится двумя независимыми ходами: (1) целостность проверяется СРАВНЕНИЕМ с
        # источником, а не фактом существования; (2) запись атомарна, поэтому обрезок не
        # может появиться вовсе — в каталоге либо старый файл, либо новый целиком.
        _нужно = src.read_bytes()
        try:
            _есть = dst.read_bytes()
        except OSError:
            _есть = None
        if _есть == _нужно:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        _tmp = dst.with_name(dst.name + '.tmp')
        try:
            _tmp.write_bytes(_нужно)
            os.replace(_tmp, dst)
        finally:
            # Хвост от неудачной попытки не оставляем: он не мешает работе, но копится и
            # путает разбор каталога.
            if _tmp.exists():
                _tmp.unlink()
        made.append(legacy if _есть is None else legacy + ' (был повреждён, переписан)')
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
