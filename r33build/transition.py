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

FUT_ROOTS = ('MES', 'ES', 'ZN')          # порядок важен: MES проверяется раньше ES


def fut_root(instrument):
    """Корень фьючерса из ПОЛНОГО имени: 'MESU26' -> 'MES', 'ZNZ26' -> 'ZN'.

    ИМЯ И КОРЕНЬ — РАЗНЫЕ ВЕЛИЧИНЫ (двадцать пятый круг, №2). Маржа (FUT_MARGIN, живой
    замер) и упаковка ES/MES ключуются КОРНЕМ, а инструмент заявки, книга и поставочная
    серия требуют ПОЛНОГО имени. Прежде эти два смысла были склеены: mapped_book сравнивал
    имя точно с 'MES', а hand_over_book (двадцать третий круг, №7) требовал серию — переход
    Е→Ф либо отказывал до сделки на правильных именах, либо исполнялся на голых и отвергал
    книгу УЖЕ ПОСЛЕ покупки фьючерсов, то есть уходил в MIXED с фактической позицией без
    управляемой серии и без ролла перед поставкой.
    """
    n = str(instrument)
    for r in FUT_ROOTS:
        if n.startswith(r):
            return r
    return n


def fut_series(instrument):
    """Серия из полного имени ('MESU26' -> 'U26'); пустая строка, если имя голое."""
    n = str(instrument)
    return n[len(fut_root(n)):]


def mapped_book(instrument, units):
    """Позиция внутренней сетки -> фактическая книга брокера (ред. 32).

    Упаковка идёт по КОРНЮ, а имена на выходе сохраняют СЕРИЮ входа: 300 MESU26 ->
    {'ESU26': 30}. Голое имя на входе даёт голые имена на выходе — прежнее поведение.
    """
    units = int(units)
    _r, _ser = fut_root(instrument), fut_series(instrument)
    if _r == 'MES':
        es, mes = map_mes(units)
        out = {}
        if es: out['ES' + _ser] = es
        if mes: out['MES' + _ser] = mes
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
    # ЗАМЕР ПРИВЯЗАН К ПОКОЛЕНИЮ РЕЕСТРА, А НЕ ТОЛЬКО К ИМЕНАМ (32-й круг, №8). Имя серии
    # переживает исправление con_id: first_connect публикует новый реестр, а при неполном
    # whatIf оставляет ПРЕЖНИЙ замер — и тот проходил ворота как относящийся к новому
    # контракту. Сверяем con_id каждой серии с ТЕКУЩИМ реестром. Старые файлы поля не
    # имеют; для них это отказ, а не «проверено»: замер пересниматься обязан в любом
    # случае (ворота выпуска требуют полной пары с 20.08).
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
    # ПОКОЛЕНИЕ РЕЕСТРА СВЕРЯЕТСЯ ПОСЛЕ ПОКРЫТИЯ СЕРИЙ (тридцать второй круг, №8).
    # Порядок существен: замер ПРЕЖНЕЙ серии (ESZ25 при реестре U26/Z26) обязан отвечать
    # «не покрывает серии реестра» — это его собственная защита семнадцатого круга; если
    # сверка поколения стоит раньше, она перехватывает случай и отвечает про con_id,
    # то есть более ранняя ветка делает старую защиту недостижимой (класс дефекта, который
    # этот круг и ищет). Сверяем con_id только по сериям, которые ЕСТЬ в текущем реестре.
    _cids = meta.get('con_ids')
    if not isinstance(_cids, dict) or not _cids:
        raise Incident(f'{p}: замер без привязки к поколению реестра (_meta.con_ids) — '
                       f'формат до тридцать второго круга; переснять first_connect')
    # КАРТА ПОКОЛЕНИЯ ОБЯЗАНА БЫТЬ ПОЛНОЙ (тридцать третий круг, №2). Проверялось только,
    # что словарь непуст и что ПЕРЕЧИСЛЕННЫЕ в нём ключи совпадают с реестром: достаточно
    # было оставить правильный ESU26 и УБРАТЬ ZNZ26 с исправленным con_id — и старый замер
    # ноги Б проходил ворота целиком. Заниженная маржа прежнего контракта завышает
    # preflight-запас, и переход открывает книгу, чей фактический запас уже ниже О-3.
    # Карта сверяется с содержимым замера, а не сама с собой.
    if set(_cids) != set(entries):
        raise Incident(
            f'{p}: карта поколения неполна — con_ids {sorted(_cids)} против замера '
            f'{sorted(entries)}; отсутствующая серия прошла бы со старым контрактом. '
            f'Переснять first_connect')
    try:
        with open(rp, encoding='utf-8') as _fr:
            _reg_now = {r['instrument']: str(r['con_id']) for r in _csv.DictReader(_fr)}
    except OSError as _exr:
        raise Incident(f'{p}: реестр для сверки поколения замера недоступен ({_exr}) — '
                       f'переход запрещён')
    _bad_gen = [f'{k}: замер con_id {v}, реестр {_reg_now.get(k, "нет строки")}'
                for k, v in sorted(_cids.items())
                if k in valid and str(v) != _reg_now.get(k)]
    if _bad_gen:
        raise Incident(f'{p}: замер относится к ДРУГОМУ поколению реестра '
                       f'({"; ".join(_bad_gen[:3])}) — маржа могла быть снята на прежнем '
                       f'контракте; переснять first_connect')
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
            # ПО КОРНЮ (двадцать пятый круг, №2): FUT_MARGIN ключуется 'ES'/'MES'/'ZN',
            # а книга несёт полные имена с серией.
            if fut_root(instr) not in FUT_MARGIN:
                raise Incident(f'{instr}: нет модельного требования маржи '
                               f'(корень {fut_root(instr)})')
            if _lm:
                # ПРИ СУЩЕСТВУЮЩЕМ ЗАМЕРЕ дыры не добираются константами (шестнадцатый
                # круг, №4): молчаливый .get прятал неполноту — повышенное требование
                # непокрытого корня не замечалось. Константы — только когда файла нет.
                # И ЗАМЕР, И КОНСТАНТЫ КЛЮЧУЮТСЯ КОРНЕМ (двадцать шестой круг, №6).
                # _live_margins() ПОКРЫТИЕ проверяет по сериям реестра, но сам словарь
                # СВОРАЧИВАЕТ в корни ('ESU26' -> 'ES'), потому что house-требование
                # относится к классу контракта. Днём я ошибочно «поправил» этот поиск на
                # полное имя, неверно прочитав функцию, — и книга с сериями переставала
                # находить свою маржу. Ключ здесь ровно один: корень.
                _rt = fut_root(instr)
                if _rt not in _lm:
                    raise Incident(f'{instr} (корень {_rt}): живой замер маржи не покрывает '
                                   f'текущую серию корня — перегенерировать first_connect')
                total += abs(int(units)) * _lm[_rt]
            else:
                total += abs(int(units)) * FUT_MARGIN[fut_root(instr)]
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
        # ЦЕНЫ УПАКОВКИ — ПОД ТЕМИ ЖЕ ИМЕНАМИ, ЧТО ОТДАЛА mapped_book (двадцать пятый круг,
        # №2): при полном имени 'MESU26' книга несёт 'ESU26'/'MESU26', и цена под голым
        # ключом до них не дошла бы.
        if fut_root(di) == 'MES':
            _ser = fut_series(di)
            prices['ES' + _ser] = dp*10.0
            prices['MES' + _ser] = dp
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

    def gross(self, d_fix=None):
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


def _state_digest(obj):
    """Хэш содержимого прогресса перехода (тридцатый круг, №13).

    Book и intent несут digest, а состояние исполнителя — нет: оно защищено только
    атомарной записью, то есть от ОБРЫВА, но не от ПОРЧИ. Валидное по JSON изменение
    принималось как истина: исчезнувший элемент `done` заставляет повторно продать
    завершённый лот, уменьшенный `partial` — повторно продать уже исполненный остаток.
    Финальная сверка потом объявит MIXED, но необратимая лишняя продажа уже сделана.
    """
    import hashlib as _hl
    _body = {k: v for k, v in obj.items() if k != 'digest'}
    return _hl.sha256(json.dumps(_body, ensure_ascii=False, sort_keys=True,
                                 default=str).encode('utf-8')).hexdigest()


def _atomic(path, obj):
    obj = dict(obj)
    obj['digest'] = _state_digest(obj)
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

def check_plan_prices(broker, legs, src_cls):
    """Сверка долларовых единиц плана с НЕЗАВИСИМОЙ рыночной полосой (29-й круг, №3).

    Прежде unit_usd и dprice проверялись ТОЛЬКО на конечность и положительность, а дальше
    по ним считалось ВСЁ: число долей цели, лимит §8б, preflight-маржа, компенсации и
    финальная сверка. Это круговое доказательство — план сверялся с планом: заниженная в
    десять раз цена цели даёт примерно десятикратную покупку, формула got_units*dprice
    сходится, и переход получает COMPLETE, тогда как фактическое плечо уходит за 2,00.

    Полоса нарочно широка — она ловит ПОРЯДОК величины, а не базисные пункты (модельная
    единица ноги Б несёт отношение d_fix/dref, исполнителю неизвестное). Метод обязателен:
    брокер, не строящий полосу, не считается брокером перехода — иначе защиту выключал бы
    сам вызывающий, чьи числа она и проверяет.
    """
    _uref = getattr(broker, 'unit_ref', None)
    if not callable(_uref):
        raise Incident('брокер не отдаёт unit_ref — цены плана непроверяемы, исполнение '
                       'запрещено (сверка unit_usd и dprice с рынком обязательна)')
    for _nm, _spec in sorted(legs.items()):
        _items = [(str(_spec['dst'][0]), float(_spec['dst'][1]), _spec['dst'][2])]
        _items += [(str(_i), float(_u), src_cls) for _i, _q, _u in _spec['src']]
        for _instr, _val, _cls in _items:
            try:
                _band = _uref(_instr, _cls)
            except Exception as ex:
                raise Incident(f'{_instr}: полоса цены у брокера недоступна ({ex}) — цена '
                               f'плана непроверяема, исполнение запрещено')
            if not _band:
                raise Incident(f'{_instr}: брокер не строит полосу цены — план опирался бы '
                               f'только на собственное число, исполнение запрещено')
            _lo, _hi = float(_band[0]), float(_band[1])
            _fin = (_lo == _lo and _hi == _hi and _lo not in (float('inf'), float('-inf'))
                    and _hi not in (float('inf'), float('-inf')))
            if not (_fin and _lo > 0 and _hi >= _lo):
                raise Incident(f'{_instr}: полоса цены {_band!r} недостоверна — '
                               f'исполнение запрещено')
            if not (_lo <= _val <= _hi):
                raise Incident(
                    f'{_instr}: долларовая единица плана ${_val:,.2f} вне рыночной полосы '
                    f'[${_lo:,.2f}; ${_hi:,.2f}] — по ней считаются число долей цели, '
                    f'лимит §8б и маржа; исполнение запрещено')
    return True


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
    # АВАРИЙНАЯ ОТМЕТКА — ПОСЛЕ СУХИХ ПРОВЕРОК (двадцать седьмой круг, №22). Прежде
    # EMERGENCY_OVERRIDE ложился в нормативный журнал ДО всех проверок: отказ на любой из
    # них оставлял в истории запись об аварийном обходе порога, которого не было. Сначала
    # убеждаемся, что переход вообще возможен, и только потом фиксируем обход.
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
    # ЧУЖОЙ ОТКРЫТЫЙ ПЕРЕХОД — ДО СОЗДАНИЯ ЧЕГО-ЛИБО (сороковой круг, №4). В 39-м круге я
    # поставил эту проверку в _execute_locked и написал «О-5 до создания чего-либо» — а она
    # стояла ПОСЛЕ создания state_path, записи snapshot/digest, вызова preview и изменения
    # postponed. При отказе preview первые два запуска возвращали POSTPONED, вообще не дойдя
    # до неё: прогресс нового перехода уже создавался поверх неразобранной эпохи. Здесь tid
    # только что посчитан, а состояние ещё не тронуто — это и есть «до создания чего-либо».
    # ЛИЧНОСТЬ СЧЁТА — ДО СОЗДАНИЯ СОСТОЯНИЯ (сорок первый круг, №8). Она проверялась
    # внутри _preflight_handover, то есть ПОСЛЕ создания/перезаписи state_path, записи
    # снимка и digest, вызова preview и изменения postponed. Чистый отказ на ЧУЖОМ счёте
    # оставлял валидное состояние перехода, снятое с чужого счёта: следующий запуск с
    # изменившимся NLV получает другой tid и блокируется, а ошибочный resume принял бы
    # разницу с чужим snapshot за уже исполненный прогресс. Здесь ещё ничего не создано.
    _pin_e = _machine_pin()
    if not _pin_e:
        raise Incident('торговый счёт не пинован (ни ADDFUT_ACCOUNT, ни account.txt) — '
                       'переход непроверяем, запрещён')
    _acct_e = getattr(broker, 'account', None)
    if not _acct_e:
        raise Incident('брокер перехода не сообщает счёт — позиции могут относиться к '
                       'чужому счёту, переход запрещён')
    if str(_acct_e) != str(_pin_e):
        raise Incident(f'брокер работает со счётом {_acct_e}, а машина пинована на '
                       f'{_pin_e}: переход запрещён до разбора (О-5)')
    _otid_now = _st_now[5] if len(_st_now) > 5 else ''
    if _otid_now and _otid_now != tid:
        raise Incident(
            f'в журнале открыт ДРУГОЙ переход tid={_otid_now!r} (наш {tid!r}) — прошлые '
            f'заявки не исключены, его файловые метки снимать нельзя; исполнение запрещено '
            f'до ручного разбора (О-5)')
    try:
        _ctx = _M.hold_strategy_lock(journal)
        _ctx.__enter__()
    except RuntimeError:
        raise Incident('strategy-lock (стратегия+счёт) занят другим процессом — параллельное исполнение запрещено')
    try:
        return _execute_locked(broker, state_path, capital, legs, signal_id, from_route, to_route,
                               in_common_window, resume, journal, mr_state, asof, registry,
                               plan, tid, reg, want_cls, src_cls, _M, emergency)
    finally:
        _ctx.__exit__(None, None, None)


def pv_remainder(plan, done):
    """ОСТАТОК ПЛАНА ДЛЯ ПРЕДПРОСМОТРА (сорок четвёртый круг, №4, P0).

    Ключ исполненного лота — тот же, которым _run_lots пропускает сделанное:
    f"{src}:{step}" в st['done']. Правило вынесено в отдельную функцию сознательно: пока
    оно жило внутри цикла сборки заявок, мутировать его можно было только вместе с
    соседним кодом, а два одинаковых правила в разных местах разъезжаются при первой же
    правке одного из них (урок сорок второго круга, leg_target_roll).
    """
    keys = set(done or ())
    out = {}
    for lt in (plan or ()):
        if f"{lt['src']}:{lt['step']}" in keys:
            continue
        out[lt['dst']] = out.get(lt['dst'], 0.0) + \
            float(lt['units']) * float(lt['unit_usd']) / float(lt['dprice'])
    return out


def _preflight_handover(from_route, to_route, _dst_names=(), _broker_p=None,
                        _resume=False):
    """Сухая проверка условий передачи книги ДО первой заявки (двадцать пятый круг, №7).

    Повторяет те барьеры hand_over_book, которые не зависят от результата торговли:
    исходная книга существует, у целевого маршрута нет неразобранного намерения, а его
    журнал либо пуст при отсутствующей книге, либо цел и закрыт итогом. Всё это можно
    узнать заранее, и узнавать это ПОСЛЕ перевода денег бессмысленно.
    """
    import sys as _sp, os as _op
    _lv = _op.path.join(_op.path.dirname(_op.path.abspath(__file__)), 'live')
    if _lv not in _sp.path:
        _sp.path.insert(0, _lv)
    import state as _STp, daily as _DLp, journal as _Jp

    # СЕРИЯ ЦЕЛИ СВЕРЯЕТСЯ С КАЛЕНДАРЁМ РОЛЛА (СОРОК ЧЕТВЁРТЫЙ КРУГ, №3, P0).
    # Проверялось лишь, что имя несёт серию, но не то, что серия НЕ УХОДЯЩАЯ. В день ролла
    # реестр законно содержит обе (26.08 — U26 и Z26), и переход Е->Ф мог купить U26. Дальше
    # hand_over_book ставит last_session=сегодня, ежедневный leg_target_roll() в этот день
    # уже не исполняется — и правка 42-го круга «первый вход в день ролла открывает СВЕЖУЮ
    # серию» обходится переходом. Книга остаётся в уходящей серии, ближайший ролл пропущен,
    # серия идёт к поставке. Правило и источник истины — те же, что у ежедневного контура.
    _due = []
    try:
        import feed as _FDr
        _today_r = _FDr.exchange_today()
        _hol_r = _DLp.holidays_for(_today_r.year)
        for _dn in (_dst_names or ()):
            _root_r = fut_root(_dn)
            if _root_r not in ('ES', 'MES', 'ZN'):
                continue                       # фонды календарём ролла не связаны
            _ser_r = fut_series(_dn)
            if not _ser_r:
                continue                       # отсутствие серии ловит отдельный барьер
            _dl = _DLp.roll_deadline(_ser_r, _hol_r)
            if _today_r >= _dl:
                _due.append(f'{_dn} (срок ролла {_dl:%d.%m.%Y})')
    except Exception as _exr:
        raise RuntimeError(f'календарь ролла недоступен ({_exr}) — серия цели непроверяема, '
                           f'переход запрещён (О-5)')
    if _due:
        raise RuntimeError(
            f'цель перехода в УХОДЯЩЕЙ серии: {"; ".join(_due)} — на день ролла и позже '
            f'открывать её нельзя, иначе книга родится в серии, которую сегодня же надо '
            f'роллить, а ежедневная проверка в этот день уже не сработает. Задать ноги '
            f'следующей серией')

    _cls_src = _DLp.BookE if from_route == 'E' else _DLp.Book
    _cls_dst = _DLp.BookE if to_route == 'E' else _DLp.Book
    _src, _, _ = _STp.load(_STp.book_path(from_route), _cls_src)
    if _src is None:
        raise RuntimeError(f'книги маршрута {from_route} нет — передавать нечего')
    # НАМЕРЕНИЕ ОБОИХ МАРШРУТОВ (двадцать восьмой круг, №6). Проверялся только целевой, но
    # неразобранное намерение ИСХОДНОГО означает, что его последняя сессия оборвалась: книга
    # источника может не соответствовать брокеру, и переход считал бы план от неверной
    # позиции. Разбирать обязаны оба — до первой заявки.
    for _r_int in (to_route, from_route):
        if _STp.load_intent(_STp.book_path(_r_int)) is not None:
            raise RuntimeError(f'у маршрута {_r_int} осталось неразобранное намерение '
                               f'прошлой эпохи — разобрать ДО перехода (О-5)')
    # ЛИЧНОСТЬ СЧЁТА У БРОКЕРА ПЕРЕХОДА (тридцатый круг, №6). Пин доходил только до
    # session.py: переход сверял с _machine_pin() лишь _meta.account в файле маржи, но НЕ
    # требовал, чтобы сам брокер работал с этим счётом. А IBBroker при незаданном пине
    # молча берёт единственный managed account. Денежный путь: маржа и машинная книга
    # счёта A, позиции/NLV/заявки счёта B; после COMPLETE ежедневный контур A получает
    # состояние B. Требование fail-closed: брокер без личности к переходу не допускается —
    # ровно как с net_liquidation в двадцать втором круге.
    if _broker_p is not None:
        _pin_p = _machine_pin()
        if not _pin_p:
            raise RuntimeError('торговый счёт не пинован (ни ADDFUT_ACCOUNT, ни '
                               'account.txt) — переход непроверяем, запрещён')
        _acct_p = getattr(_broker_p, 'account', None)
        if not _acct_p:
            raise RuntimeError('брокер перехода не сообщает счёт — позиции, NLV и заявки '
                               'могут относиться к чужому счёту, переход запрещён')
        if str(_acct_p) != str(_pin_p):
            raise RuntimeError(f'брокер работает со счётом {_acct_p}, а машинный пин — '
                               f'{_pin_p}: маржа и книга относятся к одному счёту, '
                               f'позиции к другому — переход запрещён')
    # КНИГА ИСТОЧНИКА СВЕРЯЕТСЯ С БРОКЕРОМ (тридцатый круг, №4). Прежде она грузилась
    # только ради существования, намерения и roll_pending: фактические позиции с ней НЕ
    # сравнивались. Значит переход «отмывал» расхождение — поздний fill или потерянную
    # позицию: новая книга строится из ФАКТИЧЕСКОГО счёта, старое расхождение исчезает
    # вместе со старой книгой, и О-5 не поднимается никогда. Сверка плана со снимком
    # (snap0) этого не заменяет: она сравнивает брокера с ПЛАНОМ, а не с тем, что контур
    # считал своей позицией. Расхождение обязано остановить переход, а не раствориться.
    # СВЕРКА КНИГИ С БРОКЕРОМ — ТОЛЬКО ДЛЯ СВЕЖЕГО ПЕРЕХОДА (тридцать восьмой круг, №1).
    # Она безусловно требовала, чтобы позиции брокера совпадали с книгой ИСХОДНОГО
    # маршрута. Для свежего перехода это верно и обязательно. Но после хотя бы одного
    # исполненного лота расхождение — ШТАТНОЕ состояние перехода: книга на диске ещё
    # исходная, у брокера уже промежуточная. Значит resume отвергался ДО кода
    # восстановления, и штатно продолжить частично исполненный переход было нельзя
    # вовсе — оставалась непарная позиция и ручная хирургия. Прогресс сверяет само
    # восстановление: по st['snapshot'], st['done'] и фактическим позициям, ПОСЛЕ, зная
    # что уже исполнено. Остальные барьеры предполёта (журнал, намерение, замкнутость)
    # для resume остаются в силе — снимается ровно сверка позиций.
    # СНИМАЕТСЯ СВЕРКА ПОЗИЦИЙ, А НЕ БРОКЕР (тридцать девятый круг, №4). Первая редакция
    # этой правки обнуляла _broker_p целиком — а он же нужен НИЖЕ для свежей доходности и
    # d_fix при возврате в Ф: `yield_pct(_broker_p.ib, ...)` гарантированно падал, _dfx_ok
    # становился ложью, и при целевом ZN следовал жёсткий отказ. То есть, открыв resume для
    # Ф→Е, я закрыл его для Е→Ф — направления, которое и есть аварийный выход. Мой «честный
    # resume» в selfcheck этого не поймал, потому что проверяет только Ф→Е, где ветки d_fix
    # нет вовсе. Гасим ровно то, что мешает: сверку позиций.
    if _broker_p is not None and not _resume and hasattr(_broker_p, 'net_positions'):
        # СОГЛАСОВАННЫЙ СНИМОК, А НЕ ДВА НЕЗАВИСИМЫХ ЧТЕНИЯ (тридцать первый круг, №9).
        # Здесь стоял опасный порядок «позиции -> заявки»: исполнение, случившееся МЕЖДУ
        # вызовами, даёт старую позицию и уже пустой список заявок — обе проверки проходят,
        # а книга источника на деле другая. Ровно от этой гонки защищает _snapshot_pair
        # (заявки -> позиции -> заявки), и она же — последняя сверка перед первой заявкой
        # перехода: ранний snap0 отделён от неё расчётом маржи, счётчиком заявок, журналом
        # и файловыми операциями.
        try:
            _pos_p, _oo_p = _snapshot_pair(_broker_p)
            _pos_p = _pos_p or {}
        except Exception as _exp:
            raise RuntimeError(f'согласованный снимок счёта недоступен в предполёте '
                               f'({_exp}) — книгу источника сверить нечем, переход запрещён')
        if _oo_p:
            raise RuntimeError(
                f'у маршрута {from_route} есть живые заявки {sorted(map(str, _oo_p))} — '
                f'исход прошлой сессии неизвестен, переход запрещён (О-5)')
        # СВЕРКА ПО КОРНЯМ, А НЕ ПО ТОЧНЫМ ИМЕНАМ. Разведение корня и серии — открытый долг
        # (двадцать восьмой круг, №4): книга даёт ZNU26, часть путей и стендов — голое ZN.
        # Точная сверка имён поэтому отвергала бы законные случаи, то есть защита свелась
        # бы к своему же долгу. Количества по КОРНЮ от этого не страдают: подмена «в книге
        # 26/10, у брокера один ZN» ловится целиком, а именно она и есть денежный путь.
        def _byroot(d):
            out = {}
            for _k, _v in (d or {}).items():
                if not _v:
                    continue
                out[fut_root(str(_k))] = out.get(fut_root(str(_k)), 0.0) + float(_v)
            return out
        _want = _byroot(_DLp.physical_book(_src)) if from_route == 'F' else _byroot(
            {'CSPX': getattr(_src, 'n_eq', 0), 'CBU0': getattr(_src, 'n_bd', 0)})
        _have = _byroot(_pos_p)
        _dif_p = [f'{_k}: книга {_want.get(_k, 0):g}, у брокера {_have.get(_k, 0):g}'
                  for _k in sorted(set(_want) | set(_have))
                  if abs(_want.get(_k, 0.0) - _have.get(_k, 0.0)) > 1e-9]
        if _dif_p:
            raise RuntimeError(
                'книга маршрута ' + from_route + ' РАСХОДИТСЯ с брокером до перехода — '
                'переход записал бы новую книгу от фактического счёта и расхождение '
                'исчезло бы без разбора (О-5):\n  ' + '\n  '.join(_dif_p))
    # НЕЗАМКНУТАЯ ПРЕДЫДУЩАЯ СЕССИЯ ИСТОЧНИКА — ТОЖЕ ОТКАЗ (№4). Книга с
    # close_provisional=True не имеет зафиксированного плеча закрытия: переход унёс бы её
    # незамкнутой, и нормативный триггер капа по закрытию предыдущей сессии потерялся бы
    # вместе с маршрутом.
    if getattr(_src, 'close_provisional', False):
        raise RuntimeError(
            f'книга маршрута {from_route} не замкнута (close_provisional) — сначала '
            f'замыкание сессии, затем переход: иначе плечо закрытия теряется')
    _jp = _STp.lock_dir() / f'journal-{to_route}.csv'
    # СЕРИЯ И d_fix — В ПРЕДПОЛЁТЕ (двадцать седьмой круг, №7 и №8). Обе проверки жили
    # только в hand_over_book, то есть срабатывали ПОСЛЕ продажи источника и покупки цели.
    if to_route == 'F':
        try:
            import feed as _FDp2
            _keys_p = list(_FDp2.registry().keys())
        except Exception:
            _keys_p = []
        if any(str(k) not in ('ES', 'MES', 'ZN') and
               str(k).startswith(('ES', 'MES', 'ZN')) for k in _keys_p):
            _bare = sorted({str(n) for n in _dst_names if str(n) in ('ES', 'MES', 'ZN')})
            if _bare:
                raise RuntimeError(
                    f'цели плана {_bare} заданы БЕЗ поставочной серии, а живой реестр её '
                    f'несёт: книга Ф вышла бы без ser_a/ser_b и не роллировалась')
        _pbF, _, _ = _STp.load(_STp.book_path('F'), _DLp.Book)
        # hasattr(broker,'ib') — НЕ ДОКАЗАТЕЛЬСТВО (двадцать восьмой круг, №5): атрибут
        # может быть, а доходность недоступна. Спрашиваем ФАКТ: получается ли dref.
        # D ФИКСИРУЕТСЯ ПРИ ОТКРЫТИИ НОГИ, ЗНАЧИТ СЕГОДНЯШНЕЙ ДОХОДНОСТЬЮ (33-й круг, №3).
        # Прежде старая книга Ф считалась ДОСТАТОЧНЫМ источником, и полученное живое
        # значение всё равно выбрасывалось; hand_over_book потом спрашивал доходность
        # заново и при сбое молча брал d_fix прежней книги. Но пока маршрут Е был активен,
        # BookE дюрацию не хранит, книга Ф лежит нетронутой — её d_fix относится к моменту
        # УХОДА в Е и может быть многомесячным. prev_st_bd переносится из Е, поэтому
        # следующая step() переключения ноги Б не видит и D не перефиксирует: ZN месяцами
        # оценивается по чужой дюрации — полоса, количество ZN и плечо закрытия неверны.
        # Возврат в Ф с ЖИВОЙ ногой Б требует свежей доходности; старая книга больше не
        # доказательство. Стендам — явная калитка, как у замера маржи и даты перехода.
        import os as _osd3
        _zn_target = any(str(_n).startswith('ZN') for _n in (_dst_names or ()))
        _dfx_ok = False
        _dfx_val = 0.0
        try:
            import feed as _FDd
            # ДАТА БАРА СВЕРЯЕТСЯ (тридцать четвёртый круг, №3): без expected_prev
            # feed.closes допускает бар возрастом до пяти календарных дней, и во вторник
            # при пропущенном понедельнике проходила ПЯТНИЧНАЯ доходность. «Сегодняшний»
            # d_fix оказывался позапрошлым, а по нему считаются модельная единица ZN,
            # количество контрактов, полоса и плечо закрытия.
            _t_d = _FDd.exchange_today()
            _prev_d = _FDd.prev_session(_t_d, holidays=_DLp.holidays_for(_t_d.year))
            _y, _yd = _FDd.yield_pct(_broker_p.ib, _t_d, expected_prev=_prev_d)
            _dfx_val = float(_FDd.dref_from_yield(float(_y) / 100.0))
            _dfx_ok = bool(_dfx_val)
        except Exception:
            _dfx_ok = False
            _dfx_val = 0.0
        if not _dfx_ok and _zn_target and _osd3.environ.get('ADDFUT_DFIX_TEST') != '1':
            raise RuntimeError(
                'свежая доходность недоступна, а нога Б открывается заново: D фиксируется '
                'при открытии ноги, и d_fix прежней книги Ф относится к моменту ухода в Е '
                '(возможно, многомесячной давности) — переход запрещён. Стендам: '
                'ADDFUT_DFIX_TEST=1')
        # ПРОВЕРЕННОЕ ЗНАЧЕНИЕ ИДЁТ В ПЕРЕДАЧУ (тридцать четвёртый круг, №4): предполёт
        # получал _dfx_val и выбрасывал его, а hand_over_book запрашивал доходность ЗАНОВО —
        # уже ПОСЛЕ продажи фондов и покупки фьючерсов. Второй запрос мог отказать, хотя
        # предполёт прошёл: брокер держит фьючерсы, книга и маршрут ещё Е, передача уходит
        # в MIXED. Проверка обязана относиться к тому значению, которое будет сохранено.
        if _dfx_ok:
            _PREFLIGHT_DFIX['value'] = _dfx_val
            _PREFLIGHT_DFIX['asof'] = str(_FDd.exchange_today())
        if not _dfx_ok and not (_pbF is not None and getattr(_pbF, 'd_fix', 0)):
            raise RuntimeError(
                'd_fix восстановить нечем: старой книги Ф нет и живой доходности у брокера '
                'тоже — книга Ф получила бы d_fix=0 и ложное плечо закрытия уже ПОСЛЕ '
                'покупки ZN')
    _rows = _Jp.read(_jp)
    if _rows:
        _Jp.verify_rows(_rows, _jp)
        if not str(_rows[-1].get('note', '')).startswith('итог сессии'):
            raise RuntimeError(f'журнал маршрута {to_route} не закрыт итоговой строкой — '
                               f'передавать книгу в маршрут с оборванным журналом нельзя')
    else:
        _dst, _dsess, _ = _STp.load(_STp.book_path(to_route), _cls_dst)
        if _dst is not None and int(_dsess or 0) > 0:
            raise RuntimeError(f'журнал маршрута {to_route} пуст при существующей книге '
                               f'(сессия №{_dsess}) — история утрачена (О-5)')


# ЗНАЧЕНИЕ d_fix, ПРОВЕРЕННОЕ ПРЕДПОЛЁТОМ (тридцать четвёртый круг, №4): предполёт стоит
# ДО первой заявки, hand_over_book — ПОСЛЕ необратимого перевода денег. Разные чтения одной
# величины делают проверку формальной; передаём ровно то, что проверено, и только в пределах
# ТОГО ЖЕ биржевого дня.
_PREFLIGHT_DFIX = {'value': 0.0, 'asof': ''}


def _gross_dfix():
    """d_fix ДЛЯ ИЗМЕРЕНИЯ ПЛЕЧА (тридцать седьмой круг, №3).

    broker.gross() обязан считать модельную единицу ноги Б честной формулой норматива, а не
    серединой проверочной полосы: полоса построена по крайним дюрациям D=3..12 и её середина
    занижала плечо примерно на треть — при CLOSE_CAP=2,00 и INTRA_CAP=2,02 запаса на такую
    ошибку нет. d_fix — константа КНИГИ, а не плана: при возврате в Ф это значение, уже
    проверенное предполётом по свежей доходности, при уходе из Ф — дюрация действующей книги
    Ф, чьи ZN и продаются. Ноль означает «нечем измерить», и gross() обязан отказать, а не
    подставить произвольное число.
    """
    # ПРОВЕРЕННОЕ ЗНАЧЕНИЕ ГОДНО ТОЛЬКО СЕГОДНЯ (тридцать восьмой круг, №6). _PREFLIGHT_DFIX —
    # переменная ПРОЦЕССА, и без сверки даты она переживала неудачную попытку Е→Ф: следующий
    # в том же процессе Ф→Е считал бы ZN по чужому d_fix, игнорируя действующую книгу Ф.
    # У самого пина дата уже пишется (тридцать четвёртый круг, №4) — оставалось её спросить.
    try:
        import feed as _FDx
        if _PREFLIGHT_DFIX.get('value') and \
                str(_PREFLIGHT_DFIX.get('asof') or '') == str(_FDx.exchange_today()):
            return float(_PREFLIGHT_DFIX['value'])
    except Exception:
        pass
    try:
        import state as _STg
        import daily as _DLg
        _b, _, _ = _STg.load(_STg.book_path('F'), _DLg.Book)
        return float(getattr(_b, 'd_fix', 0.0) or 0.0)
    except Exception:
        return 0.0


def _drop_handover(flags):
    """Снять барьеры незавершённой передачи, поставленные ЭТИМ вызовом (31-й круг, №12).

    Нужна ровно на пути ЧИСТОГО отказа: метки теперь ставятся ДО журнального OPEN, и если
    открытие отвергнуто, ни одной заявки не подано — оставленный барьер запер бы торговлю
    и замыкание навсегда (двадцать шестой круг, №11). После первой заявки метки не
    снимаются никогда, кроме принятого COMPLETE (двадцать четвёртый круг, №15).

    Отдельной функцией — под парную мутацию: пока снятие сидит циклом внутри процедуры,
    у него не может быть собственного стенда.
    """
    import os as _osd
    _dirs, _left = set(), []
    for _f in list(flags or ()):
        try:
            _dirs.add(str(_f.parent))
            _f.unlink()
        except FileNotFoundError:
            pass
        except Exception as _exu:
            _left.append(f'{_f}: {_exu}')
    # УДАЛЕНИЕ ДОЛГОВЕЧНО (тридцать третий круг, №7): без fsync каталога снятая метка может
    # вернуться после сбоя питания, и завершённый переход снова запрёт контур.
    for _d in _dirs:
        try:
            _fd = _osd.open(_d, _osd.O_DIRECTORY)
            try:
                _osd.fsync(_fd)
            finally:
                _osd.close(_fd)
        except Exception as _exd:
            _left.append(f'{_d}: fsync каталога не выполнен ({_exd})')
    if isinstance(flags, list):
        flags.clear()
    # ОШИБКИ УБОРКИ НЕ ГЛОТАЮТСЯ (тридцать четвёртый круг, №8). Прежде unlink и fsync
    # стояли под `except: pass`, а список меток очищался НЕЗАВИСИМО от результата: осталась
    # или «воскресла» метка — торговля и замыкание обоих маршрутов запрещены навсегда, а
    # код считает, что убрал за собой. На Ф это прямой путь к пропущенному роллу и
    # поставочной зоне. Возвращаем неубранное — вызывающий обязан сказать вслух.
    return _left


def _snapshot_pair(broker, attempts=3):
    """Согласованный снимок брокера для перехода (двадцать пятый круг, №3).

    Начальный барьер читал заявки, ПОТОМ позиции; финальный — наоборот. Заявка,
    исполнившаяся между двумя чтениями и уже исчезнувшая из open orders, не попадала ни в
    сохранённый снимок позиций, ни в список живых заявок: книга и TRANSITION_COMPLETE
    записывались по устаревшей позиции. Ежедневный контур был исправлен от этой гонки
    (_snapshot_consistent), исполнитель — нет. Схема та же: заявки-позиции-заявки, и
    расхождение = отказ, а не догадка.
    """
    for _ in range(attempts):
        oo1 = list(broker.open_orders())
        pos = broker.net_positions()
        oo2 = list(broker.open_orders())
        if sorted(map(str, oo1)) == sorted(map(str, oo2)):
            return pos, oo2
    raise Incident(
        f'снимок брокера не стабилизировался за {attempts} попытки: заявки менялись во '
        f'время чтения позиций — исполнение шло прямо сейчас, сверка была бы ложной')


def _execute_locked(broker, state_path, capital, legs, signal_id, from_route, to_route,
                    in_common_window, resume, journal, mr_state, asof, registry,
                    plan, tid, reg, want_cls, src_cls, _M, emergency=False):
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
        if not (_nlv == _nlv and _nlv > 0) or _nlv in (float('inf'), float('-inf')):
            raise Incident(f'NLV у брокера {_nlv!r} — не конечное положительное число, '
                           f'капитал перехода непроверяем (№16: inf проходил все сравнения '
                           f'молча, и любой capital считался сверенным)')
        if abs(_nlv - float(capital)) > CAPITAL_TOL * _nlv:
            raise Incident(
                f'капитал перехода ${float(capital):,.0f} расходится с NLV брокера '
                f'${_nlv:,.0f} более чем на {CAPITAL_TOL:.0%}: по нему считаются лимит '
                f'§8б, маржа и число долей цели — исполнение запрещено')
    # ЦЕНЫ ПЛАНА СВЕРЯЮТСЯ С НЕЗАВИСИМЫМ ИСТОЧНИКОМ (двадцать девятый круг, №3) —
    # см. check_plan_prices. Вынесено отдельной функцией: у стенда должна быть
    # возможность проверить саму защиту, а не только сквозной путь.
    check_plan_prices(broker, legs, src_cls)
    # ВОРОТА СЧИТАЮТСЯ ОТ МЕНЬШЕГО ИЗ ДВУХ ЧИСЕЛ (двадцать третий круг, №4). Допуск 2%
    # проверяет БЛИЗОСТЬ, но все ворота дальше считались по числу ВЫЗЫВАЮЩЕГО, а оно может
    # быть выше фактического NLV на весь допуск. Два денежных пути: (а) порог §8 проверен
    # на строке выше по capital — при фактических $2,95 млн и переданных $3,008 млн вход в
    # Ф проходил, хотя счёт НИЖЕ жёсткого порога; (б) лимит непарной дельты при NLV $10 млн
    # и capital $10,19 млн становился 1,019% ФАКТИЧЕСКОГО счёта вместо 1%.
    _cap_eff = min(float(capital), _nlv)
    # emergency ПЕРЕДАЁТСЯ ЯВНО (двадцать четвёртый круг, №18): здесь его не было ни в
    # параметрах, ни в глобалах — короткое замыкание `and not emergency` скрывало NameError
    # во всех обычных случаях и стреляло ровно на аварийном выводе из Е ниже порога, то есть
    # на ЕДИНСТВЕННОМ разрешённом спасительном пути при падении капитала.
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
        # РЕЗУЛЬТАТ ТРЕВОГИ НЕ ВЫБРАСЫВАЕТСЯ (двадцать четвёртый круг, №17):
        # _alarm_transition ловит ошибку файловой системы и ВОЗВРАЩАЕТ текст, а не падает.
        # Прежде он игнорировался, и при ENOSPC/правах «любой MIXED ставит файл» было
        # ложью: журнал МР держал MIXED, автопилот его не читает, торговля продолжалась.
        _al = _alarm_transition(asof, f'переход {from_route}->{to_route} в MIXED: {reason}'[:400])
        if str(_al).strip():          # пустая строка = файл записан; текст = сбой записи
            raise Incident(
                f'MIXED зафиксирован в журнале, но ФАЙЛ ТРЕВОГИ НЕ СОЗДАН ({_al}) — '
                f'автопилот журнал МР не читает и продолжил бы торговлю поверх '
                f'разорванной книги; немедленный ручной разбор (О-5)')
        return _ok


    if os.path.exists(state_path):
        st = json.load(open(state_path))
        # DIGEST ПРОВЕРЯЕТСЯ ДО ЛЮБОГО РЕШЕНИЯ ПО ЭТОМУ СОСТОЯНИЮ (тридцатый круг, №13).
        # По нему считается, какие лоты завершены и сколько исполнено внутри прерванного:
        # правка этих чисел означает ПОВТОРНУЮ ПРОДАЖУ, а не спорный вердикт.
        _dg_want = st.get('digest')
        if not _dg_want:
            raise Incident('состояние перехода без digest — целостность прогресса '
                           'недоказуема, повтор запрещён (О-5)')
        if _dg_want != _state_digest(st):
            raise Incident('состояние перехода ПОВРЕЖДЕНО (digest не сходится) — по нему '
                           'считается, какие лоты завершены; повтор мог бы продать их '
                           'заново, исполнение запрещено (О-5)')
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
            # BASELINE — СОГЛАСОВАННЫЙ СНИМОК (двадцать шестой круг, №4). Прежде здесь
            # стоял ГОЛЫЙ net_positions(), а согласованный snap0 использовался только для
            # проверки и baseline не заменял — хотя именно st['snapshot'] участвует во всех
            # расчётах прогресса и финальных дельт. Гонка «позиция старая, заявки уже нет»
            # попадала прямо в опору всех вычислений.
            _snap_base, _ = _snapshot_pair(broker)
            st = dict(tid=tid, asof=str(asof or ''), postponed=st.get('postponed', 0),
                      done=[], executed_usd=0.0, order_ids=[],
                      snapshot=_snap_base, log=[])
            _atomic(state_path, st)
    else:
        # RESUME БЕЗ ФАЙЛА ПРОГРЕССА — АВАРИЯ, А НЕ НАЧАЛО С НУЛЯ (тридцать девятый круг,
        # №1). Здесь безусловно создавалось СВЕЖЕЕ состояние, чей snapshot — текущие позиции
        # брокера. Для нового перехода это верно. Но при resume позиции уже ПРОМЕЖУТОЧНЫЕ:
        # продолжение видело нулевой прогресс (done пуст, executed_usd=0) и исполняло ВЕСЬ
        # план заново — уже проданное продавалось второй раз, вплоть до short источника. Хуже
        # того, финальная сверка считает дельту от этого же ложного snapshot и способна
        # признать повторное исполнение соответствующим плану; gross поймал бы лишь часть
        # случаев и уже после лишних сделок.
        # Прогресс — единственный источник знания о том, что исполнено. Нет его — исход
        # прошлой попытки неизвестен, и это ручной разбор, а не чистый старт.
        if resume:
            raise Incident(
                f'resume запрошен, но файла прогресса нет ({state_path}): что уже исполнено '
                f'предыдущей попыткой — НЕИЗВЕСТНО, а позиции брокера могут быть '
                f'промежуточными. Начать «с нуля» значит исполнить план повторно и увести '
                f'источник в short; ручной разбор (О-5)')
        _snap_base, _ = _snapshot_pair(broker)
        st = dict(tid=tid, asof=str(asof or ''), postponed=0, done=[], executed_usd=0.0,
                  order_ids=[], snapshot=_snap_base, log=[])
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
        # ПЛАН УХОДИТ В ПРЕДПРОСМОТР (тридцать седьмой круг, №7): без него broker.preview()
        # спрашивал маржу ТЕКУЩЕЙ книги, то есть здоровьем исходной позиции доказывал
        # допустимость целевой. Целевые заявки собираются по лотам плана: сколько единиц
        # цели покупается на каждый источник.
        # ПРЕДПРОСМАТРИВАЕТСЯ ОСТАТОК, А НЕ ВЕСЬ ПЛАН (СОРОК ЧЕТВЁРТЫЙ КРУГ, №4, P0).
        # Здесь перебирались ВСЕ лоты, до разбора фактического прогресса. При частично
        # исполненном переходе целевая позиция уже куплена, а whatIf спрашивал покупку
        # ПОЛНОЙ цели ПОВЕРХ неё: законный resume выглядел книгой в 150-200% цели, получал
        # POSTPONED, и после третьего отказа — MIXED. То есть новая проверка оставляла
        # разорванную книгу без штатного продолжения — ровно то, чего она должна избегать.
        # Остаток берётся ТЕМ ЖЕ ключом, которым _run_lots пропускает исполненные лоты
        # (f"{src}:{step}" в st['done']): два разных правила разъехались бы при первой же
        # правке одного из них.
        try:
            _pv_orders = pv_remainder(plan, st.get('done'))
        except Exception:
            _pv_orders = {}
        # ПРИЗНАК АВАРИЙНОСТИ ДОХОДИТ ДО ПРЕДПРОСМОТРА (сорок второй круг, №4): он не
        # решает за preview, но вызывающий обязан называть намерение, иначе аварийный
        # выход неотличим от планового ни в коде, ни в разборе.
        _pv = (broker.preview(sorted(_pv_orders.items()), emergency=bool(emergency))
               if _pv_orders else broker.preview(emergency=bool(emergency)))
    except Exception as ex:
        if resume or st.get('opened') or st.get('executed_usd', 0.0) > TOL \
                or st.get('cancel_fills'):
            _mixed('исход не разобран'); _atomic(state_path, st)
            raise Incident(f'margin preview оборван ({ex}) при возможно изменённой книге — '
                           f'состояние MIXED, ручная сверка')
        raise Incident(f'margin preview оборван ({ex}) — переход не начат')
    if not _pv:
        # ПРИЧИНА ОТКАЗА ПРЕДПРОСМОТРА ИДЁТ В ЖУРНАЛ И В СООБЩЕНИЕ (44-й круг, №14):
        # прежде любой отказ — контракт, счёт, реестр, обрыв API — читался как «маржа не
        # прошла», и оператор искал деньги там, где сломан справочник.
        _why_pv = str(getattr(broker, '_preview_why', '') or 'маржа цели не проходит О-3-Е')
        st['log'].append(('preview_отказ', _why_pv))
        st['postponed'] += 1; _atomic(state_path, st)
        if st['postponed'] >= 3:
            if st['executed_usd'] > TOL or st.get('cancel_fills'):
                # cancel_fills (№11): исполнение, пойманное отменой, — прогресс; прежний
                # критерий по одному executed_usd писал бы ЛОЖНЫЙ ABORT при изменённой книге.
                _mixed('исход не разобран')
            else:
                # ПАРА OPEN+ABORT ТОЖЕ МОЖЕТ ОБОРВАТЬСЯ ПОСЛЕ ЗАПИСИ (тридцать восьмой
                # круг, №8). В 37-м круге я научил различать «OPEN уже в журнале» ТОЛЬКО
                # основной вызов hook('open'), а эта ветка осталась голой: при отказе
                # заверения журнала или кэша наружу уходило исключение, ABORT не писался,
                # тревоги не было — нормативный журнал держал бы OPEN навсегда. Меток
                # передачи здесь ещё нет, поэтому снимать нечего; закрыть журнал — обязаны.
                try:
                    hook('open')
                except BaseException as _exo3:
                    if not getattr(_exo3, 'committed', False):
                        raise
                    _al3 = _alarm_transition(
                        asof, f'OPEN записан при третьем отказе preview, но заверение не '
                              f'прошло ({_exo3}) — журнал может держать открытый переход')
                    raise Incident(
                        f'OPEN записан, заверение не прошло ({_exo3}); ABORT не '
                        f'подтверждён — ручной разбор (О-5){_al3}')
                try:
                    hook('abort')                  # честная пара OPEN+ABORT, pending снимается строго
                except BaseException as _exa3:
                    _al4 = _alarm_transition(
                        asof, f'ABORT после третьего отказа preview не записан ({_exa3}) — '
                              f'журнал держит открытый переход')
                    raise Incident(
                        f'ABORT после третьего отказа preview не записан ({_exa3}) — '
                        f'ручной разбор (О-5){_al4}')
            raise Incident(f'margin preview отклонён три раза — инцидент; последняя '
                           f'названная причина: {_why_pv}')
        return dict(status='POSTPONED', postponed=st['postponed'], why=_why_pv)
    st['postponed'] = 0; _atomic(state_path, st)
    _r0, _p0, _mx0, _an0, _sid0, _otid0, _mk0 = _M.derive_state(journal, __import__('datetime').date.fromisoformat(asof))
    if _otid0 == tid and not resume and not st.get('opened'):
        raise Incident('переход с этим tid уже захвачен в журнале — только resume')
    # ЧУЖОЙ ОТКРЫТЫЙ ПЕРЕХОД — АВАРИЯ, А НЕ ЧИСТЫЙ ОТКАЗ (тридцать девятый круг, №7).
    # derive_state отдаёт open_tid, но проверялись только MIXED и аномалии. Для ЧУЖОГО
    # открытого tid запуск доходил до _mark_handover, там hook('open') возвращал False, и
    # обработчик «журнал отклонил открытие» снимал метки — В ТОМ ЧИСЛЕ ЧУЖИЕ, поставленные
    # незавершённым переходом. Нормативный журнал оставался в OPEN, а единственный барьер,
    # который читает ежедневный контур, исчезал: автопилот торговал бы поверх открытого
    # перехода. Старый OPEN не доказывает отсутствие прошлых заявок — это О-5, и отказать
    # надо ДО того, как что-либо будет создано или снято.
    if _otid0 and _otid0 != tid:
        raise Incident(
            f'в журнале открыт ДРУГОЙ переход tid={_otid0!r} (наш {tid!r}) — прошлые '
            f'заявки не исключены, а его файловые метки снимать нельзя; исполнение '
            f'запрещено до ручного разбора (О-5)')
    # СПИСОК МЕТОК ОБЪЯВЛЕН ДО ВЕТКИ (двадцать шестой круг, №12): он определялся только
    # внутри `if not resume`, а снимался безусловно — на resume это UnboundLocalError УЖЕ
    # ПОСЛЕ записи TRANSITION_COMPLETE: брокер переведён, книга опубликована, журнал
    # закрыт, и падение. Повтор невозможен (маршрут сменился), автопилот стоял бы до
    # ручной хирургии. На resume метки ставит тот же код ниже.
    _hoflags = []
    if not resume:
        # БАРЬЕР ЖИВЫХ ЗАЯВОК (восемнадцатый круг, №3): осиротевшая заявка другого клиента
        # или терминала исполнится поперёк плана и пробьёт лимит §8б — сверка позиций её
        # не видит. Чужое снимать нельзя — только отказ до первой заявки.
        snap0, _live0 = _snapshot_pair(broker)          # №3: согласованный снимок
        # КОНЕЧНОСТЬ ВХОДНЫХ ПОЗИЦИЙ — ДО ПЕРВОЙ ЗАЯВКИ (двадцать шестой круг, №5). Сверка
        # источника `abs(float(snap0[instr]) - units) > 1e-9` пропускает NaN: сравнение с
        # NaN ЛОЖНО. Такой снимок доходил до _run_lots, и продажа планового количества при
        # НЕИЗВЕСТНОЙ исходной позиции могла открыть short — а ловил это лишь финальный
        # цикл, уже после продаж и покупок.
        for _k0, _v0 in (snap0 or {}).items():
            try:
                _f0 = float(_v0)
            except (TypeError, ValueError):
                raise Incident(f'{_k0}: входная позиция {_v0!r} не число — переход запрещён')
            if _f0 != _f0 or _f0 in (float('inf'), float('-inf')):
                raise Incident(f'{_k0}: входная позиция {_v0!r} не конечна — плановая '
                               f'продажа при неизвестной позиции открыла бы short')
        if _live0:
            raise Incident(f'живые заявки на счёте до перехода {_live0[:4]} — исполнение '
                           f'запрещено до их разбора')
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
    # ПРЕДПОЛЁТНАЯ ПРОВЕРКА ПЕРЕДАЧИ — ДО ПЕРВОЙ ЗАЯВКИ (двадцать пятый круг, №7).
    # Исходная книга, старое намерение целевого маршрута, целевой журнал и серии
    # проверялись только внутри hand_over_book, то есть ПОСЛЕ полного перевода денег:
    # повреждённый journal-E.csv или оставшийся book-E.json.intent.json обнаруживались
    # уже после продажи Ф и покупки Е, и результат — переведённая позиция, которую
    # ежедневный контур сознательно не принимает. Те же проверки прогоняются сухо
    # ЗАРАНЕЕ; отказ здесь не стоит ничего, кроме отложенного перехода.
    try:
        _preflight_handover(from_route, to_route,
                            _dst_names=[spec['dst'][0] for spec in legs.values()],
                            _broker_p=broker, _resume=resume)
    except Exception as _exph:
        raise Incident(f'предполётная проверка передачи книги не пройдена ({_exph}) — '
                       f'переход не начат, деньги не переведены')
    # ОТМЕТКА ОБХОДА — ПОСЛЕ СУХИХ ПРОВЕРОК ИСПОЛНИТЕЛЯ (двадцать девятый круг, №17).
    # В _execute_guarded она стояла ПЕРЕД вызовом исполнителя, а сами сухие проверки
    # (предполёт передачи, согласованный снимок, маржа) живут ВНУТРИ него — то есть
    # запись об обходе порога всё равно опережала их и оставалась в журнале при отказе.

    # МЕТКИ — ПОСЛЕ ВОРОТ ОКНА И ЗАПИСИ OPEN (двадцать девятый круг, №8): прежде они
    # ставились ДО _window_till и hook(open), и ошибка календаря, закрытое окно или отказ
    # журнала оставляли метки навсегда — торговля и замыкание запирались, хотя ни одной
    # заявки не подавалось. Здесь переход уже открыт, и следующий шаг — заявки.
    # МЕТКА СТАВИТСЯ ПЕРЕД ПЕРВОЙ ЗАЯВКОЙ, ПОСЛЕ ВСЕХ СУХИХ ПРОВЕРОК
    # (двадцать шестой круг, №11: прежде — до предполёта, и любой чистый отказ
    # оставлял метки навсегда, запирая торговлю и замыкание до ручной уборки)
    # МЕТКА ОБЯЗАТЕЛЬНА И ВИДНА ОБОИМ МАРШРУТАМ (двадцать четвёртый круг, №15;
    # двадцать пятый, №6: метки ставятся ДО ПЕРВОЙ ЗАЯВКИ, а не после перевода денег). Прежде
    # сбой её создания проглатывался (_hoflag=None), то есть окно оставалось открытым ровно
    # тогда, когда файловая система уже нездорова. И имя несло ТОЛЬКО целевой маршрут: при
    # Ф→Е и смерти до записи route.txt автопилот оставался на Ф и проверял handover-inflight-F,
    # не замечая существующий handover-inflight-E. Пишем ОБЕ метки; не удалось — отказ ДО
    # публикации книги, пока ничего не переведено.
    def _mark_handover():
        """Барьеры незавершённой передачи для ОБОИХ маршрутов (вынесено в 30-м круге,
        №12: их надо ставить ДО фиксации OPEN, поэтому блок стал функцией)."""
        import state as _ST3
        # список уже объявлен выше (№12) — здесь только заполняется
        try:
            for _r in (to_route, from_route):
                _f = _ST3.lock_dir() / f'handover-inflight-{_r}.txt'
                # ПУТЬ ПОПАДАЕТ В СПИСОК УБОРКИ ДО ЗАПИСИ (тридцать девятый круг, №6).
                # Прежде append стоял ПОСЛЕ обоих fsync: если файл уже создан, а fsync файла
                # или каталога отказал, путь в _hoflags не попадал, _drop_handover его не
                # удалял, _left_m выходил пустым — и сообщение объявляло ЧИСТЫЙ отказ, тогда
                # как на диске оставалась метка handover-inflight. Она навсегда запрещает
                # торговлю и замыкание, включая ролл у поставочной зоны. Лишний путь в
                # списке безвреден (unlink несуществующего файла обрабатывается), пропущенный
                # — запирает контур: список обязан быть надмножеством созданного.
                _hoflags.append(_f)
                _f.write_text(f'{asof} tid={tid} {from_route}->{to_route}: книга передаётся, '
                              f'COMPLETE ещё не записан\n', encoding='utf-8')
                # FSYNC (двадцать шестой круг, №11): без него барьер мог ИСЧЕЗНУТЬ после уже
                # начатых сделок при потере питания — то есть пропасть ровно тогда, когда нужен.
                import os as _osf                      # локально: _os есть не во всех ветках
                with open(_f, 'r+b') as _fh:
                    _osf.fsync(_fh.fileno())
                # FSYNC КАТАЛОГА, А НЕ ТОЛЬКО ФАЙЛА (тридцать второй круг, №14). Долговечны
                # были БАЙТЫ, но не сама запись в каталоге: при потере питания новая
                # directory entry могла исчезнуть, тогда как TRANSITION_OPEN и часть заявок
                # уже долговечны. После перезапуска ежедневный контур не увидел бы
                # ЕДИНСТВЕННОГО барьера и торговал бы исходную книгу поверх частично
                # переведённой позиции — то есть заявленная долговечность метки была
                # фиктивной ровно в том случае, ради которого метку и заводили.
                _dfd = _osf.open(str(_f.parent), _osf.O_DIRECTORY)
                try:
                    _osf.fsync(_dfd)
                finally:
                    _osf.close(_dfd)
        except Exception as _exf:
            # ОСТАТОК УБОРКИ НАЗЫВАЕТСЯ (тридцать пятый круг, №6): _drop_handover очищает
            # список даже при неудачном unlink/fsync, и оставшаяся первая метка запирала бы
            # ОБА маршрута молча.
            _left_m = _drop_handover(_hoflags)
            raise RuntimeError(
                (f'МЕТКИ НЕ СНЯТЫ {_left_m} — торговля и замыкание останутся запрещены; '
                 if _left_m else '') +
                f'метка незавершённой передачи не создана ({_exf}) — без неё обрыв между '
                f'публикацией книги и COMPLETE остался бы невидимым для контура; передача '
                f'не начата, деньги не переведены')

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
        # ABORT ТРЕБУЕТ ДОКАЗАТЕЛЬСТВА ОТСУТСТВИЯ СДЕЛОК, А НЕ ОТСУТСТВИЯ ДОКАЗАТЕЛЬСТВ
        # (тридцатый круг, №5). Прежде исход считался чистым, когда executed_usd, moved и
        # cancel_fills пусты. Но если sell_units() ИСПОЛНИЛ заявку и упал до возврата,
        # номер заявки не записан (отменять нечего), заявка уже ушла из open_orders, а
        # снимок позиций может отставать: moved==0, и состоявшаяся продажа уходит в журнал
        # как ABORT. Дальше pending снимается, поздний fill ложится поверх исходной книги
        # вне всякого учёта. Правило проекта прямое: исполнение — только по permId-отчётам.
        # Спрашиваем отчёты дня; пусто И барьер отработал — только тогда ABORT. Нет способа
        # спросить или барьер не отработал — MIXED: ручной разбор дешевле лишней позиции.
        _ex_proof, _ex_note = None, ''
        _exf = getattr(broker, 'todays_executions', None)
        if callable(_exf):
            try:
                _all_ex = list(_exf() or [])
                # ОТЧЁТЫ СОПОСТАВЛЯЮТСЯ С НАШИМИ ЗАЯВКАМИ (тридцать четвёртый круг, №9).
                # Прежде брался ВЕСЬ список дня: обычный утренний ребаланс идёт под тем же
                # orderRef='ADDFUT', поэтому к запуску перехода список уже непуст — и ЛЮБОЙ
                # последующий чистый отказ классифицировался как MIXED. Следствие: pending
                # и обе метки передачи остаются, торговля и замыкание заблокированы, хотя
                # переход не подал ни одной заявки; ближайший ролл может быть пропущен.
                # Сопоставляем с номерами ЭТОГО перехода; полное сопоставление отчётов с
                # намерением — открытый долг 28-го круга №3, и он назван отдельно.
                # ЗНАК ВЫВОДА БЫЛ ИНВЕРТИРОВАН (тридцать пятый круг, №4). Прежде отсутствие
                # НАШИХ отчётов при наличии чужих записывалось в _ex_note, а _ex_note любой
                # непустой строкой означал MIXED — то есть ДОКАЗАННОЕ отсутствие исполнений
                # перехода само же и запрещало чистый ABORT. После обычного утреннего
                # ребаланса (тот же orderRef) чистый нулевой fill оставлял бы pending и обе
                # метки передачи: торговля и замыкание заблокированы, ролл может быть
                # пропущен. Плюс сравнение шло ПОДСТРОКОЙ: «12» совпадает с чужим 9123.
                # Теперь: точное совпадение; отсутствие наших отчётов — довод ЗА чистый
                # исход; неизвестность появляется только когда попытка подачи БЫЛА, а
                # номера не записались (потерянное подтверждение, №1).
                _mine = {str(x) for x in (st.get('order_ids') or [])}
                _attempted = int(st.get('attempted', 0))
                if _mine:
                    _ex_proof = [e for e in _all_ex if str(e) in _mine]
                    # РАЗНЫЕ ПРОСТРАНСТВА ИДЕНТИФИКАТОРОВ (тридцать шестой круг, №5).
                    # Состояние хранит номер, который вернул брокер перехода (у живого
                    # адаптера — локальный orderId), а todays_executions отдаёт permId:
                    # строковое равенство между ними НЕВОЗМОЖНО в принципе. Значит пустое
                    # пересечение здесь не доказывает «наших сделок не было» — оно означает
                    # «сопоставить нечем». Полное сопоставление отчётов с намерением —
                    # открытый долг двадцать восьмого круга №3; пока он открыт, отсутствие
                    # совпадений при НЕПУСТОМ списке отчётов дня обязано читаться как
                    # неизвестность, а не как чистый исход.
                    if _all_ex and not _ex_proof:
                        _ex_note = (' | отчёты дня есть, но сопоставить их с номерами '
                                    'перехода НЕЧЕМ (orderId против permId, долг 28-го №3) '
                                    '— отсутствие наших сделок НЕДОКАЗУЕМО, не ABORT')
                elif _attempted:
                    _ex_proof = []
                    _ex_note = (f' | попытки подачи были ({_attempted}), но номера заявок '
                                f'не записались — исход подачи НЕИЗВЕСТЕН, не ABORT')
                else:
                    # Ни одной попытки подачи: отчёты дня принадлежат другим операциям и
                    # доказательством сделки ПЕРЕХОДА не являются.
                    _ex_proof = []
            except Exception as _exe:
                _ex_note = f' | БАРЬЕР ОТЧЁТОВ НЕ ОТРАБОТАЛ ({_exe}) — исход не ABORT'
        else:
            _ex_note = (' | брокер не отдаёт отчёты об исполнении — отсутствие сделок '
                        'НЕДОКАЗУЕМО, исход не ABORT')
        if _ex_proof:
            _ex_note = (f' | ОТЧЁТЫ ОБ ИСПОЛНЕНИИ ЕСТЬ ({len(_ex_proof)}) — сделка '
                        f'состоялась, исход не ABORT')
        msg += _ex_note
        kind = 'mixed' if (stuck or st['executed_usd'] > TOL or moved > 0
                           or st.get('cancel_fills') or _ex_note) else 'abort'
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
        # МЕТКИ СНИМАЮТСЯ ПРИ ДОКАЗАННОМ ЧИСТОМ ABORT (тридцать третий круг, №7). Метки
        # ставятся ДО журнального OPEN (32-й круг, №12), и чистый отказ ПОСЛЕ открытия —
        # например, ворота лота при менее чем 45 минутах до края окна — оставлял их
        # навсегда: брокер не тронут, pending снят, а торговля и замыкание ОБОИХ маршрутов
        # запрещены до ручной уборки; ближайший ролл пропускается, серия идёт к поставке.
        # Только ABORT: при MIXED книга могла измениться, и барьер обязан остаться.
        if kind == 'abort':
            _left_ho = _drop_handover(_hoflags)
            if _left_ho:
                msg += (f' | МЕТКИ ПЕРЕДАЧИ НЕ СНЯТЫ {_left_ho} — торговля и замыкание '
                        f'останутся запрещены до ручной уборки (О-5)')
        # тревога уже поставлена внутри _mixed (№14) — второй раз не ставим
        raise Incident(msg)

    _wt = _window_till(asof)
    # МЕТКИ СТАВЯТСЯ ДО OPEN — И ДО ЖУРНАЛЬНОГО ТОЖЕ (тридцатый круг, №12; тридцать первый,
    # №12). Тридцатый круг перенёс маркеры перед st['opened'], но hook('open') остался
    # ВЫШЕ: между записью TRANSITION_OPEN в нормативный журнал и появлением барьеров
    # сохранялся тот же зазор. Смерть процесса в нём оставляет журнал МР в OPEN без
    # единственного файла, который читает ежедневный контур, — автопилот продолжает менять
    # исходную книгу, и resume работает уже против другой позиции.
    # ЧИСТЫЙ ОТКАЗ УБИРАЕТ МЕТКИ ЗА СОБОЙ (двадцать шестой круг, №11; двадцать девятый, №8):
    # если журнал открытие отверг, ни одной заявки не подано, и оставленные барьеры заперли
    # бы торговлю и замыкание навсегда. Снимаются ТОЛЬКО те, что поставил этот вызов.
    _mark_handover()
    try:
        _open_ok = hook('open')       # проверяется ВСЕГДА, включая resume (идемпотентно)
    except BaseException as _exo0:
        # OPEN МОГ СОСТОЯТЬСЯ (тридцать седьмой круг, №13). confirm_transition пишет событие
        # долговечно, а производный кэш — после; отказ кэша прежде выглядел здесь ЧИСТЫМ
        # отказом открытия: метки снимались, ABORT не писался, и журнал оставался в OPEN без
        # единственного файлового барьера ежедневного контура. Если событие в журнале уже
        # есть, снимать метки нельзя — переход надо закрыть нормативно. Заявок ещё не было,
        # поэтому честный терминал — ABORT; не удался и он — MIXED с тревогой.
        if getattr(_exo0, 'committed', False):
            try:
                _ab_ok = hook('abort')
            except BaseException:
                _ab_ok = False
            if _ab_ok:
                _left_c = _drop_handover(_hoflags)
                raise Incident(
                    f'открытие записано в журнал, но кэш состояния не сохранён ({_exo0}) — '
                    f'переход закрыт ABORT до первой заявки'
                    + (f'; МЕТКИ НЕ СНЯТЫ {_left_c} (О-5)' if _left_c else ''))
            _mixed('кэш состояния не сохранён после OPEN')
            raise Incident(
                f'открытие записано в журнал, кэш не сохранён ({_exo0}) И ABORT не принят — '
                f'журнал держит OPEN, метки оставлены намеренно: ручной разбор (О-5)')
        _left_o = _drop_handover(_hoflags)
        if _left_o:
            # РЕЗУЛЬТАТ ЗАПИСИ ТРЕВОГИ НЕ ГЛОТАЕТСЯ (тридцать шестой круг, №9): при
            # одновременном отказе удаления и ALARM наружу уходило только исходное
            # исключение, и контур оставался заперт молча.
            _al_o = _alarm_transition(asof, f'открытие перехода оборвано ({_exo0}), и метки '
                                            f'НЕ сняты {_left_o} — контур заперт')
            if str(_al_o).strip():
                raise Incident(f'открытие оборвано ({_exo0}); метки НЕ сняты {_left_o}; '
                               f'и ТРЕВОГА НЕ ЗАПИСАНА ({_al_o}) — немедленный ручной '
                               f'разбор (О-5)')
        raise
    if not _open_ok:
        _left_j = _drop_handover(_hoflags)
        if _left_j:
            _alarm_transition(asof, f'журнал отклонил открытие, и метки НЕ сняты {_left_j}')
        raise Incident('журнал отклонил открытие перехода (нет сигнала/sid/чужой tid) — '
                       'исполнение запрещено'
                       + (f' | МЕТКИ НЕ СНЯТЫ {_left_j} (О-5)' if _left_j else ''))
    # СБОЙ ФИКСАЦИИ OPEN В СОСТОЯНИИ — ЭТО ABORT, А НЕ СЫРОЕ ИСКЛЮЧЕНИЕ (тридцать первый
    # круг, №12): журнал МР уже держит TRANSITION_OPEN, и выход мимо fail() оставил бы его
    # открытым навсегда. Заявок ещё не было — отмена ничего не стоит.
    try:
        st['opened'] = True; _atomic(state_path, st)
    except BaseException as _exo:
        fail(f'состояние перехода не записалось после OPEN ({_exo}) — переход прерван до '
             f'первой заявки', cancel=False)
        raise

    # EMERGENCY_OVERRIDE — ПОСЛЕ ВОРОТ ОКНА И ЗАПИСИ OPEN (тридцатый круг, №18).
    # Событие писалось до _window_till(asof) и до hook('open'): закрытое окно, ошибка
    # календаря или отказ журнала оставляли в нормативной истории запись об аварийном
    # обходе порога, которого не было, — прямой ложный аудиторский след. Здесь переход
    # уже открыт и следующим шагом подаёт заявки.
    # АВАРИЙНАЯ ОТМЕТКА — ПОСЛЕ ВСЕХ СУХИХ ПРОВЕРОК (двадцать восьмой круг, №14).
    # Прежде EMERGENCY_OVERRIDE ложился в нормативный журнал ПЕРВЫМ: отказ на любой
    # последующей проверке оставлял в истории запись об обходе порога, которого не
    # было. Пишем ровно перед исполнением, когда переход уже признан возможным.
    if emergency and journal:
        # АВАРИЙНЫЙ ОБХОД ПОРОГА ЗАПИСЫВАЕТСЯ. Иначе признак emergency — молчаливый способ
        # обойти жёсткое ограничение §8, и по журналу нельзя отличить штатный переход от
        # обхода: событие пишется ДО первой заявки, а не после.
        # И ЭТА ЗАПИСЬ — ПОД АВАРИЙНОЙ ОБОЛОЧКОЙ (тридцать первый круг, №12): журнал МР уже
        # в OPEN, и падение append_event мимо fail() оставило бы его открытым навсегда.
        import mr_engine as _MJ
        try:
            _MJ.append_event(journal, str(asof or ''), 'EMERGENCY_OVERRIDE',
                             f'{from_route}->{to_route}|NLV={float(capital):.0f}|sid={signal_id}')
        except BaseException as _exe0:
            fail(f'запись EMERGENCY_OVERRIDE не выполнена ({_exe0}) — переход прерван до '
                 f'первой заявки', cancel=False)
            raise



    # ПОСЛЕДНЯЯ СВЕРКА — НЕПОСРЕДСТВЕННО ПЕРЕД ПЕРВОЙ ЗАЯВКОЙ (тридцать шестой круг, №4).
    # Предполёт снимает позиции и заявки, но после него идут запрос доходности, чтение
    # целевого журнала, вычисление окна, создание и fsync двух меток, запись OPEN, состояние
    # и, возможно, EMERGENCY_OVERRIDE. Поздний fill или ручная сделка в этом окне меняют
    # исходную позицию, а исполнитель продаёт ПЛАНОВОЕ количество: поздняя продажа 10 ZN
    # плюс плановая 101 ZN открывают short. Финальная сверка увидела бы это уже после
    # денежного ущерба. Отказ здесь ещё ничего не стоит: ни одной заявки не подано.
    # СВЕРКА ОБЯЗАНА БЫТЬ ТОЙ ЖЕ, ЧТО В ПРЕДПОЛЁТЕ (тридцать седьмой круг, №5). Первая
    # редакция этой проверки повторяла ТОЛЬКО количества инструментов-источников: появившаяся
    # позиция класса ЦЕЛИ, посторонний инструмент, расхождение счёта с книгой и устаревший
    # NLV проходили насквозь. Поздняя покупка CSPX между предполётом и этой точкой означала
    # бы покупку полной цели ПОВЕРХ неё, а обнаружилось бы это только финальной сверкой —
    # то есть после собственных заявок. Проверка «часть условий из списка» опаснее её
    # отсутствия: она выглядит как повтор предполёта и снимает вопрос.
    if not resume:
        _pos_fin0, _oo_fin0 = _snapshot_pair(broker)
        if _oo_fin0:
            fail(f'живые заявки {_oo_fin0[:4]} появились перед первой заявкой перехода — '
                 f'исход прошлой операции неизвестен', cancel=False)
        _planned_src0 = {i for sp in legs.values() for i, _, _ in sp['src']}
        for _nm0, _sp0 in sorted(legs.items()):
            for _i0, _u0, _ in _sp0['src']:
                if abs(float((_pos_fin0 or {}).get(_i0, 0)) - _units(_i0, _u0)) > 1e-9:
                    fail(f'{_i0}: позиция изменилась между предполётом и первой заявкой '
                         f'({(_pos_fin0 or {}).get(_i0, 0)} против плановых '
                         f'{_units(_i0, _u0)}) — план считался по устаревшему счёту',
                         cancel=False)
        for _i0, _q0 in sorted((_pos_fin0 or {}).items()):
            try:
                _q0f = float(_q0)
            except (TypeError, ValueError):
                fail(f'{_i0}: позиция {_q0!r} не число перед первой заявкой', cancel=False)
                continue
            if _q0f != _q0f or _q0f in (float('inf'), float('-inf')):
                fail(f'{_i0}: позиция {_q0!r} не конечна перед первой заявкой', cancel=False)
            if not _q0f:
                continue
            if _i0 not in reg:
                fail(f'{_i0}: неизвестный реестру инструмент ({_q0}) появился перед первой '
                     f'заявкой перехода', cancel=False)
                continue
            if reg[_i0]['sec_type'] == src_cls and _i0 not in _planned_src0:
                fail(f'{_i0}: позиция класса источника ({_q0}) вне плана появилась перед '
                     f'первой заявкой — книга переводится не целиком', cancel=False)
            if reg[_i0]['sec_type'] == want_cls:
                fail(f'{_i0}: позиция класса ЦЕЛИ ({_q0}) появилась между предполётом и '
                     f'первой заявкой — полная цель легла бы поверх неё', cancel=False)
        # NLV, ОТ КОТОРОГО СЧИТАН ЛИМИТ 1%, ОБЯЗАН БЫТЬ СВЕЖИМ: между предполётом и этой
        # точкой счёт мог измениться, и лимит непарной дельты считался бы от чужого числа.
        try:
            _nlv_now = float(broker.net_liquidation())
        except Exception as _exn:
            fail(f'NLV перед первой заявкой недоступен ({_exn}) — лимит непарной дельты '
                 f'непроверяем', cancel=False)
            _nlv_now = float('nan')
        if _nlv_now == _nlv_now and _cap_eff and \
                abs(_nlv_now - _cap_eff) / abs(_cap_eff) > CAPITAL_TOL:
            fail(f'NLV перед первой заявкой ${_nlv_now:,.0f} разошёлся с капиталом плана '
                 f'${_cap_eff:,.0f} более чем на {CAPITAL_TOL:.0%} — лимит 1% и маржа '
                 f'предполёта считались по устаревшему счёту', cancel=False)

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
                # ДРОБНОСТЬ ПРОГРЕССА — ЧЕРЕЗ fail(), А НЕ МИМО (двадцать четвёртый круг,
                # №12). _int_fill поднимает Incident на дробном фьючерсном прогрессе, а
                # внешний обработчик делал `except Incident: raise` — минуя отмену заявок,
                # _mixed() и файл тревоги. Компенсационная заявка при этом уже изменила
                # книгу брокера, журнал оставался в OPEN, и ежедневный контур мог войти
                # поверх промежуточной позиции.
                ds = snap.get(instr, 0) - now.get(instr, 0)
                try:
                    _int_fill(ds, instr)
                except Incident as ex:
                    fail(f'{instr}: дробный прогресс источника при resume ({ex})')
                if ds < 0:
                    fail(f'{instr}: позиция выросла во время перехода — ручная сверка')
                sold_leg += ds*u; src_prog[instr] = ds
            db = now.get(di, 0) - snap.get(di, 0)
            try:
                _int_fill(db, di)
            except Incident as ex:
                fail(f'{di}: дробный прогресс цели при resume ({ex})')
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
        # №3: тот же согласованный снимок, что и на входе — заявки-позиции-заявки
        now, _live_fin = _snapshot_pair(broker)     # №4: список заявок ИСПОЛЬЗУЕТСЯ ниже
    except Incident:
        raise
    except Exception as ex:
        fail(f'финальная сверка: позиции недоступны ({ex}) — исход недоказуем')
    snap = st['snapshot']
    # NaN НЕ ПРОХОДИТ (двадцать пятый круг, №8). Для NaN ЛОЖНЫ и `got_units < 0`, и
    # `abs(...) > tolerance`, и проверка посторонней позиции — то есть все ворота молчат.
    # book_from_broker включит его (NaN != 0), маршрут Е сохранит прямо в BookE, а JSON
    # допускает NaN, поэтому состояние получит корректный digest и дойдёт до COMPLETE.
    for _k, _v in (now or {}).items():
        try:
            _fv = float(_v)
        except (TypeError, ValueError):
            fail(f'{_k}: позиция брокера {_v!r} не число — сверка недоказуема')
            continue
        if _fv != _fv or _fv in (float('inf'), float('-inf')):
            fail(f'{_k}: позиция брокера {_v!r} — не конечное число; такая величина '
                 f'проходит ВСЕ сравнения молча и дошла бы до COMPLETE')
    # ЦЕЛОЧИСЛЕННОСТЬ ФЬЮЧЕРСНОЙ ПОЗИЦИИ (двадцать девятый круг, №10). Финальная сверка
    # проверяла ДОЛЛАРОВЫЙ допуск до половины единицы цели, но не требовала, чтобы сама
    # позиция была целой: дробный остаток фьючерса (следствие частичного фила или ошибки
    # брокера) укладывался в допуск и уходил в книгу, где нога считается целыми контрактами.
    for _kf, _vf in (now or {}).items():
        if not str(_kf).startswith(('ES', 'MES', 'ZN')):
            continue
        _ff = float(_vf or 0)
        if abs(_ff - round(_ff)) > 1e-9:
            fail(f'{_kf}: дробная фьючерсная позиция {_ff:+g} у брокера — книга считает '
                 f'ногу ЦЕЛЫМИ контрактами, COMPLETE запрещён (ручная сверка)')
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
            # ДОПУСК, А НЕ ТОЧНОЕ РАВЕНСТВО (двадцать четвёртый круг, №19). Источником
            # бывают ДРОБНЫЕ доли фондов, и 0.3 - 0.2 не обязано равняться float 0.1:
            # успешный Е→Ф после всех исполненных заявок объявлялся MIXED, handover не
            # выполнялся, и состояние оставалось на Е при фактических фьючерсах у брокера.
            # Вход в переход уже считает дроби с допуском 1e-9 — сверка обязана тем же.
            _closed = float(snap.get(instr, 0)) - float(now.get(instr, 0))
            if abs(_closed - float(units)) > 1e-9 * max(1.0, abs(float(units))):
                fail(f'{instr}: закрыто не по плану ({_closed:+g} против {float(units):+g})')
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
    # СПИСОК УЖЕ ВЗЯТ СОГЛАСОВАННО ВМЕСТЕ С ПОЗИЦИЯМИ (двадцать шестой круг, №4): отдельный
    # ПОЗДНИЙ запрос создавал ровно ту гонку, от которой защищает _snapshot_pair —
    # стабильная живая заявка успевала исполниться между снимком позиций и этим чтением,
    # позиции оставались старыми, заявки исчезали, и публиковались книга и COMPLETE.
    if _live_fin:
        fail(f'живые заявки на счёте после перехода {_live_fin[:4]} — исполнятся вне '
             f'учёта, COMPLETE запрещён до разбора')
    try:
        g = broker.gross(_gross_dfix() or None)
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
    # ИСКЛЮЧЕНИЕ ИЗ hook('complete') — ТОЖЕ ИСХОД, А НЕ СЫРАЯ ОШИБКА (тридцать шестой круг,
    # №9). Отказ (False) обрабатывался, а ИСКЛЮЧЕНИЕ уходило наружу мимо _mixed и мимо
    # тревоги: книга и route.txt уже опубликованы, ежедневный контур видит согласованное
    # состояние и торгует дальше, а нормативный журнал навсегда остаётся в OPEN.
    try:
        _complete_ok = hook('complete')
    except Exception as _exc0:
        _mixed('исход не разобран')
        _al_c = _alarm_transition(asof, f'книга и route.txt опубликованы, но запись COMPLETE '
                                        f'ОБОРВАЛАСЬ ({_exc0}) — журнал МР остался в OPEN')
        raise Incident(f'запись COMPLETE оборвана ({_exc0}) ПОСЛЕ публикации книги и '
                       f'route.txt — состояние MIXED, ручная сверка{_al_c}')
    if not _complete_ok:
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
    # СНЯТИЕ ТОЖЕ ДОЛГОВЕЧНО (тридцать третий круг, №7): без fsync каталога завершённый
    # переход после сбоя питания «воскресает» незавершённым и даёт тот же стоп контура.
    _left_fin = _drop_handover(_hoflags)
    if _left_fin:
        _al2 = _alarm_transition(asof, f'COMPLETE записан, но метки передачи НЕ сняты '
                                       f'{_left_fin} — контур останется заперт')
        raise Incident(f'переход завершён, но метки передачи не сняты {_left_fin}: '
                       f'торговля и замыкание запрещены до ручной уборки (О-5){_al2}')
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
        # ДОЛГОВЕЧНО И ПРОВЕРЯЕМО (тридцать шестой круг, №10). Прежде был голый write_text:
        # ни fsync файла и каталога, ни проверки размера, ни запасного имени, ни STOP. При
        # потере питания успешный возврат '' ничего не доказывал, а автопилот журнал МР не
        # читает — то есть «любой MIXED останавливает автопилот файлом» оставалось ложью
        # ровно в том случае, ради которого тревога и пишется.
        p = _STa.lock_dir() / f'ALARM-transition-{asof}.txt'
        _txt = f'{reason}; ручной разбор (О-5)\n'
        try:
            with open(p, 'w', encoding='utf-8') as _fh:
                _fh.write(_txt); _fh.flush(); _o.fsync(_fh.fileno())
            _dfd = _o.open(str(p.parent), _o.O_DIRECTORY)
            try:
                _o.fsync(_dfd)
            finally:
                _o.close(_dfd)
        except OSError:
            # ЗАПАСНОЕ ИМЯ, КАК У АВТОПИЛОТА: ACL «менять можно, создавать нельзя» не смеет
            # оставить переход без следа.
            # ЗАПАСНОЕ ИМЯ ЗАВЕРЯЕТСЯ ТАК ЖЕ, КАК ОСНОВНОЕ (тридцать седьмой круг, №14).
            # Прежде здесь fsync-ился только ФАЙЛ: запись каталога оставалась незаверенной, и
            # после потери питания успешный возврат '' обещал тревогу, которой на диске нет.
            # Долговечность, сделанная на одной из двух веток, — это отсутствие долговечности
            # ровно тогда, когда основная ветка уже отказала.
            p = _STa.lock_dir() / f'ALARM-transition-fallback-{_o.getpid()}.txt'
            with open(p, 'w', encoding='utf-8') as _fh:
                _fh.write(_txt); _fh.flush(); _o.fsync(_fh.fileno())
            _dfd2 = _o.open(str(p.parent), _o.O_DIRECTORY)
            try:
                _o.fsync(_dfd2)
            finally:
                _o.close(_dfd2)
        if not p.exists() or not p.stat().st_size:
            raise OSError('файл тревоги пуст после записи')
        return ''
    except Exception as ex:
        # МЕЖПРОЦЕССНЫЙ СТОП (№10): не записав тревогу, обязаны хотя бы остановить контур —
        # автопилот читает STOP-файл первым.
        # СТОП ТОЖЕ ОБЯЗАН БЫТЬ ДОЛГОВЕЧНЫМ И ПРОВЕРЕННЫМ (№14): здесь стоял голый
        # write_text без fsync, без проверки, что файл появился, и с проглоченной ошибкой.
        # Это последний барьер: если он молча не сработал, контур продолжит торговать поверх
        # разорванной книги, а сообщение наружу будет утверждать, что STOP поставлен.
        _stop_ok = False
        try:
            import state as _STs
            _sp = _STs.lock_dir() / 'autopilot.STOP'
            with open(_sp, 'w', encoding='utf-8') as _sh:
                _sh.write(f'тревога перехода не записана: {ex}\n')
                _sh.flush(); _o.fsync(_sh.fileno())
            _sfd = _o.open(str(_sp.parent), _o.O_DIRECTORY)
            try:
                _o.fsync(_sfd)
            finally:
                _o.close(_sfd)
            _stop_ok = _sp.exists() and bool(_sp.stat().st_size)
        except Exception:
            _stop_ok = False
        if not _stop_ok:
            return (f' | ТРЕВОГА НЕ ЗАПИСАНА ({ex}) И STOP НЕ ПОСТАВЛЕН — автопилот НИЧЕМ '
                    f'не остановлен, немедленно остановить его вручную (О-5)')
        return (f' | ТРЕВОГА НЕ ЗАПИСАНА ({ex}) — поставлен STOP, остановить автопилот '
                f'вручную')


def carry_pending(pb):
    """Перенести признак отложенного ролла КАК ЕСТЬ (двадцатый круг, №11): 'А'/'Б'/'АБ'
    остаются строками. Отдельной функцией — под парную мутацию."""
    return (getattr(pb, 'roll_pending', False) or False) if pb else False


def open_session_in_journal(jp, day, sess_no, from_route, to_route, was_used):
    """ИТОГ СЕССИИ ПЕРЕХОДА — ОДНОЙ ФУНКЦИЕЙ (СОРОК ЧЕТВЁРТЫЙ КРУГ, №7), чтобы у правила
    была ОДНА точка мутации.

    Правило: сессия, объявленная в книге, обязана иметь СВОЙ итог в журнале §7. Переход
    объявляет новую сессию (сегодняшняя дата, номер +1), значит и итог обязан быть — и
    когда журнал целевого маршрута пуст, и когда маршрут уже работал раньше. Прежде строка
    писалась только в ПУСТОЙ журнал, и возврат Е->Ф оставлял книгу с сегодняшней датой при
    итоге старой эпохи: первое же замыкание отвергалось якорем WORM.
    """
    import journal as _J2
    _J2.append(jp, dict(
        date=day, leg='', instrument='ИТОГ', qty=0, px_order='-', px_fill='',
        commission='', reason='', nav='', leverage='',
        roll_spread_near='', roll_spread_far='',
        note=f'итог сессии {sess_no}: строк 0 '
             f'({"сессия открыта переходом" if was_used else "журнал начат переходом"} '
             f'{from_route}->{to_route})'))


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
        # ПРИОРИТЕТ ОТДАЁТСЯ СВЕЖЕЙ ДОХОДНОСТИ (двадцать девятый круг, №5). Прежде ЛЮБОЙ
        # ненулевой d_fix из старой книги Ф побеждал: но пока маршрут Е был активен, BookE
        # дюрацию не хранит вовсе, и книга Ф лежит нетронутой — её d_fix относится к
        # доходности НА МОМЕНТ УХОДА в Е, то есть может быть многомесячной давности. При
        # возврате в Ф это дало бы неверный вклад ноги Б и ложное плечо закрытия. Живая
        # доходность точнее по определению; старая книга — запасной путь.
        import feed as _FD2b
        # СНАЧАЛА — ЗНАЧЕНИЕ, ПРОВЕРЕННОЕ ПРЕДПОЛЁТОМ (тридцать четвёртый круг, №4).
        _pf = _PREFLIGHT_DFIX.get('value') or 0.0
        if _pf and _PREFLIGHT_DFIX.get('asof') == str(_FD2b.exchange_today()):
            _dfx = float(_pf)
        if not _dfx and getattr(broker, 'ib', None) is not None:
            try:
                _y, _ = _FD2b.yield_pct(broker.ib, _FD2b.exchange_today())
                _dfx = _FD2b.dref_from_yield(_y / 100.0)
            except Exception:
                _dfx = 0.0
        if not _dfx:
            # ОТКАТ К СТАРОЙ КНИГЕ — ТОЛЬКО КОГДА НОГИ Б НЕТ (тридцать третий круг, №3).
            # При живом ZN дюрация старой книги Ф относится к моменту ухода в Е, и молчаливый
            # откат к ней означал бы месяцы оценки ноги Б по чужому D. Предполёт уже
            # потребовал свежую доходность (или явную калитку стендов) — здесь тот же порядок.
            import os as _osd4
            _zn_now = any(str(k).startswith('ZN') and float(v)
                          for k, v in ((positions if positions is not None
                                        else broker.net_positions()) or {}).items())
            if not _zn_now or _osd4.environ.get('ADDFUT_DFIX_TEST') == '1':
                try:
                    _oldF, _, _ = _ST2.load(_ST2.book_path('F'), _BF)
                    if _oldF is not None and getattr(_oldF, 'd_fix', 0.0):
                        _dfx = float(_oldF.d_fix)
                except Exception:
                    pass
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
        # ТРЕБОВАНИЕ СЕРИИ — СВОЙСТВО АКТИВНОГО РЕЕСТРА (двадцать шестой круг, №6; итог
        # 16.08). Реестр бывает двух видов: ПОСТАВЛЕННЫЙ шаблон с голыми корнями (его
        # SHA-256 пинует исполнитель, подмена запрещена) и ЖИВОЙ, который пишет
        # first_connect и который несёт поставочные серии. Требовать серию безусловно —
        # значит ломать работу с шаблоном; не требовать вовсе — значит пустить книгу без
        # ser_a, которая не роллируется. Правило: серия обязательна ровно тогда, когда её
        # несёт активный реестр.
        try:
            import feed as _FDs
            _reg_keys = list(_FDs.registry().keys())
        except Exception:
            _reg_keys = []
        _reg_has_series = any(str(k) not in ('ES', 'MES', 'ZN') and
                              str(k).startswith(('ES', 'MES', 'ZN'))
                              for k in _reg_keys)
        def _ser_of(k):                    # ТА ЖЕ логика, что у state.book_from_broker
            k = str(k)
            if k.startswith('MES'): return k[3:]
            if k.startswith('ES'):  return k[2:]
            if k.startswith('ZN'):  return k[2:]
            return None
        _bad_ser = sorted({k for k, v in (_pos_src or {}).items()
                           if float(v or 0) and _ser_of(k) == ''})
        if _bad_ser and _reg_has_series:
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
    # ВСЕ ПРОВЕРКИ ЦЕЛЕВОГО МАРШРУТА — ДО ПУБЛИКАЦИИ КНИГИ И route.txt (двадцать
    # четвёртый круг, №14). Прежде они стояли ПОСЛЕ ST.save и os.replace: отказ уже
    # уничтожил прежнюю целевую книгу и переключил маршрут, а старое намерение
    # осталось и при следующем запуске затирало новую книгу. Заявление «всё до
    # движения денег» было обратным коду.
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
    # ПОТЕРЯННЫЙ ЖУРНАЛ РАНЕЕ РАБОТАВШЕГО МАРШРУТА — НЕ НОВЫЙ GENESIS (двадцать пятый круг,
    # №14). Пустой журнал считался признаком «маршрут свежий», но если book-{to}.json уже
    # СУЩЕСТВУЕТ и несёт сессии, то журнал был и исчез: старая книга перезаписывалась,
    # дописывалась новая строка ИТОГ, связь с прежними исполнениями пропадала без отказа,
    # и WORM мог заверить новую цепочку как нормальную. Проверка стоит ВНЕ try: её отказ —
    # не «журнал не начат», а утрата истории, и подменять его другим текстом нельзя.
    if not _rows2:
        _old_bk, _old_sess, _ = _ST2.load(_ST2.book_path(to_route), _cls)
        if _old_bk is not None and int(_old_sess or 0) > 0:
            raise RuntimeError(
                f'журнал маршрута {to_route} пуст, но книга маршрута существует и несёт '
                f'сессию №{_old_sess}: журнал утрачен или подменён — передача создала бы '
                f'новую цепочку поверх прежней истории (О-5)')

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
    # ИТОГ ПИШЕТСЯ ВСЕГДА, А НЕ ТОЛЬКО В ПУСТОЙ ЖУРНАЛ (СОРОК ЧЕТВЁРТЫЙ КРУГ, №7). Здесь
    # стояло `if not _rows2`, то есть строка появлялась лишь у НОВОГО маршрута. При
    # возвращении в ранее работавший маршрут (Е->Ф — штатный аварийный выход и плановый
    # возврат) journal-F.csv почти наверняка непуст: книга получала сегодняшнюю дату и
    # новый номер сессии, а последней строкой журнала оставался итог СТАРОЙ эпохи Ф. После
    # первого же --close якорь WORM отказывает по несовпадению даты итога с книгой —
    # ALARM-backup, closed-* не ставится, следующий ролл заперт. Ровно тот отказ, который
    # 19.08 остановил контур на сутки, только заведённый переходом.
    # ПРАВИЛО: сессия, объявленная в книге, обязана иметь СВОЙ итог в журнале §7 — то же
    # правило, которым 42-й круг ввёл нулевой ИТОГ, а 44-й (№5) закрыл отложенный ролл.
    # Выпускной round-trip этого не видел: он возвращается в Ф, но не замыкает день.
    try:
        _jp2 = _ST2.lock_dir() / f'journal-{to_route}.csv'
        open_session_in_journal(_jp2, _today, int(_prev_sess or 0) + 1,
                                from_route, to_route, bool(_rows2))
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


# КЭШ СТРОК §7 ЗА СЕГОДНЯ, ключ — поколение файлов журналов (рецензия 19.08, эффективность).
_J7_TODAY = {'key': None, 'n': 0}


def _code_errors():
    """Единая точка классов «ошибка кода» (state.CODE_ERRORS) для этого модуля."""
    import os as _oc, sys as _sc
    _lvc = _oc.path.join(_oc.path.dirname(_oc.path.abspath(__file__)), 'live')
    if _lvc not in _sc.path:
        _sc.path.insert(0, _lvc)
    import state as _STc
    return _STc.CODE_ERRORS


class _CodeErrProxy:
    """Кортеж классов, читаемый В МОМЕНТ ЛОВЛИ, а не при импорте (рецензия 19.08): иначе
    подмена единственной точки в state.py не меняла бы поведения, и правило «ошибка кода
    падает громко» осталось бы словом без парной мутации."""

    @property
    def CODE_ERRORS(self):
        return _code_errors()


_STce_tr = _CodeErrProxy()


def _orders_used_today(st):
    """СКОЛЬКО ЗАЯВОК СЧЁТА СЕГОДНЯ УЖЕ ИЗРАСХОДОВАНО (СОРОК ЧЕТВЁРТЫЙ КРУГ, №11).

    Лимит 390 — статус Priority Customer, и он относится к СЧЁТУ ЗА ДЕНЬ, а не к одному
    исполнителю. Считалось же `len(st['order_ids'])` — заявки ТОЛЬКО текущего файла
    прогресса. Утренний ребаланс ежедневного контура, ролл, заявки предыдущего перехода того
    же дня в счёт не шли: при 389 израсходованных счётом продажа источника проходила как
    локальная №390, а парная покупка была для счёта №391 — брокер отвергал её ПОСЛЕ продажи,
    то есть ровно та непарная позиция, ради которой ворота и заведены.

    Считаем то, что ДОКАЗУЕМО наше: заявки этого исполнения плюс строки §7 за сегодняшнюю
    биржевую дату в журналах ОБОИХ маршрутов (одна строка — одна заявка ежедневного контура;
    итоговые строки не заявки и не считаются).

    ПРЕДЕЛ НАЗВАН ЯВНО: ручные заявки из кабинета, чужой clientId и другой файл прогресса
    того же дня остаются невидимыми — у IBKR нет запроса «сколько заявок счёт подал сегодня»,
    а отчёты об исполнении не покрывают отменённые и отвергнутые, которые в лимит входят.
    Поэтому величина — НИЖНЯЯ оценка расхода, и она честнее прежней ровно на дневной контур.
    """
    n = len(st.get('order_ids') or [])
    import os as _oq, sys as _sq
    _lvq = _oq.path.join(_oq.path.dirname(_oq.path.abspath(__file__)), 'live')
    if _lvq not in _sq.path:
        _sq.path.insert(0, _lvq)
    import state as _STq, journal as _Jq, feed as _FDq
    _today_q = _FDq.exchange_today().strftime('%Y-%m-%d')
    # ЖУРНАЛЫ ЧИТАЮТСЯ ОДИН РАЗ НА ПОКОЛЕНИЕ ФАЙЛОВ (найдено рецензией 19.08, угол
    # «эффективность»). Ворота стоят перед КАЖДОЙ заявкой — продажей, покупкой, обеими
    # компенсациями и восстановительными заявками resume: до ~780 вызовов на переход, а
    # значит и до ~780 полных разборов обоих журналов §7. Хуже задержки то, ГДЕ она
    # ложится: чтение перед покупкой удлиняет окно непарной дельты, когда источник уже
    # продан. Ключ кэша — не время и не флаг, а ПОКОЛЕНИЕ файлов (размер и mtime_ns обоих
    # журналов): любая дописанная строка ключ меняет, поэтому кэш не может показать
    # устаревшее число — в отличие от «посчитать один раз на входе», где дневной контур,
    # дописавший журнал между попытками resume, остался бы невидимым.
    _sig, _paths = [], []
    for _rt_q in ('F', 'E'):
        _jpq = _STq.lock_dir() / f'journal-{_rt_q}.csv'
        _paths.append(_jpq)
        try:
            _stq = _jpq.stat()
            _sig.append((str(_jpq), _stq.st_size, _stq.st_mtime_ns))
        except OSError:
            _sig.append((str(_jpq), None, None))
    _key = (_today_q, tuple(_sig))
    if _J7_TODAY.get('key') != _key:
        _cnt = 0
        for _jpq in _paths:
            if not _jpq.exists():
                continue
            for _r_q in _Jq.read(_jpq):
                if (str(_r_q.get('date')) == _today_q
                        and str(_r_q.get('instrument')) not in ('ИТОГ', '', 'None')):
                    _cnt += 1
        _J7_TODAY.update(key=_key, n=_cnt)
    return n + _J7_TODAY['n']


def _order_gate(st, broker, fail, where='', window_till=None, need=1):
    """RUNTIME-ЛИМИТ ПЕРЕД КАЖДОЙ ЗАЯВКОЙ (девятнадцатый круг, №8): прежняя проверка
    стояла только перед основной продажей — при 389 занятых заявках продажа №390 проходила,
    а покупка №391 подавалась БЕЗ проверки; компенсации и восстановительные заявки resume
    обходили ворота вовсе. Брокер отверг бы покупку после исполненной продажи источника —
    непарная позиция и MIXED ровно на границе, ради которой лимит и введён."""
    # КВОТА ПРОВЕРЯЕТСЯ НА ВСЮ ПАРУ (двадцать шестой круг, №2). Прежде считалась одна
    # заявка: при 389 учтённых продажа №390 проходила, а парная покупка №391 отвергалась —
    # источник продан, цель не куплена, и непарная дельта жила до следующей сессии.
    # need=2 перед продажей означает «нужно место и на покупку тоже».
    # ОСТАНОВКА ГАРАНТИРУЕТСЯ, А НЕ ПРЕДПОЛАГАЕТСЯ (рецензия 19.08). fail() сегодня всегда
    # поднимает Incident, но ворота на это ПОЛАГАЛИСЬ: появись когда-нибудь невозбуждающий
    # fail (режим счёта, dry), проверка лимита вернула бы None молча — и заявка №391 ушла бы
    # брокеру ПОСЛЕ уже проданной ноги, то есть ровно та непарная позиция, ради которой
    # ворота и заведены. Один помощник на все выходы: сначала штатный путь отказа (он
    # снимает заявки и пишет исход), затем безусловный подъём.
    def _stop(msg):
        fail(msg)
        raise Incident(msg)

    # РАСХОД СЧИТАЕТСЯ ПО СЧЁТУ ЗА ДЕНЬ, А НЕ ПО ЭТОМУ ФАЙЛУ ПРОГРЕССА (44-й круг, №11).
    if not getattr(broker, 'counting', False):
        try:
            _used = _orders_used_today(st)
        except _STce_tr.CODE_ERRORS:
            # ОШИБКА КОДА ПАДАЕТ ГРОМКО (рецензия 19.08): в этом же круге я добавил внутрь
            # вызова работу со stat() и словарём кэша, то есть новые источники TypeError и
            # AttributeError, — и тут же накрыл их широким except. Ошибка кода, поданная
            # оператору как «расход непроверяем», запускает штатный путь отказа: снятие
            # заявок, запрос исполнений, запись MIXED/ABORT в нормативный журнал.
            raise
        except Exception as _exq:
            # НЕИЗМЕРИМЫЙ РАСХОД — ОТКАЗ, А НЕ НОЛЬ: молчаливое «считаем только своё» и
            # было дефектом. Журнал §7 к этому месту уже проверен целиком (verify), поэтому
            # его нечитаемость здесь — инцидент, а не штатное состояние.
            # ПРИЧИНА ЦЕПЛЯЕТСЯ (`from _exq`, рецензия 19.08): без неё тревога теряет
            # исходный PermissionError/ValueError, о котором fail() уже знал.
            try:
                _stop(f'дневной расход заявок непроверяем ({type(_exq).__name__}: {_exq}) '
                      f'перед заявкой {where} — переход останавливается (О-5)')
            except Incident as _inc:
                raise _inc from _exq
        if _used + max(0, need - 1) >= ORDERS_PER_DAY:
            _stop(f'дневной лимит {ORDERS_PER_DAY} заявок исчерпан по счёту '
                  f'({_used}; из них {len(st["order_ids"])} в этом исполнении) перед заявкой '
                  f'{where} — переход останавливается')
    # КРАЙ ОБЩЕГО ОКНА — ПЕРЕД КАЖДОЙ ЗАЯВКОЙ (двадцатый круг, №7). Прежде окно было
    # булевым аргументом in_common_window, проверенным ОДИН раз до preview, preflight и
    # сотен заявок; тайм-аут 15 минут относился к паре, а не к закрытию площадки.
    if window_till is not None and not getattr(broker, 'counting', False):
        import pandas as _pd
        _now = _pd.Timestamp.now(tz=window_till.tz)
        # ЗАПАС ВРЕМЕНИ НА ВСЮ ПАРУ (двадцать шестой круг, №3): продажа за секунду до края
        # проходила, а покупка после ожидания её терминального статуса отвергалась как
        # поздняя. Перед продажей требуем места на ОБЕ заявки: TIMEOUT_MIN на каждую.
        _need_min = TIMEOUT_MIN * max(1, need)
        # ПОРЯДОК: сперва ЖЁСТКОЕ закрытие окна, потом запас на пару. Иначе уже закрытое
        # окно объяснялось бы «не хватит времени», что неверно по существу и сбивает
        # оператора: окна нет вовсе, а не мало.
        if _now >= window_till:
            fail(f'общее окно LSE/CME закрыто ({_now:%H:%M:%S} >= {window_till:%H:%M}) '
                 f'перед заявкой {where} — переход останавливается, непарная позиция '
                 f'разбирается вручную')
        if need > 1 and _now >= window_till - _pd.Timedelta(minutes=_need_min):
            fail(f'до края общего окна ({window_till:%H:%M}) осталось меньше {_need_min} мин '
                 f'— пары не хватит времени: продажа прошла бы, а парная покупка опоздала')


def _run_lots(broker, plan, st, state_path, lim, unp, dst_bought, fail, _M=None,
              journal=None, window_till=None):
    _wt = window_till
    for lot in plan:
        if _M is not None and journal is not None:
            _M.canonical_journal(journal)         # перед каждым лотом: эпоха и личность журнала
        key = f"{lot['src']}:{lot['step']}"
        if key in st['done']: continue
        # ЧАСЫ ПАРЫ ПУСКАЮТСЯ ДО ПЕРВОЙ ЗАЯВКИ (тридцать седьмой круг, №6). Прежде первое
        # обращение к minutes_since стояло НИЖЕ продажи, покупки, компенсации и gross(), а
        # адаптер заводил отсчёт именно первым обращением — и возвращал почти ноль. Лимит
        # 15 минут не мог сработать никогда: одноитерационная пара висела произвольно долго
        # с одной проданной ногой, а в многоитерационной терялась длительность первой
        # итерации. Пуск обязан быть отдельным видимым вызовом до денег.
        _mark = getattr(broker, 'mark_pair', None)
        if _mark is not None:
            _mark(key)
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
            # ПЕРЕД ПРОДАЖЕЙ — МЕСТО НА ПАРУ (№2, №3): продажа необратима, и разрешать её
            # без гарантии, что парная покупка успеет по квоте и по времени, нельзя.
            # РЕЗЕРВ НА ВСЮ ТРОЙКУ (двадцать восьмой круг, №11): продажа, парная покупка И
            # компенсация недобора. Прежде резервировались только две заявки, и компенсация
            # упиралась в лимит или в край окна — то есть непарный остаток оставался жить
            # до следующей сессии ровно в том случае, ради которого компенсация и заведена.
            _order_gate(st, broker, fail, window_till=_wt, where=f'продажа {lot["src"]}',
                        need=3)
            # ПОПЫТКА ФИКСИРУЕТСЯ ДО ВЫЗОВА (тридцать пятый круг, №1). order_ids
            # заполняется только ПОСЛЕ возврата: если брокер исполнил заявку и связь
            # оборвалась до ответа, в состоянии не остаётся ни номера, ни executed_usd —
            # и чистый ABORT выглядел доказанным. Отметка о попытке долговечна, поэтому
            # исход честно становится «недоказуемым», а не «чистым».
            st['attempted'] = int(st.get('attempted', 0)) + 1
            _atomic(state_path, st)
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
            st['attempted'] = int(st.get('attempted', 0)) + 1     # №1
            _atomic(state_path, st)
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
                # ДРОБНАЯ КОМПЕНСАЦИЯ — ЧЕРЕЗ fail() (двадцать четвёртый круг, №12): здесь
                # _int_fill не был обёрнут, и Incident уходил мимо отмены заявок, _mixed()
                # и файла тревоги — а компенсация УЖЕ изменила книгу брокера.
                try:
                    _fc = _int_fill(f3, lot['dst'])
                except Incident as ex:
                    st['executed_usd'] += abs(f3)*lot['dprice']
                    fail(f'{lot["dst"]}: дробная компенсация-покупка {f3} ({ex})')
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
                st['order_ids'].append(oid3)
                try:                                            # №12: и здесь через fail()
                    _fs = _int_fill(f3, lot['dst'])
                except Incident as ex:
                    st['executed_usd'] += abs(f3)*lot['dprice']
                    fail(f'{lot["dst"]}: дробная компенсация-продажа {f3} ({ex})')
                unp[lot['leg']] += _fs*lot['dprice']
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
            g = broker.gross(_gross_dfix() or None)
            if g > INTRA_CAP + 1e-9:
                fail(f'внутрисессионный gross {g:.4f} > {INTRA_CAP}')
            remaining -= sold if sold > 0 else 0
        st['done'].append(key); _atomic(state_path, st)
