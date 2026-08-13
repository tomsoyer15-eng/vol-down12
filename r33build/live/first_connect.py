#!/usr/bin/env python3
"""Первое подключение к бумажному счёту: снять всё, что можно снять без торговли.

ЧТО ДЕЛАЕТ. Заполняет реестр инструментов фактическими con_id для ТЕКУЩЕЙ И СЛЕДУЮЩЕЙ
серии каждой ноги, снимает фактические маржинальные требования на контракт предпросмотром
заявки (whatIf), проверяет права на инструменты и наличие задержанных данных. Ничего не
торгует: API стоит в режиме только чтения, а whatIf по определению не создаёт заявку.

ПОЧЕМУ НЕ ПРОСИМ ЭТО У БРОКЕРСКОГО СПЕЦИАЛИСТА. Всё перечисленное машина берёт точнее и
быстрее человека, а con_id меняются каждый квартал: запрашивать их письмом означало бы
повторять переписку четыре раза в год.

Учётные данные берутся ИЗ ОКРУЖЕНИЯ и никуда не записываются.
"""
import os, sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOST = os.environ.get('IB_HOST', '127.0.0.1')
PORT = int(os.environ.get('IB_PORT', '4002'))          # 4002 — бумажный счёт
CLIENT_ID = int(os.environ.get('IB_CLIENT_ID', '17'))

ROOTS = [('ES', 'CME', 'USD'), ('MES', 'CME', 'USD'), ('ZN', 'CBOT', 'USD')]
# ИМЕНА ФОНДОВ ПРОВЕРЕНЫ НА СЧЁТЕ 12.08.2026. В §8а нога Б маршрута Е названа «CBU0», но
# такого тикера на LSE не существует: это CSBGU0 (ISIN IE00B3VWN518), торгуемый на EBS —
# швейцарской бирже. Прежний реестр не находил контракт вовсе, и маршрут Е был неисполним
# на первом же шаге. Права на оба фонда подтверждены пробной сделкой с закрытием.
# Маршрутизация SMART, но ОСНОВНАЯ БИРЖА УКАЗЫВАЕТСЯ ЯВНО: без неё определение контракта
# не разрешается вовсе (один тикер живёт на нескольких площадках в разных валютах).
# ПЕРВОЕ ПОЛЕ — НАШЕ ИМЯ (как в §8а и в коде), ВТОРОЕ — БИРЖЕВОЙ ТИКЕР. Перевод между ними
# живёт ЗДЕСЬ и только здесь: иначе спецификация говорит «CBU0», биржа знает «CSBGU0», и
# расхождение расползается по коду. Ровно так маршрут Е и оказался неисполним.
ETFS = [('CSPX', 'CSPX', 'SMART', 'LSEETF', 'USD', 'IE00B5BMR087', 'EQ'),
        ('CBU0', 'CSBGU0', 'SMART', 'EBS', 'USD', 'IE00B3VWN518', 'BOND')]


def next_two_expiries(today):
    """Текущая и следующая квартальная поставка по правилу §1 (в месяц поставки не входим).

    ПРАЗДНИКИ БИРЖИ УЧИТЫВАЮТСЯ. Дата ролла считается назад от последнего рабочего дня
    месяца, поэтому праздник последней недели её сдвигает: в ноябре 2026 День благодарения
    переносит ролл с 25 на 24 ноября. Без праздников 25 ноября функция считала бы, что ролл
    ещё не прошёл, и построила бы реестр от УХОДЯЩЕЙ серии — то есть подать заявку в нужный
    контракт стало бы нечем. Защита была введена в сборщике входов, а генератор реестра её
    обходил.
    """
    import daily as DL
    try:
        hol = DL.holidays_for(today.year, today.year + 1)
    except RuntimeError:
        # Последний покрытый год не блокируется требованием следующего (десятый круг, №10).
        hol = DL.holidays_for(today.year)
    t1 = DL.first_tag(today)
    if DL.roll_passed_for(today, hol):
        t1 = DL.next_tag(t1)
    return [t1, DL.next_tag(t1)]


def tag_from_expiry(expiry):
    """Тег серии из биржевой поставки: '20260918' -> 'U26'."""
    mm = {3: 'H', 6: 'M', 9: 'U', 12: 'Z'}
    y, m = int(expiry[:4]), int(expiry[4:6])
    return f'{mm[m]}{y % 100}' if m in mm else None


def tag_to_yyyymm(tag):
    import daily as DL
    m, y = DL.tag_month(tag)
    return f'{y}{m:02d}'


def main():
    sys.path.insert(0, str(ROOT / 'live')); sys.path.insert(0, str(ROOT / 'r33build'))
    sys.path.insert(0, str(ROOT / 'r33build' / 'live'))
    from ib_insync import IB, Future, Stock, MarketOrder
    import pandas as pd

    ib = IB()
    ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=30)
    print(f'подключено: {HOST}:{PORT}, счёт {ib.managedAccounts()}')
    ib.reqMarketDataType(3)                 # 3 — задержанные данные, платных подписок не нужно

    import feed as FD
    today = FD.exchange_today()      # дата БИРЖИ, а не часов машины
    tags = next_two_expiries(today)
    # УДЕРЖИВАЕМЫЕ СЕРИИ ВХОДЯТ ВСЕГДА (девятая рецензия, №13): обновление реестра после
    # календарного ролла выбрасывало серию, в которой ещё лежит отложенная позиция, — её
    # становилось нельзя ни сверить, ни закрыть через адаптер.
    ib.reqPositions(); import time as _t; _t.sleep(2)
    for pos in ib.positions():
        exp = getattr(pos.contract, 'lastTradeDateOrContractMonth', '') or ''
        t = tag_from_expiry(exp) if len(exp) >= 6 else None
        if t and t not in tags and pos.position:
            tags.append(t)
            print(f'  удерживаемая серия {t} добавлена в реестр (позиция {pos.position:+.0f})')
    print(f'серии к запросу: {tags}\n')

    rows = []
    for root, exch, cur in ROOTS:
        for tag in tags:
            c = Future(symbol=root, exchange=exch, currency=cur,
                       lastTradeDateOrContractMonth=tag_to_yyyymm(tag))
            det = ib.reqContractDetails(c)
            if not det:
                print(f'  {root}{tag}: КОНТРАКТ НЕ НАЙДЕН — проверить права или месяц поставки')
                continue
            d = det[0]
            rows.append(dict(instrument=f'{root}{tag}', sec_type=d.contract.secType,
                             pair_group='EQ' if root in ('ES', 'MES') else 'BOND',
                             exchange=exch, currency=cur, con_id=d.contract.conId,
                             local_symbol=d.contract.localSymbol,
                             expiry=d.contract.lastTradeDateOrContractMonth,
                             multiplier=d.contract.multiplier,
                             primary_exchange=d.contract.primaryExchange or exch, isin=''))
            print(f'  {root}{tag}: con_id {d.contract.conId}, {d.contract.localSymbol}, '
                  f'экспирация {d.contract.lastTradeDateOrContractMonth}')
    for sym, ticker, exch, prim, cur, isin, grp in ETFS:
        c = Stock(ticker, exch, cur, primaryExchange=prim)
        det = ib.reqContractDetails(c)
        if not det:
            print(f'  {sym}: КОНТРАКТ НЕ НАЙДЕН — проверить права на {exch}')
            continue
        d = det[0]
        # ISIN ПОДТВЕРЖДАЕТСЯ ИЗ БИРЖИ (двенадцатый круг, №7): константа — лишь ожидание.
        _ids = {x.tagValue2 if hasattr(x, 'tagValue2') else getattr(x, 'value', None):
                getattr(x, 'tag', None) for x in (getattr(d, 'secIdList', None) or [])}
        _isin_exch = next((v for v, tg in
                           ((getattr(x, 'value', ''), getattr(x, 'tag', ''))
                            for x in (getattr(d, 'secIdList', None) or []))
                           if tg == 'ISIN'), '')
        if not _isin_exch:
            # ОТСУТСТВИЕ ПОДТВЕРЖДЕНИЯ — НЕ ПОДТВЕРЖДЕНИЕ (тринадцатый круг, №7).
            raise SystemExit(f'{sym}: биржа не вернула ISIN — личность фонда не '
                             f'подтверждена, реестр не пишется')
        if _isin_exch != isin:
            raise SystemExit(f'{sym}: ISIN у биржи {_isin_exch}, ожидался {isin} — '
                             f'другая листинговая линия, реестр не пишется')
        # secType — КАК ЕГО ВИДИТ БИРЖА (у IBKR фонды — 'STK'); литерал 'ETF' в реестре
        # заставлял сверку личности отвергать НАСТОЯЩИЕ контракты маршрута Е (№14).
        rows.append(dict(instrument=sym, sec_type=d.contract.secType,
                         pair_group=grp, exchange=exch,
                         currency=cur, con_id=d.contract.conId,
                         local_symbol=d.contract.localSymbol, expiry='', multiplier='',
                         primary_exchange=d.contract.primaryExchange or prim, isin=isin))
        print(f'  {sym}: con_id {d.contract.conId} ({isin})')

    # --- фактические маржинальные требования: предпросмотр заявки, без подачи ---
    print('\nмаржинальные требования (предпросмотр, заявка НЕ подаётся):')
    margins = {}
    for r in rows:
        if r['sec_type'] != 'FUT':
            continue
        from ib_insync import Contract
        c = Contract(conId=r['con_id'], exchange=r['exchange'])
        ib.qualifyContracts(c)
        _o = MarketOrder('BUY', 1)
        _acct = os.environ.get('ADDFUT_ACCOUNT') or (ib.managedAccounts() or [''])[0]
        _o.account = _acct                      # замер — на ПИНОВАННОМ счёте (№3)
        st = ib.whatIfOrder(c, _o)
        if st and st.initMarginChange:
            margins[r['instrument']] = dict(init=float(st.initMarginChange),
                                            maint=float(st.maintMarginChange))
            print(f"  {r['instrument']}: начальная {float(st.initMarginChange):,.0f}, "
                  f"поддерживающая {float(st.maintMarginChange):,.0f}")
        else:
            print(f"  {r['instrument']}: предпросмотр не вернул маржу")

    # НЕПОЛНЫЙ РЕЕСТР НЕ ПИШЕТСЯ, ЗАПИСЬ АТОМАРНА (№13): один сбой reqContractDetails не
    # должен уничтожать рабочий реестр, а полузаписанный файл — читаться контуром.
    need = len(ROOTS) * len(tags) + len(ETFS)
    if len(rows) < need:
        raise SystemExit(f'реестр неполон: {len(rows)} из {need} — прежний файл сохранён')
    # ГЕНЕРАТОР ПИШЕТ ТУДА, ОТКУДА ЧИТАЮТ (одиннадцатый круг, №6): при настроенном
    # ADDFUT_REGISTRY торговля читала внешний файл, а «успешное обновление» ложилось в
    # каталог кода — ложный след «реестр записан» при протухшем рабочем реестре.
    out = Path(os.environ.get('ADDFUT_REGISTRY') or (ROOT / 'live' / 'instruments_live.csv'))
    tmp = out.with_suffix('.csv.tmp')
    pd.DataFrame(rows).to_csv(tmp, index=False)
    import os as _os
    _os.replace(tmp, out)
    _mp = Path(os.environ.get('ADDFUT_MARGINS') or (ROOT / 'live' / 'margins_live.json'))
    _need_roots = {r0 for r0, _, _ in ROOTS}
    _got_roots = {k.rstrip('0123456789').rstrip('UZHM') for k in margins}
    if margins and _got_roots < _need_roots:
        # ЧАСТИЧНЫЙ ЗАМЕР НЕ ПУБЛИКУЕТСЯ (№2): затереть полный файл половиной значит дать
        # переходу константы по недостающему корню при, возможно, повышенном требовании.
        print(f'  замер неполон ({sorted(_got_roots)} из {sorted(_need_roots)}) — '
              f'margins_live.json сохранён прежним')
        margins = {}
    if margins:
        # атомарно и по тому же адресу, откуда читает переход (тринадцатый круг, №5)
        _tmp = _mp.with_suffix('.json.tmp')
        _tmp.write_text(json.dumps(margins, ensure_ascii=False, indent=1), encoding='utf-8')
        os.replace(_tmp, _mp)
    else:
        # ПУСТОЙ ЗАМЕР НЕ ЗАТИРАЕТ ФАКТИЧЕСКИЙ (двенадцатый круг, №4): whatIf на бумажном
        # шлюзе маржи не возвращает, и прежде файл с живыми замерами обнулялся при каждой
        # регенерации реестра.
        print('  маржи предпросмотром не получены — существующий margins_live.json сохранён')
    print(f'\nзаписано: {out} ({len(rows)} строк) и margins_live.json')

    acct = {v.tag: v.value for v in ib.accountValues() if v.tag in
            ('NetLiquidation', 'BuyingPower', 'MaintMarginReq', 'FullInitMarginReq')}
    print('счёт:', acct)
    ib.disconnect()


if __name__ == '__main__':
    main()
