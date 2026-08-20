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


def check_future_identity(contract, root, tag):
    """Ответ биржи против ЗАПРОШЕННОЙ поставки (восемнадцатый круг, №7). Прежняя запись
    копировала expiry/con_id из ответа как есть — под именем U26 мог честно завериться
    декабрьский контракт, и все дальнейшие «проверки личности» шли по кругу против тех же
    скопированных полей. Отдельной функцией — чтобы защита была под стендом и мутацией."""
    errs = []
    _exp = str(getattr(contract, 'lastTradeDateOrContractMonth', '') or '')
    _mon = int(_exp[4:6]) if len(_exp) >= 6 else 0
    _tagL = {3: 'H', 6: 'M', 9: 'U', 12: 'Z'}.get(_mon, '?')
    _tag_actual = f'{_tagL}{_exp[2:4]}' if len(_exp) >= 6 else '??'
    if _tag_actual != tag:
        errs.append(f'{root}{tag}: биржа вернула экспирацию {_exp} (серия {_tag_actual}) — '
                    f'не запрошенная поставка')
    if getattr(contract, 'symbol', '') != root:
        errs.append(f'{root}{tag}: символ биржи {getattr(contract, "symbol", "")!r} — '
                    f'не запрошенный корень')
    return errs


def check_etf_line(contract, sym, ticker, prim, cur, isin_exch, want_isin):
    """Листинговая ЛИНИЯ фонда против ОЖИДАНИЙ (девятнадцатый круг, №6). Один ISIN линии
    не различает: тот же фонд листингован на нескольких площадках, и det[0] другой линии
    становился «нормой» — фактическая площадка записывалась в реестр, а сверка личности
    затем сравнивала контракт со своей же копией. Торговые окна и календарь при этом
    остаются рассчитанными для LSE/SIX, а заявка ушла бы на другой рынок."""
    errs = []
    if not isin_exch:
        errs.append(f'{sym}: биржа не вернула ISIN — личность фонда не подтверждена')
    elif isin_exch != want_isin:
        errs.append(f'{sym}: ISIN у биржи {isin_exch}, ожидался {want_isin} — другая '
                    f'листинговая линия')
    _pe = getattr(contract, 'primaryExchange', '') or ''
    if _pe != prim:
        errs.append(f'{sym}: primaryExchange у биржи {_pe!r}, ожидалась {prim!r} — другая '
                    f'листинговая линия того же фонда')
    _sy = getattr(contract, 'symbol', '') or ''
    if _sy != ticker:
        errs.append(f'{sym}: биржевой тикер {_sy!r}, ожидался {ticker!r}')
    _cu = getattr(contract, 'currency', '') or ''
    if _cu != cur:
        errs.append(f'{sym}: валюта {_cu!r}, ожидалась {cur!r}')
    return errs


def tag_to_yyyymm(tag):
    import daily as DL
    m, y = DL.tag_month(tag)
    return f'{y}{m:02d}'


def _machine_pin():
    """Пин торгового счёта: окружение либо account.txt в каталоге замка (двадцать первый
    круг, №7). Прежде first_connect брал ADDFUT_ACCOUNT or managedAccounts()[0], а
    автопилот его не запускает — то есть его process-local экспорт здесь ничего не
    защищал: замер с ЧУЖОГО единственного счёта публиковался и потом разрешал переход на
    правильном счёте по чужому house margin."""
    import os as _o
    v = (_o.environ.get('ADDFUT_ACCOUNT') or '').strip()
    if v:
        return v
    try:
        import state as _ST
        return (_ST.lock_dir() / 'account.txt').read_text(encoding='utf-8').strip()
    except OSError:
        return ''


def _pin_or_die(ib):
    """Счёт замера ОБЯЗАН совпасть с пином. Без пина замер не снимается вовсе."""
    pin = _machine_pin()
    accts = ib.managedAccounts() or []
    if not pin:
        raise SystemExit('торговый счёт не пинован (нет ADDFUT_ACCOUNT и account.txt) — '
                         'замер маржи не снимается: он мог бы описывать чужой счёт')
    if accts and pin not in accts:
        raise SystemExit(f'пин {pin} не среди managed {accts} — не тот шлюз, замер '
                         f'не снимается')
    return pin


def measure_margin(ib, con_id, exchange, account):
    """ЗАМЕР МАРЖИ ОДНОЙ СЕРИИ — ОТДЕЛЬНОЙ ФУНКЦИЕЙ (сорок четвёртый круг, ложное
    доказательство №2).

    Замер жил внутри main(), а main() не исполняет ни один судья: удаление `_o.tif` здесь
    не роняло ни выпуска, ни мутационного контроля — генератор просто переставал обновлять
    маржу, пока ещё свежий старый файл проходил проверку возраста. То есть защита, найденная
    дорогой ценой 18.08, держалась на одной строке без единого наблюдателя.

    TIF ЗАДАЁТСЯ ЯВНО (найдено 18.08 диагностикой шлюза): пресет счёта переопределяет TIF
    (предупреждение 10349), и тогда whatIfOrder возвращает ПУСТОЙ СПИСОК вместо OrderState —
    замер молча не удаётся. Именно поэтому margins_live.json не обновлялся с 13.08: четыре
    попытки в разные часы дали пусто, а причину я искал во времени суток.
    Форма — как у настоящей заявки IBBroker.place(): GTC+outsideRth, иначе поколение маржи
    описывает ЧУЖУЮ форму заявки.

    Возвращает {'init':…, 'maint':…} либо None, если шлюз маржу не отдал.
    """
    from ib_insync import Contract, MarketOrder as _MO
    c = Contract(conId=con_id, exchange=exchange)
    ib.qualifyContracts(c)
    _o = _MO('BUY', 1)
    _o.account = account                        # замер — на ПИНОВАННОМ счёте
    _o.tif = 'GTC'
    _o.outsideRth = True
    st = ib.whatIfOrder(c, _o)
    if st and getattr(st, 'initMarginChange', None):
        _im = float(st.initMarginChange)
        _mm = float(st.maintMarginChange)
        # ЧАСОВОЙ «НЕ ПОСЧИТАНО» ФИЛЬТРУЕТСЯ У ПРОИЗВОДИТЕЛЯ (рецензия 20.08). Порог стоял
        # только в preview — потребителе того же ответа, — а ЗАПИСЫВАЕТ маржу в файл этот
        # код. UNSET_DOUBLE конечен, поэтому проходит и `0 < val < inf` в _live_margins:
        # 1.797e308 лёг бы в margins_live.json как настоящая маржа, попал в WORM-якорь как
        # аттестованная истина, и переход (включая аварийный выход Е→Ф) был бы заперт
        # маржинальным диагнозом до следующего успешного замера — до 35 дней.
        import ib_broker as _IBBm
        if abs(_im) >= _IBBm.UNSET_DOUBLE_MIN or abs(_mm) >= _IBBm.UNSET_DOUBLE_MIN:
            return None
        return dict(init=_im, maint=_mm)
    return None


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
            # ПОСТАВКА СВЕРЯЕТСЯ, А НЕ КОПИРУЕТСЯ (восемнадцатый круг, №7): буква серии
            # выводится из ФАКТИЧЕСКОЙ экспирации и обязана совпасть с запрошенной;
            # корень — с символом биржи. Проверка вынесена в check_future_identity —
            # под стенд и мутацию.
            _bad = check_future_identity(d.contract, root, tag)
            if _bad:
                raise SystemExit('; '.join(_bad) + ' — реестр не пишется')
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
        # ЛИНИЯ ФОНДА — ПРОТИВ ОЖИДАНИЙ, А НЕ КОПИИ ОТВЕТА (девятнадцатый круг, №6):
        # ISIN один на все линии; площадка, тикер и валюта обязаны совпасть с ожидаемыми
        # константами (LSEETF/EBS), иначе det[0] другой линии становился бы «нормой», а
        # сверка личности потом сравнивала бы контракт со своей же копией.
        _bad = check_etf_line(d.contract, sym, ticker, prim, cur, _isin_exch, isin)
        if _bad:
            raise SystemExit('; '.join(_bad) + ' — реестр не пишется')
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
        _m = measure_margin(ib, r['con_id'], r['exchange'], _pin_or_die(ib))
        if _m:
            margins[r['instrument']] = _m
            print(f"  {r['instrument']}: начальная {_m['init']:,.0f}, "
                  f"поддерживающая {_m['maint']:,.0f}")
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
    # ПОЛНОТА — ПО СЕРИЯМ, НЕ ПО КОРНЯМ (семнадцатый круг, №8): «хотя бы одна серия
    # корня» пропускала замер без следующей серии, и после смены реестра переход шёл по
    # марже прежней; полный замер обязан покрыть КАЖДУЮ FUT-строку реестра.
    _need_series = {r['instrument'] for r in rows if r['sec_type'] == 'FUT'}
    if margins and set(margins) < _need_series:
        print(f'  замер неполон (нет {sorted(_need_series - set(margins))}) — '
              f'margins_live.json сохранён прежним')
        margins = {}
    # ЗАМЕР ТОЛЬКО НА ЯВНОМ СЧЁТЕ (семнадцатый круг, №8): при нескольких managed accounts
    # молчаливый выбор первого публиковал маржу чужого счёта как живую.
    _accts = ib.managedAccounts() or []
    if margins and not _machine_pin() and len(_accts) > 1:
        print(f'  счетов несколько ({_accts}), пин не задан — замер не публикуется')
        margins = {}
    if margins:
        # ПРИВЯЗКА ЗАМЕРА (шестнадцатый круг, №4): дата, счёт и серии — без них старый
        # замер после квартальной смены реестра неотличим от живого.
        import datetime as _dt
        # ЗАМЕР ПРИВЯЗЫВАЕТСЯ К ПОКОЛЕНИЮ РЕЕСТРА (тридцать второй круг, №8). Прежде в
        # _meta были только дата, счёт и ИМЕНА серий. Но имя серии не меняется при
        # исправлении con_id: first_connect публикует новый реестр, а при неполном whatIf
        # СОХРАНЯЕТ прежний margins_live.json — и старый замер проходил все ворота как
        # относящийся к новому контракту. Заниженная маржа разрешает переход, после
        # которого фактический запас уже ниже О-3. Пишем con_id каждой серии: они и есть
        # поколение реестра, и сверяются с ТЕКУЩИМ файлом при чтении.
        _con_by_name = {r['instrument']: str(r['con_id']) for r in rows}
        margins['_meta'] = dict(
            date=_dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
            # ПИН БЕРЁТСЯ ИЗ ЕДИНСТВЕННОГО ИСТОЧНИКА (рецензия 20.08). Вынося замер в
            # measure_margin, я удалил единственное присваивание _acct — и оставил его
            # потребителя здесь: NameError на УСПЕШНОМ пути, реестр уже заменён атомарно, а
            # margins_live.json остаётся прежним. С обязательностью замера (№13) это дало бы
            # отказ якоря на КАЖДОМ замыкании — инцидент 19.08, заведённый правкой.
            account=_pin_or_die(ib), series=sorted(k for k in margins if k != '_meta'),
            con_ids={k: _con_by_name.get(k, '') for k in margins if k != '_meta'})
        # атомарно и по тому же адресу, откуда читает переход (тринадцатый круг, №5)
        _tmp = _mp.with_suffix('.json.tmp')
        _tmp.write_text(json.dumps(margins, ensure_ascii=False, indent=1), encoding='utf-8')
        os.replace(_tmp, _mp)
    else:
        # ПУСТОЙ ЗАМЕР НЕ ЗАТИРАЕТ ФАКТИЧЕСКИЙ (двенадцатый круг, №4): whatIf на бумажном
        # шлюзе маржи не возвращает, и прежде файл с живыми замерами обнулялся при каждой
        # регенерации реестра.
        print('  маржи предпросмотром не получены — существующий margins_live.json сохранён')
    # СООБЩЕНИЕ ГОВОРИТ О ФАКТЕ, А НЕ О НАМЕРЕНИИ (сорок третий круг, №12). Печаталось
    # безусловное «записано: реестр и margins_live.json» — даже когда whatIf вернул пусто и
    # замер СОЗНАТЕЛЬНО оставлен прежним (ветка выше). Я видел эту строку трижды за сутки, и
    # трижды она была ложью: обновлялся только реестр. Оператор по такому сообщению считает
    # поколение маржи свежим и идёт в переход со старым — либо, наоборот, не повторяет замер
    # перед роллом, думая, что он сделан.
    print(f'\nзаписано: {out} ({len(rows)} строк)')
    print('замер маржи: ' + ('обновлён' if margins else
                             'НЕ ОБНОВЛЁН (whatIf вернул пусто) — margins_live.json остался '
                             'ПРЕЖНИМ, повторить замер до перехода и до ролла'))

    acct = {v.tag: v.value for v in ib.accountValues() if v.tag in
            ('NetLiquidation', 'BuyingPower', 'MaintMarginReq', 'FullInitMarginReq')}
    print('счёт:', acct)
    ib.disconnect()


if __name__ == '__main__':
    main()
