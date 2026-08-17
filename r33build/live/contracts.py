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


SERIES_MONTH = {'H': 3, 'M': 6, 'U': 9, 'Z': 12}


def series_mismatch(name, c, row=None):
    """ИМЯ ИНСТРУМЕНТА ПРОТИВ ФАКТИЧЕСКОЙ ПОСТАВКИ (тридцатый круг, №3).

    mismatches() сверяет ответ биржи со строкой реестра, из которой сам же взял con_id:
    если строку ESU26 целиком заменить полями ESZ26 (con_id, expiry, local_symbol), всё
    сойдётся — биржа подтвердит СВОЙ контракт, реестр подтвердит СВОЮ копию, и обратное
    отображение адаптера назовёт фактический Z26 позицией ESU26. Круговое доказательство:
    смысл ИМЕНИ ни с чем не сравнивался. А именем оперирует всё остальное — календарь
    роллов, зона поставки, сверка книги.

    check_future_identity в first_connect.py защищает только ГЕНЕРАТОР реестра; при
    последующем чтении файла она не работает. Здесь имя разбирается независимо: ESU26 —
    это сентябрь 2026, и никакая согласованность строки этого не отменяет.

    Возвращает список расхождений; пустой — имя соответствует поставке.
    """
    import re
    m = re.fullmatch(r'([A-Z]{2,4})([HMUZ])(\d{2})', str(name or ''))
    if not m:
        return []                      # имя без серии (фонды, корни-шаблоны) — не наш случай
    root, letter, yy = m.group(1), m.group(2), m.group(3)
    exp = str(getattr(c, 'lastTradeDateOrContractMonth', '') or
              (row or {}).get('expiry', '') or '')
    if len(exp) < 6:
        return [f'{name}: поставка не подтверждена биржей — серию имени проверить нечем']
    try:
        y, mo = int(exp[:4]), int(exp[4:6])
    except ValueError:
        return [f'{name}: биржа вернула неразбираемую поставку {exp!r}']
    bad = []
    if SERIES_MONTH.get(letter) != mo or y % 100 != int(yy):
        _got = next((k for k, v in SERIES_MONTH.items() if v == mo), '?')
        bad.append(f'{name}: фактическая поставка {exp} — это серия {_got}{y % 100:02d}, '
                   f'а имя обещает {letter}{yy}; календарь роллов и зона поставки считаются '
                   f'ПО ИМЕНИ, поэтому расхождение означает торговлю чужой поставкой')
    # СИМВОЛ ЗДЕСЬ НЕ СВЕРЯЕТСЯ — И ЭТО ДОЛГ, А НЕ ЗАБЫВЧИВОСТЬ. Живой IBKR отдаёт в
    # Contract.symbol КОРЕНЬ ('ES'), а стенды (ib_stub) кладут туда полное имя ('ESU26').
    # Сверка символа поэтому доказывала бы фикстуру, а не контракт: на стендах она валит
    # все нормальные случаи, в бою — ничего не ловит сверх уже проверенного local_symbol.
    # Чинить надо фикстуру (она беднее и ПРОТИВОРЕЧИТ реальности), а это часть работы по
    # единому конструктору живого состояния; корень при генерации реестра уже проверяет
    # first_connect.check_future_identity. Здесь проверяется ПОСТАВКА — денежный путь
    # замечания №3 тридцатого круга.
    return bad


# НЕЗАВИСИМОЕ ОЖИДАНИЕ ЛИЧНОСТИ ФОНДА (тридцать третий круг, №4).
#
# У фьючерса имя САМО несёт поставку, и series_mismatch разбирает его независимо от строки
# реестра. У фонда независимого отображения нет: mismatches() и verify_isin() сравнивают
# ответ биржи с той же строкой CSV, из которой взят con_id. Согласованная замена строки
# CBU0 на другой USD STK — с его настоящими con_id, ISIN, local_symbol и primary_exchange —
# проходит целиком: цена чужого фонда размерит ногу Б, заявка уйдёт в него же, обратное
# отображение назовёт позицию CBU0, и сверка с брокером останется зелёной. Это вторая
# фондовая нога вместо облигационной на величину порядка NLV.
#
# ТАБЛИЦА ПУСТА И ЗАПОЛНЯЕТСЯ ПО ФАКТУ, А НЕ ВЫДУМЫВАЕТСЯ (как SHORT_SESSIONS и праздники):
# ISIN фондов — внешние данные, и придуманная константа была бы ХУЖЕ дыры, потому что
# выглядела бы проверкой. Пока ожидание не пинуется, торговля фондом запрещена — маршрут Е
# ещё не начат, и это ничего не останавливает, кроме перехода, который без независимой
# личности инструмента начинать и нельзя.
# Значения взяты не из воздуха: боевые ISIN обоих фондов зафиксированы в §12 норматива
# (пятнадцатый круг, №6 — «фикстура несёт боевые ISIN CSPX/CBU0»), листинговые линии — там
# же и в §1 (CBU0 торгуется на EBS под тикером CSBGU0). Если IBKR отдаст другую линию или
# реестр окажется подменён — это отказ, а не «уточнение таблицы».
ETF_EXPECT = {
    'CSPX': {'isin': 'IE00B5BMR087', 'primary': 'LSEETF', 'currency': 'USD'},
    'CBU0': {'isin': 'IE00B3VWN518', 'primary': 'EBS', 'currency': 'USD'},
}


def etf_expectation_bad(name, c, row):
    """Сверка фонда с ПИНОВАННЫМ ожиданием, а не со строкой реестра (33-й круг, №4).

    ОЖИДАНИЕ ПРИВЯЗАНО К ИМЕНИ, А НЕ К КЛАССУ ИЗ СТРОКИ (тридцать четвёртый круг, №2).
    Прежде проверка немедленно выходила при sec_type != 'STK' — то есть строку CBU0 можно
    было согласованно заменить на FUT/CFD вместе с настоящими con_id и полями: mismatches
    подтвердит строку ею же, series_mismatch имени фонда не разбирает, verify_isin у FUT
    ISIN не требует. step_e размерил бы «доли CBU0» по цене фьючерса, а адаптер отправил
    бы десятки тысяч контрактов вместо долей облигационного ETF. Имя из ETF_EXPECT — это
    заявка на то, ЧЕМ инструмент обязан быть, включая класс.
    """
    exp0 = ETF_EXPECT.get(str(name))
    if exp0 is None and (row or {}).get('sec_type') != 'STK':
        return []
    if exp0 is not None:
        _st = str((row or {}).get('sec_type') or '')
        _stc = str(getattr(c, 'secType', '') or '')
        if _st and _st != 'STK':
            return [f'{name}: пинован как фонд (STK), а реестр объявляет класс {_st} — '
                    f'согласованная подмена класса инструмента; торговля запрещена']
        if _stc and _stc != 'STK':
            return [f'{name}: пинован как фонд (STK), а биржа отвечает классом {_stc} — '
                    f'торговля запрещена']
    exp = exp0
    if not exp:
        return [f'{name}: независимого ожидания личности фонда нет (contracts.ETF_EXPECT '
                f'не пинован) — строка реестра доказывала бы сама себя; торговля фондом '
                f'запрещена до пиновки ISIN и площадки по факту']
    bad = []
    want_isin = str(exp.get('isin') or '')
    row_isin = str((row or {}).get('isin') or '')
    if want_isin and row_isin and row_isin != want_isin:
        bad.append(f'{name}: ISIN реестра {row_isin} не совпадает с пинованным {want_isin}')
    _prim = str(getattr(c, 'primaryExchange', '') or '')
    if exp.get('primary') and _prim and _prim != exp['primary']:
        bad.append(f'{name}: листинговая линия {_prim} не совпадает с пинованной '
                   f'{exp["primary"]}')
    _cur = str(getattr(c, 'currency', '') or '')
    if exp.get('currency') and _cur and _cur != exp['currency']:
        bad.append(f'{name}: валюта {_cur} не совпадает с пинованной {exp["currency"]}')
    return bad


def identity_bad(ib, name, c, row):
    """ПОЛНЫЙ разбор личности контракта — ОДНОЙ функцией (тридцать первый круг, №8).

    Проверок три, и они разные по смыслу: mismatches сверяет ответ биржи со строкой
    реестра, series_mismatch — смысл ИМЕНИ с фактической поставкой, verify_isin — личность
    фонда. Пока каждый вызывающий собирал их сам, набор разошёлся: feed.contract_of звал
    все три, а ib_broker._contract — только первую и третью, то есть согласованная подмена
    серии проходила ровно там, где подаётся заявка. Единая точка вызова делает такое
    расхождение невозможным по построению и даёт защите парную мутацию.

    Возвращает список расхождений; пустой — личность подтверждена.
    """
    return (mismatches(c, row) + series_mismatch(name, c, row)
            + etf_expectation_bad(name, c, row) + verify_isin(ib, c, row))


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
