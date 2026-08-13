#!/usr/bin/env python3
"""Сверка личности контракта — ОДНА на обе программы.

Торговый адаптер сверял поставку, множитель и биржевое имя, а сборщик входов брал контракт
по con_id без единой проверки. Между тем именно сборщик считает ДЕНЕЖНЫЙ вход расчёта: если
в реестре имя ESZ26 ошибочно связано с валидным con_id другой серии, размер книги, причины,
ориентиры и ролловая логика строятся по чужому активу, а отказ адаптера приходит позже — на
подаче заявки, когда решение уже принято.
"""


_ISIN_OK = set()          # con_id, чей ISIN уже подтверждён в этой сессии процесса


def verify_isin(ib, c, row):
    """ISIN сверяется И ПРИ ТОРГОВЛЕ (четырнадцатый круг, №6): реестр мог устареть или быть
    подменён — conId другой листинговой линии с тем же тикером прошёл бы все полевые
    проверки. Дорогая (reqContractDetails) проверка кэшируется на процесс."""
    want = (row.get('isin') or '').strip()
    if not want:
        # ПУСТОЙ ISIN У ФОНДА — ОТКАЗ (пятнадцатый круг, №6): тикер+валюта+площадка не
        # различают листинговые линии одного фонда; строка STK без ISIN — повреждённый или
        # усечённый реестр, а не «проверка не нужна». Фьючерсам ISIN не положен.
        if (row.get('sec_type') or '') == 'STK':
            return [f'в реестре нет ISIN для {row.get("symbol", "?")} — торговля фондом '
                    f'без сверки личности запрещена; перегенерировать реестр first_connect']
        return []
    if c.conId in _ISIN_OK:
        return []
    try:
        det = ib.reqContractDetails(c)
    except Exception as ex:
        return [f'ISIN не проверен: reqContractDetails недоступен ({ex})']
    got = ''
    for d in det or []:
        for x in getattr(d, 'secIdList', None) or []:
            if getattr(x, 'tag', '') == 'ISIN':
                got = getattr(x, 'value', '')
    if not got:
        return [f'ISIN биржей не подтверждён (ожидался {want})']
    if got != want:
        return [f'ISIN {got} вместо {want} — другая листинговая линия']
    _ISIN_OK.add(c.conId)
    return []


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
        if not got:
            bad.append(f"площадка биржей не подтверждена (ожидалась {row['primary_exchange']})")
        elif got != row['primary_exchange']:
            bad.append(f"площадка {got} вместо {row['primary_exchange']}")
    if row.get('local_symbol') and c.localSymbol != row['local_symbol']:
        bad.append(f"биржевое имя {c.localSymbol} вместо {row['local_symbol']}")
    return bad
