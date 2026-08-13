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
import sys
from pathlib import Path
import itertools

ROOT = Path(__file__).resolve().parent.parent
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
    legs = {p['leg'] for p in (d.roll_pairs or ())}
    need = {l for l, n in (('А', b.n_e), ('Б', b.n_b)) if n}
    return need <= legs


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
    return all(k in ('CSPX', 'CBU0') for k in res['broker_after'])


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
    reg = ib_stub.fixture_registry(d)
    rows = list(ib_stub.FIXTURE_ROWS)
    ib = ib_stub.StubIB(rows, behaviour=behaviour, positions=positions or {})
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
    if 501 in vis or 502 not in vis or 503 not in vis:
        return False
    stuck = DLm._cancel_all(br)
    still = {t.order.orderId for t in ib.openTrades()}
    return sorted(stuck) == [502, 503] and {502, 503} <= still


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


ADAPTER_CASES = ('normal', 'partial', 'reject', 'disconnect', 'cancelled_but_filled',
                 'stale_positions', 'foreign_fill', 'wrong_contract', 'late_fills',
                 'late_cancelled', 'other_account', 'fill_after_end',
                 'stale_twice', 'foreign_orders', 'orders_req_fails', 'nan_cushion')


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
        out = dict(kind=kind, raised=False, error='', dec=None, orders=None)
        try:
            dec, ords, diff = DL.run_session(br, m, dirpath=tmp, route='F',
                                             capital=10_000_000.0, closing_nav=10_000_000.0,
                                             book_path=str(bp))
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


def run_intent():
    cov, bad = {}, {}
    cases = [('не начиналось', _intent_case), ('прошло целиком', _intent_case),
             ('промежуточное', _intent_case),
             ('ролл исполнен целиком', _intent_full), ('ролл не начинался', _intent_full),
             ('исполнение в пути', _intent_full), ('состояние уже дописано', _intent_full)]
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
              'пропущена ровно одна сессия')


def _feed_run(case):
    """Собрать вход на подставной истории. Возвращает (Market | None, текст отказа)."""
    import tempfile
    import ib_stub
    import feed as FD
    import pandas as pd

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

    # TNX не лежит в реестре — сборщик берёт его отдельным индексом; подставляем описание.
    ib.rows[tnx] = dict(instrument='TNX', sec_type='IND', exchange='CBOE', currency='USD',
                        con_id=str(tnx), local_symbol='TNX', expiry='', multiplier='')

    tmp = tempfile.mkdtemp(prefix='addfut-feed-')
    reg = ib_stub.fixture_registry(tmp)
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
    при исправном счёте. Ни отказа, ни абсурдной цифры при этом не возникало."""
    if r['m'] is None:
        return False
    return abs(r['src']['nominal_ES'] - 50.0 * r['src']['es_close'][0]) < 1e-6


@finv('отставший бар отвергается', needs=lambda r: r['case'] == 'бар вчерашний повторён')
def _f3(r):
    return r['m'] is None and 'отстал' in r['err']


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
    ДАВНОСТИ — и только она."""
    return r['m'] is None and 'отстал' in r['err']


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
             'позднее исполнение после сессии', 'гонка при замыкании',
             'смена маршрута в связке с торговлей', 'замок между процессами',
             'маршрут Е при тонком запасе', 'отказ дня по §8',
             'ролл: исход заявки неизвестен', 'журнал повреждён')


def _session_run(case):
    """Прогнать запуск сессии на подставном брокере. Возвращает описание исхода."""
    import os
    import tempfile
    import ib_stub
    import feed as FD
    import session as SS
    import state as ST
    import pandas as pd

    route = 'E' if case == 'маршрут Е' else 'F'
    es, zn, tnx, cspx, cbu0 = 900001, 900003, 990001, 900004, 900005
    rows = list(ib_stub.FIXTURE_ROWS)
    ib = ib_stub.StubIB(rows, nlv=1_000_000.0)
    ib.rows[tnx] = dict(instrument='TNX', sec_type='IND', exchange='CBOE', currency='USD',
                        con_id=str(tnx), local_symbol='TNX', expiry='', multiplier='')
    prev, today = '2026-08-11', '2026-08-12'
    # SPY НАМЕРЕННО НЕ РАВЕН ES/10 (десятый круг, №12): при совпадающих значениях стенд не
    # отличил бы замыкание по SPY от замыкания по ES/10 — базис здесь ~-0,16%.
    bars = {900010: [('2026-08-10', 771.2), (prev, 776.0)],
            es: [('2026-08-10', 7700.0), (prev, 7747.5)],
            900002: [('2026-08-10', 7700.0), (prev, 7747.5)],
            zn: [('2026-08-10', 108.4), (prev, 108.5)],
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
    book_bytes0 = None
    try:
        ib_insync.Index = Idx
        FD.registry = lambda: {r['instrument']: r for r in
                               __import__('csv').DictReader(open(reg, encoding='utf-8'))}
        _sig = FD.signal_state
        FD.signal_state = lambda t, path=None, **kw: _sig(t, path=sig, **kw)
        # Час выбирается СЦЕНАРИЕМ: замыкание до закрытия биржи обязано быть отвергнуто.
        hour = 9 if case == 'замыкание рано' else 17
        FD.exchange_today = lambda: pd.Timestamp(today)
        _now = pd.Timestamp(f'{today} {hour:02d}:00', tz=FD.EXCHANGE_TZ)
        real_now = pd.Timestamp.now
        os.environ['ADDFUT_DIR'] = tmp
        os.environ['ADDFUT_LOCK_DIR'] = tmp
        os.environ['ADDFUT_REGISTRY'] = str(reg)
        os.environ.pop('ADDFUT_BOOK_PATH', None)

        bp = Path(tmp) / f'book-{route}.json'
        cls = DL.BookE if route == 'E' else DL.Book
        if case == 'незамкнутая предыдущая' or case.startswith('замыкание'):
            b0 = cls(prev_st_eq=True, prev_st_bd=True) if route == 'E' else DL.Book(
                d_fix=7.9, n_e=26, n_b=10, unit_is_mes=True, prev_st_eq=True, prev_st_bd=True,
                ser_a='U26', ser_b='U26', es_held=2,
                last_session=('2026-08-10' if case == 'замыкание за чужую дату'
                              else ('2026-08-11' if case == 'незамкнутая предыдущая' else today)),
                close_provisional=(case != 'замыкание повторное'), prev_close_lev=1.99)
            ST.save(bp, b0, route, 1)
            if case.startswith('замыкание'):
                ib._pos = {es: 2, 900002: 6, zn: 10}
                ib._shown = dict(ib._pos)
        if case == 'маршрут Е при тонком запасе':
            # Живой запас О-3-Е от брокера: EWL/Maint = 1,20 < 1,40 — обязательное сокращение.
            ib.behaviour = 'thin_cushion'
            b0 = DL.BookE(n_eq=1195, n_bd=6538, prev_st_eq=True, prev_st_bd=True,
                          last_session='2026-08-11', close_provisional=False,
                          prev_close_lev=1.99)
            ST.save(Path(tmp) / 'book-E.json', b0, 'E', 3)
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
            ST.save(bp, b0, route, 2)
            ib._pos = {es: 2, 900002: 6, zn: 10}
            ib._shown = dict(ib._pos)
            book_bytes0 = bp.read_bytes()
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
        pd.Timestamp.now = staticmethod(lambda tz=None: _now if tz else real_now())
        if case.startswith('замыкание'):
            out['dec'] = SS.do_close(ib, route)
        elif case == 'маршрут Е при тонком запасе':
            out['dec'] = SS.do_trade(ib, 'E', dry=False)
        else:
            out['dec'] = SS.do_trade(ib, route, dry=(case == 'наблюдение'))
    except BaseException as ex:
        out['raised'] = True; out['error'] = f'{type(ex).__name__}: {ex}'
    finally:
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
    return out


@rinv('наблюдение не подаёт заявок и не меняет состояние',
      needs=lambda r: r['case'] == 'наблюдение')
def _r1(r):
    return not r['raised'] and r['placed'] == 0 and not r['positions']


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


@rinv('тонкий живой запас О-3-Е сокращает книгу маршрута Е',
      needs=lambda r: r['case'] == 'маршрут Е при тонком запасе')
def _r20(r):
    """Запас 1,20 от брокера ниже порога 1,40: сокращение обязано пройти ЖИВЫМ путём —
    do_trade -> margin_cushion -> step_e(live_cushion=...) — а не ручной подстановкой в
    тест (десятый круг, №2 и №12)."""
    if r['raised']:
        return False
    d = r['dec']
    return (d is not None and any('О-3-Е' in x for x in d.reasons)
            and d.book_after.n_eq < 1195 and d.book_after.n_bd < 6538)


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
    if r['raised']:
        return False
    return all(k in (900004, 900005) for k in r['positions'])


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
        os.environ['ADDFUT_REGISTRY'] = str(reg)
        os.environ.pop('ADDFUT_BOOK_PATH', None)
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
        os.environ['ADDFUT_REGISTRY'] = str(reg)
        os.environ.pop('ADDFUT_BOOK_PATH', None)
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
    import state as ST
    tmp = tempfile.mkdtemp(prefix='addfut-lock-')
    env = dict(os.environ, ADDFUT_LOCK_DIR=tmp)
    child = [_sys.executable, '-c',
             'import sys; sys.path.insert(0, %r); import state as ST\n'
             'try:\n'
             '    with ST.hold_book_lock(timeout_s=1):\n'
             '        print("ВЗЯЛ")\n'
             'except RuntimeError:\n'
             '    print("ЗАНЯТО")' % str(Path(__file__).resolve().parent)]
    out = dict(case='замок между процессами', held='', freed='', raised=False)
    keep_env = os.environ.get('ADDFUT_LOCK_DIR')
    try:
        # ОКРУЖЕНИЕ РОДИТЕЛЯ И РЕБЁНКА ВЫРАВНИВАЕТСЯ ЯВНО: env имеет приоритет над hint, и
        # под selfcheck (свой ADDFUT_LOCK_DIR) родитель запирал каталог самопроверки, а
        # ребёнок — tmp: снова два файла. Оба обязаны смотреть в ОДИН каталог.
        os.environ['ADDFUT_LOCK_DIR'] = tmp
        with ST.hold_book_lock(tmp):
            r1 = subprocess.run(child, env=env, capture_output=True, text=True, timeout=30)
            out['held'] = (r1.stdout or '').strip()
        r2 = subprocess.run(child, env=env, capture_output=True, text=True, timeout=30)
        out['freed'] = (r2.stdout or '').strip()
    except Exception as ex:
        out['raised'] = True; out['error'] = f'{type(ex).__name__}: {ex}'
    finally:
        os.environ.pop('ADDFUT_LOCK_DIR', None)
        if keep_env is not None:
            os.environ['ADDFUT_LOCK_DIR'] = keep_env
    return out


@rinv('чужой процесс не берёт занятый замок и берёт свободный',
      needs=lambda r: r['case'] == 'замок между процессами')
def _r19(r):
    return not r['raised'] and r['held'] == 'ЗАНЯТО' and r['freed'] == 'ВЗЯЛ'


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
        os.environ['ADDFUT_REGISTRY'] = str(reg)
        os.environ.pop('ADDFUT_BOOK_PATH', None)
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
        pd.Timestamp.now = staticmethod(
            lambda tz=None: pd.Timestamp('2026-08-13 10:00', tz=FD.EXCHANGE_TZ) if tz
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


def run_run():
    cov, bad = {}, {}
    for case in RUN_CASES:
        try:
            r = (_session_days() if case == 'три сессии подряд'
                 else _session_late_fill() if case == 'позднее исполнение после сессии'
                 else _session_race() if case == 'гонка при замыкании'
                 else _session_route_switch()
                 if case == 'смена маршрута в связке с торговлей'
                 else _session_lock() if case == 'замок между процессами'
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
            'дробный фьючерс в плане', 'исполнение в лимите', 'дробное исполнение фьючерса',
            'повторный запуск после обрыва', 'ИСПОЛНЕНИЕ дробного источника',
            'источник мельче зерна цели', 'битый замер маржи', 'замер без привязки',
            'замер прежней серии', 'замер не покрывает корень', 'замер отсутствует',
            'живой замер покрывает', 'частичный прогресс лота при resume',
            'остаток непарной дельты при resume')


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
    def __init__(self, frac_of=None):
        self.frac_of = frac_of      # инструмент, по которому исполнение дробное
        self.calls = []
        self.n = 0

    def _f(self, instr, u):
        return u - 0.5 if self.frac_of == instr and u > 0 else u

    def sell_units(self, i, u):
        self.n += 1; self.calls.append(('sell', i, u)); return (f's{self.n}', self._f(i, u))

    def buy_units(self, i, u):
        self.n += 1; self.calls.append(('buy', i, u)); return (f'b{self.n}', self._f(i, u))

    def cancel_order(self, o): return True
    def open_orders(self): return []
    def net_positions(self): return {}
    def minutes_since(self, k): return 0
    def gross(self): return 1.50


def _tr_run(case):
    import tempfile
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import transition as TRN

    out = dict(case=case, raised=False, error='', lots=None, peak=None,
               calls=None, second_calls=None, log=None)
    ZN_U = 98_560.0
    legs_fut = {'Б': dict(src=[('ZN', 10, ZN_U)], dst=('CBU0', 5.0, 'ETF'))}
    legs_frac = {'Б': dict(src=[('CBU0', 2_000_000.5, 5.0)], dst=('ZN', ZN_U, 'FUT'))}
    legs_dup = {'Б': dict(src=[('ZN', 10, ZN_U), ('ZN', 5, ZN_U)], dst=('CBU0', 5.0, 'ETF'))}
    legs_bad = {'Б': dict(src=[('ZN', 10.5, ZN_U)], dst=('CBU0', 5.0, 'ETF'))}

    try:
        if 'замер' in case:
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
            if case == 'битый замер маржи':
                mp.write_text('{оборвано', encoding='utf-8')
            elif case == 'замер без привязки':
                mp.write_text(_json.dumps({'ESU26': {'maint': 40000.0},
                                           'ZNU26': {'maint': 2500.0}}), encoding='utf-8')
            elif case == 'замер прежней серии':
                mp.write_text(_json.dumps({'_meta': {'date': 'x', 'series': ['ESZ25']},
                                           'ESZ25': {'maint': 30000.0}}), encoding='utf-8')
            elif case == 'замер не покрывает корень':
                mp.write_text(_json.dumps({'_meta': {'date': 'x', 'series': ['ZNU26']},
                                           'ZNU26': {'maint': 2500.0}}), encoding='utf-8')
            elif case == 'живой замер покрывает':
                mp.write_text(_json.dumps({'_meta': {'date': 'x',
                                                     'series': ['ESU26', 'ZNU26']},
                                           'ESU26': {'maint': 40000.0},
                                           'ZNU26': {'maint': 2500.0}}), encoding='utf-8')
            keepm = _os.environ.get('ADDFUT_MARGINS')
            keepr = _os.environ.get('ADDFUT_REGISTRY')
            _os.environ['ADDFUT_MARGINS'] = str(mp)
            _os.environ['ADDFUT_REGISTRY'] = str(rp)
            try:
                reg = {'ES': dict(sec_type='FUT'), 'ZN': dict(sec_type='FUT')}
                out['margin'] = TRN.book_margin({'ES': 1, 'ZN': 2}, reg)
            finally:
                for k, v in (('ADDFUT_MARGINS', keepm), ('ADDFUT_REGISTRY', keepr)):
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
                      partial={'ZN': 3})
            sp = Path(tempfile.mkdtemp(prefix='addfut-tr-')) / 'st.json'
            TRN._run_lots(br, lots, st, sp, lim, _Unp({k: 0.0 for k in legs_fut}), {},
                          lambda msg, cancel=True: (_ for _ in ()).throw(TRN.Incident(msg)))
            out['sold'] = sum(q for k, i, q in st['log'] if k == 'sell')
            out['partial_left'] = dict(st.get('partial', {}))
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
        elif case == 'план целых фьючерсов':
            out['lots'] = TRN.plan_lots(legs_fut, 10e6)
        elif case == 'план дробных долей фонда':
            out['lots'] = TRN.plan_lots(legs_frac, 10e6)
        elif case == 'дублированный источник':
            out['lots'] = TRN.plan_lots(legs_dup, 10e6)
        elif case == 'дробный фьючерс в плане':
            out['lots'] = TRN.plan_lots(legs_bad, 10e6)
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
            frac = 'ZN' if case == 'дробное исполнение фьючерса' else None
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


@tinv('остаток непарной дельты resume занимает лимит §8б',
      needs=lambda r: r['case'] == 'остаток непарной дельты при resume')
def _t_p2(r):
    """Семнадцатый круг, №2: обнулённый остаток освобождал лимит — новый проход строил
    разрыв поверх старого; с переносом остатка первый же шаг обязан упереться в §8б."""
    return r['raised'] and 'нет места под лимитом' in r['error']


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


@tinv('без файла замера — явные константы',
      needs=lambda r: r['case'] == 'замер отсутствует')
def _t_m5(r):
    return not r['raised'] and r.get('margin') == 34_800.0 + 2 * 2_160.0


@tinv('живой замер используется вместо констант',
      needs=lambda r: r['case'] == 'живой замер покрывает')
def _t_m6(r):
    return not r['raised'] and r.get('margin') == 40_000.0 + 2 * 2_500.0


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
              'штатный ролл не повторяется назавтра', 'пропущенный ролл только нога Б', 'смешанная книга: просрочена только Б')


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


@rlinv('пропущенный ролл навёрстывается и книгой из одной ноги Б',
       needs=lambda r: r['case'] == 'пропущенный ролл только нога Б')
def _rl4(r):
    return not r['raised'] and r.get('ser_b') == 'H27'


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
             'хвост месяца потерян', 'новых месяцев нет', 'уровни пересчитаны поставщиком',
             'сайдкар уровней отсутствует', 'сайдкар без столбца ноги',
             'сайдкар без общих месяцев', 'свежее закрытие завышено поставщиком',
             'свежее закрытие занижено за купонным потолком',
             'дивидендное окно в допуске', 'свежий знак в дивидендной зоне',
             'порча промежуточного месяца после пропуска',
             'уровневая база из одного месяца')


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
            try:
                # ПУТЬ КНИГИ ПЕРЕДАЁТСЯ ЯВНО: контур больше не выводит его из окружения,
                # и стенд обязан подавать тот же файл, который сам создал.
                dec, planned, _ = DL.run_session(br, m, dirpath=tmp, route=route,
                                                 capital=10_000_000.0,
                                                 closing_nav=10_000_000.0,
                                                 book_path=str(sp))
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
            if needs is not None and not needs(b, m, cap0, d, orders, u_e, u_b):
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
    sys.exit(0 if not (bad or sbad or abad or ibad or fbad or rbad or tbad or lbad
                       or gbad or fbad8) else 1)
