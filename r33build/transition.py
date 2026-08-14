# -*- coding: utf-8 -*-
"""Переходный исполнитель v7.7 (ред. 30). Платформа Linux/Unix. Журнал — ПИНОВАННЫЙ
абсолютный путь (mr_engine.configure); alias-пути отклоняются. Замок — единый постоянный strategy+account
(mr_engine.hold_strategy_lock), реентерабельный только для потока-владельца, общий с движком МР.
Реестр инструментов пинован SHA-256 (одно чтение байтов). Ноги — пары src→dst с классами из реестра,
канонический порядок (-зерно цели, имя). Лимит непарной дельты СТРОГИЙ: требуемая сумма = зерно пары +
зазор округления; owner-cap 1,0% NLV (решение заказчика 08.08.2026); журнальные разрешения
GRANULARITY_EXCEPTION по sid. Ред. 31: открытие перехода требует OWNER_APPROVE заказчика
на тот же sid и ту же цель — сигнал МР является рекомендацией, а не командой.
Ред. 32: до открытия считается маржа и число заявок по ОТОБРАЖЁННОЙ книге маршрута-цели
(MES раскладывается в целые ES + остаток); запас ниже О-3/О-3-Е или заявок больше лимита
Priority Customer — отказ до первой заявки.
Двухфазность: OPEN/COMPLETE/ABORT/MIXED только через журнал МР;
сверка книги с планом и реестром до первого ордера; восстановление по нетто-позициям и order-id.
"""
import json, os, hashlib, csv, fcntl

MAX_UNPAIRED_PCT = 0.010
INTRA_CAP = 2.02

# --- Ред. 32: маржа и число заявок по ОТОБРАЖЁННОЙ книге (закрывает открытое замечание §3) ---
# Модельные требования на контракт, §7 v1.5.3.1 (таблица IBKR). Живые значения приходят
# из margin preview брокера; настоящий расчёт — предстартовая проверка ДО обращения к брокеру.
FUT_MARGIN = {'ES': 34_800.0, 'MES': 3_480.0, 'ZN': 2_160.0}
ETF_MAINT = 0.25                  # поддерживающее требование маршрута Е, §8
CUSHION_F = 2.00                  # О-3: маржинальный запас маршрута Ф
CUSHION_E = 1.40                  # О-3-Е: Equity with Loan Value / Current Maintenance Margin
ORDERS_PER_DAY = 390              # лимит статуса Priority Customer

def map_mes(n):
    """Внутренняя сетка n (в MES) -> книга брокера: целые ES = n // 10, остаток MES = n % 10.
    ОБЯЗАНА совпадать с sim_v164.map_mes — сверяется самопроверкой."""
    return (n // 10, n % 10)

def mapped_book(instrument, units):
    """Позиция внутренней сетки -> фактическая книга брокера (ред. 32)."""
    units = int(units)
    if instrument == 'MES':
        es, mes = map_mes(units)
        out = {}
        if es: out['ES'] = es
        if mes: out['MES'] = mes
        return out
    return {instrument: units} if units else {}

def _margin_of(v):
    """Требование одной серии: INIT ПРЕЖДЕ MAINT (восемнадцатый круг, №13) — возможность
    ОТКРЫТЬ книгу определяется начальным требованием; поддерживающее — запасной источник.
    Отдельной функцией — чтобы порядок источников был мутируем и доказуем стендом."""
    return float(v.get('init') or v.get('maint'))


def _meta_age_ok(md):
    """Давность замера (восемнадцатый круг, №13): старше 35 дней — неотличим от забытого.
    35 дней: замер обновляется каждым first_connect и как минимум при квартальной смене
    серий; порог операционный, не параметр стратегии."""
    import datetime as _dt
    _age = (_dt.datetime.now(_dt.timezone.utc).date() - md).days
    return 0 <= _age <= 35, _age


def _live_margins():
    """Фактические маржи из margins_live.json (пишет first_connect по замеру постановкой
    контракта). Двенадцатый круг, №4: preflight считал по константам, и при повышенном
    house-требовании переход разрешался по фиктивному запасу.

    ШЕСТНАДЦАТЫЙ КРУГ, №4: замер ПРИВЯЗАН к сериям живого реестра. Файл обязан нести _meta
    (дата, счёт, серии), а маржой корня считаются только серии, присутствующие в ТЕКУЩЕМ
    instruments_live.csv: после квартальной смены реестра старый замер ESU26 не смеет
    выдаваться за живую маржу корня ES — у новой серии требование может быть выше."""
    import json as _json
    import os as _os
    import csv as _csv
    from pathlib import Path as _P
    cand = _os.environ.get('ADDFUT_MARGINS')
    p = _P(cand) if cand else _P(__file__).resolve().parent / 'live' / 'margins_live.json'
    if not p.exists():
        # Файла нет (свежая машина, стенды): константы — ЯВНЫЙ запасной путь, о нём говорит
        # вызывающий в причинах. Битый или пустой файл — ДРУГОЕ: это сломанный замер.
        return {}
    raw_text = p.read_text(encoding='utf-8')
    try:
        raw = _json.loads(raw_text)
        meta = raw.pop('_meta', None)
        entries = {k: _margin_of(v) for k, v in raw.items()}
        for k, val in entries.items():
            if not (val == val and 0 < val < float('inf')):
                raise Incident(f'{p}: маржа {k} = {val!r} — не конечное положительное '
                               f'число, замер повреждён')
    except Exception as ex:
        # ТРИНАДЦАТЫЙ КРУГ, №5: молча подменять живой замер константами нельзя — переход
        # разрешался бы по фиктивному запасу при повышенном house-требовании.
        raise Incident(f'{p}: файл живой маржи повреждён ({ex}) — переход запрещён до '
                       f'починки замера')
    if meta is None:
        raise Incident(f'{p}: замер без привязки (_meta с датой и сериями) — формат '
                       f'устарел, перегенерировать first_connect')
    # ПРИВЯЗКА ПРОВЕРЯЕТСЯ ПО СОДЕРЖАНИЮ, А НЕ ПО НАЛИЧИЮ (восемнадцатый круг, №13).
    import datetime as _dt
    try:
        _md = _dt.datetime.strptime(str(meta.get('date', ''))[:10], '%Y-%m-%d').date()
    except ValueError:
        raise Incident(f'{p}: _meta.date {meta.get("date")!r} не разбирается — '
                       f'перегенерировать first_connect')
    _ok_age, _age = _meta_age_ok(_md)
    if not _ok_age:
        raise Incident(f'{p}: замеру {_age} дней — устарел, перегенерировать first_connect')
    if sorted(meta.get('series') or []) != sorted(entries):
        raise Incident(f'{p}: _meta.series {meta.get("series")} не совпадает с '
                       f'содержимым замера {sorted(entries)} — файл правился мимо '
                       f'first_connect')
    _pin0 = _os.environ.get('ADDFUT_ACCOUNT')
    if _pin0 and not (meta.get('account') or ''):
        raise Incident(f'{p}: счёт замера пуст при пине {_pin0} — перегенерировать '
                       f'first_connect')
    rp = _os.environ.get('ADDFUT_REGISTRY')
    rp = _P(rp) if rp else _P(__file__).resolve().parent / 'live' / 'instruments_live.csv'
    try:
        with open(rp, encoding='utf-8') as f:
            valid = {r['instrument'] for r in _csv.DictReader(f)
                     if (r.get('sec_type') or '') == 'FUT'}
    except Exception as ex:
        raise Incident(f'{rp}: живой реестр недоступен ({ex}) — привязка замера маржи '
                       f'непроверяема, переход запрещён')
    if not valid:
        raise Incident(f'{rp}: в живом реестре нет фьючерсов — привязка замера маржи '
                       f'непроверяема')
    # ЗАМЕР ОБЯЗАН ПОКРЫВАТЬ ВСЕ FUT-СЕРИИ РЕЕСТРА (семнадцатый круг, №8): реестр с U26 и
    # Z26 при замере одной U26 давал переход по марже прежней серии.
    missing = sorted(valid - set(entries))
    if missing:
        raise Incident(f'{p}: замер не покрывает серии реестра {missing} — '
                       f'перегенерировать first_connect')
    # СЧЁТ ЗАМЕРА — НАШ (семнадцатый круг, №8): пин задан, чужой замер живым не считается.
    _pin = _os.environ.get('ADDFUT_ACCOUNT')
    _macct = (meta or {}).get('account') or ''
    if _pin and _macct and _macct != _pin:
        raise Incident(f'{p}: замер снят на счёте {_macct}, торговый счёт {_pin} — '
                       f'перегенерировать first_connect')
    out = {}
    for k, val in entries.items():
        if k not in valid:
            continue                      # замер прежней серии живым не считается
        root = k.rstrip('0123456789').rstrip('UZHM') or k
        # НЕСКОЛЬКО СЕРИЙ ОДНОГО КОРНЯ — КОНСЕРВАТИВНЫЙ МАКСИМУМ (№8), а не последняя
        # по порядку JSON: заниженная маржа разрешала бы переход без запаса.
        out[root] = max(out.get(root, 0.0), val)
    if not out:
        raise Incident(f'{p}: ни одна серия замера {sorted(entries)} не совпала с живым '
                       f'реестром — маржа устарела или файл пуст, перегенерировать '
                       f'first_connect')
    return out


def book_margin(book, reg, prices=None):
    """Занятая маржа по фактической книге. Фьючерсы — требование на контракт;
    ETF — поддерживающее требование от стоимости позиции (нужна цена единицы)."""
    total = 0.0
    for instr, units in book.items():
        if instr not in reg:
            raise Incident(f'{instr}: инструмента нет в реестре — маржа не считается')
        if reg[instr]['sec_type'] == 'FUT':
            if instr not in FUT_MARGIN:
                raise Incident(f'{instr}: нет модельного требования маржи')
            _lm = _live_margins()
            if _lm:
                # ПРИ СУЩЕСТВУЮЩЕМ ЗАМЕРЕ дыры не добираются константами (шестнадцатый
                # круг, №4): молчаливый .get прятал неполноту — повышенное требование
                # непокрытого корня не замечалось. Константы — только когда файла нет.
                if instr not in _lm:
                    raise Incident(f'{instr}: живой замер маржи не покрывает текущую '
                                   f'серию корня — перегенерировать first_connect')
                total += abs(int(units)) * _lm[instr]
            else:
                total += abs(int(units)) * FUT_MARGIN[instr]
        else:
            px = (prices or {}).get(instr)
            if px is None:
                raise Incident(f'{instr}: нет цены единицы — маржа ETF не считается')
            total += abs(int(units))*float(px)*ETF_MAINT
    return total

def target_book(legs, mapped=True):
    """Книга МАРШРУТА-ЦЕЛИ после перехода, в фактических инструментах брокера.

    mapped=True — нормативная упаковка ред. 32 (MES раскладывается в целые ES + остаток);
    mapped=False — книга, которую ФАКТИЧЕСКИ покупает исполнитель (девятнадцатый круг, №2):
    _run_lots подаёт заявки в единицах цели плана (MES-сетка — намеренно: зерно §8б), а
    переупаковку в канон выполняет ежедневный контур СЛЕДУЮЩЕЙ сессией (это сделка — №3).
    Preflight обязан выдержать ОБЕ физические книги, а не только отображённую."""
    book, prices = {}, {}
    for name, spec in legs.items():
        di, dp = spec['dst'][0], float(spec['dst'][1])
        # ДРОБНЫЕ ДОЛИ ФОНДОВ НЕ УСЕКАЮТСЯ. int() здесь считал целевую книгу по 2 000 000
        # вместо фактических 2 000 000,5 — предстартовая маржа расходилась с планом.
        usd = sum(_units(_i, u)*float(uu) for _i, u, uu in spec['src'])   # переводимая сумма ноги
        units = int(usd // dp)                                    # целые единицы цели (округление вниз)
        # ПОКУПКА КРОЕТ ОСТАТОК ROUND-ОМ (семнадцатый круг, №11): исполнитель законно
        # берёт на одну долю больше плана (финальная сверка допускает ±цену цели), и
        # ворота О-3 обязаны выдержать этот худший разрешённый случай — маржа цели
        # считается по потолку, а не по полу.
        if usd - units*dp > TOL:
            units += 1
        add = mapped_book(di, units) if mapped else ({di: units} if units else {})
        for k, v in add.items():
            book[k] = book.get(k, 0) + v
        prices[di] = dp
        if di == 'MES':
            prices['ES'] = dp*10.0; prices['MES'] = dp
    return book, prices

class _CountBroker:
    """Считающий брокер: полное исполнение каждой заявки. Оценка числа заявок ведётся ТЕМ ЖЕ
    кодом, который исполняет (семнадцатый круг, №10): прежняя формула «2 на лот» занижала
    на порядок — внутри лота лимит §8б дробит исполнение на десятки пар sell/buy."""

    counting = True        # runtime-лимит §заявок в _run_lots пропускает СЧЁТНЫЙ прогон

    def __init__(self):
        self.n = 0

    def sell_units(self, i, u):
        self.n += 1
        return (f'c{self.n}', u)

    def buy_units(self, i, u):
        self.n += 1
        return (f'c{self.n}', u)

    def minutes_since(self, k):
        return 0

    def gross(self):
        return 0.0

    def open_orders(self):
        return []

    def cancel_order(self, oid):
        return True

    def net_positions(self):
        return {}


def plan_orders(plan, legs, lim):
    """Заявок за сессию перехода — прогоном плана через исполнителя на считающем брокере.
    Потолок — плюс компенсация/откат по заявке на лот.

    ПРИЗНАННЫЙ ПРЕДЕЛ (восемнадцатый круг, №12): оценка предполагает ПОЛНОЕ исполнение
    каждой заявки — partial-исполнения и компенсации порождают итерации сверх неё, и
    оценка является НИЖНЕЙ границей. Денежную защиту держит runtime-счётчик в самом
    _run_lots: упор в дневной лимит фиксируется ДО заявки, с MIXED и разбором."""
    import copy
    import tempfile
    br = _CountBroker()
    st = dict(done=[], order_ids=[], log=[], executed_usd=0.0)
    unp = {name: 0.0 for name in legs}
    sp = os.path.join(tempfile.mkdtemp(prefix='addfut-plancount-'), 'st.json')

    def _f(msg, cancel=True):
        raise Incident(f'оценка плана заявок: {msg}')

    _run_lots(br, copy.deepcopy(plan), st, sp, lim, unp, {}, _f)
    return br.n, br.n + len(plan)

def preflight_margin_orders(legs, plan, capital, reg, to_route, lim=None):
    """Ред. 32: предстартовая проверка книги маршрута-цели.
    Возвращает сводку; нарушение порога или лимита заявок = Incident до первой заявки.
    lim — лимит §8б, УЖЕ ВЫЧИСЛЕННЫЙ исполнителем (с журнальным грантом): пересчёт здесь
    без гранта ронял preflight там, где исполнение законно (семнадцатый круг, №10)."""
    book, prices = target_book(legs)
    # МАРЖА — ПО ХУДШЕЙ ИЗ ДВУХ ФИЗИЧЕСКИХ КНИГ (девятнадцатый круг, №2): отображённая
    # (норматив ред. 32) И фактическая книга исполнения (заявки идут в единицах цели плана,
    # переупаковка — следующей сессией). Живой замер может дать MES дороже ES/10 — считать
    # запас только по отображённой значило бы доказывать безопасность другой книги.
    book_exec, _ = target_book(legs, mapped=False)
    margin = max(book_margin(book, reg, prices), book_margin(book_exec, reg, prices))
    if margin <= 0:
        raise Incident('маржа целевой книги нулевая — план пуст либо книга не распознана')
    cushion = capital/margin
    need = CUSHION_E if to_route == 'E' else CUSHION_F
    if lim is None:
        lim = unpaired_limit(legs, capital)
    n_ord, n_max = plan_orders(plan, legs, lim)
    info = dict(book=book, margin_usd=margin, cushion=cushion, need=need,
                orders=n_ord, orders_max=n_max, limit=ORDERS_PER_DAY)
    if cushion < need:
        raise Incident(f'маржинальный запас целевой книги {cushion:.2f}x ниже порога {need:.2f}x '
                       f'(маржа ${margin:,.0f} при капитале ${capital:,.0f}) — переход запрещён, '
                       f'{"О-3-Е" if to_route == "E" else "О-3"}')
    if n_max > ORDERS_PER_DAY:
        raise Incident(f'план требует до {n_max} заявок при лимите {ORDERS_PER_DAY} в день '
                       f'(Priority Customer) — переход разбивается на сессии вручную')
    return info
CLOSE_CAP = 2.00
TIMEOUT_MIN = 15
TOL = 1e-6

MIN_NLV_F = 3_000_000.0      # §8 ред. 33: порог маршрута Ф


class Incident(Exception):
    pass

STRATEGY_ID = 'ADD-FUT-v1_6'

REGISTRY_SHA256 = 'd5a2982c128f6869081a820d68bbaea4da7f6284ea1695b12db093beefeba2f7'

def load_registry(path='instruments.csv'):
    body = open(path, 'rb').read()                             # ЕДИНСТВЕННОЕ чтение: хэш и разбор одного body
    if hashlib.sha256(body).hexdigest() != REGISTRY_SHA256:
        raise Incident('реестр инструментов не совпадает с пинованным SHA-256 — подмена файла, исполнение запрещено')
    import io
    reg = {}
    for r in csv.DictReader(io.StringIO(body.decode('utf-8'))):
        reg[r['instrument']] = dict(sec_type=r['sec_type'], pair_group=r['pair_group'],
                                    exchange=r['exchange'], currency=r['currency'], con_id=r['con_id'])
    return reg


def _atomic(path, obj):
    tmp = f'{path}.tmp.{os.getpid()}.{os.urandom(4).hex()}'
    with open(tmp, 'w') as f:                       # долговечность состояния исполнителя
        json.dump(obj, f, ensure_ascii=False); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)
    fd = os.open(os.path.dirname(os.path.abspath(path)) or '.', os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)

def transition_id(signal_id, from_route, to_route, capital, plan):
    key = json.dumps([signal_id, from_route, to_route, round(capital, 2), plan], sort_keys=True)
    return hashlib.sha256(key.encode()).hexdigest()[:16]

def plan_lots(legs, capital):
    lots = []
    seen_src, seen_dst = set(), set()
    for name, spec in legs.items():
        di = spec['dst'][0]
        if di in seen_dst: raise Incident(f'{di}: дублированная цель в плане')
        seen_dst.add(di)
        dpv = float(spec['dst'][1])
        if not (dpv > 0 and dpv == dpv and dpv != float('inf')):
            raise Incident(f'{di}: цена цели должна быть конечной положительной')
        for instr, units, u in spec['src']:
            if instr in seen_src: raise Incident(f'{instr}: дублированный источник в плане')
            seen_src.add(instr)
            uv = float(u)
            if not (uv > 0 and uv == uv and uv != float('inf')):
                raise Incident(f'{instr}: долларовый эквивалент должен быть конечным положительным')
    for name, spec in sorted(legs.items(), key=lambda kv: (-float(kv[1]['dst'][1]), kv[0])):
        if len(spec['dst']) != 3 or spec['dst'][2] not in ('ETF', 'FUT'):
            raise Incident(f'{name}: dst требует класс инструмента ETF|FUT')
        dinstr, dprice = spec['dst'][0], spec['dst'][1]
        for instr, units, unit_usd in spec['src']:
            # ЦЕЛОЧИСЛЕННОСТЬ ТРЕБУЕТСЯ ТОЛЬКО ОТ ФЬЮЧЕРСОВ. Фонды торгуются долями, и
            # безусловное требование делало ВЫХОД из маршрута Е невозможным ещё до первой
            # заявки: законная книга вроде 2 000 000,5 доли отвергалась планировщиком.
            frac_ok = any(str(instr).startswith(x) for x in FRACTIONAL_OK)
            if units <= 0 or (not frac_ok and int(units) != units):
                raise Incident(f'{instr}: единицы должны быть '
                               f'{"положительными" if frac_ok else "целыми положительными"}')
            q = max(1, int(round(dprice/float(unit_usd)))) if dprice > float(unit_usd)*1.5 else 1
            tail = int(units) % q
            per_lot = max(q, (int(units) // 4) // q * q)
            # Дробный остаток источника переносится ЦЕЛИКОМ в последний лот: прежде
            # округление вверх заставляло продать больше, чем есть, и уводило источник в
            # короткую позицию.
            k, left = 0, (float(units) if frac_ok else int(units))
            while left > 1e-9:
                take = min(per_lot, left)
                lots.append(dict(leg=name, src=instr, dst=dinstr, units=take,
                                 unit_usd=float(unit_usd), dprice=float(dprice), step=k))
                left -= take; k += 1
    return lots

MIN_AUTO_NLV = 9_856_000.0   # ниже — автоматический переход невозможен: один ZN не входит в owner-cap 1%

def unpaired_limit(legs, capital, grant_limit=None):
    """Owner-cap заказчика = 1,0% капитала. Контракт крупнее owner-cap НЕ расширяет лимит
    автоматически: требуется предразложение ES->MES (нормативно, §8б) либо явное разрешение
    заказчика на конкретный переход (allow_granularity_exception=True) — иначе Incident/REVIEW."""
    mx = max([u for spec in legs.values() for _, n, u in spec['src']]
             + [float(spec['dst'][1]) for spec in legs.values()])   # зерно ОБЕИХ сторон пары
    h = max(min(spec['dst'][1], float(u))/2.0 + 1.0
            for spec in legs.values() for _, n, u in spec['src'])   # зазор округления пары
    pct = MAX_UNPAIRED_PCT*capital
    if mx + h > pct:
        need = mx + h
        if grant_limit is None or grant_limit < need:
            raise Incident(f'требуемый лимит ${need:,.0f} (зерно ${mx:,.0f} + зазор округления ${h:,.0f}) '
                           f'превышает owner-cap ${pct:,.0f} — журнальное разрешение GRANULARITY_EXCEPTION '
                           f'на сумму не ниже требуемой либо NLV >= ${MIN_AUTO_NLV:,.0f}')
        return grant_limit
    return pct

FRACTIONAL_OK = ('CSPX', 'CBU0')          # UCITS-ETF торгуются долями


def _frac(instr):
    return any(str(instr).startswith(x) for x in FRACTIONAL_OK)


def _units(instr, u):
    """Количество в его собственных единицах: у фондов дробное, у фьючерсов целое.
    Безусловное int() усекало законную дробь и делало выход из маршрута Е невозможным."""
    return float(u) if _frac(instr) else int(u)


def _int_fill(fill, what):
    """Целочисленность обязательна для ФЬЮЧЕРСОВ. Для фондов дробная доля законна, и
    прежний безусловный отказ блокировал именно выход из маршрута Е."""
    if any(str(what).startswith(x) for x in FRACTIONAL_OK):
        return float(fill)
    if abs(fill - round(fill)) > 1e-9:
        raise Incident(f'{what}: дробное исполнение {fill} отклонено')
    return int(round(fill))

def execute(broker, state_path, capital, legs, signal_id='', from_route='F', to_route='E',
            emergency=False,
            in_common_window=True, resume=False, journal=None, mr_state=None, asof=None,
            registry='instruments.csv'):
    import mr_engine as _M
    import math as _math
    # ПОРОГ §8 ПРОВЕРЯЕТСЯ И ЗДЕСЬ. Движок мог выдать сигнал в Ф при капитале выше порога,
    # а к моменту исполнения капитал упал; OWNER_APPROVE остаётся действительным, и без этой
    # проверки книга переводится в маршрут, который нормативно недоступен.
    # ОДНА БЛОКИРОВКА НА КНИГУ, общая с ежедневным контуром (live/state.py). Без неё
    # ребалансировщик и перевод между маршрутами могли работать по одной книге
    # одновременно и подать встречные заявки.
    import sys as _sys, os as _os
    _live = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'live')
    if _live not in _sys.path:
        _sys.path.insert(0, _live)
    import state as _ST
    _lock = _ST.hold_book_lock()          # единый каталог блокировки, см. state.lock_dir
    _lock.__enter__()
    try:
        return _execute_guarded(broker, state_path, capital, legs, signal_id, from_route,
                                to_route, in_common_window, resume, journal, mr_state, asof,
                                registry, emergency)
    finally:
        _lock.__exit__(None, None, None)


def _execute_guarded(broker, state_path, capital, legs, signal_id='', from_route='F',
                     to_route='E', in_common_window=True, resume=False, journal=None,
                     mr_state=None, asof=None, registry='instruments.csv', emergency=False):
    import mr_engine as _M
    import math as _math
    if emergency and journal:
        # АВАРИЙНЫЙ ОБХОД ПОРОГА ЗАПИСЫВАЕТСЯ. Иначе признак emergency — молчаливый способ
        # обойти жёсткое ограничение §8, и по журналу нельзя отличить штатный переход от
        # обхода: событие пишется ДО первой заявки, а не после.
        import mr_engine as _MJ
        _MJ.append_event(journal, str(asof or ''), 'EMERGENCY_OVERRIDE',
                         f'{from_route}->{to_route}|NLV={float(capital):.0f}|sid={signal_id}')
    if to_route == 'F' and float(capital) < MIN_NLV_F and not emergency:
        raise Incident(f'переход в Ф запрещён: NLV {float(capital):,.0f} ниже порога '
                       f'{MIN_NLV_F:,.0f} (§8); требуется решение заказчика. Аварийный '
                       f'вывод из Е выполняется тем же вызовом с emergency=True и '
                       f'записывается в журнал отдельным событием')
    if not (isinstance(capital, (int, float)) and _math.isfinite(capital) and capital > 0):
        raise Incident('capital должен быть конечным положительным числом')
    if journal is None or mr_state is None or asof is None:
        raise Incident('journal/mr_state/asof обязательны — исполнение без журнала МР запрещено')
    import mr_engine as _M0
    try:
        journal = _M0.canonical_journal(journal)               # канонический путь; symlink = отказ
    except _M0.JournalCorrupt as ex:
        raise Incident(str(ex))
    reg = load_registry(registry)
    want_cls = 'ETF' if to_route == 'E' else 'FUT'
    src_cls = 'ETF' if from_route == 'E' else 'FUT'
    for name, spec in legs.items():
        di = spec['dst'][0]
        if di not in reg:
            raise Incident(f'{name}: инструмент цели {di} отсутствует в реестре instruments.csv')
        rc = reg[di]['sec_type']
        if len(spec['dst']) != 3 or spec['dst'][2] != rc:
            raise Incident(f'{name}: метка класса {spec["dst"][2] if len(spec["dst"]) > 2 else "?"} '
                           f'не совпадает с реестром ({di} = {rc})')
        if rc != want_cls:
            raise Incident(f'{name}: класс цели {rc} по реестру не соответствует маршруту {to_route} (ожидается {want_cls})')
        for instr, units, u in spec['src']:
            if instr not in reg:
                raise Incident(f'{name}: инструмент источника {instr} отсутствует в реестре')
            if reg[instr]['sec_type'] != src_cls:
                raise Incident(f'{name}: {instr} класса {reg[instr]["sec_type"]} не является источником маршрута {from_route}')
            if reg[instr]['pair_group'] != reg[di]['pair_group']:
                raise Incident(f'{name}: пара {instr}->{di} вне whitelist направлений (группы {reg[instr]["pair_group"]}/{reg[di]["pair_group"]})')
    _r_now = _M.derive_state(journal, __import__('datetime').date.fromisoformat(asof))[0]
    if _r_now != from_route:
        raise Incident(f'from_route={from_route} не совпадает с маршрутом журнала {_r_now}')
    if not in_common_window:
        raise Incident('вне пересечения фактических торговых сессий LSE/CME')
    plan = plan_lots(legs, capital)
    tid = transition_id(signal_id, from_route, to_route, capital, plan)
    try:
        _ctx = _M.hold_strategy_lock(journal)
        _ctx.__enter__()
    except RuntimeError:
        raise Incident('strategy-lock (стратегия+счёт) занят другим процессом — параллельное исполнение запрещено')
    try:
        return _execute_locked(broker, state_path, capital, legs, signal_id, from_route, to_route,
                               in_common_window, resume, journal, mr_state, asof, registry,
                               plan, tid, reg, want_cls, src_cls, _M)
    finally:
        _ctx.__exit__(None, None, None)


def _execute_locked(broker, state_path, capital, legs, signal_id, from_route, to_route,
                    in_common_window, resume, journal, mr_state, asof, registry,
                    plan, tid, reg, want_cls, src_cls, _M):
    try:                                                       # ред. 31: подпись заказчика ТОЛЬКО из журнала
        approved = _M.find_approval(journal, asof, signal_id, to_route)
    except _M.JournalCorrupt as ex:
        raise Incident(f'журнал повреждён при чтении одобрения: {ex}')
    if not approved:
        raise Incident(f'нет OWNER_APPROVE заказчика на сигнал {signal_id} в маршрут {to_route} — '
                       f'сигнал МР является рекомендацией, исполнение запрещено')
    try:
        grant_limit = _M.find_grant(journal, asof, signal_id)   # разрешение ТОЛЬКО из журнала
    except _M.JournalCorrupt as ex:
        raise Incident(f'журнал повреждён при чтении разрешения: {ex}')
    lim = unpaired_limit(legs, capital, grant_limit)
    for name, spec in legs.items():                            # хвост кванта цели допустим только в пределах лимита
        dp = spec['dst'][1]
        for instr, units, u in spec['src']:
            q = max(1, int(round(dp/float(u)))) if dp > float(u)*1.5 else 1
            _tail = _units(instr, units) % q
            if _tail*float(u) > lim + TOL:
                raise Incident(f'{instr}: хвост {_tail} единиц (${_tail*float(u):,.0f}) '
                               f'превышает лимит — предварительная подгонка книги')


    def hook(kind):
        return _M.confirm_transition(journal, mr_state, asof, to_route, kind=kind,
                                     tid=tid, sid=signal_id) is True

    if os.path.exists(state_path):
        st = json.load(open(state_path))
        if st.get('tid') != tid:
            raise Incident('состояние не соответствует переходу (transition_id) — ручная сверка')
        if not resume and (st.get('opened') or st.get('done') or st.get('executed_usd', 0.0) > TOL or st.get('order_ids')):
            raise Incident('переход уже открыт/имеет прогресс — повторный запуск только с resume=True')
        if not resume:                                         # свежий заход без прогресса: снапшот пересоздаётся
            st = dict(tid=tid, postponed=st.get('postponed', 0), done=[], executed_usd=0.0,
                      order_ids=[], snapshot=broker.net_positions(), log=[])
            _atomic(state_path, st)
    else:
        st = dict(tid=tid, postponed=0, done=[], executed_usd=0.0, order_ids=[],
                  snapshot=broker.net_positions(), log=[])
        _atomic(state_path, st)
    if resume:
        # ОТМЕНЫ РАНЬШЕ PREVIEW (восемнадцатый круг, №4): отказ preview со старой живой
        # заявкой оставлял её исполняться весь «отложенный» период — вне траектории §8б.
        # fail() здесь ещё не определён; неснятое при известном прогрессе — честный MIXED.
        try:
            _pre_open = list(broker.open_orders())
        except Exception as ex:
            hook('mixed'); _atomic(state_path, st)
            raise Incident(f'resume: запрос заявок до preview не выполнен ({ex}) — MIXED')
        for _oid in _pre_open:
            try:
                _r = broker.cancel_order(_oid)
            except Exception as ex:
                hook('mixed'); _atomic(state_path, st)
                raise Incident(f'resume: отмена {_oid} до preview оборвана ({ex}) — MIXED')
            if not (_r is True or (isinstance(_r, dict) and _r.get('terminal'))):
                hook('mixed'); _atomic(state_path, st)
                raise Incident(f'resume: отмена {_oid} до preview не подтверждена — MIXED')
            # ОТМЕНА ЗАСТАЛА ИСПОЛНЕНИЕ (девятнадцатый круг, №11): filled терминального
            # ответа — состоявшаяся сделка; книга уже изменилась, и дальнейший исход
            # НЕ ИМЕЕТ ПРАВА на ABORT (учёт — структурный, снимает его ветка POSTPONED×3).
            if isinstance(_r, dict) and _r.get('filled'):
                st['cancel_fills'] = float(st.get('cancel_fills', 0.0)) + abs(float(_r['filled']))
                st['log'].append(('cancel_filled_on_resume', _oid, float(_r['filled'])))
            else:
                st['log'].append(('cancel_on_resume', _oid, 0))
        if _pre_open:
            _atomic(state_path, st)
    # PREVIEW — ПОД АВАРИЙНОЙ ОБОЛОЧКОЙ (девятнадцатый круг, №11): после resume-отмен книга
    # могла уже измениться (исполнение в отмене); исключение preview прежде уходило сырым —
    # без перечтения позиций и без TRANSITION_MIXED, а журнал показывал OPEN.
    try:
        _pv = broker.preview()
    except Exception as ex:
        if resume or st.get('opened') or st.get('executed_usd', 0.0) > TOL \
                or st.get('cancel_fills'):
            hook('mixed'); _atomic(state_path, st)
            raise Incident(f'margin preview оборван ({ex}) при возможно изменённой книге — '
                           f'состояние MIXED, ручная сверка')
        raise Incident(f'margin preview оборван ({ex}) — переход не начат')
    if not _pv:
        st['postponed'] += 1; _atomic(state_path, st)
        if st['postponed'] >= 3:
            if st['executed_usd'] > TOL or st.get('cancel_fills'):
                # cancel_fills (№11): исполнение, пойманное отменой, — прогресс; прежний
                # критерий по одному executed_usd писал бы ЛОЖНЫЙ ABORT при изменённой книге.
                hook('mixed')
            else:
                hook('open'); hook('abort')                    # честная пара OPEN+ABORT, pending снимается строго
            raise Incident('margin preview отклонён три раза — инцидент')
        return dict(status='POSTPONED', postponed=st['postponed'])
    st['postponed'] = 0; _atomic(state_path, st)
    _r0, _p0, _mx0, _an0, _sid0, _otid0, _mk0 = _M.derive_state(journal, __import__('datetime').date.fromisoformat(asof))
    if _otid0 == tid and not resume and not st.get('opened'):
        raise Incident('переход с этим tid уже захвачен в журнале — только resume')
    if not resume:
        # БАРЬЕР ЖИВЫХ ЗАЯВОК (восемнадцатый круг, №3): осиротевшая заявка другого клиента
        # или терминала исполнится поперёк плана и пробьёт лимит §8б — сверка позиций её
        # не видит. Чужое снимать нельзя — только отказ до первой заявки.
        _live0 = list(broker.open_orders())
        if _live0:
            raise Incident(f'живые заявки на счёте до перехода {_live0[:4]} — исполнение '
                           f'запрещено до их разбора')
        snap0 = broker.net_positions()
        planned_src = {instr for spec in legs.values() for instr, _, _ in spec['src']}
        for name, spec in legs.items():
            for instr, units, u in spec['src']:
                # СРАВНЕНИЕ С ДОПУСКОМ И БЕЗ УСЕЧЕНИЯ: законная дробная позиция фонда
                # (2 000 000,5) отвергалась ДО первой заявки, и выход из маршрута Е был
                # невозможен, несмотря на исправление планировщика.
                if abs(float(snap0.get(instr, 0)) - _units(instr, units)) > 1e-9:
                    raise Incident(f'{instr}: книга ({snap0.get(instr, 0)}) не соответствует '
                                   f'плану ({_units(instr, units)}) — переход отклонён')
        for instr, qty in snap0.items():                       # ПОЛНАЯ сверка книги
            if qty == 0: continue
            if instr not in reg:
                raise Incident(f'{instr}: неизвестный реестру инструмент в книге ({qty}) — исполнение запрещено')
            if reg[instr]['sec_type'] == src_cls and instr not in planned_src:
                raise Incident(f'{instr}: позиция класса источника ({qty}) вне плана — книга не переводится целиком')
            if reg[instr]['sec_type'] == want_cls:
                raise Incident(f'{instr}: предсуществующая позиция класса цели ({qty}) до перехода — требуется разбор')
    mo = preflight_margin_orders(legs, plan, capital, reg, to_route, lim=lim)   # ред. 32: маржа и заявки ДО открытия
    st['preflight'] = {k: mo[k] for k in ('margin_usd', 'cushion', 'orders', 'orders_max')}
    _atomic(state_path, st)
    if not hook('open'):                                      # проверяется ВСЕГДА, включая resume (идемпотентно)
        raise Incident('журнал отклонил открытие перехода (нет сигнала/sid/чужой tid) — исполнение запрещено')
    st['opened'] = True; _atomic(state_path, st)

    max_dp = max(spec['dst'][1] for spec in legs.values())

    def fail(msg, cancel=True):
        stuck = []
        if cancel:
            # СБОЙ САМОГО ЗАПРОСА ЗАЯВОК НЕ РОНЯЕТ АВАРИЙНЫЙ ПУТЬ (семнадцатый круг, №9):
            # open_orders теперь обязан отказывать, и невыясненность живых заявок — это
            # НЕСНЯТОЕ по смыслу, исход MIXED, а не сырое исключение мимо hook().
            try:
                _open = list(broker.open_orders())
            except Exception as ex:
                _open = []
                stuck.append(f'запрос заявок: {ex}')
            for oid in _open:
                # РЕЗУЛЬТАТ ОТМЕНЫ ПРОВЕРЯЕТСЯ (девятая рецензия, №24): прежде он
                # игнорировался, при живой заявке писался ABORT, pending снимался — а заявка
                # позже исполнялась сама, уже вне всякого учёта.
                try:
                    r = broker.cancel_order(oid)
                except Exception:
                    stuck.append(oid); continue
                if not (r is True or (isinstance(r, dict) and r.get('terminal'))):
                    stuck.append(oid)
            if not stuck:
                try:
                    still = list(broker.open_orders())
                except Exception as ex:
                    still = [f'запрос заявок: {ex}']
                stuck += still
        try:                                                            # обязательная сверка книги до записи исхода
            now = broker.net_positions(); snap = st['snapshot']
            moved = sum(abs(now.get(k, 0) - snap.get(k, 0)) for k in set(list(now) + list(snap)))
        except Exception:
            moved = 1
        if stuck:
            msg += f' | НЕСНЯТЫЕ ЗАЯВКИ {stuck} — исход не ABORT'
        kind = 'mixed' if (stuck or st['executed_usd'] > TOL or moved > 0) else 'abort'
        try:
            ok = hook(kind)
        except Exception as ex:
            _atomic(state_path, st)
            raise Incident(msg + f' | КРИТИЧНО: запись {kind.upper()} в журнал провалилась ({ex!r}) — немедленная ручная сверка книги и журнала')
        if not ok:
            _atomic(state_path, st)
            raise Incident(msg + f' | КРИТИЧНО: журнал отклонил {kind.upper()} — немедленная ручная сверка книги и журнала')
        _atomic(state_path, st)
        raise Incident(msg)

    resume_unp = {}
    if resume:
        # ВСЕ БРОКЕРСКИЕ ШАГИ RESUME — ПОД АВАРИЙНОЙ ОБОЛОЧКОЙ (семнадцатый круг, №9):
        # сырое исключение здесь оставляло разорванную книгу без MIXED в журнале.
        try:
            _open0 = list(broker.open_orders())               # ВСЕ открытые: известные и чужие
        except Exception as ex:
            fail(f'resume: запрос открытых заявок не выполнен ({ex}) — состояние недоказуемо')
        for oid in _open0:
            # СТРУКТУРНЫЙ ФАКТ ОТМЕНЫ (десятый круг, №7): живой адаптер отдаёт словарь, и
            # строгое «is True» останавливало resume после ПОДТВЕРЖДЁННОЙ отмены; хуже —
            # ранний Incident не писал MIXED при, возможно, уже изменившейся позиции.
            try:
                _r = broker.cancel_order(oid)
            except Exception as ex:
                fail(f'resume: отмена ордера {oid} оборвана ({ex})')
            if not (_r is True or (isinstance(_r, dict) and _r.get('terminal'))):
                fail(f'отмена ордера {oid} не подтверждена терминальным статусом')
            st['log'].append(('cancel_on_resume', oid, 0))
        try:
            now = broker.net_positions()
        except Exception as ex:
            fail(f'resume: позиции недоступны ({ex}) — состояние недоказуемо')
        snap = st['snapshot']
        src_prog = {}; total_unp = 0.0; total_abs = 0.0
        for name, spec in legs.items():
            di, dp = spec['dst'][0], spec['dst'][1]
            sold_leg = 0.0
            for instr, units, u in spec['src']:
                ds = snap.get(instr, 0) - now.get(instr, 0)
                _int_fill(ds, instr)
                if ds < 0: raise Incident(f'{instr}: позиция выросла во время перехода — ручная сверка')
                sold_leg += ds*u; src_prog[instr] = ds
            db = now.get(di, 0) - snap.get(di, 0)
            _int_fill(db, di)
            diff = sold_leg - db*dp                       # компенсация СВОЕЙ ногой
            if diff > dp/2 + TOL:
                _order_gate(st, broker, fail, f'восстановительная покупка {di}')   # №8
                try:
                    oid, f = broker.buy_units(di, int(round(diff/dp)))
                except Exception as ex:
                    fail(f'{name}: восстановительная покупка {di} оборвана ({ex}) — '
                         f'исход недоказуем')
                st['order_ids'].append(oid)
                try:
                    _fi = _int_fill(f, di)
                except Incident as ex:
                    fail(f'{name}: восстановительная покупка исполнена дробно ({ex})')
                diff -= _fi*dp
                st['log'].append(('recover_buy', di, f))
            elif diff < -dp/2 - TOL:
                _order_gate(st, broker, fail, f'восстановительная продажа {di}')   # №8
                try:
                    oid, f = broker.sell_units(di, int(round(-diff/dp)))
                except Exception as ex:
                    fail(f'{name}: восстановительная продажа {di} оборвана ({ex}) — '
                         f'исход недоказуем')
                st['order_ids'].append(oid)
                try:
                    _fi = _int_fill(f, di)
                except Incident as ex:
                    fail(f'{name}: восстановительная продажа исполнена дробно ({ex})')
                diff += _fi*dp
                st['log'].append(('recover_sell', di, f))
            _atomic(state_path, st)
            if abs(diff) > dp + TOL:
                # ЧЕРЕЗ fail() (восемнадцатый круг, №5): позиция уже менялась —
                # сырой Incident оставлял журнал без MIXED.
                fail(f'{name}: рассинхрон не компенсирован своей ногой — ручная сверка')
            total_unp += diff; total_abs += abs(diff)
            resume_unp[name] = diff
        if total_abs > lim + TOL:
            hook('mixed'); _atomic(state_path, st)
            raise Incident('восстановление: |непарная| выше предела — состояние MIXED, ручная сверка')
        st['done'] = []
        st['partial'] = {}
        acc = {}
        for lot in plan:
            got = src_prog.get(lot['src'], 0) - acc.get(lot['src'], 0)
            if got >= lot['units']:
                st['done'].append(f"{lot['src']}:{lot['step']}")
                acc[lot['src']] = acc.get(lot['src'], 0) + lot['units']
        # ПРОГРЕСС ВНУТРИ ЛОТА НЕ ВЫБРАСЫВАЕТСЯ (семнадцатый круг, №1): проданные 3 из 10
        # при повторе лота продавались бы заново — источник уходил в короткую на повтор.
        for name, spec in legs.items():
            for instr, units, u in spec['src']:
                left = src_prog.get(instr, 0) - acc.get(instr, 0)
                if left > 0:
                    st['partial'][instr] = left
        st['executed_usd'] = max(st.get('executed_usd', 0.0),
            sum(src_prog.get(i, 0)*u for name, spec in legs.items() for i, n, u in spec['src']))
        _atomic(state_path, st)

    unp = {name: 0.0 for name in legs}
    if resume_unp:
        # ОСТАТОК НЕПАРНОЙ ДЕЛЬТЫ ПЕРЕЖИВАЕТ ВОССТАНОВЛЕНИЕ (семнадцатый круг, №2):
        # обнуление освобождало лимит §8б, и новый проход строил разрыв поверх старого.
        unp.update(resume_unp)
    dst_bought = {}
    try:
        _run_lots(broker, plan, st, state_path, lim, unp, dst_bought, fail, _M, journal)
    except Incident:
        raise
    except Exception as ex:
        fail(f'исключение адаптера: {ex!r}')

    for name, spec in legs.items():
        if abs(unp[name]) > spec['dst'][1] + TOL:
            fail(f'{name}: финальная |непарная| выше цены одной доли')
    # полная сверка фактической книги с планом перехода — сбой самого ЧТЕНИЯ после
    # исполненного перехода не смеет оставить журнал в OPEN (восемнадцатый круг, №5)
    try:
        now = broker.net_positions()
    except Exception as ex:
        fail(f'финальная сверка: позиции недоступны ({ex}) — исход недоказуем')
    snap = st['snapshot']
    for name, spec in legs.items():
        di, dp = spec['dst'][0], spec['dst'][1]
        planned_usd = sum(n*u for _, n, u in spec['src'])
        got_usd = (now.get(di, 0) - snap.get(di, 0))*dp
        got_units = now.get(di, 0) - snap.get(di, 0)
        if got_units < 0:
            fail(f'{di}: короткая целевая позиция после перехода')
        if abs(got_usd - planned_usd) > dp + TOL:
            fail(f'{di}: целевая позиция расходится с планом на ${abs(got_usd-planned_usd):,.0f}')
        for instr, units, u in spec['src']:
            if snap.get(instr, 0) - now.get(instr, 0) != units:
                fail(f'{instr}: закрыто не по плану')
    # ПОЛНАЯ СВЕРКА СЧЁТА, А НЕ ТОЛЬКО ПЛАНОВЫХ ИНСТРУМЕНТОВ (девятнадцатый круг, №12):
    # позиция, появившаяся ВО ВРЕМЯ перехода по инструменту вне плана (ручная или чужая
    # заявка после стартового барьера), не смеет пройти в COMPLETE — дальше книга контура
    # её выбросит, и она останется неуправляемой при формально завершённом переходе.
    _planned_names = ({spec['dst'][0] for spec in legs.values()}
                      | {i for spec in legs.values() for i, _, _ in spec['src']})
    for _instr in sorted(set(list(now) + list(snap))):
        if _instr in _planned_names:
            continue
        if abs(float(now.get(_instr, 0)) - float(snap.get(_instr, 0))) > 1e-9:
            fail(f'{_instr}: позиция изменилась во время перехода '
                 f'({snap.get(_instr, 0)} -> {now.get(_instr, 0)}) вне плана — '
                 f'COMPLETE запрещён, ручная сверка')
    try:
        g = broker.gross()
    except Exception as ex:
        fail(f'финальная сверка: gross недоступен ({ex}) — исход недоказуем')
    if g > CLOSE_CAP + 1e-9:
        fail(f'плечо на закрытии {g:.4f} > {CLOSE_CAP}', cancel=False)
    st['log'].append(('complete', tid, g)); _atomic(state_path, st)
    # КНИГА ПЕРЕДАЁТСЯ ДО ЗАПИСИ COMPLETE. Прежде порядок был обратным, и сбой передачи
    # оставлял журнал с завершённым переходом при неизменённом состоянии ежедневного
    # контура: два источника истины расходились НАВСЕГДА, потому что COMPLETE повторно не
    # пишется. Теперь при неудаче передачи COMPLETE не фиксируется вовсе, и переход
    # остаётся незавершённым — состояние, из которого есть штатный выход.
    try:
        hand_over_book(broker, from_route, to_route)
    except Exception as _ex:
        hook('mixed')
        # ТРЕВОГА ФАЙЛОМ (девятнадцатый круг, №13): публикация могла пройти ЧАСТИЧНО
        # (книга записана, route.txt — нет); автопилот журнал МР не читает — без файла
        # он продолжил бы торговать при MIXED в нормативном журнале.
        _al = _alarm_transition(asof, f'книга НЕ передана после исполненного перехода '
                                      f'{from_route}->{to_route} ({_ex}); журнал МР — MIXED')
        raise Incident(f'книга НЕ передана ежедневному контуру ({_ex}); COMPLETE НЕ записан, '
                       f'переход остаётся незавершённым — состояние MIXED, ручная сверка{_al}')
    if not hook('complete'):
        hook('mixed')
        # route.txt И КНИГА УЖЕ ОПУБЛИКОВАНЫ (девятнадцатый круг, №13): ежедневный контур
        # видит согласованное состояние и торговал бы дальше, пока нормативный журнал МР
        # говорит MIXED. Автопилот читает только ~/.addfut/ALARM-* — тревога ставится ТУТ.
        _al = _alarm_transition(asof, f'журнал МР отклонил COMPLETE перехода '
                                      f'{from_route}->{to_route} tid={tid} ПОСЛЕ публикации '
                                      f'книги и route.txt — состояние MIXED')
        raise Incident('журнал отклонил COMPLETE — книга переведена, состояние MIXED, '
                       'ручная сверка' + _al)
    return dict(status='COMPLETE', gross_close=g, lots=len(st['done']),
                unpaired_usd=sum(unp.values()), tid=tid)


def _alarm_transition(asof, reason):
    """ТРЕВОГА ПЕРЕХОДА В КАТАЛОГЕ АВТОПИЛОТА (девятнадцатый круг, №13): MIXED после
    публикации книги/route.txt не виден ежедневному контуру — сверка проходит, а
    нормативный журнал МР автопилот не читает. Файл ALARM-* останавливает автопилот
    целиком (О-5). Возвращает '' при успехе, иначе — текст в сообщение инцидента:
    молча несписанная тревога хуже отсутствия функции."""
    try:
        import sys as _s, os as _o
        _lv = _o.path.join(_o.path.dirname(_o.path.abspath(__file__)), 'live')
        if _lv not in _s.path:
            _s.path.insert(0, _lv)
        import state as _STa
        p = _STa.lock_dir() / f'ALARM-transition-{asof}.txt'
        p.write_text(f'{reason}; ручной разбор (О-5)\n', encoding='utf-8')
        return ''
    except Exception as ex:
        return f' | ТРЕВОГА НЕ ЗАПИСАНА ({ex}) — остановить автопилот вручную'


def hand_over_book(broker, from_route, to_route):
    """Передать книгу ежедневному контуру после перевода маршрута.

    ОТДЕЛЬНОЙ ФУНКЦИЕЙ — чтобы связку «переход -> ежедневная торговля» можно было ПРОВЕРИТЬ,
    а не пересказать. Пока передача была вложена в тело исполнителя, единственный способ
    проверить её в стенде состоял бы в повторении её кода у себя, то есть в проверке
    собственного пересказа вместо настоящего пути.

    Книга берётся у БРОКЕРА: после перехода истина именно там. Но позиции говорят, ЧТО есть,
    и не говорят, что осталось СДЕЛАТЬ, поэтому незавершённый ролл переносится отдельно.
    Путь книги — тот же, что у ежедневного контура: иначе источник истины раздваивается
    ровно в момент, когда брокер уже переведён.
    """
    import os as _os2, sys as _sys2
    _lv = _os2.path.join(_os2.path.dirname(_os2.path.abspath(__file__)), 'live')
    if _lv not in _sys2.path:
        _sys2.path.insert(0, _lv)
    import dataclasses as _dc2
    import state as _ST2
    import feed as _FD2
    from daily import Book as _BF, BookE as _BE
    _cls = _BE if to_route == 'E' else _BF
    # ОШИБКА ЧТЕНИЯ ИСХОДНОЙ КНИГИ — ОТКАЗ ПЕРЕДАЧИ, а не «отложенного ролла нет». Прежнее
    # except: False молча стирало незавершённое обязательство при любом сбое чтения.
    _pb, _prev_sess, _ = _ST2.load(_ST2.book_path(from_route),
                                   _BF if from_route == 'F' else _BE)
    _prev_pending = bool(getattr(_pb, 'roll_pending', False)) if _pb else False
    # d_fix И СОСТОЯНИЯ СИГНАЛА ПЕРЕЖИВАЮТ ПЕРЕХОД (двенадцатый круг, №2): книга Ф с
    # d_fix=0 занижала вклад ZN в плечо закрытия вплоть до исключения ноги Б из триггера
    # капа; пустые prev_st_* выдавали обе ноги за переключившиеся. Состояния берутся из
    # книги ПРЕЖНЕГО маршрута (они маршрутно-независимы), дюрационная база — из живой
    # доходности той же формулой, что и в расчётах.
    _st_eq = getattr(_pb, 'prev_st_eq', None) if _pb else None
    _st_bd = getattr(_pb, 'prev_st_bd', None) if _pb else None
    _dfx = 0.0
    if to_route == 'F':
        # Иерархия источников d_fix: прежняя книга Ф (если была) -> живая доходность через
        # брокера -> явная пометка в записи. Требовать .ib у ЛЮБОГО брокера нельзя — стабы
        # самопроверки его не имеют, и первый же прогон выпуска это показал.
        try:
            _oldF, _, _ = _ST2.load(_ST2.book_path('F'), _BF)
            if _oldF is not None and getattr(_oldF, 'd_fix', 0.0):
                _dfx = float(_oldF.d_fix)
        except Exception:
            pass
        if not _dfx and getattr(broker, 'ib', None) is not None:
            import feed as _FD2b
            _y, _ = _FD2b.yield_pct(broker.ib, _FD2b.exchange_today())
            _dfx = _FD2b.dref_from_yield(_y / 100.0)
    _bk = _ST2.book_from_broker(_cls, broker.net_positions(), to_route,
                                roll_pending=_prev_pending, d_fix=_dfx,
                                st_eq=_st_eq, st_bd=_st_bd)
    # ВРЕМЕННАЯ ИСТОРИЯ НЕ СТИРАЕТСЯ. Прежде книга сохранялась с session_no=0 и пустой
    # last_session: защита «сессия не новее последней» и отказ по незамкнутой сессии
    # отключались разом, и сразу после перехода разрешалась ПОВТОРНАЯ торговля тем же днём —
    # на новом маршруте prev_st_* пусты, обе ноги считались переключившимися и получали
    # дополнительные заявки. Дата ставится биржевая, замыкание объявляется предварительным:
    # день перехода закрывается штатным --close, и до него торговля запрещена.
    _today = _FD2.exchange_today().strftime('%Y-%m-%d')
    _bk = _dc2.replace(_bk, last_session=_today, close_provisional=True)
    _ST2.save(_ST2.book_path(to_route), _bk, to_route, int(_prev_sess or 0) + 1,
              note=f'книга принята от перехода {from_route}->{to_route}, '
                   f'сессия {_today} закрыта для торговли до замыкания')
    # ФАЙЛ МАРШРУТА (одиннадцатый круг, №4): автопилот читает route.txt, и без этой записи
    # после Ф->Е он продолжал бы вызывать сессии маршрута Ф — комментарий в автопилоте
    # обещал запись, которой не существовало.
    _rt = _ST2.lock_dir() / 'route.txt'
    _fd, _tmp = __import__('tempfile').mkstemp(dir=str(_rt.parent))
    with _os2.fdopen(_fd, 'w', encoding='utf-8') as _f:
        _f.write(to_route)
        _f.flush(); _os2.fsync(_f.fileno())
    _os2.replace(_tmp, _rt)
    return _bk


def _order_gate(st, broker, fail, where=''):
    """RUNTIME-ЛИМИТ ПЕРЕД КАЖДОЙ ЗАЯВКОЙ (девятнадцатый круг, №8): прежняя проверка
    стояла только перед основной продажей — при 389 занятых заявках продажа №390 проходила,
    а покупка №391 подавалась БЕЗ проверки; компенсации и восстановительные заявки resume
    обходили ворота вовсе. Брокер отверг бы покупку после исполненной продажи источника —
    непарная позиция и MIXED ровно на границе, ради которой лимит и введён."""
    if (len(st['order_ids']) >= ORDERS_PER_DAY
            and not getattr(broker, 'counting', False)):
        fail(f'дневной лимит {ORDERS_PER_DAY} заявок исчерпан в исполнении '
             f'({len(st["order_ids"])}) перед заявкой {where} — переход останавливается')


def _run_lots(broker, plan, st, state_path, lim, unp, dst_bought, fail, _M=None, journal=None):
    for lot in plan:
        if _M is not None and journal is not None:
            _M.canonical_journal(journal)         # перед каждым лотом: эпоха и личность журнала
        key = f"{lot['src']}:{lot['step']}"
        if key in st['done']: continue
        remaining = lot['units']; noop = 0
        # ЧАСТИЧНЫЙ ПРОГРЕСС ПРЕРВАННОГО ЛОТА (семнадцатый круг, №1): проданное до обрыва
        # вычитается из остатка первого незавершённого лота этого источника — повтор
        # целиком продавал бы уже исполненную часть и уводил источник в короткую.
        _part = st.get('partial', {}).get(lot['src'], 0)
        if _part:
            _take = min(_part, remaining)
            remaining -= _take
            st['partial'][lot['src']] = _part - _take
            st['log'].append(('resume_partial', lot['src'], _take))
            _atomic(state_path, st)
            if remaining <= 0:
                st['done'].append(key)
                _atomic(state_path, st)
                continue
        while remaining > 0:
            head = min(lot['dprice'], lot['unit_usd'])/2.0 + 1.0
            # СУММА МОДУЛЕЙ, а не модуль суммы (№25): противоположные разрывы двух ног
            # взаимно сокращались, и «свободное место» рисовалось там, где обе ноги уже
            # разорваны на лимит каждая.
            used = sum(abs(v) for v in unp.values())
            if lim - used - head < min(lot['dprice'], lot['unit_usd']):
                fail(f'нет места под лимитом §8б: занято ${used:,.0f} из ${lim:,.0f} — '
                     f'заявка не подаётся')
            avail = lim - used - head
            k_dst = max(1, int(avail // lot['dprice']))          # выравнивание по зерну ЦЕЛИ с зазором
            desired = k_dst*lot['dprice'] - unp[lot['leg']]      # продажа целится в кванты МИНУС текущий остаток
            k_src = int(avail // lot['unit_usd'])
            if (used + lot['unit_usd'] > lim + TOL) and not _frac(lot['src']):
                # МЕСТО ПОД ЦЕЛУЮ ДОЛЮ ИСТОЧНИКА ПРОВЕРЯЕТСЯ ДО ЗАЯВКИ (семнадцатый круг,
                # №2, вскрыто стендом остатка): прежний «or 1» продавал единицу СВЕРХ
                # лимита §8б, и Incident приходил уже после ушедшей брокеру заявки.
                # Мерило — САМ лимит, а не avail с зазором цели: зазор относится к
                # выравниванию покупки и законную продажу единицы в лимит не запрещает.
                fail(f'нет места под лимитом §8б даже для одной доли источника '
                     f'{lot["src"]}: занято ${used:,.0f} из ${lim:,.0f} — заявка не подаётся')
            step = max(1, min(remaining, int(round(desired/lot['unit_usd'])) or 1,
                              k_src or 1))
            # ОСТАТОК МЕНЬШЕ ЕДИНИЦЫ НЕ ОКРУГЛЯЕТСЯ ВВЕРХ. При remaining = 0,5 шаг выходил
            # равным 1: исполнитель продавал целую долю вместо половины и уводил источник
            # в КОРОТКУЮ позицию — ровно то, от чего защищает планировщик, но уже в цикле.
            if _frac(lot['src']):
                step = min(step, remaining)
            # RUNTIME-ЛИМИТ (восемнадцатый круг, №12; девятнадцатый, №8 — перед КАЖДОЙ
            # заявкой): partial-исполнения плодят итерации сверх любой предстартовой
            # оценки — упор фиксируется ДО заявки, с MIXED, а не отказами IB посреди книги.
            _order_gate(st, broker, fail, f'продажа {lot["src"]}')
            oid, f = broker.sell_units(lot['src'], step)
            st['order_ids'].append(oid)
            try:
                sold = _int_fill(f, lot['src'])
            except Incident:
                st['executed_usd'] += abs(f)*lot['unit_usd']
                fail(f'{lot["src"]}: дробное исполнение {f} — сверка с брокером')
            unp[lot['leg']] += sold*lot['unit_usd']; st['executed_usd'] += sold*lot['unit_usd']
            if sold > step: fail('исполнено больше заявки (продажа) — сверка с брокером')
            st['log'].append(('sell', lot['src'], sold)); _atomic(state_path, st)
            if sum(abs(v) for v in unp.values()) > lim + TOL:      # СТРОГО в пределах утверждённого лимита
                fail('|непарная дельта| выше утверждённого лимита')
            want = max(0, int(round(unp[lot['leg']]/lot['dprice'])))   # покупка кроет НАКОПЛЕННЫЙ остаток ноги
            if want == 0:
                # НУЛЕВАЯ ЗАЯВКА НЕ ПОДАЁТСЯ (одиннадцатый круг, №9): остаток меньше
                # половины зерна цели копится до следующего шага; живой адаптер нулевую
                # заявку отверг бы, и уже проданный источник повисал бы в MIXED.
                st['log'].append(('buy_skip_zero', lot['dst'], 0)); _atomic(state_path, st)
                if sold == 0:
                    noop += 1
                    if noop >= 2: fail('нулевой прогресс два шага подряд')
                    continue
                noop = 0
                remaining -= sold if sold > 0 else 0
                continue
            _order_gate(st, broker, fail, f'покупка {lot["dst"]}')      # №8: и перед покупкой
            oid2, f2 = broker.buy_units(lot['dst'], want)
            st['order_ids'].append(oid2)
            try:
                bought = _int_fill(f2, lot['dst'])
            except Incident:
                st['executed_usd'] += abs(f2)*lot['dprice']
                fail(f'{lot["dst"]}: дробное исполнение {f2} — сверка с брокером')
            st['executed_usd'] += bought*lot['dprice']
            dst_bought[lot['dst']] = dst_bought.get(lot['dst'], 0) + bought
            if bought > want:
                unp[lot['leg']] -= bought*lot['dprice']
                fail('исполнено больше заявки (покупка) — сверка с брокером')
            unp[lot['leg']] -= bought*lot['dprice']
            st['log'].append(('buy', lot['dst'], bought)); _atomic(state_path, st)
            if sold == 0 and bought == 0:
                noop += 1
                if noop >= 2: fail('нулевой прогресс два шага подряд')
                continue
            noop = 0
            u = unp[lot['leg']]
            if u > lot['dprice']/2 + TOL:       # недобор dst своей ноги — докупка
                _order_gate(st, broker, fail, f'компенсация-покупка {lot["dst"]}')   # №8
                oid3, f3 = broker.buy_units(lot['dst'], int(round(u/lot['dprice'])))
                st['order_ids'].append(oid3); unp[lot['leg']] -= _int_fill(f3, lot['dst'])*lot['dprice']
                st['log'].append(('compensate_buy', lot['dst'], f3)); _atomic(state_path, st)
            elif u < -lot['dprice']/2 - TOL:    # перебор — обратная продажа своей ноги (не глубже купленного)
                _want = min(int(round(-u/lot['dprice'])), dst_bought.get(lot['dst'], 0))
                _order_gate(st, broker, fail, f'компенсация-продажа {lot["dst"]}')   # №8
                oid3, f3 = broker.sell_units(lot['dst'], _want)
                st['order_ids'].append(oid3); _fs = _int_fill(f3, lot['dst']); unp[lot['leg']] += _fs*lot['dprice']
                dst_bought[lot['dst']] = dst_bought.get(lot['dst'], 0) - _fs
                st['log'].append(('compensate_sell', lot['dst'], f3)); _atomic(state_path, st)
            if abs(unp[lot['leg']]) > lot['dprice'] + TOL:
                fail('пара не выровнена компенсацией — ручная сверка')
            if broker.minutes_since(key) > TIMEOUT_MIN:
                fail('тайм-аут пары 15 минут')
            g = broker.gross()
            if g > INTRA_CAP + 1e-9:
                fail(f'внутрисессионный gross {g:.4f} > {INTRA_CAP}')
            remaining -= sold if sold > 0 else 0
        st['done'].append(key); _atomic(state_path, st)
