#!/usr/bin/env python3
"""Проверка ИНВАРИАНТОВ перебором состояний, а не примерами.

ПОЧЕМУ ТАК. Три круга враждебной рецензии одного и того же контура дали 20, 16 и 17
замечаний — сходимости нет, и большинство дефектов каждого круга вносилось при исправлении
предыдущих. Починка по одному найденному примеру не убирает класс ошибки: она убирает
пример. Единственное, что за всю работу убрало класс, — перевод заявок на разность двух
книг с исчерпывающей проверкой сочетаний.

Здесь тот же приём применён ко всему решению сессии. Перебирается пространство состояний
(книга, упаковка, сигналы, капитал, день ролла, фаза цикла), и на каждом сочетании
проверяются УТВЕРЖДЕНИЯ, а не ожидаемые значения. Утверждение, нарушенное хотя бы раз,
печатается вместе с породившим его состоянием.
"""
# ИЗОЛЯЦИЯ ПРИ САМОСТОЯТЕЛЬНОМ ЗАПУСКЕ (двадцать четвёртый круг, №29). Батарея гоняет
# полные сессии, а mutation.py повторяет их сотни раз. Без собственного ADDFUT_LOCK_DIR
# они брали БОЕВОЙ книжный замок ~/.addfut: на торговой машине это конкуренция с живой
# сессией, тайм-аут замка, тревога и пропуск ребаланса или ролла. Каталог создаётся один
# на прогон и не трогает машинное состояние (правило 5 проекта).
import os as _os0
import tempfile as _tf0
# ИЗОЛЯЦИЯ БЕЗУСЛОВНА (сорок первый круг, №7). Прежде свой каталог заводился ТОЛЬКО когда
# ADDFUT_LOCK_DIR не задан, то есть стенд «уважал» унаследованное значение. На торговой
# машине это означает следующее: запуск с боевым ADDFUT_LOCK_DIR берёт БОЕВОЙ книжный замок,
# а _session_lock в finally безусловно пишет route.txt='F' — то есть прогон проверок
# ДЕТЕРМИНИРОВАННО переводит действующий маршрут Е в Ф. Правило 5 проекта требует
# обратного: стенды не трогают машинное состояние ни при каких переменных окружения.
# Унаследованное значение теперь именно ПЕРЕКРЫВАЕТСЯ, а не принимается на веру; выпуск и
# так подставляет временный путь, но полагаться на дисциплину вызывающего здесь нельзя.
# ВРЕМЕННЫЕ КАТАЛОГИ УБИРАЮТСЯ ЗА СОБОЙ (рецензия 20.08). Стенды создают их десятками за
# прогон, мутационный — тысячами, и ни один не удалялся: на машине скопилось 226 тысяч
# каталогов /tmp/addfut-* общим весом 5 ГБ. Переполненный /tmp роняет не батарею, а ЖИВОЙ
# автопилот. Перехватываем сам mkdtemp: правка в одном месте покрывает все 33 точки вызова
# в этом файле, ib_stub и mutation, и не требует помнить об уборке в каждом новом стенде.
import atexit as _at0
import shutil as _sh0
_MADE_TMP0 = []
_ORIG_MKDTEMP0 = _tf0.mkdtemp


def _mkdtemp_tracked(*a, **k):
    _d = _ORIG_MKDTEMP0(*a, **k)
    _MADE_TMP0.append(_d)
    return _d


_tf0.mkdtemp = _mkdtemp_tracked


@_at0.register
def _sweep_tmp0():
    # Только СВОИ каталоги этого процесса и только под /tmp: чужого не трогаем.
    for _d in _MADE_TMP0:
        if str(_d).startswith('/tmp/'):
            _sh0.rmtree(_d, ignore_errors=True)


_os0.environ['ADDFUT_LOCK_DIR'] = _tf0.mkdtemp(prefix='addfut-inv-')
import pathlib as _pl0
(_pl0.Path(_os0.environ['ADDFUT_LOCK_DIR']) / 'route.txt').write_text('F')
for _v0 in ('ADDFUT_BOOK_PATH', 'ADDFUT_DIR', 'ADDFUT_SIGNALS'):
    _os0.environ.pop(_v0, None)

import sys
from pathlib import Path
import itertools

ROOT = Path(__file__).resolve().parent.parent
# ПУТЬ БОЕВОГО СКРИПТА АВТОПИЛОТА — ВЕЛИЧИНОЙ, А НЕ КОНСТАНТОЙ В СТЕНДЕ (инцидент
# 19.08.2026, §12): шелловый слой не проверялся ничем, и парной мутации у него не
# было тоже. Мутация подменяет путь копией того же файла с возвращённым дефектом —
# ломается ПРОИЗВОДСТВЕННЫЙ текст, а не стенд.
# ПУТЬ — ВНУТРИ ПАКЕТА, А НЕ РАБОЧЕГО ДЕРЕВА (поймано выпускным барьером 19.08.2026).
# Здесь стояло ROOT.parent/'tools'/autopilot.sh: в рабочем дереве это СИМВОЛИЧЕСКАЯ ссылка
# на live/autopilot.sh, и стенд проходил, а в распакованном архиве каталога tools нет вовсе —
# оба шелловых стенда падали. Ровно тот класс, о котором предупреждает комментарий у
# _adapter: «проверки, опиравшиеся на рабочий каталог, падали на распакованном архиве».
# Настоящий файл лежит рядом с этим модулем и есть в манифесте.
AUTOPILOT_SH = ROOT / 'live' / 'autopilot.sh'
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'live'))
import pandas as pd
import sim_v13 as S
import daily as DL

BOOKS = [(0, 0, None), (9, 0, 0), (10, 0, 1), (100, 50, 10), (260, 101, 26), (267, 101, 26),
         (16, 10, 1), (250, 101, 25)]     # упаковка на единицу НИЖЕ канонической
CAPITALS = [10_000_000.0, 3_000_000.0, 2_999_999.0, 1_000_000.0, 400_000.0]
PAPER = [False, True]        # бумажный этап обходит порог §8 — путь надо перебирать тоже
# Дата и признак ролла РАЗВЯЗАНЫ: интерфейс допускает «сегодня дата ролла, а флаг ложный»,
# и раньше такое состояние в переборе не встречалось вовсе.
DATES = [('2026-08-26', True, False), ('2026-08-26', False, False), ('2026-08-27', False, True),
         ('2026-08-10', False, False), ('2026-09-10', False, True), ('2026-09-10', True, True)]
SIGNALS = [(True, True), (True, False), (False, True), (False, False)]
# Отложенный ролл, РАЗНЫЕ серии ног и несогласованная упаковка — прежде не перебирались.
# ПЛЕЧО НА ЗАКРЫТИИ — прежде всегда нулевое, из-за чего ВСЯ ветка кап-коррекции §1 была
# недостижима: цель по построению равна капу и после округления вниз его не превышает.
# Тринадцать первых инвариантов дыру не показывали — ни один не требовал туда попасть.
EXTRAS = [dict(), dict(roll_pending=True), dict(ser_a='M26'), dict(es_held=99),
          dict(prev_close_lev=2.40), dict(prev_close_lev=2.02)]
PRICES = [600.0, -600.0, float('nan')]        # невалидные цены прежде не перебирались


def states():
    for (ne, nb, es), cap0, (ds, roll, passed), (se, sb), extra, px, paper in itertools.product(
            BOOKS, CAPITALS, DATES, SIGNALS, EXTRAS, PRICES, PAPER):
        kw = dict(d_fix=8.0, n_e=ne, n_b=nb, unit_is_mes=True, prev_st_eq=True,
                  prev_st_bd=True, ser_a='U26' if ne or nb else None,
                  ser_b='U26' if ne or nb else None, es_held=es)
        kw.update({k: v for k, v in extra.items() if k != 'es_held' or es is not None})
        b = DL.Book(**kw)
        m = DL.Market(date=pd.Timestamp(ds), px_eq_prev=px, dref_prev=8.0, dref_today=8.0,
                      px_eq_today=600.0, roll_today=roll, st_eq=se, st_bd=sb,
                      roll_passed=passed)
        yield b, m, cap0, paper


def apply_orders(book, orders):
    p = dict(DL.physical_book(book))
    for i, q in orders:
        p[i] = p.get(i, 0) + q
    return {k: v for k, v in p.items() if v}


INVARIANTS = []
COVER = {}


def inv(name, needs=None):
    """needs — предикат «состояние по существу проверяет этот инвариант».

    БЕЗ НЕГО ПРОВЕРКА ЛЖЁТ. Инвариант, чьё условие ни разу не возникло, всегда возвращает
    успех и выглядит как доказательство. Внешняя рецензия показала, что два из шести
    инвариантов сессии были именно такими, а третий отключался ровно на опасных путях.
    Теперь нулевое покрытие — ПРОВАЛ."""
    def deco(fn):
        INVARIANTS.append((name, fn, needs)); return fn
    return deco


@inv('заявки приводят книгу брокера ровно в записанное состояние',
     needs=lambda b, m, cap0, d, o, ue, ub: bool(o))
def _i1(b, m, cap0, d, orders, u_e, u_b):
    return apply_orders(b, orders) == DL.physical_book(d.book_after)


@inv('при отказе §8 суммарная экспозиция не растёт',
     needs=lambda b, m, cap0, d, o, ue, ub: bool(d.refusals) and ue == ue and ue > 0
     and ub == ub and ub > 0)
def _i2(b, m, cap0, d, orders, u_e, u_b):
    if not d.refusals:
        return True
    before = abs(b.n_e) * u_e + abs(b.n_b) * u_b
    after = abs(d.book_after.n_e) * u_e + abs(d.book_after.n_b) * u_b
    return after <= before + 1e-6


@inv('плечо после решения не превышает кап')
def _i3(b, m, cap0, d, orders, u_e, u_b):
    return d.leverage <= DL.CAP_LEV + 1e-9


@inv('удерживаемая серия не находится в своём месяце поставки без отказа',
     needs=lambda b, m, cap0, d, o, ue, ub: bool(b.ser_a or b.ser_b))
def _i4(b, m, cap0, d, orders, u_e, u_b):
    for tag in (d.book_after.ser_a, d.book_after.ser_b):
        if tag and DL.delivery_risk(tag, m.date) and not d.refusals:
            return False
    return True


@inv('при смене серии старая закрывается ЗАЯВКАМИ полностью',
     needs=lambda b, m, cap0, d, o, ue, ub: not d.refusals and b.ser_b is not None
     and b.ser_b != d.book_after.ser_b and b.n_b != 0)
def _i5(b, m, cap0, d, o, ue, ub):
    """Проверяется по ЗАЯВКАМ, а не по книге.

    Прежняя формулировка сверяла итоговую книгу с самой собой: имя старой серии в ней не
    может встретиться по построению, потому что имена собираются из ser_* новой книги.
    Мутационная проверка это и показала — утверждение не убивала ни одна поломка.
    Настоящий вопрос в другом: ушла ли брокеру заявка, закрывающая старую серию целиком.
    """
    old = DL.physical_book(b)
    sent = {}
    for inst, q in o:
        sent[inst] = sent.get(inst, 0) + q
    for inst, held in old.items():
        if inst.endswith(b.ser_b) or (b.ser_a and inst.endswith(b.ser_a)):
            if inst[-3:] != (d.book_after.ser_b or '') and inst[-3:] != (d.book_after.ser_a or ''):
                if sent.get(inst, 0) != -held:
                    return False
    return True


@inv('число целых ES не отрицательно и не превышает сетку',
     needs=lambda b, m, cap0, d, o, ue, ub: not d.refusals)
def _i6(b, m, cap0, d, orders, u_e, u_b):
    es = d.book_after.es_held
    if es is None:
        return True
    return 0 <= es and 10 * es <= max(d.book_after.n_e, 0) + 9


@inv('невалидная цена или неизвестный маршрут дают отказ, а не позицию',
     needs=lambda b, m, cap0, d, o, ue, ub: True)
def _i8(b, m, cap0, d, o, ue, ub):
    ok_px = all(isinstance(x, (int, float)) and x == x and x > 0
                for x in (m.px_eq_prev, m.dref_prev))
    return ok_px or (bool(d.refusals) and not o)


@inv('книга никогда не становится короткой',
     needs=lambda b, m, cap0, d, o, ue, ub: True)
def _i9(b, m, cap0, d, o, ue, ub):
    return d.book_after.n_e >= 0 and d.book_after.n_b >= 0


@inv('число MES в книге неотрицательно (нет скрытого short)',
     needs=lambda b, m, cap0, d, o, ue, ub: d.book_after.unit_is_mes and not d.refusals)
def _i10(b, m, cap0, d, o, ue, ub):
    es = d.book_after.es_held
    if es is None:
        return True
    return d.book_after.n_e - 10 * es >= 0


@inv('ниже порога §8 книга не строится, если это не бумажный этап',
     needs=lambda b, m, cap0, d, o, ue, ub: cap0 < DL.MIN_NLV_F and ue == ue and ue > 0
     and not any('БУМАЖНЫЙ ЭТАП' in r for r in d.reasons))
def _i11(b, m, cap0, d, o, ue, ub):
    # Мутационный тест показал, что полное отключение отказов §8 не ловил НИКТО: порог,
    # гранулярность и неизвестный маршрут можно было убрать целиком незаметно.
    grew = (abs(d.book_after.n_e) > abs(b.n_e)) or (abs(d.book_after.n_b) > abs(b.n_b))
    return bool(d.refusals) or not grew


@inv('избыток перекладки не приписывает себе оборот ролла',
     needs=lambda b, m, cap0, d, o, ue, ub: (m.roll_today or b.roll_pending)
     and not d.refusals)
def _i12a(b, m, cap0, d, o, ue, ub):
    """ДВАДЦАТЬ ДЕВЯТЫЙ КРУГ, №6. Избыток считался разностью физических книг ВМЕСТЕ со
    сменой серии: на ролле без изменения экспозиции и упаковки закрытие старой серии и
    открытие новой давали сотни единиц сетки при чистом нуле, и каждый роллный день
    оператор получал тревогу о недосписанных деньгах — при том что ролл оплачен
    нормативным 1 б.п. Ложная тревога хуже молчания: она приучает не читать причины.

    УСЛОВИЕ БОЛЬШЕ НЕ ИСКЛЮЧАЕТ РОЛЛЫ СО СМЕНОЙ УПАКОВКИ (тридцать девятый круг, №3).
    Здесь стояло `d.book_after.es_held == b.es_held`, то есть утверждение отказывалось
    смотреть ровно на те роллы, где pack_es канонизирует упаковку, — а именно на них
    избыток и приписывал себе бесплатную по норме канонизацию: появлялась ложная причина, а
    у границы капа — лишний срез по несуществующему расходу и недобранная позиция. Стенд,
    исключающий случай, ради которого написан, доказывает только сам себя."""
    return not any('ИЗБЫТОК ОБОРОТА УПАКОВКИ' in r for r in d.reasons)


@inv('кап 2,00 держится и по капиталу за вычетом несписанного избытка',
     needs=lambda b, m, cap0, d, o, ue, ub: not d.refusals and (d.orders or d.roll_pairs)
     and d.capital_after_costs and d.capital_after_costs > 0 and ue == ue and ue > 0)
def _i12b(b, m, cap0, d, o, ue, ub):
    """ДВАДЦАТЬ ДЕВЯТЫЙ КРУГ, №6. Запись причины денег не возвращает и кап не соблюдает:
    издержки известны ДО заявок, а порог 2,00 проверялся по капиталу, из которого они не
    вычтены. NAV не трогаем (списание ломает 1e-12), но ворота обязаны считаться по
    пессимистичному капиталу."""
    # Избыток пересчитывается СТЕНДОМ САМОСТОЯТЕЛЬНО, а не берётся из причин дня: иначе
    # снятие защиты убирало бы и причину, и утверждение молчало бы вместе с ней.
    # КАНОНИЗАЦИЯ РОЛЛА ИСКЛЮЧАЕТСЯ И ЗДЕСЬ — НО СВОИМ СЧЁТОМ (тридцать девятый круг, №3,
    # найдено саморецензией). Утверждение НАМЕРЕННО считает избыток само, чтобы подмена
    # repack_excess не гасила разом и защиту, и наблюдение. После правки кода это дало
    # рассогласование в другую сторону: на ролле со сменой упаковки код считает избыток
    # нулевым (канонизация бесплатна по норме), а утверждение — положительным, и требовало
    # бы кап от УМЕНЬШЕННОГО капитала строже, чем предписано. Нормативный смысл один:
    # канонизация на ролле оборота не создаёт. Повторяем его здесь независимо от
    # repack_excess — своим вызовом pack_es.
    # НА ЛЮБОМ РОЛЛЕ (в т.ч. отложенном и навёрстывании) УПАКОВКА БЕСПЛАТНА (сороковой
    # круг, №2): прежде здесь канонизировалось только при m.roll_today, и roll_pending с
    # навёрстыванием оставались на старой формуле — утверждение снова стало бы строже кода.
    _roll_now = bool(m.roll_today or b.roll_pending
                     or DL.leg_roll_overdue(b.ser_a, m) or DL.leg_roll_overdue(b.ser_b, m))
    _same = DL.replace(d.book_after, ser_a=b.ser_a, ser_b=b.ser_b)
    _phys = DL.orders_from_books(b, _same)
    _g = DL.repack_grid(_phys, b.unit_is_mes) if _phys else 0
    _n = _g if _roll_now else abs(d.book_after.n_e - b.n_e) * (1 if b.unit_is_mes else 10)
    if _g <= _n:
        return True
    _ex = DL.repack_cost(_g - _n, ue)
    _e = d.capital_after_costs - _ex
    if _e <= 0:
        return True                     # книга и так срезана до нуля — ворота отработали
    return (d.exposure['А'] + d.exposure['Б']) <= DL.CAP_LEV * _e + 1e-9


@inv('ниже механического пола книга не наращивается никогда',
     needs=lambda b, m, cap0, d, o, ue, ub: ue == ue and ue > 0 and ub == ub and ub > 0
     and (ub / 2 > 0.10 * cap0))
def _i12(b, m, cap0, d, o, ue, ub):
    return bool(d.refusals) and not (abs(d.book_after.n_b) > abs(b.n_b))


@inv('упаковка ES даёт МИНИМАЛЬНЫЙ оборот',
     needs=lambda b, m, cap0, d, o, ue, ub: b.unit_is_mes and b.es_held is not None
     and not d.refusals and not m.roll_today and not b.roll_pending
     and not DL.is_roll_day(m.date)          # ролл определяется КАЛЕНДАРЁМ, а не признаком
     # НАВЁРСТЫВАНИЕ ПРОПУЩЕННОГО РОЛЛА — тоже ролл (упаковка каноническая); условие берём
     # ЕДИНЫМ ИСТОЧНИКОМ из движка: дублированная копия уже разошлась один раз (нога Б).
     and not DL.missed_roll_check(b, m)
     and 0 <= 10 * b.es_held <= max(b.n_e, 0) and d.book_after.n_e > 0)
def _i13(b, m, cap0, d, o, ue, ub):
    """Оборот измеряется в MES-эквивалентах: смена одного ES стоит десяти.

    Прежняя формулировка ловила лишь «поменяли, когда не обязаны», и мутация
    «упаковка всегда каноническая» проходила мимо: канон меняет ES БОЛЬШЕ необходимого,
    но формально имеет право менять. Оптимум определяется здесь независимо — перебором
    всех допустимых упаковок, а не ссылкой на проверяемую функцию.
    """
    n0, n1 = b.n_e, d.book_after.n_e
    es0, es1 = b.es_held, d.book_after.es_held
    if es1 is None:
        return True

    def turn(es):
        mes = n1 - 10 * es
        if mes < 0:
            return None
        return abs(es - es0) * 10 + abs(mes - (n0 - 10 * es0))

    best = min(t for t in (turn(e) for e in range(0, n1 // 10 + 1)) if t is not None)
    got = turn(es1)
    return got is not None and got <= best


@inv('выключенная сигналом нога закрывается ПОЛНОСТЬЮ, а не частично',
     needs=lambda b, m, cap0, d, o, ue, ub: not d.refusals and
     ((not m.st_eq and b.n_e) or (not m.st_bd and b.n_b)))
def _i14(b, m, cap0, d, o, ue, ub):
    """Класс «сделано, но недостаточно»: двоичные утверждения его не видят.

    Сокращение ноги вдвое вместо выключения удовлетворяет и капу, и полосе, и совпадению
    книги с состоянием — а это уже другая стратегия, чем описана в §1.
    """
    if not m.st_eq and d.book_after.n_e != 0:
        return False
    if not m.st_bd and d.book_after.n_b != 0:
        return False
    return True


@inv('нога вне полосы возвращается К ЦЕЛИ, а не просто ближе к ней',
     needs=lambda b, m, cap0, d, o, ue, ub: not d.refusals and not d.cap_correction
     and ue > 0 and ub > 0 and (
         (m.st_eq and abs(1.0 * cap0 - b.n_e * ue) > 0.10 * cap0) or
         (m.st_bd and abs(1.0 * cap0 - b.n_b * ub) > 0.10 * cap0)))
def _i15(b, m, cap0, d, o, ue, ub):
    """Выход за полосу обязывает вернуться к цели с точностью до целого контракта.

    ОБЕ НОГИ. Прежняя редакция смотрела только на ZN, да ещё требовала обе ноги включёнными:
    поломка, возвращающая ES/MES внутрь полосы, но не к цели, этим утверждением не ловилась
    вовсе — притом что название обещало проверку любой ноги.
    """
    for on, held, got, u in ((m.st_eq, b.n_e, d.book_after.n_e, ue),
                             (m.st_bd, b.n_b, d.book_after.n_b, ub)):
        if not on:
            continue
        tgt = 1.0 * cap0
        if abs(tgt - held * u) <= 0.10 * cap0:
            continue                       # эта нога в полосе — трогать не обязаны
        if abs(got * u - tgt) > u / 2 + 1e-6:
            return False
    return True


@inv('кап срезает НЕ ГЛУБЖЕ, чем требует фактическое превышение',
     needs=lambda b, m, cap0, d, o, ue, ub: d.cap_correction and d.cap_before
     and not d.refusals and ue > 0 and ub > 0)
def _i16(b, m, cap0, d, o, ue, ub):
    """Обратная сторона класса «сделано не то»: срезать сверх нужного — тоже дефект.

    Минимальности вообще §1 не обещает: после превышения плеча на ЗАКРЫТИИ округление вверх
    запрещено обеим ногам, и это намеренная предосторожность (измерено: на окне ядра она
    даёт 10,95% против 10,90% без неё при меньшей качке). Проверяется поэтому не минимум
    вообще, а минимум ОТНОСИТЕЛЬНО этой отправной точки: ниже неё нога опускается только
    тогда, когда возврат одной единицы действительно пробивает кап.
    """
    import math
    pe0, pb0 = d.cap_before
    n0e, n0b = b.n_e, b.n_b
    fe, fb = d.book_after.n_e, d.book_after.n_b
    roll_now = bool(m.roll_today or b.roll_pending)

    # СТОИМОСТЬ ПРОБНОГО ВОЗВРАТА СЧИТАЕТСЯ ФОРМУЛОЙ ДВИЖКА, А НЕ «минус комиссия».
    # Прежняя редакция всегда ВЫЧИТАЛА COST*u, тогда как возврат единицы, приближающий
    # конечную позицию к исходной, комиссию наоборот ВОЗВРАЩАЕТ. Из-за этого утверждение
    # могло и пропустить чрезмерный срез, и обвинить правильный.
    def costs(ne, nb):
        return DL.S.COST * ((abs(ne - n0e) - abs(pe0 - n0e)) * ue +
                            (abs(nb - n0b) - abs(pb0 - n0b)) * ub)

    def roll_cost(ne, nb):
        return DL.S.ROLL_BP * (ne * ue + nb * ub) if roll_now else 0.0

    e_pre = d.capital_after_costs + costs(fe, fb) + roll_cost(fe, fb)

    def breach(ne, nb):
        ea = e_pre - costs(ne, nb) - roll_cost(ne, nb)
        return (ne * ue + nb * ub) > DL.CAP_LEV * ea

    base_e = min(pe0, math.floor(1.0 * m.st_eq * cap0 / ue))
    base_b = min(pb0, math.floor(1.0 * m.st_bd * cap0 / ub))
    if fe > base_e or fb > base_b:
        return False                                    # обрезка не может НАРАЩИВАТЬ
    if fe < base_e and not breach(fe + 1, fb):
        return False                                    # единицу ноги А можно было оставить
    if fb < base_b and not breach(fe, fb + 1):
        return False                                    # единицу ноги Б можно было оставить
    return True


@inv('при ролле переносятся ВСЕ ноги с позицией, а не часть',
     needs=lambda b, m, cap0, d, o, ue, ub: not d.refusals
     and (m.roll_today or b.roll_pending) and (b.n_e or b.n_b))
def _i17(b, m, cap0, d, o, ue, ub):
    """НУЖДА ВЫВОДИТСЯ ИЗ ВХОДА, А НЕ ИЗ РЕЗУЛЬТАТА. Прежнее условие требовало непустого
    d.roll_pairs — то есть дефект, УДАЛЯЮЩИЙ перенос целиком, отключал само утверждение, и
    оно молчало ровно в том случае, ради которого написано. Парная мутация убирала лишь
    ногу Б и потому создавала покрытие искусственно."""
    # НУЖДА — ПО СРОКУ КАЖДОЙ СЕРИИ, А НЕ ПО ОБЩЕМУ ДНЮ (тридцать шестой круг, №1).
    # Прежняя редакция требовала роллить ВСЕ ненулевые ноги при m.roll_today и тем самым
    # ЗАКРЕПЛЯЛА дефект как норму: при смешанной книге (А уже в Z26, Б ещё в U26) она
    # требовала увести исправную ногу А в H27 — квартал вперёд и лишний оборот. Роллится
    # нога, чья серия сегодня истекает либо чей перенос откатился; остальные не трогаются.
    # ОЖИДАНИЕ СЧИТАЕТСЯ НЕЗАВИСИМО ОТ ПРОВЕРЯЕМОГО КОДА (тридцать седьмой круг, №17).
    # Прежде нужда выводилась через DL.leg_roll_due — ту же функцию, которая управляет
    # решением: мутация `leg_roll_due = lambda ...: True` заставляла и код, и инвариант
    # требовать лишний ролл, и прогон оставался зелёным. Доказательство, использующее
    # проверяемую функцию как эталон, доказывает только её самосогласованность. Срок серии
    # выводится здесь из СРОКА ПОСТАВКИ (roll_deadline), а признак дня — из даты.
    def _due(_ser):
        if _ser is None:
            return (False, False)
        try:
            _dl = DL.roll_deadline(_ser, getattr(m, 'holidays', ()) or ())
        except Exception:
            return (False, False)
        return (m.date == _dl, m.date > _dl)

    _rp = b.roll_pending
    _pend = {'А': _rp is True or (isinstance(_rp, str) and 'А' in _rp),
             'Б': _rp is True or (isinstance(_rp, str) and 'Б' in _rp)}
    legs = {p['leg'] for p in (d.roll_pairs or ())}
    need = set()
    for _l, _n, _ser in (('А', b.n_e, b.ser_a), ('Б', b.n_b, b.ser_b)):
        if not _n:
            continue
        # ТО ЖЕ ПРАВИЛО, ЧТО В КОДЕ, НО СВОИМ СЧЁТОМ (сорок первый круг, №4): условие
        # `not _mixed or _today` закрепляло дефект — при обеих ногах в уже свежей серии
        # инвариант ТРЕБОВАЛ лишнего ролла на квартал вперёд. Срок решает всегда, когда
        # календарь известен; без праздничной таблицы судить нечем и решает общий признак.
        _hh = bool(getattr(m, 'holidays', ()) or ())
        _today, _late = _due(_ser)
        if _pend[_l] or (m.roll_today and (not _hh or _today)) or _late:
            need.add(_l)
    # И ОБРАТНО: нога, чей срок НЕ наступил, роллиться не смеет.
    _extra = legs - need
    return need <= legs and not _extra


@inv('нулевых заявок не бывает',
     needs=lambda b, m, cap0, d, o, ue, ub: bool(o))
def _i7(b, m, cap0, d, orders, u_e, u_b):
    return all(q for _, q in orders)


# ---------------------------------------------------------------- уровень сессии

SESSION_INVARIANTS = []
SCOVER = {}


def sinv(name, needs=None):
    def deco(fn):
        SESSION_INVARIANTS.append((name, fn, needs)); return fn
    return deco


@sinv('без исключения книга брокера совпадает с сохранённой',
      needs=lambda r: not r['raised'])
def _s1(res):
    # Разложение книги зависит от МАРШРУТА: у фондов нет ни серий, ни упаковки.
    import state as _ST
    exp = _ST.expected_positions(res['saved'], res['route'])
    return {k: float(v) for k, v in res['broker_after'].items()} == \
           {k: float(v) for k, v in exp.items()}


@sinv('ПРИ ИСКЛЮЧЕНИИ сохранённая книга не выдаётся за исполненную',
      needs=lambda r: r['raised'])
def _s2(res):
    # Прежняя редакция при исключении всегда возвращала успех — то есть отключалась ровно
    # на опасных путях. Теперь проверяется по существу: либо состояние осталось прежним,
    # либо оно помечено как отложенный ролл; выдать целевую книгу за исполненную нельзя.
    return res['saved_digest'] == res['digest_before'] or res['note_pending']


@sinv('ПРИ ИСКЛЮЧЕНИИ расхождение с брокером либо устранено, либо НАЗВАНО',
      needs=lambda r: r['raised'] and r['route'] == 'F')
def _s2b(res):
    """Сохранности файла НЕДОСТАТОЧНО, и это главный денежный исход.

    Прежнее утверждение смотрело только на состояние: файл остался прежним — значит успех.
    Но книга у БРОКЕРА при этом могла быть любой, и удаление всего восстановления после
    частичного исполнения такую проверку прошло бы. Требуется одно из двух: фактическая
    книга совпала с сохранённой (восстановление удалось) — либо расхождение прямо названо
    в сообщении как промежуточное состояние, требующее разбора О-5. Молчаливое расхождение
    запрещено.
    """
    import state as ST
    # НИ ОДНОЙ ЗАЯВКИ — НИЧЕГО И НЕ СЛОМАНО. Сессия, остановленная на входной сверке
    # (нечисловое количество у брокера, живая заявка), расхождения не создавала: оно
    # существовало до неё, и отказ — правильный исход. Требовать здесь совпадения книг
    # значило бы требовать от контура чинить то, чего он не делал.
    if not res.get('placed_count'):
        return True
    saved, broker = res['saved'], res['broker_after']
    if saved is not None and not ST.reconcile(saved, res['route'], broker):
        return True
    # Расхождение СОЗДАНО этой сессией — оно обязано быть названо, а не оставлено молча.
    # ФРАЗА «книга восстановлена в исходном виде» СЮДА НЕ ВХОДИТ НАМЕРЕННО: это утверждение,
    # а не факт, и принимать его за доказательство — та же ошибка, что принимать статус
    # заявки за исполнение. Если книга у брокера не совпала с сохранённой, заявление о
    # восстановлении ЛОЖНО, и сессия обязана называть состояние неразобранным.
    msg = res.get('error') or ''
    return any(k in msg for k in ('О-5', 'промежуточ', 'ВОССТАНОВИТЬ НЕ УДАЛОСЬ',
                                  'ручной разбор'))


@sinv('живых заявок после сессии не остаётся, либо они НАЗВАНЫ',
      needs=lambda r: bool(r.get('open_after')))
def _s2c(res):
    """Незакрытая заявка живёт своей жизнью и исполнится ПОЗЖЕ — после того, как контур
    решил, что сессия окончена. Молчаливо оставить её нельзя: либо снята, либо названа.
    Мутация «неснятые заявки игнорируются» прежде не ловилась ничем: сверка смотрела на
    позиции и сохранённый файл, а живые заявки после сессии не проверял никто."""
    msg = res.get('error') or ''
    # ПОКА ЖИВАЯ ЗАЯВКА ЕСТЬ, ЗАЯВЛЯТЬ О ВОССТАНОВЛЕНИИ НЕЛЬЗЯ. Иначе сообщение говорит
    # «книга восстановлена в исходном виде», а заявка исполнится позже и книгу изменит —
    # ровно тот случай, который сессия объявила бы благополучно закрытым.
    # ПРИ ЖИВОЙ ЗАЯВКЕ ЛЮБОЕ заявление о приведении книги ложно — с оговоркой или без:
    # заявка исполнится позже и книгу изменит. Оговорка «подтверждение сверкой» не
    # индульгенция (двенадцатый круг: ослабленный вариант пропускал мутацию). И «по
    # снимку соответствует исходной» (шестнадцатый круг, №5) — то же заявление: ok=True
    # достижим только при подтверждённо снятых заявках, живая заявка рядом с ним = ложь.
    if 'приведена к исходной' in msg or 'соответствует исходной' in msg:
        return False
    return any(k in msg for k in ('заяв', 'О-5', 'ручной разбор'))


@sinv('плохой брокер обязательно поднимает исключение',
      needs=lambda r: r['behaviour'] != 'normal' and r['orders_expected'])
def _s3(res):
    return res['raised']


@sinv('незакрытая заявка останавливает сессию', needs=lambda r: r['had_open'])
def _s5(res):
    return res['raised']


@sinv('нечисловое количество не проходит как совпадение', needs=lambda r: r['nan_case'])
def _s6(res):
    return res['raised']


@sinv('после разрыва связи дубликат не подаётся',
      needs=lambda r: r['behaviour'] == 'disconnect')
def _s7(res):
    return res['raised'] and res['placed_count'] <= 1


@sinv('маршрут Е строит книгу в долях фондов', needs=lambda r: r['route'] == 'E')
def _s8(res):
    """ДВАДЦАТЬ ПЕРВЫЙ КРУГ, №20: all() по ПУСТОМУ словарю истинно. Если живой путь Е
    перестанет подавать заявки и оставит брокера пустым, прежнее утверждение прошло бы —
    то есть доказывало «нет чужих инструментов», а выглядело как «книга в фондах».
    Теперь требуется НЕПУСТАЯ позиция и обе ноги."""
    b = res['broker_after']
    chuzhih_net = all(k in ('CSPX', 'CBU0') for k in b)
    # В сбойных повадках (отказ, обрыв, частичное) пустая книга законна — там утверждение
    # только про отсутствие чужих инструментов. А в ШТАТНОЙ книга обязана быть непустой,
    # иначе доказательство вакуумно.
    if res.get('behaviour') != 'normal':
        return chuzhih_net
    return chuzhih_net and bool(b) and any(float(v) for v in b.values())


# ---------------------------------------------------------------- живой адаптер
# ШЕСТАЯ РЕЦЕНЗИЯ, ЗАМЕЧАНИЕ 28. Перебор и мутации гоняли только расчётчик через макет, а
# live/ib_broker.py — код, который реально ходит на биржу, — не проверялся ничем. Поэтому
# несовместимость контракта отмены, усечение дробей, сопоставление отчётов об исполнении и
# подмена контракта имели ГАРАНТИРОВАННО нулевую наблюдаемость. Здесь адаптер попадает под
# те же правила: утверждение с нулевым покрытием — провал.
ADAPTER = []


def ainv(name, needs=None):
    def deco(fn):
        ADAPTER.append((name, fn, needs)); return fn
    return deco


def _adapter(behaviour, positions=None):
    """Адаптер на ФИКСТУРЕ реестра. instruments_live.csv принадлежит конкретному счёту,
    меняется вместе с con_id каждый квартал и в пакет не входит: проверки, опиравшиеся на
    него, проходили в рабочем каталоге и падали на распакованном архиве."""
    import tempfile
    import ib_stub
    import ib_broker as IBB
    d = tempfile.mkdtemp(prefix='addfut-fix-')
    rows = [dict(r) for r in ib_stub.FIXTURE_ROWS]
    if behaviour == 'etf_class_swap':
        # ТРИДЦАТЬ ПЯТЫЙ КРУГ, №11: строка CBU0 согласованно объявляет класс FUT, и биржа
        # (стаб читает те же строки) отвечает тем же. Это ПРЕЖНИЙ дефект
        # etf_expectation_bad — ранний выход при sec_type != 'STK'. Сценарий etf_line_swap
        # менял только primaryExchange при sec_type='STK' и ловился обычной mismatches, то
        # есть привязку класса к ИМЕНИ не доказывал вовсе.
        for _r in rows:
            if _r['instrument'] == 'CBU0':
                _r['sec_type'] = 'FUT'; _r['isin'] = ''
                _r['expiry'] = '20261218'; _r['multiplier'] = '1000'
    if behaviour == 'coherent_series_swap':
        # СОГЛАСОВАННАЯ подмена: строка ESU26 несёт поставку и локальное имя ДЕКАБРЯ, и
        # биржа (стаб читает те же строки) отвечает тем же. mismatches сойдётся, ISIN у
        # фьючерса пуст — расхождение видит только разбор ИМЕНИ.
        for _r in rows:
            if _r['instrument'] == 'ESU26':
                _r['expiry'] = '20261218'; _r['local_symbol'] = 'ESZ6'
    reg = ib_stub.fixture_registry(d, rows)
    ib = ib_stub.StubIB(rows, behaviour=behaviour, positions=positions or {})
    ib._fixture_reg = str(reg)      # путь фикстуры нужен стендам, читающим рынок (№6)
    # Короткое ожидание: при разрыве связи заявка остаётся нетерминальной, и штатные
    # 120 с превращают перебор сценариев в минуты простоя.
    return IBB.IBBroker(ib, registry=reg, settle_s=0.0, timeout_s=1.0), ib, rows


@ainv('исполнение при статусе Cancelled не выдаётся за неисполнение',
      needs=lambda b: b == 'cancelled_but_filled')
def _a1(beh):
    br, ib, rows = _adapter(beh)
    r = br.place(rows[0]['instrument'], 2)
    return isinstance(r, dict) and abs(r['filled'] - 2) < 1e-9


@ainv('позиции не читаются устаревшими', needs=lambda b: b == 'stale_positions')
def _a2(beh):
    import ib_broker as IBB
    br, ib, rows = _adapter(beh)
    br.place(rows[0]['instrument'], 3)
    return br.net_positions().get(rows[0]['instrument']) == 3


@ainv('чужое исполнение с тем же номером заявки не засчитывается',
      needs=lambda b: b == 'foreign_fill')
def _a3(beh):
    br, ib, rows = _adapter(beh)
    r = br.place(rows[0]['instrument'], 1)
    return abs(r['filled'] - 1) < 1e-9        # 77 из чужого отчёта попасть не должно


@ainv('согласованная подмена КЛАССА фонда не торгуется',
      needs=lambda b: b == 'etf_class_swap')
def _a35c(beh):
    """ТРИДЦАТЬ ПЯТЫЙ КРУГ, №11. Пин фонда в 34-м круге привязали к имени, но стенд этого
    не доказывал: etf_line_swap оставлял sec_type='STK', и регрессия к раннему выходу
    `if row['sec_type'] != 'STK': return []` прошла бы все случаи. Денежный путь прежний:
    цена фьючерса размеряет «доли CBU0», и исполнитель отправляет тысячи контрактов вместо
    долей облигационного ETF."""
    import ib_broker as IBB
    br, ib, rows = _adapter(beh)
    try:
        br.place('CBU0', 1)
    except IBB.BrokerError as ex:
        return (not ib._fills) and ('класс' in str(ex))
    return False


@ainv('согласованная подмена листинговой линии фонда не торгуется',
      needs=lambda b: b == 'etf_line_swap')
def _a33e(beh):
    """ТРИДЦАТЬ ТРЕТИЙ КРУГ, №4. У фьючерса имя само несёт поставку (series_mismatch), у
    фонда независимого отображения не было: mismatches() и verify_isin() сравнивают ответ
    биржи со строкой, из которой взят con_id. Согласованная замена CBU0 на другой USD STK
    с его настоящими полями проходила целиком — цена чужого фонда размерила бы ногу Б, а
    сверка с брокером осталась бы зелёной. Ловит только пинованное ожидание (ETF_EXPECT)."""
    import ib_broker as IBB
    br, ib, rows = _adapter(beh)
    try:
        br.place('CBU0', 1)
    except IBB.BrokerError as ex:
        return (not ib._fills) and 'листинговая линия' in str(ex)
    return False


@ainv('бесконечное требование маржи — отказ, а не приказ на продажу',
      needs=lambda b: b == 'inf_maint')
def _a33i(beh):
    """ТРИДЦАТЬ ТРЕТИЙ КРУГ, №13. У EquityWithLoanValue конечность проверялась с 26-го
    круга, у MaintMarginReq — только NaN. inf даёт cushion РОВНО 0, отрицательное —
    отрицательный: оба МОЛЧА проходят порог 1,40 и с тридцать первого круга запускают уже
    не тревогу, а ПРОДАЖУ половины книги по ошибке сводки IBKR."""
    import ib_broker as IBB
    br, ib, rows = _adapter(beh)
    try:
        br.margin_cushion()
    except IBB.BrokerError as ex:
        return 'не конечное положительное' in str(ex)
    return False


@ainv('отрицательное требование маржи — отказ, а не приказ на продажу',
      needs=lambda b: b == 'neg_maint')
def _a33n(beh):
    import ib_broker as IBB
    br, ib, rows = _adapter(beh)
    try:
        br.margin_cushion()
    except IBB.BrokerError as ex:
        return 'не конечное положительное' in str(ex)
    return False


@ainv('котировка реального времени помечается live только при подтверждении биржи',
      needs=lambda b: b == 'realtime_md')
def _a33r(beh):
    """ТРИДЦАТЬ ТРЕТИЙ КРУГ, №12. Проверялась только отрицательная ветка: стаб не задавал
    marketDataType вовсе, а SAME_API не включал realtime_md. Значит возврат условия к
    `_mdt in (None, 1)` (то есть «отсутствие ответа считаем подтверждением») не изменил бы
    ни одного утверждения, а в бою delayed-fallback снова доказывал бы 5 б.п. движением
    рынка. Проверяются ОБА конца: подтверждённое реальное время даёт live=True, задержанные
    данные и молчание биржи — False."""
    br, ib, rows = _adapter(beh)
    br.realtime_md = True
    ib.quote_px = 100.0
    ib.md_type = 1
    _px, _live_rt = br._quote_ref(rows[0]['instrument'])
    ib.md_type = 3
    _px3, _live_d = br._quote_ref(rows[0]['instrument'])
    ib.md_type = None
    _px0, _live_n = br._quote_ref(rows[0]['instrument'])
    return (_live_rt is True and _live_d is False and _live_n is False
            and _px == 100.0)


@ainv('согласованно подменённая серия не торгуется на границе заявки',
      needs=lambda b: b == 'coherent_series_swap')
def _a31s(beh):
    """ТРИДЦАТЬ ПЕРВЫЙ КРУГ, №8. series_mismatch завели в тридцатом круге и подключили
    ТОЛЬКО к feed.contract_of. Адаптер, то есть сама граница placeOrder, продолжал сверять
    ответ биржи со строкой реестра, из которой взял con_id: согласованная подмена проходила
    целиком, и заявка уходила в ДЕКАБРЬСКУЮ поставку под именем сентябрьской. Календарь
    роллов и зона поставки считаются ПО ИМЕНИ, поэтому цена ошибки — вход в месяц поставки.
    Стенд wrong_contract этого не ловит: там биржа отвечает не то, что в реестре."""
    import ib_broker as IBB
    br, ib, rows = _adapter(beh)
    try:
        br.place('ESU26', 1)
    except IBB.BrokerError as ex:
        return (not ib._fills) and ('серия' in str(ex) or 'поставка' in str(ex))
    return False


@ainv('живая полоса долларовой единицы отделяет MES от ES',
      needs=lambda b: b == 'unit_ref')
def _a31u(beh):
    """ТРИДЦАТЬ ПЕРВЫЙ КРУГ, №6. unit_ref — независимая проверка цен плана перехода, и
    именно её ЖИВУЮ реализацию не исполнял ни один стенд: и батарея, и самопроверка
    подставляли собственную правильную таблицу. Полоса MES считалась как
    es_to_unit(px * 10), то есть выходила равной полосе ES — вдесятеро выше истины.
    Правильный план Е->Ф отвергался бы «вне рыночной полосы», а подогнанный под ошибочную
    полосу купил бы десятую часть ноги А.

    Стенд проверяет ОБА конца: модельная единица MES (50 x SPY) обязана попасть в полосу,
    а единица ES (500 x SPY) — НЕ попасть. Совпадение полос ES и MES и есть дефект.
    """
    import os
    import feed as FDx
    br, ib, rows = _adapter(beh)
    _t = FDx.exchange_today()
    import pandas as _pd
    _d1 = (_pd.Timestamp(_t) - _pd.Timedelta(days=1)).strftime('%Y-%m-%d')
    _d2 = (_pd.Timestamp(_t) - _pd.Timedelta(days=2)).strftime('%Y-%m-%d')
    # MES и ES котируются ОДНИМ уровнем индекса; SPY — его десятая доля.
    _es = [(_d2, 7700.0), (_d1, 7747.5)]
    ib.set_bars({900001: list(_es), 900002: list(_es),
                 900004: [(_d2, 690.0), (_d1, 700.0)]})
    _keep = os.environ.get('ADDFUT_REGISTRY')
    os.environ['ADDFUT_REGISTRY'] = ib._fixture_reg
    try:
        lo_m, hi_m = br.unit_ref('MESU26', 'FUT')
        lo_e, hi_e = br.unit_ref('ESU26', 'FUT')
        lo_f, hi_f = br.unit_ref('CSPX', 'ETF')
    finally:
        os.environ.pop('ADDFUT_REGISTRY', None)
        if _keep is not None:
            os.environ['ADDFUT_REGISTRY'] = _keep
    _spy = 7747.5 / 10.0
    return (lo_m <= 50.0 * _spy <= hi_m          # единица MES внутри своей полосы
            and not (lo_m <= 500.0 * _spy <= hi_m)   # и полоса НЕ равна полосе ES
            and lo_e <= 500.0 * _spy <= hi_e
            and lo_f <= 700.0 <= hi_f)


@ainv('предпросмотр БЕЗ плана судит по нормативу О-3-Е, а не по единице',
      needs=lambda b: b in ('normal', 'thin_cushion'))
def _a45nopl(beh):
    """СОРОК ПЯТЫЙ КРУГ, №2 (P0). Ветка без плана отказывала лишь ниже 1,0, а порог
    1,0 не значит ничего: норматив О-3-Е требует 1,40. Книга с ЖИВЫМ запасом 1,20 получала
    «да» — и это не гипотеза: при полностью исполненном, но не завершённом resume
    pv_remainder честно отдаёт ПУСТОЙ план, вызывающий зовёт preview без плана, и переход
    получает COMPLETE при фактическом запасе ниже норматива. Финальный gross() не спасает:
    он сверяет модельное плечо, а не живое требование маржи.

    Случай гоняется на ДВУХ сценариях стаба: здоровый запас обязан пройти, тонкий —
    получить отказ с честной причиной. Один конец доказывал бы либо «разрешает всегда»,
    либо «отказывает всегда»."""
    import daily as _DLn
    br, ib, rows = _adapter(beh)
    ib._pos = {900001: 1.0}
    ib._shown = dict(ib._pos)
    _c = br.margin_cushion()
    _r = br.preview()
    if _c is None:
        return _r is False
    if float(_c) >= _DLn.O3E_MIN:
        return _r is True and br._preview_why == ''
    return _r is False and 'О-3-Е' in br._preview_why


@ainv('неизвестный срок ролла ОСТАНАВЛИВАЕТ, а не отвечает «роллить не пора»',
      needs=lambda b: b == 'normal')
def _a45roll(beh):
    """СОРОК ПЯТЫЙ КРУГ, №4 (P0). В leg_roll_due и leg_roll_overdue стояло
    `except Exception: return False`: любая ошибка календаря — непокрытый год, нечитаемый
    тег серии, опечатка — молча становилась доменным «роллить не пора». Дальше сессия идёт
    как ни в чём не бывало и может УВЕЛИЧИТЬ старую серию, а delivery_risk молчит до месяца
    поставки: поставочный сторож был fail-open ровно там, где поставка и решается.

    Проверяются ОБА конца: нечитаемая серия обязана ОСТАНОВИТЬ обе функции, а штатная —
    пройти. Без второго конца годился бы код, отказывающий всегда."""
    import daily as _DLr
    import pandas as _pdr

    class _M:
        date = _pdr.Timestamp('2026-08-20')
        holidays = ()

    _stopped = 0
    for _tag in ('XX', ''):
        for _fn in (_DLr.leg_roll_due, _DLr.leg_roll_overdue):
            try:
                _fn(_tag, _M())
            except RuntimeError as _ex:
                if 'поставочный риск неизвестен' in str(_ex):
                    _stopped += 1
    _ok_normal = (_DLr.leg_roll_due('U26', _M()) is False
                  and _DLr.leg_roll_overdue('U26', _M()) is False)
    return bool(_stopped == 4 and _ok_normal)


@ainv('покупка и продажа единиц двигают позицию в СВОЮ сторону',
      needs=lambda b: b == 'normal')
def _a45units(beh):
    """СОРОК ПЯТЫЙ КРУГ, №13. buy_units и sell_units — денежная граница: инверсия
    направления удвоит источник или продаст цель, а SAME_API сверял лишь МОДУЛЬ
    возвращённого объёма и не смотрел на изменение позиции. Мутационного контроля у них не
    было вовсе.

    Проверяется ФАКТ у брокера, а не ответ метода: позиция после покупки выросла ровно на
    заказанное, после продажи — упала. Возвращённый объём сверяется с изменением позиции —
    иначе метод, честно вернувший число и не подавший заявку, прошёл бы."""
    br, ib, rows = _adapter(beh)
    _inst = 'ESU26'
    _cid = 900001
    ib._pos = {_cid: 0.0}
    ib._shown = dict(ib._pos)
    _oid1, _f1 = br.buy_units(_inst, 3)
    _after_buy = float((ib._pos or {}).get(_cid, 0.0))
    _oid2, _f2 = br.sell_units(_inst, 2)
    _after_sell = float((ib._pos or {}).get(_cid, 0.0))
    return bool(abs(_after_buy - 3.0) < 1e-9 and abs(_f1 - 3.0) < 1e-9
                and abs(_after_sell - 1.0) < 1e-9 and abs(_f2 - 2.0) < 1e-9)


@ainv('отчёты дня видят исполнение НАШЕЙ метки, а не пустоту',
      needs=lambda b: b == 'normal')
def _a45exec(beh):
    """СОРОК ПЯТЫЙ КРУГ, №13. todays_executions решает ABORT против MIXED и
    допустимость повторной подачи: пустой ответ означает «заявок не было», то есть
    разрешает повтор поверх уже исполненного. Мутации не было.

    Проверяются оба конца: после исполнения список НЕ пуст и содержит permId поданной
    заявки; на чистом брокере он пуст — иначе годился бы метод, возвращающий что угодно."""
    br, ib, rows = _adapter(beh)
    ib._pos = {900001: 0.0}
    ib._shown = dict(ib._pos)
    _before = list(br.todays_executions() or [])
    br.buy_units('ESU26', 1)
    _after = list(br.todays_executions() or [])
    return bool(not _before and _after)


@ainv('часовой шлюза «не посчитано» не идёт в маржу',
      needs=lambda b: b == 'normal')
def _a44sent(beh):
    """РЕЦЕНЗИЯ 20.08. UNSET_DOUBLE = 1.7976931348623157E308 конечен, и фильтр ib_insync
    его не ловит (сравнивает со строкой Python «…e+308», а TWS шлёт запись Java с заглавной
    E). Часовой доходил до нас обычным значением: одна заявка давала «запас 0.00x», две —
    переполнение в inf, и законный переход уходил в ABORT со словом «маржа» там, где шлюз
    просто не считал. Защита была введена без единого наблюдателя.

    Проверяются ОБА конца: часовой отвергнут с ЧЕСТНОЙ причиной (не «маржа не прошла»), а
    крупная, но законная маржа проходит — иначе порог, опечатанный вниз, запер бы весь
    предпросмотр и стенд этого не заметил."""
    br, ib, rows = _adapter(beh)
    ib._pos = {900001: 1.0}; ib._shown = dict(ib._pos)
    ib.whatif_values = [1.7976931348623157e308]
    _sent = (br.preview([('ESU26', 1)]), br._preview_why)
    ib.whatif_values = [500000.0]                      # крупно, но законно: 1 млн / 500k = 2,0
    _ok = (br.preview([('ESU26', 1)]), br._preview_why)
    return bool(_sent[0] is False and 'не посчитано' in _sent[1]
                and _ok[0] is True and _ok[1] == '')


@ainv('ошибка кода в предпросмотре падает громко, а не становится вердиктом',
      needs=lambda b: b == 'normal')
def _a44code(beh):
    """ИНЦИДЕНТ 19.08 + РЕЦЕНЗИЯ 20.08. Правило слоя «ошибка кода не переодевается в
    доменный вердикт» заведено в state.CODE_ERRORS и применено воротами в preview и
    _release_by_measure — но не наблюдалось ничем: снятие любого из этих `raise` возвращало
    дефект молча. Стенд ломает КОД внутри пути предпросмотра (подменяет helper на
    поднимающий AttributeError) и требует, чтобы исключение вышло НАРУЖУ, а не превратилось
    в False с маржинальным объяснением."""
    br, ib, rows = _adapter(beh)
    ib._pos = {900001: 1.0}; ib._shown = dict(ib._pos)
    _keep = type(br)._contract
    type(br)._contract = lambda self, name: (_ for _ in ()).throw(
        AttributeError('проба: ошибка кода в предпросмотре'))
    try:
        _res, _raised = None, False
        try:
            _res = br.preview([('ESU26', 1)])
        except AttributeError:
            _raised = True
    finally:
        type(br)._contract = _keep
    return bool(_raised and _res is None)


@ainv('каждый отказ предпросмотра называет СВОЮ причину, а не общую',
      needs=lambda b: b == 'normal')
def _a44why(beh):
    """СОРОК ЧЕТВЁРТЫЙ КРУГ, №14(б) + рецензия 19.08. Правка обещала «оператор читает
    настоящую причину», но проверялось это НИЧЕМ: мутация причины в пустую строку
    оставляла батарею зелёной, а пять выходов из десяти не исполнял ни один стенд —
    включая ПУСТОЙ ОТВЕТ шлюза, то есть ровно семью граблей 18.08, ради которой правка и
    делалась.

    Проверяются три разных исхода: молчание шлюза, недостаток запаса по худшей оценке и
    успех. Причины обязаны РАЗЛИЧАТЬСЯ между собой (иначе годилась бы одна общая строка)
    и быть пустыми при успехе — отказа не было, объяснять нечего."""
    br, ib, rows = _adapter(beh)
    ib._pos = {900001: 1.0}; ib._shown = dict(ib._pos)
    ib.whatif = 'нет'                                  # шлюз молчит (семья 18.08)
    _pusto = (br.preview([('ESU26', 1)]), br._preview_why)
    ib.whatif = ''
    ib.whatif_values = [-900000.0, 800000.0]           # худшая оценка 800k при NLV 1 млн
    _tesno = (br.preview([('ESU26', 1), ('MESU26', 1)]), br._preview_why)
    ib.whatif_values = [-900000.0, 500000.0]
    _ok = (br.preview([('ESU26', 1), ('MESU26', 1)]), br._preview_why)
    return bool(_pusto[0] is False and 'пустой ответ whatIf' in _pusto[1]
                and _tesno[0] is False and 'О-3-Е' in _tesno[1]
                and _pusto[1] != _tesno[1]
                and _ok[0] is True and _ok[1] == '')


@ainv('диагност различает порог §8 по капиталу и порог О-3-Е по марже',
      needs=lambda b: b == 'normal')
def _a44dg(beh):
    """СОРОК ЧЕТВЁРТЫЙ КРУГ, №14(в). Сигнатура «ниже порога» совпадала и с текстом О-3-Е
    («запас 1,20x ниже 1,40»), и маржинальный инцидент маршрута Е диагност объявлял отказом
    политики §8 по капиталу с советом ждать решения о пополнении — противоположный диагноз
    в самом срочном из состояний. Проверяются ОБА текста: каждый обязан получить СВОЮ
    первую причину, иначе «разведены» доказывалось бы одним удачным примером."""
    import contextlib
    import io
    import tempfile
    import diagnose as DG
    # ТЕКСТЫ БЕРУТСЯ У ПРОИЗВОДИТЕЛЕЙ, А НЕ ПИШУТСЯ РУКОЙ (рецензия 20.08). Прежняя пара
    # фикстур была скопирована из двух строк daily.py — тех самых, под которые и подгонялись
    # сигнатуры, — поэтому стенд зеленел, а пять ДРУГИХ фактических формулировок (переход
    # запрещён по §8, маржа целевой книги, вахта О-3-Е, пост-трейд О-3-Е, отказ mr_engine)
    # не классифицировались вовсе. Здесь перечислены реальные формы всех производителей;
    # добавление шестой обязано начинаться с добавления сюда.
    _res = {}
    for _key, _body in (
            ('о3е', 'О-3-Е: запас 1.20x ниже 1.4 — сокращение до L=1\n'),
            ('о3е-после', 'О-3-Е ПОСЛЕ ИСПОЛНЕНИЙ: запас 1.20x ниже 1.4 — книга сокращена\n'),
            ('о3е-вахта', 'О-3-Е ВНУТРИДНЕВНАЯ ВАХТА: запас 1.31x ниже 1.4\n'),
            ('о3е-цель', 'маржинальный запас целевой книги 1.20x ниже порога 1.40x О-3-Е\n'),
            ('порог8', 'NLV 2,999,999 ниже порога маршрута Ф 3,000,000 (§8)\n'),
            ('порог8-переход', 'переход в Ф запрещён: NLV 2,999,999 ниже порога 3,000,000 (§8)\n'),
            ('порог8-мр', 'сигнал в Ф при NLV ниже порога §8 (3,000,000)\n')):
        _f = Path(tempfile.mkdtemp(prefix='addfut-dg-')) / 'ALARM.txt'
        _f.write_text(_body, encoding='utf-8')
        _buf = io.StringIO()
        with contextlib.redirect_stdout(_buf):
            DG.main(str(_f))
        _txt = _buf.getvalue()
        _first = next((l for l in _txt.splitlines() if l.startswith('Вероятная причина 1')), '')
        _res[_key] = _first
    _o3e = all('маржи' in _res[k] and 'О-3-Е' in _res[k]
               for k in ('о3е', 'о3е-после', 'о3е-вахта', 'о3е-цель'))
    _p8 = all('капитал' in _res[k] and '§8' in _res[k]
              for k in ('порог8', 'порог8-переход', 'порог8-мр'))
    return bool(_o3e and _p8)


@ainv('смешанные приращения предпросмотра решаются ХУДШЕЙ оценкой, а не суммой',
      needs=lambda b: b == 'normal')
def _a44mix(beh):
    """СОРОК ЧЕТВЁРТЫЙ КРУГ, ложное доказательство №5. Стаб задавал один режим whatIf
    сразу всем заявкам плана, поэтому смеси [-900k, +800k] не существовало ни в одном
    стенде: возврат к раннему разрешению по отрицательной СУММЕ не ловили ни «всё
    отрицательно», ни «всё тесно». Между тем это ровно тот случай, ради которого 40-й круг
    (№1) убрал ранний выход: сумма −100k выглядит освобождением маржи, а положительная
    часть требует 800k при NLV 1 млн, то есть запас 1,25× — ниже норматива О-3-Е 1,40.

    Проверяются обе стороны: смесь с большой положительной частью — ОТКАЗ; та же смесь,
    где положительная часть мала (запас выше 1,40), — разрешение. Иначе «False» доказывал
    бы лишь то, что предпросмотр отказывает всегда."""
    br, ib, rows = _adapter(beh)
    # ЗАПАС СЧЁТА ОБЯЗАН БЫТЬ ЧИСЛОМ, ИНАЧЕ ПРЕДПРОСМОТР ВЫХОДИТ РАНЬШЕ ПЛАНА и стенд
    # проверял бы не смесь приращений, а отсутствие сводки (зонд достижимости: без позиции
    # margin_cushion() = None, оба исхода False, и стенд был бы зелен при снятой защите).
    ib._pos = {900001: 1.0}; ib._shown = dict(ib._pos)
    ib.whatif_values = [-900000.0, 800000.0]          # сумма −100k, худшая оценка 800k
    _bad = br.preview([('ESU26', 1), ('MESU26', 1)])
    ib.whatif_values = [-900000.0, 500000.0]          # 1 млн / 500k = 2,0 ≥ 1,40
    _good = br.preview([('ESU26', 1), ('MESU26', 1)])
    return bool(_bad is False and _good is True)


@ainv('замер маржи спрашивает шлюз ТОЙ ЖЕ формой заявки, что уходит в рынок',
      needs=lambda b: b == 'normal')
def _a44fc(beh):
    """СОРОК ЧЕТВЁРТЫЙ КРУГ, ложное доказательство №2. Явный TIF в замере маржи не
    наблюдался НИЧЕМ: first_connect.main() не исполняет ни один судья, а мутация TIF
    существовала только для IBBroker.preview. Удаление строки не роняло выпуск — генератор
    просто переставал обновлять маржу, пока свежий старый файл проходил проверку возраста,
    и «работает» держалось на удаче.

    Стенд гоняет ПРОИЗВОДСТВЕННУЮ функцию замера на стабе, который воспроизводит причину
    отказа боевого шлюза (без TIF — пустой список), и требует: маржа получена, а заявка
    предпросмотра имеет ту же форму, что уходит в рынок у place() — GTC + outsideRth, и
    привязана к пинованному счёту."""
    import first_connect as FC
    br, ib, rows = _adapter(beh)
    _row = next(r for r in rows if (r.get('sec_type') or '') == 'FUT')
    _m = FC.measure_margin(ib, _row['con_id'], _row['exchange'], ib.managedAccounts()[0])
    _o = getattr(ib, '_last_whatif_order', None)
    return bool(_m and _m.get('init')
                and getattr(_o, 'tif', '') == 'GTC'
                and getattr(_o, 'outsideRth', False) is True
                and getattr(_o, 'account', '') == ib.managedAccounts()[0])


@ainv('фонд живёт по своему календарю, фьючерс — по CME',
      needs=lambda b: b == 'unit_ref')
def _a44cal(beh):
    """СОРОК ЧЕТВЁРТЫЙ КРУГ, №8. unit_ref безусловно брала календарь CME и навязывала его
    CSPX/CBU0. 3 июля 2026 — праздник CME, а LSE/SIX торгуют; 6 июля свежий бар фонда
    ДАТИРОВАН 3-м, тогда как «предыдущая сессия CME» — 2-е. closes(expected_prev=...)
    требует точного совпадения, поднимает [STALE_BAR], и gross() падает уже ПОСЛЕ первой
    исполненной пары: переход уходит в MIXED с непарной позицией.

    Проверяются ОБЕ стороны, иначе правка «взять другой календарь» была бы неотличима от
    «взять европейский всем»: фонд обязан ПРОЙТИ на баре 3 июля, фьючерс — на баре 2 июля.
    """
    import os
    import pandas as _pd
    import feed as FDc
    br, ib, rows = _adapter(beh)
    # СЦЕНАРИЙ ВЫБРАН ПОД НИЖНЮЮ ГРАНИЦУ (уточнено 21.08 мутационным прогоном). После
    # правки №5 полоса фонда сверяется через min_prev, а не точным равенством, и прежний
    # сценарий (праздник CME, Европа торгует) перестал РАЗЛИЧАТЬ календари: бар фонда там
    # свежее обеих предыдущих сессий и проходит при любом календаре — мутация «навязать
    # фондам CME» стала непойманной, то есть моя же правка обезоружила стенд.
    # Различает обратный случай: 1 мая — праздник ЕС и ТОРГОВЫЙ день CME. На 4 мая
    # предыдущая сессия CME — 1 мая, европейская — 30 апреля, а последний бар фонда именно
    # 30 апреля (Европа не работала). С календарём CME нижняя граница отвергает законный
    # бар как устаревший; со своим — принимает.
    _t = _pd.Timestamp('2026-05-04')            # понедельник; 1 мая: ЕС закрыт, CME торгует
    _keep_t, _keep_r = FDc.exchange_today, os.environ.get('ADDFUT_REGISTRY')
    FDc.exchange_today = lambda: _t
    os.environ['ADDFUT_REGISTRY'] = ib._fixture_reg
    try:
        ib.set_bars({900004: [('2026-04-29', 690.0), ('2026-04-30', 700.0)],   # CSPX (LSE)
                     900005: [('2026-04-29', 150.0), ('2026-04-30', 152.0)],   # CBU0 (EBS)
                     900001: [('2026-04-30', 7700.0), ('2026-05-01', 7747.5)], # ES (CME)
                     900002: [('2026-04-30', 7700.0), ('2026-05-01', 7747.5)],
                     # SPY — модельная база ноги А, и она тоже с CME-календарём
                     900010: [('2026-04-30', 770.0), ('2026-05-01', 774.75)]})
        _fund_ok = _fut_ok = False
        try:
            _lo, _hi = br.unit_ref('CSPX', 'STK', at_close=True)
            _fund_ok = _lo <= 700.0 <= _hi
        except Exception:
            _fund_ok = False
        try:
            _lo2, _hi2 = br.unit_ref('ESU26', 'FUT', at_close=True)
            _fut_ok = _hi2 > _lo2 > 0
        except Exception:
            _fut_ok = False
    finally:
        FDc.exchange_today = _keep_t
        os.environ.pop('ADDFUT_REGISTRY', None)
        if _keep_r is not None:
            os.environ['ADDFUT_REGISTRY'] = _keep_r
    return bool(_fund_ok and _fut_ok)


@ainv('часы пары монотонны и пускаются заново',
      needs=lambda b: b == 'normal')
def _a44clk(beh):
    """СОРОК ЧЕТВЁРТЫЙ КРУГ, №10. minutes_since меряла длительность непарной дельты
    НАСТЕННЫМИ часами: шаг NTP назад делает возраст отрицательным и снимает предел 15 минут
    ровно тогда, когда одна нога уже продана. А setdefault в mark_pair означал, что
    повторный ключ наследует часы прошлой пары — ложный тайм-аут на первой же заявке новой.

    Источник различается тривиально и надёжно: monotonic отсчитывает от загрузки машины,
    time() — от 1970 года; разница между ними больше миллиарда. Перезапуск проверяется
    состариванием метки: после mark_pair возраст обязан снова быть нулевым.
    """
    import time as _tm
    br, ib, rows = _adapter(beh)
    br.mark_pair('пара-1')
    _v = br._since['пара-1']
    _mono = abs(_v - _tm.monotonic()) < 5.0
    _wall = abs(_v - _tm.time()) < 5.0
    br._since['пара-1'] = _tm.monotonic() - 3600.0        # состарили на час
    _old = br.minutes_since('пара-1')
    br.mark_pair('пара-1')                                # пуск обязан ПЕРЕЗАПУСТИТЬ часы
    _new = br.minutes_since('пара-1')
    return bool(_mono and not _wall and _old > 59.0 and _new < 1.0)


@ainv('подменённый контракт не торгуется', needs=lambda b: b == 'wrong_contract')
def _a4(beh):
    import ib_broker as IBB
    br, ib, rows = _adapter(beh)
    try:
        br.place(rows[0]['instrument'], 1)
    except IBB.BrokerError:
        return not ib._fills                  # заявка не подана вовсе
    return False


@ainv('дробная доля фонда не усекается', needs=lambda b: b == 'normal')
def _a5(beh):
    br, ib, rows = _adapter(beh)
    # Фонд ищется ПО ИМЕНИ, а не по sec_type: у IBKR фонды — 'STK', и после исправления
    # фикстур под правду биржи (№14) прежний поиск 'ETF' не находил ничего — утверждение
    # тихо стало пустым, и мутацию усечения дробей никто не ловил.
    etf = next((r['instrument'] for r in rows if r['instrument'] in ('CSPX', 'CBU0')), None)
    if etf is None:
        return False
    r = br.place(etf, 100.5)
    return abs(r['filled'] - 100.5) < 1e-9


@ainv('отчёт, приходящий только по запросу, не теряется',
      needs=lambda b: b in ('late_fills', 'late_cancelled'))
def _a8(beh):
    """Исход заявки обязан определяться БАРЬЕРОМ выгрузки, а не тем, что успело разнестись
    за фиксированную паузу. Иначе состоявшаяся сделка выглядит несостоявшейся, книга у
    брокера меняется, а контур идёт восстанавливать то, что уже исполнено."""
    br, ib, rows = _adapter(beh)
    r = br.place(rows[0]['instrument'], 2)
    return isinstance(r, dict) and abs(r['filled'] - 2) < 1e-9


@ainv('исполнение ПОСЛЕ конца выгрузки не выдаётся за неисполнение',
      needs=lambda b: b == 'fill_after_end')
def _a10(beh):
    """Предел барьера, названный прямо. execDetailsEnd завершает КОНКРЕТНЫЙ ответ и не
    обещает, что позднее исполнение уже невозможно. Если барьер прошёл, а отчёта нет,
    единственный честный исход — ОТКАЗ с неизвестным статусом: заявить «исполнено ноль»
    значит выдать незнание за факт, а книга у брокера при этом уже изменилась.
    """
    import ib_broker as IBB
    br, ib, rows = _adapter(beh)
    try:
        br.place(rows[0]['instrument'], 2)
        return False
    except IBB.BrokerError as ex:
        return 'НЕИЗВЕСТЕН' in str(ex)


@ainv('устойчиво устаревшие позиции не распознаются, но и не скрываются',
      needs=lambda b: b == 'stale_twice')
def _a11(beh):
    """ЧЕСТНАЯ ГРАНИЦА. Два совпавших снимка могут быть двумя одинаково устаревшими, и на
    уровне адаптера это неотличимо — никакое число согласных чтений этого не решает.
    Проверяется поэтому не распознавание, а отсутствие ЛОЖНОГО обещания: снимок отдаётся
    как есть, без пометки «устоялось, значит верно». Гарантию даёт следующая сессия —
    см. сценарий «позднее исполнение после сессии» в проверках запуска.
    """
    br, ib, rows = _adapter(beh, positions={900001: 5})
    ib._pos[900001] = 9                      # истина изменилась, выгрузка ещё лжёт
    got = br.net_positions()
    return got.get(rows[0]['instrument']) == 5


@ainv('чужие заявки: другой счёт невидим, чужой инструмент НЕ снимается',
      needs=lambda b: b == 'foreign_orders')
def _a12(beh):
    """Авария ADD-FUT не вправе отменить ручную защитную заявку или заявку соседнего
    managed account: чужой счёт не виден вовсе, чужой инструмент нашего счёта попадает в
    неснятые и останавливает сессию по имени, а не снимается молча."""
    import ib_stub
    import daily as DLm
    br, ib, rows = _adapter('normal')
    from ib_insync import MarketOrder
    # заявка ЧУЖОГО СЧЁТА
    o1 = MarketOrder('BUY', 1); o1.orderId = 501; o1.account = 'DU999999'
    t1 = ib_stub._Trade(ib._contract_of(int(rows[0]['con_id'])), o1)
    # заявка НАШЕГО счёта по ЧУЖОМУ инструменту (ручная защитная)
    o2 = MarketOrder('SELL', 1); o2.orderId = 502; o2.account = ib._acct
    alien = ib._contract_of(777777)
    t2 = ib_stub._Trade(alien, o2)
    # заявка ЧУЖОЙ СТРАТЕГИИ (метка не наша) на НАШЕМ инструменте — снимать нельзя (№3)
    o3 = MarketOrder('SELL', 1); o3.orderId = 503; o3.account = ib._acct
    o3.orderRef = 'ДРУГАЯ'
    t3 = ib_stub._Trade(ib._contract_of(int(rows[0]['con_id'])), o3)
    ib._trades += [t1, t2, t3]
    vis = br.open_orders()
    # Ключи заявок — 'clientId:orderId' (восемнадцатый круг, №2): голый номер схлопывал
    # заявки разных клиентов.
    if '0:501' in vis or '0:502' not in vis or '0:503' not in vis:
        return False
    stuck = DLm._cancel_all(br)
    still = {t.order.orderId for t in ib.openTrades()}
    return sorted(stuck) == ['0:502', '0:503'] and {502, 503} <= still


@ainv('NLV читается свежим request/end-барьером, а не кэшем подписки',
      needs=lambda b: b == 'stale_nlv')
def _a16(beh):
    """Девятнадцатый круг, №4: accountSummary() после первого вызова отдаёт кэш подписки
    (IB обновляет её раз в ~3 мин) — доторговый NLV/MaintMarginReq выдавался за свежий.
    Свежесть даёт только явный reqAccountSummary() (новый reqId + accountSummaryEnd)."""
    br, ib, rows = _adapter(beh)
    return abs(br.net_liquidation() - 1_000_000.0) < 1e-6


@ainv('цена исполнения — из отчётов о сделках, а не из статуса',
      needs=lambda b: b == 'no_avg_price')
def _a17(beh):
    """Девятнадцатый круг, №16: orderStatus.avgFillPrice обновляется отдельным сообщением
    и exec-барьером не гарантирован; нулевая статусная цена давала пустой px_fill, строка
    выпадала из §7 — систематически на стороне, где статус отстаёт."""
    br, ib, rows = _adapter(beh)
    r = br.place(rows[0]['instrument'], 2)
    return isinstance(r, dict) and r.get('px_fill') == 100.0


@ainv('комиссия без пришедшего отчёта — пусто, а не ноль',
      needs=lambda b: b == 'no_commission_report')
def _a18(beh):
    """Девятнадцатый круг, №16: exec-барьер — не барьер commissionReport; отчёт без execId
    ещё не пришёл, и ноль вместо него занижал бы измеренные издержки §7."""
    br, ib, rows = _adapter(beh)
    r = br.place(rows[0]['instrument'], 2)
    return isinstance(r, dict) and r.get('commission') == ''


@ainv('линия инструмента сверяется с ОЖИДАНИЯМИ, а не с копией ответа биржи',
      needs=lambda b: b == 'normal')
def _a19(beh):
    """Девятнадцатый круг, №6 (+ пара к 18-му, №7): чужая площадка/тикер фонда и чужая
    поставка фьючерса обязаны отвергаться против ОЖИДАЕМЫХ констант — прежняя сверка шла
    по кругу против полей, скопированных из того же ответа."""
    import first_connect as FC

    class _Wrong:
        primaryExchange = 'AEB'; symbol = 'CSPX'; currency = 'USD'

    class _Right:
        primaryExchange = 'LSEETF'; symbol = 'CSPX'; currency = 'USD'

    bad = FC.check_etf_line(_Wrong, 'CSPX', 'CSPX', 'LSEETF', 'USD',
                            'IE00B5BMR087', 'IE00B5BMR087')
    ok = FC.check_etf_line(_Right, 'CSPX', 'CSPX', 'LSEETF', 'USD',
                           'IE00B5BMR087', 'IE00B5BMR087')

    class _WrongF:
        lastTradeDateOrContractMonth = '20261218'; symbol = 'ES'

    class _RightF:
        lastTradeDateOrContractMonth = '20260918'; symbol = 'ES'

    badf = FC.check_future_identity(_WrongF, 'ES', 'U26')
    okf = FC.check_future_identity(_RightF, 'ES', 'U26')
    return bool(bad) and ok == [] and bool(badf) and okf == []


@ainv('NaN-запас О-3-Е — отказ, а не «выше порога»',
      needs=lambda b: b == 'nan_cushion')
def _a15(beh):
    """Семнадцатый круг, №7: NaN/maint давал cushion=NaN, «NaN < 1,40» ложно — аварийное
    сокращение молча отключалось при фактическом запасе 1,20."""
    import ib_broker as IBB
    br, ib, rows = _adapter(beh)
    try:
        br.margin_cushion()
        return False
    except IBB.BrokerError:
        return True


@ainv('неизвестность компенсации не объявляется восстановлением',
      needs=lambda b: b == 'normal')
def _a14(beh):
    """Семнадцатый круг, №4: оборванная компенсирующая заявка могла исполниться позже —
    совпавший снимок после неё ничего не доказывает, restore_to обязан вернуть отказ."""
    import daily as DLm

    class _B:
        def open_orders(self):
            return []

        def cancel_order(self, oid):
            return True

        def net_positions(self):
            return {'ZNU26': 5}

        def place(self, inst, qty, px_order=None):
            raise RuntimeError('компенсация оборвана')
    b = DLm.Book(d_fix=8.0, n_e=0, n_b=10, unit_is_mes=True, prev_st_eq=False,
                 prev_st_bd=True, ser_a=None, ser_b='U26', es_held=0)
    ok, have, stuck = DLm.restore_to(_B(), b, 'F')
    return (not ok) and any('исход неизвестен' in str(s) for s in stuck)


@ainv('сбой запроса заявок — отказ, а не пустой список',
      needs=lambda b: b == 'orders_req_fails')
def _a13(beh):
    """Шестнадцатый круг, №2: соединение живо, а reqAllOpenOrders падает; проглоченная
    ошибка оставляла слепой локальный кэш — сверка разрешала дубль живой заявки."""
    import ib_broker as IBB
    br, ib, rows = _adapter(beh)
    try:
        br.open_orders()
        return False
    except IBB.BrokerError:
        return True


@ainv('фонд без ISIN в реестре отвергается ДО торговли',
      needs=lambda b: b == 'normal')
def _a12(beh):
    """Пятнадцатый круг, №6: пустой ISIN у строки STK — повреждённый или усечённый реестр,
    а не «проверка не нужна»: тикер+валюта+площадка не различают листинговые линии одного
    фонда. Фьючерсам ISIN не положен — пустое поле для FUT штатно."""
    import contracts as CT

    class _C:
        conId = 999001
    stk = CT.verify_isin(None, _C(), {'sec_type': 'STK', 'symbol': 'CSPX', 'isin': ''})
    fut = CT.verify_isin(None, _C(), {'sec_type': 'FUT', 'symbol': 'ES', 'isin': ''})
    return bool(stk) and 'ISIN' in stk[0] and fut == []


@ainv('позиция ЧУЖОГО счёта не попадает в книгу',
      needs=lambda b: b == 'other_account')
def _a9(beh):
    """reqPositions отдаёт позиции ВСЕХ доступных счетов. Прежде поле счёта игнорировалось,
    и сверялся чужой счёт, а торговался свой: на целевом ноль ES, на соседнем 26, сверка
    «проходит», книга не строится вовсе."""
    br, ib, rows = _adapter(beh)
    return not br.net_positions()


@ainv('отказ брокера не меняет позицию', needs=lambda b: b == 'reject')
def _a6(beh):
    import ib_broker as IBB
    br, ib, rows = _adapter(beh)
    try:
        br.place(rows[0]['instrument'], 1)
        return False
    except IBB.BrokerError:
        return not ib._pos


@ainv('разрыв связи даёт исключение и ровно одну заявку',
      needs=lambda b: b == 'disconnect')
def _a7(beh):
    import ib_broker as IBB
    br, ib, rows = _adapter(beh)
    try:
        br.place(rows[0]['instrument'], 1)
        return False
    except IBB.BrokerError:
        return len(ib._trades) == 1


@ainv('предпросмотр перехода проходит на законном плане', needs=lambda b: b == 'normal')
def _a8(beh):
    """СОРОК ЧЕТВЁРТЫЙ КРУГ, саморецензия: УСПЕШНЫЙ путь preview не наблюдался НИЧЕМ.

    Мутации адаптера ловит run_adapter(), а preview() в этом наборе не исполнялся вовсе:
    я завёл мутацию «TIF заявки предпросмотра не задаётся» и она честно осталась
    НЕПОЙМАННОЙ — выпуск отказал. Зонд, доказавший смену поведения, проверял не ту сетку:
    покрытие preview жило в самопроверке ib_broker, а адаптерные мутации судятся не ею.

    Утверждение требует, чтобы законный план получал «можно». Заявка предпросмотра без
    явного TIF получает от шлюза ПУСТОЙ СПИСОК (пресет счёта переопределяет TIF,
    предупреждение 10349) — стаб это воспроизводит, и preview падает в «отложить».
    Значит удаление строки _o.tif делает это утверждение ложным, а мутацию — видимой.
    """
    br, ib, rows = _adapter(beh, positions={900004: 100.0})
    ib.quote_px = 100.0
    return br.preview([('CSPX', 1.0)]) is True


@ainv('предпросмотр ОТКАЗЫВАЕТ, когда запаса О-3-Е не остаётся',
      needs=lambda b: b == 'normal')
def _a9(beh):
    """СОРОК ЧЕТВЁРТЫЙ КРУГ, угол «от отрицания»: утверждение _a8 требует только «можно».

    Опыт показал дыру: предпросмотр, слепо возвращающий True, не роняет НИ ОДНОГО
    утверждения — а ведь весь его смысл в отказе. Пара к _a8: тот же плановый путь, но
    маржа стаба оставляет запас 1,20 против порога О-3-Е 1,40, и ответом обязан быть отказ.
    Вместе они запирают обе стороны: разрешать законное и не разрешать незаконное.
    """
    br, ib, rows = _adapter(beh, positions={900004: 100.0})
    ib.quote_px = 100.0
    ib.whatif = 'тесно'
    return br.preview([('CSPX', 1.0)]) is False


@ainv('предпросмотр спрашивает про ТУ ЖЕ форму заявки, что уходит в рынок',
      needs=lambda b: b == 'normal')
def _a10(beh):
    """СОРОК ЧЕТВЁРТЫЙ КРУГ: правка «предпросмотр зеркалит place()» была НЕ НАБЛЮДАЕМА.

    Опыт: подмена формы на DAY+outsideRth=False не роняла ничего, то есть доказательство
    маржи могло относиться к заявке, которая в рынок не уйдёт. Источник истины ОДИН —
    сама заявка place(): форма читается у неё, а не задаётся константой в стенде, иначе
    правило пришлось бы держать в двух местах и они разъехались бы.
    """
    br, ib, rows = _adapter(beh, positions={900004: 100.0})
    ib.quote_px = 100.0
    br.place(rows[0]['instrument'], 1)
    real = ib._trades[-1].order
    br.preview([('CSPX', 1.0)])
    prev = getattr(ib, '_last_whatif_order', None)
    if prev is None:
        return False
    return (str(getattr(prev, 'tif', '')) == str(getattr(real, 'tif', '')) and
            bool(getattr(prev, 'outsideRth', False)) == bool(getattr(real, 'outsideRth', False)))


@ainv('предпросмотр привязывает заявку к пинованному счёту', needs=lambda b: b == 'normal')
def _a11(beh):
    """СОРОК ЧЕТВЁРТЫЙ КРУГ, механическая выборка по семье «защита без наблюдателя».

    Привязка к счёту введена в 37-м круге (№4): без account ответ whatIf относится к
    ПРОИЗВОЛЬНОМУ счёту под тем же логином, и предпросмотр доказывал бы маржу не того
    портфеля. Опыт показал, что снятие привязки не роняло ни одного утверждения.
    Проверка НЕ ВАКУУМНА: счёт фикстуры непуст (DU000001), иначе сравнение '' == ''
    проходило бы всегда и защита осталась бы ненаблюдаемой во второй раз.
    """
    br, ib, rows = _adapter(beh, positions={900004: 100.0})
    ib.quote_px = 100.0
    br.preview([('CSPX', 1.0)])
    prev = getattr(ib, '_last_whatif_order', None)
    if prev is None or not br.account:
        return False
    return str(getattr(prev, 'account', '')) == str(br.account)


@ainv('предпросмотр округляет дробное количество фьючерса', needs=lambda b: b == 'normal')
def _a12(beh):
    """СОРОК ЧЕТВЁРТЫЙ КРУГ, та же выборка. Округление введено в 38-м круге (№5): план
    приходит сырым отношением и даёт, например, 109,295 MES, а дробная what-if заявка на
    фьючерс у IBKR недопустима — ответ пуст, предпросмотр отвечает «отложить», и ЗАКОННЫЙ
    переход после трёх отказов уходит в ABORT.

    Прежние утверждения о preview брали ФОНД (CSPX), которому дробность законна, поэтому
    фьючерсная ветка не исполнялась вовсе и снятие округления не ловилось ничем.
    """
    br, ib, rows = _adapter(beh, positions={900004: 100.0})
    ib.quote_px = 100.0
    ok = br.preview([('MESU26', 109.295)])
    prev = getattr(ib, '_last_whatif_order', None)
    if prev is None:
        return False
    q = float(getattr(prev, 'totalQuantity', 0) or 0)
    return ok is True and q > 0 and abs(q - round(q)) < 1e-9


@ainv('аварийный признак не смягчает решение предпросмотра', needs=lambda b: b == 'normal')
def _a13(beh):
    """СОРОК ЧЕТВЁРТЫЙ КРУГ, угол «от отрицания»: ветка emergency не исполнялась в наборе
    адаптера НИ РАЗУ (счётчик вызовов с emergency=True дал 0), и мутация «в аварийном
    режиме разрешается всё» не ловилась ничем.

    Код заявляет правило прямо: флаг передаётся исполнителем, чтобы назвать НАМЕРЕНИЕ, а
    «смысл ветки не зависит от него». Утверждение и проверяет ровно это: план, требующий
    маржи при недостаточном запасе, обязан быть отвергнут ОДИНАКОВО с флагом и без него.
    Иначе аварийный признак становится обходом норматива О-3-Е — то есть слово вызывающего
    начинает заменять доказательство.
    """
    br, ib, rows = _adapter(beh, positions={900004: 100.0})
    ib.quote_px = 100.0
    ib.whatif = 'тесно'
    return (br.preview([('CSPX', 1.0)]) is False
            and br.preview([('CSPX', 1.0)], emergency=True) is False)


@ainv('плечо ноги Б считается по d_fix книги, а не серединой полосы',
      needs=lambda b: b == 'normal')
def _a14(beh):
    """СОРОК ЧЕТВЁРТЫЙ КРУГ, механическая выборка: у gross() НЕТ НИ ОДНОЙ МУТАЦИИ во всём
    мутаторе, и набор адаптера его не вызывает вовсе. А это ворота капа плеча 2,00 — самое
    дорогое число стратегии: подмена модельной единицы ноги Б серединой полосы занижает
    плечо примерно на треть (дефект 37-го круга, №3) и пропускает книгу больше капа.

    Дискриминатор взят тот же, что в самопроверке адаптера, и он не требует пересчёта всей
    формулы: модельная единица ноги Б ПРОПОРЦИОНАЛЬНА d_fix, а середина полосы от него не
    зависит вовсе. Значит удвоение d_fix обязано удвоить плечо; у подменённой реализации
    отношение равно 1. Плюс отказ без d_fix при непустой ноге Б: молчаливая подстановка
    середины — это и есть занижение.
    """
    import datetime as _dtg
    import daily as _DLg
    import feed as _FDg
    br, ib, rows = _adapter(beh, positions={900003: 10.0})
    ib.quote_px = 100.0
    _tz = _FDg.exchange_today()
    _pz = _FDg.prev_session(_tz, holidays=_DLg.holidays_for(_tz.year)).date()
    ib.set_bars({990001: [(str(_pz - _dtg.timedelta(days=1)), 46.9), (str(_pz), 46.84)]})
    try:
        g6, g12 = float(br.gross(6.0)), float(br.gross(12.0))
    except Exception:
        return False
    if not g6 or abs(g12 / g6 - 2.0) > 1e-9:
        return False
    try:
        br.gross(None)
    except Exception:
        return True
    return False        # без d_fix при непустой ноге Б обязан быть отказ


@ainv('модельная единица ноги А берётся у САМОГО SPY, а не у ES/10',
      needs=lambda b: b == 'normal')
def _a15(beh):
    """СОРОК ЧЕТВЁРТЫЙ КРУГ, №1 (P0). Норматив: единица ноги А = ES_MULT x SPY.
    feed.build_market так и считает, держа ES/10 ТОЛЬКО сверкой базиса, а unit_ref строил
    полосу из ES — и gross() брал её середину как ЦЕНУ. Значит ворота капа на закрытии
    считали ногу А фьючерсом, а решение дня — индексом; базис сдвигает их, а между
    CLOSE_CAP=2,00 и INTRA_CAP=2,02 запаса на 2% нет вовсе.

    ПРОВЕРКА НЕ КРУГОВАЯ (замечание «ложные доказательства», №4): в прежнем стенде _a31u
    «независимый SPY» выводился из того же ES делением на 10, то есть правило 500xSPY не
    проверялось вовсе. Здесь SPY задан в фикстуре ОТДЕЛЬНЫМ рядом (780,0) и намеренно НЕ
    равен ES/10 (775,0): реализация по ES даёт 387 500, по нормативу — 390 000.
    """
    import os as _osq
    import datetime as _dtq
    import daily as _DLq
    import feed as _FDq
    import sim_v13 as _Sq
    br, ib, rows = _adapter(beh, positions={900001: 1.0})
    _keep = _osq.environ.get('ADDFUT_REGISTRY')
    _osq.environ['ADDFUT_REGISTRY'] = ib._fixture_reg
    try:
        _tz = _FDq.exchange_today()
        _pz = _FDq.prev_session(_tz, holidays=_DLq.holidays_for(_tz.year)).date()
        _y = str(_pz - _dtq.timedelta(days=1))
        ib.set_bars({900001: [(_y, 7740.0), (str(_pz), 7750.0)],
                     900010: [(_y, 778.0), (str(_pz), 780.0)]})
        band = br.unit_ref('ESU26', 'FUT', at_close=True)
    except Exception:
        return False
    finally:
        if _keep is None:
            _osq.environ.pop('ADDFUT_REGISTRY', None)
        else:
            _osq.environ['ADDFUT_REGISTRY'] = _keep
    if not band:
        return False
    mid = (float(band[0]) + float(band[1])) / 2.0
    return abs(mid - _Sq.ES_MULT * 780.0) < 1.0


def _margins_fixture():
    """Изолированный замер маржи для стендов (правило 5). _live_margins требует ПОЛНОГО
    покрытия FUT-серий реестра, привязки к счёту и con_ids — фикстура несёт всё это."""
    import json as _js
    import tempfile as _tf
    import os as _os
    import datetime as _dt
    _ser = {'ESU26': 35000.0, 'MESU26': 3500.0, 'ZNU26': 2200.0,
            'ESZ26': 35200.0, 'MESZ26': 3520.0, 'ZNZ26': 2210.0}
    _cid = {'ESU26': '900001', 'MESU26': '900002', 'ZNU26': '900003',
            'ESZ26': '900006', 'MESZ26': '900007', 'ZNZ26': '900008'}
    raw = {'_meta': {'date': str(_dt.date.today()), 'account': 'DU000001',
                     'series': sorted(_ser), 'con_ids': _cid}}
    for k, v in _ser.items():
        raw[k] = {'init': v, 'maint': v * 0.72}
    d = _tf.mkdtemp(prefix='addfut-mrg-')
    fp = _os.path.join(d, 'margins_live.json')
    with open(fp, 'w', encoding='utf-8') as f:
        _js.dump(raw, f)
    return fp


def _with_measure(br, ib, fn):
    """Выполнить fn при ИЗОЛИРОВАННОМ замере, реестре и пине (правило 5)."""
    import os as _os
    keep = {k: _os.environ.get(k) for k in
            ('ADDFUT_MARGINS', 'ADDFUT_REGISTRY', 'ADDFUT_ACCOUNT')}
    _os.environ['ADDFUT_MARGINS'] = _margins_fixture()
    _os.environ['ADDFUT_REGISTRY'] = ib._fixture_reg
    _os.environ['ADDFUT_ACCOUNT'] = 'DU000001'
    try:
        return fn()
    finally:
        for k, v in keep.items():
            if v is None:
                _os.environ.pop(k, None)
            else:
                _os.environ[k] = v


@ainv('разгрузка маржи подтверждается ИЗМЕРЕННОЙ маржой цели, а не приращениями',
      needs=lambda b: b == 'margin_call')
def _a16(beh):
    """СОРОК ЧЕТВЁРТЫЙ КРУГ, №2 (P0). Ветка объявляла доказательством разгрузки то, что все
    приращения whatIf неположительны. Это ложно: каждое приращение считается против ещё не
    проданной исходной книги, и отрицательным оно бывает от неттинга с активами, которые
    переход затем продаст. Проверяем ОБЕ стороны при запасе ниже порога (margin_call):
    дешёвая по замеру цель — разрешена (дверь аварийного выхода открыта), дорогая —
    отвергнута, хотя приращения по-прежнему отрицательны.
    """
    br, ib, rows = _adapter(beh, positions={900004: 100.0})
    ib.quote_px = 100.0
    ib.whatif = 'освобождает'

    def _run():
        cheap = br.preview([('ZNU26', 1)])
        dear = br.preview([('ESU26', 100)])
        return cheap is True and dear is not True

    return _with_measure(br, ib, _run)


ADAPTER_CASES = ('normal', 'margin_call', 'partial', 'reject', 'disconnect', 'cancelled_but_filled',
                 'stale_positions', 'foreign_fill', 'wrong_contract', 'late_fills',
                 'late_cancelled', 'other_account', 'fill_after_end',
                 'stale_twice', 'foreign_orders', 'orders_req_fails', 'nan_cushion',
                 'stale_nlv', 'no_avg_price', 'no_commission_report',
                 # ТРИДЦАТЬ ПЕРВЫЙ КРУГ: два сценария живого адаптера, которых не было.
                 # coherent_series_swap — согласованная подмена серии (реестр и биржа
                 # подтверждают друг друга, лжёт только ИМЯ): №8. unit_ref — ЖИВАЯ полоса
                 # долларовой единицы, которую все переходные стенды подменяли своей
                 # таблицей и потому не исполняли ни разу: №6.
                 'coherent_series_swap', 'unit_ref',
                 # ТРИДЦАТЬ ТРЕТИЙ КРУГ: №13 — inf и отрицательное требование маржи (была
                 # только NaN-ветка), №12 — УСПЕШНЫЙ путь признака реального времени.
                 'inf_maint', 'neg_maint', 'realtime_md',
                 # №4: согласованная подмена листинговой линии фонда — ловится только
                 # независимым ожиданием, а не сверкой ответа биржи со строкой реестра.
                 # №11 (35-й круг): согласованная подмена КЛАССА фонда.
                 'etf_line_swap', 'etf_class_swap')


def run_adapter():
    behs = ADAPTER_CASES
    cov, bad = {}, {}
    for b in behs:
        for name, fn, needs in ADAPTER:
            if needs is not None and not needs(b):
                continue
            cov[name] = cov.get(name, 0) + 1
            try:
                ok = fn(b)
            except Exception as ex:
                ok = False; name = f'{name} [исключение: {type(ex).__name__}: {ex}]'
            if not ok:
                bad.setdefault(name, []).append(b)
    return cov, bad


# ---------------------------------------------------------------- намерение сессии
# СБОЙ МЕЖДУ СДЕЛКОЙ И ЗАПИСЬЮ СОСТОЯНИЯ. Связка «заявка у брокера + состояние на диске»
# не атомарна принципиально. Намерение, записанное ДО первой заявки, делает три исхода
# различимыми; здесь все три перебираются, и утверждение с нулевым покрытием — провал.
INTENT = []


def iinv(name, needs=None):
    def deco(fn):
        INTENT.append((name, fn, needs)); return fn
    return deco


def _intent_full(kind):
    """ПОЛНЫЙ прогон сессии после обрыва, а не только разбор намерения.

    Седьмая рецензия указала: _intent_case вызывал только _resume_intent и проверял, что
    книга принята, — то есть САМАЯ ОПАСНАЯ часть последовательности отсутствовала по
    построению. После принятия намерения run_session продолжал считать решение по ТОМУ ЖЕ
    рынку, и в день ролла уже перенесённая серия проходила target_tag второй раз: Z26 -> H27,
    уход на квартал вперёд. Здесь прогоняется вся сессия целиком.
    """
    import tempfile
    import state as ST
    from fake_broker import FakeBroker

    roll_day = pd.Timestamp('2026-08-26')
    before = DL.Book(d_fix=8.0, n_e=100, n_b=50, unit_is_mes=True, prev_st_eq=True,
                     prev_st_bd=True, ser_a='U26', ser_b='U26', es_held=10,
                     last_session='2026-08-25', close_provisional=False)
    m = DL.Market(date=roll_day, px_eq_prev=600.0, dref_prev=8.0, dref_today=8.0,
                  px_eq_today=600.0, roll_today=True, st_eq=True, st_bd=True)
    dec0 = DL.step(before, m, 10_000_000.0)
    after = dec0.book_after
    orders = DL.orders_from_books(before, after)

    with tempfile.TemporaryDirectory() as tmp:
        import os
        bp = Path(tmp) / 'book.json'
        ST.save(bp, before, 'F', 7)
        ST.save_intent(bp, 'F', 8, before, after, orders, session_date='2026-08-26')
        pos = (DL.physical_book(after) if kind in ('ролл исполнен целиком',
                                                   'состояние уже дописано')
               else DL.physical_book(before))
        prices = {k: 600.0 if 'ES' in k else 112.0
                  for k in set(pos) | set(DL.physical_book(after)) | set(DL.physical_book(before))}
        br = FakeBroker(prices=prices, positions=dict(pos)); br.nlv = 10_000_000.0
        # ЕСЛИ КНИГА У БРОКЕРА НАМЕЧЕННАЯ — ЗНАЧИТ ЗАЯВКИ ИСПОЛНЯЛИСЬ (двадцать четвёртый
        # круг, №5): барьер исполнений теперь требует отчётов и в этой ветке, а фикстура
        # «сделка прошла» без единого отчёта описывает состояние, которого не бывает.
        if kind in ('ролл исполнен целиком', 'состояние уже дописано'):
            br.todays_executions = lambda: [7001, 7002]
        if kind == 'исполнение в пути':
            # Позиции ещё исходные, но БАРЬЕР уже несёт сегодняшнее исполнение нашей метки
            # (семнадцатый круг, №3): снимок один не доказывает «заявок не было».
            br.todays_executions = lambda: [4242]
        if kind == 'состояние уже дописано':
            # Процесс умер МЕЖДУ ST.save() и clear_intent(): книга уже записана прошлым
            # запуском, намерение осталось — повторная запись удвоила бы номер сессии.
            import dataclasses as _dc
            ST.save(bp, _dc.replace(after, last_session='2026-08-26',
                                    close_provisional=True), 'F', 8)
        keep = os.environ.get('ADDFUT_BOOK_PATH'), os.environ.get('ADDFUT_LOCK_DIR')
        os.environ['ADDFUT_BOOK_PATH'] = str(bp); os.environ['ADDFUT_LOCK_DIR'] = tmp
        (Path(tmp) / 'route.txt').write_text('F', encoding='utf-8')   # №1
        out = dict(kind=kind, raised=False, error='', dec=None, orders=None)
        # ОРИЕНТИРЫ И ЖУРНАЛ — КАК В БОЮ (двадцать второй круг, №17): живой вход обязан
        # нести оба, и фикстура без них проверяла путь, которого больше не существует.
        import journal as _J7i
        _jp = Path(tmp) / 'journal-F.csv'
        _J7i.append(_jp, dict(date='2026-08-25', leg='', instrument='ИТОГ', qty=0,
                              px_order='-', px_fill='', commission='', reason='',
                              nav='10000000', leverage='1.0', roll_spread_near='',
                              roll_spread_far='', note='итог сессии 7: строк 0'))
        try:
            dec, ords, diff = DL.run_session(br, m, dirpath=tmp, route='F',
                                             capital=10_000_000.0, closing_nav=10_000_000.0,
                                             book_path=str(bp), ref_prices=dict(prices),
                                             journal_path=str(_jp))
            out['dec'] = dec; out['orders'] = ords
        except Exception as ex:
            out['raised'] = True; out['error'] = f'{type(ex).__name__}: {ex}'
        finally:
            for k, v in (('ADDFUT_BOOK_PATH', keep[0]), ('ADDFUT_LOCK_DIR', keep[1])):
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
        sv, sess_after, _ = ST.load(bp, DL.Book)
        out['saved'] = sv
        out['sess'] = sess_after
        out['intent_left'] = bool(ST.load_intent(bp))
        out['before'] = before; out['after'] = after
        return out


@iinv('после принятого ролла серия НЕ уезжает второй раз',
      needs=lambda r: r.get('kind') == 'ролл исполнен целиком')
def _t4(r):
    """Ключевое: перенос U26->Z26 состоялся, состояние не записалось. Повторный запуск
    обязан ЗАВЕРШИТЬ ту сессию, а не роллить Z26->H27 в том же прогоне."""
    sv = r['saved']
    return (not r['raised'] and not r['orders']
            and sv.ser_a == r['after'].ser_a and sv.ser_b == r['after'].ser_b
            and sv.ser_a == 'Z26')


@iinv('после НЕисполненного ролла сессия считается обычным порядком',
      needs=lambda r: r.get('kind') == 'ролл не начинался')
def _t5(r):
    """Обратная сторона: если заявки не проходили, намерение снимается и сессия идёт как
    обычно — иначе перенос не состоялся бы вовсе и книга дожила бы до поставки."""
    return not r['raised'] and bool(r['orders'])


def _intent_case(kind):
    """Сценарий обрыва: что осталось на диске и что у брокера. Возвращает результат разбора."""
    import tempfile
    import state as ST
    from fake_broker import FakeBroker

    before = DL.Book(d_fix=8.0, n_e=100, n_b=50, unit_is_mes=True, prev_st_eq=True,
                     prev_st_bd=True, ser_a='U26', ser_b='U26', es_held=10,
                     last_session='2026-08-10')
    after = DL.Book(d_fix=8.0, n_e=260, n_b=101, unit_is_mes=True, prev_st_eq=True,
                    prev_st_bd=True, ser_a='U26', ser_b='U26', es_held=26,
                    last_session='2026-08-10')
    pos = {'не начиналось': DL.physical_book(before),
           'прошло целиком': DL.physical_book(after),
           'промежуточное': {**DL.physical_book(before), 'ZNU26': 77}}[kind]

    with tempfile.TemporaryDirectory() as tmp:
        bp = Path(tmp) / 'book.json'
        ST.save(bp, before, 'F', 3)
        ST.save_intent(bp, 'F', 4, before, after, [('ESU26', 16), ('ZNU26', 51)])
        br = FakeBroker(prices={k: 100.0 for k in set(pos) | set(DL.physical_book(after))},
                        positions=dict(pos))
        if kind == 'прошло целиком':          # №5: намеченная книга = заявки исполнялись
            br.todays_executions = lambda: [7003]
        out = dict(kind=kind, raised=False, book=None, intent_left=None, saved=None)
        try:
            out['book'], out['done_date'] = DL._resume_intent(
                ST, bp, DL.Book, 'F', before, 3, br, False)
        except Exception as ex:
            out['raised'] = True
            out['error'] = str(ex)
        out['intent_left'] = ST.load_intent(bp) is not None
        out['saved'], _, _ = ST.load(bp, DL.Book)
        out['before'], out['after'] = before, after
        return out


@iinv('заявки не проходили: намерение снимается, книга прежняя',
      needs=lambda r: r['kind'] == 'не начиналось')
def _t1(r):
    return (not r['raised'] and not r['intent_left']
            and r['book'].n_e == r['before'].n_e and r['book'].n_b == r['before'].n_b
            and r['saved'].n_e == r['before'].n_e)


@iinv('сделка прошла, состояние не успело: намеченная книга ПРИНИМАЕТСЯ',
      needs=lambda r: r['kind'] == 'прошло целиком')
def _t2(r):
    return (not r['raised'] and not r['intent_left']
            and r['book'].n_e == r['after'].n_e and r['book'].n_b == r['after'].n_b
            and r['saved'].n_e == r['after'].n_e and r['saved'].n_b == r['after'].n_b)


@iinv('промежуточная книга НЕ выдаётся ни за исходную, ни за намеченную',
      needs=lambda r: r['kind'] == 'промежуточное')
def _t3(r):
    # Намерение остаётся на диске: разбор ручной, и следующий запуск обязан снова упереться.
    return r['raised'] and r['intent_left'] and r['saved'].n_e == r['before'].n_e


@iinv('исполнение в пути: снимок исходных позиций не очищает намерение',
      needs=lambda r: r.get('kind') == 'исполнение в пути')
def _t4(r):
    """Семнадцатый круг, №3: заявка принята, отчёт в барьере, позиция запаздывает —
    очистка намерения разрешила бы новую торговлю поверх позднего исполнения."""
    return (r['raised'] and 'снимок недостоверен' in r['error'] and r['intent_left']
            and r['saved'].n_e == r['before'].n_e)


@iinv('дописанное состояние не дописывается второй раз',
      needs=lambda r: r.get('kind') == 'состояние уже дописано')
def _t5(r):
    """Семнадцатый круг, №3: обрыв между save() и clear_intent() — повторная запись
    удваивала номер сессии и стирала поля замыкания; теперь намерение снимается, книга
    не трогается, день честно объявляется отторгованным."""
    return (r['raised'] and 'не новее' in r['error'] and not r['intent_left']
            and r['sess'] == 8 and r['saved'].n_e == r['after'].n_e)


def _intent_cut(kind):
    """ОБРЫВ ПОСРЕДИ ВНУТРИДНЕВНОГО СРЕЗА О-3-Е (тридцать второй круг, №13).

    Намерение среза пишется, когда книга УЖЕ несёт сегодняшнюю дату (день отторгован).
    Ярлык «состояние уже дописано прошлым процессом» срабатывал по одной этой дате: он
    архивировал намерение, не сравнив книгу ни с целью, ни с брокером. Брокер при этом
    сокращён, активная книга на диске — досрезная, и разрыва не видит никто.

    Здесь заявки среза ПРОШЛИ, а состояние записаться не успело. Правильный исход — тот
    же, что у обычной сессии: намеченная (сокращённая) книга принимается. Ярлык по дате
    вернул бы досрезную книгу и снял намерение — ровно ложное «всё в порядке».
    """
    import os
    import tempfile
    import state as ST
    from fake_broker import FakeBroker

    out = dict(kind=kind, raised=False, error='', book=None, intent_left=None)
    tmp = tempfile.mkdtemp(prefix='addfut-cut-')
    keep = (os.environ.get('ADDFUT_BOOK_PATH'), os.environ.get('ADDFUT_LOCK_DIR'))
    try:
        os.environ['ADDFUT_BOOK_PATH'] = str(Path(tmp) / 'book-E.json')
        os.environ['ADDFUT_LOCK_DIR'] = tmp
        (Path(tmp) / 'route.txt').write_text('E', encoding='utf-8')
        bp = Path(tmp) / 'book-E.json'
        day = '2026-08-12'
        before = DL.BookE(n_eq=1195, n_bd=6538, prev_st_eq=True, prev_st_bd=True,
                          last_session=day, close_provisional=True, prev_close_lev=1.99)
        after = DL.replace(before, n_eq=598, n_bd=3267)
        ST.save(bp, before, 'E', 3)
        ST.save_intent(bp, 'E', 3, before, after,
                       [('CSPX', -597), ('CBU0', -3271)], session_date=day)
        br = FakeBroker(prices={'CSPX': 834.66, 'CBU0': 152.94},
                        positions={'CSPX': 598.0, 'CBU0': 3267.0})
        br.nlv = 1_000_000.0
        br.todays_executions = lambda: [9001, 9002]   # заявки среза ИСПОЛНЯЛИСЬ
        book2, done = DL._resume_intent(ST, bp, DL.BookE, 'E', before, 3, br, False)
        out['book'] = book2
        out['done'] = done
        out['intent_left'] = ST.load_intent(bp) is not None
    except Exception as ex:
        out['raised'] = True; out['error'] = f'{type(ex).__name__}: {ex}'
    finally:
        for k, v in zip(('ADDFUT_BOOK_PATH', 'ADDFUT_LOCK_DIR'), keep):
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    return out


@iinv('обрыв посреди внутридневного среза не выдаётся за завершённую сессию',
      needs=lambda r: r['kind'] == 'обрыв посреди внутридневного среза')
def _t31(r):
    """ТРИДЦАТЬ ВТОРОЙ КРУГ, №13. Ярлык по дате принимал ДОсрезную книгу и снимал
    намерение: брокер сокращён, книга старая, тревоги нет. Правильный исход — принять
    намеченную (сокращённую) книгу, как в любом обрыве между сделкой и записью."""
    b = r['book']
    return (not r['raised'] and b is not None
            and b.n_eq == 598 and b.n_bd == 3267)


def run_intent():
    cov, bad = {}, {}
    cases = [('не начиналось', _intent_case), ('прошло целиком', _intent_case),
             ('промежуточное', _intent_case),
             ('ролл исполнен целиком', _intent_full), ('ролл не начинался', _intent_full),
             ('исполнение в пути', _intent_full), ('состояние уже дописано', _intent_full),
             ('обрыв посреди внутридневного среза', _intent_cut)]
    for kind, maker in cases:
        r = maker(kind)
        for name, fn, needs in INTENT:
            if needs is not None and not needs(r):
                continue
            cov[name] = cov.get(name, 0) + 1
            try:
                ok = fn(r)
            except Exception as ex:
                ok = False; name = f'{name} [исключение: {type(ex).__name__}: {ex}]'
            if not ok:
                bad.setdefault(name, []).append(kind)
    return cov, bad


# ---------------------------------------------------------------- сборщик входов
# ЗАМЕЧАНИЕ 28, ВТОРАЯ ПОЛОВИНА. Адаптер под перебор попал, а СБОРЩИК ВХОДОВ — нет, притом
# что четыре из пяти дефектов первой живой сессии сидели именно в нём: единицы цены ноги А,
# даты баров, разбор месячного сигнала, часовые зоны. Проверять его «живьём» нельзя —
# ответ биржи меняется каждый день, — поэтому история подставляется, и утверждения
# описывают то, что сборщик обязан ОТВЕРГНУТЬ.
FEED = []


def finv(name, needs=None):
    def deco(fn):
        FEED.append((name, fn, needs)); return fn
    return deco


FEED_CASES = ('норма', 'бар вчерашний повторён', 'источники разошлись',
              'цена ноги А в индексных пунктах', 'сигнал строкой False',
              'сигнал пустой', 'ряд сигналов устарел', 'ОБА источника устарели одинаково',
              'множитель контракта изменился',
              'пропущена ровно одна сессия',
              'ориентир дальней серии отстал на сессию',
              'серия реестра подменена согласованно',
              # ТРИДЦАТЬ ПЕРВЫЙ КРУГ, №7: сверка digest ЖИВОГО ряда включалась только при
              # существующем непустом сайдкаре — удаление файла её выключало целиком.
              'живой ряд без digest', 'живой ряд с digest', 'живой ряд правлен мимо digest')


def _live_series_case(case):
    """ЖИВОЙ ряд сигналов читается БЕЗ явного path (тридцать первый круг, №7).

    Все прочие стенды сигнала передают path=... — то есть путь «живого» ряда, по которому
    и работает боевой контур (ADDFUT_SIGNALS/~/.addfut), не исполнялся ни одним из них.
    Именно на нём сверка digest была fail-open: сайдкар удалили или обнулили — и ряд можно
    править весь месяц, а один бит ряда включает или выключает целую ногу.
    """
    import hashlib as _hl
    import os
    import tempfile
    import pandas as pd
    import feed as FD
    d = tempfile.mkdtemp(prefix='addfut-livesig-')
    live = Path(d) / 'signals_live.csv'
    live.write_text(',leg_eq,leg_bond\n2026-07-31,1,1\n2026-08-31,1,0\n', encoding='utf-8')
    dp = Path(str(live) + '.sha256')
    if case != 'живой ряд без digest':
        dp.write_text(_hl.sha256(live.read_bytes()).hexdigest() + '\n', encoding='utf-8')
    if case == 'живой ряд правлен мимо digest':
        live.write_text(',leg_eq,leg_bond\n2026-07-31,1,1\n2026-08-31,1,1\n',
                        encoding='utf-8')          # нога Б включена одним битом
    keep = os.environ.get('ADDFUT_SIGNALS')
    os.environ['ADDFUT_SIGNALS'] = str(live)
    try:
        st = FD.signal_state(pd.Timestamp('2026-08-12'))
        return None, '', dict(state=st)
    except Exception as ex:
        return None, f'{type(ex).__name__}: {ex}', {}
    finally:
        os.environ.pop('ADDFUT_SIGNALS', None)
        if keep is not None:
            os.environ['ADDFUT_SIGNALS'] = keep


@finv('живой ряд БЕЗ digest не читается', needs=lambda r: r['case'] == 'живой ряд без digest')
def _f31a(r):
    """Отсутствие доказательства обязано означать отказ, а не разрешение: сайдкар пишет
    signal_update на ОБЕИХ ветках (30-й круг, №9), значит его пропажа — событие."""
    return bool(r['err']) and 'digest' in r['err']


@finv('живой ряд С digest читается', needs=lambda r: r['case'] == 'живой ряд с digest')
def _f31b(r):
    """Пара к предыдущему: стенд, проверяющий только ОТКАЗ, пропускает поломку успешного
    пути — класс дефекта №11 тридцатого круга."""
    return (not r['err']) and r['src'].get('state') is not None


@finv('правка живого ряда мимо digest ловится',
      needs=lambda r: r['case'] == 'живой ряд правлен мимо digest')
def _f31c(r):
    return bool(r['err']) and 'digest' in r['err']


def _feed_run(case):
    """Собрать вход на подставной истории. Возвращает (Market | None, текст отказа)."""
    import tempfile
    import ib_stub
    import feed as FD
    import pandas as pd

    if case.startswith('живой ряд'):
        return _live_series_case(case)

    d0 = pd.Timestamp('2026-08-12')
    es, tnx = 900001, 990001
    rows = list(ib_stub.FIXTURE_ROWS)
    ib = ib_stub.StubIB(rows)
    prev, today = '2026-08-11', '2026-08-12'
    es_bars = [('2026-08-10', 7700.0), (prev, 7747.5)]
    spy_bars = [('2026-08-10', 770.0), (prev, 774.75)]
    tnx_bars = [('2026-08-10', 46.9), (prev, 46.84)]
    if case == 'бар вчерашний повторён':
        es_bars = [('2026-08-03', 7700.0), ('2026-08-04', 7747.5)]      # отстал на неделю
        spy_bars = [('2026-08-03', 770.0), ('2026-08-04', 774.75)]
    if case == 'пропущена ровно одна сессия':
        # Пятничный бар во вторник: возраст 4 дня проходит пятидневный допуск, даты
        # источников СОВПАДАЮТ — ловит только сверка с точной предыдущей сессией биржи.
        es_bars = [('2026-08-07', 7700.0), ('2026-08-10', 7747.5)]
        spy_bars = [('2026-08-07', 770.0), ('2026-08-10', 774.75)]
        tnx_bars = [('2026-08-07', 46.9), ('2026-08-10', 46.84)]
    if case == 'ОБА источника устарели одинаково':
        # САМЫЙ ОПАСНЫЙ ВАРИАНТ: даты источников СОВПАДАЮТ, поэтому межисточниковая сверка
        # проходит, и единственная защита — проверка давности. Прежний набор его не
        # содержал, из-за чего мутация «даты не проверяются» ловилась другой защитой и
        # объявлялась пойманной по чужой причине.
        es_bars = [('2026-08-03', 7700.0), ('2026-08-04', 7747.5)]
        spy_bars = [('2026-08-03', 770.0), ('2026-08-04', 774.75)]
        tnx_bars = [('2026-08-03', 46.9), ('2026-08-04', 46.84)]
    if case == 'источники разошлись':
        tnx_bars = [('2026-08-07', 46.9), ('2026-08-10', 46.84)]        # TNX на день раньше
    ib.set_bars({es: es_bars, 900010: spy_bars, tnx: tnx_bars})
    if case == 'ориентир дальней серии отстал на сессию':
        # ДЕВЯТНАДЦАТЫЙ КРУГ, №16: дальняя серия отстала РОВНО НА ОДНУ сессию — возраст в
        # пятидневном допуске, и прежний сборщик ориентиров принимал позапрошлое закрытие;
        # ловит только требование ТОЧНОЙ предыдущей сессии.
        _far_es = [('2026-08-07', 7712.0), ('2026-08-10', 7760.0)]
        ib.set_bars({es: es_bars, 900010: spy_bars, tnx: tnx_bars,
                     900002: list(es_bars),
                     900003: [('2026-08-10', 108.4), (prev, 108.5)],
                     900006: _far_es, 900007: list(_far_es),
                     900008: [('2026-08-07', 108.1), ('2026-08-10', 108.2)]})

    # TNX не лежит в реестре — сборщик берёт его отдельным индексом; подставляем описание.
    ib.rows[tnx] = dict(instrument='TNX', sec_type='IND', exchange='CBOE', currency='USD',
                        con_id=str(tnx), local_symbol='TNX', expiry='', multiplier='')

    tmp = tempfile.mkdtemp(prefix='addfut-feed-')
    reg = ib_stub.fixture_registry(tmp)
    if case == 'серия реестра подменена согласованно':
        # ТРИДЦАТЫЙ КРУГ, №3. Строка ESU26 целиком заменена полями ДЕКАБРЬСКОГО контракта:
        # expiry, local_symbol и ответ биржи согласованы между собой. mismatches() такую
        # подмену пропускает по построению — она сверяет контракт со строкой, из которой
        # сам же взят con_id. Отказать обязана сверка ИМЕНИ с поставкой: ESU26 — это
        # сентябрь, и никакая согласованность строки этого не отменяет. Иначе календарь
        # роллов и зона поставки считаются по имени, а торгуется чужая поставка.
        t = Path(reg).read_text(encoding='utf-8').replace('ESU6,20260918,50',
                                                         'ESZ6,20261218,50')
        Path(reg).write_text(t, encoding='utf-8')
        row = dict(ib.rows[es]); row['expiry'] = '20261218'; row['local_symbol'] = 'ESZ6'
        ib.rows[es] = row

    if case == 'множитель контракта изменился':
        # БИРЖА сменила множитель ES (как SP в 1997: $500 -> $250), first_connect честно
        # перенёс его в реестр: ЛИЧНОСТЬ контракта совпадает (биржа 250 = реестр 250), и
        # единственная защита — сверка реестра с константами МОДЕЛИ. Первая редакция этого
        # сценария меняла только реестр, срабатывала сверка личности — чужая защита, и
        # мутация «модельная сверка отключена» проходила незамеченной.
        t = Path(reg).read_text(encoding='utf-8').replace('ESU6,20260918,50', 'ESU6,20260918,250')
        Path(reg).write_text(t, encoding='utf-8')
        row = dict(ib.rows[es]); row['multiplier'] = '250'
        ib.rows[es] = row

    sig = Path(tmp) / 'signals.csv'
    val = {'сигнал строкой False': 'False', 'сигнал пустой': ''}.get(case, '1')
    if case == 'ряд сигналов устарел':
        # Строки ТЕКУЩЕГО месяца нет: signal_update не отработал. По конвенции месяца
        # действия именно это, а не «возраст», означает устаревший ряд.
        rows = ',leg_eq,leg_bond\n2026-06-30,1,1\n2026-07-31,1,1\n'
    else:
        rows = f',leg_eq,leg_bond\n2026-07-31,1,1\n2026-08-31,{val},1\n'
    sig.write_text(rows, encoding='utf-8')

    class Idx:
        def __init__(self, *a, **k):
            self.conId = tnx; self.symbol = 'TNX'; self.localSymbol = 'TNX'
            self.exchange = 'CBOE'; self.currency = 'USD'
            self.secType = 'IND'; self.multiplier = ''
            self.lastTradeDateOrContractMonth = ''

    import ib_insync
    real_index, real_reg, real_sig = ib_insync.Index, FD.registry, FD.signal_state
    ib_insync.Index = Idx
    FD.registry = lambda: {r['instrument']: r for r in
                           __import__('csv').DictReader(open(reg, encoding='utf-8'))}
    FD.signal_state = lambda today, path=None, **kw: real_sig(today, path=sig, **kw)
    try:
        if case == 'ориентир дальней серии отстал на сессию':
            keep_t = FD.exchange_today
            FD.exchange_today = lambda: d0
            try:
                refs = FD.reference_prices(ib, route='F')
            finally:
                FD.exchange_today = keep_t
            return None, '', refs
        px = 7747.5 if case != 'цена ноги А в индексных пунктах' else None
        m, src = FD.build_market(ib, d0, DL.Book(ser_a='U26', ser_b='U26'), route='F')
        if case == 'цена ноги А в индексных пунктах':
            # Проверяется НЕ отказ, а величина: контрактный эквивалент обязан совпасть с
            # биржевым номиналом 50 x индекс, иначе шаг ноги А отличается вдесятеро.
            return m, '', src
        return m, '', src
    except Exception as ex:
        return None, f'{type(ex).__name__}: {ex}', {}
    finally:
        ib_insync.Index = real_index
        FD.registry = real_reg
        FD.signal_state = real_sig


@finv('нормальный вход собирается', needs=lambda r: r['case'] == 'норма')
def _f1(r):
    return r['m'] is not None and not r['err']


@finv('контрактный эквивалент ноги А равен биржевому номиналу',
      needs=lambda r: r['case'] == 'цена ноги А в индексных пунктах')
def _f2(r):
    """Дефект первой живой сессии: цена ES подавалась как есть, а S.ES_MULT откалиброван
    под цену SPY — шаг ноги А выходил ВДЕСЯТЕРО больше, и книга объявлялась неисполнимой
    при исправном счёте. Ни отказа, ни абсурдной цифры при этом не возникало.

    ДВАДЦАТЫЙ КРУГ, №21: прежде здесь сравнивался ТОЛЬКО диагностический src['nominal_ES'],
    а сама величина, которая идёт в размер книги, — m.px_eq_prev — не проверялась вовсе.
    Регрессия Market(px_eq_prev=es_prev), то есть точное возвращение старого дефекта 10x,
    прошла бы этот assert, оставь она диагностический словарь прежним. Теперь проверяется
    ЦЕНА, ПОДАННАЯ В РЕШЕНИЕ: она обязана быть закрытием SPY (774.75 в стенде), а не
    закрытием ES (7747.5), и обязана отличаться от него на порядок."""
    if r['m'] is None:
        return False
    _spy_prev, _es_prev = 774.75, r['src']['es_close'][0]
    return (abs(r['src']['nominal_ES'] - 50.0 * _es_prev) < 1e-6
            and abs(r['m'].px_eq_prev - _spy_prev) < 1e-9
            and abs(r['m'].px_eq_prev - _es_prev) > 1.0)


@finv('согласованно подменённая серия реестра отвергается',
      needs=lambda r: r['case'] == 'серия реестра подменена согласованно')
def _f30(r):
    """ТРИДЦАТЫЙ КРУГ, №3. Реестр доказывал сам себя: mismatches() сверяет ответ биржи со
    строкой, из которой взят con_id, поэтому согласованная подмена (ESU26 с полями ESZ26)
    проходила целиком, а обратное отображение адаптера называло фактический Z26 позицией
    ESU26. Смысл ИМЕНИ не сравнивался ни с чем, хотя именно по имени считаются календарь
    роллов и зона поставки. Отказ обязан быть по существу — про поставку, а не про
    что-нибудь постороннее."""
    return r['m'] is None and ('поставка' in r['err'] or 'серия' in r['err'])


@finv('отставший бар отвергается', needs=lambda r: r['case'] == 'бар вчерашний повторён')
def _f3(r):
    """Отставание ловится ТОЧНОЙ сверкой с календарём, а не допуском в днях (двадцать
    третий круг, №8): плоские пять дней отвергали ПРАВИЛЬНЫЙ бар после праздничной связки
    (29.12.2026 — разрыв шесть дней) и одновременно пропускали пятничный бар во вторник.
    Стенд требует отказа по существу: источник не отдал предыдущую БИРЖЕВУЮ сессию."""
    return r['m'] is None and ('отстал' in r['err'] or 'предыдущая сессия' in r['err'])


@finv('расхождение дат между источниками отвергается',
      needs=lambda r: r['case'] == 'источники разошлись')
def _f4(r):
    """С точной сверкой предыдущей сессии расхождение источников на Ф ловится раньше — как
    [STALE_BAR] отставшего источника; межисточниковая сверка остаётся для маршрута Е."""
    return r['m'] is None and ('[STALE_BAR]' in r['err'] or 'не совпадают' in r['err'])


@finv('пропуск одной сессии в допуске возраста отвергается',
      needs=lambda r: r['case'] == 'пропущена ровно одна сессия')
def _f10(r):
    """Позапрошлое закрытие в пределах пятидневного допуска: цель обеих ног считалась бы по
    старой цене при уже состоявшейся сессии. Единственная защита — календарная точность."""
    return r['m'] is None and '[STALE_BAR]' in r['err'] and 'пропустил сессию' in r['err']


@finv('строковое False не становится включённой ногой',
      needs=lambda r: r['case'] == 'сигнал строкой False')
def _f5(r):
    """bool('False') истинно. Ошибка формата ряда включила бы ногу вместо выключения."""
    return r['m'] is not None and r['m'].st_eq is False


@finv('пустое значение сигнала — отказ, а не включённая нога',
      needs=lambda r: r['case'] == 'сигнал пустой')
def _f6(r):
    return r['m'] is None and 'состояни' in r['err']


@finv('одинаково устаревшие источники отвергаются',
      needs=lambda r: r['case'] == 'ОБА источника устарели одинаково')
def _f8(r):
    """Даты совпадают, значит межисточниковая сверка молчит. Отвергнуть обязана проверка
    ДАВНОСТИ — и только она (после №8 это точная сверка с календарём, а не допуск в днях)."""
    return r['m'] is None and ('отстал' in r['err'] or 'предыдущая сессия' in r['err'])


@finv('смена множителя биржей останавливает торговлю',
      needs=lambda r: r['case'] == 'множитель контракта изменился')
def _f9(r):
    """Ногу Б движок считает модельной единицей: смену множителя не поймала бы ни цена, ни
    сверка позиций. Единственная защита — сверка реестра биржи с константами модели."""
    return r['m'] is None and 'множитель' in r['err']


@finv('устаревший ряд сигналов отвергается',
      needs=lambda r: r['case'] == 'ряд сигналов устарел')
def _f7(r):
    return r['m'] is None and '[SIGNAL_STALE]' in r['err']


@finv('ориентир не старше предыдущей сессии: отставшая дальняя серия помечается',
      needs=lambda r: r['case'] == 'ориентир дальней серии отстал на сессию')
def _f11(r):
    """Девятнадцатый круг, №16: пятидневный допуск давности принимал ориентир дальней
    MES/ZN-серии на сессию старше — сверка §7 на роллах мерила бы издержки от чужого
    закрытия. Отставшая серия обязана дать маркер ОРИЕНТИР-НЕТ; свежие — цену."""
    refs = r['src'] or {}
    stale = [k for k in refs if k.startswith('ОРИЕНТИР-НЕТ:')
             and k.split(':', 1)[1] in ('ESZ26', 'MESZ26', 'ZNZ26')]
    fresh = all(isinstance(refs.get(k), float) for k in ('ESU26', 'MESU26', 'ZNU26'))
    return len(stale) == 3 and fresh


def run_feed():
    cov, bad = {}, {}
    for case in FEED_CASES:
        try:
            m, err, src = _feed_run(case)
        except Exception as ex:
            m, err, src = None, f'{type(ex).__name__}: {ex}', {}
        r = dict(case=case, m=m, err=err, src=src)
        for name, fn, needs in FEED:
            if needs is not None and not needs(r):
                continue
            cov[name] = cov.get(name, 0) + 1
            try:
                ok = fn(r)
            except Exception as ex:
                ok = False; name = f'{name} [исключение: {type(ex).__name__}: {ex}]'
            if not ok:
                bad.setdefault(name, []).append(f'{case}: {err[:70]}')
    return cov, bad


# ---------------------------------------------------------------- запуск сессии
# ЗАПУСК ТОНОК, НО ИМЕННО В НЁМ ЖИВЁТ ПОРЯДОК ДЕЙСТВИЙ: отказ по незамкнутой сессии, выбор
# маршрута, снятие ориентиров ДО заявок, разделение наблюдения и торговли. Ошибка порядка
# не видна ни одному утверждению уровня решения: там всё считается правильно, просто не
# тогда и не по тем данным. Стенд подставляет брокера и историю целиком.
RUN = []


def rinv(name, needs=None):
    def deco(fn):
        RUN.append((name, fn, needs)); return fn
    return deco


RUN_CASES = ('наблюдение', 'торговля', 'незамкнутая предыдущая',
             'посторонняя позиция', 'замыкание', 'замыкание рано', 'маршрут Е',
             'замыкание повторное', 'замыкание за чужую дату', 'три сессии подряд',
             # СОРОК ПЕРВЫЙ КРУГ, №6: смерть между ST.save и touch traded-* — повтор обязан
             # быть ШТАТНЫМ, иначе ветка BK2 ставит ложную тревогу и запирает ролл.
             'повтор при сегодняшней незамкнутой книге',
             # СОРОК ЧЕТВЁРТЫЙ КРУГ, №6: та же книга, но журнал НЕ закрыт итогом этой
             # сессии — обрыв между ST.save и J.append(ИТОГ); отказ обязан звучать иначе.
             'повтор при НЕПОЛНОМ журнале',
             # ТРИДЦАТЬ СЕДЬМОЙ КРУГ, №9: замыкатель не смотрел на intent — после аварии
             # среза О-3-Е он заверял книгу, которую поздний отчёт ещё изменит.
             'замыкание при незавершённом намерении',
             'позднее исполнение после сессии', 'гонка при замыкании',
             'смена маршрута в связке с торговлей', 'замок между процессами',
             'маршрут Е при тонком запасе', 'отказ дня по §8',
             'ролл: исход заявки неизвестен',
             'ролл отложен доказуемо: журнал закрыт итогом',
             'журнал повреждён',
             'Е: тонкий запас после исполнений', 'Е: пост-трейд запас неизвестен',
             'Е: срез у самого края окна',
             'Е: запас восстановился после сокращения',
             'Е: вахта сокращает уже отторгованную сессию',
             'пути состояния: один namespace', 'счёт не пинован',
             'окно ушло за время сессии', 'пропущен торговый день',
             'worm: обязательный файл отсутствует',
             'worm: якорь аттестует действующие пути',
             'worm: подмена содержимого при коммите ловится',
             'worm: архив разных поколений помечается',
             'worm: ШТАТНЫЙ снимок проходит целиком',
             'worm: ВТОРОЙ снимок боевым вызовом',
             'worm: утрата заверенного замера',
             'автопилот: причина тревоги не затирается общей',
             'автопилот: возраст сердцебиения строг',
             'автопилот: вердикт вахты читается сквозь шум шлюза',
             'автопилот: пустая книга Е не считается слепотой',
             # ПРАВИЛА, ЗАВЕДЁННЫЕ РАЗБОРОМ /code-review 45-го круга: у каждой защиты свой
             # случай и своя парная мутация — иначе защиту можно выкинуть целиком, и
             # батарея останется зелёной (так и было у восьми из них).
             'правила45: допуск бара снимается известным календарём',
             'правила45: остаток ниже допуска не заявка',
             'правила45: беспланный предпросмотр',
             'правила45: замок книги один на всех писателей',
             'правила45: журнал не дописывается под мусорной шапкой',
             'правила45: диагност различает капитал и маржу',
             'правила45: пара реестра и замера сверяется',
             'правила45: dref кэшируется только закреплённым',
             'правила45: ошибка кода не переодевается календарём',
             'правила45: предполёт передачи не переодевает ошибку кода')


_ROLLGAP_K = 2


def _session_run(case):
    """Прогнать запуск сессии на подставном брокере. Возвращает описание исхода."""
    import os
    import tempfile
    import ib_stub
    import feed as FD
    import session as SS
    import state as ST
    import pandas as pd

    route = ('E' if case in ('маршрут Е', 'Е: тонкий запас после исполнений',
                             'Е: запас восстановился после сокращения',
                             'Е: вахта сокращает уже отторгованную сессию',
                             'Е: срез у самого края окна',
                             'Е: пост-трейд запас неизвестен') else 'F')
    es, zn, tnx, cspx, cbu0 = 900001, 900003, 990001, 900004, 900005
    esz, mesz, znz = 900006, 900007, 900008
    rows = list(ib_stub.FIXTURE_ROWS)
    ib = ib_stub.StubIB(rows, nlv=1_000_000.0)
    ib.rows[tnx] = dict(instrument='TNX', sec_type='IND', exchange='CBOE', currency='USD',
                        con_id=str(tnx), local_symbol='TNX', expiry='', multiplier='')
    prev, today = '2026-08-11', '2026-08-12'
    if case == 'повтор при НЕПОЛНОМ журнале':
        # ПРАВИЛО ДЕЙСТВУЕТ С ДАТЫ ВВЕДЕНИЯ (J.ITOG_RULE_FROM), поэтому случай обязан жить
        # ПОСЛЕ неё: на 12.08 разрыв дат законен, и стенд молчал бы, ничего не проверяя.
        # 17.08 — понедельник, 18.08 — вторник, оба торговые: prev_session сходится.
        prev, today = '2026-08-17', '2026-08-18'
    # SPY НАМЕРЕННО НЕ РАВЕН ES/10 (десятый круг, №12): при совпадающих значениях стенд не
    # отличил бы замыкание по SPY от замыкания по ES/10 — базис здесь ~-0,16%.
    bars = {900010: [('2026-08-10', 771.2), (prev, 776.0)],
            es: [('2026-08-10', 7700.0), (prev, 7747.5)],
            900002: [('2026-08-10', 7700.0), (prev, 7747.5)],
            zn: [('2026-08-10', 108.4), (prev, 108.5)],
            esz: [('2026-08-10', 7712.0), (prev, 7760.0)],
            mesz: [('2026-08-10', 7712.0), (prev, 7760.0)],
            znz: [('2026-08-10', 108.1), (prev, 108.2)],
            tnx: [('2026-08-10', 46.9), (prev, 46.84)],
            cspx: [('2026-08-10', 830.0), (prev, 834.66)],
            cbu0: [('2026-08-10', 152.5), (prev, 152.94)]}
    if case.startswith('замыкание'):
        for k in bars:
            bars[k] = bars[k] + [(today, bars[k][-1][1] * 1.01)]
    ib.set_bars(bars)

    tmp = tempfile.mkdtemp(prefix='addfut-run-')
    reg = ib_stub.fixture_registry(tmp)
    sig = Path(tmp) / 'signals.csv'
    sig.write_text(',leg_eq,leg_bond\n2026-06-30,1,1\n2026-07-31,1,1\n2026-08-31,1,1\n', encoding='utf-8')

    class Idx:
        def __init__(self, *a, **k):
            self.conId = tnx; self.symbol = 'TNX'; self.localSymbol = 'TNX'
            self.exchange = 'CBOE'; self.currency = 'USD'
            self.secType = 'IND'; self.multiplier = ''
            self.lastTradeDateOrContractMonth = ''

    import ib_insync
    keep = (ib_insync.Index, FD.registry, FD.signal_state, FD.exchange_today,
            os.environ.get('ADDFUT_DIR'), os.environ.get('ADDFUT_BOOK_PATH'),
            os.environ.get('ADDFUT_LOCK_DIR'), os.environ.get('ADDFUT_REGISTRY'))
    # ОРИГИНАЛЫ СВЯЗЫВАЮТСЯ ДО try, А НЕ ЧЕРЕЗ ДВЕСТИ СТРОК ВНУТРИ НЕГО (рецензия 20.08).
    # Прежде _rs_orig/_al_orig/_sv_orig присваивались глубоко в теле, а finally восстанавливал
    # их безусловно: любой отказ на подготовке фикстуры уводил в finally, где имён ещё нет, —
    # UnboundLocalError ЗАМЕЩАЛ настоящую причину и вылетал мимо `except BaseException`.
    # Стенд сообщал бессмыслицу вместо падения случая, а подменённые SS._alarm_o3e и ST.save
    # оставались подменёнными на весь остаток батареи.
    _rs_orig, _al_orig, _sv_orig = DL.run_session, SS._alarm_o3e, ST.save
    book_bytes0 = None
    try:
        ib_insync.Index = Idx
        FD.registry = lambda: {r['instrument']: r for r in
                               __import__('csv').DictReader(open(reg, encoding='utf-8'))}
        _sig = FD.signal_state
        FD.signal_state = lambda t, path=None, **kw: _sig(t, path=sig, **kw)
        # Час выбирается СЦЕНАРИЕМ: замыкание до закрытия биржи обязано быть отвергнуто.
        # ЧАСЫ СТЕНДА ПО СМЫСЛУ СЛУЧАЯ (двадцатый круг, №6): торговые случаи обязаны
        # идти ВНУТРИ торгового окна — прежние 17:00 означали сделку за краем окна,
        # что теперь запрещено воротами. Замыкание идёт после закрытия, как и было.
        hour = (9 if case == 'замыкание рано'
                else 17 if case.startswith('замыкание')
                else 16 if case == 'окно ушло за время сессии'
                # Окно Е узкое (08:45-09:45), и часы обязаны быть внутри него у ВСЕХ
                # случаев, которые торгуют Е, — включая те, где книга заведена как Ф.
                else 9 if (route == 'E' or 'Е' in case)
                else 10)
        FD.exchange_today = lambda: pd.Timestamp(today)
        _now = pd.Timestamp(f'{today} {hour:02d}:35' if case == 'Е: срез у самого края окна'
                            else f'{today} {hour:02d}:00', tz=FD.EXCHANGE_TZ)
        real_now = pd.Timestamp.now
        os.environ['ADDFUT_DIR'] = tmp
        os.environ['ADDFUT_LOCK_DIR'] = tmp
        # МАРШРУТ ЕСТЬ ВСЕГДА (двадцать седьмой круг, №1): его пишет hand_over_book, а
        # пилот стартовал на Ф. Стенд без route.txt описывает состояние, которого нет.
        (Path(tmp) / 'route.txt').write_text(route, encoding='utf-8')
        os.environ['ADDFUT_REGISTRY'] = str(reg)
        os.environ.pop('ADDFUT_BOOK_PATH', None)
        # ПИН ТОРГОВОГО СЧЁТА (двадцатый круг, №5). Контур обязан знать свой счёт, поэтому
        # стенд обязан его дать — и ровно тот, который отдаёт стаб, а не выдуманный:
        # выдуманный проверял бы сам себя. Отсутствие пина — ОТДЕЛЬНЫЙ случай
        # ('счёт не пинован'), иначе у новой защиты не было бы отрицательной половины.
        os.environ.pop('ADDFUT_ACCOUNT', None)
        if case != 'счёт не пинован':
            (Path(tmp) / 'account.txt').write_text(ib.managedAccounts()[0], encoding='utf-8')

        bp = Path(tmp) / f'book-{route}.json'

        def _seed_j7(rt, n_sess, _d7='2026-08-11'):
            # ЖУРНАЛ ПРОШЛОЙ СЕССИИ (двадцать второй круг, №16): у торговавшей книги
            # журнал есть ВСЕГДА; фикстура с session_no>0 без журнала — состояние,
            # которого в жизни не бывает, и новая защита от «нового GENESIS» честно
            # отказывала. Сеем закрытый итогом журнал там же, где сохраняем книгу.
            import journal as _J7
            _J7.append(Path(tmp) / f'journal-{rt}.csv', dict(
                date=_d7, leg='', instrument='ИТОГ', qty=0, px_order='-',
                px_fill='', commission='', reason='', nav='1000000', leverage='1.0',
                roll_spread_near='', roll_spread_far='',
                note=f'итог сессии {n_sess}: строк 0'))

        cls = DL.BookE if route == 'E' else DL.Book
        if case == 'пропущен торговый день':
            # ДВАДЦАТЬ ПЕРВЫЙ КРУГ, №10: книга закрыта 10.08, сегодня 12.08, а предыдущая
            # биржевая сессия — 11.08. Прежнее условие требовало лишь «сегодня новее
            # последней», и пропущенный день забывался молча; он мог нести ролл или
            # переключение сигнала.
            b0 = DL.Book(d_fix=7.9, n_e=26, n_b=10, unit_is_mes=True, prev_st_eq=True,
                         prev_st_bd=True, ser_a='U26', ser_b='U26', es_held=2,
                         last_session='2026-08-10', close_provisional=False,
                         prev_close_lev=1.99)
            ST.save(bp, b0, route, 1); _seed_j7(route, 1)
            ib._pos = {es: 2, 900002: 6, zn: 10}
            ib._shown = dict(ib._pos)
        if case in ('повтор при сегодняшней незамкнутой книге', 'повтор при НЕПОЛНОМ журнале'):
            # ДВА СЛУЧАЯ ОДНОЙ ФИКСТУРЫ, ОТЛИЧИЕ РОВНО В ОДНОМ (сведено рецензией 19.08).
            # Книга СЕГОДНЯШНЕЙ даты, close_provisional=True, отметки traded-* нет — ровно
            # состояние после обрыва между сохранением книги и созданием отметки. Разница:
            # в штатном повторе журнал закрыт итогом легаси-даты (правило ИТОГа её не
            # трогает), а во втором — итогом ПРОШЛОЙ сессии при книге за 18.08, то есть
            # обрыв между ST.save и J.append(ИТОГ), который вход обязан отличить. Две копии
            # блока разъехались бы на первой же правке, и «второй, непохожий случай того же
            # правила» тихо стал бы случаем ДРУГОГО правила.
            b0 = DL.Book(d_fix=7.9, n_e=26, n_b=10, unit_is_mes=True, prev_st_eq=True,
                         prev_st_bd=True, ser_a='U26', ser_b='U26', es_held=2,
                         last_session=today, close_provisional=True, prev_close_lev=1.99)
            ST.save(bp, b0, route, 3)
            _seed_j7(route, 3, prev if case == 'повтор при НЕПОЛНОМ журнале' else '2026-08-11')
            ib._pos = {es: 2, 900002: 6, zn: 10}
            ib._shown = dict(ib._pos)
        if case == 'незамкнутая предыдущая' or case.startswith('замыкание'):
            b0 = cls(prev_st_eq=True, prev_st_bd=True) if route == 'E' else DL.Book(
                d_fix=7.9, n_e=26, n_b=10, unit_is_mes=True, prev_st_eq=True, prev_st_bd=True,
                ser_a='U26', ser_b='U26', es_held=2,
                last_session=('2026-08-10' if case == 'замыкание за чужую дату'
                              else ('2026-08-11' if case == 'незамкнутая предыдущая' else today)),
                close_provisional=(case != 'замыкание повторное'), prev_close_lev=1.99)
            ST.save(bp, b0, route, 1); _seed_j7(route, 1)
            if case.startswith('замыкание'):
                ib._pos = {es: 2, 900002: 6, zn: 10}
                ib._shown = dict(ib._pos)
            if case == 'замыкание при незавершённом намерении':
                # Намерение с НЕИЗВЕСТНЫМ исходом лежит на диске, а снимок позиций
                # СОВПАДАЕТ с книгой: ровно тот случай, где сверка ничего не замечает и
                # прежнее замыкание спокойно фиксировало плечо, снимало backup и WORM.
                ST.save_intent(bp, route, 2, b0,
                               __import__('dataclasses').replace(b0, n_e=13, es_held=1),
                               [('ESU26', -13)])
        if case == 'Е: тонкий запас после исполнений':
            # ВОСЕМНАДЦАТЫЙ КРУГ, №1 (пара) / девятнадцатый, №10: до сделки книга пуста и
            # запаса нет вовсе (None), ПОСЛЕ исполнений брокер отдаёт 1,20 < 1,40 —
            # контур обязан поставить тревогу файлом, а не сохранить день успешным молча.
            ib.behaviour = 'thin_after'
        if case == 'Е: запас восстановился после сокращения':
            ib.behaviour = 'thin_after_ok'
        if case == 'Е: срез у самого края окна':
            # ЗАПАС ВРЕМЕНИ НА ПАРУ НАБЛЮДАЕМ ТОЛЬКО У КРАЯ (тридцать седьмой круг, №18).
            # Все случаи Е шли в 09:00 при крае окна 09:45, то есть с запасом 45 минут:
            # снятие требования TRADE_MARGIN_MIN не меняло НИЧЕГО, и парная мутация
            # pair_margin_off ничего не наблюдала. Здесь часы стоят в 09:35 — до края 10
            # минут, меньше требуемых 15: ворота ПЕРЕД ПЕРВОЙ ногой обязаны отказать, а без
            # требования запаса первая нога ушла бы к брокеру и вторая упёрлась бы в край,
            # оставив непарную дельту порядка половины NLV.
            ib.behaviour = 'thin_after'
        if case == 'Е: пост-трейд запас неизвестен':
            # ДЕВЯТНАДЦАТЫЙ КРУГ, №10: сводка после исполнений не вернула требование —
            # запас НЕИЗВЕСТЕН при живой книге; прежнее условие глотало None без тревоги.
            ib.behaviour = 'no_maint'
        if case == 'маршрут Е при тонком запасе':
            # Живой запас О-3-Е от брокера: EWL/Maint = 1,20 < 1,40 — обязательное сокращение.
            ib.behaviour = 'thin_cushion'
            b0 = DL.BookE(n_eq=1195, n_bd=6538, prev_st_eq=True, prev_st_bd=True,
                          last_session='2026-08-11', close_provisional=False,
                          prev_close_lev=1.99)
            ST.save(Path(tmp) / 'book-E.json', b0, 'E', 3); _seed_j7('E', 3)
            # СЛУЧАЙ ГОНЯЕТ МАРШРУТ Е (двадцать седьмой круг, №1): действующий
            # маршрут обязан совпадать с запрошенным, иначе сессия честно отказывает.
            (Path(tmp) / 'route.txt').write_text('E', encoding='utf-8')
            ib._pos = {cspx: 1195.0, cbu0: 6538.0}
            ib._shown = dict(ib._pos)
        if case == 'Е: вахта сокращает уже отторгованную сессию':
            # ТРИДЦАТЬ ПЕРВЫЙ КРУГ, №1: день УЖЕ отторгован (книга несёт сегодняшнюю дату и
            # предварительное замыкание), а внутридневная вахта нашла запас 1,20 < 1,40.
            # Норматив §8 требует сокращения В ТУ ЖЕ СЕССИЮ; прежде вахта умела только
            # писать ALARM, который сам же и запрещал запуск, способный книгу сократить.
            ib.behaviour = 'thin_cushion'
            b0 = DL.BookE(n_eq=1195, n_bd=6538, prev_st_eq=True, prev_st_bd=True,
                          last_session=today, close_provisional=True, prev_close_lev=1.99)
            ST.save(Path(tmp) / 'book-E.json', b0, 'E', 3); _seed_j7('E', 3)
            (Path(tmp) / 'route.txt').write_text('E', encoding='utf-8')
            ib._pos = {cspx: 1195.0, cbu0: 6538.0}
            ib._shown = dict(ib._pos)
        if case == 'отказ дня по §8':
            ib._nlv = 400_000.0    # ниже механического пола: решение отвергает день
        if case == 'ролл: исход заявки неизвестен':
            # Отложенный ролл; первая же заявка обрывается с НЕИЗВЕСТНЫМ статусом
            # (шестнадцатый круг, №5): состояние не смеет записаться, а исход не смеет
            # называться восстановлением — только О-5.
            ib.behaviour = 'disconnect'
            b0 = DL.Book(d_fix=7.9, n_e=26, n_b=10, unit_is_mes=True, prev_st_eq=True,
                         prev_st_bd=True, ser_a='U26', ser_b='U26', es_held=2,
                         last_session='2026-08-11', close_provisional=False,
                         prev_close_lev=1.5, roll_pending=True)
            ST.save(bp, b0, route, 2); _seed_j7(route, 2)
            ib._pos = {es: 2, 900002: 6, zn: 10}
            ib._shown = dict(ib._pos)
            book_bytes0 = bp.read_bytes()
        if case == 'ролл отложен доказуемо: журнал закрыт итогом':
            # СОРОК ЧЕТВЁРТЫЙ КРУГ, №5. Отложенный ролл, ворота окна не пускают ни одной
            # заявки, книга совпадает с исходной — значит откат ДОКАЗУЕМ (provable=True).
            # На этом пути книга сохранялась с НОВЫМ номером сессии и сегодняшней датой, а
            # исключение уходило наружу ДО блока записи ИТОГа. Дальше связка двух правок:
            # автопилот по дате книги ставит traded-*, а якорь WORM требует итог ИМЕННО
            # этой сессии и отвергает книгу — постоянный ALARM-backup, ролл заблокирован.
            b0 = DL.Book(d_fix=7.9, n_e=26, n_b=10, unit_is_mes=True, prev_st_eq=True,
                         prev_st_bd=True, ser_a='U26', ser_b='U26', es_held=2,
                         last_session='2026-08-11', close_provisional=False,
                         prev_close_lev=1.5, roll_pending=True)
            ST.save(bp, b0, route, 2); _seed_j7(route, 2)
            ib._pos = {es: 2, 900002: 6, zn: 10}
            ib._shown = dict(ib._pos)
        if case == 'журнал повреждён':
            # ПРАВКА ЗАДНИМ ЧИСЛОМ (семнадцатый круг, №15): цепочка хэшей рвётся, и
            # торговля обязана остановиться ДО заявок, а не дописывать битый журнал.
            import journal as JJ
            jp = Path(tmp) / f'journal-{route}.csv'
            JJ.append(jp, dict(date='2026-08-11', leg='', instrument='X', qty=1,
                               px_order='1', px_fill='', commission='', reason='',
                               nav='', leverage='', roll_spread_near='',
                               roll_spread_far='', note='итог сессии 1: строк 0'))
            jp.write_text(jp.read_text(encoding='utf-8').replace(',X,', ',Y,'),
                          encoding='utf-8')
        if case == 'посторонняя позиция':
            ib._pos = {777777: 5}; ib._shown = dict(ib._pos)
            ib.rows[777777] = dict(instrument='ЧУЖОЙ', sec_type='FUT', exchange='CME',
                                   currency='USD', con_id='777777', local_symbol='XX',
                                   expiry='', multiplier='1')

        out = dict(case=case, route=route, raised=False, error='', dec=None,
                   placed=len(ib._trades), saved=None, provisional=None, lev=None)
        # ЧАСЫ, ИДУЩИЕ ВНУТРИ СЕССИИ (тридцать второй круг, №4, пара). Случай «окно ушло
        # за время сессии» проверяет ВОРОТА ПЕРЕД ЗАЯВКОЙ, а не вход: теперь у входа стоит
        # своя проверка дня и окна, и статичные 16:00 перехватывали бы случай раньше —
        # старая защита стала бы недостижимой, а стенд доказывал бы новую. Часы отдают
        # время ВНУТРИ окна на первых обращениях (вход, подготовка) и уходят за край
        # дальше: ровно то, что происходит в бою, когда сессия затягивается.
        _clock = {'n': 0}

        def _now_fn(tz=None):
            if not tz:
                return real_now()
            # ЧАСЫ ОБЯЗАНЫ ДОЙТИ ДО СРЕЗА, А НЕ ОСТАНОВИТЬ СЕССИЮ НА ВХОДЕ (тридцать
            # восьмой круг, №3). Случай «срез у самого края окна» я завёл в 37-м круге со
            # СТАТИЧНЫМИ часами 09:35 — и его перехватывали входные ворота
            # (_window_gate(margin_min=True) на «начало подачи заявок»), потому что до края
            # 09:45 остаётся меньше требуемых 15 минут. Пост-трейдовый срез и его первые
            # ворота не исполнялись вообще: сценарий стоял в списке и не проверял НИЧЕГО.
            # Ровно тот дефект, который я же ищу у других, — стенд, не достигающий цели.
            # Здесь часы привязаны к ФАКТУ: пока основной ребаланс не подан, мы внутри окна;
            # как только он подан — мы у края. Так и бывает в бою, когда сессия затягивается.
            if case == 'Е: срез у самого края окна':
                return (pd.Timestamp(f'{today} 09:00', tz=FD.EXCHANGE_TZ)
                        if len(ib._trades) < 2 else _now)
            if case == 'ролл отложен доказуемо: журнал закрыт итогом':
                # ВХОД — ВНУТРИ ОКНА, ЗАЯВКА — ЗА КРАЕМ. Со статичными часами за краем отказ
                # приходил на ВХОДНЫХ воротах, до логики ролла: стенд не достигал ветки.
                # Порог подобран опытом (печатью пройденного пути), а не на глаз.
                _clock['n'] += 1
                return (pd.Timestamp(f'{today} 09:00', tz=FD.EXCHANGE_TZ)
                        if _clock['n'] <= _ROLLGAP_K else
                        pd.Timestamp(f'{today} 15:59', tz=FD.EXCHANGE_TZ))
            if case != 'окно ушло за время сессии':
                return _now
            _clock['n'] += 1
            return (pd.Timestamp(f'{today} 10:00', tz=FD.EXCHANGE_TZ)
                    if _clock['n'] <= 2 else _now)
        pd.Timestamp.now = staticmethod(_now_fn)
        # РЫНОК, С КОТОРЫМ РАБОТАЛА СЕССИЯ, — НАРУЖУ (тридцать седьмой круг, №18). Он
        # строится внутри session.do_trade, и без него утверждение о Decision после среза
        # могло сверять экспозицию только САМУ С СОБОЙ (тождество exposure == ne*px при
        # px = exposure/ne) — то есть не наблюдало объявленную границу вовсе. Перехватываем
        # ВХОД run_session: цены — независимый от Decision источник.
        _rs_orig = DL.run_session          # (уже связан выше, до try — см. правку ниже)

        def _rs_spy(_br, _m, *a_, **k_):
            out['market'] = _m
            return _rs_orig(_br, _m, *a_, **k_)
        DL.run_session = _rs_spy
        # ПОРЯДОК СОБЫТИЙ — ЖУРНАЛОМ ВЫЗОВОВ, А НЕ mtime ФАЙЛОВ (СОРОК ЧЕТВЁРТЫЙ КРУГ,
        # ложное доказательство №6). Здесь сравнивались st_mtime_ns тревоги и книги, но
        # mtime не задаёт happens-before: значения совпадают при быстрой записи, уезжают при
        # коррекции часов и переписываются повторным открытием файла. «Один процесс и
        # несколько секунд» — свойство ЭТОЙ машины, а не механизм. Ставим наблюдателя на обе
        # точки: он записывает ПОСЛЕДОВАТЕЛЬНОСТЬ вызовов, и она уже не зависит ни от
        # файловой системы, ни от часов.
        _seq = out['порядок'] = []

        def _al_spy(*a_, **k_):
            _seq.append('тревога')
            return _al_orig(*a_, **k_)

        def _sv_spy(*a_, **k_):
            _seq.append('книга')
            return _sv_orig(*a_, **k_)
        SS._alarm_o3e, ST.save = _al_spy, _sv_spy
        if case.startswith('замыкание'):
            out['dec'] = SS.do_close(ib, route)
        elif case == 'маршрут Е при тонком запасе':
            out['dec'] = SS.do_trade(ib, 'E', dry=False)
        elif case == 'Е: вахта сокращает уже отторгованную сессию':
            out['dec'] = SS.do_o3e_cut(ib, 'E')
        else:
            out['dec'] = SS.do_trade(ib, route, dry=(case == 'наблюдение'))
    except BaseException as ex:
        out['raised'] = True; out['error'] = f'{type(ex).__name__}: {ex}'
    finally:
        DL.run_session = _rs_orig
        SS._alarm_o3e, ST.save = _al_orig, _sv_orig   # наблюдатель порядка снимается
        pd.Timestamp.now = real_now
        (ib_insync.Index, FD.registry, FD.signal_state, FD.exchange_today,
         d_old, b_old, l_old, r_old) = keep
        for k, v in (('ADDFUT_DIR', d_old), ('ADDFUT_BOOK_PATH', b_old),
                     ('ADDFUT_LOCK_DIR', l_old), ('ADDFUT_REGISTRY', r_old)):
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    out['placed'] = len(ib._trades)
    out['positions'] = {k: v for k, v in ib._pos.items() if v}
    # тревога О-3-Е (девятнадцатый круг, №10): пишется файлом в каталог состояния
    out['alarm_o3e'] = bool(list(Path(tmp).glob('ALARM-o3e-*.txt')))
    # ПОРЯДОК, А НЕ ФАКТ (тридцать пятый круг, №3; мутация «состоявшийся срез не поднимает
    # тревогу» осталась НЕ ПОЙМАНОЙ именно поэтому). Норматив требует, чтобы тревога была
    # долговечна ДО сохранения состояния: иначе потеря питания оставляет штатно выглядящую
    # сокращённую книгу без тревоги. Наличие файла этого не доказывает — внешний путь
    # создаёт его к концу запуска в любом случае. Сравниваем время записи: книга обязана
    # быть НЕ РАНЬШЕ тревоги.
    _al = sorted(Path(tmp).glob('ALARM-o3e-*.txt'))
    # ТЕКСТ ТРЕВОГИ — НАРУЖУ (тридцать седьмой круг, №1). Проверялось только НАЛИЧИЕ файла,
    # поэтому утверждение «сокращение выполнено», сделанное до подачи среза, не наблюдалось
    # ничем: оператор получил бы единственное свидетельство, и оно лгало бы о книге.
    try:
        out['alarm_text'] = _al[0].read_text(encoding='utf-8') if _al else ''
    except OSError:
        out['alarm_text'] = ''
    # ПРИЗНАК СТРОИТСЯ ИЗ ЖУРНАЛА ВЫЗОВОВ (ложное доказательство №6): тревога обязана быть
    # записана ДО того, как состояние книги ушло на диск. Отсутствие тревоги — не «порядок
    # соблюдён», а None: сказать нечего.
    _sq = out.get('порядок') or []
    out['alarm_before_book'] = (('тревога' in _sq and 'книга' in _sq
                                 and _sq.index('тревога') < _sq.index('книга'))
                                if 'тревога' in _sq else None)
    if book_bytes0 is not None:
        try:
            out['book_same'] = (bp.read_bytes() == book_bytes0)
        except Exception:
            out['book_same'] = False
    try:
        sv, _, _ = ST.load(bp, cls)
    except Exception:
        sv = None
    out['saved'] = sv
    out['provisional'] = getattr(sv, 'close_provisional', None) if sv else None
    out['lev'] = getattr(sv, 'prev_close_lev', None) if sv else None
    out['journal'] = Path(tmp) / f'journal-{route}.csv'
    # НАМЕРЕНИЕ И СТРОКИ ЖУРНАЛА — НАРУЖУ (тридцать первый круг, №3 и №11): срез О-3-Е
    # подавался поверх старого намерения и не попадал в §7, а увидеть это было нечем.
    try:
        out['intent_left'] = ST.load_intent(bp) is not None
    except Exception:
        out['intent_left'] = None
    try:
        import journal as _J7r          # JJ выше — локальный импорт ветки фикстуры
        out['j7_rows'] = _J7r.read(out['journal']) if out['journal'].exists() else []
    except Exception:
        out['j7_rows'] = []
    return out


@rinv('наблюдение не подаёт заявок и не меняет состояние',
      needs=lambda r: r['case'] == 'наблюдение')
def _r1(r):
    return not r['raised'] and r['placed'] == 0 and not r['positions']


@rinv('наблюдение не пишет строк §7 и не рвёт пару «строки+итог»',
      needs=lambda r: r['case'] == 'наблюдение')
def _r1b(r):
    """Девятнадцатый круг, №15: dry-строки без итоговой оставляли журнал «незакрытым», и
    следующая ЖИВАЯ сессия отказывала — режим П-2 детерминированно блокировал торговлю."""
    return not r['raised'] and not r['journal'].exists()


@rinv('торговля подаёт заявки и сохраняет книгу', needs=lambda r: r['case'] == 'торговля')
def _r2(r):
    return not r['raised'] and r['placed'] > 0 and r['saved'] is not None and r['positions']


@rinv('журнал сессии закрыт итоговой строкой', needs=lambda r: r['case'] == 'торговля')
def _r3b(r):
    """Семнадцатый круг, №15: цепочка хэшей не доказывает полноту — маркер полноты
    ставит итоговая строка; обрыв между append-ами оставит журнал без неё, и следующая
    сессия остановится вместо накопления ложной выборки §7."""
    import journal as JJ
    if r['raised'] or not r['journal'].exists():
        return False
    rows = JJ.read(r['journal'])
    return (bool(rows) and rows[-1].get('instrument') == 'ИТОГ'
            and str(rows[-1].get('note', '')).startswith('итог сессии'))


@rinv('повреждённый журнал останавливает торговлю ДО заявок',
      needs=lambda r: r['case'] == 'журнал повреждён')
def _r3c(r):
    """Семнадцатый круг, №15: правленный задним числом журнал прежде молча продолжался."""
    return r['raised'] and 'журнал §7 повреждён' in r['error'] and r['placed'] == 0


@rinv('в живом журнале есть цена-ориентир', needs=lambda r: r['case'] == 'торговля')
def _r3(r):
    """Заявки подавались без ориентира, сверка §7 такие строки отбрасывает — и реальные
    сессии не давали НИ ОДНОГО наблюдения, притом что журнал объявлен единственным
    основанием для пересмотра издержек."""
    import csv
    if not r['journal'].exists():
        return False
    rows = list(csv.DictReader(open(r['journal'], encoding='utf-8')))
    return bool(rows) and all(x['px_order'] not in ('', None) for x in rows)


@rinv('неизвестный исход ролла: состояние не пишется, «восстановлено» не объявляется',
      needs=lambda r: r['case'] == 'ролл: исход заявки неизвестен')
def _r_unknown(r):
    """Шестнадцатый круг, №5: после неизвестного статуса совпавший снимок позиций ничего
    не доказывает (stale_twice, fill_after_end) — автоперенос ролла и слово «приведена»
    запрещены, книга на диске остаётся нетронутой, исход О-5."""
    return (r['raised'] and 'НЕИЗВЕСТЕН' in r['error']
            and 'недоказуемо' in r['error'] and 'переносится' not in r['error']
            and r.get('book_same') is True)


@rinv('пропущенный торговый день останавливает сессию',
      needs=lambda r: r['case'] == 'пропущен торговый день')
def _r_gap(r):
    """ДВАДЦАТЬ ПЕРВЫЙ КРУГ, №10. Книга 10.08, сегодня 12.08, предыдущая сессия 11.08 —
    день потерян. Проверяются ПРИЧИНА и НОЛЬ ЗАЯВОК: падение по любому другому поводу
    (незамкнутая книга, сверка) защитой от пропуска не является."""
    return (r['raised'] and r['placed'] == 0
            and 'пропущены торговые дни' in r['error'])


@rinv('заявка за краем торгового окна не подаётся',
      needs=lambda r: r['case'] == 'окно ушло за время сессии')
def _r_window(r):
    """ДВАДЦАТЫЙ КРУГ, №6. Окно проверялось ОДИН раз — в автопилоте, перед запуском
    session.py; внутри сессии идут исторические запросы, ориентиры, снимки позиций и
    ожидания заявок до 120 с каждое. Заявка с tif=GTC + outsideRth, ушедшая за край,
    висит до чужой сессии либо исполняется отдельно от парной. Проверяются ПРИЧИНА и
    НОЛЬ ЗАЯВОК: отказ по любому другому поводу защитой не является."""
    return r['raised'] and r['placed'] == 0 and 'окно закрыто' in r['error']


@rinv('архив разных поколений не остаётся под рабочим именем',
      needs=lambda r: r['case'] == 'worm: архив разных поколений помечается')
def _r_worm_reject(r):
    """ДВАДЦАТЫЙ КРУГ, №22. Проверяются ТРИ вещи разом: снимок отклонён, под рабочим
    именем не осталось ни одного архива, и ровно один помечен .rejected. Одного лишь
    факта исключения мало: прежде оно тоже поднималось, а файл продолжал лежать."""
    return not r['raised'] and r.get('ok') is True


@rinv('без пина торгового счёта сессия не подаёт заявок',
      needs=lambda r: r['case'] == 'счёт не пинован')
def _r_pin(r):
    """ДВАДЦАТЫЙ КРУГ, №5. Пин существовал в адаптере, но в бою был мёртв: ADDFUT_ACCOUNT
    не задавался нигде, и при одном managedAccount брался тот счёт, который дал шлюз.
    Проверяется ПРИЧИНА и НОЛЬ ЗАЯВОК: падение по любой другой причине защитой не является.
    Положительная половина — все прочие случаи запуска: они пинуются счётом стаба."""
    return r['raised'] and r['placed'] == 0 and 'не пинован' in r['error']


@rinv('незамкнутая предыдущая сессия останавливает торговлю',
      needs=lambda r: r['case'] == 'незамкнутая предыдущая')
def _r4(r):
    return r['raised'] and 'не замкнута' in r['error'] and r['placed'] == 0


@rinv('посторонняя позиция на счёте останавливает сессию',
      needs=lambda r: r['case'] == 'посторонняя позиция')
def _r5(r):
    """Проверяется ПРИЧИНА отказа, а не сам факт. Иначе любая поломка стенда — отсутствующий
    метод подстановки, опечатка — засчитывалась бы как успешная защита: сессия ведь упала."""
    return r['raised'] and r['placed'] == 0 and 'осторонн' in r['error']


@rinv('замыкание снимает предварительность и ставит плечо закрытия ПО SPY',
      needs=lambda r: r['case'] == 'замыкание')
def _r6(r):
    """Плечо закрытия обязано считаться от SPY (десятый круг, №1). Первая редакция этого
    утверждения сравнивала ПОЛНОЕ плечо с вкладом одной ноги А и выбирала просто больший из
    кандидатов — оно проходило и при фактическом ES/10 (одиннадцатый круг, №2). Теперь
    считаются ОБА полных плеча (SPY и ES/10, включая ногу Б по dref закрытия), и сохранённое
    обязано совпасть со SPY-вариантом с точностью до округления, а варианты — различаться.
    """
    if r['raised'] or r['provisional'] is not False or r['placed'] != 0:
        return False
    import sim_v13 as S
    b = r['saved']
    nav = 1_000_000.0
    # сегодняшние закрытия стенда: prev * 1.01 (см. ветку 'замыкание' в _session_run)
    spy_t = 776.0 * 1.01
    es10_t = 7747.5 * 1.01 / 10.0
    y_t = 46.84 * 1.01 / 10.0 / 100.0
    dref_t = float(S.dur(y_t))
    u_b = (S.ZN_MODEL_PX_EQ * S.CTD_RATIO * b.d_fix * 1e-4) / (dref_t * 1e-4)
    leg_b = b.n_b * u_b
    lev_spy = (b.n_e * (S.ES_MULT / 10) * spy_t + leg_b) / nav
    lev_es = (b.n_e * (S.ES_MULT / 10) * es10_t + leg_b) / nav
    if abs(lev_spy - lev_es) < 1e-6:
        return False                      # бары неразличимы — утверждение было бы пустым
    return abs(r['lev'] - lev_spy) < 1e-9


@rinv('тонкий запас ПОСЛЕ исполнений СОКРАЩАЕТ книгу в ту же сессию',
      needs=lambda r: r['case'] == 'Е: тонкий запас после исполнений')
def _r30b(r):
    """ТРИДЦАТЫЙ КРУГ, №1. Норматив §8: «live-отношение <1,40 — сокращение до L=1 В ТУ ЖЕ
    СЕССИЮ». Прежде контур только ставил тревогу, а под этой тревогой он не торгует —
    значит сокращения не происходило ни в ту сессию, ни в следующую, вообще никогда.
    В двадцать девятом круге я переписал ТЕКСТ тревоги, убрав ложное обещание: текст стал
    честным, норматив остался невыполненным. Здесь проверяется ПОВЕДЕНИЕ: книга обязана
    быть сокращена, плечо закрытия — не выше 1,0, а позиции брокера совпасть с книгой."""
    cut = getattr(r['dec'], 'o3e_cut', None)
    sv = r['saved']
    if not cut or sv is None:
        return False
    _, n0e, n0b, ne, nb = cut
    return (ne < n0e and nb < n0b
            and sv.n_eq == ne and sv.n_bd == nb
            and sorted(r['positions'].values()) == sorted([float(ne), float(nb)])
            and sv.prev_close_lev <= 1.0 + 1e-9)


@rinv('вахта О-3-Е сокращает книгу в уже отторгованной сессии',
      needs=lambda r: r['case'] == 'Е: вахта сокращает уже отторгованную сессию')
def _r31c(r):
    """ТРИДЦАТЬ ПЕРВЫЙ КРУГ, №1. Норматив §8 требует сокращения до L=1 В ТУ ЖЕ СЕССИЮ, а
    внутридневная вахта автопилота умела только писать ALARM — и этим же ALARM запрещала
    любой запуск, который мог бы книгу сократить. Провал запаса ПОСЛЕ traded-* оставлял
    маржинальную книгу около 2x на ночь: небольшой следующий ход даёт margin call.

    Проверяется поведение целиком: книга сокращена и СОХРАНЕНА тем же номером сессии,
    позиции брокера ей соответствуют, плечо предварительного замыкания не выше 1,0,
    намерение снято, тревога поставлена (причина не разобрана — О-5), а дата в §7 ЯВНО
    исключена из выборки издержек: строки среза легли после итога торговой сессии.
    """
    sv, dec = r['saved'], r['dec']
    rows = r.get('j7_rows') or []
    if r['raised'] or dec is None or sv is None:
        return False
    day = max((x['date'] for x in rows), default='')
    tot = [x for x in rows if x['date'] == day and x.get('instrument') == 'ИТОГ']
    cut_rows = [x for x in rows if x['date'] == day and x.get('instrument') in ('CSPX', 'CBU0')
                and int(float(x['qty'])) < 0]
    return (sv.n_eq < 1195 and sv.n_bd < 6538
            and sorted(r['positions'].values()) == sorted([float(sv.n_eq), float(sv.n_bd)])
            and sv.close_provisional is True and sv.prev_close_lev <= 1.0 + 1e-9
            and r.get('intent_left') is False and r.get('alarm_o3e') is True
            and len(cut_rows) == 2
            and any('ИСКЛЮЧЕНА' in (x.get('note') or '') for x in tot))


@rinv('состоявшееся сокращение О-3-Е ставит тревогу, даже когда запас восстановился',
      needs=lambda r: r['case'] == 'Е: запас восстановился после сокращения')
def _r31a(r):
    """ТРИДЦАТЬ ПЕРВЫЙ КРУГ, №2. Тревога ставилась только если ПОВТОРНЫЙ замер всё ещё
    ниже 1,40 — а удачный срез запас как раз поднимает. В штатном исходе норматива §8
    тревоги не возникало вовсе: аварийное сокращение проходило молча, причина не
    разбиралась, и следующая сессия полосой возвращала книгу к 2x. Прежний стенд этого не
    видел, потому что стаб держал 1,20 навсегда — успешная ветка не исполнялась."""
    cut = getattr(r['dec'], 'o3e_cut', None)
    return (not r['raised'] and bool(cut) and r.get('alarm_o3e') is True
            and r.get('alarm_before_book') is True          # №3: тревога ДО состояния
            and r['saved'] is not None)


@rinv('после среза О-3-Е решение описывает ФАКТ, а не досрезную книгу',
      needs=lambda r: r['case'] in ('Е: тонкий запас после исполнений',
                                    'Е: запас восстановился после сокращения'))
def _r36(r):
    """ТРИДЦАТЬ ШЕСТОЙ КРУГ, №8 и №12. Проверки смотрели на сохранённую книгу и o3e_cut, а
    сам Decision оставался от книги ДО среза: §7 получал старое плечо, заём и суточные
    расходы относились к уже проданной позиции. Наблюдаем ровно объявленную границу —
    экспозиция, плечо и расходы обязаны соответствовать сокращённой книге."""
    d = r['dec']
    cut = getattr(d, 'o3e_cut', None)
    if r['raised'] or not cut:
        return False
    _, _, _, _ne, _nb = cut
    _sv = r['saved']
    if _sv is None or _sv.n_eq != _ne or _sv.n_bd != _nb:
        return False
    _exp = getattr(d, 'exposure', None) or {}
    if not _exp:
        return False
    # ТОЖДЕСТВО НЕ ЕСТЬ ПРОВЕРКА (тридцать седьмой круг, №18). Здесь стояло
    # `px = exposure/ne; exposure == ne*px` — истинно для ЛЮБОЙ ненулевой экспозиции, в том
    # числе досрезной: объявленная граница не наблюдалась вовсе. Сверяем с ценами рынка,
    # то есть с независимо восстановимой величиной, и по ОБЕИМ ногам.
    _m = r.get('market')
    _pe = float(getattr(_m, 'px_eq_prev', 0.0) or 0.0)
    _pb = float(getattr(_m, 'px_bd_prev', 0.0) or 0.0)
    if not (_pe > 0 and _pb > 0):
        return False
    if abs(float(_exp.get('А', 0.0)) - _ne * _pe) > 1e-6:
        return False
    if abs(float(_exp.get('Б', 0.0)) - _nb * _pb) > 1e-6:
        return False
    # РАСХОДЫ И ЗАПАС — ТОЖЕ ОБЪЯВЛЕННЫЕ ГРАНИЦЫ (№18): без них мутация, оставляющая
    # daily_costs/cushion досрезными, не наблюдается ничем.
    _dc = getattr(d, 'daily_costs', None) or {}
    if _dc:
        _P = _ne * _pe + _nb * _pb
        _cap = float(getattr(d, 'capital_after_costs', 0.0) or 0.0)
        _want = DL.costs_e(_P, _cap, _ne, _pe)
        for _k in ('ter', 'drag', 'loan', 'debit'):
            if abs(float(_dc.get(_k, 0.0)) - float(_want[_k])) > 1e-6:
                return False
    return d.leverage <= 1.05


@rinv('срез О-3-Е идёт через намерение и попадает в журнал §7',
      needs=lambda r: r['case'] in ('Е: тонкий запас после исполнений',
                                    'Е: запас восстановился после сокращения'))
def _r31b(r):
    """ТРИДЦАТЬ ПЕРВЫЙ КРУГ, №3 и №11. Заявки среза подавались поверх СТАРОГО намерения
    (на диске — книга до сессии, в намерении — книга до среза, у брокера после отказа
    второй ноги — третья) и не попадали в журнал §7: строки и ИТОГ пишутся ВЫШЕ, до среза.
    Значит крупнейший аварийный оборот сессии и его комиссии исключались из выборки, а
    счётчик «строк N» сходился — дата считалась ПОЛНЫМ наблюдением 5 б.п.

    Проверяется: намерение снято (сессия дошла до конца), в журнале есть строки по обеим
    ногам среза, и ИТОГ обещает столько строк, сколько их на дату."""
    import re as _re31
    cut = getattr(r['dec'], 'o3e_cut', None)
    rows = r.get('j7_rows') or []
    if r['raised'] or not cut or r.get('intent_left') is not False:
        return False
    _, n0e, n0b, ne, nb = cut
    day = max(x['date'] for x in rows) if rows else ''
    live = [x for x in rows if x['date'] == day and x.get('instrument') != 'ИТОГ']
    tot = [x for x in rows if x['date'] == day and x.get('instrument') == 'ИТОГ']
    if not tot:
        return False
    # ПОМЕТКА ИСКЛЮЧЕНИЯ ТРЕБУЕТСЯ ЯВНО (тридцать четвёртый круг, №13): без неё потеря
    # признака задержанного ориентира у строк среза остаётся незамеченной.
    if not any('ИСКЛЮЧЕНА' in (x.get('note') or '') for x in tot):
        return False
    # ПРИЗНАК ЗАДЕРЖАННОГО ОРИЕНТИРА У СТРОК СРЕЗА — НАПРЯМУЮ (тридцать шестой круг, разбор
    # непойманной мутации). Через ИТОГ он маскируется задержанными заявками ОСНОВНОЙ сессии:
    # пометка ИСКЛЮЧЕНА стоит в любом случае, и потеря признака у среза невидима. В сессии
    # без первоначального ребаланса это означало бы, что строки аварийного среза с данными
    # типа 3 попадут в §7 как полноценное измерение исполнения. Стенды идут на задержанных
    # данных, значит список обязан быть непустым.
    if not getattr(r['dec'], 'o3e_delayed', None):
        return False
    m = _re31.search(r'строк (\d+)', tot[-1].get('note') or '')
    # заявки среза: ровно те количества, что записаны в o3e_cut
    want = {('CSPX', ne - n0e), ('CBU0', nb - n0b)}
    have = {(x['instrument'], int(float(x['qty']))) for x in live}
    return (bool(m) and int(m.group(1)) == len(live) and want <= have)


@rinv('тонкий запас ПОСЛЕ исполнений ставит тревогу файлом',
      needs=lambda r: r['case'] == 'Е: тонкий запас после исполнений')
def _r22(r):
    """Восемнадцатый круг, №1 (пара) / девятнадцатый, №10: предторговый замер пустой книги
    ничего не говорит о книге ПОСЛЕ удвоения; фактический запас 1,20 < 1,40 обязан лечь
    тревогой файлом, которую ворота автопилота видят независимо от кода возврата."""
    return (not r['raised'] and r['placed'] > 0 and r.get('alarm_o3e') is True
            and r['saved'] is not None)


@rinv('неизвестный запас после исполнений — тревога, а не молчание',
      needs=lambda r: r['case'] == 'Е: пост-трейд запас неизвестен')
def _r23(r):
    """Девятнадцатый круг, №10: None при живой книге — не «требования нет», а неизвестный
    запас; прежнее условие «not None and < порога» глотало его без следа."""
    return (not r['raised'] and r['placed'] > 0 and r.get('alarm_o3e') is True
            and r['saved'] is not None)


@rinv('каталог журнала и тревог совпадает с каталогом замка',
      needs=lambda r: r['case'] == 'пути состояния: один namespace')
def _r24(r):
    """Девятнадцатый круг, №17: state_dir() без ADDFUT_DIR обязан следовать за
    state.lock_dir() — иначе тревога О-3-Е пишется мимо автопилота."""
    return not r['raised'] and r.get('ok') is True


@rinv('WORM: отсутствие обязательного файла — отказ снимка',
      needs=lambda r: r['case'] == 'worm: обязательный файл отсутствует')
def _r25(r):
    """Девятнадцатый круг, №18: строка «ФАЙЛА НЕТ» при успешном снимке заверяла пустоту."""
    return not r['raised'] and r.get('ok') is True


@rinv('WORM: якорь аттестует действующие пути состояния',
      needs=lambda r: r['case'] == 'worm: якорь аттестует действующие пути')
def _r26(r):
    """Девятнадцатый круг, №18: хэш в якоре — от файла, которым ТОРГУЕТ контур
    (ADDFUT_SIGNALS/lock_dir), а не от жёсткого ~/.addfut."""
    return not r['raised'] and r.get('ok') is True


@rinv('WORM: подмена содержимого между add и commit ловится blob-сверкой',
      needs=lambda r: r['case'] == 'worm: подмена содержимого при коммите ловится')
def _r27(r):
    """Девятнадцатый круг, №19: ls-tree по имени подтверждал лишь путь; pre-commit hook
    подменял текст, и «якорь в HEAD» относился к другому содержимому."""
    return not r['raised'] and r.get('ok') is True


@rinv('тонкий живой запас О-3-Е сокращает книгу маршрута Е',
      needs=lambda r: r['case'] == 'маршрут Е при тонком запасе')
def _r20(r):
    """Запас 1,20 от брокера ниже порога 1,40: сокращение обязано пройти ЖИВЫМ путём —
    do_trade -> margin_cushion -> step_e(live_cushion=...) — а не ручной подстановкой в
    тест (десятый круг, №2 и №12)."""
    if r['raised']:
        return False
    d = r['dec']
    # И ТРЕВОГА ТОЖЕ (тридцать второй круг, №2). Прежний стенд смотрел только количества:
    # предторговый срез РЕАЛЬНО резал книгу, но признак среза не выставлялся, тревога не
    # ставилась, причина не разбиралась — и следующая сессия полосой возвращала книгу к 2x.
    # Зелёное утверждение не наблюдало собственную защиту.
    return (d is not None and any('О-3-Е' in x for x in d.reasons)
            and d.book_after.n_eq < 1195 and d.book_after.n_bd < 6538
            and getattr(d, 'o3e_cut', None) is not None
            and r.get('alarm_o3e') is True)


@rinv('О-3-Е: капитал, плечо и заём считаются от ФИНАЛЬНОЙ книги',
      needs=lambda r: r['case'] == 'маршрут Е при тонком запасе')
def _r20b(r):
    """Семнадцатый круг, №16: цель L=1 от капитала ДО расходов сокращения оставляла
    L≈1,0005, а заём журналировался по несуществующей позиции. Проверяются деньги:
    комиссии соответствуют финальным заявкам, плечо не выше 1, debit — от финала."""
    if r['raised'] or r['dec'] is None:
        return False
    d = r['dec']
    b = d.book_after
    if not (b.n_eq and b.n_bd):
        return False
    p_e = d.exposure['А'] / b.n_eq
    p_b = d.exposure['Б'] / b.n_bd
    turn = (abs(d.orders.get('CSPX', 0)) * p_e + abs(d.orders.get('CBU0', 0)) * p_b)
    want_e = 1_000_000.0 - DL.S.COST * turn
    P = d.exposure['А'] + d.exposure['Б']
    want_debit = max(0.0, P + 0.05 * d.capital_after_costs - d.capital_after_costs)
    return (abs(d.capital_after_costs - want_e) < 1e-6
            and d.leverage <= 1.0 + 1e-9
            and abs(d.daily_costs['debit'] - want_debit) < 1e-6)


@rinv('отказ дня не помечает день отторгованным',
      needs=lambda r: r['case'] == 'отказ дня по §8')
def _r21(r):
    """Отказ решения обязан быть ОТКАЗОМ процесса (десятый круг, №5): прежде сессия с
    отказом завершалась кодом 0, автопилот ставил «отторговано», и требуемого входа просто
    не было при журнале «ок»."""
    return (r['raised'] and 'отвергнуто' in r['error']
            and (r['saved'] is None or r['saved'].last_session is None))


@rinv('замыкание при незавершённом намерении отвергается',
      needs=lambda r: r['case'] == 'замыкание при незавершённом намерении')
def _r9b(r):
    """ТРИДЦАТЬ СЕДЬМОЙ КРУГ, №9. Замыкатель проверял метку передачи, но не intent. После
    аварии среза О-3-Е на диске остаётся промежуточная книга и намерение с неизвестным
    исходом; снимок позиций может временно совпасть — и замыкание заверяло состояние,
    которое поздний отчёт ещё изменит: фиксировало плечо, снимало backup и WORM-якорь,
    разрешало closed-*. Основная сессия разбирает intent ДО сверки, замыкание обходило."""
    return r['raised'] and 'намерение' in r['error']


@rinv('тревога О-3-Е не объявляет сокращённой книгу, которая не сокращалась',
      needs=lambda r: r['case'] in ('Е: пост-трейд запас неизвестен',
                                    'Е: тонкий запас после исполнений'))
def _r_alarm_txt(r):
    """ТРИДЦАТЬ СЕДЬМОЙ КРУГ, №1. Текст тревоги БЕЗУСЛОВНО утверждал «Сокращение выполнено
    автоматикой», хотя один из трёх вызовов стоит ДО расчёта и подачи среза: при неизвестном
    запасе среза не будет вовсе. Обрыв питания между fsync тревоги и первой заявкой оставлял
    книгу около 2× и файл, сообщающий оператору, что она сокращена. Проверяется соответствие
    текста ФАКТУ: срез был — можно утверждать; среза не было — обязано стоять предупреждение
    проверить книгу первым действием."""
    txt = r.get('alarm_text') or ''
    if not txt:
        return False
    cut = getattr(r['dec'], 'o3e_cut', None) if r.get('dec') is not None else None
    if cut:
        return 'выполнено автоматикой' in txt
    return ('НЕ ПОДТВЕРЖДЕНО' in txt or 'НЕ ВЫПОЛНЕНО' in txt) \
        and 'Сокращение выполнено автоматикой' not in txt


@rinv('срез О-3-Е у края окна НЕ начинается и не торгует за краем',
      needs=lambda r: r['case'] == 'Е: срез у самого края окна')
def _r_edge(r):
    """ТРИДЦАТЬ ПЯТЫЙ КРУГ, №2 (пара) И ТРИДЦАТЬ ВОСЬМОЙ, №3. Срез — операция ДВУХНОГАЯ:
    без запаса времени первая продажа успевает исполниться у самого края, а перед второй
    ногой срабатывает WindowClosed — непарная дельта порядка половины NLV. Требование
    TRADE_MARGIN_MIN перед ПЕРВОЙ ногой наблюдаемо только здесь: во всех прочих случаях Е
    часы стоят в 09:00 при крае 09:45, и снятие запаса ничего не меняет.
    Проверяется ровно объявленное: (а) отказ произошёл ДО первой заявки среза — подано
    столько же заявок, сколько в основном ребалансе (2), и среза в решении нет; (б) чистый
    отказ ворот НЕ запустил компенсацию и не оставил намерение (тридцать восьмой круг, №2):
    иначе автоматика торговала бы за краем окна, «леча» то, чего сама не делала."""
    return (r['raised'] and 'НЕ НАЧАТО' in (r.get('error') or '')
            and r.get('placed') == 2
            and getattr(r.get('dec'), 'o3e_cut', None) is None
            and r.get('intent_left') is False)


@rinv('повтор в тот же день — ШТАТНЫЙ отказ, а не тревога',
      needs=lambda r: r['case'] == 'повтор при сегодняшней незамкнутой книге')
def _r_rep(r):
    """СОРОК ПЕРВЫЙ КРУГ, №6. Смерть между ST.save и touch traded-* оставляет книгу
    сегодняшней даты с close_provisional=True. Прежде повтор упирался в отказ «не замкнута»
    РАНЬШЕ проверки «не новее последней завершённой», по которой автопилот узнаёт штатный
    повтор: ветка BK2 становилась недостижимой, ставила traded-* и тут же писала
    ALARM-trade-*, и следующие сессии, включая РОЛЛ, стояли до человека. Отказ обязан нести
    узнаваемое слово и НЕ подавать заявок."""
    return (r['raised'] and 'не новее последней завершённой' in (r.get('error') or '')
            and r.get('placed') == 0)


@rinv('неполная сессия отличается от штатного повтора и НЕ подаёт заявок',
      needs=lambda r: r['case'] == 'повтор при НЕПОЛНОМ журнале')
def _r44j(r):
    """СОРОК ЧЕТВЁРТЫЙ КРУГ, №6. Три требования сразу, потому что порознь каждое зелено и
    при снятой защите: отказ ЕСТЬ; он НЕ несёт слов штатного повтора («не новее последней
    завершённой»), по которым автопилот ставит traded-* и идёт дальше, — иначе недостача
    всплывёт лишь на замыкании, когда день уже объявлен отторгованным; заявок не подано."""
    err = r.get('error') or ''
    return (r['raised'] and 'НЕПОЛНА' in err
            and 'не новее последней завершённой' not in err
            and r.get('placed') == 0)


@rinv('повторное замыкание отвергается',
      needs=lambda r: r['case'] == 'замыкание повторное')
def _r9(r):
    """Повторный запуск переписал бы плечо закрытия ценами ДРУГОЙ сессии — молчаливая смена
    триггера капа §1."""
    return r['raised'] and 'уже замкнута' in r['error']


@rinv('замыкание книги ЧУЖОЙ даты отвергается',
      needs=lambda r: r['case'] == 'замыкание за чужую дату')
def _r10(r):
    """Пропущенное в понедельник замыкание, запущенное во вторник, посчитало бы триггер капа
    по вторничным ценам для понедельничной книги."""
    return r['raised'] and 'относится к сессии' in r['error']


@rinv('замыкание до закрытия биржи отвергается',
      needs=lambda r: r['case'] == 'замыкание рано')
def _r7(r):
    return r['raised'] and 'НЕ ЗАВЕРШЁН' in r['error'] and r['provisional'] is True


@rinv('маршрут Е строит книгу в долях фондов, а не во фьючерсах',
      needs=lambda r: r['case'] == 'маршрут Е')
def _r8(r):
    """ДВАДЦАТЬ ПЕРВЫЙ КРУГ, №20: та же вакуумность — all() по пустым позициям истинно.
    Требуется непустая книга фондов, иначе утверждение не отличает «маршрут Е сработал»
    от «маршрут Е не сделал ничего»."""
    if r['raised']:
        return False
    pos = r['positions']
    return (bool(pos) and all(k in (900004, 900005) for k in pos)
            and any(float(v) for v in pos.values()))


def _session_days(days=('2026-08-12', '2026-08-13', '2026-08-14')):
    """НЕСКОЛЬКО СЕССИЙ ПОДРЯД: торговля -> замыкание -> торговля -> ...

    Седьмая рецензия указала на пробел по построению: все проверки уровня сессии
    одношаговые, а самые дорогие дефекты лежат на временной оси — дрейф серии, повторное
    замыкание, торговля по незамкнутой книге, накопление last_session. Здесь стенд ведёт
    один и тот же счёт через три биржевых дня.
    """
    import os
    import tempfile
    import ib_stub
    import feed as FD
    import session as SS
    import state as ST
    import pandas as pd

    es, zn, tnx = 900001, 900003, 990001
    rows = list(ib_stub.FIXTURE_ROWS)
    ib = ib_stub.StubIB(rows, nlv=1_000_000.0)
    ib.rows[tnx] = dict(instrument='TNX', sec_type='IND', exchange='CBOE', currency='USD',
                        con_id=str(tnx), local_symbol='TNX', expiry='', multiplier='')
    base = {900010: [('2026-08-10', 770.0), ('2026-08-11', 774.75)],
            es: [('2026-08-10', 7700.0), ('2026-08-11', 7747.5)],
            900002: [('2026-08-10', 7700.0), ('2026-08-11', 7747.5)],
            zn: [('2026-08-10', 108.4), ('2026-08-11', 108.5)],
            tnx: [('2026-08-10', 46.9), ('2026-08-11', 46.84)]}

    tmp = tempfile.mkdtemp(prefix='addfut-days-')
    reg = ib_stub.fixture_registry(tmp)
    sig = Path(tmp) / 'signals.csv'
    sig.write_text(',leg_eq,leg_bond\n2026-06-30,1,1\n2026-07-31,1,1\n2026-08-31,1,1\n', encoding='utf-8')

    class Idx:
        def __init__(self, *a, **k):
            self.conId = tnx; self.symbol = 'TNX'; self.localSymbol = 'TNX'
            self.exchange = 'CBOE'; self.currency = 'USD'
            self.secType = 'IND'; self.multiplier = ''
            self.lastTradeDateOrContractMonth = ''

    import ib_insync
    keep = (ib_insync.Index, FD.registry, FD.signal_state, FD.exchange_today,
            os.environ.get('ADDFUT_DIR'), os.environ.get('ADDFUT_LOCK_DIR'),
            os.environ.get('ADDFUT_REGISTRY'), os.environ.get('ADDFUT_BOOK_PATH'))
    try:
        ib_insync.Index = Idx
        FD.registry = lambda: {r['instrument']: r for r in
                               __import__('csv').DictReader(open(reg, encoding='utf-8'))}
        _sig = FD.signal_state
        FD.signal_state = lambda t, path=None, **kw: _sig(t, path=sig, **kw)
        os.environ['ADDFUT_DIR'] = tmp
        os.environ['ADDFUT_LOCK_DIR'] = tmp
        # МАРШРУТ ЕСТЬ ВСЕГДА (двадцать седьмой круг, №1): его пишет hand_over_book, а
        # пилот стартовал на Ф. Стенд без route.txt описывает состояние, которого нет.
        (Path(tmp) / 'route.txt').write_text('F', encoding='utf-8')
        os.environ['ADDFUT_REGISTRY'] = str(reg)
        os.environ.pop('ADDFUT_BOOK_PATH', None)
        # ПИН ТОРГОВОГО СЧЁТА (двадцатый круг, №5): контур обязан знать свой счёт, и
        # стенд даёт ровно тот, что отдаёт стаб — выдуманный проверял бы сам себя.
        os.environ.pop('ADDFUT_ACCOUNT', None)
        (Path(tmp) / 'account.txt').write_text(ib.managedAccounts()[0], encoding='utf-8')
        real_now = pd.Timestamp.now

        out = dict(case='три сессии подряд', errors=[], series=[], sessions=[], levs=[],
                   refused_without_close=False)
        prev_close = 7747.5
        for i, day in enumerate(days):
            FD.exchange_today = (lambda d=day: pd.Timestamp(d))
            bars = {k: list(v) for k, v in base.items()}
            for k in bars:
                # Каждый день добавляет закрытие ПРЕДЫДУЩЕГО дня, затем — своё.
                for j in range(i):
                    px = bars[k][-1][1] * 1.001
                    bars[k].append((days[j], px))
            ib.set_bars(bars)
            # 1) торговля
            pd.Timestamp.now = staticmethod(lambda tz=None: pd.Timestamp(f'{day} 10:00', tz=FD.EXCHANGE_TZ)
                                            if tz else real_now())
            try:
                SS.do_trade(ib, 'F', dry=False)
            except BaseException as ex:
                out['errors'].append(f'{day} торговля: {type(ex).__name__}: {ex}')
            # 2) вторая торговля в тот же день до замыкания — обязана быть отвергнута
            try:
                SS.do_trade(ib, 'F', dry=False)
                out['errors'].append(f'{day}: вторая торговля до замыкания НЕ отвергнута')
            except BaseException:
                out['refused_without_close'] = True
            # 3) замыкание после закрытия биржи
            bars2 = {k: list(v) + [(day, v[-1][1] * 1.002)] for k, v in bars.items()}
            ib.set_bars(bars2)
            pd.Timestamp.now = staticmethod(lambda tz=None: pd.Timestamp(f'{day} 17:00', tz=FD.EXCHANGE_TZ)
                                            if tz else real_now())
            try:
                SS.do_close(ib, 'F')
            except BaseException as ex:
                out['errors'].append(f'{day} замыкание: {type(ex).__name__}: {ex}')
            b, sess, _ = ST.load(ST.book_path('F'), DL.Book)
            out['series'].append(b.ser_a); out['sessions'].append(b.last_session)
            out['levs'].append(round(b.prev_close_lev, 4))
    finally:
        pd.Timestamp.now = real_now
        (ib_insync.Index, FD.registry, FD.signal_state, FD.exchange_today,
         d0, l0, r0, b0) = keep
        for k, v in (('ADDFUT_DIR', d0), ('ADDFUT_LOCK_DIR', l0),
                     ('ADDFUT_REGISTRY', r0), ('ADDFUT_BOOK_PATH', b0)):
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    return out


def _session_late_fill():
    """Позднее исполнение ПОСЛЕ завершённой сессии обязано остановить следующую.

    Это и есть настоящая гарантия против устойчиво устаревших позиций и исполнений,
    приходящих после конца выгрузки: распознать их в момент сделки нельзя, но расхождение
    книги обнаруживается на входной сверке следующей сессии — и та НЕ ТОРГУЕТ.
    """
    import os
    import tempfile
    import ib_stub
    import feed as FD
    import session as SS
    import state as ST
    import pandas as pd

    es, zn, tnx = 900001, 900003, 990001
    rows = list(ib_stub.FIXTURE_ROWS)
    ib = ib_stub.StubIB(rows, nlv=1_000_000.0)
    ib.rows[tnx] = dict(instrument='TNX', sec_type='IND', exchange='CBOE', currency='USD',
                        con_id=str(tnx), local_symbol='TNX', expiry='', multiplier='')
    bars = {900010: [('2026-08-10', 770.0), ('2026-08-11', 774.75)],
            es: [('2026-08-10', 7700.0), ('2026-08-11', 7747.5)],
            900002: [('2026-08-10', 7700.0), ('2026-08-11', 7747.5)],
            zn: [('2026-08-10', 108.4), ('2026-08-11', 108.5)],
            tnx: [('2026-08-10', 46.9), ('2026-08-11', 46.84)]}

    tmp = tempfile.mkdtemp(prefix='addfut-late-')
    reg = ib_stub.fixture_registry(tmp)
    sig = Path(tmp) / 'signals.csv'
    sig.write_text(',leg_eq,leg_bond\n2026-06-30,1,1\n2026-07-31,1,1\n2026-08-31,1,1\n', encoding='utf-8')

    class Idx:
        def __init__(self, *a, **k):
            self.conId = tnx; self.symbol = 'TNX'; self.localSymbol = 'TNX'
            self.exchange = 'CBOE'; self.currency = 'USD'
            self.secType = 'IND'; self.multiplier = ''
            self.lastTradeDateOrContractMonth = ''

    import ib_insync
    keep = (ib_insync.Index, FD.registry, FD.signal_state, FD.exchange_today,
            os.environ.get('ADDFUT_DIR'), os.environ.get('ADDFUT_LOCK_DIR'),
            os.environ.get('ADDFUT_REGISTRY'), os.environ.get('ADDFUT_BOOK_PATH'))
    try:
        ib_insync.Index = Idx
        FD.registry = lambda: {r['instrument']: r for r in
                               __import__('csv').DictReader(open(reg, encoding='utf-8'))}
        _sig = FD.signal_state
        FD.signal_state = lambda t, path=None, **kw: _sig(t, path=sig, **kw)
        os.environ['ADDFUT_DIR'] = tmp
        os.environ['ADDFUT_LOCK_DIR'] = tmp
        # МАРШРУТ ЕСТЬ ВСЕГДА (двадцать седьмой круг, №1): его пишет hand_over_book, а
        # пилот стартовал на Ф. Стенд без route.txt описывает состояние, которого нет.
        (Path(tmp) / 'route.txt').write_text('F', encoding='utf-8')
        os.environ['ADDFUT_REGISTRY'] = str(reg)
        os.environ.pop('ADDFUT_BOOK_PATH', None)
        # ПИН ТОРГОВОГО СЧЁТА (двадцатый круг, №5): контур обязан знать свой счёт, и
        # стенд даёт ровно тот, что отдаёт стаб — выдуманный проверял бы сам себя.
        os.environ.pop('ADDFUT_ACCOUNT', None)
        (Path(tmp) / 'account.txt').write_text(ib.managedAccounts()[0], encoding='utf-8')
        real_now = pd.Timestamp.now

        out = dict(case='позднее исполнение после сессии', first_ok=False, second_refused=False,
                   second_traded=False, error='')
        ib.set_bars(bars)
        FD.exchange_today = lambda: pd.Timestamp('2026-08-12')
        pd.Timestamp.now = staticmethod(
            lambda tz=None: pd.Timestamp('2026-08-12 10:00', tz=FD.EXCHANGE_TZ) if tz
            else real_now())
        try:
            SS.do_trade(ib, 'F', dry=False)
            out['first_ok'] = True
        except BaseException as ex:
            out['error'] = f'первая сессия: {type(ex).__name__}: {ex}'
        # ЗАМЫКАНИЕ
        b2 = {k: list(v) + [('2026-08-12', v[-1][1] * 1.002)] for k, v in bars.items()}
        ib.set_bars(b2)
        pd.Timestamp.now = staticmethod(
            lambda tz=None: pd.Timestamp('2026-08-12 17:00', tz=FD.EXCHANGE_TZ) if tz
            else real_now())
        try:
            SS.do_close(ib, 'F')
        except BaseException as ex:
            out['error'] += f' | замыкание: {type(ex).__name__}: {ex}'
        # ПОЗДНЕЕ ИСПОЛНЕНИЕ: отменённая заявка легла уже ПОСЛЕ того, как сессия завершена.
        ib._pos[zn] = ib._pos.get(zn, 0) + 3
        ib._shown = dict(ib._pos)
        # СЛЕДУЮЩАЯ СЕССИЯ
        b3 = {k: list(v) + [('2026-08-13', v[-1][1] * 1.001)] for k, v in b2.items()}
        ib.set_bars(b3)
        FD.exchange_today = lambda: pd.Timestamp('2026-08-13')
        pd.Timestamp.now = staticmethod(
            lambda tz=None: pd.Timestamp('2026-08-13 10:00', tz=FD.EXCHANGE_TZ) if tz
            else real_now())
        n_before = len(ib._trades)
        try:
            SS.do_trade(ib, 'F', dry=False)
            out['second_traded'] = len(ib._trades) > n_before
        except BaseException as ex:
            out['second_refused'] = True
            out['error'] += f' | вторая: {type(ex).__name__}: {str(ex)[:90]}'
            out['second_traded'] = len(ib._trades) > n_before
    finally:
        pd.Timestamp.now = real_now
        (ib_insync.Index, FD.registry, FD.signal_state, FD.exchange_today,
         d0, l0, r0, b0) = keep
        for k, v in (('ADDFUT_DIR', d0), ('ADDFUT_LOCK_DIR', l0),
                     ('ADDFUT_REGISTRY', r0), ('ADDFUT_BOOK_PATH', b0)):
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    return out


def _session_race():
    """ПЕРЕХОД МЕЖДУ ЧТЕНИЕМ КНИГИ И ЕЁ ЗАМЫКАНИЕМ.

    Гонка воспроизводится ДЕТЕРМИНИРОВАННО: книга подменяется ровно в окне между чтением и
    записью — там, где переходный исполнитель успевал перевести маршрут, а замыкание
    затирало его старой книгой (потерянная запись при фактической позиции уже на другом
    маршруте). Многопоточность здесь не нужна и вредна: окно известно точно, а плавающий
    тест доказывал бы меньше, чем воспроизводимый.
    """
    import feed as FD
    import state as ST
    from dataclasses import replace

    orig = FD.closing_values
    marker = {}

    def racing(ib, route, book):
        # ВНУТРИ ОКНА: другой процесс дописал книгу и освободил замок.
        res = orig(ib, route, book)
        bp = ST.book_path(route)
        b, sess, r = ST.load(bp, type(book))
        ST.save(bp, replace(b, n_b=(b.n_b + 7)), r, sess + 1,
                note='подмена книги другим процессом внутри окна замыкания')
        marker['подменено'] = True
        return res

    FD.closing_values = racing
    try:
        out = _session_run('замыкание')
    finally:
        FD.closing_values = orig
    out['case'] = 'гонка при замыкании'
    out['подменено'] = marker.get('подменено', False)
    return out


# ИСТОЧНИК ДЛЯ ДОЧЕРНИХ ПРОЦЕССОВ СТЕНДА ЗАМКА — ВЕЛИЧИНОЙ (сорок четвёртый круг, №9).
# Оба участника гонки обязаны исполнять ОДИН И ТОТ ЖЕ код: пока держатель жил в родителе,
# мутация замка меняла только его, дочерний брал неизменённый — и «поймана» получалось из
# рассогласования стенда, а не из проверяемого свойства. Величина даёт мутации одну ручку.
LOCK_SRC = str(Path(__file__).resolve().parent)


def _session_lock():
    """ЗАМОК ОБЩИЙ МЕЖДУ ПРОЦЕССАМИ — доказательство отдельным процессом, а не пересказом.

    Восьмая рецензия права: прежний сценарий «гонки» проверял защиту от записи МИМО замка
    (второе чтение внутри do_close), но не сам замок между процессами. Здесь родительский
    процесс держит hold_book_lock, дочерний с тем же ADDFUT_LOCK_DIR пробует взять его с
    секундным ожиданием и ОБЯЗАН отказать; после освобождения — обязан пройти."""
    import os
    import subprocess
    import sys as _sys
    import tempfile
    tmp = tempfile.mkdtemp(prefix='addfut-lock-')
    env = dict(os.environ, ADDFUT_LOCK_DIR=tmp)
    out = dict(case='замок между процессами', held='', freed='', raised=False)
    # ДЕРЖАТЕЛЬ — ТОЖЕ ОТДЕЛЬНЫЙ ПРОЦЕСС (сорок четвёртый круг, №9): он берёт замок, говорит
    # ГОТОВ и ждёт строки на stdin, не отпуская. Так обе стороны исполняют один источник.
    holder_src = ('import sys; sys.path.insert(0, %r); import state as ST\n'
                  'with ST.hold_book_lock(timeout_s=5):\n'
                  '    print("ГОТОВ", flush=True)\n'
                  '    sys.stdin.readline()\n' % LOCK_SRC)
    child = [_sys.executable, '-c',
             'import sys; sys.path.insert(0, %r); import state as ST\n'
             'try:\n'
             '    with ST.hold_book_lock(timeout_s=1):\n'
             '        print("ВЗЯЛ")\n'
             'except RuntimeError:\n'
             '    print("ЗАНЯТО")' % LOCK_SRC]
    keep_env = os.environ.get('ADDFUT_LOCK_DIR')
    holder = None
    try:
        os.environ['ADDFUT_LOCK_DIR'] = tmp
        # МАРШРУТ ЕСТЬ ВСЕГДА (двадцать седьмой круг, №1): его пишет hand_over_book, а
        # пилот стартовал на Ф. Стенд без route.txt описывает состояние, которого нет.
        (Path(tmp) / 'route.txt').write_text('F', encoding='utf-8')
        holder = subprocess.Popen([_sys.executable, '-c', holder_src], env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        out['держатель'] = (holder.stdout.readline() or '').strip()
        r1 = subprocess.run(child, env=env, capture_output=True, text=True, timeout=30)
        out['held'] = (r1.stdout or '').strip()
        # ПОДМЕНА ФАЙЛА ЗАМКА ПОД ДЕРЖАТЕЛЕМ (тот же №9). flock запирает inode, а не имя:
        # восстановление каталога из копии, unlink или атомарная замена давали второму
        # процессу НОВЫЙ inode — он честно брал СВОЙ замок, и оба шли подавать заявки по
        # одной книге. Держатель на месте, файл заменён — чужой обязан получить ЗАНЯТО.
        import state as _STl
        (Path(tmp) / '.подмена').write_text('чужой', encoding='utf-8')
        os.replace(Path(tmp) / '.подмена', Path(tmp) / _STl.LOCK_NAME)
        r1b = subprocess.run(child, env=env, capture_output=True, text=True, timeout=30)
        out['held_after_swap'] = (r1b.stdout or '').strip()
        holder.stdin.write('\n'); holder.stdin.flush()
        holder.wait(timeout=30); holder = None
        r2 = subprocess.run(child, env=env, capture_output=True, text=True, timeout=30)
        out['freed'] = (r2.stdout or '').strip()
    except Exception as ex:
        out['raised'] = True; out['error'] = f'{type(ex).__name__}: {ex}'
    finally:
        if holder is not None:
            holder.kill()
        os.environ.pop('ADDFUT_LOCK_DIR', None)
        if keep_env is not None:
            os.environ['ADDFUT_LOCK_DIR'] = keep_env
            (Path(keep_env) / 'route.txt').write_text('F', encoding='utf-8')
    return out


def _session_statedir():
    """ОДИН NAMESPACE ПУТЕЙ (девятнадцатый круг, №17): без ADDFUT_DIR каталог журнала и
    тревог обязан совпасть с каталогом ЗАМКА И КНИГИ (state.lock_dir), а не жить своей
    жизнью в ~/.addfut — иначе тревога О-3-Е пишется туда, где её никто не ищет."""
    import os
    import tempfile
    import session as SS
    tmp = tempfile.mkdtemp(prefix='addfut-ns-')
    keep = (os.environ.get('ADDFUT_DIR'), os.environ.get('ADDFUT_LOCK_DIR'))
    out = dict(case='пути состояния: один namespace', raised=False, ok=False, error='')
    try:
        os.environ.pop('ADDFUT_DIR', None)
        os.environ['ADDFUT_LOCK_DIR'] = tmp
        # МАРШРУТ ЕСТЬ ВСЕГДА (двадцать седьмой круг, №1): его пишет hand_over_book, а
        # пилот стартовал на Ф. Стенд без route.txt описывает состояние, которого нет.
        (Path(tmp) / 'route.txt').write_text('F', encoding='utf-8')
        out['ok'] = str(SS.state_dir()) == tmp
    except Exception as ex:
        out['raised'] = True; out['error'] = f'{type(ex).__name__}: {ex}'
    finally:
        for k, v in (('ADDFUT_DIR', keep[0]), ('ADDFUT_LOCK_DIR', keep[1])):
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    return out



def _autopilot_case(kind):
    """ШЕЛЛОВЫЙ СЛОЙ ТОЖЕ ПРОВЕРЯЕТСЯ (инцидент 19.08.2026, §12).

    Ветка run_close «копия не снята» затирала тревогу, которую backup_state уже написал с
    ПРИЧИНОЙ: alarm_write открывает файл на запись. Причина нигде больше не живёт — в
    журнал она попадает только при УСПЕХЕ, — поэтому разбор инцидента 18.08 начался с
    воспроизведения того, что уже было напечатано и стёрто.

    Стенд гоняет НАСТОЯЩУЮ функцию из настоящего файла: скрипт сорсится (ветка `*)` зовёт
    exit, поэтому exit на время сорсинга подменён функцией), границы подставлены — вместо
    питона исполняемая заглушка, вместо backup_state заглушка, которая ведёт себя как
    боевая: пишет причину в ALARM-backup-ДЕНЬ и отказывает. Проверяется то, что случилось
    в бою: причина обязана пережить общее сообщение."""
    import os
    import subprocess
    import tempfile
    out = dict(case=kind, raised=False, error='', ok=False)
    tmp = tempfile.mkdtemp(prefix='addfut-autopilot-')
    # ОБВЯЗКА СОРСИНГА — ОДНИМ ШАБЛОНОМ (рецензия 19.08, угол «упрощение»): она была
    # скопирована в оба стенда, а стендов будет больше. Когда у autopilot.sh появится новый
    # побочный эффект при сорсинге и понадобится ещё одна заглушка рядом с exit, её добавят
    # в один экземпляр из трёх — остальные начнут сорсить полуинициализированный скрипт и
    # падать не по проверяемой причине.
    _PREFIX = r"""
set -u -o pipefail
export HOME=%(tmp)s
mkdir -p "$HOME/.addfut"
exit() { return 0; }                 # ветка `*)` боевого dispatch не должна убить сорсинг
source %(sh)s __стенд__ >/dev/null 2>&1
unset -f exit
""" % dict(tmp=tmp, sh=str(AUTOPILOT_SH))
    if kind == 'автопилот: пустая книга Е не считается слепотой':
        # СОРОК ПЯТЫЙ КРУГ, №6 (P1) + разбор /code-review. margin_cushion() законно отдаёт
        # None при ОБЕИХ выключенных ногах; прежде shell-вахта не отличала это от неполной
        # сводки, и маршрут Е с НУЛЕВОЙ позицией сам себя останавливал через три тика.
        #
        # СТЕНД ИСПОЛНЯЕТ БОЕВУЮ ВЕТКУ, А НЕ ПЕРЕПИСЫВАЕТ ЕЁ (правило 8а(в), зонд
        # достижимости). Первая редакция этого стенда кормила литералами одну лишь функцию
        # verdict() и собственный `case`, то есть тело боевой ветки не исполнялось НИ РАЗУ —
        # и не заметило, что ветка EMPTY обнуляла o3e-intraday-fail-$day, имя, которого во
        # всём скрипте больше нет: счётчик слепоты не обнулялся вовсе. Теперь блок `case`
        # ВЫРЕЗАЕТСЯ ИЗ ФАЙЛА и исполняется как есть; подменены только внешние действия.
        _sh_txt = AUTOPILOT_SH.read_text(encoding='utf-8')
        _mark = '_cw=$(o3e_probe 94)'
        _i0 = _sh_txt.index(_mark)
        _i1 = _sh_txt.index('        case "$(verdict "$_cw")" in', _i0)
        _i2 = _sh_txt.index('\n        esac', _i1) + len('\n        esac')
        _case = _sh_txt[_i1:_i2]
        out['ветка_вырезана'] = ('EMPTY' in _case and 'o3e-watch-fail' in _case
                                 and 'session.py --o3e' in _case)
        _drv = ('%(pre)s\n'
                'day=2026-08-21\n'
                'alarm_write() { echo "ALARM $1" >> "$HOME/следы"; return 0; }\n'
                'log() { echo "LOG $*" >> "$HOME/следы"; }\n'
                'chicago() { echo 0900; }\n'
                'trade_till() { echo 1530; }\n'
                'LIVE=$HOME; LOG=$HOME/лог\n'
                'PY=$HOME/py-заглушка\n'
                "printf '%%s\\n' '#!/bin/sh' 'echo книга-СОКРАЩЕНА-в-ту-же-сессию' > \"$PY\"\n"
                'chmod +x "$PY"\n'
                'вахта() { local _cw="$1"\n%(case)s\n}\n'
                'счёт() { counter_read "$ST/o3e-watch-fail-$day"; }\n'
                'вахта "ADDFUT-VERDICT SKIP запаса нет (сводка неполна)"; echo "СБОЙ1=$(счёт)"\n'
                'вахта "ADDFUT-VERDICT SKIP запаса нет (сводка неполна)"; echo "СБОЙ2=$(счёт)"\n'
                'вахта "ADDFUT-VERDICT EMPTY книга пуста — сокращать нечего"; echo "ПОСЛЕ-ПУСТОЙ=$(счёт)"\n'
                'вахта "ADDFUT-VERDICT SKIP запаса нет (сводка неполна)"; echo "СНОВА=$(счёт)"\n'
                'вахта "ADDFUT-VERDICT LOW 1.200"\n'
                'grep -c "ALARM" "$HOME/следы" 2>/dev/null | sed "s/^/ТРЕВОГ=/"\n'
                'grep -q "книга-СОКРАЩЕНА" "$HOME/лог" && echo "СРЕЗ=да" || echo "СРЕЗ=нет"\n'
                ) % dict(pre=_PREFIX, case=_case)
        try:
            r6 = subprocess.run(['bash', '-c', _drv], capture_output=True, text=True,
                                cwd=str(ROOT), timeout=120)
            t6 = r6.stdout
            out['вывод'] = (t6.strip() + ' | ' + r6.stderr.strip())[:400]
            # Пустая книга ОБНУЛЯЕТ тот самый счётчик, который наращивает ветка `*)`:
            # два сбоя дают 2, пустая книга — 0, следующий сбой снова 1, а не 3.
            out['сбой_считается'] = ('СБОЙ1=1' in t6 and 'СБОЙ2=2' in t6)
            out['пустая_обнуляет'] = 'ПОСЛЕ-ПУСТОЙ=0' in t6
            out['счёт_начат_заново'] = 'СНОВА=1' in t6
            out['низкий_режет'] = 'СРЕЗ=да' in t6
            out['пустая_не_тревожит'] = 'ТРЕВОГ=1' in t6      # ровно одна — от ветки LOW
            out['ok'] = all([out['ветка_вырезана'], out['сбой_считается'],
                             out['пустая_обнуляет'], out['счёт_начат_заново'],
                             out['низкий_режет'], out['пустая_не_тревожит']])
        except Exception as ex:
            out['raised'] = True
            out['error'] = f'{type(ex).__name__}: {ex}'
        return out
    if kind == 'автопилот: вердикт вахты читается сквозь шум шлюза':
        # СОРОК ПЯТЫЙ КРУГ, №1 (P0). Пробы идут с 2>&1 — и правильно: ответы шлюза нужны
        # в журнале (урок 18.08). Но ib_insync пишет туда же свои строки («Error 2104 …
        # farm connection is OK»), они встают ПЕРЕД маркером, и разбор «весь ответ
        # начинается с LOW » уходил в `*)`: запас пробит, а предписанный §8 срез в ту же
        # сессию НЕ запускался — только счётчик слепоты и тревога через три тика, когда
        # сокращение уже пропущено.
        # Четыре точки: вердикт читается сквозь шум; при нескольких метках берётся
        # ПОСЛЕДНЯЯ (повторный замер сильнее раннего); отсутствие метки не выдаётся за
        # вердикт (иначе fail-open); прежний разбор на том же ответе ПРОМАХИВАЕТСЯ — без
        # этого стенд доказывал бы, что «и так работало».
        prog1 = _PREFIX + r"""
noise=$(printf 'Error 2104 Market data farm connection is OK:usfarm\nADDFUT-VERDICT LOW 1.200')
v=$(verdict "$noise")
case "$v" in LOW\ *) echo "СКВОЗЬ-ШУМ=да" ;; *) echo "СКВОЗЬ-ШУМ=нет" ;; esac
two=$(printf 'ADDFUT-VERDICT OK 1.900\nADDFUT-VERDICT LOW 1.200')
case "$(verdict "$two")" in LOW\ *) echo "ПОСЛЕДНЯЯ=да" ;; *) echo "ПОСЛЕДНЯЯ=нет" ;; esac
[ -z "$(verdict 'Error 502 Couldnt connect to TWS')" ] && echo "БЕЗ-МЕТКИ=пусто" || echo "БЕЗ-МЕТКИ=вердикт"
case "$noise" in LOW\ *) echo "ПРЕЖНИЙ-РАЗБОР=ловит" ;; *) echo "ПРЕЖНИЙ-РАЗБОР=промах" ;; esac
"""
        try:
            r1 = subprocess.run(['bash', '-c', prog1], capture_output=True, text=True,
                                cwd=str(ROOT), timeout=120)
            t1 = r1.stdout
            out['вывод'] = t1.strip()[:300]
            out['сквозь_шум'] = 'СКВОЗЬ-ШУМ=да' in t1
            out['последняя_метка'] = 'ПОСЛЕДНЯЯ=да' in t1
            out['без_метки_пусто'] = 'БЕЗ-МЕТКИ=пусто' in t1
            out['прежний_промахивался'] = 'ПРЕЖНИЙ-РАЗБОР=промах' in t1
            out['ok'] = all([out['сквозь_шум'], out['последняя_метка'],
                             out['без_метки_пусто'], out['прежний_промахивался']])
        except Exception as ex:
            out['raised'] = True
            out['error'] = f'{type(ex).__name__}: {ex}'
        return out
    if kind == 'автопилот: возраст сердцебиения строг':
        # СОРОК ЧЕТВЁРТЫЙ КРУГ, №12. Сторож занятого замка при пустой или нечисловой
        # отметке снова брал mtime — изменяемую величину, ради ухода от которой правка и
        # делалась: touch или восстановление каталога из копии делают ЗАВИСШЕЕ сердцебиение
        # «свежим», сторож молчит, контур слеп, ролл пропущен. Метка из будущего была ещё
        # хуже: отрицательный возраст обнулялся, и тревога не наступала вовсе.
        # Проверяются ЧЕТЫРЕ точки: живая отметка даёт возраст (иначе стенд доказывал бы
        # «всегда ломается»), пустая и нечисловая — поломку, будущая — поломку. Плюс
        # атомарность записи: без неё строгость чтения давала бы ложные тревоги на гонке.
        prog12 = _PREFIX + r"""
f=$ST/проба
hb_write "$f" >/dev/null 2>&1
if v=$(hb_age "$f"); then echo "ЖИВАЯ=$v"; else echo "ЖИВАЯ-ПОЛОМКА=$v"; fi
printf '' > "$f"
if v=$(hb_age "$f"); then echo "ПУСТАЯ=$v"; else echo "ПУСТАЯ-ПОЛОМКА"; fi
printf 'мусор' > "$f"
if v=$(hb_age "$f"); then echo "МУСОР=$v"; else echo "МУСОР-ПОЛОМКА"; fi
touch "$f"                     # mtime свежий, содержимое по-прежнему негодное
if v=$(hb_age "$f"); then echo "ПОСЛЕ-TOUCH=$v"; else echo "TOUCH-ПОЛОМКА"; fi
printf '%s' "$(( $(date +%s) + 5000 ))" > "$f"
if v=$(hb_age "$f"); then echo "БУДУЩЕЕ=$v"; else echo "БУДУЩЕЕ-ПОЛОМКА"; fi
printf '%s' "$(( $(date +%s) - 7200 ))" > "$f"
if v=$(hb_age "$f"); then echo "СТАРАЯ=$v"; else echo "СТАРАЯ-ПОЛОМКА"; fi
hb_write "$f" >/dev/null 2>&1
echo "ВРЕМЕННЫХ=$(ls "$ST" | grep -c 'tmp' || true)"
"""
        try:
            r12 = subprocess.run(['bash', '-c', prog12], capture_output=True, text=True,
                                 cwd=str(ROOT), timeout=120)
            t12 = r12.stdout
            out['вывод'] = t12.strip()[:400]
            out['живая_читается'] = 'ЖИВАЯ=' in t12
            out['пустая_поломка'] = 'ПУСТАЯ-ПОЛОМКА' in t12
            out['мусор_поломка'] = 'МУСОР-ПОЛОМКА' in t12
            out['touch_не_лечит'] = 'TOUCH-ПОЛОМКА' in t12
            out['будущее_поломка'] = 'БУДУЩЕЕ-ПОЛОМКА' in t12
            out['старая_считается'] = 'СТАРАЯ=7200' in t12 or 'СТАРАЯ=7201' in t12
            out['без_временных'] = 'ВРЕМЕННЫХ=0' in t12
            out['ok'] = all([out['живая_читается'], out['пустая_поломка'],
                             out['мусор_поломка'], out['touch_не_лечит'],
                             out['будущее_поломка'], out['старая_считается'],
                             out['без_временных']])
        except Exception as ex:
            out['raised'] = True
            out['error'] = f'{type(ex).__name__}: {ex}'
        return out
    prog = _PREFIX + r"""
echo F > "$ST/route.txt"
cat > "$HOME/fakepy" <<'FP'
#!/bin/sh
echo "замкнуто: NLV закрытия 1"
FP
chmod +x "$HOME/fakepy"
PY=$HOME/fakepy                      # граница с питоном подставлена
backup_state() {                     # ведёт себя как боевая: пишет ПРИЧИНУ и отказывает
    alarm_write "$ST/ALARM-backup-$1.txt" "снимок состояния/WORM за $1 не создан: ПРИЧИНА-ЗОНДА"
    return 1
}
run_close 2026-08-18 >/dev/null 2>&1
echo "RC=$?"
echo "ФАЙЛ-НАЧАЛО"
cat "$ST/ALARM-backup-2026-08-18.txt" 2>/dev/null
echo "ФАЙЛ-КОНЕЦ"
[ -e "$ST/closed-2026-08-18" ] && echo "ОТМЕТКА=есть" || echo "ОТМЕТКА=нет"
"""
    try:
        r = subprocess.run(['bash', '-c', prog], capture_output=True, text=True,
                           cwd=str(ROOT), timeout=120)
        txt = r.stdout
        body = txt.split('ФАЙЛ-НАЧАЛО', 1)[1].split('ФАЙЛ-КОНЕЦ', 1)[0] if 'ФАЙЛ-НАЧАЛО' in txt else ''
        out['вывод'] = txt.strip()[:400]
        out['причина_жива'] = 'ПРИЧИНА-ЗОНДА' in body
        out['общее_сказано'] = 'не ставится (О-5)' in body
        # ЗОНД ДОСТИЖИМОСТИ: без входа в саму ветку оба признака ничего не значат — ветка
        # выполняется только когда run_close дошёл до backup_state и получил отказ.
        out['ветка_пройдена'] = 'RC=1' in txt and out['общее_сказано']
        out['день_не_закрыт'] = 'ОТМЕТКА=нет' in txt
        out['ok'] = bool(out['причина_жива'] and out['ветка_пройдена']
                         and out['день_не_закрыт'])
    except Exception as ex:
        out['raised'] = True
        out['error'] = f'{type(ex).__name__}: {ex}'
    return out


# ------------------------------------------------------------------ правила 45-го круга
# ПРАВИЛА, ЗАВЕДЁННЫЕ РАЗБОРОМ /code-review, ПРОВЕРЯЮТСЯ ПООТДЕЛЬНОСТИ. Рецензия показала
# восемь новых защит без единой парной мутации: их можно было выкинуть целиком, и батарея
# осталась бы зелёной. Каждый случай ниже трогает ПРОИЗВОДСТВЕННУЮ функцию (не копию
# правила), проверяет ОБА конца (законное проходит, незаконное отвергается) и назван так,
# чтобы отказ читался без чтения кода. Машинное состояние не трогается: всё под mkdtemp.
def _rules45_case(kind):
    import os as _os45
    import tempfile as _tf45
    out = dict(case=kind, raised=False, error='', ok=False,
               placed=0, positions={}, saved=None, provisional=None, lev=None,
               journal=Path('/nonexistent'))
    try:
        if kind == 'правила45: допуск бара снимается известным календарём':
            # Плоские пять дней действуют, только когда календарь НЕИЗВЕСТЕН ВОВСЕ. Переход
            # полосы фонда на min_prev вернул отказ маршрута Е на 29.12.2026 (разрыв 6 дней
            # при допуске 5) — то есть условие смотрело лишь на expected_prev.
            import pandas as _pd45
            import feed as _FD45
            _t = _pd45.Timestamp('2026-12-29')
            _prev = _FD45.prev_session(_t, _FD45.eu_holidays(2026))
            _df = _pd45.DataFrame({'date': [_pd45.Timestamp('2026-12-22'),
                                            _pd45.Timestamp('2026-12-23')],
                                   'close': [690.0, 700.0]})
            _old = _FD45._bars
            class _C45:
                symbol = 'CSPX'; localSymbol = 'CSPX'
            def _try(**kw):
                try:
                    return _FD45.closes(None, _C45(), _t, **kw)[0], ''
                except Exception as _e:
                    return None, f'{type(_e).__name__}: {_e}'
            try:
                _FD45._bars = lambda ib, c, days=10: _df
                out['разрыв_дней'] = int((_t - _prev).days)
                out['ветка_достижима'] = out['разрыв_дней'] > int(_FD45.MAX_BAR_GAP_D)
                _px, _e1 = _try(expected_prev=None, min_prev=_prev)
                out['граница_снимает_допуск'] = (_px == 700.0)
                _px2, _e2 = _try(expected_prev=None, min_prev=None)
                out['без_календаря_допуск_держит'] = (_px2 is None and 'STALE_BAR' in _e2)
                _df2 = _pd45.DataFrame({'date': [_pd45.Timestamp('2026-12-21'),
                                                 _pd45.Timestamp('2026-12-22')],
                                        'close': [680.0, 690.0]})
                _FD45._bars = lambda ib, c, days=10: _df2
                _px3, _e3 = _try(expected_prev=None, min_prev=_prev)
                out['старый_бар_отвергнут'] = (_px3 is None and 'СТАРШЕ' in _e3)
            finally:
                _FD45._bars = _old
            out['ok'] = all([out['ветка_достижима'], out['граница_снимает_допуск'],
                             out['без_календаря_допуск_держит'], out['старый_бар_отвергнут']])
        elif kind == 'правила45: остаток ниже допуска не заявка':
            # Точный ноль на разности float оставлял 5,55e-16 юнита, а цели маршрута Е от
            # округления освобождены: whatIf молчит, три POSTPONED и ABORT на завершённом
            # переходе. Второй конец обязателен: НАСТОЯЩИЙ остаток обязан дожить до заявки.
            import transition as _TR45
            _plan = [dict(src='ESU26', dst='CSPX', step=i, units=u, unit_usd=100.0,
                          dprice=10.0) for i, u in enumerate([0.1, 0.2, 0.3, 0.4])]
            _full = _TR45.pv_remainder(_plan, [], {'ESU26': sum(l['units'] for l in _plan)})
            _part = _TR45.pv_remainder(_plan, [], {'ESU26': 0.6})
            _none = _TR45.pv_remainder(_plan, [], None)
            # ДВА ИСТОЧНИКА, А НЕ ОДИН (разбор /code-review): книга Ф двуногая, и формула
            # «весь прогресс на один src» на одноисточниковом плане верна случайно.
            _two = [dict(src='ESU26', dst='CSPX', step=0, units=1.0, unit_usd=100.0, dprice=10.0),
                    dict(src='ZNU26', dst='CBU0', step=0, units=2.0, unit_usd=50.0, dprice=5.0)]
            _mix = _TR45.pv_remainder(_two, [], {'ESU26': 1.0})
            out['источники_не_смешиваются'] = (
                'CSPX' not in _mix and abs(_mix.get('CBU0', 0) - 20.0) < 1e-9)
            out['пыль_не_доходит'] = (_full == {})
            out['остаток_доходит'] = (abs(_part.get('CSPX', 0) - 4.0) < 1e-9)
            out['без_прогресса_весь_план'] = (abs(_none.get('CSPX', 0) - 10.0) < 1e-9)
            out['ok'] = all([out['пыль_не_доходит'], out['остаток_доходит'],
                             out['без_прогресса_весь_план'],
                             out['источники_не_смешиваются']])
        elif kind == 'правила45: беспланный предпросмотр':
            # Порог О-3-Е без плана обязан отвергать НЕЗАКОННЫЙ COMPLETE и пропускать два
            # законных пути: аварийный выход (он маржу освобождает) и завершение уже
            # исполненного (покупать нечего, отказ дал бы MIXED на целой книге).
            import ib_broker as _IB45
            import daily as _DL45
            def _pv(c, **kw):
                _b = _IB45.IBBroker.__new__(_IB45.IBBroker)
                _b.margin_cushion = lambda: c
                _r = _IB45.IBBroker.preview(_b, **kw)
                return bool(_r), str(getattr(_b, '_preview_pass_why', '') or '')
            _low = float(_DL45.O3E_MIN) - 0.2
            out['норма_проходит'] = _pv(float(_DL45.O3E_MIN) + 0.1)[0] is True
            out['низкий_отвергнут'] = _pv(_low)[0] is False
            _e_ok, _e_why = _pv(_low, emergency=True)
            out['авария_проходит'] = _e_ok is True and 'АВАРИЙНЫЙ' in _e_why
            _d_ok, _d_why = _pv(_low, done_all=True)
            out['исполненное_завершается'] = _d_ok is True and 'нечего' in _d_why
            out['пропуск_назван'] = bool(_e_why) and bool(_d_why)
            out['ok'] = all([out['норма_проходит'], out['низкий_отвергнут'],
                             out['авария_проходит'], out['исполненное_завершается'],
                             out['пропуск_назван']])
        elif kind == 'правила45: замок книги один на всех писателей':
            # При ручном ADDFUT_BOOK_PATH без ADDFUT_LOCK_DIR голый hold_book_lock() запирал
            # ~/.addfut, а сессия — каталог книги: два писателя одного файла под разными
            # flock. Правило живёт в УМОЛЧАНИИ, поэтому проверяется именно голый вызов —
            # но через _lock_target, то есть БЕЗ взятия замка и без отметки держателя:
            # иначе под мутацией стенд сам писал бы в машинный каталог (правило 5).
            import state as _ST45
            _bd = _tf45.mkdtemp(prefix='addfut-i45lock-')
            _keepb = _os45.environ.get('ADDFUT_BOOK_PATH')
            _keepl = _os45.environ.get('ADDFUT_LOCK_DIR')
            try:
                _os45.environ['ADDFUT_BOOK_PATH'] = _bp = _os45.path.join(_bd, 'book-F.json')
                _os45.environ.pop('ADDFUT_LOCK_DIR', None)
                _naked = str(_ST45._lock_target())                 # переход и WORM
                _named = str(_ST45._lock_target(_ST45.book_lock_dir(Path(_bp))))
                out['голый_и_явный_совпали'] = (_naked == _named)
                out['замок_у_книги'] = (_naked == _bd)
                # Изоляция стенда сильнее правила: заданный ADDFUT_LOCK_DIR обязан побеждать.
                _os45.environ['ADDFUT_LOCK_DIR'] = _bd2 = _tf45.mkdtemp(prefix='addfut-i45env-')
                out['окружение_сильнее'] = (str(_ST45._lock_target()) == _bd2)
                # И сам замок обязан работать — берём его во временном каталоге.
                with _ST45.hold_book_lock():
                    out['замок_берётся'] = (Path(_bd2) / _ST45.LOCK_NAME).exists()
            finally:
                for _k, _v in (('ADDFUT_BOOK_PATH', _keepb), ('ADDFUT_LOCK_DIR', _keepl)):
                    if _v is None:
                        _os45.environ.pop(_k, None)
                    else:
                        _os45.environ[_k] = _v
            out['ok'] = all([out['голый_и_явный_совпали'], out['замок_у_книги'],
                             out['окружение_сильнее'], out.get('замок_берётся') is True])
        elif kind == 'правила45: журнал не дописывается под мусорной шапкой':
            # Проверка заголовка стояла в read(), а append брал предыдущий хэш своим,
            # НЕЗАЩИЩЁННЫМ читателем — и дописывал строку под мусорной шапкой, начиная
            # цепочку заново от GENESIS. Второй конец: исправный журнал обязан принимать.
            import journal as _J45
            _d = _tf45.mkdtemp(prefix='addfut-i45j-')
            _row = {c: 'x' for c in _J45.BASE}
            def _app(text):
                _p = Path(_d) / f'journal-{abs(hash(text)) % 10**6}.csv'
                _p.write_text(text, encoding='utf-8')
                try:
                    _J45.append(_p, dict(_row)); return 'ДОПИСАЛ'
                except ValueError as _e:
                    return 'ОТКАЗ'
                except Exception as _e:
                    return f'{type(_e).__name__}'
            out['мусорная_шапка_отвергнута'] = _app('мусорная,шапка\n') == 'ОТКАЗ'
            out['лишний_столбец_отвергнут'] = _app(','.join(_J45.COLS) + ',лишний\n') == 'ОТКАЗ'
            out['нулевой_принят'] = _app('') == 'ДОПИСАЛ'
            out['исправный_принят'] = _app(','.join(_J45.COLS) + '\n') == 'ДОПИСАЛ'
            out['ok'] = all([out['мусорная_шапка_отвергнута'], out['лишний_столбец_отвергнут'],
                             out['нулевой_принят'], out['исправный_принят']])
        elif kind == 'правила45: диагност различает капитал и маржу':
            # ТЕКСТЫ БЕРУТСЯ У ПРОИЗВОДИТЕЛЕЙ. Тело тревоги в бою — ВЕСЬ вывод сессии,
            # поэтому здоровая строка про запас стояла рядом с отказом по капиталу и
            # отменяла верный диагноз, а общий якорь «О-3-Е» дублировал частный.
            import diagnose as _DG45
            _kap = ('NLV 2,999,999 ниже порога маршрута Ф 3,000,000 (§8) — '
                    'торговля остановлена')
            _zdor = ('О-3-Е ВНУТРИДНЕВНАЯ ВАХТА: запас 2.10x не ниже 1.40 — '
                     'сокращение не требуется')
            _niz = ('О-3-Е ПОСЛЕ ИСПОЛНЕНИЙ: запас 1.28x ниже 1.40 — книга сокращена '
                    'по нормативу §8')
            _ned = 'запас О-3-Е недоступен при существующих позициях'
            def _causes(body):
                return [c for sig, c, _t in _DG45.SIGNS
                        if (sig(body) if callable(sig) else sig in body)]
            _c1 = _causes('\n'.join([_kap, _zdor]))
            out['капитал_виден_рядом_со_здоровым'] = any('капитал ниже' in c for c in _c1)
            out['здоровый_не_даёт_маржи'] = not any('ниже норматива О-3-Е' in c for c in _c1)
            out['маржа_видна'] = any('ниже норматива О-3-Е' in c for c in _causes(_niz))
            _c4 = _causes(_ned)
            out['недоступность_не_дублируется'] = (
                any('не отдаёт живой запас' in c for c in _c4)
                and not any('ниже норматива О-3-Е' in c for c in _c4))
            out['ok'] = all([out['капитал_виден_рядом_со_здоровым'],
                             out['здоровый_не_даёт_маржи'], out['маржа_видна'],
                             out['недоступность_не_дублируется']])
        elif kind == 'правила45: пара реестра и замера сверяется':
            # Якорь хэшировал оба файла порознь и заверял НЕСОВМЕСТИМУЮ пару. Область
            # сверки — FUT (замеряет их только first_connect), направления — оба.
            import csv as _csv45
            import json as _js45
            import worm_anchor as _WA45
            _d = Path(_tf45.mkdtemp(prefix='addfut-i45w-'))
            def _pair(rows, meta):
                _r = _d / f'r{abs(hash(str(rows)+str(meta))) % 10**6}.csv'
                _m = _r.with_suffix('.json')
                with open(_r, 'w', newline='', encoding='utf-8') as _f:
                    _w = _csv45.DictWriter(_f, fieldnames=['instrument', 'sec_type', 'con_id'])
                    _w.writeheader()
                    for _x in rows:
                        _w.writerow(_x)
                _m.write_text(_js45.dumps(meta, ensure_ascii=False), encoding='utf-8')
                return _WA45._registry_margins_mismatch(str(_r), str(_m))
            _fut = [dict(instrument='ESU26', sec_type='FUT', con_id='1'),
                    dict(instrument='ZNU26', sec_type='FUT', con_id='2')]
            _etf = [dict(instrument='CSPX', sec_type='STK', con_id='9')]
            _ok = {'_meta': dict(series=['ESU26', 'ZNU26'],
                                 con_ids={'ESU26': '1', 'ZNU26': '2'})}
            out['согласованное_молчит'] = (_pair(_fut, _ok) == '')
            out['фонды_не_мешают'] = (_pair(_fut + _etf, _ok) == '')
            out['реестр_новее_виден'] = bool(_pair(
                [dict(instrument='ESZ26', sec_type='FUT', con_id='3')], _ok))
            out['замер_новее_виден'] = bool(_pair([_fut[0]], _ok))
            out['con_id_виден'] = bool(_pair(_fut, {'_meta': dict(
                series=['ESU26', 'ZNU26'], con_ids={'ESU26': '99', 'ZNU26': '2'})}))
            out['форма_не_роняет'] = ('сверка невозможна' in _pair(_fut, {'_meta': 5})
                                      or 'форму' in _pair(_fut, {'_meta': 5}))
            out['ok'] = all([out['согласованное_молчит'], out['фонды_не_мешают'],
                             out['реестр_новее_виден'], out['замер_новее_виден'],
                             out['con_id_виден'], out['форма_не_роняет']])
        elif kind == 'правила45: dref кэшируется только закреплённым':
            # Кэш сбрасывался ТОЛЬКО в начале gross(), а unit_ref зовут и мимо него: живая
            # доходность залипала на весь век объекта брокера и выглядела исправной.
            import ib_broker as _IB45c
            import feed as _FD45c
            import pandas as _pd45c
            _n = {'i': 0}
            _orig = _FD45c.yield_pct
            try:
                def _fake(ib, today, expected_prev=None):
                    _n['i'] += 1
                    return (4.0 + 0.5 * _n['i'], None)
                _FD45c.yield_pct = _fake
                _b = _IB45c.IBBroker.__new__(_IB45c.IBBroker)
                _b.ib = None; _b._dref_cache = None
                _t = _pd45c.Timestamp('2026-08-21'); _p = _pd45c.Timestamp('2026-08-20')
                _a1 = _b._dref_once(_t, _p); _a2 = _b._dref_once(_t, _p)
                out['закреплённое_читается_раз'] = (_n['i'] == 1 and _a1 == _a2)
                _n['i'] = 0; _b._dref_cache = None
                _l1 = _b._dref_once(_t, None); _l2 = _b._dref_once(_t, None)
                out['живое_не_залипает'] = (_n['i'] == 2 and _l1 != _l2)
                out['живое_не_пишет_кэш'] = (_b._dref_cache is None)
            finally:
                _FD45c.yield_pct = _orig
            out['ok'] = all([out['закреплённое_читается_раз'], out['живое_не_залипает'],
                             out['живое_не_пишет_кэш']])
        elif kind == 'правила45: предполёт передачи не переодевает ошибку кода':
            # Запрет 45-й круг поставил на ВЫЗЫВАЮЩЕГО (_execute_locked), а переодевание
            # случается ВНУТРИ _preflight_handover: до внешнего except доходит уже
            # RuntimeError. Пропуск CODE_ERRORS поставлен механически на каждый широкий
            # перехват функции — стенд проверяет, что ошибка кода доходит СВОИМ типом, а
            # доменный отказ по-прежнему остаётся доменным.
            import sys as _sy45
            _lv = str(ROOT)
            if _lv not in _sy45.path:
                _sy45.path.insert(0, _lv)
            import transition as _TRp
            import daily as _DLp45
            _keep = _DLp45.roll_deadline
            try:
                def _boom(*a, **k):
                    raise AttributeError('опечатка в календаре ролла')
                _DLp45.roll_deadline = _boom
                try:
                    _TRp._preflight_handover('E', 'F', _dst_names=('ESU26', 'ZNU26'))
                    _got = None
                except AttributeError:
                    _got = 'AttributeError'
                except Exception as _e:
                    _got = type(_e).__name__
                out['ошибка_кода_доходит'] = (_got == 'AttributeError')
                out['ветка_достижима'] = (_got is not None)
            finally:
                _DLp45.roll_deadline = _keep
            out['ok'] = all([out['ошибка_кода_доходит'], out['ветка_достижима']])
        elif kind == 'правила45: ошибка кода не переодевается календарём':
            # daily.py не ссылался на CODE_ERRORS ни строкой, поэтому парная мутация
            # «запрет переодевания снят» до него не доставала, а О-5 начинался бы с
            # поставочного риска вместо трассировки.
            import daily as _DL45e
            _orig = _DL45e.roll_deadline
            try:
                def _raise(exc):
                    def _f(*a, **k):
                        raise exc
                    return _f
                _DL45e.roll_deadline = _raise(AttributeError('опечатка'))
                try:
                    _DL45e._roll_deadline_or_stop('ZNU26', (), 'срок ролла')
                    _got = None
                except Exception as _e:
                    _got = type(_e)
                out['ошибка_кода_своим_типом'] = (_got is AttributeError)
                _DL45e.roll_deadline = _raise(ValueError('нет таблицы праздников'))
                try:
                    _DL45e._roll_deadline_or_stop('ZNU26', (), 'срок ролла')
                    _got2 = None
                except Exception as _e:
                    _got2 = type(_e)
                out['доменная_остановка_названа'] = (_got2 is RuntimeError)
            finally:
                _DL45e.roll_deadline = _orig
            out['ok'] = all([out['ошибка_кода_своим_типом'],
                             out['доменная_остановка_названа']])
        else:
            out['error'] = f'неизвестный случай {kind}'
    except Exception as ex:
        out['raised'] = True
        out['error'] = f'{type(ex).__name__}: {ex}'
    return out


def _worm_case(kind):
    """Стенды WORM-якоря (девятнадцатый круг, №18/№19): обязательные файлы, действующие
    пути, blob-сверка HEAD. Всё — на временных каталогах и временном git-репозитории;
    машинное состояние не читается и не пишется."""
    import os
    import subprocess
    import tempfile
    import hashlib as _hl
    import state as ST
    import worm_anchor as WA
    import journal as JJ
    import ib_stub
    out = dict(case=kind, raised=False, error='', ok=False)
    tmp = tempfile.mkdtemp(prefix='addfut-worm-')
    keep = {k: os.environ.get(k) for k in
            ('ADDFUT_LOCK_DIR', 'ADDFUT_SIGNALS', 'ADDFUT_REGISTRY',
             'ADDFUT_BOOK_PATH', 'ADDFUT_DIR', 'ADDFUT_MARGINS')}
    try:
        os.environ['ADDFUT_LOCK_DIR'] = tmp
        # МАРШРУТ ЕСТЬ ВСЕГДА (двадцать седьмой круг, №1): его пишет hand_over_book, а
        # пилот стартовал на Ф. Стенд без route.txt описывает состояние, которого нет.
        (Path(tmp) / 'route.txt').write_text('F', encoding='utf-8')
        for k in ('ADDFUT_BOOK_PATH', 'ADDFUT_DIR'):
            os.environ.pop(k, None)
        os.environ['ADDFUT_REGISTRY'] = str(ib_stub.fixture_registry(tmp))
        # ЗАМЕР МАРЖИ — В ФИКСТУРЕ, А НЕ ПУСТЫМ ПУТЁМ (сорок четвёртый круг, №13). С этого
        # круга обязательность файла берётся из ИСТОРИИ ЯКОРЕЙ: раз машина уже заверяла
        # замер, его отсутствие — утрата, а не молодость контура. Стенд, подставлявший
        # несуществующий путь, описывал состояние, которого на этой машине не бывает,
        # и падал по существу. Кладём настоящий замер, построенный из той же фикстуры
        # реестра; случай «утрата заверенного замера» задаёт свой путь сам.
        os.environ['ADDFUT_MARGINS'] = str(ib_stub.fixture_margins(tmp))
        os.environ['ADDFUT_SIGNALS'] = str(Path(tmp) / 'signals_live.csv')
        b0 = DL.Book(d_fix=8.0, n_e=26, n_b=10, unit_is_mes=True, prev_st_eq=True,
                     prev_st_bd=True, ser_a='U26', ser_b='U26', es_held=2,
                     last_session='2026-08-13', close_provisional=False)
        import state as STm
        STm.save(STm.book_path('F'), b0, 'F', 5)
        (Path(tmp) / 'route.txt').write_text('F', encoding='utf-8')
        JJ.append(Path(tmp) / 'journal-F.csv', dict(
            date='2026-08-13', leg='', instrument='ИТОГ', qty=0, px_order='-', px_fill='',
            commission='', reason='', nav='', leverage='', roll_spread_near='',
            roll_spread_far='', note='итог сессии 5: строк 0'))
        if kind == 'worm: обязательный файл отсутствует':
            # живого ряда сигналов НЕТ: тело якоря обязано отказать, а не писать
            # «ФАЙЛА НЕТ» при успешном завершении (девятнадцатый круг, №18).
            try:
                WA._anchor_body('2026-08-14')
                out['ok'] = False
            except RuntimeError as ex:
                out['ok'] = 'обязательный' in str(ex)
            return out
        (Path(tmp) / 'signals_live.csv').write_text(
            ',leg_eq,leg_bond\n2026-08-31,1,1\n', encoding='utf-8')
        # САЙДКАР УРОВНЕЙ ОБЯЗАТЕЛЕН (двадцать первый круг, №14): он попадает в архив и
        # теперь входит в тело якоря — без него сигнал недоказуем, и настоящее состояние
        # его всегда несёт. Прежняя фикстура его не создавала.
        (Path(tmp) / 'signals_levels.csv').write_text(
            ',IEF,SPY\n2026-07-31,92.630000,747.030000\n', encoding='utf-8')
        # ПИН СЧЁТА И САЙДКАР КОНТРОЛЬНОЙ СУММЫ — ТОЖЕ ЧАСТЬ ЖИВОГО СОСТОЯНИЯ (44-й круг,
        # №13). Обязательность приходит из истории якорей, и она растёт: пин уже заверялся,
        # сайдкар начнёт заверяться первым же снимком после этой правки. Кладём оба СЕЙЧАС,
        # иначе батарея краснела бы не на правке, а на следующем замыкании — «траектория, а
        # не снимок».
        (Path(tmp) / 'account.txt').write_text('DU000001\n', encoding='utf-8')
        import hashlib as _hl13
        _sig13 = Path(tmp) / 'signals_live.csv'
        (Path(tmp) / 'signals_live.csv.sha256').write_text(
            _hl13.sha256(_sig13.read_bytes()).hexdigest() + '\n', encoding='utf-8')
        if kind == 'worm: ШТАТНЫЙ снимок проходит целиком':
            # ТРИДЦАТЫЙ КРУГ, №11. Успешный производственный путь snap() не исполнял НИ
            # ОДИН стенд: единственный вызов проверял ОТКАЗ. Поэтому правка, из-за которой
            # заверение не могло совпасть никогда (в файл уходило body_full, а сверялось
            # body), прошла батарею, мутации, replay и выпуск — а в бою остановила бы
            # автопилот на первом же замыкании. Здесь снимок обязан ПРОЙТИ: архив остаётся
            # под рабочим именем, якорь лежит в HEAD, и его текст несёт sha256 архива.
            bdir = Path(tmp) / 'backups'
            rd = Path(tempfile.mkdtemp(prefix='addfut-git-ok-'))
            subprocess.run(['git', 'init', '-q', str(rd)], check=True, capture_output=True)
            for _k, _v in (('user.email', 'stend@local'), ('user.name', 'stend')):
                subprocess.run(['git', '-C', str(rd), 'config', _k, _v],
                               check=True, capture_output=True)
            (rd / 'anchors').mkdir()
            keep_root, keep_anch = WA.ROOT, WA.ANCHORS
            WA.ROOT, WA.ANCHORS = rd, rd / 'anchors'
            try:
                WA.snap('2026-08-14', bdir)
                _rab = sorted(x.name for x in bdir.glob('addfut-*.tgz'))
                _rej = sorted(x.name for x in bdir.glob('addfut-*.rejected'))
                _anch = sorted(x.name for x in (rd / 'anchors').glob('worm-*.txt'))
                _txt = (rd / 'anchors' / _anch[0]).read_text(encoding='utf-8') if _anch else ''
                _in_head = subprocess.run(
                    ['git', '-C', str(rd), 'ls-tree', 'HEAD', '--', f'anchors/{_anch[0]}'],
                    capture_output=True, text=True).stdout.strip() if _anch else ''
                # ЗНАЧЕНИЕ ХЭША, А НЕ НАЛИЧИЕ СТРОКИ (тридцать первый круг, №18). Стенд
                # требовал лишь подстроку «sha256 архива»: замени _sha(dst) константой или
                # хэшем другого файла — стенд остался бы зелёным, а якорь перестал бы
                # привязывать копию к состоянию. Смысл строки в том, что по ней можно
                # проверить КОНКРЕТНЫЙ архив после выгрузки в зеркало; проверяем ровно это.
                import re as _re30
                _m30 = _re30.search(r'sha256 архива \(([^)]+)\):\s*([0-9a-f]{64})', _txt)
                _bind = False
                if _m30 and _rab:
                    _tgz = bdir / _m30.group(1)
                    _bind = (_m30.group(1) == _rab[0] and _tgz.exists()
                             and _hl.sha256(_tgz.read_bytes()).hexdigest() == _m30.group(2))
                out['ok'] = (len(_rab) == 1 and not _rej and len(_anch) == 1
                             and bool(_in_head) and _bind)
                out['files'] = dict(рабочие=_rab, помеченные=_rej, якоря=_anch,
                                    архив_привязан=_bind)
            finally:
                WA.ROOT, WA.ANCHORS = keep_root, keep_anch
            return out
        if kind == 'worm: утрата заверенного замера':
            # СОРОК ЧЕТВЁРТЫЙ КРУГ, №13. Обязательность файла не может выводиться из него
            # самого: удали замер — и он объявит себя необязательным, а якорь заверит утрату
            # как штатное отсутствие. Признак берётся из ИСТОРИИ ЯКОРЕЙ. Стенд проверяет обе
            # стороны в одном прогоне, иначе «отказал» ничего не говорит: без истории тот же
            # вызов обязан ПРОЙТИ (молодой контур), с историей — ОТКАЗАТЬ (утрата).
            _anch_dir = Path(tempfile.mkdtemp(prefix='addfut-ever-')) / 'anchors'
            _anch_dir.mkdir(parents=True)
            keep_anch = WA.ANCHORS
            WA.ANCHORS = _anch_dir
            # ПРОВЕРЯЮТСЯ ВСЕ ТРИ МЕТКИ, И ЧЕРЕЗ КОНСТАНТЫ (рецензия 19.08): у пина счёта и
            # контрольной суммы ряда не было стенда вовсе — переименование любой из них
            # молча снимало обязательность навсегда, и батарея оставалась зелёной. Литерал
            # в стенде ловил бы только совпадение с самим собой; ссылка на WA.LBL_* ловит
            # расхождение между записью в тело якоря и чтением истории.
            _mrg_missing = str(Path(tmp) / 'замера-нет.json')
            # ЛИТЕРАЛЫ — ЭТО ИСТОРИЯ, КОНСТАНТЫ — ЭТО КОД (уточнено зондом при этой же
            # правке). Первая редакция писала фикстуру ЧЕРЕЗ WA.LBL_*, и переименование
            # константы стенд не ловило: запись и чтение менялись согласованно. А в бою
            # метку несут якоря, УЖЕ ЛЕЖАЩИЕ В GIT, — она заморожена и переименованию не
            # подлежит. Поэтому фикстура пишет ровно те строки, что лежат в истории, а
            # совпадение с константами проверяется отдельно и прямо.
            out['метки_заморожены'] = (
                (WA.LBL_MARGIN, WA.LBL_PIN, WA.LBL_SUM)
                == ('замера маржи', 'пина счёта', 'контрольной суммы ряда'))
            _cases13 = (
                ('замера маржи', 'margins_live.json',
                 lambda: os.environ.__setitem__('ADDFUT_MARGINS', _mrg_missing)),
                ('пина счёта', 'account.txt',
                 lambda: (Path(tmp) / 'account.txt').unlink()),
                ('контрольной суммы ряда', 'signals_live.csv.sha256',
                 lambda: (Path(tmp) / 'signals_live.csv.sha256').unlink()),
            )
            out['метки'] = {}
            try:
                for _lbl13, _name13, _lose13 in _cases13:
                    WA._EVER_CACHE.update(key=None, val=frozenset())
                    for _f13 in _anch_dir.glob('worm-*.txt'):
                        _f13.unlink()
                    _lose13()                       # файл утрачен
                    try:
                        WA._anchor_body('2026-08-14')
                        _young = True               # истории нет — молодой контур проходит
                    except RuntimeError:
                        _young = False
                    (_anch_dir / 'worm-2026-08-01.txt').write_text(
                        f'sha256 {_lbl13} ({_name13}): ' + 'a' * 64 + '\n', encoding='utf-8')
                    WA._EVER_CACHE.update(key=None, val=frozenset())
                    try:
                        WA._anchor_body('2026-08-14')
                        _lost = False
                    except RuntimeError as _ex13:
                        _lost = 'обязательный файл отсутствует' in str(_ex13)
                    out['метки'][_lbl13] = (_young, _lost)
                    if _lbl13 == 'замера маржи':
                        # ОПИСЬ СВЕРЯЕТСЯ, ПОКА В ИСТОРИИ ЛЕЖИТ ИМЕННО ЭТОТ ЯКОРЬ: после
                        # следующей итерации он затирается, и проверка мерила бы пустоту.
                        _must13 = [_m for _l, _p, _a, _m in WA._attested_paths()
                                   if _l == 'замер маржи']
                        out['опись_требует'] = _must13 == [True]
                out['молодой_проходит'] = all(v[0] for v in out['метки'].values())
                out['утрата_отвергнута'] = all(v[1] for v in out['метки'].values())
            finally:
                WA.ANCHORS = keep_anch
                WA._EVER_CACHE.update(key=None, val=frozenset())
            out['ok'] = (out['молодой_проходит'] is True
                         and out['утрата_отвергнута'] is True
                         and out.get('опись_требует') is True
                         and out['метки_заморожены'] is True
                         and len(out['метки']) == 3)
            return out
        if kind == 'worm: ВТОРОЙ снимок боевым вызовом':
            # ИНЦИДЕНТ 19.08.2026 (§12): контур встал на первом же замыкании после того,
            # как появился якорь НОВОГО формата. Стенд «ШТАТНЫЙ снимок» этого не видел,
            # потому что отличался от боя ДВУМЯ вещами сразу, и обе нужны вместе:
            #   (1) он передавал bdir объектом Path, а `worm_anchor.py --snap ДЕНЬ КАТАЛОГ`
            #       отдаёт СТРОКУ из argv — и `bdir / имя` внутри anchors_without_archive
            #       падало TypeError только в бою;
            #   (2) он снимал ПЕРВУЮ копию в пустом каталоге якорей, а список кандидатов
            #       собирается лишь из якорей со строкой «sha256 архива» — на первом снимке
            #       он пуст, и дефектная строка недостижима вовсе.
            # Отсюда траектория, а не снимок: снимаем ДВЕ копии подряд боевой формой вызова.
            # Второй снимок и есть проверяемое место; на нём выпуск и падал.
            bdir = Path(tmp) / 'backups'
            rd = Path(tempfile.mkdtemp(prefix='addfut-git-argv-'))
            subprocess.run(['git', 'init', '-q', str(rd)], check=True, capture_output=True)
            for _k, _v in (('user.email', 'stend@local'), ('user.name', 'stend')):
                subprocess.run(['git', '-C', str(rd), 'config', _k, _v],
                               check=True, capture_output=True)
            (rd / 'anchors').mkdir()
            keep_root, keep_anch = WA.ROOT, WA.ANCHORS
            WA.ROOT, WA.ANCHORS = rd, rd / 'anchors'
            import re as _re44
            try:
                WA.snap('2026-08-14', str(bdir))          # СТРОКА, как из argv
                _t1 = (rd / 'anchors' / 'worm-2026-08-14.txt').read_text(encoding='utf-8')
                # ЗОНД ДОСТИЖИМОСТИ: без якоря со строкой «sha256 архива» второй снимок
                # проверяемую ветку не проходит вовсе, и стенд был бы пустым.
                out['якорь_несёт_архив'] = 'sha256 архива (' in _t1
                WA.snap('2026-08-17', str(bdir))          # ЗДЕСЬ И ОТКАЗЫВАЛО 18.08
                _rab = sorted(x.name for x in bdir.glob('addfut-*.tgz'))
                _rej = sorted(x.name for x in bdir.glob('addfut-*.rejected'))
                _anch = sorted(x.name for x in (rd / 'anchors').glob('worm-*.txt'))
                # ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ (угол «от отрицания»): пройти ветку мало — она
                # обязана в ней ГОВОРИТЬ. Прячем архив последнего якоря под .pending, то
                # есть воспроизводим обрыв между коммитом и публикацией, и требуем, чтобы
                # ТОТ ЖЕ боевой вызов со строкой назвал разрыв поимённо. Иначе «снимок
                # прошёл» доказывало бы лишь отсутствие исключения.
                _t2 = (rd / 'anchors' / 'worm-2026-08-17.txt').read_text(encoding='utf-8')
                _nm2 = _re44.search(r'addfut-[0-9A-Za-z\-]+\.tgz', _t2).group(0)
                (bdir / _nm2).rename(bdir / (_nm2 + '.pending'))
                _bad = WA.anchors_without_archive(str(bdir))
                out['разрыв_назван'] = bool(_bad) and any(_nm2 in x for x in _bad)
                # ОШИБКА КОДА ОБЯЗАНА ПАДАТЬ, А НЕ СТАНОВИТЬСЯ ВЕРДИКТОМ (инцидент
                # 19.08.2026). Воспроизводим исходный дефект точечно — снимаем приведение
                # пути — и требуем ИСКЛЮЧЕНИЯ, а не списка: прежде здесь получался мягкий
                # «считать недоказанной», и контур останавливался с чужим диагнозом.
                _keep_ap = WA._as_path
                WA._as_path = lambda x: x
                try:
                    WA.anchors_without_archive(str(bdir))
                    out['ошибка_кода_громко'] = False
                except TypeError:
                    out['ошибка_кода_громко'] = True
                except Exception:
                    out['ошибка_кода_громко'] = False
                finally:
                    WA._as_path = _keep_ap
                out['ok'] = (len(_rab) == 2 and not _rej and len(_anch) == 2
                             and out['якорь_несёт_архив'] is True
                             and out['разрыв_назван'] is True
                             and out['ошибка_кода_громко'] is True)
                out['files'] = dict(рабочие=_rab, помеченные=_rej, якоря=_anch,
                                    разрыв=_bad)
            finally:
                WA.ROOT, WA.ANCHORS = keep_root, keep_anch
            return out
        if kind == 'worm: архив разных поколений помечается':
            # ДВАДЦАТЫЙ КРУГ, №22: _tar_state публикует addfut-*.tgz ДО повторного
            # вычисления тела. Состояние «меняется» между двумя вычислениями — снимок
            # обязан быть отклонён, а уже опубликованный архив НЕ смеет остаться под
            # рабочим именем: следующий backup_push или ручное восстановление взяли бы
            # архив, про который сам код установил, что поколения разные.
            bdir = Path(tmp) / 'backups'
            _orig_body = WA._anchor_body
            _calls = {'n': 0}

            def _shifting(day):
                _calls['n'] += 1
                return _orig_body(day) + ('' if _calls['n'] == 1 else '\nИЗМЕНЕНО')
            WA._anchor_body = _shifting
            try:
                WA.snap('2026-08-14', bdir)
                out['ok'] = False              # отказа не было — защита мертва
            except RuntimeError as ex:
                rabochie = sorted(x.name for x in bdir.glob('addfut-*.tgz'))
                pomecheny = sorted(x.name for x in bdir.glob('addfut-*.rejected'))
                out['ok'] = ('разных поколений' in str(ex)
                             and not rabochie and len(pomecheny) == 1)
                out['files'] = dict(рабочие=rabochie, помеченные=pomecheny)
            finally:
                WA._anchor_body = _orig_body
            return out
        body = WA._anchor_body('2026-08-14')
        if kind == 'worm: якорь аттестует действующие пути':
            # ДЕЙСТВУЮЩИЙ путь ряда сигналов (ADDFUT_SIGNALS), а не жёсткий ~/.addfut.
            _sig_sha = _hl.sha256((Path(tmp) / 'signals_live.csv').read_bytes()).hexdigest()
            out['ok'] = (_sig_sha in body) and ('2026-08-13' in body)
            return out
        # kind == 'worm: подмена содержимого при коммите ловится' (№19): временный
        # git-репозиторий с pre-commit hook, дописывающим якорь ПОСЛЕ git add — blob в
        # HEAD расходится с файлом на диске, и заверение обязано отказать.
        rd = Path(tempfile.mkdtemp(prefix='addfut-git-'))
        subprocess.run(['git', 'init', '-q', str(rd)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(rd), 'config', 'user.email', 'stend@local'],
                       check=True, capture_output=True)
        subprocess.run(['git', '-C', str(rd), 'config', 'user.name', 'stend'],
                       check=True, capture_output=True)
        (rd / 'anchors').mkdir()
        hooks = rd / '.git' / 'hooks'
        hooks.mkdir(parents=True, exist_ok=True)
        hk = hooks / 'pre-commit'
        hk.write_text('#!/bin/sh\nfor f in anchors/worm-*.txt; do '
                      'echo tampered >> "$f"; done\nexit 0\n', encoding='utf-8')
        hk.chmod(0o755)
        keep_root, keep_anch = WA.ROOT, WA.ANCHORS
        WA.ROOT, WA.ANCHORS = rd, rd / 'anchors'
        try:
            outp = WA._write_anchor('2026-08-14', body)
            try:
                WA._git_commit_verified(outp)
                out['ok'] = False              # подмена прошла незамеченной
            except RuntimeError as ex:
                out['ok'] = 'НЕ СОВПАДАЕТ' in str(ex)
        finally:
            WA.ROOT, WA.ANCHORS = keep_root, keep_anch
    except Exception as ex:
        out['raised'] = True; out['error'] = f'{type(ex).__name__}: {ex}'
    finally:
        for k, v in keep.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    return out


@rinv('штатный снимок WORM проходит целиком, а не только отказывает',
      needs=lambda r: r['case'] == 'worm: ШТАТНЫЙ снимок проходит целиком')
def _r30a(r):
    """ТРИДЦАТЫЙ КРУГ, №11. Все стенды WORM проверяли ОТКАЗЫ, и защита, сломанная так, что
    заверение не совпадало никогда, оставалась зелёной во всём выпуске. Отказной стенд от
    этого не спасает: он получает исключение и по ЛОЖНОЙ причине тоже."""
    return not r['raised'] and r['ok'] is True


@rinv('утрата однажды заверенного замера отвергается, а молодой контур проходит',
      needs=lambda r: r['case'] == 'worm: утрата заверенного замера')
def _r44e(r):
    """СОРОК ЧЕТВЁРТЫЙ КРУГ, №13. Обе половины в одном утверждении: без истории якорей
    отсутствие замера законно (иначе стенд доказывал бы «отказывает всегда»), с историей —
    утрата, и опись архива требует файл так же, как тело якоря."""
    return (not r['raised'] and r['ok'] is True
            and r.get('молодой_проходит') is True and r.get('утрата_отвергнута') is True
            and r.get('метки_заморожены') is True and len(r.get('метки') or {}) == 3)


@rinv('второй снимок WORM боевым вызовом (строка из argv) проходит, а разрыв назван',
      needs=lambda r: r['case'] == 'worm: ВТОРОЙ снимок боевым вызовом')
def _r44w(r):
    """ИНЦИДЕНТ 19.08.2026 (§12). Проверяются три вещи разом, потому что поодиночке каждая
    зелена и при сломанной защите: снимок не поднял исключения; якорь первого снимка
    ДЕЙСТВИТЕЛЬНО несёт имя архива (иначе проверяемая ветка недостижима и стенд пуст);
    та же боевая форма вызова НАЗЫВАЕТ разрыв, когда он есть."""
    return (not r['raised'] and r['ok'] is True
            and r.get('якорь_несёт_архив') is True and r.get('разрыв_назван') is True)


@rinv('пустая книга маршрута Е не объявляется слепотой вахты',
      needs=lambda r: r['case'] == 'автопилот: пустая книга Е не считается слепотой')
def _r45empty(r):
    """СОРОК ПЯТЫЙ КРУГ, №6 (P1). Три исхода сразу: пустая книга законна, неполная
    сводка остаётся слепотой, низкий запас остаётся срезом. Одного конца мало — он
    зелен и для кода, объявляющего законным что угодно."""
    return not r['raised'] and r['ok'] is True


@rinv('вердикт вахты О-3-Е читается сквозь диагностику шлюза',
      needs=lambda r: r['case'] == 'автопилот: вердикт вахты читается сквозь шум шлюза')
def _r45o3e(r):
    """СОРОК ПЯТЫЙ КРУГ, №1 (P0). Четыре требования сразу: метка читается сквозь шум,
    берётся ПОСЛЕДНЯЯ (повторный замер сильнее раннего), отсутствие метки не выдаётся за
    вердикт, и прежний разбор на том же ответе ПРОМАХИВАЕТСЯ — последнее доказывает, что
    стенд проверяет правку, а не совпадение."""
    return (not r['raised'] and r['ok'] is True
            and r.get('прежний_промахивался') is True)



# --- утверждения к правилам 45-го круга (по одному на защиту, чтобы отказ был назван) ---
def _r45(kind, key=None):
    """Каждое правило круга — своё утверждение. Общий сборник дал бы один отказ на девять
    защит, и мутационный вердикт «поймана» не сказал бы, ЧЕМ именно."""
    def _mk(r):
        if r['raised'] or not r['ok']:
            return False
        return True if key is None else r.get(key) is True
    return _mk


@rinv('допуск бара снимается ЛЮБОЙ заданной границей, а не только точной',
      needs=lambda r: r['case'] == 'правила45: допуск бара снимается известным календарём')
def _r45gap(r):
    """Разбор /code-review 45-го круга. Плоские пять дней — это правило «календарь
    неизвестен», а не «expected_prev не задан»: переход полосы фонда на min_prev вернул
    отказ маршрута Е на 29.12.2026. Проверяются оба конца и достижимость ветки: без
    разрыва больше допуска стенд доказывал бы пустоту."""
    return (not r['raised'] and r['ok'] is True and r.get('ветка_достижима') is True
            and r.get('без_календаря_допуск_держит') is True)


@rinv('пыль от вычитания прогресса не превращается в заявку',
      needs=lambda r: r['case'] == 'правила45: остаток ниже допуска не заявка')
def _r45dust(r):
    """Разбор /code-review. Точный ноль на разности float оставлял 5,55e-16 юнита, и
    завершённый переход получал POSTPONED. Второй конец обязателен: НАСТОЯЩИЙ остаток
    обязан дожить до заявки, иначе годился бы код, выбрасывающий всё."""
    return (not r['raised'] and r['ok'] is True and r.get('остаток_доходит') is True)


@rinv('беспланный предпросмотр держит норматив, но не запирает аварию и завершение',
      needs=lambda r: r['case'] == 'правила45: беспланный предпросмотр')
def _r45pv(r):
    """Разбор /code-review, угол «от противоположного знака». Порог О-3-Е без плана я
    поставил безусловным и закрыл им аварийный выход Е->Ф и завершение уже исполненного
    перехода. Незаконный COMPLETE при этом обязан остаться отвергнутым."""
    return (not r['raised'] and r['ok'] is True and r.get('низкий_отвергнут') is True
            and r.get('авария_проходит') is True
            and r.get('исполненное_завершается') is True)


@rinv('замок книги выводится из книги во всех входах, включая голый',
      needs=lambda r: r['case'] == 'правила45: замок книги один на всех писателей')
def _r45lock(r):
    """Разбор /code-review. Правило жило перечислением вызывающих — и переходный
    исполнитель с якорем WORM остались на ~/.addfut. Теперь оно в умолчании; изоляция
    стенда переменной окружения обязана оставаться сильнее правила."""
    return (not r['raised'] and r['ok'] is True and r.get('голый_и_явный_совпали') is True
            and r.get('окружение_сильнее') is True)


@rinv('журнал §7 не принимает дописывание под повреждённой шапкой',
      needs=lambda r: r['case'] == 'правила45: журнал не дописывается под мусорной шапкой')
def _r45j(r):
    """Разбор /code-review. Проверка заголовка стояла у читателя, а пишет append через
    СВОЙ незащищённый читатель — и начинал цепочку заново от GENESIS поверх мусора."""
    return (not r['raised'] and r['ok'] is True
            and r.get('мусорная_шапка_отвергнута') is True
            and r.get('исправный_принят') is True)


@rinv('диагност не подменяет капитальный отказ маржинальным и наоборот',
      needs=lambda r: r['case'] == 'правила45: диагност различает капитал и маржу')
def _r45dg(r):
    """Разбор /code-review. Соседство проверялось по всему телу тревоги, а тело в бою —
    весь вывод сессии: одна здоровая строка про запас отменяла верный диагноз."""
    return (not r['raised'] and r['ok'] is True
            and r.get('капитал_виден_рядом_со_здоровым') is True
            and r.get('здоровый_не_даёт_маржи') is True)


@rinv('якорь не заверяет реестр и замер разных поколений молча',
      needs=lambda r: r['case'] == 'правила45: пара реестра и замера сверяется')
def _r45w(r):
    """Разбор /code-review. Сверка шла в одну сторону и по одному типу инструмента;
    согласованная пара обязана молчать, иначе маршрут Е получил бы вечное расхождение."""
    return (not r['raised'] and r['ok'] is True and r.get('согласованное_молчит') is True
            and r.get('замер_новее_виден') is True and r.get('фонды_не_мешают') is True)


@rinv('живая доходность не залипает в кэше между расчётами',
      needs=lambda r: r['case'] == 'правила45: dref кэшируется только закреплённым')
def _r45d(r):
    """Разбор /code-review. Кэш сбрасывался только в gross(), а unit_ref зовут и мимо
    него: величина часовой давности выглядела бы исправной проверкой."""
    return (not r['raised'] and r['ok'] is True and r.get('живое_не_залипает') is True
            and r.get('закреплённое_читается_раз') is True)


@rinv('календарный сторож не переодевает ошибку кода в поставочный риск',
      needs=lambda r: r['case'] == 'правила45: ошибка кода не переодевается календарём')
def _r45ce(r):
    """Разбор /code-review. Docstring обещал «ошибка кода падает своим типом», а код ловил
    Exception целиком; доменная неизвестность обязана при этом остаться остановкой."""
    return (not r['raised'] and r['ok'] is True and r.get('ошибка_кода_своим_типом') is True
            and r.get('доменная_остановка_названа') is True)



@rinv('предполёт передачи книги роняет ошибку кода своим типом',
      needs=lambda r: r['case'] == 'правила45: предполёт передачи не переодевает ошибку кода')
def _r45pf(r):
    """Разбор /code-review 45-го круга. Пятый широкий перехват круг закрыл у ВЫЗЫВАЮЩЕГО, а
    переодевание живёт внутри предполёта: до внешнего except доходит уже RuntimeError, и
    аварийный выход Е→Ф блокируется ложным доменным диагнозом вместо трассировки."""
    return (not r['raised'] and r['ok'] is True and r.get('ветка_достижима') is True)


@rinv('возраст сердцебиения читается по содержимому, а touch его не лечит',
      needs=lambda r: r['case'] == 'автопилот: возраст сердцебиения строг')
def _r44h(r):
    """СОРОК ЧЕТВЁРТЫЙ КРУГ, №12. Живая отметка обязана ЧИТАТЬСЯ (иначе доказывалось бы
    «ломается всегда»), пустая и нечисловая — быть поломкой, touch поверх негодного
    содержимого — не лечить её, метка из будущего — быть поломкой, а не нулём. Плюс
    атомарность записи: строгое чтение без неё ловило бы гонку писателя."""
    return not r['raised'] and r['ok'] is True


@rinv('общая тревога автопилота не затирает причину отказа снимка',
      needs=lambda r: r['case'] == 'автопилот: причина тревоги не затирается общей')
def _r44a(r):
    """ИНЦИДЕНТ 19.08.2026 (§12). Проверяется вместе: ветка ПРОЙДЕНА (run_close вернул 1 и
    сказал своё общее слово), причина от backup_state в файле ОСТАЛАСЬ, день закрытым не
    объявлен. Порознь каждый признак зелен и при затирании."""
    return (not r['raised'] and r['ok'] is True and r.get('причина_жива') is True
            and r.get('ветка_пройдена') is True)


@rinv('чужой процесс не берёт занятый замок и берёт свободный',
      needs=lambda r: r['case'] == 'замок между процессами')
def _r19(r):
    return not r['raised'] and r['held'] == 'ЗАНЯТО' and r['freed'] == 'ВЗЯЛ'


@rinv('подмена файла замка под держателем не впускает второго',
      needs=lambda r: r['case'] == 'замок между процессами')
def _r44l(r):
    """СОРОК ЧЕТВЁРТЫЙ КРУГ, №9. Проверяется вместе с соседним утверждением намеренно:
    «ЗАНЯТО после подмены» без «ВЗЯЛ на свободном» доказывалось бы и наглухо сломанным
    замком, который не даётся никому."""
    return (not r['raised'] and r.get('held_after_swap') == 'ЗАНЯТО'
            and r['freed'] == 'ВЗЯЛ')


@rinv('подмена книги внутри окна замыкания отвергается',
      needs=lambda r: r['case'] == 'гонка при замыкании')
def _r16(r):
    """Потерянная запись: замыкание, начатое до перевода маршрута, не имеет права записать
    поверх новой книги старую. Проверяется И то, что подмена действительно произошла, —
    иначе утверждение доказывало бы отсутствие гонки, а не защиту от неё."""
    return r.get('подменено') and r['raised'] and 'изменилась' in r['error']


def _session_route_switch():
    """СКВОЗНАЯ ПОСЛЕДОВАТЕЛЬНОСТЬ: торговля на Ф -> передача книги -> торговля на Е.

    Переходный исполнитель проверялся отдельно, ежедневный контур отдельно, а СТЫК между
    ними — нет. Между тем именно на стыке жили два дефекта: книга писалась по другому пути,
    и незавершённый ролл терялся. Передача здесь идёт НАСТОЯЩИМ кодом исполнителя
    (transition.hand_over_book), а не пересказом: иначе стенд проверял бы сам себя.
    """
    import os
    import sys as _sys
    import tempfile
    import ib_stub
    import feed as FD
    import session as SS
    import state as ST
    import pandas as pd
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import transition as TRN

    es, zn, tnx, cspx, cbu0 = 900001, 900003, 990001, 900004, 900005
    esz, mesz, znz = 900006, 900007, 900008
    rows = list(ib_stub.FIXTURE_ROWS)
    ib = ib_stub.StubIB(rows, nlv=1_000_000.0)
    ib.rows[tnx] = dict(instrument='TNX', sec_type='IND', exchange='CBOE', currency='USD',
                        con_id=str(tnx), local_symbol='TNX', expiry='', multiplier='')
    bars = {900010: [('2026-08-10', 770.0), ('2026-08-11', 774.75)],
            es: [('2026-08-10', 7700.0), ('2026-08-11', 7747.5)],
            900002: [('2026-08-10', 7700.0), ('2026-08-11', 7747.5)],
            zn: [('2026-08-10', 108.4), ('2026-08-11', 108.5)],
            tnx: [('2026-08-10', 46.9), ('2026-08-11', 46.84)],
            cspx: [('2026-08-10', 830.0), ('2026-08-11', 834.66)],
            cbu0: [('2026-08-10', 152.5), ('2026-08-11', 152.94)]}

    tmp = tempfile.mkdtemp(prefix='addfut-sw-')
    reg = ib_stub.fixture_registry(tmp)
    sig = Path(tmp) / 'signals.csv'
    sig.write_text(',leg_eq,leg_bond\n2026-06-30,1,1\n2026-07-31,1,1\n2026-08-31,1,1\n', encoding='utf-8')

    class Idx:
        def __init__(self, *a, **k):
            self.conId = tnx; self.symbol = 'TNX'; self.localSymbol = 'TNX'
            self.exchange = 'CBOE'; self.currency = 'USD'
            self.secType = 'IND'; self.multiplier = ''
            self.lastTradeDateOrContractMonth = ''

    import ib_insync
    keep = (ib_insync.Index, FD.registry, FD.signal_state, FD.exchange_today,
            os.environ.get('ADDFUT_DIR'), os.environ.get('ADDFUT_LOCK_DIR'),
            os.environ.get('ADDFUT_REGISTRY'), os.environ.get('ADDFUT_BOOK_PATH'))
    try:
        ib_insync.Index = Idx
        FD.registry = lambda: {r['instrument']: r for r in
                               __import__('csv').DictReader(open(reg, encoding='utf-8'))}
        _sig = FD.signal_state
        FD.signal_state = lambda t, path=None, **kw: _sig(t, path=sig, **kw)
        os.environ['ADDFUT_DIR'] = tmp
        os.environ['ADDFUT_LOCK_DIR'] = tmp
        # МАРШРУТ ЕСТЬ ВСЕГДА (двадцать седьмой круг, №1): его пишет hand_over_book, а
        # пилот стартовал на Ф. Стенд без route.txt описывает состояние, которого нет.
        (Path(tmp) / 'route.txt').write_text('F', encoding='utf-8')
        os.environ['ADDFUT_REGISTRY'] = str(reg)
        os.environ.pop('ADDFUT_BOOK_PATH', None)
        # ПИН ТОРГОВОГО СЧЁТА (двадцатый круг, №5): контур обязан знать свой счёт, и
        # стенд даёт ровно тот, что отдаёт стаб — выдуманный проверял бы сам себя.
        os.environ.pop('ADDFUT_ACCOUNT', None)
        (Path(tmp) / 'account.txt').write_text(ib.managedAccounts()[0], encoding='utf-8')
        real_now = pd.Timestamp.now

        out = dict(case='смена маршрута в связке с торговлей', f_ok=False, handed=None,
                   e_ok=False, e_positions=None, route_saved=None, error='')
        ib.set_bars(bars)
        FD.exchange_today = lambda: pd.Timestamp('2026-08-12')
        pd.Timestamp.now = staticmethod(
            lambda tz=None: pd.Timestamp('2026-08-12 10:00', tz=FD.EXCHANGE_TZ) if tz
            else real_now())
        try:
            SS.do_trade(ib, 'F', dry=False)
            out['f_ok'] = True
        except BaseException as ex:
            out['error'] += f'Ф: {type(ex).__name__}: {ex} | '
        b2 = {k: list(v) + [('2026-08-12', v[-1][1] * 1.002)] for k, v in bars.items()}
        ib.set_bars(b2)
        pd.Timestamp.now = staticmethod(
            lambda tz=None: pd.Timestamp('2026-08-12 17:00', tz=FD.EXCHANGE_TZ) if tz
            else real_now())
        try:
            SS.do_close(ib, 'F')
        except BaseException as ex:
            out['error'] += f'замыкание Ф: {type(ex).__name__}: {ex} | '

        # ПЕРЕВОД МАРШРУТА: фьючерсы закрыты, куплены фонды. Передача книги — настоящий код.
        ib._pos = {cspx: 1195.0, cbu0: 6538.0}
        ib._shown = dict(ib._pos)
        try:
            import ib_broker as IBB
            _br = IBB.IBBroker(ib, registry=reg, settle_s=0.0, timeout_s=1.0)
            out['handed'] = TRN.hand_over_book(_br, 'F', 'E')
        except BaseException as ex:
            out['error'] += f'передача книги: {type(ex).__name__}: {ex} | '
        # ТОРГОВЛЯ В ДЕНЬ ПЕРЕХОДА обязана быть отвергнута: временная история книги
        # сохранена, замыкание дня перехода предварительное.
        try:
            SS.do_trade(ib, 'E', dry=False)
            out['same_day_refused'] = False
        except BaseException:
            out['same_day_refused'] = True

        # ЗАМЫКАНИЕ ДНЯ ПЕРЕХОДА — штатный --close: книга принята с предварительным
        # замыканием, и без него следующая сессия торговать откажется (проверено выше).
        pd.Timestamp.now = staticmethod(
            lambda tz=None: pd.Timestamp('2026-08-12 17:00', tz=FD.EXCHANGE_TZ) if tz
            else real_now())
        try:
            SS.do_close(ib, 'E')
        except BaseException as ex:
            out['error'] += f'замыкание Е: {type(ex).__name__}: {ex} | '
        # ТОРГОВЛЯ НА НОВОМ МАРШРУТЕ СЛЕДУЮЩЕЙ СЕССИЕЙ
        b3 = {k: list(v) + [('2026-08-13', v[-1][1] * 1.001)] for k, v in b2.items()}
        ib.set_bars(b3)
        FD.exchange_today = lambda: pd.Timestamp('2026-08-13')
        # ВНУТРИ ОКНА МАРШРУТА Е (двадцатый круг, №6): окно Е 08:45-09:45, и прежние 10:00
        # означали торговлю за краем — теперь это запрещено воротами.
        pd.Timestamp.now = staticmethod(
            lambda tz=None: pd.Timestamp('2026-08-13 09:00', tz=FD.EXCHANGE_TZ) if tz
            else real_now())
        try:
            SS.do_trade(ib, 'E', dry=False)
            out['e_ok'] = True
        except BaseException as ex:
            out['error'] += f'Е: {type(ex).__name__}: {ex}'
        out['e_positions'] = {k: v for k, v in ib._pos.items() if v}
        try:
            _b, _s, out['route_saved'] = ST.load(ST.book_path('E'), DL.BookE)
        except Exception:
            out['route_saved'] = None
        rt = ST.lock_dir() / 'route.txt'
        out['route_file'] = rt.read_text(encoding='utf-8').strip() if rt.exists() else None
        # ВОЗВРАТ В РАНЕЕ РАБОТАВШИЙ МАРШРУТ (СОРОК ЧЕТВЁРТЫЙ КРУГ, №7). journal-F.csv здесь
        # УЖЕ непуст — маршрут Ф торговал 12.08, — и прежде hand_over_book писал итоговую
        # строку ТОЛЬКО в пустой журнал. Книга получала сегодняшнюю дату и новый номер
        # сессии, а последней строкой журнала оставался итог старой эпохи Ф: первое же
        # замыкание — отказ якоря WORM по несовпадению даты, ALARM-backup навсегда, ролл
        # заперт. Выпускной round-trip этого не видел, потому что после возврата не
        # замыкает день. Проверяем ФАКТ в журнале: последняя строка — итог ЭТОЙ сессии.
        try:
            import journal as _J7t
            _jf = ST.lock_dir() / 'journal-F.csv'
            out['f_rows_before'] = len(_J7t.read(_jf))
            ib._pos = {es: 2.0, 900002: 6.0, zn: 10.0}
            ib._shown = dict(ib._pos)
            _brf = IBB.IBBroker(ib, registry=reg, settle_s=0.0, timeout_s=1.0)
            _bk_back = TRN.hand_over_book(_brf, 'E', 'F')
            _rows_f = _J7t.read(_jf)
            _last_f = _rows_f[-1] if _rows_f else {}
            out['back_last_note'] = str(_last_f.get('note', ''))
            out['back_last_date'] = str(_last_f.get('date', ''))
            out['back_book_date'] = str(getattr(_bk_back, 'last_session', ''))
            # ИМЕННО ТО, ЧТО СПРОСИТ ЯКОРЬ: журнал закрыт итогом ЭТОЙ сессии.
            out['back_gap'] = _J7t.session_incomplete(_rows_f, _bk_back.last_session)
        except BaseException as ex:
            out['error'] += f'возврат Е->Ф: {type(ex).__name__}: {ex} | '

    finally:
        pd.Timestamp.now = real_now
        (ib_insync.Index, FD.registry, FD.signal_state, FD.exchange_today,
         d0, l0, r0, b0) = keep
        for k, v in (('ADDFUT_DIR', d0), ('ADDFUT_LOCK_DIR', l0),
                     ('ADDFUT_REGISTRY', r0), ('ADDFUT_BOOK_PATH', b0)):
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    return out


@rinv('после перехода ежедневная торговля продолжается на НОВОМ маршруте',
      needs=lambda r: r['case'] == 'смена маршрута в связке с торговлей')
def _r17(r):
    """Штатного продолжения после перехода прежде не существовало: запуск на старом маршруте
    останавливался на расхождении, на новом — падал при чтении чужого формата или читал
    книгу по другому пути. Проверяется вся связка целиком."""
    # route.txt обязан быть написан ПЕРЕХОДОМ (одиннадцатый круг, №4).
    return (r['f_ok'] and r['handed'] is not None and r.get('same_day_refused')
            and r['e_ok'] and r['route_saved'] == 'E' and r.get('route_file') == 'E')


@rinv('возврат в ранее работавший маршрут закрывает журнал итогом ЭТОЙ сессии',
      needs=lambda r: r['case'] == 'смена маршрута в связке с торговлей')
def _r44ret(r):
    """СОРОК ЧЕТВЁРТЫЙ КРУГ, №7. Три требования: журнал целевого маршрута БЫЛ непуст
    (иначе стенд проверял бы ветку «новый маршрут», где итог писался и раньше), последняя
    строка — итог этой сессии, и разрыва нет по тому самому правилу, которое спросит якорь
    WORM при замыкании."""
    return (int(r.get('f_rows_before') or 0) > 0
            and str(r.get('back_last_note', '')).startswith('итог сессии')
            and r.get('back_last_date') == r.get('back_book_date')
            and r.get('back_gap') == '')


@rinv('после перехода книга состоит ТОЛЬКО из долей фондов',
      needs=lambda r: r['case'] == 'смена маршрута в связке с торговлей')
def _r18(r):
    """Иначе контур продолжил бы строить фьючерсную книгу на маршруте Е — торговал бы не тем
    инструментом и не по тому мандату."""
    pos = r['e_positions'] or {}
    return bool(pos) and all(k in (900004, 900005) for k in pos)


@rinv('позднее исполнение останавливает СЛЕДУЮЩУЮ сессию',
      needs=lambda r: r['case'] == 'позднее исполнение после сессии')
def _r15(r):
    """Распознать позднее исполнение в момент сделки нельзя — ни барьером, ни числом
    согласных снимков. Но расхождение книги обязано быть пойманным на входной сверке
    следующей сессии, и та НЕ ДОЛЖНА торговать."""
    return r['first_ok'] and r['second_refused'] and not r['second_traded']


@rinv('три сессии подряд идут без ошибок',
      needs=lambda r: r['case'] == 'три сессии подряд')
def _r11(r):
    return not r['errors']


@rinv('серия не дрейфует между сессиями',
      needs=lambda r: r['case'] == 'три сессии подряд')
def _r12(r):
    """Серия меняется ТОЛЬКО в день ролла. Дрейф по дням означал бы лишние переносы и
    повторный вход в уходящий контракт."""
    return len(set(r['series'])) == 1 and r['series'][0] == 'U26'


@rinv('дата последней сессии растёт каждый день',
      needs=lambda r: r['case'] == 'три сессии подряд')
def _r13(r):
    return r['sessions'] == sorted(r['sessions']) and len(set(r['sessions'])) == len(r['sessions'])


@rinv('вторая торговля до замыкания отвергается',
      needs=lambda r: r['case'] == 'три сессии подряд')
def _r14(r):
    return r['refused_without_close']


def check_ib_interface():
    """СТАБ ОБЯЗАН ОПИСЫВАТЬ ИНТЕРФЕЙС, КОТОРЫЙ У ЖИВОГО КЛАССА ЕСТЬ (двадцатый круг, №15).

    Двадцатая рецензия утверждала, что ib_insync.IB.reqAccountSummary не существует и что
    StubIB «изобрёл» метод, поэтому все стенды доказывают несуществующий интерфейс. ФАКТ
    проверен и утверждение ОТКЛОНЕНО: в установленной 0.9.86 метод есть, сигнатура (self),
    блокирующий, внутри открывает новый reqId и ждёт accountSummaryEnd — ровно то, что
    написано в комментарии адаптера.

    Но КЛАСС дефекта назван верно: у стендов не было ни одной проверки, связывающей стаб с
    живым классом, и апгрейд библиотеки сломал бы контур молча. Здесь имена, которые
    адаптер зовёт у self.ib, сверяются С ОБОИХ концов: они обязаны быть и у ib_insync.IB,
    и у StubIB. Первое ловит уехавшую библиотеку, второе — стаб, доказывающий выдумку.
    """
    import re
    import ib_stub
    from ib_insync import IB
    src = (Path(__file__).resolve().parent / 'ib_broker.py').read_text(encoding='utf-8')
    used = sorted(set(re.findall(r'self\.ib\.([a-zA-Z_]+)', src)))
    assert len(used) >= 10, f'подозрительно мало вызовов адаптера: {used}'
    no_live = [m for m in used if not hasattr(IB, m)]
    no_stub = [m for m in used if not hasattr(ib_stub.StubIB, m)]
    ok = not no_live and not no_stub
    # СВЕРЯЕТСЯ НЕ ТОЛЬКО НАЛИЧИЕ ИМЕНИ, НО И СВОЙСТВО, РАДИ КОТОРОГО ОНО ЗОВЁТСЯ
    # (тридцатый круг, №2). Замечание утверждало, что reqAccountSummary в 0.9.86 ходит на
    # сервер лишь при пустом кэше, поэтому NLV и запас О-3-Е могут быть доторговыми.
    # Проверено по исходнику и ОТКЛОНЕНО: условие `if not self.wrapper.acctSummary` живёт
    # в accountSummaryAsync (АКСЕССОРЕ), а reqAccountSummaryAsync безусловно берёт новый
    # reqId и шлёт запрос. Но методическая половина замечания верна — стенды доказывали
    # СТАБ. Здесь проверяется само свойство живой библиотеки: барьер обязан быть
    # безусловным. Апгрейд, сделавший его кэш-зависимым, покраснеет здесь, а не в бою.
    import inspect as _insp
    _bar = _insp.getsource(IB.reqAccountSummaryAsync)
    _acc = _insp.getsource(IB.accountSummaryAsync)
    _bar_uncond = 'acctSummary' not in _bar and 'reqAccountSummary(' in _bar
    _acc_cached = 'if not self.wrapper.acctSummary' in _acc
    if not _bar_uncond:
        print('[FAIL] ib_insync.reqAccountSummaryAsync перестал быть БЕЗУСЛОВНЫМ запросом: '
              'барьер свежести NLV/О-3-Е недостоверен')
        ok = False
    if not _acc_cached:
        print('[ПРЕДУПРЕЖДЕНИЕ] accountSummaryAsync больше не кэширует по-прежнему — '
              'перечитать §12 о барьере сводки')
    print(f'[{"OK  " if ok else "FAIL"}] интерфейс адаптера сверен с живым ib_insync.IB и '
          f'со стабом: {len(used)} имён')
    if no_live:
        print(f'       НЕТ У ЖИВОГО ib_insync.IB: {no_live} — контур упадёт в бою')
    if no_stub:
        print(f'       НЕТ У СТАБА: {no_stub} — стенды не покрывают этот путь')
    return ok


@rinv('доказуемо отложенный ролл закрывает журнал итогом СВОЕЙ сессии',
      needs=lambda r: r['case'] == 'ролл отложен доказуемо: журнал закрыт итогом')
def _r_rollgap_total(r):
    """СОРОК ЧЕТВЁРТЫЙ КРУГ, №5. На этом пути книга сохраняется с НОВЫМ номером сессии и
    сегодняшней датой, после чего исключение уходит наружу — а блок записи ИТОГа лежит ниже
    и не достигался вовсе.

    Дальше складывалась связка двух моих же правок: автопилот по дате книги ставит traded-*
    и разрешает замыкание, а якорь WORM (43-й круг, №7) требует, чтобы журнал был закрыт
    итогом ИМЕННО этой сессии, и книгу отвергает. Постоянный ALARM-backup, closed-* не
    ставится, СЛЕДУЮЩИЙ РОЛЛ БЛОКИРУЕТСЯ.

    Проверяется ровно то, что потребует якорь: последняя строка журнала — ИТОГ, и её дата
    совпадает с last_session сохранённой книги. Сверка идёт с КНИГОЙ, а не с константой:
    иначе стенд закрепил бы дату фикстуры, а не правило.
    """
    rows = r.get('j7_rows') or []
    b = r.get('saved')
    if not rows or b is None:
        return False
    last = rows[-1]
    return (str(last.get('instrument')) == 'ИТОГ'
            and str(last.get('date')) == str(getattr(b, 'last_session', '')))


def run_run(stop_on_first=False):
    """stop_on_first — для мутационного прогона (рецензия 20.08): вердикт «мутацию не поймал
    НИКТО» требует прогнать ВСЕ случаи, а вот «поймана» доказывается ПЕРВЫМ же несогласным
    утверждением. Досрочный выход не ослабляет доказательства и снимает с прогона ~15 минут:
    полная батарея RUN идёт две минуты, а мутаций запуска уже 34."""
    cov, bad = {}, {}
    for case in RUN_CASES:
        try:
            r = (_session_days() if case == 'три сессии подряд'
                 else _session_late_fill() if case == 'позднее исполнение после сессии'
                 else _session_race() if case == 'гонка при замыкании'
                 else _session_route_switch()
                 if case == 'смена маршрута в связке с торговлей'
                 else _session_lock() if case == 'замок между процессами'
                 else _session_statedir() if case == 'пути состояния: один namespace'
                 else _worm_case(case) if case.startswith('worm:')
                 else _rules45_case(case) if case.startswith('правила45:')
                 else _autopilot_case(case) if case.startswith('автопилот:')
                 else _session_run(case))
        except Exception as ex:
            r = dict(case=case, raised=True, error=f'СТЕНД: {type(ex).__name__}: {ex}',
                     placed=0, positions={}, saved=None, provisional=None, lev=None,
                     journal=Path('/nonexistent'))
        for name, fn, needs in RUN:
            if needs is not None and not needs(r):
                continue
            cov[name] = cov.get(name, 0) + 1
            try:
                ok = fn(r)
            except Exception as ex:
                ok = False; name = f'{name} [исключение: {type(ex).__name__}: {ex}]'
            if not ok:
                bad.setdefault(name, []).append(f"{case}: {r.get('error','')[:80]}")
                if stop_on_first:
                    return cov, bad        # мутация поймана — остальные случаи излишни
    return cov, bad


# ---------------------------------------------------------------- переходный исполнитель
# ПЕРЕХОД МЕЖДУ МАРШРУТАМИ — единственное место, где книга существует РАЗОРВАННОЙ: часть
# экспозиции уже продана, часть ещё не куплена. Именно поэтому §8б ограничивает непарную
# дельту одним процентом капитала. Исполнитель рецензировался, но собственных утверждений
# не имел: ни лимит, ни целостность плана, ни повторный запуск после обрыва не проверялись
# ничем. Переход Ф->Е предстоит, и проверять его после первого запуска поздно.
TR = []


def tinv(name, needs=None):
    def deco(fn):
        TR.append((name, fn, needs)); return fn
    return deco


TR_CASES = ('план целых фьючерсов', 'план дробных долей фонда', 'дублированный источник',
            'цена плана вне рыночной полосы', 'цена плана в полосе',
            'брокер без полосы цен',
            'дробный фьючерс в плане', 'исполнение в лимите', 'дробное исполнение фьючерса',
            'повторный запуск после обрыва', 'ИСПОЛНЕНИЕ дробного источника',
            'источник мельче зерна цели', 'битый замер маржи', 'замер без привязки',
            'замер прежней серии', 'замер не покрывает корень', 'замер отсутствует',
            'живой замер покрывает', 'частичный прогресс лота при resume',
            'остаток непарной дельты при resume',
            'предпросмотр resume спрашивает остаток, а не весь план',
            'лимит заявок: покупка №391 не подаётся',
            'лимит заявок: дневной контур уже потратил квоту',
            'маржа цели: фактическая книга дороже отображённой',
            'замер с нечисловой маржой', 'замер устарел',
            'замер: init прежде maint', 'замер: карта поколения неполна',
            'тревога перехода — файл в каталоге автопилота',
            'общее окно закрылось: заявка не подаётся',
            # ТРИДЦАТЬ СЕДЬМОЙ КРУГ, №18: сценария потерянного подтверждения не было ни
            # одного, и поле st['attempted'] не утверждалось нигде — парная мутация
            # attempt_trace_off ничего не наблюдала.
            'подтверждение первой заявки потеряно',
            'компенсация исполнена не полностью',
            'переход задним числом запрещён',
            # ТРИДЦАТЬ ПЕРВЫЙ КРУГ, №13: пин журнала МР защищал путь и inode, но не
            # содержимое — одной валидной строкой CSV разрешался переход маршрута.
            'журнал МР правлен на месте')


class _Unp(dict):
    """Словарь непарной дельты, запоминающий МАКСИМУМ за прогон. Лимит §8б — свойство
    траектории, а не итога: проверять его только в конце значит не проверять вовсе."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.peak = 0.0

    def __setitem__(self, k, v):
        super().__setitem__(k, v)
        tot = sum(abs(x) for x in self.values())
        if tot > self.peak:
            self.peak = tot


class _TrBroker:
    """Брокер перехода: продаёт источник, покупает цель. Дробность задаётся сценарием."""
    def __init__(self, frac_of=None, short_buy=None):
        self.frac_of = frac_of      # инструмент, по которому исполнение дробное
        # НЕДОБОР ПОКУПКИ НА ЦЕЛУЮ ЕДИНИЦУ (двадцатый круг, №2): повадка живого брокера,
        # которую прежние стабы не воспроизводили вовсе — оттого недостача цели ровно на
        # один контракт и доходила до COMPLETE.
        self.short_buy = short_buy
        self.calls = []
        self.n = 0
        # личность счёта (тридцатый круг, №6): переход её требует
        self.account = __import__('os').environ.get('ADDFUT_ACCOUNT') or 'DUTEST01'

    def _f(self, instr, u):
        return u - 0.5 if self.frac_of == instr and u > 0 else u

    def _b(self, instr, u):
        if self.short_buy == instr and u >= 1:
            return u - 1
        return self._f(instr, u)

    def sell_units(self, i, u):
        self.n += 1; self.calls.append(('sell', i, u)); return (f's{self.n}', self._f(i, u))

    def buy_units(self, i, u):
        self.n += 1; self.calls.append(('buy', i, u)); return (f'b{self.n}', self._b(i, u))

    def unit_ref(self, instrument, cls):
        """Полоса долларовой единицы (двадцать девятый круг, №3): стенд повторяет живого
        брокера — цены плана обязаны укладываться в рыночный порядок величины."""
        # Фонды именуются целиком (CBU0, CSPX): срезание цифр и месячных букв превратило
        # бы CBU0 в CB — полосы не нашлось бы и законный план был бы отвергнут.
        tab = {'ZN': 98_560.0, 'ES': 384_280.0, 'MES': 38_428.0,
               'CBU0': 5.0, 'CSBGU0': 5.0, 'CSPX': 700.0}
        name = str(instrument)
        root = ''.join(ch for ch in name if not ch.isdigit()).rstrip('UZHM')
        base = tab.get(name, tab.get(root))
        return None if base is None else (base * 0.5, base * 2.0)

    def cancel_order(self, o): return True
    def open_orders(self): return []
    def net_positions(self): return {}
    def minutes_since(self, k): return 0
    def gross(self, d_fix=None): return 1.50


def _tr_run(case):
    import tempfile
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import transition as TRN

    # КАЛИТКА d_fix (тридцать третий круг, №3): у макета брокера нет .ib, живой доходности
    # взять неоткуда; дверь названа явно, чтобы откат к старой книге Ф не выглядел нормой.
    import os as _osdf
    _osdf.environ.setdefault('ADDFUT_DFIX_TEST', '1')
    out = dict(case=case, raised=False, error='', lots=None, peak=None,
               calls=None, second_calls=None, log=None)
    ZN_U = 98_560.0
    legs_fut = {'Б': dict(src=[('ZNU26', 10, ZN_U)], dst=('CBU0', 5.0, 'ETF'))}
    legs_frac = {'Б': dict(src=[('CBU0', 2_000_000.5, 5.0)], dst=('ZN', ZN_U, 'FUT'))}
    legs_dup = {'Б': dict(src=[('ZNU26', 10, ZN_U), ('ZNU26', 5, ZN_U)], dst=('CBU0', 5.0, 'ETF'))}
    legs_bad = {'Б': dict(src=[('ZNU26', 10.5, ZN_U)], dst=('CBU0', 5.0, 'ETF'))}

    try:
        if case == 'журнал МР правлен на месте':
            # СОДЕРЖИМОЕ, А НЕ ТОЛЬКО ЛИЧНОСТЬ ФАЙЛА (тридцать первый круг, №13).
            # `open(path, 'w')` сохраняет inode, и обширные тесты symlink/hardlink/inode
            # доказывали личность файла, а не неизменность нормативных событий: дописанный
            # OWNER_APPROVE разрешает полный перевод счёта в другой маршрут.
            # Проверяются ОБА конца: законная запись читается, подделанная — отказ.
            import mr_engine as _M31
            import tempfile as _tf31
            td31 = Path(_tf31.mkdtemp(prefix='addfut-mrdg-'))
            keep31 = _M31._STATE['dir']
            _M31.set_state_dir_for_tests(str(td31 / 'install'))
            try:
                j31 = td31 / 'mr.csv'
                j31.write_text('asof,event,detail\n', encoding='utf-8')
                _M31.test_configure(str(j31))
                _M31.append_event(str(j31), '2026-08-08', 'SWITCH_SIGNAL', 'E|s1')
                out['ok_read'] = (len(_M31.journal_rows(str(j31))) == 1)
                with open(j31, 'a', encoding='utf-8') as _f31:
                    _f31.write('2026-08-08,OWNER_APPROVE,E|s1\n')
                try:
                    _M31.journal_rows(str(j31))
                except _M31.JournalCorrupt as ex:
                    out['raised'] = True; out['error'] = str(ex)
            finally:
                _M31.set_state_dir_for_tests(keep31)
        elif case == 'переход задним числом запрещён':
            # ASOF — ЭТО СЕГОДНЯ (двадцать второй круг, №1). Вчерашняя дата отключала
            # часы, праздники и барьер «resume в той же сессии»; проверка стоит РАНЬШЕ
            # чтения журнала, поэтому стенд обходится фиктивными путями и не касается
            # ни брокера, ни диска. Калитку стендов selfcheck (ADDFUT_ASOF_OVERRIDE)
            # здесь снимаем НАМЕРЕННО: иначе защита была бы отключена во всём выпуске.
            import os as _oe
            _keep = _oe.environ.pop('ADDFUT_ASOF_OVERRIDE', None)
            try:
                TRN.execute(object(), '/nonexistent/state.json', 1e6, legs_fut,
                            signal_id='s1', journal='/nonexistent/j.csv',
                            mr_state='/nonexistent/s.csv', asof='2020-01-02')
            except TRN.Incident as ex:
                out['raised'] = True; out['error'] = str(ex)
            except Exception as ex:
                out['raised'] = True; out['error'] = f'{type(ex).__name__}: {ex}'
            finally:
                if _keep is not None:
                    _oe.environ['ADDFUT_ASOF_OVERRIDE'] = _keep
        elif 'замер' in case:
            # МАРЖА ПЕРЕХОДА (шестнадцатый круг, №4; закрыт и старый пробел: у защит
            # двенадцатого-тринадцатого кругов не было ни одного стенда). Замер обязан
            # нести _meta и относиться к сериям ТЕКУЩЕГО живого реестра; дыры существующего
            # замера не добираются константами.
            import os as _os
            import json as _json
            td = Path(tempfile.mkdtemp(prefix='addfut-mrg-'))
            mp, rp = td / 'margins.json', td / 'reg.csv'
            rp.write_text(
                'instrument,sec_type,pair_group,exchange,currency,con_id,local_symbol,'
                'expiry,multiplier,primary_exchange,isin\n'
                'ESU26,FUT,EQ,CME,USD,1,ESU6,20260918,50,,\n'
                'ZNU26,FUT,BOND,CBOT,USD,2,ZNU6,20260921,1000,,\n', encoding='utf-8')
            # ДАТА ЗАМЕРА — БИРЖЕВАЯ, НЕ МАШИННАЯ (грабли §7 CLAUDE.md). Проверка возраста
            # считает от биржевого «сегодня»; фикстура штамповала машинной датой, и на
            # смене суток в Чикаго возраст выходил −1 день — «замер из будущего».
            import feed as _FDm
            _today = _FDm.exchange_today().strftime('%Y-%m-%d')
            if case == 'битый замер маржи':
                mp.write_text('{оборвано', encoding='utf-8')
            elif case == 'замер с нечисловой маржой':
                # ВОСЕМНАДЦАТЫЙ КРУГ, №13 (пара): NaN-требование превращало маржу корня в
                # мусор; sorted/сравнения его не ловили — только явная проверка конечности.
                mp.write_text(_json.dumps({'_meta': {'date': _today, 'account': 'DUTEST01',
                                                     'series': ['ESU26', 'ZNU26'],
                                                     'con_ids': {'ESU26': '1', 'ZNU26': '2'}},
                                           'ESU26': {'init': float('nan')},
                                           'ZNU26': {'init': 2500.0}}), encoding='utf-8')
            elif case == 'замер устарел':
                # ВОСЕМНАДЦАТЫЙ КРУГ, №13 (пара): замер старше 35 дней неотличим от
                # забытого — переход по нему шёл бы по прошлогоднему требованию.
                import datetime as _dtx
                _old = (_dtx.date.today() - _dtx.timedelta(days=60)).isoformat()
                mp.write_text(_json.dumps({'_meta': {'date': _old,
                                                     'series': ['ESU26', 'ZNU26'],
                                                     'con_ids': {'ESU26': '1', 'ZNU26': '2'}},
                                           'ESU26': {'init': 40000.0},
                                           'ZNU26': {'init': 2500.0}}), encoding='utf-8')
            elif case == 'замер: init прежде maint':
                # ВОСЕМНАДЦАТЫЙ КРУГ, №13 (пара): возможность ОТКРЫТЬ книгу определяет
                # НАЧАЛЬНОЕ требование; выбор maint занижал маржу и завышал запас.
                mp.write_text(_json.dumps({'_meta': {'date': _today, 'account': 'DUTEST01',
                                                     'series': ['ESU26', 'ZNU26'],
                                                     'con_ids': {'ESU26': '1', 'ZNU26': '2'}},
                                           'ESU26': {'init': 50000.0, 'maint': 2500.0},
                                           'ZNU26': {'init': 3000.0, 'maint': 1000.0}}),
                              encoding='utf-8')
            elif case == 'замер без привязки':
                mp.write_text(_json.dumps({'ESU26': {'maint': 40000.0},
                                           'ZNU26': {'maint': 2500.0}}), encoding='utf-8')
            elif case == 'замер прежней серии':
                mp.write_text(_json.dumps({'_meta': {'date': _today, 'account': 'DUTEST01', 'series': ['ESZ25'], 'con_ids': {'ESZ25': '9'}},
                                           'ESZ25': {'maint': 30000.0}}), encoding='utf-8')
            elif case == 'замер не покрывает корень':
                mp.write_text(_json.dumps({'_meta': {'date': _today, 'account': 'DUTEST01', 'series': ['ZNU26'], 'con_ids': {'ZNU26': '2'}},
                                           'ZNU26': {'maint': 2500.0}}), encoding='utf-8')
            elif case == 'замер: карта поколения неполна':
                # ТРИДЦАТЬ ЧЕТВЁРТЫЙ КРУГ, №14: entries и _meta.series ПОЛНЫ, а con_ids —
                # правильное непустое ПОДМНОЖЕСТВО (ZN пропущен). Ровно дефект до правки
                # 33-го круга: достаточно убрать серию с исправленным con_id, и старый
                # замер ноги Б проходит ворота целиком.
                mp.write_text(_json.dumps({'_meta': {'date': _today, 'account': 'DUTEST01',
                                                     'series': ['ESU26', 'ZNU26'],
                                                     'con_ids': {'ESU26': '1'}},
                                           'ESU26': {'maint': 40000.0},
                                           'ZNU26': {'maint': 2500.0}}), encoding='utf-8')
            elif case == 'живой замер покрывает':
                mp.write_text(_json.dumps({'_meta': {'date': _today, 'account': 'DUTEST01',
                                                     'series': ['ESU26', 'ZNU26'],
                                                     'con_ids': {'ESU26': '1', 'ZNU26': '2'}},
                                           'ESU26': {'maint': 40000.0},
                                           'ZNU26': {'maint': 2500.0}}), encoding='utf-8')
            keepm = _os.environ.get('ADDFUT_MARGINS')
            keepr = _os.environ.get('ADDFUT_REGISTRY')
            # ПИН ЗАДАЁТСЯ ЯВНО (правило 5 проекта): иначе _machine_pin() ушёл бы читать
            # МАШИННЫЙ ~/.addfut/account.txt, и стенд начал бы зависеть от живого состояния.
            keepa = _os.environ.get('ADDFUT_ACCOUNT')
            _os.environ['ADDFUT_ACCOUNT'] = 'DUTEST01'
            _os.environ['ADDFUT_MARGINS'] = str(mp)
            _os.environ['ADDFUT_REGISTRY'] = str(rp)
            try:
                # РЕЕСТР СТЕНДА НЕСЁТ СЕРИИ, КАК ЖИВОЙ (двадцать шестой круг, №21):
                # корневой реестр из пяти строк не может одновременно обслуживать планы
                # с ZNU26/MESU26 и проверку личности — и именно на этом расхождении
                # ломался переход Е→Ф.
                reg = {'ESU26': dict(sec_type='FUT'), 'ZNU26': dict(sec_type='FUT'),
                       'MESU26': dict(sec_type='FUT'),
                       'ES': dict(sec_type='FUT'), 'ZN': dict(sec_type='FUT')}
                out['margin'] = TRN.book_margin({'ES': 1, 'ZN': 2}, reg)
            finally:
                for k, v in (('ADDFUT_MARGINS', keepm), ('ADDFUT_REGISTRY', keepr),
                             ('ADDFUT_ACCOUNT', keepa)):
                    _os.environ.pop(k, None)
                    if v is not None:
                        _os.environ[k] = v
        elif case == 'частичный прогресс лота при resume':
            # Семнадцатый круг, №1: продано 3 из 10 до обрыва — повтор обязан продать
            # РОВНО 7, а не 10; st['partial'] строится resume-блоком и потребляется циклом.
            lots = TRN.plan_lots(legs_fut, 10e6)
            lim = TRN.unpaired_limit(legs_fut, 10e6)
            br = _TrBroker()
            st = dict(done=[], order_ids=[], log=[], executed_usd=0.0,
                      partial={'ZNU26': 3})     # №21: имя с серией, как в плане
            sp = Path(tempfile.mkdtemp(prefix='addfut-tr-')) / 'st.json'
            TRN._run_lots(br, lots, st, sp, lim, _Unp({k: 0.0 for k in legs_fut}), {},
                          lambda msg, cancel=True: (_ for _ in ()).throw(TRN.Incident(msg)))
            out['sold'] = sum(q for k, i, q in st['log'] if k == 'sell')
            out['partial_left'] = dict(st.get('partial', {}))
        elif case == 'предпросмотр resume спрашивает остаток, а не весь план':
            # ВНУТРИЛОТОВЫЙ ПРОГРЕСС (45-й круг, №3, P0): остаток обязан вычитать и
            # st['partial'], а не только завершённые лоты. Цепочка из четырёх состояний
            # одного плана ловит и «не вычитает вовсе», и «вычитает дважды».
            _plan45 = TRN.plan_lots(legs_fut, 10e6)
            _src45 = _plan45[0]['src']
            _key45 = f"{_src45}:{_plan45[0]['step']}"
            _s0 = TRN.pv_remainder(_plan45, [])
            _s1 = TRN.pv_remainder(_plan45, [_key45])
            _s2 = TRN.pv_remainder(_plan45, [_key45], {_src45: 3})
            _s3 = TRN.pv_remainder(_plan45, [], {_src45: sum(float(l['units']) for l in _plan45)})
            _sum45 = lambda d: sum(d.values())
            out['pv_цепочка_убывает'] = _sum45(_s0) > _sum45(_s1) > _sum45(_s2) > 0
            out['pv_пусто_в_конце'] = not _s3
            # СОРОК ЧЕТВЁРТЫЙ КРУГ, №4 (P0): _pv_orders строился из ВСЕХ лотов до разбора
            # фактического прогресса. При частично исполненном переходе целевая позиция уже
            # куплена, а whatIf спрашивал полную цель ПОВЕРХ неё: законный resume выглядел
            # книгой в 150-200% цели, получал POSTPONED, а после третьего отказа — MIXED.
            lots = TRN.plan_lots(legs_fut, 10e6)
            _k0 = f"{lots[0]['src']}:{lots[0]['step']}"
            out['pv_full'] = TRN.pv_remainder(lots, [])
            out['pv_part'] = TRN.pv_remainder(lots, [_k0])
            out['pv_all'] = TRN.pv_remainder(lots, [f"{l['src']}:{l['step']}" for l in lots])
        elif case == 'остаток непарной дельты при resume':
            # Семнадцатый круг, №2: перенесённый из resume остаток занимает лимит §8б —
            # обнулённый остаток дал бы разрыв поверх разрыва.
            lots = TRN.plan_lots(legs_fut, 10e6)
            lim = TRN.unpaired_limit(legs_fut, 10e6)
            br = _TrBroker()
            st = dict(done=[], order_ids=[], log=[], executed_usd=0.0)
            sp = Path(tempfile.mkdtemp(prefix='addfut-tr-')) / 'st.json'
            unp = _Unp({k: 0.0 for k in legs_fut})
            unp['Б'] = lim - 1_000.0          # почти весь лимит уже занят остатком resume

            def _f(msg, cancel=True):
                raise TRN.Incident(msg)

            TRN._run_lots(br, lots, st, sp, lim, unp, {}, _f)
        elif case == 'лимит заявок: покупка №391 не подаётся':
            # ДЕВЯТНАДЦАТЫЙ КРУГ, №8: при 389 занятых заявках продажа №390 законна, а
            # ПОКУПКА №391 обязана упереться в ворота ДО подачи — прежде она уходила
            # брокеру без проверки, и отказ IB оставлял непарную позицию.
            lots = TRN.plan_lots(legs_fut, 10e6)
            lim = TRN.unpaired_limit(legs_fut, 10e6)
            br = _TrBroker()
            st = dict(done=[], order_ids=[f'x{i}' for i in range(TRN.ORDERS_PER_DAY - 1)],
                      log=[], executed_usd=0.0)
            sp = Path(tempfile.mkdtemp(prefix='addfut-tr-')) / 'st.json'

            def _f8(msg, cancel=True):
                raise TRN.Incident(msg)

            try:
                TRN._run_lots(br, lots, st, sp, lim, _Unp({k: 0.0 for k in legs_fut}), {},
                              _f8)
            finally:
                out['calls'] = list(br.calls)
        elif case == 'лимит заявок: дневной контур уже потратил квоту':
            # СОРОК ЧЕТВЁРТЫЙ КРУГ, №11. Лимит 390 принадлежит СЧЁТУ ЗА ДЕНЬ, а считался как
            # len(st['order_ids']) — заявки только текущего файла прогресса. Утренний
            # ребаланс, ролл и предыдущий переход того же дня в счёт не шли: при 389
            # израсходованных счётом продажа источника проходила как локальная №390, а
            # парная покупка была для счёта №391 и отвергалась ПОСЛЕ продажи — непарная
            # позиция ровно на границе, ради которой ворота и заведены.
            # Ставим 5 строк §7 за СЕГОДНЯШНЮЮ биржевую дату (это и есть дневной контур) и
            # 386 заявок в исполнении: локально 386 < 390, по счёту 391 — ворота обязаны
            # остановить переход ДО первой заявки.
            import journal as _J11
            import feed as _FD11
            import state as _ST11
            # КЭШ СБРАСЫВАЕТСЯ ЯВНО (рецензия 19.08): модульный _J7_TODAY переживает
            # границу «чистый базлайн -> мутант» внутри одного процесса мутационного
            # прогона, и мутант, живущий ПОД кэшем, был бы объявлен пойманным по чужой
            # причине. Сегодня его спасает то, что фикстура каждый раз пересоздаёт журнал и
            # даёт свежий mtime_ns, — но это совпадение фикстуры, а не механизм.
            TRN._J7_TODAY.update(key=None, n=0)
            _d11 = _FD11.exchange_today().strftime('%Y-%m-%d')
            _jp11 = _ST11.lock_dir() / 'journal-F.csv'
            for _i11 in range(5):
                _J11.append(_jp11, dict(
                    date=_d11, leg='Б', instrument='ZNU26', qty=1, px_order='', px_fill='',
                    commission='', reason='', nav='', leverage='',
                    roll_spread_near='', roll_spread_far='', note='заявка дневного контура'))
            out['j7_rows'] = len(_J11.read(_jp11))
            lots = TRN.plan_lots(legs_fut, 10e6)
            lim = TRN.unpaired_limit(legs_fut, 10e6)
            br = _TrBroker()
            st = dict(done=[], order_ids=[f'y{i}' for i in range(TRN.ORDERS_PER_DAY - 4)],
                      log=[], executed_usd=0.0)
            sp = Path(tempfile.mkdtemp(prefix='addfut-tr-')) / 'st.json'

            def _f11(msg, cancel=True):
                raise TRN.Incident(msg)

            try:
                TRN._run_lots(br, lots, st, sp, lim, _Unp({k: 0.0 for k in legs_fut}), {},
                              _f11)
            finally:
                out['calls'] = list(br.calls)
                try:
                    _jp11.unlink()          # стенд не оставляет следов в каталоге стендов
                except OSError:
                    pass
        elif case == 'компенсация исполнена не полностью':
            # ДВАДЦАТЫЙ КРУГ, №2: брокер недобирает КАЖДУЮ покупку на единицу. Основная
            # покупка оставляет недостачу, компенсация её не закрывает — и прежде это
            # проходило: исполнение компенсации ни с чем не сверялось, а допуск был в
            # ЦЕЛУЮ единицу цели. Итог — COMPLETE при недостающем контракте.
            lots = TRN.plan_lots(legs_fut, 10e6)
            lim = TRN.unpaired_limit(legs_fut, 10e6)
            br = _TrBroker(short_buy='CBU0')
            st = dict(done=[], order_ids=[], log=[], executed_usd=0.0)
            sp = Path(tempfile.mkdtemp(prefix='addfut-tr-')) / 'st.json'

            def _f2c(msg, cancel=True):
                raise TRN.Incident(msg)

            try:
                TRN._run_lots(br, lots, st, sp, lim, _Unp({k: 0.0 for k in legs_fut}), {},
                              _f2c)
            finally:
                out['calls'] = list(br.calls)
        elif case == 'подтверждение первой заявки потеряно':
            # ТРИДЦАТЬ ПЯТЫЙ КРУГ, №1 (пара) И ТРИДЦАТЬ СЕДЬМОЙ, №18. Брокер ИСПОЛНИЛ
            # заявку и упал до возврата номера: order_ids пуст, позиции ещё старые, отчёта
            # дня нет. Без следа попытки такой исход неотличим от «мы ничего не подавали» и
            # заканчивался бы ДОКАЗАННЫМ чистым ABORT — с уже проданной ногой у брокера.
            # Отметка обязана лечь ДО вызова брокера и пережить исключение.
            lots = TRN.plan_lots(legs_fut, 10e6)
            lim = TRN.unpaired_limit(legs_fut, 10e6)

            class _BLost(_TrBroker):
                def sell_units(self, i, u):
                    self.calls.append(('sell', i, u))
                    raise RuntimeError('связь оборвана после исполнения, номер не получен')

            br = _BLost()
            st = dict(done=[], order_ids=[], log=[], executed_usd=0.0, attempted=0)
            sp = Path(tempfile.mkdtemp(prefix='addfut-tr-')) / 'st.json'

            def _fl(msg, cancel=True):
                raise TRN.Incident(msg)

            try:
                TRN._run_lots(br, lots, st, sp, lim, _Unp({k: 0.0 for k in legs_fut}), {}, _fl)
            finally:
                out['calls'] = list(br.calls)
                out['attempted'] = int(st.get('attempted', 0))
                out['order_ids'] = list(st.get('order_ids') or [])
        elif case == 'общее окно закрылось: заявка не подаётся':
            # ДВАДЦАТЫЙ КРУГ, №7: окно перехода было БУЛЕВЫМ аргументом, проверенным один
            # раз до preview, preflight и сотен заявок. Здесь край общего окна LSE/CME уже
            # позади — ворота обязаны остановить переход ДО первой заявки, иначе продажа
            # фьючерса уходит брокеру, а покупка фонда идёт на закрытую площадку.
            import pandas as _pdw
            lots = TRN.plan_lots(legs_fut, 10e6)
            lim = TRN.unpaired_limit(legs_fut, 10e6)
            br = _TrBroker()
            st = dict(done=[], order_ids=[], log=[], executed_usd=0.0)
            sp = Path(tempfile.mkdtemp(prefix='addfut-tr-')) / 'st.json'

            def _fw(msg, cancel=True):
                raise TRN.Incident(msg)

            _past = _pdw.Timestamp.now(tz='America/Chicago') - _pdw.Timedelta(minutes=1)
            try:
                TRN._run_lots(br, lots, st, sp, lim, _Unp({k: 0.0 for k in legs_fut}), {},
                              _fw, window_till=_past)
            finally:
                out['calls'] = list(br.calls)
        elif case == 'маржа цели: фактическая книга дороже отображённой':
            # ДЕВЯТНАДЦАТЫЙ КРУГ, №2: исполнитель покупает единицы ЦЕЛИ ПЛАНА (MES-сетку),
            # а отображённая книга ES+MES существует только в preflight. Запас обязан
            # считаться по ХУДШЕЙ из двух физических книг: живой замер может дать MES
            # дороже ES/10 — тогда фактическая книга дороже отображённой.
            import os as _os
            import json as _json
            td = Path(tempfile.mkdtemp(prefix='addfut-map-'))
            mp2, rp2 = td / 'margins.json', td / 'reg.csv'
            rp2.write_text(
                'instrument,sec_type,pair_group,exchange,currency,con_id,local_symbol,'
                'expiry,multiplier,primary_exchange,isin\n'
                'ESU26,FUT,EQ,CME,USD,1,ESU6,20260918,50,,\n'
                'MESU26,FUT,EQ,CME,USD,2,MESU6,20260918,5,,\n', encoding='utf-8')
            import feed as _FDm2
            _today2 = _FDm2.exchange_today().strftime('%Y-%m-%d')   # биржевая дата
            mp2.write_text(_json.dumps({'_meta': {'date': _today2, 'account': 'DUTEST01',
                                                  'series': ['ESU26', 'MESU26'],
                                                  'con_ids': {'ESU26': '1', 'MESU26': '2'}},
                                        'ESU26': {'init': 40000.0},
                                        'MESU26': {'init': 4500.0}}), encoding='utf-8')
            keepm = _os.environ.get('ADDFUT_MARGINS')
            keepr = _os.environ.get('ADDFUT_REGISTRY')
            # ПИН ЗАДАЁТСЯ ЯВНО (правило 5 проекта): иначе _machine_pin() ушёл бы читать
            # МАШИННЫЙ ~/.addfut/account.txt, и стенд начал бы зависеть от живого состояния.
            keepa = _os.environ.get('ADDFUT_ACCOUNT')
            _os.environ['ADDFUT_ACCOUNT'] = 'DUTEST01'
            _os.environ['ADDFUT_MARGINS'] = str(mp2)
            _os.environ['ADDFUT_REGISTRY'] = str(rp2)
            try:
                legs2 = {'EQ': dict(src=[('CSPX', 199.0, 700.0)],
                                    dst=('MESU26', 4802.0, 'FUT'))}
                reg2 = {'MESU26': dict(sec_type='FUT'), 'ESU26': dict(sec_type='FUT'),
                        'ZNU26': dict(sec_type='FUT'),
                        'MES': dict(sec_type='FUT'), 'ES': dict(sec_type='FUT'),
                        'CSPX': dict(sec_type='STK')}
                out['info'] = TRN.preflight_margin_orders(
                    legs2, TRN.plan_lots(legs2, 1e6), 1e6, reg2, 'F',
                    lim=TRN.unpaired_limit(legs2, 1e6))
            finally:
                for k, v in (('ADDFUT_MARGINS', keepm), ('ADDFUT_REGISTRY', keepr),
                             ('ADDFUT_ACCOUNT', keepa)):
                    _os.environ.pop(k, None)
                    if v is not None:
                        _os.environ[k] = v
        elif case == 'тревога перехода — файл в каталоге автопилота':
            # ДЕВЯТНАДЦАТЫЙ КРУГ, №13: MIXED после публикации книги/route.txt невидим
            # ежедневному контуру — тревога обязана лечь файлом туда, где её ищет автопилот.
            import os as _os
            td = tempfile.mkdtemp(prefix='addfut-al-')
            keepl = _os.environ.get('ADDFUT_LOCK_DIR')
            _os.environ['ADDFUT_LOCK_DIR'] = td
            (__import__('pathlib').Path(td) / 'route.txt').write_text('F')
            try:
                out['alarm_extra'] = TRN._alarm_transition('2026-08-14', 'проверка стендом')
                out['alarm_file'] = (Path(td) /
                                     'ALARM-transition-2026-08-14.txt').exists()
            finally:
                _os.environ.pop('ADDFUT_LOCK_DIR', None)
                if keepl is not None:
                    _os.environ['ADDFUT_LOCK_DIR'] = keepl
        elif case == 'план целых фьючерсов':
            out['lots'] = TRN.plan_lots(legs_fut, 10e6)
        elif case == 'план дробных долей фонда':
            out['lots'] = TRN.plan_lots(legs_frac, 10e6)
        elif case == 'дублированный источник':
            out['lots'] = TRN.plan_lots(legs_dup, 10e6)
        elif case == 'дробный фьючерс в плане':
            out['lots'] = TRN.plan_lots(legs_bad, 10e6)
        elif case == 'цена плана вне рыночной полосы':
            # ДВАДЦАТЬ ДЕВЯТЫЙ КРУГ, №3: цену цели занижаем в десять раз. Прежде это
            # проходило — dprice проверялся лишь на положительность, а число долей цели,
            # лимит §8б и финальная сверка считались по нему же (план сверялся с планом).
            _bad_px = {'Б': dict(src=[('ZNU26', 10, ZN_U)], dst=('CBU0', 0.5, 'ETF'))}
            TRN.check_plan_prices(_TrBroker(), _bad_px, 'FUT')
        elif case == 'цена плана в полосе':
            out['ok'] = TRN.check_plan_prices(_TrBroker(), legs_fut, 'FUT')
        elif case == 'брокер без полосы цен':
            # Защиту нельзя выключить, не отдав unit_ref: иначе её отключал бы сам
            # вызывающий, чьи числа она и проверяет.
            class _NoBand:
                pass
            TRN.check_plan_prices(_NoBand(), legs_fut, 'FUT')
        else:
            # ДРОБНЫЙ ИСТОЧНИК ПРОГОНЯЕТСЯ ЧЕРЕЗ ИСПОЛНЕНИЕ, а не только через планировщик:
            # прежняя проверка сверяла сумму лотов и объявляла выход из маршрута Е рабочим,
            # тогда как в цикле остаток 0,5 округлялся вверх и уводил источник в короткую.
            frac_case = (case == 'ИСПОЛНЕНИЕ дробного источника')
            # Цель соизмерима с источником, поэтому план распадается на несколько лотов и
            # ПОСЛЕДНИЙ оказывается дробным (5+5+5+5+0,5). Если весь остаток уходит одной
            # заявкой, опасное место — округление хвоста вверх — не исполняется вовсе, и
            # проверка ничего не доказывает.
            if case == 'источник мельче зерна цели':
                # остаток продажи меньше половины зерна цели: want=0, покупка не подаётся
                legs = {'Б': dict(src=[('CBU0', 30, 5.0)], dst=('ZN', ZN_U, 'FUT'))}
            elif frac_case:
                legs = {'Б': dict(src=[('CBU0', 20.5, 5.0)], dst=('CSPX', 6.0, 'ETF'))}
            else:
                legs = legs_fut
            lots = TRN.plan_lots(legs, 10e6)
            lim = TRN.unpaired_limit(legs, 10e6)
            # ИМЯ ДРОБЯЩЕГОСЯ ИНСТРУМЕНТА — ПОЛНОЕ (двадцать шестой круг, №21): ноги
            # переведены на поставочные серии, и брокер, дробящий 'ZN', больше не совпадал
            # ни с одним инструментом плана — стенд молчал, ничего не проверяя.
            frac = 'ZNU26' if case == 'дробное исполнение фьючерса' else None
            br = _TrBroker(frac_of=frac)
            st = dict(done=[], order_ids=[], log=[], executed_usd=0.0)
            unp = _Unp({k: 0.0 for k in legs})
            sp = Path(tempfile.mkdtemp(prefix='addfut-tr-')) / 'st.json'

            def fail(msg):
                raise TRN.Incident(msg)

            TRN._run_lots(br, lots, st, sp, lim, unp, {}, fail)
            out['peak'] = unp.peak; out['calls'] = list(br.calls); out['log'] = list(st['log'])
            out['sold'] = sum(q for k, i, q in st['log'] if k == 'sell')
            if case == 'повторный запуск после обрыва':
                # ТО ЖЕ состояние подаётся снова: завершённые лоты не должны переисполняться.
                br2 = _TrBroker()
                TRN._run_lots(br2, lots, st, sp, lim, _Unp({k: 0.0 for k in legs}), {}, fail)
                out['second_calls'] = list(br2.calls)
    except Exception as ex:
        out['raised'] = True; out['error'] = f'{type(ex).__name__}: {ex}'
    return out


@tinv('правка журнала МР «на месте» ловится, законная запись проходит',
      needs=lambda r: r['case'] == 'журнал МР правлен на месте')
def _tr31(r):
    """ТРИДЦАТЬ ПЕРВЫЙ КРУГ, №13. Единственный источник действующего маршрута и одобрений
    перехода не имел защиты содержимого: пин сверял путь, st_dev/st_ino, symlink и
    hardlink — то есть ЛИЧНОСТЬ файла. Синтаксически корректная замена OWNER_APPROVE,
    TRANSITION_COMPLETE, цели или sid проходила все проверки, и одной строкой CSV
    разрешался полный перевод счёта в другой маршрут.

    Проверяется пара: законная запись читается (иначе заверение сломало бы штатный путь —
    класс №11 тридцатого круга), подделанная — отказ с называнием причины.
    """
    return bool(r.get('ok_read')) and r['raised'] and 'мимо append_event' in r['error']


@tinv('недобор компенсации не проходит в успешный переход',
      needs=lambda r: r['case'] == 'компенсация исполнена не полностью')
def _t_comp(r):
    """ДВАДЦАТЫЙ КРУГ, №2. Прежде исполнение компенсации не сравнивалось с заказанным, а
    допуск пары и финальной сверки был в ЦЕЛУЮ единицу цели — недостача ровно одного
    контракта (около 1% NLV при минимальном размере перехода) доходила до COMPLETE и не
    исправлялась ежедневной полосой 10%. Проверяется ПРИЧИНА: остановка по недостаче, а
    не по лимиту или окну."""
    return (r['raised']
            and ('компенсаци' in r['error'] or 'не выровнена' in r['error'])
            and ('исполнено' in r['error'] or 'половины единицы' in r['error']))


@tinv('за краем общего окна LSE/CME заявка перехода не подаётся',
      needs=lambda r: r['case'] == 'общее окно закрылось: заявка не подаётся')
def _t_window(r):
    """ДВАДЦАТЫЙ КРУГ, №7. Проверяются ПРИЧИНА и НОЛЬ ЗАЯВОК: остановка по любому другому
    поводу (лимит 390, лимит §8б) защитой окна не является, а одна ушедшая заявка означает
    непарную позицию на закрытой площадке."""
    return (r['raised'] and 'окно LSE/CME закрыто' in r['error']
            and not r.get('calls'))


@tinv('план покрывает РОВНО все единицы источника',
      needs=lambda r: r['case'] == 'план целых фьючерсов')
def _t1(r):
    """Ни потери, ни округления вверх. Округление вверх заставляло продать больше, чем есть,
    и уводило источник в короткую позицию."""
    if r['raised'] or not r['lots']:
        return False
    return abs(sum(l['units'] for l in r['lots']) - 10) < 1e-9


@tinv('дробные доли фонда планируются целиком',
      needs=lambda r: r['case'] == 'план дробных долей фонда')
def _t2(r):
    """Безусловное требование целых единиц делало ВЫХОД из маршрута Е невозможным ещё до
    первой заявки: законная книга 2 000 000,5 доли отвергалась планировщиком."""
    if r['raised'] or not r['lots']:
        return False
    return abs(sum(l['units'] for l in r['lots']) - 2_000_000.5) < 1e-6


@tinv('дублированный источник в плане отвергается',
      needs=lambda r: r['case'] == 'дублированный источник')
def _t3(r):
    return r['raised'] and 'дублированный' in r['error']


@tinv('дробное количество фьючерса в плане отвергается',
      needs=lambda r: r['case'] == 'дробный фьючерс в плане')
def _t4(r):
    return r['raised'] and 'целыми' in r['error']


@tinv('цена плана вне рыночной полосы отвергается',
      needs=lambda r: r['case'] == 'цена плана вне рыночной полосы')
def _t3a(r):
    """ДВАДЦАТЬ ДЕВЯТЫЙ КРУГ, №3. Заниженная в десять раз цена цели даёт десятикратную
    покупку, а сверка got_units*dprice всё равно сходится — план доказывал сам себя."""
    return r['raised'] and 'вне рыночной полосы' in r['error']


@tinv('законная цена плана полосу проходит',
      needs=lambda r: r['case'] == 'цена плана в полосе')
def _t3b(r):
    """Полоса обязана быть проходимой: защита, отвергающая всё, останавливает переход."""
    return (not r['raised']) and r.get('ok') is True


@tinv('брокер без полосы цен к переходу не допускается',
      needs=lambda r: r['case'] == 'брокер без полосы цен')
def _t3c(r):
    return r['raised'] and 'unit_ref' in r['error']


@tinv('непарная дельта НИ РАЗУ не превышает лимит §8б',
      needs=lambda r: r['case'] == 'исполнение в лимите')
def _t5(r):
    """Лимит — свойство ТРАЕКТОРИИ. Проверка только по итогу пропускает разрыв книги в
    середине перехода, а именно он и ограничен §8б."""
    if r['raised']:
        return False
    return r['peak'] is not None and r['peak'] <= 0.01 * 10e6 + 1e-6


@tinv('дробное исполнение фьючерса — инцидент',
      needs=lambda r: r['case'] == 'дробное исполнение фьючерса')
def _t6(r):
    return r['raised'] and 'дробное исполнение' in r['error']


@tinv('дробный источник продаётся РОВНО, без округления вверх',
      needs=lambda r: r['case'] == 'ИСПОЛНЕНИЕ дробного источника')
def _t8(r):
    """Продано должно быть ровно 20,5 доли. Округление вверх до 21 уводит источник в
    короткую позицию — то есть создаёт экспозицию, которой у владельца нет."""
    if r['raised']:
        return False
    return r.get('sold') is not None and abs(r['sold'] - 20.5) < 1e-9


@tinv('нулевая покупка не подаётся брокеру',
      needs=lambda r: r['case'] == 'источник мельче зерна цели')
def _t9(r):
    """Живой адаптер отверг бы нулевую заявку, и уже проданный источник повисал бы в MIXED
    (одиннадцатый круг, №9): остаток обязан копиться, а не превращаться в buy(0)."""
    if r['raised']:
        return False
    return (not any(k == 'buy' and q == 0 for k, i, q in (r['log'] or []))
            and any(k == 'buy_skip_zero' for k, i, q in (r['log'] or [])))


@tinv('повторный запуск не переисполняет завершённые лоты',
      needs=lambda r: r['case'] == 'повторный запуск после обрыва')
def _t7(r):
    """Состояние пишется атомарно после каждого шага именно ради этого: обрыв посреди
    перехода не должен приводить к ДВОЙНОЙ продаже при возобновлении."""
    return not r['raised'] and r['second_calls'] == []


@tinv('прерванный лот допродаёт РОВНО остаток',
      needs=lambda r: r['case'] == 'частичный прогресс лота при resume')
def _t_p1(r):
    """Семнадцатый круг, №1: повтор целого лота продавал уже исполненную часть заново —
    короткая позиция источника на величину прогресса."""
    return (not r['raised'] and r.get('sold') == 7
            and not any(v for v in (r.get('partial_left') or {}).values()))


@tinv('предпросмотр resume спрашивает ОСТАТОК плана, а не весь план',
      needs=lambda r: r['case'] == 'предпросмотр resume спрашивает остаток, а не весь план')
def _t_pv(r):
    """СОРОК ЧЕТВЁРТЫЙ КРУГ, №4 (P0). Правило вынесено в pv_remainder ради ОДНОЙ точки
    мутации: пока оно жило внутри цикла сборки заявок, мутировать его можно было только
    вместе с соседним кодом.

    Проверяются три состояния, и все три обязаны различаться: свежий переход спрашивает
    весь план, частично исполненный — строго меньше, полностью исполненный — ничего.
    Равенство полного и частичного и есть дефект.
    """
    if r['raised']:
        return False
    full, part, allд = r.get('pv_full'), r.get('pv_part'), r.get('pv_all')
    if not full or part is None or allд is None:
        return False
    if allд:                                   # всё исполнено — остатка быть не может
        return False
    _sf = sum(full.values())
    _sp = sum(part.values())
    return _sf > 0 and _sp > 0 and _sp < _sf - 1e-9


@tinv('остаток непарной дельты resume занимает лимит §8б',
      needs=lambda r: r['case'] == 'остаток непарной дельты при resume')
def _t_p2(r):
    """Семнадцатый круг, №2: обнулённый остаток освобождал лимит — новый проход строил
    разрыв поверх старого; с переносом остатка первый же шаг обязан упереться в §8б."""
    return r['raised'] and 'нет места под лимитом' in r['error']


@tinv('остаток предпросмотра вычитает и внутрилотовый прогресс',
      needs=lambda r: r['case'] == 'предпросмотр resume спрашивает остаток, а не весь план')
def _t45pv(r):
    """СОРОК ПЯТЫЙ КРУГ, №3 (P0). Из предпросмотра исключались только ЦЕЛИКОМ
    завершённые лоты; st['partial'] — проданные 3 из 10 внутри лота — нет. Частично
    исполненный переход просматривался как покупка ПОЛНОЙ цели поверх уже купленной части:
    ложные POSTPONED и MIXED на третьем. Мутации у этого правила не было вовсе, хотя
    комментарий обещал «одну точку мутации».

    Проверяется цепочка из четырёх состояний одного плана: чистый, лот завершён, внутри
    второго продано 3 из 10, продано всё. Каждое следующее обязано быть СТРОГО меньше
    предыдущего, а последнее — пустым: монотонность ловит и «не вычитает вовсе», и
    «вычитает дважды»."""
    return bool(r.get('pv_цепочка_убывает')) and bool(r.get('pv_пусто_в_конце'))


@tinv('лимит 390 держит и ПОКУПКУ: заявка №391 не подаётся',
      needs=lambda r: r['case'] == 'лимит заявок: покупка №391 не подаётся')
def _t_g1(r):
    """Квота проверяется НА ПАРУ (двадцать шестой круг, №2).

    Прежний стенд закреплял ДЕФЕКТ как ожидаемый исход: продажа №390 проходила, покупка
    №391 отвергалась — источник продан, цель не куплена, непарная дельта жила до следующей
    сессии. Правильный исход: НИ ОДНОЙ заявки, отказ ДО необратимой продажи.
    """
    calls = r.get('calls') or []
    return (r['raised'] and 'дневной лимит' in r['error']
            and not any(k in ('sell', 'buy') for k, *_ in calls))


@tinv('квота дня считается по СЧЁТУ, а не по файлу прогресса',
      needs=lambda r: r['case'] == 'лимит заявок: дневной контур уже потратил квоту')
def _t_g44(r):
    """СОРОК ЧЕТВЁРТЫЙ КРУГ, №11. Локально заявок 386 — меньше 390, и прежний счёт пустил
    бы переход; вместе со строками §7 дневного контура их 391. Требуется: отказ ДО первой
    заявки, отказ ИМЕННО по дневному лимиту, и в фикстуре действительно были строки §7 —
    иначе стенд повторял бы соседний случай."""
    calls = r.get('calls') or []
    return (r['raised'] and 'дневной лимит' in r['error']
            and int(r.get('j7_rows') or 0) >= 5
            and not any(k in ('sell', 'buy') for k, *_ in calls))


@tinv('запас preflight — по худшей из отображённой и фактической книги',
      needs=lambda r: r['case'] == 'маржа цели: фактическая книга дороже отображённой')
def _t_g2(r):
    """Девятнадцатый круг, №2: исполнение покупает MES-сетку (30 MES по 4500 = 135 000),
    отображённая книга 3 ES = 120 000; preflight обязан считать по 135 000 — иначе
    доказывается безопасность ДРУГОЙ физической книги."""
    info = r.get('info')
    return (not r['raised'] and info is not None
            and abs(info['margin_usd'] - 135_000.0) < 1e-6)


@tinv('тревога перехода пишется файлом в каталог автопилота',
      needs=lambda r: r['case'] == 'тревога перехода — файл в каталоге автопилота')
def _t_g3(r):
    """Девятнадцатый круг, №13: MIXED после публикации книги должен останавливать
    автопилот — ALARM-transition-*.txt в каталоге замка, успех = пустой довесок."""
    return (not r['raised'] and r.get('alarm_extra') == ''
            and r.get('alarm_file') is True)


@tinv('нечисловой замер маржи — отказ перехода',
      needs=lambda r: r['case'] == 'замер с нечисловой маржой')
def _t_m7(r):
    """Восемнадцатый круг, №13 (пара): NaN-требование не смеет превращаться в маржу."""
    return r['raised'] and 'повреждён' in r['error']


@tinv('устаревший замер маржи — отказ перехода',
      needs=lambda r: r['case'] == 'замер устарел')
def _t_m8(r):
    """Восемнадцатый круг, №13 (пара): замер старше 35 дней неотличим от забытого."""
    return r['raised'] and 'устарел' in r['error']


@tinv('маржа серии — init прежде maint',
      needs=lambda r: r['case'] == 'замер: init прежде maint')
def _t_m9(r):
    """Восемнадцатый круг, №13 (пара): открытие книги определяет НАЧАЛЬНОЕ требование
    (50 000 + 2×3 000), а не поддерживающее (2 500 + 2×1 000)."""
    return not r['raised'] and r.get('margin') == 50_000.0 + 2 * 3_000.0


@tinv('битый замер маржи — отказ перехода',
      needs=lambda r: r['case'] == 'битый замер маржи')
def _t_m1(r):
    """Тринадцатый круг, №5 — впервые ПОД СТЕНДОМ: молчаливые константы вместо живого
    замера разрешали переход по фиктивному запасу."""
    return r['raised'] and 'повреждён' in r['error']


@tinv('замер без привязки — отказ перехода',
      needs=lambda r: r['case'] == 'замер без привязки')
def _t_m2(r):
    """Шестнадцатый круг, №4: файл без _meta (дата, серии) неотличим от устаревшего."""
    return r['raised'] and 'без привязки' in r['error']


@tinv('неполная карта поколения замера не проходит',
      needs=lambda r: r['case'] == 'замер: карта поколения неполна')
def _t34(r):
    """ТРИДЦАТЬ ЧЕТВЁРТЫЙ КРУГ, №14. Полнота con_ids была реализована в 33-м круге, но не
    наблюдалась ничем: сценария, где карта — правильное ПОДМНОЖЕСТВО замера, не было, и
    регрессия «сверяем только перечисленные ключи» прошла бы батарею. Денежное следствие:
    старая маржа ноги Б разрешает Е→Ф при фактическом запасе ниже О-3."""
    return r['raised'] and 'карта поколения неполна' in r['error']


@tinv('замер прежней серии живым не считается',
      needs=lambda r: r['case'] == 'замер прежней серии')
def _t_m3(r):
    """Шестнадцатый круг, №4 (усилено семнадцатым, №8): после смены реестра старый ESZ25
    не покрывает текущие серии — замер обязан покрыть КАЖДУЮ FUT-строку реестра."""
    return r['raised'] and ('не покрывает серии' in r['error'] or 'не совпала' in r['error'])


@tinv('дыра существующего замера не добирается константами',
      needs=lambda r: r['case'] == 'замер не покрывает корень')
def _t_m4(r):
    """Шестнадцатый круг, №4: молчаливый .get(root, КОНСТАНТА) прятал неполноту замера."""
    return r['raised'] and 'не покрывает' in r['error']


@tinv('без файла замера — ОТКАЗ, а не константы',
      needs=lambda r: r['case'] == 'замер отсутствует')
def _t_m5(r):
    """Двадцать третий круг, №5. Прежний стенд ЗАКРЕПЛЯЛ fail-open как ожидаемый успех:
    удаление margins_live.json снимало ворота серий и открывало переход по модельным
    константам. Отказ обязан называть и файл, и калитку — иначе оператор станет искать
    обход вместо того, чтобы запустить first_connect."""
    return (r['raised'] and 'замера маржи нет' in r['error']
            and 'first_connect' in r['error'])


@tinv('переход задним числом запрещён',
      needs=lambda r: r['case'] == 'переход задним числом запрещён')
def _t_asof(r):
    """asof обязан совпадать с биржевым сегодня: от него считаются окно, resume и
    хронология журнала МР. Отказ обязан НАЗЫВАТЬ дату — иначе оператор не поймёт, что
    именно не сошлось (и стенд не отличит эту защиту от любого другого падения)."""
    return (r['raised'] and 'asof' in r['error']
            and '2020-01-02' in r['error'] and 'сегодня' in r['error'])


@tinv('живой замер используется вместо констант',
      needs=lambda r: r['case'] == 'живой замер покрывает')
def _t_m6(r):
    return not r['raised'] and r.get('margin') == 40_000.0 + 2 * 2_500.0


@tinv('потерянное подтверждение оставляет след попытки подачи',
      needs=lambda r: r['case'] == 'подтверждение первой заявки потеряно')
def _t_ack(r):
    """ТРИДЦАТЬ СЕДЬМОЙ КРУГ, №18. Отметка st['attempted'] существовала с тридцать пятого
    круга, но не утверждалась НИГДЕ: мутация, снимающая её, красила прогон только через
    соседний признак moved (позиция уже видна), а сочетание «исполнено, номер потерян,
    позиции ещё старые, отчёта нет» стендов не имело. Проверяем ровно объявленное: попытка
    подачи зафиксирована, номеров нет, и заявка у брокера действительно была."""
    return (r['raised'] and int(r.get('attempted') or 0) >= 1
            and not r.get('order_ids') and len(r.get('calls') or []) == 1)


def run_transition():
    cov, bad = {}, {}
    for case in TR_CASES:
        r = _tr_run(case)
        for name, fn, needs in TR:
            if needs is not None and not needs(r):
                continue
            cov[name] = cov.get(name, 0) + 1
            try:
                ok = fn(r)
            except Exception as ex:
                ok = False; name = f'{name} [исключение: {type(ex).__name__}: {ex}]'
            if not ok:
                bad.setdefault(name, []).append(f"{case}: {r['error'][:80]}")
    return cov, bad


# ---------------------------------------------------------------- календарь и переход
# ВРЕМЕННАЯ ОСЬ РОЛЛА. Перебор состояний одношаговый: он видит книгу с отложенным роллом, но
# не видит, что с ней станет ЧЕРЕЗ выходные и праздник, и не видит смену маршрута посреди
# незавершённого переноса. Обе последовательности стоят на пути к ближайшим роллам.
ROLL = []


def rlinv(name, needs=None):
    def deco(fn):
        ROLL.append((name, fn, needs)); return fn
    return deco


ROLL_CASES = ('отложенный ролл через праздник', 'переход посреди отложенного ролла',
              'штатный ролл не повторяется назавтра', 'пропущенный ролл только нога Б',
              'смешанная книга: просрочена только Б',
              'откат ролла одной ноги Б: исправная А не уезжает',
              # ТРИДЦАТЬ СЕДЬМОЙ КРУГ, №17: точечного случая «в ДЕНЬ календарного ролла
              # серия А уже свежая, а Б ещё нет» не было, и пер-ножный срок (36-й круг, №1)
              # наблюдался только через ту же функцию, которой управляет решение.
              'день ролла при смешанных сериях: едет только Б',
              # СОРОК ПЕРВЫЙ КРУГ, №4: обе ноги УЖЕ в свежей серии — общий признак дня
              # уводил их ещё на квартал вперёд. Зонд назван рецензентом дословно.
              'день ролла при ОДИНАКОВЫХ свежих сериях: не едет никто',
              # СОРОК ВТОРОЙ КРУГ, №1: ПЕРВЫЙ вход в день ролла. У пустой ноги нет серии,
              # которую роллить, но есть серия, в которую входить — и правка 41-го круга
              # заставляла её покупать УХОДЯЩУЮ U26 вместо Z26.
              'первый вход В ДЕНЬ ролла берёт свежую серию',
              'смена упаковки Е-Ф: сделка без экспозиции',
              'отказ §8 не перекладывает упаковку',
              'две серии одной ноги при передаче книги',
              'передача книги хранит пер-ножный ролл',
              'resume перехода из другой сессии',
              'посторонняя позиция при передаче книги')


def _roll_seq(case):
    """Последовательность сессий вокруг ролла. Ноябрь 2026: ролл сдвинут праздником на 24-е,
    26-е — День благодарения, дальше выходные."""
    import state as ST
    hol = DL.holidays_for(2026, 2027)
    out = dict(case=case, series=[], pending=[], raised=False, error='')

    def run_days(book, days):
        ser, pen = [], []
        b = book
        for ds in days:
            d = pd.Timestamp(ds)
            m = DL.Market(date=d, px_eq_prev=600.0, dref_prev=8.0, dref_today=8.0,
                          px_eq_today=600.0, roll_today=DL.is_roll_day(d, hol),
                          st_eq=b.prev_st_eq if b.prev_st_eq is not None else True,
                          st_bd=True, holidays=hol,
                          roll_passed=DL.roll_passed_for(d, hol))
            dec = DL.step(b, m, 10_000_000.0)
            b = dec.book_after
            ser.append(b.ser_a); pen.append(b.roll_pending)
        return b, ser, pen

    try:
        if case == 'отложенный ролл через праздник':
            b0 = DL.Book(d_fix=8.0, n_e=100, n_b=50, unit_is_mes=True, prev_st_eq=True,
                         prev_st_bd=True, ser_a='Z26', ser_b='Z26', es_held=10,
                         roll_pending=True)
            _, out['series'], out['pending'] = run_days(
                b0, ('2026-11-25', '2026-11-27', '2026-11-30', '2026-12-01'))
        elif case == 'переход посреди отложенного ролла':
            b0 = DL.Book(d_fix=8.0, n_e=100, n_b=50, unit_is_mes=True, prev_st_eq=True,
                         prev_st_bd=True, ser_a='Z26', ser_b='Z26', es_held=10,
                         roll_pending=True)
            # Книга перестраивается по позициям брокера — так делает переходный исполнитель.
            back = ST.book_from_broker(DL.Book, DL.physical_book(b0), 'F',
                                       roll_pending=b0.roll_pending)
            out['carried'] = back.roll_pending
            _, out['series'], out['pending'] = run_days(back, ('2026-11-25', '2026-11-27'))
        elif case == 'смешанная книга: просрочена только Б':
            # Нога А уже в свежей серии, Б просрочена. Исправная нога трогаться НЕ должна.
            b0 = DL.Book(d_fix=8.0, n_e=100, n_b=50, unit_is_mes=True, prev_st_eq=True,
                         prev_st_bd=True, ser_a='Z26', ser_b='U26', es_held=10)
            # День навёрстывания отдельно — ради ДЕНЕЖНОЙ сверки (пятнадцатый круг, №2):
            # рецензия верно указала, что стенд смотрел лишь серии, а списание стоимости
            # переноса с ИСПРАВНОЙ ноги А проходило незамеченным.
            d1 = pd.Timestamp('2026-09-10')
            m1 = DL.Market(date=d1, px_eq_prev=600.0, dref_prev=8.0, dref_today=8.0,
                           px_eq_today=600.0, roll_today=DL.is_roll_day(d1, hol),
                           st_eq=True, st_bd=True, holidays=hol,
                           roll_passed=DL.roll_passed_for(d1, hol))
            dec0 = DL.step(b0, m1, 10_000_000.0)
            ba = dec0.book_after
            ue, ub = DL.units(ba, m1)
            out['cap_after'] = dec0.capital_after_costs
            out['money'] = (abs(ba.n_e - b0.n_e) * ue + abs(ba.n_b - b0.n_b) * ub,
                            ba.n_e * ue, ba.n_b * ub)
            bx, ser, pen = run_days(b0, ('2026-09-10', '2026-09-11'))
            out['ser_a_fin'] = bx.ser_a; out['ser_b_fin'] = bx.ser_b
        elif case == 'откат ролла одной ноги Б: исправная А не уезжает':
            # ДЕВЯТНАДЦАТЫЙ КРУГ, №1: признак отложенного ролла — ПЕР-НОЖНЫЙ. Смешанная
            # книга: А уже в Z26, у Б перенос U26->Z26 доказуемо откатился (pending='Б').
            # Повтор обязан дороллить ТОЛЬКО Б; общий признак гнал А в H27 — дальняя серия
            # и одноногая дыра на номинале порядка NLV.
            b0 = DL.Book(d_fix=8.0, n_e=100, n_b=50, unit_is_mes=True, prev_st_eq=True,
                         prev_st_bd=True, ser_a='Z26', ser_b='U26', es_held=10,
                         roll_pending='Б')
            bx, out['series'], out['pending'] = run_days(b0, ('2026-09-10', '2026-09-11'))
            out['ser_a_fin'] = bx.ser_a; out['ser_b_fin'] = bx.ser_b
        elif case == 'первый вход В ДЕНЬ ролла берёт свежую серию':
            # книга ПУСТА (обе ноги None), 26.08.2026 — день квартального ролла. Календарь
            # праздников присутствует, как в бою: именно при нём и гас признак.
            b0 = DL.Book(d_fix=8.0, n_e=0, n_b=0, unit_is_mes=True, es_held=0,
                         ser_a=None, ser_b=None, prev_st_eq=False, prev_st_bd=False)
            bx, out['series'], out['pending'] = run_days(b0, ('2026-08-26',))
            out['ser_a_fin'] = bx.ser_a; out['ser_b_fin'] = bx.ser_b
        elif case == 'день ролла при ОДИНАКОВЫХ свежих сериях: не едет никто':
            # 26.08.2026 — день квартального ролла, но ОБЕ ноги уже в Z26, чей срок в
            # ноябре. Состояние достижимо после Е->Ф сразу в постролловую серию или после
            # досрочного ручного переноса. Ехать не должен НИКТО: иначе полный лишний
            # оборот и квартал вперёд (H27) на ровном месте.
            b0 = DL.Book(d_fix=8.0, n_e=100, n_b=50, unit_is_mes=True, prev_st_eq=True,
                         prev_st_bd=True, ser_a='Z26', ser_b='Z26', es_held=10)
            bx, out['series'], out['pending'] = run_days(b0, ('2026-08-26',))
            out['ser_a_fin'] = bx.ser_a; out['ser_b_fin'] = bx.ser_b
        elif case == 'день ролла при смешанных сериях: едет только Б':
            # 26.08.2026 — день квартального ролла (три сессии до последнего рабочего дня
            # августа), он же ПЕРВЫЙ живой ролл пилота. Книга смешана после пер-ножного
            # отката: А уже в Z26, Б ещё в U26, отложенного признака НЕТ. Срок — свойство
            # серии: у U26 он сегодня, у Z26 — в декабре. Ехать обязана только Б; общий
            # признак дня уводил исправную А в H27 (квартал вперёд, лишний оборот, риск
            # одноногого разрыва).
            b0 = DL.Book(d_fix=8.0, n_e=100, n_b=50, unit_is_mes=True, prev_st_eq=True,
                         prev_st_bd=True, ser_a='Z26', ser_b='U26', es_held=10)
            bx, out['series'], out['pending'] = run_days(b0, ('2026-08-26',))
            out['ser_a_fin'] = bx.ser_a; out['ser_b_fin'] = bx.ser_b
        elif case == 'смена упаковки Е-Ф: сделка без экспозиции':
            # ДЕВЯТНАДЦАТЫЙ КРУГ, №3: книга после перехода Е->Ф вся в MES (29 единиц,
            # es_held=0); канон требует 2 ES + 9 MES. Изменение одной упаковки обязано
            # считаться СДЕЛКОЙ: заявки уходят, экспозиция не меняется. Прежде trade=False,
            # заявок нет — и финальная сверка падала на рассинхронизации каждую сессию.
            b0 = DL.Book(d_fix=8.0, n_e=29, n_b=0, unit_is_mes=True, prev_st_eq=True,
                         prev_st_bd=False, ser_a='U26', ser_b=None, es_held=0)
            cap0 = 29 * (DL.S.ES_MULT / 10) * 600.0        # книга ровно в цели: полоса молчит
            m1 = pd.Timestamp('2026-08-10')
            mkt = DL.Market(date=m1, px_eq_prev=600.0, dref_prev=8.0, dref_today=8.0,
                            px_eq_today=600.0, roll_today=False, st_eq=True, st_bd=False)
            dec = DL.step(b0, mkt, cap0, paper=True)
            out['dec'] = dec
            out['orders'] = DL.book_to_orders(dec, b0)
            out['exp_same'] = (dec.book_after.n_e == b0.n_e and dec.book_after.n_b == b0.n_b)
            out['cap0'] = cap0
            out['cap_pack'] = dec.capital_after_costs
        elif case == 'отказ §8 не перекладывает упаковку':
            # ДВАДЦАТЫЙ КРУГ, №4: книга 100 MES (es_held=0), NLV ниже порога §8 (paper не
            # выставлен) — наращивание запрещено, сокращение разрешено. Цель вдвое меньше,
            # и честная заявка ровно одна: продать 50 MES. Прежде канон упаковки слал
            # ПРОДАЖУ 90 MES и ПОКУПКУ 4 ES — покупка ровно на ветке отказа, а сбой второй
            # заявки оставлял книгу резко недобранной.
            b0 = DL.Book(d_fix=8.0, n_e=100, n_b=0, unit_is_mes=True, prev_st_eq=True,
                         prev_st_bd=False, ser_a='U26', ser_b=None, es_held=0)
            mkt = DL.Market(date=pd.Timestamp('2026-08-12'), px_eq_prev=600.0,
                            dref_prev=8.0, dref_today=8.0, px_eq_today=600.0,
                            roll_today=False, st_eq=True, st_bd=False, roll_passed=False)
            dec = DL.step(b0, mkt, 50 * (DL.S.ES_MULT / 10) * 600.0)
            out['dec'] = dec
            out['orders'] = DL.book_to_orders(dec, b0)
        elif case == 'передача книги хранит пер-ножный ролл':
            # ДВАДЦАТЫЙ КРУГ, №11: bool('Б') давал True = «обе ноги», и после возврата
            # Ф->Е->Ф исправная нога А уходила в дальнюю серию. Проверяется САМ перенос
            # признака (carry_pending), а не его нормализация в book_from_broker.
            import transition as _TRN
            class _PB:
                roll_pending = 'Б'
            out['carry'] = _TRN.carry_pending(_PB())
            out['carry_pusto'] = _TRN.carry_pending(None)
        elif case == 'resume перехода из другой сессии':
            # ДВАДЦАТЬ ПЕРВЫЙ КРУГ (защита двадцатого, №1): продолжение вчерашнего перехода
            # шло бы по вчерашнему капиталу, лимиту §8б и ценам. ПРЕДЕЛ СТЕНДА назван
            # честно: проверяется САМА защита, а не её вызов из _execute_locked — полный
            # путь execute требует журнала МР с одобрением и живёт в selfcheck.
            import transition as _TRN
            out['chuzhaya'] = _TRN.resume_same_session({'asof': '2026-08-06'}, '2026-08-07')
            out['svoya'] = _TRN.resume_same_session({'asof': '2026-08-07'}, '2026-08-07')
        elif case == 'две серии одной ноги при передаче книги':
            # ДВАДЦАТЫЙ КРУГ, №10: {'ZNU26': 1, 'ZNZ26': 100} превращалось в n_b=101
            # ОДНОЙ произвольно выбранной серии — поставочный контракт исчезал из
            # состояния, оставаясь у брокера, и переход мог получить COMPLETE.
            try:
                ST.book_from_broker(DL.Book, {'ZNU26': 1, 'ZNZ26': 100}, 'F')
                out['merge_refused'] = False
            except ValueError as ex:
                out['merge_refused'] = 'несколько серий' in str(ex)
            # законная пара ES+MES ОДНОЙ серии обязана строиться
            try:
                _bk = ST.book_from_broker(DL.Book, {'ESU26': 2, 'MESU26': 6}, 'F')
                out['legit_ok'] = (_bk.n_e == 26 and _bk.ser_a == 'U26')
            except Exception:
                out['legit_ok'] = False
        elif case == 'посторонняя позиция при передаче книги':
            # ДЕВЯТНАДЦАТЫЙ КРУГ, №12: чужая позиция в снимке брокера не выбрасывается
            # молча при построении книги — она осталась бы неуправляемой навсегда.
            try:
                ST.book_from_broker(DL.Book, {'NQZ26': 5, 'ESU26': 2, 'ZNU26': 3}, 'F')
                out['alien_refused'] = False
            except ValueError as ex:
                out['alien_refused'] = 'осторонн' in str(ex)
            try:
                ST.book_from_broker(DL.BookE, {'CSPX': 10.0, 'ESU26': 1}, 'E')
                out['alien_refused_e'] = False
            except ValueError as ex:
                out['alien_refused_e'] = 'осторонн' in str(ex)
        elif case == 'пропущенный ролл только нога Б':
            # Нога А выключена (пустая), в книге ТОЛЬКО ZN старой серии; день ролла (24.11)
            # пропущен целиком. Прежний предикат смотрел лишь на ser_a и не навёрстывал.
            b0 = DL.Book(d_fix=8.0, n_e=0, n_b=50, unit_is_mes=True, prev_st_eq=False,
                         prev_st_bd=True, ser_a=None, ser_b='Z26', es_held=0)
            bx, ser, pen = run_days(b0, ('2026-11-25', '2026-11-27'))
            out['series'] = [bx.ser_b] if False else ser
            out['ser_b'] = bx.ser_b; out['pending'] = pen
        else:
            b0 = DL.Book(d_fix=8.0, n_e=100, n_b=50, unit_is_mes=True, prev_st_eq=True,
                         prev_st_bd=True, ser_a='Z26', ser_b='Z26', es_held=10)
            _, out['series'], out['pending'] = run_days(
                b0, ('2026-11-24', '2026-11-25', '2026-11-27', '2026-11-30'))
    except Exception as ex:
        out['raised'] = True; out['error'] = f'{type(ex).__name__}: {ex}'
    return out


@rlinv('отложенный ролл переносит серию РОВНО ОДИН раз',
       needs=lambda r: r['case'] == 'отложенный ролл через праздник')
def _rl1(r):
    """Через праздник и выходные признак обязан сработать в ближайшую сессию и погаснуть.
    Повтор дал бы уход на квартал вперёд, отсутствие — жизнь до месяца поставки."""
    return (not r['raised'] and r['series'] == ['H27'] * 4
            and r['pending'] == [False] * 4)


@rlinv('незавершённый ролл ПЕРЕЖИВАЕТ смену маршрута',
       needs=lambda r: r['case'] == 'переход посреди отложенного ролла')
def _rl2(r):
    """Позиции у брокера говорят, ЧТО есть, но не что осталось СДЕЛАТЬ. Прежде признак
    терялся, перенос не состоялся бы до следующего квартального ролла, и поймал бы это лишь
    запрет входа в месяц поставки — то есть поздно и уже отказом."""
    return not r['raised'] and r.get('carried') and r['series'] == ['H27', 'H27']


@rlinv('стоимость навёрстывания ложится ТОЛЬКО на роллящуюся ногу',
       needs=lambda r: r['case'] == 'смешанная книга: просрочена только Б')
def _rl6(r):
    """Пятнадцатый круг, №2: общий roll_any списывал перенос и с исправной ноги А —
    фиктивно заниженный капитал сдвигает цель и кап на границе округления."""
    if r['raised'] or r.get('cap_after') is None:
        return False
    turn, exp_a, exp_b = r['money']
    want = 10_000_000.0 - DL.S.COST * turn - DL.S.ROLL_BP * exp_b
    return exp_a > 0 and abs(r['cap_after'] - want) < 1e-6


@rlinv('просрочка одной ноги НЕ роллит исправную',
       needs=lambda r: r['case'] == 'смешанная книга: просрочена только Б')
def _rl5(r):
    """Тринадцатый круг, №1/№9: срок — свойство серии; общий флаг гнал исправную ногу А
    на квартал вперёд (лишний оборот, дальняя серия, второй обязательный ролл)."""
    return (not r['raised'] and r.get('ser_a_fin') == 'Z26'
            and r.get('ser_b_fin') == 'Z26')


@rlinv('первый вход в день ролла открывает СВЕЖУЮ серию, а не уходящую',
       needs=lambda r: r['case'] == 'первый вход В ДЕНЬ ролла берёт свежую серию')
def _rl5d(r):
    """СОРОК ВТОРОЙ КРУГ, №1. leg_roll_due(None, m) честно отдаёт False — роллить нечего, —
    и признак ролла гас, а target_tag по погасшему признаку брал УХОДЯЩУЮ серию: 26.08.2026
    первый вход купил бы U26 вместо Z26, назавтра последовал бы полный лишний ролл, а при
    пропуске сессии позиция осталась бы у поставки. Своей серии нет — решает календарь."""
    return (not r['raised'] and r.get('ser_a_fin') == 'Z26'
            and r.get('ser_b_fin') == 'Z26')


@rlinv('в день ролла НЕ едет нога, чей срок ещё не настал',
       needs=lambda r: r['case'] == 'день ролла при ОДИНАКОВЫХ свежих сериях: не едет никто')
def _rl5c(r):
    """СОРОК ПЕРВЫЙ КРУГ, №4. В 36-м круге пер-ножный срок был сужен до РАСХОДЯЩИХСЯ серий
    (починка примера), и при обеих ногах в уже свежей Z26 августовский общий признак уводил
    их в H27 — квартал вперёд и полный лишний оборот. Срок — свойство серии всегда, когда
    известен календарь."""
    return (not r['raised'] and r.get('ser_a_fin') == 'Z26'
            and r.get('ser_b_fin') == 'Z26')


@rlinv('в день ролла едет только нога, чей срок настал',
       needs=lambda r: r['case'] == 'день ролла при смешанных сериях: едет только Б')
def _rl5b(r):
    """ТРИДЦАТЬ ШЕСТОЙ КРУГ, №1 (точечная пара) И ТРИДЦАТЬ СЕДЬМОЙ, №17. Пер-ножный срок
    проверялся последовательностями про ПРОСРОЧКУ и ОТКАТ, но не самим днём календарного
    ролла при расходящихся сериях — то есть ровно тем случаем, ради которого правка
    делалась. Здесь у А срок в декабре, у Б — сегодня: А обязана остаться в Z26."""
    return (not r['raised'] and r.get('ser_a_fin') == 'Z26'
            and r.get('ser_b_fin') == 'Z26')


@rlinv('пропущенный ролл навёрстывается и книгой из одной ноги Б',
       needs=lambda r: r['case'] == 'пропущенный ролл только нога Б')
def _rl4(r):
    return not r['raised'] and r.get('ser_b') == 'H27'


@rlinv('откат ролла одной ноги повторяет ТОЛЬКО её: исправная нога не уезжает',
       needs=lambda r: r['case'] == 'откат ролла одной ноги Б: исправная А не уезжает')
def _rl7(r):
    """Девятнадцатый круг, №1: pending='Б' обязан дороллить Б (U26->Z26) и погаснуть;
    нога А остаётся в своей Z26. Общий признак давал ser_a='H27' — лишний полный ролл."""
    return (not r['raised'] and r.get('ser_a_fin') == 'Z26' and r.get('ser_b_fin') == 'Z26'
            and r['pending'] and r['pending'][-1] is False)


@rlinv('смена упаковки без изменения экспозиции — сделка с заявками',
       needs=lambda r: r['case'] == 'смена упаковки Е-Ф: сделка без экспозиции')
def _rl8(r):
    """Девятнадцатый круг, №3: 29 MES -> 2 ES + 9 MES. trade=True, pack_change=True,
    заявки ±0 по сетке; количества ног не меняются."""
    d = r.get('dec')
    if r['raised'] or d is None:
        return False
    sent = {i: q for i, q in (r.get('orders') or [])}
    grid = sum(q * (10 if i.startswith('ES') else 1) for i, q in sent.items())
    # ДВАДЦАТЫЙ КРУГ, №3: перекладка — сделка, и её ИЗДЕРЖКИ обязаны быть списаны.
    # 29 MES -> 2 ES + 9 MES это продажа 20 MES и покупка 2 ES: оборот 40 единиц сетки,
    # 5 б.п. от номинала. Прежде capital_after_costs не менялся вовсе, и кап с плечом
    # считались так, будто встречные заявки бесплатны.
    _u = (DL.S.ES_MULT / 10) * 600.0
    _want = r['cap0'] - DL.S.COST * 40 * _u
    return (d.trade and d.pack_change and bool(sent) and grid == 0
            and r.get('exp_same') and d.book_after.es_held == 2
            and abs(r['cap_pack'] - _want) < 1e-9)


@rlinv('передача книги сохраняет пер-ножный признак ролла',
       needs=lambda r: r['case'] == 'передача книги хранит пер-ножный ролл')
def _rl_carry(r):
    """ДВАДЦАТЫЙ КРУГ, №11: 'Б' обязано остаться 'Б', а не стать True («обе ноги»)."""
    return (not r['raised'] and r.get('carry') == 'Б' and r.get('carry_pusto') is False)


@rlinv('resume перехода принимается только в своей сессии',
       needs=lambda r: r['case'] == 'resume перехода из другой сессии')
def _rl_resume(r):
    """ДВАДЦАТЫЙ КРУГ, №1: чужая дата — отказ, своя — проход."""
    return (not r['raised'] and r.get('chuzhaya') is False and r.get('svoya') is True)


@rlinv('две серии одной ноги не складываются в выдуманную',
       needs=lambda r: r['case'] == 'две серии одной ноги при передаче книги')
def _rl_series(r):
    """ДВАДЦАТЫЙ КРУГ, №10. Проверяется И отказ на смеси серий, И то, что законная пара
    ES+MES одной серии по-прежнему строится: защита, запрещающая исправное, не защита."""
    return (not r['raised'] and r.get('merge_refused') is True
            and r.get('legit_ok') is True)


@rlinv('отказ §8 не трогает упаковку: только продажа, без встречной покупки',
       needs=lambda r: r['case'] == 'отказ §8 не перекладывает упаковку')
def _rl_pack_ref(r):
    """ДВАДЦАТЫЙ КРУГ, №4. Обещание «при отказе упаковка не трогается» стояло в
    комментарии, а pack_es вызывался безусловно. Проверяется, что отказ ЕСТЬ, что заявок
    на ПОКУПКУ нет ни одной и что es_held не изменился: иначе на ветке, где наращивание
    запрещено, уходила бы покупка ES, а отказ второй заявки оставлял книгу недобранной."""
    d = r.get('dec')
    if r['raised'] or d is None:
        return False
    sent = dict(r.get('orders') or [])
    return (bool(d.refusals) and sent == {'MESU26': -50}
            and d.book_after.es_held == 0)


@rlinv('посторонняя позиция не глотается при построении книги из позиций брокера',
       needs=lambda r: r['case'] == 'посторонняя позиция при передаче книги')
def _rl9(r):
    """Девятнадцатый круг, №12: book_from_broker обязан отказать на чужом инструменте на
    ОБОИХ маршрутах, а не молча строить книгу без него."""
    return (not r['raised'] and r.get('alien_refused') is True
            and r.get('alien_refused_e') is True)


@rlinv('штатный ролл не повторяется в следующие сессии',
       needs=lambda r: r['case'] == 'штатный ролл не повторяется назавтра')
def _rl3(r):
    """В день ролла серия меняется один раз; назавтра и позже она обязана стоять — иначе
    книга откатывается в покинутый контракт или уезжает дальше положенного."""
    return not r['raised'] and r['series'] == ['H27'] * 4


def run_roll():
    cov, bad = {}, {}
    for case in ROLL_CASES:
        r = _roll_seq(case)
        for name, fn, needs in ROLL:
            if needs is not None and not needs(r):
                continue
            cov[name] = cov.get(name, 0) + 1
            try:
                ok = fn(r)
            except Exception as ex:
                ok = False; name = f'{name} [исключение: {type(ex).__name__}]'
            if not ok:
                bad.setdefault(name, []).append(
                    f"{case}: серии {r['series']} pending {r['pending']} {r['error'][:60]}")
    return cov, bad


# ---------------------------------------------------------------- отказ §8 с ростом
# ШЕСТНАДЦАТЫЙ КРУГ, №1/№6: системный перебор держит prev_st_* = True и не видит ВКЛЮЧЕНИЯ
# ноги под отказом. Точечный сценарий рецензента: счёт ниже порога, Б ровно на 1x, сигнал
# включает А; прежний ПОЗДНИЙ фильтр оставлял кап-срез исправной Б (продажа без нормативной
# причины) и капитал с комиссиями неподанных заявок.
REF8 = []


def r8inv(name, needs=None):
    def deco(fn):
        REF8.append((name, fn, needs)); return fn
    return deco


REF8_CASES = ('включение А запрещено порогом §8',)


def _ref8_run(case):
    out = dict(case=case, raised=False, error='', dec=None)
    try:
        b = DL.Book(d_fix=8.0, n_e=0, n_b=10, unit_is_mes=True, prev_st_eq=False,
                    prev_st_bd=True, ser_a=None, ser_b='U26', es_held=0)
        m = DL.Market(date=pd.Timestamp('2026-08-12'), px_eq_prev=600.0, dref_prev=8.0,
                      dref_today=8.0, px_eq_today=600.0, roll_today=False, st_eq=True,
                      st_bd=True, roll_passed=False)
        out['dec'] = DL.step(b, m, 985_600.0)
    except Exception as ex:
        out['raised'] = True; out['error'] = f'{type(ex).__name__}: {ex}'
    return out


@r8inv('запрет роста действует с первого шага: исправная нога не режется',
       needs=lambda r: r['case'] == 'включение А запрещено порогом §8')
def _f81(r):
    """Кап-срез, вызванный ЗАПРЕЩЁННОЙ ногой, не смеет пережить запрет: без включения А
    книга 10 ZN на капе 2,00 законна и не трогается."""
    d = r['dec']
    return (not r['raised'] and d is not None and d.refusals
            and d.orders == {} and d.book_after.n_e == 0 and d.book_after.n_b == 10
            and not d.cap_correction)


@r8inv('капитал при отказе не несёт комиссий неподанных заявок',
       needs=lambda r: r['case'] == 'включение А запрещено порогом §8')
def _f82(r):
    """№6: расходы решения обязаны соответствовать ЗАЯВКАМ, ушедшим брокеру; здесь их нет —
    капитал равен входному до последнего цента."""
    d = r['dec']
    return (not r['raised'] and d is not None
            and abs(d.capital_after_costs - 985_600.0) < 1e-9)


def run_refusal():
    cov, bad = {}, {}
    for case in REF8_CASES:
        r = _ref8_run(case)
        for name, fn, needs in REF8:
            if needs is not None and not needs(r):
                continue
            cov[name] = cov.get(name, 0) + 1
            try:
                ok = fn(r)
            except Exception as ex:
                ok = False; name = f'{name} [исключение: {type(ex).__name__}]'
            if not ok:
                d = r['dec']
                bad.setdefault(name, []).append(
                    f"{case}: заявки {getattr(d, 'orders', None)} книга "
                    f"{getattr(getattr(d, 'book_after', None), 'n_b', None)} "
                    f"капитал {getattr(d, 'capital_after_costs', None)} {r['error'][:60]}")
    return cov, bad


# ------------------------------------------------------- избыток упаковки и ворота капа
# ДВАДЦАТЬ ДЕВЯТЫЙ КРУГ, №6. Перебор состояний сюда не достаёт: нарушенная упаковка
# (вся сетка в MES при es_held=0) появляется только после передачи книги из маршрута Е, а
# ворота капа по пессимистичному капиталу связывают лишь у самой границы 2,00. Точечные
# сценарии — иначе обе защиты сняли бы бесследно (мутационный прогон это и показал).
PACK = []


def pkinv(name, needs=None):
    def deco(fn):
        PACK.append((name, fn, needs)); return fn
    return deco


PACK_CASES = ('ролл без смены упаковки', 'нарушенная упаковка у границы капа')


def _pack_run(case):
    out = dict(case=case, raised=False, error='', dec=None, exc=None)
    try:
        if case == 'ролл без смены упаковки':
            b = DL.Book(n_e=333, n_b=10, unit_is_mes=True, d_fix=7.9, ser_a='U26',
                        ser_b='U26', es_held=33)
            after = DL.replace(b, ser_a='Z26', ser_b='Z26',
                               es_held=DL.pack_es(b.es_held, 333, True, True))
            out['exc'] = DL.repack_excess(b, after, True)
        else:
            # книга после Е->Ф: 260 единиц сетки лежат MES-ами, остаток 260 против
            # допустимых 0..19 — перекладка вынужденная, её избыток известен ДО заявок.
            b = DL.Book(n_e=260, n_b=0, unit_is_mes=True, d_fix=7.9, prev_close_lev=1.99,
                        prev_st_eq=True, prev_st_bd=True, ser_a='U26', ser_b='U26',
                        es_held=0, last_session='2026-08-11', close_provisional=False)
            m = DL.Market(date=pd.Timestamp('2026-08-12'), px_eq_prev=776.0, dref_prev=7.9,
                          dref_today=7.9, px_eq_today=776.0, roll_today=False,
                          st_eq=True, st_bd=True)
            out['dec'] = DL.step(b, m, 5_022_000.0)
            out['exc'] = DL.repack_excess(b, out['dec'].book_after, True)
    except Exception as ex:
        out['raised'] = True; out['error'] = f'{type(ex).__name__}: {ex}'
    return out


@pkinv('оборот ролла не выдаётся за избыток перекладки',
       needs=lambda r: r['case'] == 'ролл без смены упаковки')
def _pk1(r):
    """Замер брал разность физических книг ВМЕСТЕ со сменой серии: закрытие старой серии
    плюс открытие новой давали 666 единиц сетки при чистом нуле, и каждый роллный день
    оператор получал тревогу о недостаче денег — ролл оплачен нормативным 1 б.п."""
    return (not r['raised']) and r['exc'] is not None and r['exc'][0] == 0


@pkinv('кап срезает книгу по капиталу за вычетом несписанного избытка',
       needs=lambda r: r['case'] == 'нарушенная упаковка у границы капа')
def _pk2(r):
    """Запись причины денег не возвращает и кап не соблюдает: издержки известны ДО заявок,
    а порог 2,00 проверялся по капиталу, из которого они не вычтены."""
    d = r['dec']
    return (not r['raised'] and d is not None and r['exc'][0] > 0
            and d.cap_correction
            and any('ЗА ВЫЧЕТОМ' in x for x in d.reasons))


@pkinv('комиссия среза считается по обороту, а не по числу срезанных единиц',
       needs=lambda r: r['case'] == 'нарушенная упаковка у границы капа')
def _pk31(r):
    """ТРИДЦАТЬ ПЕРВЫЙ КРУГ, №4. Ворота капа списывали ЕЩЁ ОДНУ комиссию за каждую
    срезанную единицу. Но срез меняет не число заявок, а РАЗМЕР заявки: здесь цель ноги Б
    выросла с 0 до 51, и срез до 50 означает купить на единицу МЕНЬШЕ — комиссия обязана
    вернуться. Заниженный капитал делает ворота строже нормы: существует пограничный
    интервал, где книга режется на контракт лишний раз, а следом идёт обратный оборот.

    Стенд считает издержки ОБОРОТА сам, из выставленных наружу чисел ворот, и сравнивает с
    записанным capital_after_costs. На старом знаке расхождение ровно 2 x COST x u —
    величина, которую прежний точечный стенд PACK не смотрел вовсе (он проверял только
    факт среза и наличие пометки «ЗА ВЫЧЕТОМ»).
    """
    d = r['dec']
    if r['raised'] or d is None or getattr(d, 'cap_gate_e0', None) is None:
        return False
    n0e, n0b = d.cap_gate_n0
    pe0, pb0 = d.cap_gate_plan
    u_e, u_b = d.cap_gate_units
    ne, nb = d.book_after.n_e, d.book_after.n_b
    want = d.cap_gate_e0 - S.COST * ((abs(ne - n0e) - abs(pe0 - n0e)) * u_e
                                     + (abs(nb - n0b) - abs(pb0 - n0b)) * u_b)
    # срез растущей цели обязан ВЕРНУТЬ деньги, а не списать их второй раз
    return (abs(d.capital_after_costs - want) < 1e-9
            and (nb < pb0 and pb0 > n0b) and d.capital_after_costs > d.cap_gate_e0)


@pkinv('избыток на смешанном пути посчитан и объявлен',
       needs=lambda r: r['case'] == 'нарушенная упаковка у границы капа')
def _pk3(r):
    d = r['dec']
    return (not r['raised'] and d is not None
            and any('ИЗБЫТОК ОБОРОТА УПАКОВКИ' in x for x in d.reasons))


def run_pack():
    cov, bad = {}, {}
    for case in PACK_CASES:
        r = _pack_run(case)
        for name, fn, needs in PACK:
            if needs is not None and not needs(r):
                continue
            cov[name] = cov.get(name, 0) + 1
            try:
                ok = fn(r)
            except Exception as ex:
                ok = False; name = f'{name} [исключение: {type(ex).__name__}]'
            if not ok:
                bad.setdefault(name, []).append(f"{case}: избыток {r['exc']} {r['error'][:60]}")
    return cov, bad


# ---------------------------------------------------------------- обновлятор сигналов
# Девятая рецензия, №28: signal_update не исполнялся ни одним стендом. Здесь проверяется
# МЕХАНИКА (замок, атомарная публикация, сверка перекрытия, отказ на потерянном хвосте
# месяца, дописывание ровно одного месяца); истинность математики держит живой контур —
# сверкой перекрытия с фактической историей при каждом запуске.
SIG = []


def sginv(name, needs=None):
    def deco(fn):
        SIG.append((name, fn, needs)); return fn
    return deco


SIG_CASES = ('дописывается новый месяц', 'источник разошёлся с историей',
             'хвост месяца потерян', 'хвост ПРОМЕЖУТОЧНОГО месяца потерян',
             'новых месяцев нет', 'уровни пересчитаны поставщиком',
             'сайдкар уровней отсутствует', 'сайдкар без столбца ноги',
             'сайдкар без общих месяцев', 'свежее закрытие завышено поставщиком',
             'свежее закрытие занижено за купонным потолком',
             'дивидендное окно в допуске', 'свежий знак в дивидендной зоне',
             'порча промежуточного месяца после пропуска',
             'уровневая база из одного месяца',
             'общая порча сделочного среза — ловят котировки')


def _sig_run(case):
    import os
    import tempfile
    import ib_stub
    import feed as FD
    import signal_update as SU
    import pandas as pd

    ib = ib_stub.StubIB(ib_stub.FIXTURE_ROWS)
    months = pd.date_range('2024-02-01', '2026-07-31', freq='BME')
    spy_id, ief_id = ib_stub.StubIB.AUX['SPY'], ib_stub.StubIB.AUX['IEF']
    bars = {spy_id: [], ief_id: []}
    for i, d in enumerate(months):
        bars[spy_id].append((f'{d:%Y-%m-%d}', 500.0 * (1.01 ** i)))          # ровный рост
        bars[ief_id].append((f'{d:%Y-%m-%d}', 90.0 + (3.0 if i % 7 < 4 else -3.0)))
    if case == 'хвост месяца потерян':
        # последний бар июля отодвинут с последней сессии (31.07) на середину месяца
        bars[spy_id][-1] = ('2026-07-15', bars[spy_id][-1][1])
    if case == 'хвост ПРОМЕЖУТОЧНОГО месяца потерян':
        # ДВАДЦАТЫЙ КРУГ, №9: дыра не в последнем, а в ПРЕДпоследнем дописываемом месяце.
        # Прежняя проверка смотрела только me.index[-1], и такой месяц входил в SMA
        # нетронутым; TRADES и MIDPOINT его не ловят — тот же поставщик, та же дыра.
        _d, _px = bars[spy_id][-2]
        bars[spy_id][-2] = (f'{_d[:8]}15', _px)
    # СВЕЖИЙ МЕСЯЦ ПОД НЕЗАВИСИМЫМ СРЕЗОМ TRADES (шестнадцатый круг, №3): скорректированный
    # ряд портится ТОЛЬКО в последней точке — все прежние сверки (сайдкар, перекрытие) её
    # не видят по построению.
    if case == 'свежее закрытие завышено поставщиком':
        raw = list(bars[spy_id])
        d_last, px_last = bars[spy_id][-1]
        bars[spy_id][-1] = (d_last, px_last * 1.01)
        ib.set_bars({spy_id: raw, ief_id: list(bars[ief_id])}, what='TRADES')
    elif case == 'свежее закрытие занижено за купонным потолком':
        raw = list(bars[spy_id])
        d_last, px_last = bars[spy_id][-1]
        bars[spy_id][-1] = (d_last, px_last * 0.97)
        ib.set_bars({spy_id: raw, ief_id: list(bars[ief_id])}, what='TRADES')
    elif case == 'общая порча сделочного среза — ловят котировки':
        # ДЕВЯТНАДЦАТЫЙ КРУГ, №5: ADJUSTED_LAST и TRADES — производные ОДНОГО дневного
        # бара; общая порча закрытия проходила их сверку с отношением около нуля.
        # Здесь испорчены ОБА сделочных среза, честен только котировочный MIDPOINT.
        raw = list(bars[spy_id])
        d_last, px_last = bars[spy_id][-1]
        bars[spy_id][-1] = (d_last, px_last * 1.02)
        ib.set_bars({spy_id: list(bars[spy_id]), ief_id: list(bars[ief_id])}, what='TRADES')
        ib.set_bars({spy_id: raw, ief_id: list(bars[ief_id])}, what='MIDPOINT')
    elif case == 'дивидендное окно в допуске':
        # сырое закрытие выше скорректированного на обычный купон 0,3% — штатное дивидендное
        # окно, обновление обязано ПРОЙТИ
        tr = list(bars[spy_id])
        d_last, px_last = tr[-1]
        tr[-1] = (d_last, px_last * 1.003)
        ib.set_bars({spy_id: tr, ief_id: list(bars[ief_id])}, what='TRADES')
    elif case == 'свежий знак в дивидендной зоне':
        # последнее закрытие ставится на +1% к SMA-12: внутри дивидендной зоны (~1,8%), но
        # вне прежнего допуска 0,1% — искажение размером с купон могло бы сменить знак
        s11 = sum(px for _, px in bars[spy_id][-12:-1])
        d_last, _ = bars[spy_id][-1]
        x = (1.01 * s11 / 12.0) / (1.0 - 1.01 / 12.0)
        bars[spy_id][-1] = (d_last, x)
    ib.set_bars(bars)

    tmp = tempfile.mkdtemp(prefix='addfut-sig-')
    live = Path(tmp) / 'sig.csv'
    keep_env = os.environ.get('ADDFUT_SIGNALS')
    keep_today = FD.exchange_today
    out = dict(case=case, raised=False, error='', added=None, rows_after=None)
    try:
        os.environ['ADDFUT_SIGNALS'] = str(live)
        if case != 'сайдкар уровней отсутствует':
            os.environ['ADDFUT_LEVELS_BOOTSTRAP'] = '1'
        FD.exchange_today = lambda: pd.Timestamp('2026-08-13')
        eq = SU.states(SU.monthly_adjusted(ib, 'SPY', 'ARCA'))
        bd = SU.states(SU.monthly_adjusted(ib, 'IEF', 'NASDAQ'))
        both = sorted(set(eq.index) & set(bd.index))
        seed = both if case == 'новых месяцев нет' else both[:-1]
        with open(live, 'w', encoding='utf-8') as f:
            f.write(',leg_eq,leg_bond\n')
            for d in seed:
                e, b = int(eq.loc[d]), int(bd.loc[d])
                if case == 'источник разошёлся с историей' and d == seed[-3]:
                    b = 1 - b                     # один бит истории подделан
                f.write(f'{d:%Y-%m-%d},{e},{b}\n')
        if case == 'уровни пересчитаны поставщиком':
            # Первый прогон пишет сайдкар уровней; затем поставщик «пересчитал историю»
            # НЕОДНОРОДНО (одному месяцу — свой множитель): биты SMA те же, уровни — нет.
            SU.update(ib)
            for k in bars:
                bars[k] = [(d, px * (1.03 if d.startswith('2026-03') else 1.0))
                           for d, px in bars[k]]
            ib.set_bars(bars)
        if case in ('сайдкар без столбца ноги', 'сайдкар без общих месяцев'):
            # ЧАСТИЧНЫЙ сайдкар (пятнадцатый круг, №5): публикация полного файла первым
            # прогоном, затем порча. Прежний код молча возвращался к слабым 12 битам.
            SU.update(ib)
            os.environ.pop('ADDFUT_LEVELS_BOOTSTRAP', None)
            lp = live.with_name('signals_levels.csv')
            dfl = pd.read_csv(lp, parse_dates=[0], index_col=0)
            if case == 'сайдкар без столбца ноги':
                dfl.drop(columns=['IEF']).to_csv(lp)
            else:
                dfl.index = dfl.index + pd.DateOffset(years=30)
                dfl.to_csv(lp)
        if case == 'порча промежуточного месяца после пропуска':
            # ПРОПУСК НЕСКОЛЬКИХ МЕСЯЦЕВ (семнадцатый круг, №12): сайдкар и живой ряд
            # отстали на три месяца; поставщик испортил ПРОМЕЖУТОЧНЫЙ месяц только в
            # скорректированном срезе — старые уровни целы, последний месяц цел, но SMA
            # свежего решения строится по непроверенной точке.
            SU.update(ib)
            os.environ.pop('ADDFUT_LEVELS_BOOTSTRAP', None)
            lp = live.with_name('signals_levels.csv')
            pd.read_csv(lp, parse_dates=[0], index_col=0).iloc[:-3].to_csv(lp)
            pd.read_csv(live, parse_dates=[0], index_col=0).iloc[:-3].to_csv(live)
            raw_spy = list(bars[spy_id])
            d_mid, px_mid = bars[spy_id][-2]
            bars[spy_id][-2] = (d_mid, px_mid * 1.02)
            ib.set_bars({spy_id: raw_spy, ief_id: list(bars[ief_id])}, what='TRADES')
            ib.set_bars(bars)
        if case == 'уровневая база из одного месяца':
            # Сайдкар из ОДНОЙ строки прежде «проходил» как полноценная база уровней.
            SU.update(ib)
            os.environ.pop('ADDFUT_LEVELS_BOOTSTRAP', None)
            lp = live.with_name('signals_levels.csv')
            pd.read_csv(lp, parse_dates=[0], index_col=0).iloc[-1:].to_csv(lp)
        out['added'] = SU.update(ib)
        out['rows_after'] = sum(1 for _ in open(live, encoding='utf-8')) - 1
    except Exception as ex:
        out['raised'] = True
        out['error'] = f'{type(ex).__name__}: {ex}'
    finally:
        os.environ.pop('ADDFUT_SIGNALS', None)
        os.environ.pop('ADDFUT_LEVELS_BOOTSTRAP', None)
        if keep_env is not None:
            os.environ['ADDFUT_SIGNALS'] = keep_env
        FD.exchange_today = keep_today
    return out


@sginv('новый месяц дописывается ровно один раз',
       needs=lambda r: r['case'] == 'дописывается новый месяц')
def _sg1(r):
    return (not r['raised'] and r['added'] and len(r['added']) == 1
            and r['added'][0][0] == '2026-08-31')


@sginv('уехавший источник отвергается сверкой перекрытия',
       needs=lambda r: r['case'] == 'источник разошёлся с историей')
def _sg2(r):
    """Молча уехавший источник менял бы стратегию без единой ошибки — это главный денежный
    риск сигнального слоя."""
    return r['raised'] and 'РАЗОШЁЛСЯ' in r['error']


@sginv('потерянный хвост ПРОМЕЖУТОЧНОГО месяца отвергается',
       needs=lambda r: r['case'] == 'хвост ПРОМЕЖУТОЧНОГО месяца потерян')
def _g_tail_mid(r):
    """ДВАДЦАТЫЙ КРУГ, №9: проверялся только последний месяц решения, а дописываться может
    до двенадцати сразу. Дыра в промежуточном входила в SMA целой — цена ошибки —
    переключение целой ноги на величину порядка NLV."""
    return r['raised'] and 'хвост месяца' in r['error']


@sginv('потерянный хвост месяца отвергается',
       needs=lambda r: r['case'] == 'хвост месяца потерян')
def _sg3(r):
    return r['raised'] and 'хвост месяца' in r['error']


@sginv('неоднородный пересчёт истории поставщиком отвергается по УРОВНЯМ',
       needs=lambda r: r['case'] == 'уровни пересчитаны поставщиком')
def _sg5(r):
    """Биты «закрытие>SMA» совпадают, а уровни разошлись сверх общего множителя — ровно
    случай, который сверка битов пропускает (десятый круг, №9)."""
    return r['raised'] and 'УРОВНИ' in r['error']


@sginv('без сайдкара уровней обновление отвергается',
       needs=lambda r: r['case'] == 'сайдкар уровней отсутствует')
def _sg6(r):
    """Двенадцатый круг, №6: без уровней сверка сводится к 12 битам и закрепляет уехавший
    источник как норму; первый запуск — только явным разрешением оператора."""
    return r['raised'] and 'сайдкара уровней' in r['error']


@sginv('сайдкар без столбца ноги отвергается',
       needs=lambda r: r['case'] == 'сайдкар без столбца ноги')
def _sg7(r):
    """Пятнадцатый круг, №5: файл только с SPY молча переводил IEF на слабые 12 битов —
    неоднородный пересчёт истории IEF мог сменить свежий знак и десятки ZN."""
    return r['raised'] and 'столбца' in r['error']


@sginv('сайдкар без общих месяцев отвергается',
       needs=lambda r: r['case'] == 'сайдкар без общих месяцев')
def _sg8(r):
    """Пятнадцатый круг, №5: непересекающийся сайдкар по силе равен отсутствующему —
    база сравнения пуста, а проверка молча «проходила»."""
    return r['raised'] and 'общих месяцев' in r['error']


@sginv('завышенное свежее закрытие отвергается TRADES-срезом',
       needs=lambda r: r['case'] == 'свежее закрытие завышено поставщиком')
def _sg9(r):
    """Шестнадцатый круг, №3: новейший месяц не покрыт сайдкаром по построению —
    скорректированное закрытие выше сырого возможно только порчей источника."""
    return r['raised'] and 'разошёлся с независимым' in r['error']


@sginv('заниженное за купонным потолком закрытие отвергается',
       needs=lambda r: r['case'] == 'свежее закрытие занижено за купонным потолком')
def _sg10(r):
    """Отставание от сырого сверх дивидендной границы окна не объясняется купоном."""
    return r['raised'] and 'разошёлся с независимым' in r['error']


@sginv('штатное дивидендное окно проходит сверку',
       needs=lambda r: r['case'] == 'дивидендное окно в допуске')
def _sg11(r):
    """Обычный купон 0,3% в окне не должен блокировать обновление — иначе защита
    останавливала бы каждый месяц IEF и её пришлось бы снять."""
    return (not r['raised'] and r['added'] and len(r['added']) == 1
            and r['added'][0][0] == '2026-08-31')


@sginv('свежий знак внутри дивидендной зоны требует подтверждения',
       needs=lambda r: r['case'] == 'свежий знак в дивидендной зоне')
def _sg12(r):
    """Занижение в пределах купона неотличимо от купона TRADES-срезом; если такой сдвиг
    способен сменить знак — знак стоит в зоне, и автодописывание запрещено."""
    return r['raised'] and 'пограничное' in r['error']


@sginv('непроверенный промежуточный месяц отвергается TRADES-срезом',
       needs=lambda r: r['case'] == 'порча промежуточного месяца после пропуска')
def _sg13(r):
    """Семнадцатый круг, №12: сверка одного лишь последнего месяца пропускала порчу
    внутри пропущенного окна — SMA и свежий знак менялись без единой ошибки."""
    return r['raised'] and 'разошёлся с независимым' in r['error']


@sginv('уровневая база из одного месяца отвергается',
       needs=lambda r: r['case'] == 'уровневая база из одного месяца')
def _sg14(r):
    """Семнадцатый круг, №12: один общий месяц — не база; порог как у сверки битов."""
    return r['raised'] and 'коротка' in r['error']


@sginv('общая порча сделочного среза отвергается котировочным',
       needs=lambda r: r['case'] == 'общая порча сделочного среза — ловят котировки')
def _sg15(r):
    """Девятнадцатый круг, №5: ADJUSTED_LAST и TRADES делят один бар сделок — их сверка
    на общей ошибке молчит; MIDPOINT (поток котировок) — другой ряд данных, и порча,
    «уверенно» перебросившая закрытие через SMA, обязана быть отвергнута им."""
    return r['raised'] and 'котировоч' in r['error']


@sginv('без новых месяцев файл не меняется',
       needs=lambda r: r['case'] == 'новых месяцев нет')
def _sg4(r):
    return not r['raised'] and r['added'] == []


def run_signal():
    cov, bad = {}, {}
    for case in SIG_CASES:
        r = _sig_run(case)
        for name, fn, needs in SIG:
            if needs is not None and not needs(r):
                continue
            cov[name] = cov.get(name, 0) + 1
            try:
                ok = fn(r)
            except Exception as ex:
                ok = False; name = f'{name} [исключение: {type(ex).__name__}]'
            if not ok:
                bad.setdefault(name, []).append(f"{case}: {r['error'][:90]}")
    return cov, bad


# ---------------------------------------------------------------- сверка §7
# ДЕВЯТНАДЦАТЫЙ КРУГ, №16 (+ долг пар восемнадцатого, №15/№16): сверка §7 объявлена
# единственным основанием пересмотра денежных параметров, а её собственные защиты — полнота
# против ИТОГ, конечность чисел, односторонний ролловый номинал — не имели ни одного стенда.
J7 = []


def jinv(name, needs=None):
    def deco(fn):
        J7.append((name, fn, needs)); return fn
    return deco


J7_CASES = ('пометка исключения', 'счётчик итога расходится', 'пустая цена в живой строке',
            'нечисловое наблюдение', 'ролловый номинал односторонний',
            'комиссия не пришла', 'нет строки ИТОГ', 'ролл без своих двадцати сессий',
            # ТРИДЦАТЬ ТРЕТИЙ КРУГ, №11: ролловая доля меньше одного ES округлялась в ноль.
            'ролл меньше одного ES')


def _j7_row(date, inst, qty, po, pf, note='', leg='Б', commission='0'):
    # КОМИССИЯ '0' И КОМИССИЯ '' — РАЗНОЕ (двадцатый круг, №18): ноль есть утверждение
    # «сбора не было», пустое поле — «commissionReport не пришёл, расход неизвестен».
    # Прежде фикстуры писали пустое, а сверка молча считала его нулём.
    return dict(date=date, leg=leg, instrument=inst, qty=qty, px_order=po, px_fill=pf,
                commission=commission, reason='', nav='1000000.00', leverage='1.0',
                roll_spread_near='', roll_spread_far='', note=note)


def _j7_run(case):
    import tempfile
    import journal as J
    out = dict(case=case, raised=False, error='', res=None)
    jp = Path(tempfile.mkdtemp(prefix='addfut-j7-')) / 'j.csv'
    try:
        if case == 'ролловый номинал односторонний':
            # 20 сессий, на каждой перенос 1 ZN: закрытие теряет 0,05 (50 $ на 1000-м
            # множителе), открытие столько же. Односторонний номинал ~99 950 $ на сессию
            # даёт ~10 б.п.; двусторонний (старый) показал бы вдвое меньше.
            for i in range(20):
                d = f'2026-07-{i + 1:02d}'
                J.append(jp, _j7_row(d, 'ZNU26', -1, '100', '99.95', note='ролл'))
                J.append(jp, _j7_row(d, 'ZNZ26', 1, '100', '100.05', note='ролл'))
                J.append(jp, _j7_row(d, 'ИТОГ', 0, '-', '', note=f'итог сессии {i + 1}: строк 2'))
            out['res'] = J.reconcile(jp)
        elif case == 'ролл меньше одного ES':
            # ТРИДЦАТЬ ТРЕТИЙ КРУГ, №11. Старая упаковка — 1 ES (10 единиц сетки), цель
            # после ролла — 5 MES: переносится 5 единиц, то есть МЕНЬШЕ одного ES.
            # `int(take // 10)` давал ноль, вся продажа ES уходила в обычные сделки, и
            # _roll_block подставлял вместо недостающей закрывающей стороны половину
            # номинала открывающей — результат MES-стороны удваивался.
            import journal as _Jr
            from types import SimpleNamespace as _SN
            import pandas as _pdr
            # ВНУТРЕННИЕ ЕДИНИЦЫ СЕТКИ, КАК В НАСТОЯЩЕМ Decision (тридцать четвёртый круг,
            # №11): roll_pairs несёт единицы сетки (1 ES = 10), а не контракты.
            _dec = _SN(date=_pdr.Timestamp('2026-08-26'), leverage=1.5,
                       reasons=['ролл'], refusals=[],
                       roll_pairs=[{'leg': 'А', 'close': ('U26', -10), 'open': ('Z26', 5)}])
            _rows = _Jr.rows_from_decision(
                _dec, 1e6, [('ESU26', -1), ('MESZ26', 5)],
                {'ESU26': dict(px_fill=7000.0, commission='2'),
                 'MESZ26': dict(px_fill=700.0, commission='1')})
            out['rows'] = _rows
            # ВТОРОЙ СЛУЧАЙ: ОСТАТОК СВЕРХ ЦЕЛЫХ ЛОТОВ (тридцать пятый круг, №9 и №10).
            # Упаковка 2 ES, ролловый пул 15 единиц: ролловая доля — 1,5 ES, обычная — 0,5.
            # Прежний стенд покрывал только «0,5 из 1 ES», и производственный дефект
            # оставался невидимым именно там, где он и живёт.
            _dec2 = _SN(date=_pdr.Timestamp('2026-08-26'), leverage=1.5,
                        reasons=['ролл'], refusals=[],
                        roll_pairs=[{'leg': 'А', 'close': ('U26', -20), 'open': ('Z26', 15)}])
            out['rows2'] = _Jr.rows_from_decision(
                _dec2, 1e6, [('ESU26', -2), ('MESZ26', 15)],
                {'ESU26': dict(px_fill=7000.0, commission='4'),
                 'MESZ26': dict(px_fill=700.0, commission='3')})
            out['res'] = None
        elif case == 'пометка исключения':
            J.append(jp, _j7_row('2026-08-01', 'ZNU26', 1, '100', '100.1'))
            J.append(jp, _j7_row('2026-08-01', 'ИТОГ', 0, '-', '',
                                 note='итог сессии 1: состояние принято по намерению, '
                                      'строки исполнения утрачены — сверка §7 обязана '
                                      'исключить сессию'))
            J.append(jp, _j7_row('2026-08-02', 'ZNU26', 1, '100', '100.1'))
            J.append(jp, _j7_row('2026-08-02', 'ИТОГ', 0, '-', '', note='итог сессии 2: строк 1'))
            out['res'] = J.reconcile(jp)
        elif case == 'счётчик итога расходится':
            # итог обещает 3 строки, в журнале 2 — часть строк исполнения утрачена
            J.append(jp, _j7_row('2026-08-03', 'ZNU26', 1, '100', '100.1'))
            J.append(jp, _j7_row('2026-08-03', 'ESU26', 1, '7000', '7001'))
            J.append(jp, _j7_row('2026-08-03', 'ИТОГ', 0, '-', '', note='итог сессии 3: строк 3'))
            out['res'] = J.reconcile(jp)
        elif case == 'пустая цена в живой строке':
            # одна сторона сессии без цены исполнения: дата обязана выйти целиком,
            # а не «посчитаться по другой ноге»
            J.append(jp, _j7_row('2026-08-04', 'ZNU26', 1, '100', '100.1'))
            J.append(jp, _j7_row('2026-08-04', 'ESU26', 1, '7000', ''))
            J.append(jp, _j7_row('2026-08-04', 'ИТОГ', 0, '-', '', note='итог сессии 4: строк 2'))
            out['res'] = J.reconcile(jp)
        elif case == 'комиссия не пришла':
            # ДВАДЦАТЫЙ КРУГ, №18: commissionReport задержался, поле пусто — расход
            # неизвестен. Прежде он молча считался нулевым, и такие даты «доказывали»
            # достаточность 5 б.п. на заниженных издержках.
            J.append(jp, _j7_row('2026-08-06', 'ZNU26', 1, '100', '100.1', commission=''))
            J.append(jp, _j7_row('2026-08-06', 'ИТОГ', 0, '-', '', note='итог сессии 6: строк 1'))
            J.append(jp, _j7_row('2026-08-07', 'ZNU26', 1, '100', '100.1'))
            J.append(jp, _j7_row('2026-08-07', 'ИТОГ', 0, '-', '', note='итог сессии 7: строк 1'))
            out['res'] = J.reconcile(jp)
        elif case == 'нет строки ИТОГ':
            # ДВАДЦАТЫЙ КРУГ, №20: процесс упал после записи исполнений, но до итога.
            # Цепочка хэшей валидна, дата выглядит полноценной — и входила в выборку.
            J.append(jp, _j7_row('2026-08-08', 'ZNU26', 1, '100', '100.1'))
            J.append(jp, _j7_row('2026-08-09', 'ZNU26', 1, '100', '100.1'))
            J.append(jp, _j7_row('2026-08-09', 'ИТОГ', 0, '-', '', note='итог сессии 9: строк 1'))
            out['res'] = J.reconcile(jp)
        elif case == 'ролл без своих двадцати сессий':
            # ДВАДЦАТЫЙ КРУГ, №19: двадцать ОБЫЧНЫХ дат плюс ОДИН ролл. Прежде порог
            # проверялся по объединению классов, и единственный перенос получал
            # полноценный вердикт против MODEL_ROLL_BP.
            for i in range(20):
                d = f'2026-06-{i + 1:02d}'
                J.append(jp, _j7_row(d, 'ZNU26', 1, '100', '100.01'))
                J.append(jp, _j7_row(d, 'ИТОГ', 0, '-', '', note=f'итог сессии {i + 1}: строк 1'))
            J.append(jp, _j7_row('2026-06-25', 'ZNU26', -1, '100', '99.95', note='ролл'))
            J.append(jp, _j7_row('2026-06-25', 'ZNZ26', 1, '100', '100.05', note='ролл'))
            J.append(jp, _j7_row('2026-06-25', 'ИТОГ', 0, '-', '', note='итог сессии 21: строк 2'))
            out['res'] = J.reconcile(jp)
        elif case == 'нечисловое наблюдение':
            J.append(jp, _j7_row('2026-08-05', 'ZNU26', 1, '100', 'nan'))
            J.append(jp, _j7_row('2026-08-05', 'ИТОГ', 0, '-', '', note='итог сессии 5: строк 1'))
            try:
                out['res'] = J.reconcile(jp)
                out['nan_refused'] = False
            except ValueError as ex:
                out['nan_refused'] = 'нечисловое' in str(ex)
    except Exception as ex:
        out['raised'] = True; out['error'] = f'{type(ex).__name__}: {ex}'
    return out


@jinv('дата с неизвестной комиссией выходит из выборки §7',
      needs=lambda r: r['case'] == 'комиссия не пришла')
def _j_comm(r):
    """ДВАДЦАТЫЙ КРУГ, №18: пустая комиссия — не нулевая. Дата с ней обязана быть названа
    в excluded с причиной, а не тихо занижать измеренный расход."""
    res = r.get('res')
    return (not r['raised'] and res is not None
            and '2026-08-06' in (res.get('excluded') or {})
            and 'комисси' in (res.get('excluded') or {}).get('2026-08-06', '')
            and res['n_sessions'] == 1)


@jinv('дата без строки ИТОГ выходит из выборки §7',
      needs=lambda r: r['case'] == 'нет строки ИТОГ')
def _j_total(r):
    """ДВАДЦАТЫЙ КРУГ, №20: проверялся только счётчик ВНУТРИ существующего итога, а его
    отсутствие не значило ничего — при валидной цепочке хэшей дата шла в выборку."""
    res = r.get('res')
    return (not r['raised'] and res is not None
            and '2026-08-08' in (res.get('excluded') or {})
            and res['n_sessions'] == 1)


@jinv('ролл не получает вердикт без своих двадцати сессий',
      needs=lambda r: r['case'] == 'ролл без своих двадцати сессий')
def _j_roll20(r):
    """ДВАДЦАТЫЙ КРУГ, №19: порог двадцати проверялся по ОБЪЕДИНЕНИЮ классов, и один
    перенос после двадцати обычных дат уже получал полноценный вердикт против
    MODEL_ROLL_BP; счётчик при этом считал строки, а не ролловые сессии."""
    res = r.get('res')
    if r['raised'] or res is None:
        return False
    roll = res.get('roll') or {}
    return (roll.get('n') == 1 and 'не делается' in str(roll.get('verdict'))
            and 'ratio' not in roll)


@jinv('пометка «исключить сессию» выводит дату из выборки §7',
      needs=lambda r: r['case'] == 'пометка исключения')
def _j1(r):
    """Восемнадцатый круг, №15 (пара): дата с пометкой не входит в счёт сессий и названа
    в excluded с причиной."""
    res = r.get('res')
    return (not r['raised'] and res is not None and res['n_sessions'] == 1
            and '2026-08-01' in (res.get('excluded') or {}))


@jinv('несовпавший счётчик строк ИТОГ исключает сессию',
      needs=lambda r: r['case'] == 'счётчик итога расходится')
def _j2(r):
    """Девятнадцатый круг, №16: итог «строк 3» при двух строках = часть исполнений
    утрачена; сессия неполна и не смеет входить в выборку как полная."""
    res = r.get('res')
    return (not r['raised'] and res is not None and res['n_sessions'] == 0
            and '2026-08-03' in (res.get('excluded') or {}))


@jinv('живая строка без цены исключает дату целиком',
      needs=lambda r: r['case'] == 'пустая цена в живой строке')
def _j3(r):
    """Девятнадцатый круг, №16: систематически пустая сторона ролла прежде исчезала из
    выборки МОЛЧА, а дата продолжала считаться по другой ноге."""
    res = r.get('res')
    return (not r['raised'] and res is not None and res['n_sessions'] == 0
            and '2026-08-04' in (res.get('excluded') or {}))


@jinv('нечисловое наблюдение — громкий отказ сверки, а не «в пределах»',
      needs=lambda r: r['case'] == 'нечисловое наблюдение')
def _j4(r):
    """Девятнадцатый круг, №16: NaN проходил арифметику, сравнения лгали, и вердикт
    становился «в пределах двукратного» — порча данных доказывала параметр."""
    return not r['raised'] and r.get('nan_refused') is True


@jinv('ролловая ставка §7 — от одностороннего номинала',
      needs=lambda r: r['case'] == 'ролловый номинал односторонний')
def _j5(r):
    """Восемнадцатый круг, №16 (пара): потеря обеих сторон делится на номинал ОДНОЙ
    (закрывающей): здесь ~10 б.п.; двусторонний номинал показал бы ~5 и «доказал бы»
    заниженный параметр."""
    res = r.get('res')
    if r['raised'] or res is None:
        return False
    roll = res.get('roll') or {}
    # n — РОЛЛОВЫЕ СЕССИИ, а не строки (двадцатый круг, №19): двадцать переносов по две
    # строки давали n=40 и выглядели вдвое более доказанными, чем есть.
    return (roll.get('n') == 20 and roll.get('n_rows') == 40
            and abs(roll.get('bp', 0.0) - 10.0) < 0.2)


@jinv('ролловая доля меньше одного ES не теряется',
       needs=lambda r: r['case'] == 'ролл меньше одного ES')
def _j33(r):
    """ТРИДЦАТЬ ТРЕТИЙ КРУГ, №11: закрывающая сторона обязана быть помечена роллом, иначе
    сверка §7 сравнивает ролловые издержки не с той моделью, а _roll_block достраивает
    недостающую сторону половиной номинала открывающей."""
    rows = r.get('rows') or []
    if r['raised'] or not rows:
        return False
    es = [x for x in rows if x['instrument'] == 'ESU26']
    mes = [x for x in rows if x['instrument'] == 'MESZ26']
    # ПРОВЕРЯЮТСЯ КОЛИЧЕСТВА, А НЕ НАЛИЧИЕ СЛОВА «ролл» (тридцать четвёртый круг, №11):
    # переносится 5 единиц сетки из 10, значит ролловая доля ES — ровно половина контракта,
    # остаток — обычная сделка. Прежняя редакция стенда была зелёной и когда весь ES уходил
    # в ролл, то есть закрепляла как норму ровно тот дефект, ради которого заведена.
    _es_roll = sum(float(x['qty']) for x in es if 'ролл' in (x.get('note') or ''))
    _es_rest = sum(float(x['qty']) for x in es if 'ролл' not in (x.get('note') or ''))
    # ВТОРОЙ СЛУЧАЙ (№9): 1,5 ES в ролл, 0,5 ES обычной сделкой.
    _rows2 = r.get('rows2') or []
    _es2 = [x for x in _rows2 if x['instrument'] == 'ESU26']
    _r2 = sum(float(x['qty']) for x in _es2 if 'ролл' in (x.get('note') or ''))
    _o2 = sum(float(x['qty']) for x in _es2 if 'ролл' not in (x.get('note') or ''))
    return (abs(_es_roll + 0.5) < 1e-9 and abs(_es_rest + 0.5) < 1e-9
            and abs(_r2 + 1.5) < 1e-9 and abs(_o2 + 0.5) < 1e-9
            and any('ролл' in (x.get('note') or '') for x in mes))


def run_j7():
    cov, bad = {}, {}
    for case in J7_CASES:
        r = _j7_run(case)
        for name, fn, needs in J7:
            if needs is not None and not needs(r):
                continue
            cov[name] = cov.get(name, 0) + 1
            try:
                ok = fn(r)
            except Exception as ex:
                ok = False; name = f'{name} [исключение: {type(ex).__name__}]'
            if not ok:
                bad.setdefault(name, []).append(f"{case}: {r['error'][:80]}")
    return cov, bad


def run_sessions():
    import tempfile, state as ST
    from fake_broker import FakeBroker, BrokerError

    class Frac(FakeBroker):
        """Брокер, исполняющий дробно — путь, недостижимый прежним перебором."""
        def place(self, instrument, qty, px_order=None):
            return FakeBroker.place(self, instrument, qty * 0.5 if qty else qty, px_order)

    out = []
    cases = [('F', b, ds, roll, beh, nan, op)
             for b in ((260, 101, 26), (0, 0, None), (100, 50, 10))
             for ds, roll in (('2026-08-26', True), ('2026-08-10', False))
             for beh in ('normal', 'partial', 'overfill', 'reject', 'disconnect')
             for nan, op in ((False, False), (True, False), (False, True))]
    cases += [('E', (0, 0, None), '2026-08-10', False, beh, False, False)
              for beh in ('normal', 'partial', 'reject', 'disconnect')]
    for route, (ne, nb, es), ds, roll, beh, nan_case, had_open in cases:
        with tempfile.TemporaryDirectory() as tmp:
            if route == 'E':
                b = DL.BookE(prev_st_eq=False, prev_st_bd=False)
                m = DL.MarketE(date=pd.Timestamp(ds), px_eq_prev=700.0, px_bd_prev=5.0,
                               px_eq_today=700.0, px_bd_today=5.0, st_eq=True, st_bd=True)
                prices = {'CSPX': 700.0, 'CBU0': 5.0}
            else:
                b = DL.Book(d_fix=8.0, n_e=ne, n_b=nb, unit_is_mes=True, prev_st_eq=True,
                            prev_st_bd=True, ser_a='U26' if ne or nb else None,
                            ser_b='U26' if ne or nb else None, es_held=es)
                m = DL.Market(date=pd.Timestamp(ds), px_eq_prev=600.0, dref_prev=8.0,
                              dref_today=8.0, px_eq_today=600.0, roll_today=roll,
                              st_eq=True, st_bd=True)
                prices = {k: 600.0 if 'ES' in k else 112.0
                          for k in list(DL.physical_book(b)) + ['ESZ26', 'MESZ26', 'ZNZ26']}
            sp = Path(tmp) / 'book.json'
            dig0 = ST.save(sp, b, route, 1)
            pos = DL.physical_book(b) if route == 'F' else {}
            if nan_case and pos:
                pos = dict(pos); pos[list(pos)[0]] = float('nan')
            BR = Frac if beh == 'partial' and route == 'E' else FakeBroker
            br = BR(prices=prices, positions=dict(pos),
                    behaviour=beh if BR is FakeBroker else 'normal')
            br.nlv = 10_000_000.0
            if had_open:
                br._orders[999] = dict(status='open', instrument='ZNU26', qty=1)
            raised = rollback = False
            err = ''
            planned = []
            # ОЖИДАЕМЫЕ заявки считаются ДО сессии и не зависят от того, упала ли она.
            # Прежнее условие опиралось на результат, который при исключении пуст, и
            # инвариант оказывался вакуумным — ровно та ловушка, что и в прошлый раз.
            try:
                _d0 = (DL.step_e(b, m, 10_000_000.0) if route == 'E'
                       else DL.step(b, m, 10_000_000.0))
                orders_expected = bool(DL.orders_from_books(b, _d0.book_after)
                                       if route == 'F' else _d0.orders)
            except Exception:
                orders_expected = False
            # ОРИЕНТИРЫ И ЖУРНАЛ — КАК В БОЮ (двадцать второй круг, №17): без них живой
            # вход теперь отказывает ДО сценария, и ВСЕ повадки падали одинаково — _s1
            # получал покрытие 0, а сценарные различия не проверялись вовсе.
            import journal as _J7s
            _jps = Path(tmp) / f'journal-{route}.csv'
            if not _jps.exists():
                _J7s.append(_jps, dict(date='2026-08-11', leg='', instrument='ИТОГ',
                                       qty=0, px_order='-', px_fill='', commission='',
                                       reason='', nav='10000000', leverage='1.0',
                                       roll_spread_near='', roll_spread_far='',
                                       note='итог сессии 1: строк 0'))
            try:
                # ПУТЬ КНИГИ ПЕРЕДАЁТСЯ ЯВНО: контур больше не выводит его из окружения,
                # и стенд обязан подавать тот же файл, который сам создал.
                dec, planned, _ = DL.run_session(br, m, dirpath=tmp, route=route,
                                                 capital=10_000_000.0,
                                                 closing_nav=10_000_000.0,
                                                 book_path=str(sp),
                                                 ref_prices=dict(prices),
                                                 journal_path=str(_jps))
            except DL.RollGap as ex:
                raised = True
                rollback = ('приведена к исходной' in str(ex)
                            or 'соответствует исходной' in str(ex))
                err = str(ex)
            except Exception as ex:
                raised = True; err = str(ex)
            cls = DL.BookE if route == 'E' else DL.Book
            saved, sess, _ = ST.load(sp, cls)
            raw = __import__('json').loads(sp.read_text())
            out.append(dict(route=route, behaviour=beh, raised=raised, rollback=rollback,
                            error=err,
                            had_open=had_open, nan_case=bool(nan_case and pos),
                            saved=saved, orders_planned=bool(planned),
                            orders_expected=orders_expected,
                            placed_count=len(br.log),
                            open_after=list(br.open_orders() or []),
                            broker_after={k: v for k, v in br.net_positions().items()
                                          if v == v and v},
                            digest_before=dig0, saved_digest=raw['digest'],
                            note_pending=bool(getattr(saved, 'roll_pending', False))))
    return out


if __name__ == '__main__':
    bad = {}
    n = 0
    for b, m, cap0, paper in states():
        n += 1
        try:
            d = DL.step(b, m, cap0, paper=paper)
        except Exception as ex:
            bad.setdefault(f'step падает с исключением [{type(ex).__name__}]', []).append(
                f'книга {b.n_e}/{b.n_b} es={b.es_held} серии {b.ser_a}/{b.ser_b} '
                f'pending={b.roll_pending}, {m.date:%d.%m} ролл={m.roll_today}')
            continue
        orders = DL.book_to_orders(d, b)
        u_e, u_b = DL.units(b, m)
        for name, fn, needs in INVARIANTS:
            # ПРЕДИКАТ ОТБОРА — ПОД ТОЙ ЖЕ ЗАЩИТОЙ, ЧТО И САМ ИНВАРИАНТ (разбор /code-review
            # 45-го круга). needs() зовут ВНЕ try, а он ходит в тот же движок: после того
            # как _roll_deadline_or_stop перестал быть fail-open, DL.missed_roll_check внутри
            # needs получил право БРОСАТЬ. Любая книга с непарсимым тегом серии роняла бы
            # весь прогон сырым RuntimeError — то есть скрывала бы и все прочие результаты,
            # ради которых прогон и запущен. Отказ предиката — это отказ, а не тишина.
            try:
                _need = needs(b, m, cap0, d, orders, u_e, u_b) if needs is not None else True
            except Exception as ex:
                bad.setdefault(f'{name} [предикат отбора падает: {type(ex).__name__}]',
                               []).append(f'книга {b.n_e}/{b.n_b} es={b.es_held} '
                                          f'серии {b.ser_a}/{b.ser_b}, {m.date:%d.%m}')
                continue
            if not _need:
                continue
            COVER[name] = COVER.get(name, 0) + 1
            try:
                ok = fn(b, m, cap0, d, orders, u_e, u_b)
            except Exception as ex:
                ok = False; name = f'{name} [исключение: {type(ex).__name__}]'
            if not ok:
                bad.setdefault(name, []).append(
                    f'книга {b.n_e}/{b.n_b} es={b.es_held}, капитал {cap0:,.0f}, '
                    f'{m.date:%d.%m}, ролл={m.roll_today}, сигналы {m.st_eq}/{m.st_bd}')
    print(f'состояний проверено: {n}, инвариантов: {len(INVARIANTS)}\n')
    for name, _, _n in INVARIANTS:
        cases = bad.get(name, []); cov = COVER.get(name, 0)
        if cov == 0:
            bad.setdefault(name, []).append('ПОКРЫТИЕ НУЛЕВОЕ — инвариант вакуумен')
            print(f'[FAIL] {name}: НИ РАЗУ НЕ ПРОВЕРЕН (покрытие 0)')
            continue
        mark = f'ДЕРЖИТСЯ на {cov} состояниях' if not cases else f'НАРУШЕН в {len(cases)}'
        print(f'[{"OK  " if not cases else "FAIL"}] {name}: {mark}')
        for c in cases[:3]:
            print(f'         {c}')
    extra = {k: v for k, v in bad.items() if k not in {n for n, _, _ in INVARIANTS}}
    for k, v in extra.items():
        print(f'[FAIL] {k}: {len(v)} состояний')
        for c in v[:4]:
            print(f'         {c}')
    print()
    res = run_sessions()
    sbad = {}
    for r in res:
        for name, fn, needs in SESSION_INVARIANTS:
            if needs is not None and not needs(r):
                continue
            SCOVER[name] = SCOVER.get(name, 0) + 1
            try:
                ok = fn(r)
            except Exception as ex:
                ok = False; name = f'{name} [исключение: {type(ex).__name__}]'
            if not ok:
                sbad.setdefault(name, []).append(f"брокер {r['behaviour']}, поднято={r['raised']}")
    print(f'сессий проверено: {len(res)}, инвариантов сессии: {len(SESSION_INVARIANTS)}\n')
    for name, _, _n in SESSION_INVARIANTS:
        cases = sbad.get(name, []); cov = SCOVER.get(name, 0)
        if cov == 0:
            sbad.setdefault(name, []).append('ПОКРЫТИЕ НУЛЕВОЕ')
            print(f'[FAIL] {name}: НИ РАЗУ НЕ ПРОВЕРЕН (покрытие 0)'); continue
        print(f'[{"OK  " if not cases else "FAIL"}] {name}: '
              f'{f"ДЕРЖИТСЯ на {cov}" if not cases else f"НАРУШЕН в {len(cases)} из {cov}"}')
        for c in cases[:3]:
            print(f'         {c}')
    # --- ЖИВОЙ АДАПТЕР. Прежде под перебор попадал только расчётчик, и код, реально
    # ходящий на биржу, не проверялся ничем.
    acov, abad = run_adapter()
    print(f'\nсценариев брокера: {len(ADAPTER_CASES)}, '
          f'утверждений адаптера: {len(ADAPTER)}\n')
    for name, _, _n in ADAPTER:
        cov = acov.get(name, 0)
        if cov == 0:
            abad.setdefault(name, []).append('ПОКРЫТИЕ НУЛЕВОЕ')
            print(f'[FAIL] {name}: НИ РАЗУ НЕ ПРОВЕРЕН (покрытие 0)'); continue
        cases = abad.get(name, [])
        print(f'[{"OK  " if not cases else "FAIL"}] {name}: '
              f'{f"ДЕРЖИТСЯ на {cov}" if not cases else f"НАРУШЕН: {cases}"}')
    # --- НАМЕРЕНИЕ СЕССИИ: три исхода обрыва между сделкой и записью состояния.
    icov, ibad = run_intent()
    print(f'\nсценариев обрыва: 5, утверждений намерения: {len(INTENT)}\n')
    for name, _, _n in INTENT:
        cov = icov.get(name, 0)
        if cov == 0:
            ibad.setdefault(name, []).append('ПОКРЫТИЕ НУЛЕВОЕ')
            print(f'[FAIL] {name}: НИ РАЗУ НЕ ПРОВЕРЕН (покрытие 0)'); continue
        cases = ibad.get(name, [])
        print(f'[{"OK  " if not cases else "FAIL"}] {name}: '
              f'{f"ДЕРЖИТСЯ на {cov}" if not cases else f"НАРУШЕН: {cases}"}')
    # --- СБОРЩИК ВХОДОВ: единицы, даты баров, разбор сигнала.
    fcov, fbad = run_feed()
    print(f'\nслучаев входа: {len(FEED_CASES)}, утверждений сборщика: {len(FEED)}\n')
    for name, _, _n in FEED:
        cov = fcov.get(name, 0)
        if cov == 0:
            fbad.setdefault(name, []).append('ПОКРЫТИЕ НУЛЕВОЕ')
            print(f'[FAIL] {name}: НИ РАЗУ НЕ ПРОВЕРЕН (покрытие 0)'); continue
        cases = fbad.get(name, [])
        print(f'[{"OK  " if not cases else "FAIL"}] {name}: '
              f'{f"ДЕРЖИТСЯ на {cov}" if not cases else f"НАРУШЕН: {cases}"}')
    # --- ЗАПУСК СЕССИИ: порядок действий, а не арифметика.
    rcov, rbad = run_run()
    print(f'\nслучаев запуска: {len(RUN_CASES)}, утверждений запуска: {len(RUN)}\n')
    for name, _, _n in RUN:
        cov = rcov.get(name, 0)
        if cov == 0:
            rbad.setdefault(name, []).append('ПОКРЫТИЕ НУЛЕВОЕ')
            print(f'[FAIL] {name}: НИ РАЗУ НЕ ПРОВЕРЕН (покрытие 0)'); continue
        cases = rbad.get(name, [])
        print(f'[{"OK  " if not cases else "FAIL"}] {name}: '
              f'{f"ДЕРЖИТСЯ на {cov}" if not cases else f"НАРУШЕН: {cases}"}')
    # --- ПЕРЕХОДНЫЙ ИСПОЛНИТЕЛЬ: лимит непарной дельты и целостность плана.
    tcov, tbad = run_transition()
    print(f'\nслучаев перехода: {len(TR_CASES)}, утверждений перехода: {len(TR)}\n')
    for name, _, _n in TR:
        cov = tcov.get(name, 0)
        if cov == 0:
            tbad.setdefault(name, []).append('ПОКРЫТИЕ НУЛЕВОЕ')
            print(f'[FAIL] {name}: НИ РАЗУ НЕ ПРОВЕРЕН (покрытие 0)'); continue
        cases = tbad.get(name, [])
        print(f'[{"OK  " if not cases else "FAIL"}] {name}: '
              f'{f"ДЕРЖИТСЯ на {cov}" if not cases else f"НАРУШЕН: {cases}"}')
    # --- КАЛЕНДАРЬ И ПЕРЕХОД: последовательности вокруг ролла.
    lcov, lbad = run_roll()
    print(f'\nслучаев ролла: {len(ROLL_CASES)}, утверждений ролла: {len(ROLL)}\n')
    for name, _, _n in ROLL:
        cov = lcov.get(name, 0)
        if cov == 0:
            lbad.setdefault(name, []).append('ПОКРЫТИЕ НУЛЕВОЕ')
            print(f'[FAIL] {name}: НИ РАЗУ НЕ ПРОВЕРЕН (покрытие 0)'); continue
        cases = lbad.get(name, [])
        print(f'[{"OK  " if not cases else "FAIL"}] {name}: '
              f'{f"ДЕРЖИТСЯ на {cov}" if not cases else f"НАРУШЕН: {cases}"}')
    # --- ОТКАЗ §8 С РОСТОМ: точечный сценарий вне перебора (шестнадцатый круг, №1/№6).
    fcov8, fbad8 = run_refusal()
    print(f'\nслучаев отказа с ростом: {len(REF8_CASES)}, утверждений: {len(REF8)}\n')
    for name, _, _n in REF8:
        cov = fcov8.get(name, 0)
        if cov == 0:
            fbad8.setdefault(name, []).append('ПОКРЫТИЕ НУЛЕВОЕ')
            print(f'[FAIL] {name}: НИ РАЗУ НЕ ПРОВЕРЕН (покрытие 0)'); continue
        cases = fbad8.get(name, [])
        print(f'[{"OK  " if not cases else "FAIL"}] {name}: '
              f'{f"ДЕРЖИТСЯ на {cov}" if not cases else f"НАРУШЕН: {cases}"}')
    # --- ИЗБЫТОК УПАКОВКИ И ВОРОТА КАПА: точечные сценарии вне перебора (29-й круг, №6).
    pcov, pbad = run_pack()
    print(f'\nслучаев упаковки: {len(PACK_CASES)}, утверждений упаковки: {len(PACK)}\n')
    for name, _, _n in PACK:
        cov = pcov.get(name, 0)
        if cov == 0:
            pbad.setdefault(name, []).append('ПОКРЫТИЕ НУЛЕВОЕ')
            print(f'[FAIL] {name}: НИ РАЗУ НЕ ПРОВЕРЕН (покрытие 0)'); continue
        cases = pbad.get(name, [])
        print(f'[{"OK  " if not cases else "FAIL"}] {name}: '
              f'{f"ДЕРЖИТСЯ на {cov}" if not cases else f"НАРУШЕН: {cases}"}')
    # --- ОБНОВЛЯТОР СИГНАЛОВ: механика замка, сверки и дописывания.
    gcov, gbad = run_signal()
    print(f'\nслучаев сигнала: {len(SIG_CASES)}, утверждений сигнала: {len(SIG)}\n')
    for name, _, _n in SIG:
        cov = gcov.get(name, 0)
        if cov == 0:
            gbad.setdefault(name, []).append('ПОКРЫТИЕ НУЛЕВОЕ')
            print(f'[FAIL] {name}: НИ РАЗУ НЕ ПРОВЕРЕН (покрытие 0)'); continue
        cases = gbad.get(name, [])
        print(f'[{"OK  " if not cases else "FAIL"}] {name}: '
              f'{f"ДЕРЖИТСЯ на {cov}" if not cases else f"НАРУШЕН: {cases}"}')
    # --- СВЕРКА §7: полнота против ИТОГ, конечность, односторонний ролл (19-й круг, №16).
    jcov, jbad = run_j7()
    print(f'\nслучаев §7: {len(J7_CASES)}, утверждений §7: {len(J7)}\n')
    for name, _, _n in J7:
        cov = jcov.get(name, 0)
        if cov == 0:
            jbad.setdefault(name, []).append('ПОКРЫТИЕ НУЛЕВОЕ')
            print(f'[FAIL] {name}: НИ РАЗУ НЕ ПРОВЕРЕН (покрытие 0)'); continue
        cases = jbad.get(name, [])
        print(f'[{"OK  " if not cases else "FAIL"}] {name}: '
              f'{f"ДЕРЖИТСЯ на {cov}" if not cases else f"НАРУШЕН: {cases}"}')
    # --- ИНТЕРФЕЙС АДАПТЕРА против ЖИВОГО ib_insync (двадцатый круг, №15: замечание
    # отклонено по факту, но класс «стенды доказывают стаб» признан).
    print()
    # SAME_API ГОНЯЕТСЯ В БАТАРЕЕ (двадцать четвёртый круг, №24): прежде он запускался
    # ТОЛЬКО при прямом исполнении ib_broker.py, то есть ни в батарее, ни в выпуске. Проверка
    # имён методов (check_ib_interface) не ловит расхождение СОДЕРЖИМОГО записи исполнения,
    # а именно на нём стоят все сценарии на макете.
    _iface_ok = check_ib_interface()
    try:
        import ib_broker as _IBB2
        _same = _IBB2.SAME_API()
        if _same:
            print('[FAIL] SAME_API: макет и живой адаптер расходятся:')
            for _x in _same:
                print('   ', _x)
            _iface_ok = False
        else:
            print('[OK  ] SAME_API: макет и живой адаптер дают одинаковые записи')
    except Exception as _exs:
        print(f'[FAIL] SAME_API не выполнен: {_exs}')
        _iface_ok = False
    sys.exit(0 if (_iface_ok and not (bad or sbad or abad or ibad or fbad or rbad or tbad
                                      or lbad or gbad or fbad8 or jbad or pbad)) else 1)
