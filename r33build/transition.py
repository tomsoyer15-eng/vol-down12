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


def _machine_pin():
    """Пин торгового счёта: окружение ЛИБО account.txt в каталоге замка (двадцать первый
    круг, №7). Прежде сверка счёта замера включалась только при заданной переменной
    окружения — то есть в бою (автопилот её экспортирует, но оператор вручную мог и не)
    молчала, и замер с чужого счёта считался живым."""
    import os as _o
    v = (_o.environ.get('ADDFUT_ACCOUNT') or '').strip()
    if v:
        return v
    try:
        import sys as _s, os as _os2
        _lv = _os2.path.join(_os2.path.dirname(_os2.path.abspath(__file__)), 'live')
        if _lv not in _s.path:
            _s.path.insert(0, _lv)
        import state as _ST
        return (_ST.lock_dir() / 'account.txt').read_text(encoding='utf-8').strip()
    except OSError:
        return ''


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
        # ОТСУТСТВИЕ ЗАМЕРА — ОТКАЗ, А НЕ КОНСТАНТЫ (двадцать третий круг, №5). Прежде
        # «файла нет» молча включало модельные константы, и это давало ИЗВРАЩЁННЫЙ стимул:
        # НЕПОЛНЫЙ замер блокирует переход воротами серий, а УДАЛЁННЫЙ — открывает его.
        # Достаточно было переименовать margins_live.json, чтобы обойти обязательное живое
        # измерение и пойти по фиктивному запасу при повышенном house-требовании.
        # Стендам и свежей машине — явная калитка, как у даты перехода (№1).
        import os as _os2
        if _os2.environ.get('ADDFUT_MARGINS_CONST_OK') == '1':
            return {}
        raise Incident(f'{p}: замера маржи нет — переход по модельным константам запрещён '
                       f'(house-требование может быть выше, запас оказался бы фиктивным). '
                       f'Запустить first_connect; стендам — ADDFUT_MARGINS_CONST_OK=1')
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
    # ПИН ОБЯЗАТЕЛЕН ДЛЯ ЖИВОГО ЗАМЕРА (двадцать второй круг, №5): обе сверки счёта были
    # условны на «пин задан», и при пустом _machine_pin() файл с ЛЮБЫМ _meta.account
    # принимался — переход мог пойти по house margin чужого счёта (удаление account.txt
    # не удаляет старый margins_live.json). Нет пина — замер непроверяем, отказ.
    _pin0 = _machine_pin()
    if not _pin0:
        raise Incident(f'{p}: торговый счёт не пинован (нет ADDFUT_ACCOUNT и account.txt) '
                       f'— принадлежность замера маржи непроверяема, переход запрещён')
    if not (meta.get('account') or ''):
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
    _pin = _pin0                       # пин уже проверен выше: пустого здесь не бывает
    _macct = (meta or {}).get('account') or ''
    if _macct != _pin:
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


def book_margin(book, reg, prices=None, live=None):
    """Занятая маржа по фактической книге. Фьючерсы — требование на контракт;
    ETF — поддерживающее требование от стоимости позиции (нужна цена единицы).

    ОДНО ПОКОЛЕНИЕ ЗАМЕРА НА ВЕСЬ РАСЧЁТ (двадцатый круг, №8). Прежде _live_margins()
    вызывался ЗАНОВО для каждого инструмента, а preflight звал book_margin ещё и для двух
    вариантов книги — итого файл перечитывался многократно, и атомарная замена
    (first_connect) между чтениями давала смесь: низкая маржа ES из поколения A и низкая
    маржа MES/ZN из поколения B. Сумма выходила ниже ОБОИХ настоящих снимков, и О-3
    разрешал книгу больше допустимой. Замер читается один раз и передаётся вниз.
    """
    total = 0.0
    _lm = _live_margins() if live is None else live
    for instr, units in book.items():
        if instr not in reg:
            raise Incident(f'{instr}: инструмента нет в реестре — маржа не считается')
        if reg[instr]['sec_type'] == 'FUT':
            if instr not in FUT_MARGIN:
                raise Incident(f'{instr}: нет модельного требования маржи')
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
    # ОДИН СНИМОК ЗАМЕРА НА ОБЕ КНИГИ (двадцатый круг, №8): иначе «худшая из двух» могла
    # оказаться смесью двух поколений файла, то есть книгой, которой не существовало.
    _live = _live_margins()
    margin = max(book_margin(book, reg, prices, live=_live),
                 book_margin(book_exec, reg, prices, live=_live))
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
# Допуск расхождения капитала перехода с NLV брокера (двадцать первый круг, №1).
# Операционная величина, не параметр стратегии: цель — поймать подставленное «не то»
# число, а не расхождение из-за движения счёта между чтениями.
CAPITAL_TOL = 0.02
CLOSE_CAP = 2.00
TIMEOUT_MIN = 15
TOL = 1e-6

def compensation_ok(filled, want, ostatok, dprice):
    """Компенсация закрыта ЧЕСТНО (двадцатый круг, №2; форма — двадцать первый).

    ОДНОЙ функцией сразу две проверки: исполнение совпало с заказанным И остаток пары не
    превышает половины кванта цели. Раздельно их мутировать бессмысленно — они маскируют
    друг друга: подмена сверки исполнения оставляет остаток, который тут же ловит допуск,
    и мутация выглядит «пойманной» при мёртвой первой линии. Прежнее поведение (никакой
    сверки и допуск в ЦЕЛУЮ единицу) воспроизводится одной подменой, как и должно быть.

    Возвращает пустую строку при успехе, иначе причину отказа.
    """
    if filled != want:
        return f'заказано {want}, исполнено {filled} — недостача цели'
    if abs(ostatok) > dprice/2.0 + TOL:
        return (f'остаток ${abs(ostatok):,.0f} выше половины единицы цели '
                f'${dprice/2.0:,.0f}')
    return ''


def comp_fill_ok(filled, want):
    """Совместимость: прежнее имя первой линии. Логика — в compensation_ok."""
    return filled == want


def pair_tol(dprice):
    """Допустимый остаток непарной дельты — ПОЛОВИНА кванта цели (двадцатый круг, №2).

    Отдельной функцией, чтобы мутация могла ударить ровно в неё, а не в общий TOL:
    раздувание общего допуска ловилось бы посторонними отказами и ничего не доказывало.
    Округление покупки к целому кванту больше половины оставить не может; целая единица —
    уже недостача цели (~1% NLV при минимальном размере перехода), которую ежедневная
    полоса 10% не исправляет.
    """
    return dprice/2.0 + TOL


def resume_same_session(st, asof):
    """Продолжается ли переход В ТОЙ ЖЕ биржевой сессии (двадцатый круг, №1).

    Отдельной функцией по той же причине: защита обязана иметь парную мутацию.
    """
    return (st.get('asof') or '') == str(asof or '')


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
    # ASOF — ЭТО СЕГОДНЯ, А НЕ АРГУМЕНТ ДОВЕРИЯ (двадцать второй круг, №1). Вся дневная
    # дисциплина — ворота окна, барьер «resume в той же сессии», праздники — считалась от
    # asof, который задаёт ВЫЗЫВАЮЩИЙ. Вчерашний asof отключал текущие часы (край окна не
    # возвращался «для прошлой даты») и разрешал resume старого плана по старым ценам.
    # Дата принудительно сверяется с биржевым «сегодня»; ADDFUT_ASOF_OVERRIDE=1 — явная
    # калитка для стендов и разборов задним числом, в бою переменная не выставляется
    # (env_guard автопилота запрещает переопределения ADDFUT_* на торговом входе).
    import os as _oa0
    if _oa0.environ.get('ADDFUT_ASOF_OVERRIDE') != '1':
        import sys as _sa, os as _oa
        _lv0 = _oa.path.join(_oa.path.dirname(_oa.path.abspath(__file__)), 'live')
        if _lv0 not in _sa.path:
            _sa.path.insert(0, _lv0)
        import feed as _FD0
        _today0 = _FD0.exchange_today().strftime('%Y-%m-%d')
        if str(asof) != _today0:
            raise Incident(f'asof={asof!r} не совпадает с биржевым сегодня ({_today0}) — '
                           f'переход задним или будущим числом запрещён: от asof считаются '
                           f'окно, resume и хронология журнала (стендам — '
                           f'ADDFUT_ASOF_OVERRIDE=1)')
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
    _st_now = _M.derive_state(journal, __import__('datetime').date.fromisoformat(asof))
    _r_now = _st_now[0]
    if _r_now != from_route:
        raise Incident(f'from_route={from_route} не совпадает с маршрутом журнала {_r_now}')
    # MIXED И АНОМАЛИИ ЧИТАЮТСЯ, А НЕ ТОЛЬКО МАРШРУТ (двадцать второй круг, №2). Прежде
    # бралось лишь [0], и исполнитель заходил поверх открытого TRANSITION_MIXED: hook('open')
    # для прежнего tid идемпотентно отвечал True ещё ДО проверки mixed, resume снова
    # продавал и покупал, hand_over_book публиковал книгу — прямой обход О-5 при журнале,
    # который продолжал утверждать «состояние не разобрано».
    if _st_now[2]:
        raise Incident('журнал МР держит открытый TRANSITION_MIXED — состояние не '
                       'разобрано (О-5), любое исполнение запрещено до RESOLVED')
    if len(_st_now) > 3 and _st_now[3]:
        raise Incident(f'журнал МР несёт аномалии {_st_now[3][:3]} — исполнение запрещено '
                       f'до ручного разбора (О-5)')
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
    # КАПИТАЛ СВЕРЯЕТСЯ С БРОКЕРОМ (двадцать первый круг, №1). Прежде capital, unit_usd и
    # dprice полностью задавал вызывающий: план, лимит §8б (1% капитала), preflight-маржа,
    # число долей цели и финальная сверка считались по ОДНИМ И ТЕМ ЖЕ непроверенным
    # числам, то есть переход доказывал сам себя. Капитал проверяем сразу — брокер его
    # знает; про цены см. признанный предел ниже и §12.
    # FAIL-CLOSED (двадцать второй круг, №4): прежде проверка шла только при наличии
    # метода net_liquidation — брокер без него (в т.ч. подставные брокеры выпуска)
    # проходил без сверки вовсе, и завышенный capital расширял owner-cap и запас. Метод
    # обязателен: интерфейс брокера перехода без NLV не считается брокером.
    _nlv_fn = getattr(broker, 'net_liquidation', None)
    if not callable(_nlv_fn):
        raise Incident('брокер не отдаёт net_liquidation — капитал перехода непроверяем, '
                       'исполнение запрещено (сверка capital с NLV обязательна)')
    if True:
        try:
            _nlv = float(_nlv_fn())
        except Exception as ex:
            raise Incident(f'NLV у брокера недоступен ({ex}) — капитал перехода '
                           f'непроверяем, исполнение запрещено')
        if not (_nlv == _nlv and _nlv > 0):
            raise Incident(f'NLV у брокера {_nlv!r} — не число, капитал перехода непроверяем')
        if abs(_nlv - float(capital)) > CAPITAL_TOL * _nlv:
            raise Incident(
                f'капитал перехода ${float(capital):,.0f} расходится с NLV брокера '
                f'${_nlv:,.0f} более чем на {CAPITAL_TOL:.0%}: по нему считаются лимит '
                f'§8б, маржа и число долей цели — исполнение запрещено')
    # ВОРОТА СЧИТАЮТСЯ ОТ МЕНЬШЕГО ИЗ ДВУХ ЧИСЕЛ (двадцать третий круг, №4). Допуск 2%
    # проверяет БЛИЗОСТЬ, но все ворота дальше считались по числу ВЫЗЫВАЮЩЕГО, а оно может
    # быть выше фактического NLV на весь допуск. Два денежных пути: (а) порог §8 проверен
    # на строке выше по capital — при фактических $2,95 млн и переданных $3,008 млн вход в
    # Ф проходил, хотя счёт НИЖЕ жёсткого порога; (б) лимит непарной дельты при NLV $10 млн
    # и capital $10,19 млн становился 1,019% ФАКТИЧЕСКОГО счёта вместо 1%.
    _cap_eff = min(float(capital), _nlv)
    if to_route == 'F' and _cap_eff < MIN_NLV_F and not emergency:
        raise Incident(
            f'NLV брокера ${_nlv:,.0f} ниже порога маршрута Ф ${MIN_NLV_F:,.0f} (§8) — '
            f'переданный капитал ${float(capital):,.0f} прошёл проверку порога только '
            f'за счёт допуска {CAPITAL_TOL:.0%}; требуется решение заказчика')
    lim = unpaired_limit(legs, _cap_eff, grant_limit)
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

    def _mixed(reason=''):
        """MIXED + ФАЙЛ ТРЕВОГИ, ВСЕГДА (двадцать третий круг, №14). Утверждение «любой
        MIXED ставит ALARM» было неверным: _alarm_transition() звался только из fail(),
        а hook('mixed') писался напрямую ещё в семи местах — сбой open_orders до preview
        на resume, обрыв отмены, исключение preview после возможного изменения книги,
        превышение остатка resume. Автопилот журнал МР НЕ читает, поэтому после
        освобождения книжного замка он мог продолжить торговлю поверх разорванной позиции.
        Падение самого hook тоже не должно съедать тревогу — файл ставится в любом случае.
        """
        try:
            _ok = hook('mixed')
        except Exception as _ex:
            _alarm_transition(asof, f'MIXED, но журнал МР его не принял ({_ex!r}): {reason}')
            raise
        _alarm_transition(asof, f'переход {from_route}->{to_route} в MIXED: {reason}'[:400])
        return _ok


    if os.path.exists(state_path):
        st = json.load(open(state_path))
        if st.get('tid') != tid:
            raise Incident('состояние не соответствует переходу (transition_id) — ручная сверка')
        # RESUME — ТОЛЬКО В ТОЙ ЖЕ БИРЖЕВОЙ СЕССИИ (двадцатый круг, №1).
        #
        # tid включает capital и весь plan (unit_usd, dprice), а resume требует ТОЧНОГО
        # совпадения tid — значит передать свежие NLV и цены нельзя по построению: выйдет
        # «состояние не соответствует переходу». Со старыми же по вчерашним деньгам
        # считаются лимит §8б (1% капитала), маржинальный preflight, число долей цели и
        # финальная сверка. После ночного движения исполнитель способен превысить
        # фактический 1%, купить неверное количество и получить COMPLETE по устаревшим
        # ценам. Плана переспланировать нельзя — источник уже частично продан, — поэтому
        # ограничивается СТАРЕНИЕ: продолжать вчерашний переход запрещено, разбор ручной.
        if resume and not resume_same_session(st, asof):
            raise Incident(
                f'resume перехода из другой сессии: состояние снято {st.get("asof")!r}, '
                f'сейчас {asof!r}. Продолжение шло бы по капиталу и ценам того дня '
                f'(лимит §8б, маржа, доли цели, финальная сверка), а переспланировать '
                f'нельзя — источник уже продан частично. Ручной разбор (О-5)')
        if not resume and (st.get('opened') or st.get('done') or st.get('executed_usd', 0.0) > TOL or st.get('order_ids')):
            raise Incident('переход уже открыт/имеет прогресс — повторный запуск только с resume=True')
        if not resume:                                         # свежий заход без прогресса: снапшот пересоздаётся
            st = dict(tid=tid, asof=str(asof or ''), postponed=st.get('postponed', 0),
                      done=[], executed_usd=0.0, order_ids=[],
                      snapshot=broker.net_positions(), log=[])
            _atomic(state_path, st)
    else:
        st = dict(tid=tid, asof=str(asof or ''), postponed=0, done=[], executed_usd=0.0,
                  order_ids=[], snapshot=broker.net_positions(), log=[])
        _atomic(state_path, st)
    if resume:
        # ОТМЕНЫ РАНЬШЕ PREVIEW (восемнадцатый круг, №4): отказ preview со старой живой
        # заявкой оставлял её исполняться весь «отложенный» период — вне траектории §8б.
        # fail() здесь ещё не определён; неснятое при известном прогрессе — честный MIXED.
        try:
            _pre_open = list(broker.open_orders())
        except Exception as ex:
            _mixed('исход не разобран'); _atomic(state_path, st)
            raise Incident(f'resume: запрос заявок до preview не выполнен ({ex}) — MIXED')
        for _oid in _pre_open:
            try:
                _r = broker.cancel_order(_oid)
            except Exception as ex:
                _mixed('исход не разобран'); _atomic(state_path, st)
                raise Incident(f'resume: отмена {_oid} до preview оборвана ({ex}) — MIXED')
            if not (_r is True or (isinstance(_r, dict) and _r.get('terminal'))):
                _mixed('исход не разобран'); _atomic(state_path, st)
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
            _mixed('исход не разобран'); _atomic(state_path, st)
            raise Incident(f'margin preview оборван ({ex}) при возможно изменённой книге — '
                           f'состояние MIXED, ручная сверка')
        raise Incident(f'margin preview оборван ({ex}) — переход не начат')
    if not _pv:
        st['postponed'] += 1; _atomic(state_path, st)
        if st['postponed'] >= 3:
            if st['executed_usd'] > TOL or st.get('cancel_fills'):
                # cancel_fills (№11): исполнение, пойманное отменой, — прогресс; прежний
                # критерий по одному executed_usd писал бы ЛОЖНЫЙ ABORT при изменённой книге.
                _mixed('исход не разобран')
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
    # ЗАПАС СЧИТАЕТСЯ ОТ ФАКТИЧЕСКОГО СЧЁТА (№4): cushion = capital/margin, и завышенный
    # на допуск capital давал завышенный запас — те же ворота О-3 по фиктивному числу.
    mo = preflight_margin_orders(legs, plan, _cap_eff, reg, to_route, lim=lim)   # ред. 32: маржа и заявки ДО открытия
    st['preflight'] = {k: mo[k] for k in ('margin_usd', 'cushion', 'orders', 'orders_max')}
    _atomic(state_path, st)
    # КРАЙ ОБЩЕГО ОКНА — ДО ЗАПИСИ OPEN (двадцатый круг, №7; двадцать второй, №15).
    # Прежде _window_till звался ПОСЛЕ hook('open') и st['opened']=True: запуск вне окна,
    # в праздник или при сбое календаря не подавал ни одной заявки, но оставлял журнал и
    # состояние в OPEN без ABORT — свежий запуск дальше запрещён («уже захвачен»), resume
    # другой сессии тоже, и переход зависал до ручного разбора на ровном месте.
    _wt = _window_till(asof)
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
                # ИСПОЛНЕНИЕ, ПОЙМАННОЕ ОТМЕНОЙ, — СДЕЛКА (двадцать второй круг, №3).
                # Терминальный ответ с filled означает, что книга УЖЕ изменилась; в
                # resume-ветке это учтено (cancel_fills), а здесь прежде игнорировалось:
                # при lost-ack до записи executed_usd и устойчиво старом снимке позиций
                # moved==0, и состоявшееся исполнение уходило в журнал как ABORT.
                if isinstance(r, dict) and r.get('filled'):
                    st['cancel_fills'] = (float(st.get('cancel_fills', 0.0))
                                          + abs(float(r['filled'])))
                    st['log'].append(('cancel_filled_on_fail', oid, float(r['filled'])))
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
        kind = 'mixed' if (stuck or st['executed_usd'] > TOL or moved > 0
                           or st.get('cancel_fills')) else 'abort'
        try:
            # MIXED идёт через _mixed: тревога-файл ставится и когда журнал МР ПАДАЕТ,
            # и когда он отвергает запись (№14) — автопилот журнала не читает вовсе.
            ok = _mixed(msg[:200]) if kind == 'mixed' else hook(kind)
        except Exception as ex:
            _atomic(state_path, st)
            raise Incident(msg + f' | КРИТИЧНО: запись {kind.upper()} в журнал провалилась ({ex!r}) — немедленная ручная сверка книги и журнала')
        if not ok:
            _atomic(state_path, st)
            raise Incident(msg + f' | КРИТИЧНО: журнал отклонил {kind.upper()} — немедленная ручная сверка книги и журнала')
        _atomic(state_path, st)
        # MIXED = ТРЕВОГА ФАЙЛОМ ВСЕГДА (двадцать второй круг, №10). Прежде _alarm_transition
        # ставился только при сбоях публикации книги/COMPLETE; обычный MIXED (частичное
        # исполнение до handover) жил только в журнале МР, который автопилот НЕ читает:
        # после освобождения замка книги дневной контур мог подать заявки поверх позднего
        # исполнения при устойчиво старом снимке позиций у брокера.
        # тревога уже поставлена внутри _mixed (№14) — второй раз не ставим
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
                _order_gate(st, broker, fail, window_till=_wt, where=f'восстановительная покупка {di}')   # №8
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
                _order_gate(st, broker, fail, window_till=_wt, where=f'восстановительная продажа {di}')   # №8
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
            _mixed('исход не разобран'); _atomic(state_path, st)
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
        _run_lots(broker, plan, st, state_path, lim, unp, dst_bought, fail, _M, journal,
                  window_till=_wt)
    except Incident:
        raise
    except Exception as ex:
        fail(f'исключение адаптера: {ex!r}')

    for name, spec in legs.items():
        if abs(unp[name]) > pair_tol(spec['dst'][1]):    # №2: половина кванта, не целый
            fail(f'{name}: финальная |непарная| ${abs(unp[name]):,.0f} выше половины '
                 f'единицы цели ${spec["dst"][1]/2:,.0f}')
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
        if abs(got_usd - planned_usd) > pair_tol(dp):    # №2: целая единица — недостача
            fail(f'{di}: целевая позиция расходится с планом на '
                 f'${abs(got_usd-planned_usd):,.0f} (допуск — половина единицы цели '
                 f'${dp/2:,.0f}); COMPLETE запрещён')
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
    # ФИНАЛЬНЫЙ БАРЬЕР ЖИВЫХ ЗАЯВОК (двадцатый круг, №13). Стартовый барьер защищает
    # только момент ДО перехода. Нормальная ветка сверяла позиции и gross, но ни разу не
    # спрашивала open_orders: чужая или ручная заявка, появившаяся ВО ВРЕМЯ перехода и ещё
    # не исполнившаяся, позициям невидима — переход публиковал книгу и COMPLETE, а заявка
    # исполнялась позже уже вне всякого учёта. Сверка позиций такой случай поймать не
    # может по построению, поэтому нужен именно запрос заявок.
    try:
        _live_fin = list(broker.open_orders())
    except Exception as ex:
        fail(f'финальная сверка: запрос заявок не выполнен ({ex}) — исход недоказуем')
    if _live_fin:
        fail(f'живые заявки на счёте после перехода {_live_fin[:4]} — исполнятся вне '
             f'учёта, COMPLETE запрещён до разбора')
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
    # БЕСШУМНОЕ ОКНО МЕЖДУ КНИГОЙ И COMPLETE ЗАКРЫТО (двадцать третий круг, №15).
    # hand_over_book пишет целевую книгу и route.txt, и только ПОСЛЕ возврата ставится
    # hook('complete'). Убийство процесса в этом окне не выполняло ни одной ветки: брокер,
    # книга и route.txt уже согласованы, ежедневная сверка проходила и торговля шла дальше,
    # хотя нормативный журнал навсегда оставался в TRANSITION_OPEN. Метка на диске ставится
    # ДО передачи и снимается только после COMPLETE; входной барьер контура её видит.
    _hoflag = _ST2_flag = None
    try:
        import state as _ST3
        _hoflag = _ST3.lock_dir() / f'handover-inflight-{to_route}.txt'
        _hoflag.write_text(f'{asof} tid={tid} {from_route}->{to_route}: книга передаётся, '
                           f'COMPLETE ещё не записан\n', encoding='utf-8')
    except Exception:
        _hoflag = None
    try:
        hand_over_book(broker, from_route, to_route, positions=now)
    except Exception as _ex:
        _mixed('исход не разобран')
        # ТРЕВОГА ФАЙЛОМ (девятнадцатый круг, №13): публикация могла пройти ЧАСТИЧНО
        # (книга записана, route.txt — нет); автопилот журнал МР не читает — без файла
        # он продолжил бы торговать при MIXED в нормативном журнале.
        _al = _alarm_transition(asof, f'книга НЕ передана после исполненного перехода '
                                      f'{from_route}->{to_route} ({_ex}); журнал МР — MIXED')
        raise Incident(f'книга НЕ передана ежедневному контуру ({_ex}); COMPLETE НЕ записан, '
                       f'переход остаётся незавершённым — состояние MIXED, ручная сверка{_al}')
    if not hook('complete'):
        _mixed('исход не разобран')
        # route.txt И КНИГА УЖЕ ОПУБЛИКОВАНЫ (девятнадцатый круг, №13): ежедневный контур
        # видит согласованное состояние и торговал бы дальше, пока нормативный журнал МР
        # говорит MIXED. Автопилот читает только ~/.addfut/ALARM-* — тревога ставится ТУТ.
        _al = _alarm_transition(asof, f'журнал МР отклонил COMPLETE перехода '
                                      f'{from_route}->{to_route} tid={tid} ПОСЛЕ публикации '
                                      f'книги и route.txt — состояние MIXED')
        raise Incident('журнал отклонил COMPLETE — книга переведена, состояние MIXED, '
                       'ручная сверка' + _al)
    # МЕТКА ПЕРЕДАЧИ СНИМАЕТСЯ ТОЛЬКО ЗДЕСЬ — после принятого COMPLETE (№15).
    if _hoflag is not None:
        try:
            _hoflag.unlink()
        except FileNotFoundError:
            pass
    # СУММА МОДУЛЕЙ И ЗДЕСЬ (двадцать второй круг, №19): отчёт со знаковой суммой при
    # остатках +$49k/-$49k показывал unpaired_usd=0 — ложное доказательство полного
    # спаривания, хотя это два разных риска и полоса 10% может не исправить ни один.
    return dict(status='COMPLETE', gross_close=g, lots=len(st['done']),
                unpaired_usd=sum(abs(v) for v in unp.values()),
                unpaired_by_leg={k: round(v, 2) for k, v in unp.items()}, tid=tid)


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


def carry_pending(pb):
    """Перенести признак отложенного ролла КАК ЕСТЬ (двадцатый круг, №11): 'А'/'Б'/'АБ'
    остаются строками. Отдельной функцией — под парную мутацию."""
    return (getattr(pb, 'roll_pending', False) or False) if pb else False


def hand_over_book(broker, from_route, to_route, positions=None):
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
    # ОТСУТСТВИЕ ИСХОДНОЙ КНИГИ — ОТКАЗ (двадцать первый круг, №13). state.load при
    # отсутствии файла возвращает None, и функция шла дальше как ни в чём не бывало:
    # терялись roll_pending (поставочное обязательство!) и prev_st_* обеих ног, а при
    # возврате в Ф без старой книги и без .ib у брокера d_fix обнулялся — замыкание
    # считало вклад реального ZN нулевым, то есть давало ЛОЖНЫЙ триггер капа, а
    # следующая сессия отказывала из-за нулевого шага. Переход при этом получал COMPLETE.
    if _pb is None:
        raise Incident(
            f'книги маршрута {from_route} нет ({_ST2.book_path(from_route)}) — передавать '
            f'нечего: потерялись бы отложенный ролл и состояния сигнала обеих ног, а d_fix '
            f'обнулился бы. Ручной разбор (О-5)')
    # ПРИЗНАК ОТЛОЖЕННОГО РОЛЛА ПЕРЕНОСИТСЯ КАК ЕСТЬ (двадцатый круг, №11). bool() здесь
    # превращал пер-ножное 'Б' в True, то есть в «обе ноги»: после возврата Ф->Е->Ф
    # исправная нога А тоже получала перенос и уходила из своей серии в дальнюю — лишний
    # оборот по всей ноге и ролл, которого нормативно не было. Пер-ножный признак ввёл
    # девятнадцатый круг (№1), а эта передача его молча уничтожала. Нормализацию строки
    # и не-строки делает state.book_from_broker; здесь значение не трогается.
    _prev_pending = carry_pending(_pb)
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
    # КНИГА — ИЗ ПРОВЕРЕННОГО СНИМКА (двадцать первый круг, №3). Прежде здесь заново
    # спрашивались позиции, уже ПОСЛЕ финальной сверки и барьера заявок: поздний фил или
    # ручная сделка, пришедшие в этот зазор, записывались в состояние как законная книга и
    # получали COMPLETE. Дальше сверка их не ловила никогда — они и есть наше состояние.
    # Теперь берётся снимок, который прошёл сверку; расхождение с брокером всплывёт
    # входной сверкой СЛЕДУЮЩЕЙ сессии, то есть станет видимым, а не узаконенным.
    _pos_src = broker.net_positions() if positions is None else positions
    # d_fix=0 ПРИ ЖИВОЙ НОГЕ Б — ОТКАЗ (двадцать второй круг, №9). Иерархия источников
    # могла закончиться нулём (нет старой книги Ф, у брокера нет .ib), и книга с реальным
    # ZN сохранялась с d_fix=0 под COMPLETE: close_out считал вклад ноги Б нулевым, плечо
    # закрытия лгало, а следующая сессия падала на нулевом шаге. Обещанной «явной пометки»
    # не существовало — теперь это честный отказ передачи ДО записи состояния.
    _zn_live = sum(float(v) for k, v in (_pos_src or {}).items()
                   if str(k).startswith('ZN') and float(v))
    if to_route == 'F' and _zn_live and not _dfx:
        raise RuntimeError(
            f'd_fix не восстановлен (старой книги Ф нет, живой доходности у брокера нет), '
            f'а нога Б у брокера живая ({_zn_live:+g} ZN) — передача книги с d_fix=0 '
            f'дала бы ложное плечо закрытия; ручной разбор (О-5)')
    # СЕРИЯ ОБЯЗАТЕЛЬНА ДЛЯ ЖИВОЙ НОГИ (двадцать третий круг, №7). book_from_broker берёт
    # серию СРЕЗОМ имени ('MESU26' -> 'U26'), и для ГОЛОГО 'MES'/'ZN' она выходит пустой.
    # Ноги перехода набирает ОПЕРАТОР РУКАМИ — живого вызывающего у execute() нет вовсе, —
    # а весь выпускной набор ходит голыми именами, поэтому путь не проверялся ни разу.
    # Книга без серии не роллируется и падает на пустом теге уже ПОСЛЕ движения денег.
    if to_route == 'F':
        def _ser_of(k):                    # ТА ЖЕ логика, что у state.book_from_broker
            k = str(k)
            if k.startswith('MES'): return k[3:]
            if k.startswith('ES'):  return k[2:]
            if k.startswith('ZN'):  return k[2:]
            return None
        _bad_ser = sorted({k for k, v in (_pos_src or {}).items()
                           if float(v or 0) and _ser_of(k) == ''})
        if _bad_ser:
            raise RuntimeError(
                f'позиции {_bad_ser} без серии контракта: книга Ф получила бы пустой '
                f'ser_a/ser_b, не смогла бы роллироваться и встала бы перед поставкой. '
                f'Ноги перехода задавать ПОЛНЫМИ именами (MESU26, ZNZ26), ручной разбор')
    _bk = _ST2.book_from_broker(_cls, _pos_src, to_route,
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
    # ЖУРНАЛ НОВОГО МАРШРУТА НАЧИНАЕТСЯ ПЕРЕХОДОМ (двадцать второй круг, №16). Книга
    # нового маршрута несёт номер сессии, а журнал §7 этого маршрута ещё пуст — и защита
    # «пустой журнал при торговавшей книге = утрата» отказывала бы первой же сессии после
    # перехода. Ослаблять защиту нельзя (утрата и подмена выглядят так же); вместо этого
    # переход ЯВНО открывает цепочку якорной итоговой строкой — удаление журнала после
    # этого снова различимо.
    try:
        import journal as _J2
        _jp2 = _ST2.lock_dir() / f'journal-{to_route}.csv'
        # НАМЕРЕНИЕ СТАРОЙ ЭПОХИ ЦЕЛЕВОГО МАРШРУТА (двадцать третий круг, №9). Перед
        # записью book-{to_route}.json никто не смотрел на book-{to_route}.json.intent.json.
        # После COMPLETE первый же run_session разбирал ЧУЖОЕ намерение прошлой эпохи: при
        # совпавших количествах он принимал старую book_after и затирал только что
        # переданную книгу, при несовпавших — вставал в О-5. Оба исхода наступают уже
        # ПОСЛЕ физического перевода денег, поэтому проверка стоит ДО передачи.
        if _ST2.load_intent(_ST2.book_path(to_route)) is not None:
            raise RuntimeError(
                f'у маршрута {to_route} осталось НЕРАЗОБРАННОЕ намерение прошлой эпохи '
                f'({_ST2.book_path(to_route)}.intent.json): после передачи книги первая же '
                f'сессия разобрала бы его и затёрла новую книгу либо встала в О-5 — '
                f'разобрать намерение ДО перехода (О-5)')
        _rows2 = _J2.read(_jp2)
        # СУЩЕСТВУЮЩИЙ ЖУРНАЛ ЦЕЛЕВОГО МАРШРУТА ПРОВЕРЯЕТСЯ ЦЕЛИКОМ (двадцать третий круг,
        # №10). Прежде вызывался только read(): журнал с пересчитанным хэшем, оборванной
        # сессией или незакрытым хвостом пропускал публикацию книги и COMPLETE, а первая же
        # ежедневная сессия отказывала — оставляя НОВУЮ физическую позицию без управления.
        # Проверять надо ДО передачи: после неё деньги уже переведены.
        if _rows2:
            _J2.verify(_jp2)                     # цепочка хэшей; расхождение — исключение
            if not str(_rows2[-1].get('note', '')).startswith('итог сессии'):
                raise RuntimeError(
                    f'журнал маршрута {to_route} не закрыт итоговой строкой прошлой '
                    f'сессии — записи исполнения могли потеряться; передавать книгу в '
                    f'маршрут с оборванным журналом нельзя (О-5)')
        if not _rows2:
            _J2.append(_jp2, dict(
                date=_today, leg='', instrument='ИТОГ', qty=0, px_order='-', px_fill='',
                commission='', reason='', nav='', leverage='',
                roll_spread_near='', roll_spread_far='',
                note=f'итог сессии {int(_prev_sess or 0) + 1}: строк 0 '
                     f'(журнал начат переходом {from_route}->{to_route})'))
    except Exception as _exj:
        raise RuntimeError(f'журнал маршрута {to_route} не начат ({_exj}) — без него '
                           f'первая сессия нового маршрута не пройдёт защиту §7')
    # FSYNC КАТАЛОГА (двадцать первый круг, №16): книга пишется через state.save с fsync
    # каталога, а route.txt — нет. После потери питания возможны долговечные целевая книга
    # и TRANSITION_COMPLETE при исчезнувшем или старом route.txt: автопилот выберет
    # ПРЕЖНИЙ маршрут и оставит фактическую книгу без управления.
    _dfd = _os2.open(str(_rt.parent), _os2.O_RDONLY)
    try:
        _os2.fsync(_dfd)
    finally:
        _os2.close(_dfd)
    return _bk


def _window_till(asof=None):
    """Край общего окна LSE/SIX и CME на дату перехода (двадцатый круг, №7; двадцать
    первый, №2 — ворота стали fail-CLOSED и проверяют ОБЕ границы).

    Считается ЗДЕСЬ, а не приходит аргументом: в бою переход запускает оператор руками, и
    защита, которую можно забыть передать, защитой не является.

    ОТКАЗ ВЫЧИСЛЕНИЯ — ЭТО ОТКАЗ ПЕРЕХОДА. Прежде исключение превращалось в None, и
    _order_gate переставал смотреть на часы вовсе: непокрытый календарём год или поломка
    feed открывали ворота настежь. Теперь край считается ОДИН раз до первой заявки, и если
    он неизвестен — переход не начинается. Внутри исполнения None уже невозможен, поэтому
    прежний довод «ложный отказ посреди перехода хуже» больше не применим: посреди
    перехода ничего не пересчитывается.
    """
    import sys as _s, os as _o
    _lv = _o.path.join(_o.path.dirname(_o.path.abspath(__file__)), 'live')
    if _lv not in _s.path:
        _s.path.insert(0, _lv)
    import feed as _FD
    import pandas as _pd
    d = _pd.Timestamp(asof) if asof else None
    try:
        start, till = _FD.common_window(d)
    except Exception as ex:
        raise Incident(f'общее окно LSE/CME не вычислено ({ex}) — переход не начинается: '
                       f'без края окна заявки уходили бы на закрытую площадку')
    # ЧАСЫ ОТНОСЯТСЯ ТОЛЬКО К СЕГОДНЯШНЕЙ ДАТЕ. Границы окна и календарь обеих площадок
    # проверяются ВСЕГДА (выходной или праздник отвергается на любой дате), а вот сравнение
    # с настенными часами имеет смысл лишь когда переход идёт СЕГОДНЯ. Для разбора задним
    # числом и для стендов край окна не возвращается вовсе: иначе _order_gate сравнивал бы
    # текущее время с окном ПРОШЛОГО дня и останавливал переход «за краем», которого в тот
    # день ещё не наступало. Поймано репетицией выпуска: стенды исполнителя падали на
    # «окно закрыто 15:34 >= 10:30» при asof=07.08.
    if d is not None and d.normalize() != _FD.exchange_today().normalize():
        return None
    now = _pd.Timestamp.now(tz=_FD.EXCHANGE_TZ)
    if not (start <= now < till):
        raise Incident(f'сейчас {now:%H:%M} вне общего окна LSE/CME '
                       f'({start:%H:%M}-{till:%H:%M}) — переход не начинается')
    return till


def _order_gate(st, broker, fail, where='', window_till=None):
    """RUNTIME-ЛИМИТ ПЕРЕД КАЖДОЙ ЗАЯВКОЙ (девятнадцатый круг, №8): прежняя проверка
    стояла только перед основной продажей — при 389 занятых заявках продажа №390 проходила,
    а покупка №391 подавалась БЕЗ проверки; компенсации и восстановительные заявки resume
    обходили ворота вовсе. Брокер отверг бы покупку после исполненной продажи источника —
    непарная позиция и MIXED ровно на границе, ради которой лимит и введён."""
    if (len(st['order_ids']) >= ORDERS_PER_DAY
            and not getattr(broker, 'counting', False)):
        fail(f'дневной лимит {ORDERS_PER_DAY} заявок исчерпан в исполнении '
             f'({len(st["order_ids"])}) перед заявкой {where} — переход останавливается')
    # КРАЙ ОБЩЕГО ОКНА — ПЕРЕД КАЖДОЙ ЗАЯВКОЙ (двадцатый круг, №7). Прежде окно было
    # булевым аргументом in_common_window, проверенным ОДИН раз до preview, preflight и
    # сотен заявок; тайм-аут 15 минут относился к паре, а не к закрытию площадки.
    if window_till is not None and not getattr(broker, 'counting', False):
        import pandas as _pd
        _now = _pd.Timestamp.now(tz=window_till.tz)
        if _now >= window_till:
            fail(f'общее окно LSE/CME закрыто ({_now:%H:%M:%S} >= {window_till:%H:%M}) '
                 f'перед заявкой {where} — переход останавливается, непарная позиция '
                 f'разбирается вручную')


def _run_lots(broker, plan, st, state_path, lim, unp, dst_bought, fail, _M=None,
              journal=None, window_till=None):
    _wt = window_till
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
            _order_gate(st, broker, fail, window_till=_wt, where=f'продажа {lot["src"]}')
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
            _order_gate(st, broker, fail, window_till=_wt, where=f'покупка {lot["dst"]}')      # №8: и перед покупкой
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
                _order_gate(st, broker, fail, window_till=_wt, where=f'компенсация-покупка {lot["dst"]}')   # №8
                _wc = int(round(u/lot['dprice']))
                oid3, f3 = broker.buy_units(lot['dst'], _wc)
                st['order_ids'].append(oid3)
                _fc = _int_fill(f3, lot['dst'])
                unp[lot['leg']] -= _fc*lot['dprice']
                st['log'].append(('compensate_buy', lot['dst'], f3)); _atomic(state_path, st)
                # ИСПОЛНЕНИЕ КОМПЕНСАЦИИ СВЕРЯЕТСЯ С ЗАКАЗАННЫМ (двадцатый круг, №2):
                # прежде сравнения не было вовсе, и недобор ловился лишь допуском в ЦЕЛУЮ
                # единицу цели — то есть не ловился. Повтор заявки запрещён (§12, урок 5),
                # поэтому единственный честный исход — остановка и ручной разбор.
                _bad = compensation_ok(_fc, _wc, unp[lot['leg']], lot['dprice'])
                if _bad:
                    fail(f'компенсация-покупка {lot["dst"]}: {_bad}; повтор запрещён, '
                         f'ручная сверка')
            elif u < -lot['dprice']/2 - TOL:    # перебор — обратная продажа своей ноги (не глубже купленного)
                _want = min(int(round(-u/lot['dprice'])), dst_bought.get(lot['dst'], 0))
                _order_gate(st, broker, fail, window_till=_wt, where=f'компенсация-продажа {lot["dst"]}')   # №8
                oid3, f3 = broker.sell_units(lot['dst'], _want)
                st['order_ids'].append(oid3); _fs = _int_fill(f3, lot['dst']); unp[lot['leg']] += _fs*lot['dprice']
                dst_bought[lot['dst']] = dst_bought.get(lot['dst'], 0) - _fs
                st['log'].append(('compensate_sell', lot['dst'], f3)); _atomic(state_path, st)
                _bad = compensation_ok(_fs, _want, unp[lot['leg']], lot['dprice'])
                if _bad:                             # двадцатый круг, №2 — та же сверка
                    fail(f'компенсация-продажа {lot["dst"]}: {_bad}; повтор запрещён, '
                         f'ручная сверка')
            # ДОПУСК — ПОЛОВИНА ЕДИНИЦЫ ЦЕЛИ (двадцатый круг, №2). Округление покупки к
            # целому кванту оставляет не больше половины кванта; ЦЕЛАЯ единица — уже
            # недостача, а не округление. Прежний допуск в единицу пропускал недобор
            # ровно на один контракт (~1% NLV при минимальном размере перехода), который
            # ежедневная полоса 10% не исправляет месяцами.
            _bad_pair = compensation_ok(0, 0, unp[lot['leg']], lot['dprice'])
            if _bad_pair:
                # через compensation_ok (двадцать второй круг, №21-урок): прямая проверка
                # pair_tol здесь МАСКИРОВАЛА мутацию первой линии — подмена одной функции
                # обязана гасить всю внутрицикловую честность, чтобы её ловили финальные
                # ворота ноги (984/1001) своим, различимым текстом.
                fail(f'пара не выровнена компенсацией: остаток '
                     f'${abs(unp[lot["leg"]]):,.0f} выше половины единицы цели '
                     f'${lot["dprice"]/2:,.0f} — ручная сверка')
            if broker.minutes_since(key) > TIMEOUT_MIN:
                fail('тайм-аут пары 15 минут')
            g = broker.gross()
            if g > INTRA_CAP + 1e-9:
                fail(f'внутрисессионный gross {g:.4f} > {INTRA_CAP}')
            remaining -= sold if sold > 0 else 0
        st['done'].append(key); _atomic(state_path, st)
