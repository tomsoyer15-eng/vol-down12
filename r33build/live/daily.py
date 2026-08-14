#!/usr/bin/env python3
"""Контур ежедневной торговли ADD-FUT ред. 33: расчёт целевой книги и заявки на сессию.

ЧТО ЭТО. Симулятор считает историю; переходный исполнитель переводит книгу между
маршрутами. Между ними не хватало главного — программы, которая КАЖДУЮ СЕССИЮ берёт
фактический капитал и цены, решает, торговать ли, и выдаёт нетто-заявку по каждой ноге.
Здесь она.

ПОЧЕМУ ОТДЕЛЬНАЯ РЕАЛИЗАЦИЯ, А НЕ ВЫЗОВ СИМУЛЯТОРА. Симулятор устроен вокруг цикла по
истории: капитал он считает сам из доходностей. В бою капитал приходит от брокера, а
доходности неизвестны. Поэтому решение сессии вынесено в чистую функцию step(), которой
капитал ПОДАЁТСЯ. Совпадение с симулятором не предполагается, а ДОКАЗЫВАЕТСЯ прогоном по
всей истории (replay_check.py): два независимых кода, сошедшихся на 63 годах до 1e-12.

ЧЕГО ЗДЕСЬ НЕТ. Связи с брокером. step() не знает ни о каком API: на вход — состояние и
рынок, на выход — заявки и запись журнала. Адаптер подключается снаружи и подменяем в тестах.
"""
import os
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
import math

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sim_v13 as S

CAP_LEV = 2.00                    # кап плеча §1
MIN_NLV_F = 3_000_000.0           # порог маршрута Ф §8 (решение заказчика 11.08.2026)

# Месяцы поставки квартального цикла и их биржевые коды.
DELIVERY = {3: 'H', 6: 'M', 9: 'U', 12: 'Z'}


def roll_date(year, month, holidays=()):
    """Дата ролла для месяца из КАЛЕНДАРЯ, а не из имеющихся данных.

    Штатная функция пакета выводит дату из индекса наблюдений и потому требует данных за
    следующий месяц: в бою будущих данных нет, и она НИКОГДА не скажет, что сегодня день
    ролла, — только задним числом. Здесь дата считается вперёд: последний рабочий день
    месяца минус три рабочих дня, с учётом переданных биржевых выходных.
    """
    import pandas as _pd
    days = _pd.bdate_range(_pd.Timestamp(year, month, 1),
                           _pd.Timestamp(year, month, 1) + _pd.offsets.MonthEnd(0))
    days = days[~days.isin(_pd.DatetimeIndex(holidays))]
    return days[-4] if len(days) >= 4 else None


def is_roll_day(dte, holidays=()):
    """Сегодня день ролла? Считается вперёд, работает в бою."""
    if dte.month not in (2, 5, 8, 11):
        return False
    d = roll_date(dte.year, dte.month, holidays)
    return d is not None and d.normalize() == dte.normalize()


def roll_passed_for(dte, holidays=()):
    """Нужен ли ДОПОЛНИТЕЛЬНЫЙ сдвиг серии при первом входе в книгу.

    ТОЛЬКО ВНУТРИ МЕСЯЦА РОЛЛА. Вне его first_tag уже учитывает прошедший переезд: в
    сентябре он сам даёт декабрьскую поставку. Прежняя редакция возвращала True и там,
    отчего серия уезжала на КВАРТАЛ ВПЕРЁД — с сентября по ноябрь книга открывалась бы в
    H27 вместо Z26.
    """
    if dte.month not in (2, 5, 8, 11):
        return False
    d = roll_date(dte.year, dte.month, holidays)
    return d is not None and dte > d


def first_tag(dte):
    """Метка серии для ПЕРВОГО входа в книгу: ближайшая поставка, не раньше текущего
    месяца; в месяц поставки не входим. Дальше метка НЕ пересчитывается из календаря."""
    m, y = dte.month, dte.year
    dm = ((m - 1) // 3 + 1) * 3
    if dm == m:
        dm += 3
    if dm > 12:
        dm -= 12; y += 1
    return f'{DELIVERY[dm]}{y % 100:02d}'


def next_tag(tag):
    """Следующая поставка того же квартального цикла."""
    m = {v: k for k, v in DELIVERY.items()}[tag[0]]
    y = int(tag[1:]) + 2000
    m += 3
    if m > 12:
        m -= 12; y += 1
    return f'{DELIVERY[m]}{y % 100:02d}'


def tag_month(tag):
    m = {v: k for k, v in DELIVERY.items()}[tag[0]]
    return m, int(tag[1:]) + 2000


# ---------------------------------------------------------------- праздники бирж
# ДАТА РОЛЛА СЧИТАЕТСЯ НАЗАД ОТ ПОСЛЕДНЕГО РАБОЧЕГО ДНЯ МЕСЯЦА, поэтому биржевой праздник
# в последнюю неделю месяца СДВИГАЕТ ролл. Проверено: в ноябре 2026 День благодарения
# (26.11) сдвигает ролл с 25 на 24 ноября. Без таблицы контур перенёс бы серию не в ту
# сессию — при полном молчании всех проверок, поскольку встроенный календарь считает
# просто понедельник–пятницу.
#
# ТАБЛИЦА — ВНЕШНЕЕ ЗНАНИЕ, А НЕ ВЫВОД. Её нельзя получить из данных, и она обязана
# сверяться с официальным календарём CME/CBOT при каждом продлении. Год без покрытия —
# ОТКАЗ, а не молчаливый возврат к пятидневке: молчание здесь равносильно неверной серии.
HOLIDAYS_CME = {
    2026: ('2026-01-01', '2026-01-19', '2026-02-16', '2026-04-03', '2026-05-25',
           '2026-06-19', '2026-07-03', '2026-09-07', '2026-11-26', '2026-12-25'),
    2027: ('2027-01-01', '2027-01-18', '2027-02-15', '2027-03-26', '2027-05-31',
           '2027-06-18', '2027-07-05', '2027-09-06', '2027-11-25', '2027-12-24'),
    2028: ('2028-01-17', '2028-02-21', '2028-04-14', '2028-05-29', '2028-06-19',
           '2028-07-04', '2028-09-04', '2028-11-23', '2028-12-25'),
}


def holidays_for(*years):
    """Праздники за указанные годы. Непокрытый год — исключение, а не пустой список."""
    import pandas as pd
    out = []
    for y in sorted(set(int(x) for x in years)):
        if y not in HOLIDAYS_CME:
            raise RuntimeError(
                f'праздничный календарь бирж не покрывает {y} год: даты ролла считались бы '
                f'по пятидневке и могли разойтись с биржей (проверено на ноябре 2026). '
                f'Продлить HOLIDAYS_CME по официальному календарю CME/CBOT')
        out.extend(pd.Timestamp(x) for x in HOLIDAYS_CME[y])
    return tuple(out)


def roll_deadline(tag, holidays=()):
    """День ролла ЦИКЛА ЭТОЙ СЕРИИ: за 3 сессии до конца месяца, предшествующего поставке."""
    m, y = tag_month(tag)
    rm, ry = (m - 1, y) if m > 1 else (12, y - 1)
    return roll_date(ry, rm, holidays)


def leg_roll_overdue(held, m):
    """Просрочен ли СРОК ролла ИМЕННО ЭТОЙ серии (тринадцатый круг, №1: срок — свойство
    серии, а не книги; общий флаг роллил исправную ногу на квартал вперёд)."""
    if held is None:
        return False
    hol = getattr(m, 'holidays', ()) or ()
    try:
        return m.date > roll_deadline(held, hol)
    except Exception:
        return False


def missed_roll_check(b, m):
    """Хотя бы одна нога просрочена (для предусловий стендов; решение внутри step — по
    каждой ноге отдельно)."""
    return leg_roll_overdue(b.ser_a, m) or leg_roll_overdue(b.ser_b, m)


def target_tag(held, dte, roll_today, roll_passed=False):
    """Какую серию держать после этой сессии.

    ПОЧЕМУ НЕ ИЗ КАЛЕНДАРЯ. Прежняя редакция вычисляла серию из даты каждый день, поэтому
    назавтра после ролла календарь снова указывал на СТАРУЮ поставку, и книга откатывалась
    в только что покинутый контракт — три ролла вместо одного и повторный вход в серию у
    самой поставки. Серия меняется ТОЛЬКО в день ролла и только вперёд.
    """
    if held is None:
        t = first_tag(dte)
        return next_tag(t) if (roll_today or roll_passed) else t
    if not roll_today:
        return held
    # СДВИГ ОТ УДЕРЖИВАЕМОЙ СЕРИИ, А НЕ ОТ КАЛЕНДАРЯ. Прежняя редакция брала
    # next_tag(first_tag(dte)) — и при ОТЛОЖЕННОМ ролле перепрыгивала через нужную поставку:
    # если августовский переход U26->Z26 не состоялся до сентября, first_tag(сентябрь) уже
    # равен Z26 (в месяц поставки не входим), а next_tag давал H27. Книга уходила в чужую
    # серию с другим базисом. Теперь серия сдвигается ровно на одну вперёд от удерживаемой
    # и дальше — только пока попадает в свой месяц поставки; пропуск при этом
    # самоисправляется, а лишнего квартала не набегает.
    t = next_tag(held)
    while delivery_risk(t, dte):
        t = next_tag(t)
    return t


def delivery_risk(tag, dte):
    """Мы в месяце поставки удерживаемой серии? §1 это запрещает. Возникает, если сессия
    ролла была пропущена (остановка системы), — состояние само по себе этого не покажет."""
    m, y = tag_month(tag)
    return (dte.year, dte.month) >= (y, m)


@dataclass(frozen=True)
class Book:
    """Состояние книги между сессиями. Переносится через перезапуск."""
    n_e: int = 0                  # контрактов ноги А во внутренней сетке (ES до 06.05.2019, далее MES)
    n_b: int = 0                  # контрактов ZN
    unit_is_mes: bool = False
    d_fix: float = 0.0            # дюрация, зафиксированная при последнем ролле/переключении ноги Б
    prev_close_lev: float = 0.0   # плечо фактического закрытия предыдущей сессии
    prev_st_eq: bool = None
    prev_st_bd: bool = None
    # СЕРИИ УДЕРЖИВАЕМЫХ КОНТРАКТОВ. Без них ролл существует только в модели: число
    # контрактов не меняется, заявок не возникает, и книга остаётся в старой серии —
    # для ZN это прямой путь в зону поставки.
    ser_a: str = None
    ser_b: str = None
    # ФИЗИЧЕСКОЕ ЧИСЛО ЦЕЛЫХ ES. Без него книга каждый раз приводится к канону n//10 ES +
    # n%10 MES, и переход через границу упаковки (9 -> 10 единиц сетки) требует продать
    # 9 MES и купить 1 ES — 19 MES-эквивалентов оборота ради изменения экспозиции на одну
    # единицу, тогда как симулятор списывает издержки за одну. Храня фактическую упаковку,
    # контур меняет книгу МИНИМАЛЬНО и совпадает с моделью издержек; канонизация делается
    # только на ролле, где книга закрывается целиком и переупаковка бесплатна.
    es_held: int = None
    # ОТЛОЖЕННЫЙ РОЛЛ. День ролла один; если перенос был откачен из-за отказа брокера,
    # без этого признака он не повторился бы никогда, и книга дожила бы до месяца поставки.
    roll_pending: bool = False
    # ДАТА ПОСЛЕДНЕЙ ЗАВЕРШЁННОЙ СЕССИИ. Без неё контур не отличает повторный запуск за тот
    # же день от нового и допускает торговлю по устаревшим данным задним числом.
    last_session: str = None
    # ЗАМЫКАНИЕ ПРЕДВАРИТЕЛЬНОЕ, ПОКА НЕ ДОКАЗАНО ОБРАТНОЕ. Плечо закрытия — единственный
    # источник триггера капа §1, и посчитать его можно только ПОСЛЕ закрытия: по фактическим
    # ценам закрытия и фактическому NLV. В момент подачи заявки этих величин не существует.
    close_provisional: bool = True


@dataclass(frozen=True)
class Market:
    """Рыночные данные сессии. Цены — ЗАКРЫТИЯ ПРЕДЫДУЩЕЙ СЕССИИ (конвенция §2: сигнал на
    закрытии T, сделка в сессию T+1), кроме close_* для расчёта плеча на конец дня."""
    date: object
    px_eq_prev: float             # закрытие ноги А предыдущей сессии (индексный пункт)
    dref_prev: float              # дюрация референсного десятилетнего пара, предыдущая сессия
    dref_today: float
    px_eq_today: float
    roll_today: bool
    st_eq: bool                   # состояние сигнала ноги А на эту сессию
    st_bd: bool
    # Прошёл ли ролл текущего квартального цикла. Нужен ТОЛЬКО при первом входе в книгу:
    # календарь сам по себе не знает, состоялся ли уже переезд, и свежая книга, открытая в
    # день ролла или после него, вошла бы в уходящую серию.
    roll_passed: bool = False
    # Праздники биржи. Пустой кортеж означает «календарь не подан», и встроенная сверка
    # тогда считает по пятидневке — она остаётся СВЕРЯЮЩЕЙ, а решение задаёт roll_today.
    holidays: tuple = ()
    # Биржевые праздники для сверки встроенного календаря. Пусто — сверка приблизительная.
    holidays: tuple = ()


@dataclass
class Decision:
    """Что делать в эту сессию и почему. Журналируется целиком."""
    date: object
    orders: dict = field(default_factory=dict)     # нога -> изменение в контрактах (нетто)
    book_after: Book = None
    capital_after_costs: float = 0.0
    exposure: dict = field(default_factory=dict)
    leverage: float = 0.0
    roll_pairs: list = field(default_factory=list)  # перенос серий: закрыть старую / открыть новую
    reasons: list = field(default_factory=list)    # что вызвало сделку
    refusals: list = field(default_factory=list)   # почему заявка НЕ подаётся
    cap_correction: bool = False
    cap_before: tuple = None      # книга ДО обрезки капом: без неё глубину среза не проверить
    cushion: float = float('inf')                  # маршрут Е: запас к поддерживающему требованию
    daily_costs: dict = field(default_factory=dict)

    @property
    def trade(self):
        """Есть ли что подавать брокеру. РОЛЛ УЧИТЫВАЕТСЯ: при чистом переносе серии число
        контрактов не меняется и orders пуст, но две заявки подать обязаны — иначе книга
        останется в старой серии, а учёт будет считать её перенесённой."""
        # НЕ завязано на refusals: step() уже оставил только разрешённое (сокращение риска
        # и перенос серии), а полный запрет выражается пустыми orders и roll_pairs. Прежняя
        # редакция при отказе НЕ подавала спасительных заявок, но состояние сохраняла как
        # исполненное — брокер оставался с опасной позицией, а книга считала её закрытой.
        return bool(self.orders or self.roll_pairs)


def units(book, m):
    """Контрактный эквивалент ноги в долларах по ценам предыдущей сессии."""
    mult = S.ES_MULT / 10 if book.unit_is_mes else S.ES_MULT
    u_e = mult * m.px_eq_prev
    u_b = (S.ZN_MODEL_PX_EQ * S.CTD_RATIO * book.d_fix * 1e-4) / (m.dref_prev * 1e-4)
    return u_e, u_b


ROUTES = ('F', 'E')


def validate_inputs(capital, *values, names=()):
    """Отказ на нечисловых, нулевых и отрицательных величинах.

    Отрицательная цена прежде не вызывала ни отказа, ни исключения: цель делилась на
    отрицательный шаг, и книга становилась КОРОТКОЙ — стратегия, не предусмотренная §1
    вовсе. Ноль давал деление на ноль, NaN — исключение при округлении; ни то, ни другое
    не отличалось от обычной ошибки и не называло причину.
    """
    out = []
    if not (isinstance(capital, (int, float)) and math.isfinite(capital) and capital > 0):
        out.append(f'капитал {capital!r} не является положительным конечным числом')
    for v, nm in zip(values, names or [f'величина {i}' for i in range(len(values))]):
        if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0):
            out.append(f'{nm} {v!r} не является положительным конечным числом')
    return out


def guards(capital, u_e, u_b, band, route='F', paper=False):
    """Отказы §8: порог размера счёта и неумещающийся в полосу шаг контракта.
    Проверка на ФАКТИЧЕСКИХ ценах дня, а не на константах."""
    out = []
    if route not in ROUTES:
        # ОПЕЧАТКА В ИМЕНИ МАРШРУТА ОБХОДИЛА ПОРОГ: сравнение шло со строкой 'F', и любое
        # другое написание — 'f', 'Ф', 'FUT' — молча снимало ограничение §8.
        return [f'неизвестный маршрут {route!r}; допустимы только {ROUTES}']
    if route == 'F' and capital < MIN_NLV_F:
        # ИСКЛЮЧЕНИЕ ДЛЯ БУМАЖНОГО ЭТАПА. Порог §8 защищает РЕАЛЬНЫЙ капитал от книги,
        # которую нельзя удержать в полосе; на бумажном счёте капитала под риском нет, а
        # механический пол (половина шага контракта = полоса) заметно ниже. Исключение
        # ЯВНОЕ: оно требует признака paper и попадает в причины решения, поэтому по
        # журналу видно, что порог был обойдён сознательно.
        if paper:
            out_note = (f'БУМАЖНЫЙ ЭТАП: порог §8 ({MIN_NLV_F:,.0f}) не применён при NLV '
                        f'{capital:,.0f}; механический пол соблюдён')
            globals().setdefault('_PAPER_NOTES', []).append(out_note)
        else:
            out.append(f'NLV {capital:,.0f} ниже порога маршрута Ф {MIN_NLV_F:,.0f} (§8)')
    for nm, u in (('А', u_e), ('Б', u_b)):
        if u / 2 > band * capital:
            out.append(f'половина шага ноги {nm} ({u/2:,.0f}) превышает полосу '
                       f'({band*capital:,.0f}) — книга неисполнима (§8)')
    return out


def step(book, m, capital, band=None, cap=CAP_LEV, route='F', check_guards=True,
         paper=False):
    """Решение одной сессии. Возвращает Decision. Капитал ПОДАЁТСЯ (в бою — NLV брокера).

    Порядок в точности повторяет нормативный §1: конверсия единицы ES→MES без сделки →
    фиксация D_fix при ролле или переключении ноги Б → штатная логика по полосе →
    кап-блок односторонним аллокатором → нетто-ордер → ролловая издержка → плечо закрытия.
    """
    band = S.BAND_OP if band is None else band
    b = book
    if (not b.unit_is_mes) and m.date >= S.MES_START:
        b = replace(b, n_e=b.n_e * 10, unit_is_mes=True)   # конверсия единицы, не сделка

    # ПРИЗНАК РОЛЛА ОСТАЁТСЯ ЗА ВЫЗЫВАЮЩИМ КОДОМ, но сверяется со встроенным календарём.
    # Подменять его нельзя: встроенный календарь считает по рабочим дням и НЕ ЗНАЕТ
    # биржевых праздников, поэтому на истории расходится с фактическими торговыми
    # сессиями. Кто вызывает — тот и обязан знать календарь биржи; наша проверка лишь
    # выявляет расхождение и записывает его в причины решения.
    cal_roll = is_roll_day(m.date, getattr(m, 'holidays', ()) or ())
    roll_now = m.roll_today or b.roll_pending
    # ПРОПУЩЕННЫЙ ДЕНЬ РОЛЛА НАВЁРСТЫВАЕТСЯ (девятая рецензия, №6). Если день ролла целиком
    # пропущен (остановка машины), назавтра roll_today уже ложен, а прежний код держал
    # старую серию до месяца поставки и получал лишь отказ. Признак: ролл цикла пройден, а
    # удерживаемая серия всё ещё та, которую календарь велел покинуть.
    # ПО КАЖДОЙ НОГЕ (тринадцатый круг, №1): просрочка ноги Б не повод роллить исправную
    # ногу А на квартал вперёд — у каждой серии свой срок.
    missed_a = (not roll_now) and leg_roll_overdue(b.ser_a, m)
    missed_b = (not roll_now) and leg_roll_overdue(b.ser_b, m)
    missed_roll = missed_a or missed_b
    roll_a = roll_now or missed_a
    roll_b = roll_now or missed_b
    roll_any = roll_now or missed_roll
    eq_switch = (m.st_eq != b.prev_st_eq)
    bd_switch = (m.st_bd != b.prev_st_bd)
    # d_fix — свойство НОГИ Б (четырнадцатый круг, №4; уточнение пятнадцатого, №1):
    # перефиксация — роллом ноги Б ИЛИ переключением её сигнала (bd_switch), В ТОЧНОСТИ как
    # в замороженном движке (sim_v164: «if roll_today or bd_switch»). Формулировка
    # четырнадцатого круга «только роллом» была неточной: включение Б после паузы обязано
    # брать СВЕЖУЮ дюрацию — старая d_fix могла устареть на годы. Навёрстывание одной лишь
    # ноги А единицу ZN по-прежнему не трогает.
    if roll_b or bd_switch:
        b = replace(b, d_fix=m.dref_prev)

    u_e, u_b = units(b, m)
    e = float(capital)
    n0_e, n0_b = b.n_e, b.n_b
    n_e, n_b = n0_e, n0_b

    d = Decision(date=m.date, book_after=b, capital_after_costs=e)
    if missed_roll:
        d.reasons.append('день ролла был пропущен — перенос серии выполняется немедленно')
    # ЖУРНАЛ ГОВОРИТ ТО, ЧТО ПРОИСХОДИТ НА САМОМ ДЕЛЕ. Прежняя запись утверждала, что
    # «принято решение по КАЛЕНДАРЮ», тогда как решает roll_now = признак или отложенный
    # ролл, а встроенный календарь на него не влияет вовсе (он не знает биржевых праздников
    # и потому оставлен СВЕРЯЮЩИМ — см. пояснение к is_roll_day). Ложный аудиторский след
    # хуже отсутствия следа: по нему нельзя восстановить, почему перенос не состоялся.
    if m.roll_today != cal_roll:
        d.reasons.append(
            f'признак ролла ({m.roll_today}) расходится со встроенным календарём '
            f'({cal_roll}); решение принято ПО ПРИЗНАКУ, календарь — только сверка, '
            f'он не знает биржевых праздников. Проверить праздничный календарь CME/CBOT')
    # СОГЛАСОВАННОСТЬ ВХОДНОЙ КНИГИ. Повреждённое или подменённое состояние может нести
    # упаковку, невозможную физически (целых ES больше, чем единиц сетки). Прежде такая
    # книга проходила насквозь и порождала отрицательное число MES — скрытую короткую
    # позицию по младшему контракту.
    if b.unit_is_mes and b.es_held is not None and not (0 <= 10 * b.es_held <= max(b.n_e, 0)):
        d0 = Decision(date=m.date, book_after=book,
                      capital_after_costs=float(capital) if isinstance(capital, (int, float)) else 0.0)
        d0.refusals = [f'состояние несогласовано: целых ES {b.es_held} при сетке {b.n_e} '
                       f'единиц — книга физически невозможна, требуется ручной разбор']
        return d0
    bad = validate_inputs(capital, m.px_eq_prev, m.dref_prev, m.dref_today, u_e, u_b,
                          names=('капитал', 'цена ноги А', 'дюрация вчера', 'дюрация сегодня',
                                 'шаг ноги А', 'шаг ноги Б')[1:])
    if bad:
        d.refusals = bad
        d.book_after = book
        d.capital_after_costs = float(capital) if isinstance(capital, (int, float)) else 0.0
        return d
    if check_guards:
        _PAPER_NOTES.clear() if '_PAPER_NOTES' in globals() else None
        d.refusals = guards(e, u_e, u_b, band, route, paper=paper)
        for n in globals().get('_PAPER_NOTES', []):
            d.reasons.append(n)

    def _size(lim_e=None, lim_b=None):
        """ОДИН проход размеров: полоса -> кап §1 -> стоимость ролла. lim_e/lim_b — потолки
        количеств при запрете наращивания (шестнадцатый круг, №1); None — без ограничения.
        Проход без потолков воспроизводит замороженную арифметику байт в байт — регрессия
        1e-12 держится на нём."""
        e = float(capital)
        n_e, n_b = n0_e, n0_b
        reasons = []
        capf = dict(corr=False, before=None)
        tgt_e = 1.0 * m.st_eq * e
        tgt_b = 1.0 * m.st_bd * e
        exp_e, exp_b = n_e * u_e, n_b * u_b

        if eq_switch or abs(tgt_e - exp_e) > band * e:
            new = round(tgt_e / u_e)
            if lim_e is not None and new > lim_e:
                new = lim_e
            if new != n_e:
                e -= S.COST * abs(new - n_e) * u_e
                reasons.append('смена состояния ноги А' if eq_switch else 'нога А вне полосы')
                n_e = new
        if bd_switch or roll_b or abs(tgt_b - exp_b) > band * e:
            new = round(tgt_b / u_b)
            if lim_b is not None and new > lim_b:
                new = lim_b
            if new != n_b:
                e -= S.COST * abs(new - n_b) * u_b
                reasons.append('смена состояния ноги Б' if bd_switch else
                               ('ролл' if m.roll_today else 'нога Б вне полосы'))
                n_b = new

        if cap is not None:
            def e_after(ne, nb):
                ea = e - S.COST * ((abs(ne - n0_e) - abs(n_e - n0_e)) * u_e +
                                   (abs(nb - n0_b) - abs(n_b - n0_b)) * u_b)
                if roll_any:
                    # ПО РОЛЛЯЩИМСЯ НОГАМ (пятнадцатый круг, №2): просрочка одной ноги не
                    # списывает стоимость переноса с исправной.
                    ea -= S.ROLL_BP * ((ne * u_e if roll_a else 0.0) +
                                       (nb * u_b if roll_b else 0.0))
                return ea

            def breach(ne, nb):
                return (ne * u_e + nb * u_b) > cap * e_after(ne, nb)

            breach_at_open = b.prev_st_eq is not None and b.prev_close_lev > cap
            if breach_at_open or breach(n_e, n_b):
                pe0, pb0 = n_e, n_b
                ne = min(n_e, math.floor(tgt_e / u_e)) if breach_at_open else math.floor(tgt_e / u_e)
                nb = min(n_b, math.floor(tgt_b / u_b)) if breach_at_open else math.floor(tgt_b / u_b)
                if lim_e is not None and ne > lim_e:
                    ne = lim_e
                if lim_b is not None and nb > lim_b:
                    nb = lim_b
                while breach(ne, nb) and (ne > 0 or nb > 0):
                    if ne > 0 and (u_e >= u_b or nb == 0):
                        ne -= 1
                    else:
                        nb -= 1
                if (ne, nb) != (pe0, pb0):
                    e -= S.COST * ((abs(ne - n0_e) - abs(n_e - n0_e)) * u_e +
                                   (abs(nb - n0_b) - abs(n_b - n0_b)) * u_b)
                    capf = dict(corr=True, before=(pe0, pb0))
                    n_e, n_b = ne, nb
                    reasons.append('кап плеча §1')

        exp_e, exp_b = n_e * u_e, n_b * u_b
        # ПО roll_now, А НЕ ПО КАЛЕНДАРНОМУ ФЛАГУ. Отложенный ролл (roll_pending) переносил
        # серию, но стоимость переноса НЕ списывалась: капитал и доступное плечо завышались,
        # и пограничная книга получала на контракт больше положенного.
        # И ПО КАЖДОЙ НОГЕ ОТДЕЛЬНО (пятнадцатый круг, №2): при просрочке одной лишь Б
        # прежний код списывал ролл и с исправной А — фиктивно занижая капитал и цель.
        # В квартальный ролл roll_a=roll_b=True, поэтому с движком расхождения нет.
        if roll_any:
            e -= S.ROLL_BP * ((exp_e if roll_a else 0.0) + (exp_b if roll_b else 0.0))
        return n_e, n_b, e, exp_e, exp_b, reasons, capf

    n_e, n_b, e, exp_e, exp_b, size_reasons, capf = _size()

    # Плечо закрытия ЗДЕСЬ НЕ СЧИТАЕТСЯ: §1 определяет триггер капа через плечо
    # ФАКТИЧЕСКОГО ЗАКРЫТИЯ, то есть от NAV на закрытии, а расчётчик в момент подачи
    # заявки знает лишь капитал после собственных расходов — рыночный P&L дня ещё не
    # случился. Замыкание сессии выполняет close_out() по факту закрытия.
    # --- серии: какую держим после этой сессии ---
    tgt_a = target_tag(b.ser_a, m.date, roll_a, m.roll_passed)
    tgt_b = target_tag(b.ser_b, m.date, roll_b, m.roll_passed)

    def _series_pairs(fin_e, fin_b):
        rp, rs = [], []
        for leg, held, want, n_old, n_new in (('А', b.ser_a, tgt_a, n0_e, fin_e),
                                              ('Б', b.ser_b, tgt_b, n0_b, fin_b)):
            if held is not None and held != want and n_old:
                rp.append(dict(leg=leg, close=(held, -n_old), open=(want, n_new)))
                if 'ролл серии' not in rs:
                    rs.append('ролл серии')
        return rp, rs

    # Поставочный отказ не зависит от количеств — считается РОВНО ОДИН РАЗ, до возможного
    # пересчёта размеров.
    for leg, held in (('А', b.ser_a), ('Б', b.ser_b)):
        _rolling_this_leg = roll_now or (missed_a if leg == 'А' else missed_b)
        if held is not None and delivery_risk(held, m.date) and not _rolling_this_leg:
            d.refusals.append(f'нога {leg}: удерживается серия {held}, наступил её месяц '
                              f'поставки — сессия ролла пропущена, требуется ручной разбор (§1)')

    if d.refusals and (n_e > n0_e or n_b > n0_b):
        # ОТКАЗ ЗАПРЕЩАЕТ НАРАЩИВАТЬ РИСК, А НЕ СНИЖАТЬ ЕГО: пропускаются перенос серии
        # (экспозиция не растёт, а невыход означает поставку) и любое сокращение книги.
        # РЕШЕНИЕ ПЕРЕСЧИТЫВАЕТСЯ С ПОТОЛКАМИ n0 (шестнадцатый круг, №1 и №6): прежний
        # ПОЗДНИЙ фильтр откатывал запрещённое наращивание ПОСЛЕ кап-аллокатора, и срез
        # исправной ноги, вызванный запрещённой, оставался — продажа без нормативной
        # причины; капитал сохранял комиссии заявок, которые брокеру не уходили. Теперь
        # запрет действует с первого шага размеров, и стоимость, плечо и кап считаются
        # по исполняемой книге.
        n_e, n_b, e, exp_e, exp_b, size_reasons, capf = _size(lim_e=n0_e, lim_b=n0_b)

    d.reasons.extend(size_reasons)
    if capf['corr']:
        d.cap_before = capf['before']
        d.cap_correction = True
    d.roll_pairs, _rp_reasons = _series_pairs(n_e, n_b)
    d.reasons.extend(_rp_reasons)

    if n_e != n0_e:
        d.orders['А'] = n_e - n0_e
    if n_b != n0_b:
        d.orders['Б'] = n_b - n0_b
    es_after = pack_es(b.es_held, n_e, b.unit_is_mes, roll_a)
    d.book_after = replace(b, n_e=n_e, n_b=n_b, prev_close_lev=float('nan'),
                           prev_st_eq=m.st_eq, prev_st_bd=m.st_bd,
                           ser_a=tgt_a, ser_b=tgt_b, es_held=es_after, roll_pending=False)
    d.capital_after_costs = e
    d.exposure = {'А': exp_e, 'Б': exp_b}
    d.leverage = (exp_e + exp_b) / e if e else 0.0
    if d.refusals:
        d.reasons.append('ЧАСТИЧНОЕ ИСПОЛНЕНИЕ ПРИ ОТКАЗЕ §8: пропущены только '
                         'сокращение риска и перенос серии')
    return d


def close_out(book, px_eq_close, dref_close, nav_close):
    """Замыкание сессии по факту закрытия: сохранить плечо, с которым книга ушла в ночь.

    Это ЕДИНСТВЕННОЕ место, где рождается триггер капа следующей сессии (§1). В бою
    nav_close — фактический NLV счёта на закрытии, а не расчётная величина.
    """
    mult = S.ES_MULT / 10 if book.unit_is_mes else S.ES_MULT
    u_b = (S.ZN_MODEL_PX_EQ * S.CTD_RATIO * book.d_fix * 1e-4) / (dref_close * 1e-4)
    lev = (book.n_e * mult * px_eq_close + book.n_b * u_b) / nav_close
    return replace(book, prev_close_lev=lev)


def finalize_close(book, px_eq_close, dref_close, nav_close, route='F', px_bd_close=None):
    """Окончательное замыкание сессии ПО ФАКТУ ЗАКРЫТИЯ. Отдельный шаг и отдельное время.

    Внутри торгового запуска цены закрытия ещё не существуют, поэтому там книга замыкается
    ПРЕДВАРИТЕЛЬНО и помечается как таковая. Настоящий триггер капа §1 рождается здесь.
    """
    closed = (close_out_e(book, px_eq_close, px_bd_close, nav_close) if route == 'E'
              else close_out(book, px_eq_close, dref_close, nav_close))
    return replace(closed, close_provisional=False)


def pack_es(es_held, n_grid, unit_is_mes, roll_today):
    """Сколько целых ES держать. На ролле — канон; иначе минимальное изменение упаковки."""
    if not unit_is_mes:
        return None
    if roll_today or es_held is None:
        return n_grid // 10
    mes = n_grid - 10 * es_held
    if 0 <= mes <= 19:
        return es_held
    return n_grid // 10 if mes < 0 else es_held + (mes - 9) // 10


def physical_book(book):
    """Книга в терминах инструментов: {серия: количество}. Единственное место, где
    внутренняя сетка превращается в то, что лежит у брокера."""
    out = {}
    if book.n_b and book.ser_b:
        out[f'ZN{book.ser_b}'] = book.n_b
    if book.n_e and book.ser_a:
        if not book.unit_is_mes:
            out[f'ES{book.ser_a}'] = book.n_e
        else:
            es = book.es_held if book.es_held is not None else book.n_e // 10
            mes = book.n_e - 10 * es
            if es:
                out[f'ES{book.ser_a}'] = es
            if mes:
                out[f'MES{book.ser_a}'] = mes
    return out


def orders_from_books(before, after):
    """Заявки как ЧИСТАЯ РАЗНОСТЬ двух физических книг.

    ПОЧЕМУ ТАК. Прежде заявки собирались из трёх источников — orders, roll_pairs и
    book_after, — которые могли разойтись: фильтр отказа менял книгу, но не пересчитывал
    roll_pairs, и брокеру уходило открытие на дофильтровочное количество. Три починки
    подряд дали три новых дефекта этого класса. Разность двух книг рассинхронизироваться
    не может по построению: ролл выражается сам собой (старая серия уходит в ноль, новая
    получает целевое количество), как и любая смена упаковки.
    """
    a, b = physical_book(before), physical_book(after)
    out = []
    for inst in sorted(set(a) | set(b)):
        d = b.get(inst, 0) - a.get(inst, 0)
        if d:
            out.append((inst, d))
    # закрытия вперёд: освободить маржу до наращивания
    return sorted(out, key=lambda x: (x[1] > 0, x[0]))


def book_to_orders(dec, book_before):
    """Совместимость: заявки сессии как разность книг до и после."""
    return orders_from_books(book_before, dec.book_after)


# ============================================================================
# МАРШРУТ Е: ежедневный контур для UCITS-ETF с маржинальным займом.
#
# Прежде route='E' влиял только на проверку порога, а сама книга всё равно
# считалась во фьючерсах ES/MES/ZN. После перехода в Е ежедневный процесс
# продолжил бы строить фьючерсную книгу — то есть торговать не тем
# инструментом и не по тому мандату. Здесь маршрут Е получает собственное
# решение сессии, симметричное фьючерсному: доли CSPX и CBU0, фактический
# дебет, кэш 5%, тот же кап 2,00 и тот же односторонний аллокатор.
# ============================================================================

TER_E = 0.0007
DRAG_E = 0.0025               # ред. 33: норматив повышен с 0,20%
BM_BASIS_E = 0.0010
TIERS_E = [(100_000.0, 0.0150), (900_000.0, 0.0100), (49_000_000.0, 0.0075),
           (float('inf'), 0.0050)]
MAINT_E = 0.25                # нормативное поддерживающее требование
O3E_MIN = 1.40                # §6: ниже — сокращение до L=1 в ту же сессию


def o3e_reduce(capital, m, p_e, p_b, n_eq, n_bd, n0_eq, n0_bd, share):
    """Аварийное сокращение О-3-Е до L=1 — ЦЕЛЬ ОТ КАПИТАЛА ПОСЛЕ ФАКТИЧЕСКИХ РАСХОДОВ
    (семнадцатый круг, №16): сокращение само стоит комиссий, и цель от старого капитала
    оставляла L≈1,0005 при правиле «не выше 1». Неподвижная точка за пару итераций:
    оборот меняет капитал, капитал меняет цель. Ограничители прежние — не выше капнутого
    и не выше исходного (аварийный критерий не открывает позицию)."""
    e = float(capital) - S.COST * (abs(n_eq - n0_eq) * p_e + abs(n_bd - n0_bd) * p_b)
    for _ in range(4):
        ne2 = min(n_eq, n0_eq, math.floor(share * (1.0 * m.st_eq * e) / p_e))
        nb2 = min(n_bd, n0_bd, math.floor(share * (1.0 * m.st_bd * e) / p_b))
        e2 = float(capital) - S.COST * (abs(ne2 - n0_eq) * p_e + abs(nb2 - n0_bd) * p_b)
        if (ne2, nb2) == (n_eq, n_bd) and abs(e2 - e) < 1e-9:
            return n_eq, n_bd, e
        n_eq, n_bd, e = ne2, nb2, e2
    return n_eq, n_bd, e


@dataclass(frozen=True)
class BookE:
    """Состояние книги маршрута Е. Доли, а не контракты; серий и роллов нет.

    roll_pending здесь — НЕ про роллы Е (их не существует), а про незавершённый перенос
    серии маршрута Ф, переживающий уход в Е и возврат: обязательство «доделать ролл»
    относится к СТРАТЕГИИ, а не к маршруту, и обязано пережить оба перехода Ф->Е->Ф.
    Прежде ветка Е его выбрасывала, и при возвращении в Ф система не знала, что перенос
    не был завершён."""
    n_eq: int = 0
    n_bd: int = 0
    roll_pending: bool = False
    prev_close_lev: float = 0.0
    prev_st_eq: bool = None
    prev_st_bd: bool = None
    last_session: str = None
    # ЗАМЫКАНИЕ ПРЕДВАРИТЕЛЬНОЕ, ПОКА НЕ ДОКАЗАНО ОБРАТНОЕ. Плечо закрытия — единственный
    # источник триггера капа §1, и посчитать его можно только ПОСЛЕ закрытия: по фактическим
    # ценам закрытия и фактическому NLV. В момент подачи заявки этих величин не существует.
    close_provisional: bool = True


@dataclass(frozen=True)
class MarketE:
    date: object
    px_eq_prev: float          # цена фондовой ноги предыдущей сессии
    px_bd_prev: float
    px_eq_today: float
    px_bd_today: float
    st_eq: bool
    st_bd: bool


def loan_spread_amt(debit):
    """Годовая долларовая стоимость спреда займа по лесенке плюс трение бенчмарка."""
    rest = debit; amt = 0.0
    for size, sp in TIERS_E:
        take = min(rest, size)
        amt += take * sp; rest -= take
        if rest <= 0:
            break
    return amt + debit * BM_BASIS_E


def step_e(book, m, capital, band=None, cap=CAP_LEV, drag=DRAG_E, check_o3e=True,
           maint=MAINT_E, live_cushion=None, paper=False):
    """Решение одной сессии маршрута Е. Капитал подаётся (в бою — NLV брокера).

    ПРИЗНАК БУМАЖНОГО ЭТАПА ПРИНИМАЕТСЯ, ХОТЯ ПРАВИЛ НЕ МЕНЯЕТ. Порог §8 в 3 млн относится
    к маршруту Ф; у маршрута Е его в коде нет, и исключать нечего. Но запуск сессии подаёт
    этот признак ОБОИМ маршрутам, и прежде маршрут Е падал на нём с TypeError — то есть
    живая сессия маршрута Е не могла состояться вовсе. Признак записывается в причины,
    чтобы по журналу было видно, на каком этапе сделана запись.
    """
    band = S.BAND_OP if band is None else band
    paper_note = ('БУМАЖНЫЙ ЭТАП: порог §8 относится к маршруту Ф и к маршруту Е не '
                  'применяется') if paper else None
    bad = validate_inputs(capital, m.px_eq_prev, m.px_bd_prev,
                          names=('цена фондовой ноги', 'цена облигационной ноги'))
    if bad:
        d0 = Decision(date=m.date, book_after=book,
                      capital_after_costs=float(capital) if isinstance(capital, (int, float)) else 0.0)
        d0.refusals = bad
        return d0
    e = float(capital)
    n0_eq, n0_bd = book.n_eq, book.n_bd
    n_eq, n_bd = n0_eq, n0_bd
    p_e, p_b = m.px_eq_prev, m.px_bd_prev
    d = Decision(date=m.date, book_after=book, capital_after_costs=e)
    if paper_note:
        d.reasons.append(paper_note)

    eq_switch = (m.st_eq != book.prev_st_eq)
    bd_switch = (m.st_bd != book.prev_st_bd)
    tgt_e = 1.0 * m.st_eq * e
    tgt_b = 1.0 * m.st_bd * e
    if eq_switch or abs(tgt_e - n_eq * p_e) > band * e:
        new = round(tgt_e / p_e)
        if new != n_eq:
            e -= S.COST * abs(new - n_eq) * p_e
            d.reasons.append('смена состояния ноги А' if eq_switch else 'нога А вне полосы')
            n_eq = new
    if bd_switch or abs(tgt_b - n_bd * p_b) > band * e:
        new = round(tgt_b / p_b)
        if new != n_bd:
            e -= S.COST * abs(new - n_bd) * p_b
            d.reasons.append('смена состояния ноги Б' if bd_switch else 'нога Б вне полосы')
            n_bd = new

    if cap is not None:
        def e_after(ne, nb):
            ea = e - S.COST * ((abs(ne - n0_eq) - abs(n_eq - n0_eq)) * p_e +
                               (abs(nb - n0_bd) - abs(n_bd - n0_bd)) * p_b)
            P = ne * p_e + nb * p_b
            ea -= (TER_E * P + drag * ne * p_e +
                   loan_spread_amt(max(0.0, P + 0.05 * ea - ea))) / 252.0
            return ea

        def breach(ne, nb):
            return (ne * p_e + nb * p_b) > cap * e_after(ne, nb)

        breach_at_open = book.prev_st_eq is not None and book.prev_close_lev > cap
        if breach_at_open or breach(n_eq, n_bd):
            pe0, pb0 = n_eq, n_bd
            ne = min(n_eq, math.floor(tgt_e / p_e)) if breach_at_open else math.floor(tgt_e / p_e)
            nb = min(n_bd, math.floor(tgt_b / p_b)) if breach_at_open else math.floor(tgt_b / p_b)
            while breach(ne, nb) and (ne > 0 or nb > 0):
                if ne > 0 and (p_e >= p_b or nb == 0):
                    ne -= 1
                else:
                    nb -= 1
            if (ne, nb) != (pe0, pb0):
                e -= S.COST * ((abs(ne - n0_eq) - abs(n_eq - n0_eq)) * p_e +
                               (abs(nb - n0_bd) - abs(n_bd - n0_bd)) * p_b)
                d.cap_before = (pe0, pb0)
                n_eq, n_bd = ne, nb
                d.cap_correction = True
                d.reasons.append('кап плеча §1')

    P = n_eq * p_e + n_bd * p_b
    d.exposure = {'А': n_eq * p_e, 'Б': n_bd * p_b}
    d.leverage = P / e if e else 0.0
    # О-3-Е — КРИТЕРИЙ ПО ЖИВОМУ ЗНАЧЕНИЮ БРОКЕРА, а не по нашему расчёту. После кап-блока
    # плечо всегда ≤ 2,00, поэтому расчётный запас не опускается ниже 1,43 даже при
    # house-требовании 35%: сработать он может только на величине, пришедшей от брокера
    # (она включает фактическое требование, начисления и внутридневное движение).
    # Расчётная величина остаётся контрольной прокси, как и сказано в §8а.
    d.cushion = (1.0 / (maint * d.leverage) if d.leverage > 0 else float('inf')) \
        if live_cushion is None else float(live_cushion)
    if check_o3e and d.cushion < O3E_MIN:
        # §6, О-3-Е: сокращение до L=1 В ТУ ЖЕ СЕССИЮ. Прежде критерий существовал только
        # на бумаге: живого маржинального контура не было вовсе.
        d.reasons.append(f'О-3-Е: запас {d.cushion:.2f}× ниже {O3E_MIN} — сокращение до L=1')
        # НЕ ВЫШЕ ИСХОДНОГО. min() от целевой половины сам по себе мог оказаться БОЛЬШЕ
        # того, что было в книге, и аварийный критерий открыл бы новую позицию.
        # L=1 означает СУММАРНОЕ плечо 1,0, а не половину каждой цели: при выключенной ноге
        # половина цели давала бы ноль по ней и полное плечо по другой.
        share = 1.0 / max(float(m.st_eq) + float(m.st_bd), 1.0)
        n_eq, n_bd, e = o3e_reduce(capital, m, p_e, p_b, n_eq, n_bd, n0_eq, n0_bd, share)
        P = n_eq * p_e + n_bd * p_b
        d.exposure = {'А': n_eq * p_e, 'Б': n_bd * p_b}
        d.leverage = P / e if e else 0.0
        d.cushion = 1.0 / (maint * d.leverage) if d.leverage > 0 else float('inf')

    if n_eq != n0_eq:
        d.orders['CSPX'] = n_eq - n0_eq
    if n_bd != n0_bd:
        d.orders['CBU0'] = n_bd - n0_bd
    # ЗАЁМ И СУТОЧНЫЕ РАСХОДЫ — ОТ ФИНАЛЬНОЙ КНИГИ (семнадцатый круг, №16): debit,
    # посчитанный до аварийного сокращения, относил стоимость займа к уже несуществующей
    # позиции, а журнал уносил её в сверку §7.
    debit = max(0.0, P + 0.05 * e - e)
    d.daily_costs = dict(ter=TER_E * P / 252.0, drag=drag * n_eq * p_e / 252.0,
                         loan=loan_spread_amt(debit) / 252.0, debit=debit)
    d.capital_after_costs = e
    d.book_after = replace(book, n_eq=n_eq, n_bd=n_bd, prev_close_lev=float('nan'),
                           prev_st_eq=m.st_eq, prev_st_bd=m.st_bd)
    return d


def close_out_e(book, px_eq_close, px_bd_close, nav_close):
    lev = (book.n_eq * px_eq_close + book.n_bd * px_bd_close) / nav_close
    return replace(book, prev_close_lev=lev)


# ============================================================================
# СЕССИЯ ЦЕЛИКОМ: блокировка -> восстановление -> сверка с брокером -> решение
# -> заявки -> журнал -> сохранение. Порядок существен: сверка выполняется ДО
# расчёта, потому что считать книгу от неверного состояния хуже, чем не считать.
# ============================================================================

class RollGap(RuntimeError):
    """Книга осталась без требуемой экспозиции после переноса серии.

    provable — ДОКАЗУЕМО ли восстановление (шестнадцатый круг, №5): True только когда исход
    исходной заявки ИЗВЕСТЕН (терминальный отчёт) и снимок позиций совпал с целью. После
    НЕИЗВЕСТНОГО исхода совпадение снимков ничего не доказывает (stale_twice,
    fill_after_end) — такой ролл не переносится автоматически, только О-5."""

    def __init__(self, msg, provable=False):
        super().__init__(msg)
        self.provable = provable


def _filled(rec, want):
    """Исполнено ли РОВНО заявленное. Без усечения до целого: для дробных долей фондов
    int() отбрасывал остаток, и недобор 100,9 при заявке 100 проходил как точное."""
    got = rec.get('filled', 0)
    try:
        return abs(float(got) - float(want)) <= 1e-9
    except (TypeError, ValueError):
        return False


def _cancel_all(broker):
    """Снять все живые заявки, ПОДТВЕРДИВ терминальный статус. Возвращает список неснятых.

    Ответ брокера — структурный факт, а не «да»: заявка могла УСПЕТЬ ИСПОЛНИТЬСЯ до снятия.
    Незакрытой считается только заявка без терминального статуса; исполнившуюся при снятии
    закрывать нечего, а её след подхватит сверка по фактическим позициям. Прежнее условие
    требовало ровно True, и живой адаптер (возвращающий описание) давал stuck ВСЕГДА —
    то есть на IBKR ни одно восстановление не могло завершиться успешно.
    """
    stuck = []
    for oid in list(getattr(broker, 'open_orders', lambda: [])()):
        try:
            r = broker.cancel_order(oid)
        except Exception:
            stuck.append(oid); continue
        if r is True:
            continue
        if isinstance(r, dict) and r.get('terminal'):
            continue
        stuck.append(oid)
    return stuck


def restore_to(broker, target_book, route='F'):
    """Привести книгу брокера к целевой РАЗНОСТЬЮ ОТ ФАКТИЧЕСКОЙ, а не отменой своих шагов.

    ПОЧЕМУ ТАК. Прежняя компенсация откатывала записанные нами действия и объявляла книгу
    восстановленной по числу успешных отмен. Это ложь в трёх случаях: заявка исполнилась,
    а подтверждение потерялось; исполнение оказалось больше заявленного; после отката у
    брокера осталась живая заявка. Единственная надёжная опора — фактические позиции.
    """
    import state as ST
    stuck = _cancel_all(broker)
    if stuck:
        # ЖИВАЯ ЗАЯВКА = НИКАКИХ НОВЫХ ЗАЯВОК (четырнадцатый круг, №1): компенсация поверх
        # неснятого остатка исполняется вместе с ним, и счёт уезжает на сумму остатка при
        # «восстановленном» файле. Стоим и зовём человека.
        have0 = {k: v for k, v in (broker.net_positions() or {}).items() if v}
        return False, have0, stuck
    want = ST.expected_positions(target_book, route)
    have = {k: v for k, v in (broker.net_positions() or {}).items() if v}
    unknown = []
    for inst in sorted(set(want) | set(have)):
        d = want.get(inst, 0) - have.get(inst, 0)
        if d:
            try:
                broker.place(inst, d)
            except Exception as ex:
                # НЕИЗВЕСТНОСТЬ КОМПЕНСАЦИИ НЕ ГЛОТАЕТСЯ (семнадцатый круг, №4): оборванная
                # компенсирующая заявка могла исполниться позже — совпавший снимок после неё
                # ничего не доказывает, и «восстановлено» объявлять нельзя.
                unknown.append(f'{inst} {d:+}: исход неизвестен ({ex})')
                # И ПОСЛЕ ПЕРВОЙ НЕИЗВЕСТНОСТИ — СТОП (восемнадцатый круг, №6): подача
                # следующих компенсаций поверх неизвестного исхода создавала бы ВТОРУЮ
                # неизвестную позицию вместо локализации первой.
                break
    stuck += _cancel_all(broker)
    have2 = {k: float(v) for k, v in (broker.net_positions() or {}).items() if v}
    want2 = {k: float(v) for k, v in want.items()}
    if unknown:
        return False, have2, stuck + unknown
    return (have2 == want2 and not stuck), have2, stuck


def execute_roll(broker, orders, book_before=None, route='F', ref_prices=None):
    """Перенос серии. Повторов НЕТ: исключение означает неизвестный статус заявки.

    Прежняя редакция повторяла открытие, если у брокера не осталось живых заявок. Но
    «заявка исполнилась, а подтверждение потерялось» выглядит ровно так же — и повтор
    удваивал новую серию. Единственный безопасный ответ на неизвестность — остановиться и
    привести книгу к исходной по фактическим позициям.
    """
    closes = [(i, q) for i, q in orders if q < 0]
    opens = [(i, q) for i, q in orders if q > 0]
    # ПАРАМИ ПО НОГАМ (восемнадцатый круг, №8): «все закрытия, потом все открытия» держали
    # книгу ПОЛНОСТЬЮ плоской до нескольких минут (ожидание до 120 с на заявку) — на
    # $20 млн gross движение 0,5% в этой дыре стоит до сотни тысяч. Теперь дыра ограничена
    # одной ногой: закрыл А — открыл А, затем то же для Б.
    _leg = lambda i: 'А' if i.startswith(('ES', 'MES', 'CSPX')) else 'Б'
    seq = []
    for lg in ('А', 'Б'):
        seq += [(i, q) for i, q in closes if _leg(i) == lg]
        seq += [(i, q) for i, q in opens if _leg(i) == lg]
    closes_opens = seq
    placed = []

    def bail(msg, known=True):
        if book_before is None:
            raise RollGap(f'{msg}; исходная книга не передана — восстановление невозможно, '
                          f'требуется ручной разбор (О-5)')
        try:
            ok, have, stuck = restore_to(broker, book_before, route)
        except Exception as ex2:
            raise RollGap(f'{msg}; восстановление НЕ ВЫПОЛНЕНО ({ex2}) — требуется ручной '
                          f'разбор (О-5)')
        if ok and known:
            # ЧЕСТНОЕ ОПИСАНИЕ ПЕРЕНОСА (восемнадцатый круг, №11): roll_pending сохранён,
            # но автопилот ставит тревогу, а намерение остаётся до разбора — фактический
            # повтор случится СЛЕДУЮЩЕЙ сессией ПОСЛЕ ручного снятия тревоги (О-5), и
            # прежняя формулировка обещала автоматизм, которого по дисциплине нет.
            raise RollGap(f'{msg}; книга приведена к исходной ПО ДОСТУПНЫМ ДАННЫМ '
                          f'(подтверждение — сверкой следующей сессии); roll_pending '
                          f'сохранён — перенос выполнится следующей сессией ПОСЛЕ ручного '
                          f'снятия тревоги (О-5)', provable=True)
        if ok:
            # ИСХОД НЕИЗВЕСТЕН (шестнадцатый круг, №5): снимок совпал, но поздний отчёт
            # ещё возможен — «восстановлено» недоказуемо, автоперенос ролла запрещён.
            raise RollGap(f'{msg}; книга ПО СНИМКУ соответствует исходной, но исход заявки '
                          f'НЕИЗВЕСТЕН — поздние отчёты возможны, восстановление '
                          f'недоказуемо; требуется ручной разбор (О-5)')
        raise RollGap(f'{msg}; ВОССТАНОВИТЬ НЕ УДАЛОСЬ: у брокера {have}, неснятых заявок '
                      f'{len(stuck)} — требуется ручной разбор (О-5)')

    for inst, qty in closes_opens:
        try:
            rec = broker.place(inst, qty, (ref_prices or {}).get(inst))
        except Exception as ex:
            bail(f'{inst} {qty:+d}: {ex} (статус заявки НЕИЗВЕСТЕН, повтор не выполняется)',
                 known=False)
        placed.append(rec)
        if not _filled(rec, qty):
            bail(f'{inst}: заявка {qty:+d}, исполнено {rec.get("filled")}')
    stuck = _cancel_all(broker)
    if stuck:
        bail(f'после переноса остались неснятые заявки: {stuck}')
    return placed


def _resume_intent(ST, bp, cls, route, book, sess, broker, dry_run):
    """Разобрать намерение, оставшееся от оборвавшегося запуска.

    Три исхода, и все три различимы только при наличии намерения:
      — у брокера ПРЕЖНЯЯ книга: заявки не проходили, намерение снимается;
      — у брокера НАМЕЧЕННАЯ книга: сделка состоялась, а состояние записаться не успело —
        книга принимается и дописывается, сессия продолжается штатно;
      — всё остальное: промежуточное состояние, ручной разбор (О-5).
    Ни один из исходов не «чинится» подачей заявок: восстановление идёт отдельным путём.
    """
    it = ST.load_intent(bp)
    if not it:
        return book, None
    if it.get('route') != route:
        raise RuntimeError(f'намерение записано для маршрута {it.get("route")}, '
                           f'запрошен {route} — ручной разбор')
    before = cls(**it['book_before'])
    after = cls(**it['book_after'])
    # СОСТОЯНИЕ УЖЕ ДОПИСАНО ПРОШЛЫМ ПРОЦЕССОМ (семнадцатый круг, №3): обрыв между
    # ST.save() и clear_intent() оставлял намерение при уже записанной книге — повторная
    # запись удваивала номер сессии и стирала рассчитанные поля замыкания.
    d_int = it.get('session_date') or ''
    if d_int and book.last_session == d_int:
        if not dry_run:
            ST.clear_intent(bp)
        return book, None
    pos = broker.net_positions()
    live = getattr(broker, 'open_orders', lambda: [])()
    if live:
        raise RuntimeError(f'осталось незавершённое намерение прошлого запуска И живые '
                           f'заявки {live} — сессия остановлена до ручного разбора (О-5)')
    if not ST.reconcile(before, route, pos):
        # СНИМОК ПОЗИЦИЙ — НЕ ДОКАЗАТЕЛЬСТВО «заявок не было» (семнадцатый круг, №3):
        # исполнение могло состояться, а отчёт и позиция запаздывать (fill_after_end,
        # stale_twice). Второй независимый источник — БАРЬЕР сегодняшних исполнений нашей
        # метки: пустой барьер ПЛЮС исходные позиции = заявок действительно не было; отчёт
        # при исходных позициях = снимок лжёт, О-5.
        try:
            execs = list(getattr(broker, 'todays_executions', lambda: [])())
        except Exception as ex:
            raise RuntimeError(f'намерение не разобрано: барьер исполнений недоступен '
                               f'({ex}) — ручной разбор (О-5)')
        if execs:
            raise RuntimeError(
                f'позиции совпадают с исходной книгой, но за сегодня есть исполнения '
                f'нашей метки (permId {execs[:6]}) — снимок недостоверен, поздний отчёт '
                f'возможен; ручной разбор (О-5)')
        if not dry_run:
            ST.clear_intent(bp)
        return book, None
    if not ST.reconcile(after, route, pos):
        # КНИГА У БРОКЕРА — НАМЕЧЕННАЯ: сделка прошла, запись состояния не успела. Сессия
        # ТОЙ ДАТЫ СОСТОЯЛАСЬ, и продолжать её нельзя. Прежняя редакция дописывала книгу и
        # шла считать решение дальше по ТОМУ ЖЕ рынку: в день ролла уже перенесённая серия
        # проходила target_tag второй раз и уезжала на КВАРТАЛ вперёд (Z26 -> H27).
        d = it.get('session_date') or ''
        after = replace(after, last_session=d or after.last_session,
                        close_provisional=True)
        if dry_run:
            return after, d
        ST.save(bp, after, route, sess + 1,
                note='намерение прошлого запуска исполнено у брокера; состояние дописано, '
                     'сессия той даты завершена')
        ST.clear_intent(bp)
        return after, d
    raise RuntimeError(
        'намерение прошлого запуска не завершено: у брокера книга не совпадает ни с '
        f'исходной, ни с намеченной. Фактически {pos}; намечалось '
        f'{ST.expected_positions(after, route)}; было {ST.expected_positions(before, route)}. '
        'Ручной разбор (О-5).')


def run_session(broker, market, *, dirpath, route='F', band=None, cap=CAP_LEV,
                capital=None, closing_nav=None, journal_path=None, dry_run=False,
                ref_prices=None, book_path=None, series_a=None, **kw):
    """Одна торговая сессия. Возвращает (Decision, список заявок, список расхождений).

    dry_run=True считает и сверяет, но не подаёт заявок и не меняет состояние — режим
    наблюдения без позиции, требуемый протоколом П-2.
    """
    import state as ST
    import journal as J
    cls = BookE if route == 'E' else Book
    # ЕДИНОЕ МЕСТО КНИГИ, общее с переходным исполнителем: иначе после перехода контур
    # читал бы другой файл и торговал по устаревшему состоянию.
    # ПУТЬ КНИГИ — АРГУМЕНТ, А НЕ ПЕРЕМЕННАЯ ОКРУЖЕНИЯ. Прежде запуск сессии ЗАПИСЫВАЛ
    # ADDFUT_BOOK_PATH под свой маршрут, и следующий запуск на другом маршруте в том же
    # процессе читал чужую книгу. Переменная задумана как ручное переопределение оператора,
    # и программа не вправе её подменять.
    bp = Path(book_path) if book_path else ST.book_path(route)
    with ST.hold_book_lock():
        book, sess, saved_route = ST.load(bp, cls)
        if book is None:
            book = cls()
        elif saved_route != route:
            raise RuntimeError(f'состояние записано для маршрута {saved_route}, '
                               f'запрошен {route}: смену маршрута выполняет transition.py')
        # НЕЗАВЕРШЁННОЕ НАМЕРЕНИЕ ПРОШЛОГО ЗАПУСКА. Разбирается ДО сверки: иначе сделка,
        # состоявшаяся перед падением процесса, выглядит просто расхождением, и различить
        # «не начинали» от «прошло целиком» невозможно.
        book, done_date = _resume_intent(ST, bp, cls, route, book, sess, broker, dry_run)
        if done_date:
            # Сессия этой даты уже исполнена прошлым запуском: заявок нет, решение не
            # считается заново. Замыкание выполняется отдельным запуском, как обычно.
            # ЖУРНАЛ §7 ПОЛУЧАЕТ ЧЕСТНУЮ ПОМЕТКУ (семнадцатый круг, №15): строки
            # исполнения этой сессии утрачены вместе с упавшим процессом — без пометки
            # выборка §7 выглядела бы полной, не быв ею.
            if journal_path and not dry_run:
                J.append(journal_path, dict(
                    date=done_date, leg='', instrument='ИТОГ', qty=0, px_order='-',
                    px_fill='', commission='', reason='', nav='', leverage='',
                    roll_spread_near='', roll_spread_far='',
                    note=f'итог сессии {sess + 1}: состояние принято по намерению, строки '
                         f'исполнения утрачены — сверка §7 обязана исключить сессию'))
            dd = Decision(date=market.date, book_after=book, capital_after_costs=0.0)
            dd.reasons.append(f'сессия {done_date} исполнена прошлым запуском; состояние '
                              f'дописано по намерению, повторного решения не было')
            return dd, [], []

        diff = ST.reconcile(book, route, broker.net_positions(),
                            open_orders=getattr(broker, 'open_orders', lambda: [])())
        if diff:
            raise RuntimeError('книга брокера не совпадает с состоянием — сессия '
                               'остановлена до ручного разбора:\n  ' + '\n  '.join(diff))
        # СЕРИЯ РЫНКА ПРОТИВ КНИГИ ПОД ЗАМКОМ (№11): вход собирался вне замка, и если в этом
        # окне другой процесс перевёл маршрут или серию, размеры считались бы по цене ЧУЖОЙ
        # серии при формально совпавших позициях.
        if series_a is not None and route == 'F' and book.ser_a not in (None, series_a):
            raise RuntimeError(f'вход собран для серии {series_a}, а книга под замком держит '
                               f'{book.ser_a} — книга изменилась между сборкой и замком')
        cur = market.date.strftime('%Y-%m-%d')
        if book.last_session and cur <= book.last_session:
            raise RuntimeError(
                f'сессия {cur} не новее последней завершённой ({book.last_session}): '
                f'повторный запуск за тот же день или торговля по устаревшим данным '
                f'запрещены')
        nlv = float(broker.net_liquidation() if capital is None else capital)
        dec = (step_e(book, market, nlv, band=band, cap=cap, **kw) if route == 'E'
               else step(book, market, nlv, band=band, cap=cap, route=route, **kw))
        # ЗАКРЫТИЯ ВПЕРЁД И НА МАРШРУТЕ Е: порядок словаря произволен, и открытие могло
        # уйти раньше закрытия — то есть требовать маржу, которая ещё не освобождена.
        orders = (sorted(dec.orders.items(), key=lambda x: (x[1] > 0, x[0])) if route == 'E'
                  else book_to_orders(dec, book))
        placed = []
        if dec.trade and not dry_run and ref_prices is not None:
            # ОРИЕНТИР ОБЯЗАТЕЛЕН ДЛЯ КАЖДОЙ ЗАЯВКИ (восемнадцатый круг, №17): пропавший
            # молча ориентир давал пустое поле px_order, строка выпадала из §7, и выборка
            # редела выборочно — на дальней серии ролла это систематика, не случайность.
            _no_ref = [i for i, _q in orders if ref_prices.get(i) is None]
            if _no_ref:
                raise RuntimeError(f'нет цен-ориентиров для {_no_ref} — торговля без '
                                   f'ориентира запрещена, журнал §7 потерял бы наблюдение')
        if dec.trade and not dry_run:
            # ЖУРНАЛ §7 ПРОВЕРЯЕТСЯ ДО ЗАЯВОК (семнадцатый круг, №15): цепочка хэшей
            # доказывает нетронутость записанного, но не полноту — повреждённый журнал
            # прежде молча продолжался, а сессия без итоговой строки означает, что часть
            # строк исполнения могла не записаться; выборка §7 стала бы ложной.
            if journal_path:
                try:
                    J.verify(journal_path)
                except Exception as ex:
                    raise RuntimeError(f'журнал §7 повреждён: {ex} — торговля запрещена '
                                       f'до починки (О-5)')
                _jr = J.read(journal_path)
                if _jr and not str(_jr[-1].get('note', '')).startswith('итог сессии'):
                    raise RuntimeError('журнал §7 не закрыт итоговой строкой прошлой '
                                       'сессии — записи исполнения могли потеряться; '
                                       'ручной разбор (О-5)')
            # НАМЕРЕНИЕ ДО ПЕРВОЙ ЗАЯВКИ. С этого момента на диске записано, какая книга
            # намечалась; сбой между сделкой и сохранением состояния перестаёт быть
            # неразличимым случаем.
            ST.save_intent(bp, route, sess + 1, book, dec.book_after, orders,
                           session_date=cur)
            if dec.roll_pairs:
                try:
                    placed = execute_roll(broker, orders, book_before=book, route=route,
                                          ref_prices=ref_prices)
                except RollGap as gap:
                    # ОТКАЧЕННЫЙ РОЛЛ ДОЛЖЕН ПОВТОРИТЬСЯ. День ролла один; без отметки
                    # перенос не состоялся бы никогда, и книга дожила бы до поставки.
                    # ТОЛЬКО ДОКАЗУЕМОЕ восстановление (шестнадцатый круг, №5): признак —
                    # структурный provable, а не подстрока сообщения; неизвестный исход
                    # не переносится автоматически — О-5 без записи состояния.
                    if getattr(gap, 'provable', False):
                        ST.save(bp, replace(book, roll_pending=True), route, sess + 1,
                                note=f'ролл отложен: {gap}')
                    raise
            else:
                # СВЕРКА НА КАЖДОЙ ЗАЯВКЕ. Прежде обычный путь подавал все заявки подряд и
                # проверял исполнение лишь в конце: после частичного закрытия открытия
                # продолжались, и книга уходила в состояние, которого никто не намечал.
                for inst, qty in orders:
                    try:
                        # ЦЕНА-ОРИЕНТИР ИДЁТ В ЗАЯВКУ. Без неё журнал §7 пишет пустое поле,
                        # сверка такие строки отбрасывает, и наблюдения не копятся вовсе.
                        rec = broker.place(inst, qty, (ref_prices or {}).get(inst))
                    except Exception as ex:
                        # ИСХОД НЕИЗВЕСТЕН (шестнадцатый круг, №5): совпавший снимок не
                        # доказывает восстановление — поздний отчёт возможен; исход всегда
                        # О-5, «сессия переносится» не заявляется.
                        try:
                            ok_r, have_r, stuck_r = restore_to(broker, book, route)
                            note = ('книга ПО СНИМКУ соответствует исходной, но исход '
                                    'заявки НЕИЗВЕСТЕН — поздние отчёты возможны, '
                                    'восстановление недоказуемо; ручной разбор (О-5)'
                                    if ok_r else
                                    f'ВОССТАНОВИТЬ НЕ УДАЛОСЬ: у брокера {have_r}, '
                                    f'неснятых заявок {len(stuck_r)} — ручной разбор (О-5)')
                        except Exception as ex2:
                            note = (f'восстановление НЕ ВЫПОЛНЕНО ({ex2}) — ручной '
                                    f'разбор (О-5)')
                        raise RuntimeError(
                            f'{inst} {qty:+d}: {ex} (статус НЕИЗВЕСТЕН, повтор не '
                            f'выполняется); {note}')
                    except BaseException as ex:
                        # ИСКЛЮЧЕНИЕ ПОСЛЕ ПЕРВОГО ИСПОЛНЕНИЯ — это рассинхронизация, а не
                        # просто ошибка: часть книги уже изменена. Отменяем всё живое и
                        # называем состояние прямо.
                        try:
                            for oid in getattr(broker, 'open_orders', lambda: [])():
                                try:
                                    broker.cancel_order(oid)
                                except Exception:
                                    pass
                        except Exception:
                            pass          # аварийная уборка не смеет маскировать причину
                        raise RuntimeError(
                            f'{inst} {qty:+d}: {ex}; ранее исполнено {len(placed)} из '
                            f'{len(orders)} заявок — книга в промежуточном состоянии, '
                            f'состояние не сохранено, требуется ручной разбор (О-5)')
                    placed.append(rec)
                    if not _filled(rec, qty):
                        try:
                            ok_r, have_r, stuck_r = restore_to(broker, book, route)
                            note = ('книга приведена к исходной ПО ДОСТУПНЫМ ДАННЫМ (окончательное подтверждение — входной сверкой следующей сессии), сессия переносится'
                                    if ok_r else
                                    f'ВОССТАНОВИТЬ НЕ УДАЛОСЬ: у брокера {have_r} — '
                                    f'ручной разбор (О-5)')
                        except Exception as ex2:
                            note = (f'восстановление НЕ ВЫПОЛНЕНО ({ex2}) — ручной '
                                    f'разбор (О-5)')
                        raise RuntimeError(
                            f'{inst}: заявка {qty:+d}, исполнено {rec.get("filled")}; '
                            + note)
        if journal_path:
            fills = {r['instrument']: r for r in placed}
            _rows = J.rows_from_decision(dec, nlv, orders, fills)
            for row in _rows:
                J.append(journal_path, row)
            # ИТОГОВАЯ СТРОКА СЕССИИ (семнадцатый круг, №15): маркер полноты. Обрыв между
            # append-ами оставляет валидную ЦЕПОЧКУ без итога — следующая сессия увидит
            # незакрытый журнал и остановится, вместо того чтобы копить ложную выборку §7.
            if dec.trade and not dry_run:
                J.append(journal_path, dict(
                    date=f'{market.date:%Y-%m-%d}', leg='', instrument='ИТОГ', qty=0,
                    px_order='-', px_fill='', commission='', reason='', nav=nlv,
                    leverage='', roll_spread_near='', roll_spread_far='',
                    note=f'итог сессии {sess + 1}: строк {len(_rows)}'))
        # ОТКАЗ РЕШЕНИЯ БЕЗ ЕДИНОЙ ЗАЯВКИ НЕ ЗАКРЫВАЕТ ДЕНЬ (десятый круг, №5): прежде
        # last_session ставился, книга сохранялась, и «сессия не считается» на деле
        # означало «день отторгован без требуемого входа».
        if dec.refusals and not placed and not dry_run:
            ST.clear_intent(bp)
            return dec, orders, diff
        if not dry_run:
            # СВЕРКА ПОСЛЕ ИСПОЛНЕНИЯ. Частичное исполнение и исполнение больше заявки
            # прежде считались успехом, после чего сохранялась ЦЕЛЕВАЯ книга — немедленная
            # потеря синхронизации. Состояние сохраняется только если фактическая книга
            # брокера совпала с намеченной.
            bad = [r for r in placed if r.get('filled') != r.get('qty')]
            after = ST.reconcile(dec.book_after, route, broker.net_positions(),
                                 open_orders=getattr(broker, 'open_orders', lambda: [])())
            if bad or after:
                raise RuntimeError(
                    'исполнение разошлось с намерением — состояние НЕ сохранено, книга '
                    'у брокера требует ручного разбора (О-5):\n  ' +
                    '\n  '.join([f"{r['instrument']}: заявка {r['qty']:+d}, исполнено "
                                  f"{r.get('filled'):+d}" for r in bad] + after))
            # ЗАМЫКАНИЕ СЕССИИ: без него prev_close_lev остаётся NaN, сравнение с капом
            # всегда ложно, и нормативный триггер «плечо фактического закрытия предыдущей
            # сессии» в бою просто не работает.
            # NLV НА ЗАКРЫТИИ, а не на открытии: §1 определяет триггер капа через плечо
            # ФАКТИЧЕСКОГО закрытия. Прежде подставлялся капитал начала сессии, и триггер
            # считался от неверной величины.
            nav_close = float(closing_nav if closing_nav is not None
                              else broker.net_liquidation())
            closed = (close_out_e(dec.book_after, market.px_eq_today, market.px_bd_today,
                                  nav_close) if route == 'E' else
                      close_out(dec.book_after, market.px_eq_today, market.dref_today,
                                nav_close))
            # Замыкание внутри торгового запуска ВСЕГДА предварительное: цен закрытия ещё
            # нет. Окончательное делает finalize_close после закрытия биржи.
            closed = replace(closed, last_session=cur, close_provisional=True)
            ST.save(bp, closed, route, sess + 1,
                    note='; '.join(dec.reasons))
            ST.clear_intent(bp)          # состояние записано — намерение исполнено
            dec.book_after = closed
        return dec, orders, diff
