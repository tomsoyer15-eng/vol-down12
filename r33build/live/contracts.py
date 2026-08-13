#!/usr/bin/env python3
"""Сверка личности контракта — ОДНА на обе программы.

Торговый адаптер сверял поставку, множитель и биржевое имя, а сборщик входов брал контракт
по con_id без единой проверки. Между тем именно сборщик считает ДЕНЕЖНЫЙ вход расчёта: если
в реестре имя ESZ26 ошибочно связано с валидным con_id другой серии, размер книги, причины,
ориентиры и ролловая логика строятся по чужому активу, а отказ адаптера приходит позже — на
подаче заявки, когда решение уже принято.
"""


def mismatches(c, row):
    """Чем описание биржи расходится с реестром. Пустой список — совпало."""
    bad = []
    if not getattr(c, 'conId', 0):
        bad.append('con_id не подтверждён биржей')
        return bad
    if row.get('sec_type') and c.secType != row['sec_type']:
        bad.append(f"класс {c.secType} вместо {row['sec_type']}")
    if row.get('currency') and c.currency != row['currency']:
        bad.append(f"валюта {c.currency} вместо {row['currency']}")
    if row.get('expiry') and (c.lastTradeDateOrContractMonth or '') != row['expiry']:
        bad.append(f"поставка {c.lastTradeDateOrContractMonth} вместо {row['expiry']}")
    if row.get('multiplier') and str(c.multiplier or '') != str(row['multiplier']):
        bad.append(f"множитель {c.multiplier} вместо {row['multiplier']}")
    if row.get('primary_exchange'):
        got = c.primaryExchange or c.exchange
        if got and got != row['primary_exchange']:
            bad.append(f"площадка {got} вместо {row['primary_exchange']}")
    if row.get('local_symbol') and c.localSymbol != row['local_symbol']:
        bad.append(f"биржевое имя {c.localSymbol} вместо {row['local_symbol']}")
    return bad
