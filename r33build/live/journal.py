#!/usr/bin/env python3
"""Журнал исполнения §7: единственное основание для будущего пересмотра издержек.

Издержки 5 б.п. и ролл 1 б.п. заданы АПРИОРИ и ни разу не сверены с фактом. §7 требует
фиксировать на каждой операционной сессии цену заявки, цену исполнения и комиссию, и раз
в двадцать наблюдений сопоставлять факт с моделью.

ЦЕЛОСТНОСТЬ — НЕ ДЕКЛАРАЦИЯ, А МЕХАНИЗМ. Прежняя редакция утверждала в описании, что
переписывание задним числом невозможно, а делала обычное дописывание. Теперь каждая
строка несёт хэш предыдущей и свой собственный: правка любой старой строки рвёт цепочку и
обнаруживается verify(). Запись идёт под файловой блокировкой, поэтому два писателя не
перемешают строки. Это не защищает от злонамеренного пересчёта всей цепочки, но делает
случайную или незаметную правку невозможной — а именно её и надо исключить, раз журнал
объявлен основанием для пересмотра финансовых параметров.

СОПОСТАВЛЕНИЕ ВЗВЕШЕНО ПО ДЕНЬГАМ. Усреднять базисные пункты по строкам нельзя: одна
заявка на один MES получила бы тот же вес, что сотня ZN. Считается долларовая недостача
против цены заявки, делённая на совокупный торгуемый НОМИНАЛ. Ролл сверяется ОТДЕЛЬНО:
у него своя модельная ставка, и без отдельного счёта она осталась бы непроверенной навсегда.

НАБЛЮДЕНИЕ — ЭТО СЕССИЯ, А НЕ СТРОКА. Двадцать строк можно набрать мелкими заявками
одного дня; порог §7 считается по различным датам.
"""
import csv, fcntl, hashlib, io, os
from pathlib import Path

BASE = ['date', 'leg', 'instrument', 'qty', 'px_order', 'px_fill', 'commission',
        'reason', 'nav', 'leverage', 'roll_spread_near', 'roll_spread_far', 'note']
CHAIN = ['prev_hash', 'row_hash']
COLS = BASE + CHAIN

# Множитель контракта: издержки сопоставляют с НОМИНАЛОМ, а не с котировкой. Для ZN
# котировка 112 при номинале 112 000 $ — разница в тысячу раз.
MULT = {'ES': 50.0, 'MES': 5.0, 'ZN': 1000.0, 'CSPX': 1.0, 'CBU0': 1.0}
GENESIS = '0' * 64

MODEL_COST_BP = 5.0        # §2: издержки на изменение экспозиции
MODEL_ROLL_BP = 1.0        # §2: ролл
MIN_OBS = 20               # §7: пересмотр не ранее двадцати наблюдений (различных сессий)


def root_of(instrument):
    """Серия -> корень: ZNZ26 -> ZN. Множитель задаётся корнем, а не серией."""
    for r in sorted(MULT, key=len, reverse=True):
        if instrument.startswith(r):
            return r
    raise ValueError(f'неизвестный инструмент: {instrument}')


def _digest(prev, row):
    payload = '\x1f'.join(str(row.get(c, '')) for c in BASE)
    return hashlib.sha256((prev + '\x1e' + payload).encode('utf-8')).hexdigest()


def _last_hash(path):
    if not path.exists():
        return GENESIS
    rows = list(csv.DictReader(open(path, newline='', encoding='utf-8')))
    return rows[-1]['row_hash'] if rows else GENESIS


def append(path, row):
    """Дописать строку под блокировкой, продолжив цепочку хэшей."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + '.lock')
    with open(lock, 'w') as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)          # два писателя не перемешают строки
        try:
            prev = _last_hash(path)
            # НОРМАЛИЗАЦИЯ ДО ХЭША. None попадал в хэш строкой 'None', а в файл — пустым
            # полем: цепочка рвалась при первой же проверке, и журнал первой живой сессии
            # выглядел правленым задним числом, не быв правленым. Хэшируется РОВНО то, что
            # будет прочитано.
            rec = {c: ('' if row.get(c) is None else str(row.get(c))) for c in BASE}
            rec['prev_hash'] = prev
            rec['row_hash'] = _digest(prev, rec)
            new = not path.exists()
            with open(path, 'a', newline='', encoding='utf-8') as f:
                w = csv.DictWriter(f, fieldnames=COLS, extrasaction='raise')
                if new:
                    w.writeheader()
                w.writerow(rec)
                f.flush(); os.fsync(f.fileno())
            return rec['row_hash']
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def read(path):
    path = Path(path)
    if not path.exists():
        return []
    out = []
    with open(path, newline='', encoding='utf-8') as f:
        for i, r in enumerate(csv.DictReader(f), 2):
            if set(r) != set(COLS) or None in r.values():
                raise ValueError(f'{path}: повреждённая строка {i}')
            out.append(r)
    return out


ITOG_RULE_FROM = '2026-08-18'


def session_incomplete(rows, last_session):
    """ЖУРНАЛ ЗАКРЫТ ИТОГОМ ИМЕННО ЭТОЙ СЕССИИ — ОДНО ПРАВИЛО НА ДВА МЕСТА (44-й круг, №6).

    Порядок записи в run_session: ST.save -> clear_intent -> J.append(ИТОГ). Обрыв между
    сохранением книги и итогом оставляет книгу СЕГОДНЯШНЕЙ датой при журнале, закрытом
    итогом ЧУЖОЙ сессии, — день выглядит отторгованным, а §7 неполон. Спрашивают об этом
    двое и по разным поводам: якорь WORM перед тем, как день объявят завершённым (43-й круг,
    №7), и торговый вход, когда повтор в тот же день надо отличить от штатного (44-й круг,
    №6). Правило поэтому живёт ЗДЕСЬ, у журнала, а не двумя копиями с двумя датами: копии
    разъезжаются молча, и разъехались бы ровно в легаси-оговорке ниже.

    ДВЕ ЧАСТИ. Первая — последняя строка обязана быть ИТОГом — действует всегда. Вторая —
    итог обязан относиться К ЭТОЙ сессии — действует С ДАТЫ ВВЕДЕНИЯ нулевого ИТОГа
    (42-й круг): до 18.08.2026 сессия без заявок итога не писала вовсе, поэтому живой журнал
    закрыт итогом за 13.08 при книге за 17.08 — законный разрыв, а не обрыв. Легаси-дни
    восстановить нечем, и делать вид, что можно, хуже, чем назвать дату.

    Возвращает '' (разрыва нет) либо краткое описание последней записи — им же и объясняются
    оба отказа."""
    if not rows:
        return 'журнал пуст'
    last = rows[-1]
    note = str(last.get('note', ''))
    if not note.startswith('итог сессии'):
        return f"последняя запись {last.get('date')}/{last.get('instrument')}: {note[:40]!r}"
    if (last_session and str(last_session) >= ITOG_RULE_FROM
            and str(last.get('date', '')) != str(last_session)):
        return f"итог относится к {last.get('date')!r}, а книга несёт {last_session!r}"
    return ''


def verify_rows(rows, path='<память>'):
    """Проверить УЖЕ ПРОЧИТАННЫЕ строки (двадцать пятый круг, №17): вызывающий, которому
    нужны и число, и сами строки, обязан работать с одним снимком, а не читать дважды."""
    prev = GENESIS
    for i, r in enumerate(rows, 2):
        if r['prev_hash'] != prev:
            raise ValueError(f'{path}: строка {i} не продолжает цепочку '
                             f'(ожидался {prev[:12]}, записан {r["prev_hash"][:12]})')
        if _digest(prev, r) != r['row_hash']:
            raise ValueError(f'{path}: строка {i} изменена после записи '
                             f'(хэш содержимого не совпадает)')
        prev = r['row_hash']
    return len(rows)


def verify(path):
    """Пройти цепочку. Возвращает число строк либо поднимает исключение с номером сбоя.

    ОДИН СНИМОК НА ПРОВЕРКУ И НА ОТВЕТ (двадцать четвёртый круг, №27). Прежде цепочка
    проверялась по read(path), а длина возвращалась ОТДЕЛЬНЫМ read(path): строка с неверным
    хэшем, дописанная между двумя чтениями, не проверялась, но попадала в число «проверенных
    строк» — и следующий read мог принять её как итог сессии. WORM при этом заверял неверное
    число якобы проверенных строк. Читаем ОДИН раз и по нему же отвечаем.
    """
    rows = read(path)
    prev = GENESIS
    for i, r in enumerate(rows, 2):
        if r['prev_hash'] != prev:
            raise ValueError(f'{path}: строка {i} не продолжает цепочку '
                             f'(ожидался {prev[:12]}, записан {r["prev_hash"][:12]})')
        if _digest(prev, r) != r['row_hash']:
            raise ValueError(f'{path}: строка {i} изменена после записи '
                             f'(хэш содержимого не совпадает)')
        prev = r['row_hash']
    return len(rows)


def rows_from_decision(dec, nav, orders, fills=None, delayed_out=None):
    """delayed_out — список, куда складываются инструменты с ЗАДЕРЖАННЫМ ориентиром:
    вызывающий обязан пометить итоговую строку, иначе §7 посчитает день пригодным для
    измерения издержек (двадцать пятый круг, №15)."""
    _delayed_seen = delayed_out if delayed_out is not None else []
    """Строки журнала из решения сессии. fills — фактические цены и комиссии от брокера;
    пока их нет (наблюдение без позиции), поля остаются пустыми и запись всё равно ведётся:
    отсутствие сделки тоже факт."""
    fills = fills or {}
    # РОЛЛОВЫМ СЧИТАЕТСЯ ТОЛЬКО ОБОРОТ ПЕРЕНОСА, А НЕ ВСЯ СЕССИЯ. Прежде ролл помечал все
    # заявки дня: когда сигнал или полоса меняют количество в тот же день, часть оборота —
    # обычная сделка по 5 б.п., а попадала она в блок 1 б.п., и сверка §7 сравнивала
    # измеренные издержки не с той моделью.
    #
    # РАЗДЕЛЕНИЕ ПО СЕРИЯМ И КОЛИЧЕСТВУ. Закрытие УХОДЯЩЕЙ серии — целиком перенос.
    # В приходящей серии переносом считается столько, сколько закрыто; избыток сверх этого —
    # обычное изменение экспозиции, и он выносится ОТДЕЛЬНОЙ строкой. Внутри ноги А, где
    # сетка распадается на ES и MES, перенос разбирается по инструментам по порядку.
    rolled = {}          # (нога, серия) -> сколько единиц переносится
    old_ser, new_ser = {}, {}
    for pr in (dec.roll_pairs or ()):
        leg = pr['leg']
        held, n_old = pr['close']
        want, n_new = pr['open']
        old_ser[leg] = held
        new_ser[leg] = want
        rolled[leg] = min(abs(n_old), abs(n_new))

    def leg_of(instrument):
        return 'А' if root_of(instrument) in ('ES', 'MES', 'CSPX') else 'Б'

    def series_of(instrument):
        return instrument[len(root_of(instrument)):]

    def grid(instrument, qty):
        """Единицы сетки: 1 ES = 10 MES, поэтому переносимое количество сопоставимо."""
        return abs(qty) * (10 if root_of(instrument) == 'ES' else 1)

    left_new = dict(rolled)
    left_old = dict(rolled)
    out = []

    def comm_split(f, q_part, q_full):
        """Комиссия ПРОПОРЦИОНАЛЬНО объёму части (№27): относить её целиком к ролловой
        части значит систематически завышать ролловую ставку в сверке §7."""
        c = f.get('commission', '')
        if c in ('', None) or not q_full:
            return ''
        return f'{float(c) * abs(q_part) / abs(q_full):.4f}'

    for instrument, qty in orders:
        leg = leg_of(instrument)
        ser = series_of(instrument)
        f = fills.get(instrument, {})
        # ОРИЕНТИР НЕ КОТИРОВКА МОМЕНТА — СТРОКА НЕ ГОДИТСЯ ДЛЯ ИЗДЕРЖЕК (№23). Снимок мог
        # не удаться либо отдать только close (то есть ВЧЕРАШНЕЕ закрытие). Молча считать
        # такую разницу проскальзыванием значит записывать ночной гэп в торговые издержки —
        # ровно то ложное доказательство, из-за которого 5 б.п. «сходились».
        # ОРИЕНТИР СОХРАНЯЕТСЯ ВСЕГДА (двадцать пятый круг, №15 — уточнение к №23 24-го).
        # Обнулять его неверно: цена нужна и для разбора, и для сверки базиса. Помечается
        # не строка, а ПРИГОДНОСТЬ ДАТЫ для статистики издержек §7 — через итоговую строку,
        # тем же механизмом, что и прочие исключения. При задержанных данных (тип 3, ~15
        # минут) разница fill−ориентир измеряет движение рынка, а не качество исполнения.
        _po_live = f.get('px_order_live', True)
        if not _po_live:
            _delayed_seen.append(instrument)
        base = dict(date=dec.date.strftime('%Y-%m-%d'), leg=leg,
                    px_order=f.get('px_order', ''),
                    px_fill=f.get('px_fill', ''),
                    reason='; '.join(dec.reasons), nav=f'{nav:.2f}',
                    leverage=f'{dec.leverage:.4f}',
                    roll_spread_near=f.get('roll_near', ''),
                    roll_spread_far=f.get('roll_far', ''))
        refus = '; '.join(dec.refusals)
        per = 10 if root_of(instrument) == 'ES' else 1

        def emit(part, note_roll, pool):
            """Часть заявки в пределах переносимого объёма — ролл, избыток — сделка.

            ГРАНИЦА ES/MES: ролловая доля МЕНЬШЕ ОДНОГО ES не округляется в ноль (тридцать
            третий круг, №11). Прежде стояло `int(take // 10)`: при старой упаковке 1 ES и
            цели 5 MES вся продажа ES уходила в обычные сделки, покупка 5 MES — в ролловые,
            а _roll_block подставлял вместо недостающей закрывающей стороны половину
            номинала открывающей, то есть удваивал результат MES-стороны. При разном
            проскальзывании ES и MES обе ставки — ролловая и обычная — становились ложными,
            и после двадцати роллов это стало бы основанием менять параметры §2.

            Контракт неделим, поэтому доля относится ПРОПОРЦИОНАЛЬНО: если переносимая
            часть покрывает хотя бы часть лота, весь лот идёт в ролловую строку, а комиссия
            и объём делятся по фактической доле сетки. Пул уменьшается ровно на то, что
            действительно перенесено.
            """
            g = grid(instrument, part)
            take = min(g, pool.get(leg, 0))
            if take <= 0:
                out.append(dict(base, instrument=instrument, qty=part, note=refus,
                                commission=comm_split(f, part, part)))
                return
            sign = 1 if part > 0 else -1
            # ДРОБНЫЙ ОСТАТОК ПОСЛЕ ЦЕЛЫХ ЛОТОВ (тридцать пятый круг, №9). В 34-м круге я
            # сделал пропорцию ТОЛЬКО для случая «вся ролловая часть меньше лота», а остаток
            # сверх целых лотов по-прежнему терялся: при упаковке 2 ES и пуле 15 единиц
            # int(15 // 10) = 1, и пять единиц закрывающей стороны выпадали из роллового
            # класса — односторонний номинал занижался, ставка §7 завышалась. Ролловая доля
            # считается ОДНОЙ формулой: take / per, без округления вниз.
            q_roll = (take / per) * sign
            if not q_roll and take > 0:
                # ДОЛЯ МЕНЬШЕ ЛОТА РАСПРЕДЕЛЯЕТСЯ ПРОПОРЦИОНАЛЬНО (тридцать четвёртый круг,
                # №11). В тридцать третьем я убрал округление в ноль, но отнёс к роллу ВЕСЬ
                # контракт (q_roll = sign): при переносе пяти MES-эквивалентов из одного ES
                # весь номинал ES уходил в ролловый класс, а обычное сокращение второй
                # половины исчезало из класса 5 б.п. — §7 занижал ролловую ставку
                # увеличенным односторонним номиналом. Комментарий обещал пропорцию,
                # код её не делал. Контракт неделим, поэтому дробится КОЛИЧЕСТВО в строке:
                # ролловая часть — take/per лота, остаток — обычная сделка. Обе строки
                # несут один и тот же инструмент и цену, а комиссия делится тем же
                # comm_split по объёму.
                q_roll = sign * (take / per)
            pool[leg] = max(0, pool.get(leg, 0) - min(take, abs(q_roll) * per))
            if q_roll:
                out.append(dict(base, instrument=instrument, qty=q_roll,
                                note='ролл' + refus,
                                commission=comm_split(f, q_roll, part)))
            rest = part - q_roll
            if abs(rest) > 1e-12:
                out.append(dict(base, instrument=instrument, qty=rest, note=refus,
                                commission=comm_split(f, rest, part)))

        is_old = leg in old_ser and ser == old_ser[leg]
        is_new = leg in new_ser and ser == new_ser[leg]
        if is_old:
            # ЗАКРЫТИЕ СТАРОЙ СЕРИИ ТОЖЕ РАЗБИВАЕТСЯ (№27): при сокращении на ролле парная
            # часть — перенос (1 б.п.), избыток закрытия — обычная сделка (5 б.п.). Прежде
            # весь объём уходил в ролловую выборку.
            emit(qty, True, left_old)
            continue
        if is_new and left_new.get(leg, 0) > 0:
            emit(qty, True, left_new)
            continue
        out.append(dict(base, instrument=instrument, qty=qty, note=refus,
                        commission=f.get('commission', '')))
    return out


def _num(x, what):
    """Число §7 обязано быть КОНЕЧНЫМ (девятнадцатый круг, №16): NaN проходил арифметику,
    «nan > 2» и «nan < 0.5» ложны — и вердикт становился «в пределах двукратного», то есть
    порча данных ДОКАЗЫВАЛА достаточность параметра. Нечисловое наблюдение — стоп сверки."""
    import math
    v = float(x)
    if not math.isfinite(v):
        raise ValueError(f'§7: нечисловое наблюдение {what}={x!r} — сверка остановлена, '
                         f'журнал требует разбора')
    return v


def _cost_bp(rows):
    """Долларовая недостача против цены заявки, отнесённая к торгуемому номиналу."""
    loss = 0.0; notional = 0.0
    for r in rows:
        q = _num(r['qty'], 'qty'); po = _num(r['px_order'], 'px_order')
        pf = _num(r['px_fill'], 'px_fill')
        mult = MULT[root_of(r['instrument'])]
        if po <= 0 or q == 0:
            continue
        loss += (pf - po) * q * mult                 # покупка дороже / продажа дешевле = потеря
        if r['commission']:
            loss += abs(_num(r['commission'], 'commission'))
        notional += abs(q) * pf * mult
    return (loss / notional * 1e4 if notional else 0.0), notional


def _excluded_dates(rows):
    """Сессии, ИСКЛЮЧАЕМЫЕ из выборки §7, с причинами (девятнадцатый круг, №16;
    восемнадцатый, №15). Три основания: (1) пометка исключения в итоговой строке;
    (2) счётчик «строк N» итога не совпал с фактом — часть строк исполнения утрачена;
    (3) живая строка заявки без цены исполнения/ориентира — систематически пустая сторона
    прежде исчезала из выборки МОЛЧА, а дата продолжала считаться по другой ноге."""
    import re as _re
    skip = {}
    data = [r for r in rows if r.get('instrument') != 'ИТОГ']
    per_date = {}
    for r in data:
        per_date[r['date']] = per_date.get(r['date'], 0) + 1
    for r in rows:
        if r.get('instrument') != 'ИТОГ':
            continue
        # РЕГИСТР НЕ ВАЖЕН (двадцать пятый круг, №15): пометка «ИСКЛЮЧЕНА» заглавными
        # молча не находилась подстрокой в нижнем регистре — исключение не срабатывало.
        if 'исключ' in (r.get('note') or '').lower():
            skip.setdefault(r['date'], 'пометка исключения в итоге')
        m = _re.search(r'строк (\d+)', r.get('note') or '')
        if m and per_date.get(r['date'], 0) != int(m.group(1)):
            skip.setdefault(r['date'], f'итог обещает {m.group(1)} строк, '
                                       f'в журнале {per_date.get(r["date"], 0)}')
    for r in data:
        if r['qty'] and (not r['px_fill'] or not r['px_order']):
            skip.setdefault(r['date'], 'строка исполнения без цены — сессия неполна')
    # ОТСУТСТВИЕ СТРОКИ ИТОГ ДЕЛАЕТ ДАТУ НЕПОЛНОЙ (двадцатый круг, №20). Прежде
    # проверялся только СЧЁТЧИК внутри существующего итога: процесс, упавший после записи
    # исполнений, но до итога, оставлял валидную цепочку хэшей и дату, которую выборка §7
    # считала полноценной. Маркер полноты был обязателен лишь для следующей торговли, но
    # не для самого статистического доказательства.
    _with_total = {r['date'] for r in rows if r.get('instrument') == 'ИТОГ'}
    for d in {r['date'] for r in data}:
        if d not in _with_total:
            skip.setdefault(d, 'нет строки ИТОГ — сессия не закрыта, часть строк '
                               'исполнения могла не записаться')
    # ОТСУТСТВУЮЩАЯ КОМИССИЯ НЕ ЕСТЬ НУЛЕВАЯ (двадцатый круг, №18). _rec честно пишет
    # commission='' , пока не пришёл commissionReport, но _cost_bp просто не прибавлял её —
    # то есть считал нулём. Двадцать таких строк «доказали» бы достаточность 5 б.п. на
    # систематически заниженных расходах; позднего дополнения append-only журнала нет.
    for r in data:
        if r['qty'] and not r['commission']:
            skip.setdefault(r['date'], 'комиссия не пришла (commissionReport) — расход '
                                       'занижен, дата не годится для сверки §7')
    return skip


def _verdict(ratio):
    """Вердикт в ПОЛОЖИТЕЛЬНОЙ форме (девятнадцатый круг, №16): «в пределах» — только при
    доказанном попадании в границы; прежняя форма «если ratio>2 или <0.5» на NaN давала
    «в пределах» через ложные сравнения."""
    if 0.5 <= ratio <= 2.0:
        return 'в пределах двукратного — параметр не пересматривается'
    return 'расхождение свыше двукратного — вопрос о параметре §2 ставится заново'


def _roll_block(sub):
    """НОМИНАЛ РОЛЛА — ОДНОСТОРОННИЙ (восемнадцатый круг, №16): перенос состоит из
    закрытия И открытия, но модель ROLL_BP описывает стоимость ПЕРЕНОСА ЭКСПОЗИЦИИ;
    деление суммарной потери двух сторон на их УДВОЕННЫЙ номинал занижало измеренную
    стоимость вдвое и могло «доказать» достаточность заниженного параметра. Потеря — по
    обеим сторонам, номинал — только закрывающая сторона. На уровне модуля — под стенд и
    мутацию (девятнадцатый круг, долг пар)."""
    if not sub:
        return dict(label='ролл', n=0, verdict='наблюдений нет')
    # СЧЁТ — ПО РОЛЛОВЫМ СЕССИЯМ (двадцатый круг, №19): len(sub) считал строки, и один
    # ролл из четырёх строк выглядел четырьмя наблюдениями.
    _sess = {r['date'] for r in sub}
    bp2, _ = _cost_bp(sub)
    closes = [r for r in sub if float(r['qty']) < 0]
    _, notional_one = _cost_bp(closes)
    if not notional_one:
        _, notional_one = _cost_bp(sub)
        notional_one /= 2.0
    loss = bp2 / 1e4 * _cost_bp(sub)[1]
    bp = loss / notional_one * 1e4 if notional_one else 0.0
    if len(_sess) < MIN_OBS:
        return dict(label='ролл', n=len(_sess), n_rows=len(sub), bp=bp,
                    model_bp=MODEL_ROLL_BP, notional=notional_one,
                    verdict=f'ролловых сессий {len(_sess)} из {MIN_OBS} — '
                            f'вывод не делается')
    ratio = bp / MODEL_ROLL_BP if MODEL_ROLL_BP else float('inf')
    return dict(label='ролл', n=len(_sess), n_rows=len(sub), bp=bp, model_bp=MODEL_ROLL_BP,
                notional=notional_one, ratio=ratio, verdict=_verdict(ratio))


def reconcile(path):
    """Сопоставление факта с моделью, раздельно по штатным сделкам и роллам.
    Вывод не делается, пока наблюдений (РАЗЛИЧНЫХ СЕССИЙ) меньше двадцати."""
    # ОДИН СНИМОК НА ПРОВЕРКУ И НА СТАТИСТИКУ (двадцать седьмой круг, №18). Прежде
    # verify(path) читал файл сам, а _all = read(path) читал ЕЩЁ РАЗ: строка, появившаяся
    # между чтениями, не проверялась цепочкой, но участвовала в вердикте §7 по 5 и 1 б.п.
    # и в решении о полноте дня. Читаем один раз и по нему же считаем.
    _all = read(path)
    verify_rows(_all, path)
    # ИСКЛЮЧЁННЫЕ СЕССИИ — С ПРИЧИНАМИ (восемнадцатый круг, №15; девятнадцатый, №16):
    # пометка итога, несовпавший счётчик строк, живые строки без цены. Строки ИТОГ —
    # маркеры, в данные не попадают никогда; исключение видно в ответе, а не молчит.
    _skip = _excluded_dates(_all)
    rows = [r for r in _all
            if r['px_fill'] and r['px_order'] and r['qty']
            and r.get('instrument') != 'ИТОГ' and r['date'] not in _skip]
    # КОНЕЧНОСТЬ — ДО ЛЮБОГО ВЕРДИКТА (девятнадцатый круг, №16): nan-строка не смеет ни
    # пройти в арифметику, ни отсидеться за порогом «мало сессий» до его преодоления.
    for r in rows:
        _num(r['qty'], 'qty'); _num(r['px_order'], 'px_order'); _num(r['px_fill'], 'px_fill')
        if r['commission']:
            _num(r['commission'], 'commission')
    sessions = {r['date'] for r in rows}
    if len(sessions) < MIN_OBS:
        return dict(n_rows=len(rows), n_sessions=len(sessions), excluded=_skip,
                    verdict=f'сессий {len(sessions)} из {MIN_OBS} — вывод не делается')

    def block(sub, model_bp, label):
        """ПОРОГ ДВАДЦАТИ — ПО СВОЕМУ КЛАССУ (двадцатый круг, №19).

        Прежде порог проверялся по ОБЪЕДИНЕНИЮ классов и один раз: после двадцати обычных
        торговых дат единственный ролл уже получал полноценный вердикт против
        MODEL_ROLL_BP, а счётчик n считал СТРОКИ, а не различные ролловые сессии. Это
        ложное доказательство параметра, а не ожидание накопления наблюдений; §8
        отдельно признаёт, что сверка ролловых издержек ждёт двадцати РОЛЛОВ.
        """
        if not sub:
            return dict(label=label, n=0, verdict='наблюдений нет')
        _sess = {r['date'] for r in sub}
        bp, notional = _cost_bp(sub)
        if len(_sess) < MIN_OBS:
            return dict(label=label, n=len(_sess), n_rows=len(sub), bp=bp,
                        model_bp=model_bp, notional=notional,
                        verdict=f'сессий класса {len(_sess)} из {MIN_OBS} — '
                                f'вывод не делается')
        ratio = bp / model_bp if model_bp else float('inf')
        return dict(label=label, n=len(_sess), n_rows=len(sub), bp=bp, model_bp=model_bp,
                    notional=notional, ratio=ratio, verdict=_verdict(ratio))

    # КЛАСС СТОИМОСТИ — ТОЛЬКО ИЗ ПОМЕТКИ note, которую ставит разделение оборота.
    # Прежде слово искалось и в reason: в день ролла там всегда есть «ролл серии», и строка
    # ОБЫЧНОГО остатка (5 б.п.) попадала в выборку ролла (1 б.п.) — сверка §7 была
    # систематически искажена именно в сессиях «ролл + сигнал/полоса/кап».
    is_roll = lambda r: r['note'].startswith('ролл')
    out = dict(n_rows=len(rows), n_sessions=len(sessions), excluded=_skip,
               trade=block([r for r in rows if not is_roll(r)], MODEL_COST_BP, 'сделка'),
               roll=_roll_block([r for r in rows if is_roll(r)]))
    by = {}
    for r in rows:
        by.setdefault(root_of(r['instrument']), []).append(r)
    out['by_instrument'] = {k: _cost_bp(v)[0] for k, v in by.items()}
    return out
