#!/usr/bin/env python3
"""Рыночные входы сессии из живого источника, по конвенции §2.

ЧТО ИМЕННО НУЖНО КОНТУРУ. Величины ПРЕДЫДУЩЕЙ сессии: цены ног, дюрация референсной
десятилетней ноты и состояние месячного сигнала. Сегодняшняя цена нужна только для
замыкания сессии, то есть уже после подачи заявки. Поэтому задержанных на пятнадцать минут
данных достаточно: сделка опирается на вчерашнее закрытие, а не на текущую котировку.

ЗАДЕРЖАННЫЕ ДАННЫЕ НЕ ЗНАЧИТ ПРИБЛИЖЁННЫЕ. Закрытие берётся историческим запросом
(reqHistoricalData с whatToShow='TRADES'), а не из потока: закрытие — величина
окончательная, и у неё нет задержки вовсе.

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ, А НЕ ПОДРАЗУМЕВАЕТСЯ.
  — ДАТА БАРА. Положительность цены не отличает вчерашнее закрытие от пятничного,
    повторённого во вторник после сбоя источника. Дата предыдущего бара сверяется с датой
    сессии и МЕЖДУ ИСТОЧНИКАМИ: цена и дюрация обязаны относиться к одной сессии.
  — ЕДИНИЦЫ. S.ES_MULT=500 откалиброван под цену SPY, а S.dur принимает доходность долей
    единицы. Ошибка множителя не даёт ни исключения, ни абсурдной цифры — только другую
    стратегию при тишине в логе.
  — ДАТА СЕССИИ берётся в зоне БИРЖИ, а не машины: запуск около полуночи UTC пометил бы
    сделку соседней датой, а last_session тогда либо заблокирует правильный запуск, либо
    допустит второй под другой локальной датой.
  — СИГНАЛ читается со СТРОГИМ разбором значений: 'False', '0', 'N' и пустое значение
    непусты и через bool() дали бы ВКЛЮЧЁННУЮ ногу вместо выключенной.

СИГНАЛ НЕ ПЕРЕСЧИТЫВАЕТСЯ ЕЖЕДНЕВНО. §1 определяет его на месячных закрытиях по
двенадцатимесячной средней; между месячными датами состояние ПОСТОЯННО.
"""
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
import tz                      # noqa: F401 — разбор дат IBKR: устаревшие зоны (см. tz.py)
import contracts as CT
import sim_v13 as S

MAX_BAR_GAP_D = 5              # дольше — источник отстал (праздничные выходные укладываются)
EXCHANGE_TZ = 'America/Chicago'
# ЗАМЫКАТЬ МОЖНО ТОЛЬКО ПОСЛЕ ЗАКРЫТИЯ. Дневной бар с сегодняшней датой существует и в
# середине сессии — он просто ещё не закончен. Наличие бара НЕ доказывает, что торги
# завершены, и замыкание в 08:30 по Чикаго взяло бы цену середины дня за цену закрытия:
# триггер капа §1 снова считался бы от неверной величины. ES закрывается в 15:15, ZN в
# 15:00 по Чикаго; берётся 16:00 с запасом на расчётные бары.
CLOSE_AFTER_H = 16

DUR_MIN, DUR_MAX = 3.0, 12.0
YIELD_MIN, YIELD_MAX = 0.0005, 0.25

# МНОЖИТЕЛИ МОДЕЛИ. Спецификации фьючерсов НЕСТАЦИОНАРНЫ на десятилетиях: SP стоил $500 за
# пункт (1982–1997), потом $250, потом исчез вовсе (2021); ES ($50) существует лишь с
# 09.1997, MES — с 05.2019. Расчёт закладывает сегодняшние множители, и их смена биржей
# обязана останавливать торговлю, а не молча менять размер книги: ногу Б движок считает
# МОДЕЛЬНОЙ единицей (112 000 x 0,88 x D), и смену множителя ZN не поймала бы ни цена, ни
# сверка позиций — только эта таблица против реестра биржи.
MODEL_MULT = {'ES': '50', 'MES': '5', 'ZN': '1000'}

TRUE_SET = {'1', 'true', 'да', 'yes', 'y', 't'}
FALSE_SET = {'0', 'false', 'нет', 'no', 'n', 'f'}


class FeedError(RuntimeError):
    """Вход недостоверен. Сессия не считается: торговать по догадке хуже, чем не торговать."""


def exchange_today():
    """Сегодняшняя дата В ЗОНЕ БИРЖИ, без привязки к часам машины."""
    import pandas as pd
    return pd.Timestamp.now(tz=EXCHANGE_TZ).tz_localize(None).normalize()


def registry():
    import csv
    # РЕЕСТР ПРИНАДЛЕЖИТ СЧЁТУ, А НЕ КОДУ: con_id меняются каждый квартал, и держать их
    # в каталоге программы значит менять пакет ради данных счёта. Место переопределяется
    # переменной окружения; по умолчанию — рядом с кодом, как было.
    reg = Path(os.environ.get('ADDFUT_REGISTRY') or (HERE / 'instruments_live.csv'))
    if not reg.exists():
        raise FeedError(f'нет реестра {reg}: сначала first_connect.py')
    return {r['instrument']: r for r in csv.DictReader(open(reg, encoding='utf-8'))}


def contract_of(ib, name, reg=None):
    from ib_insync import Contract
    reg = reg or registry()
    if name not in reg:
        raise FeedError(f'{name}: нет в реестре — обновить first_connect.py')
    c = Contract(conId=int(reg[name]['con_id']))
    ib.qualifyContracts(c)
    bad = CT.mismatches(c, reg[name])
    if bad:
        raise FeedError(f'{name}: con_id описывает другой контракт — {"; ".join(bad)}; '
                        f'цена чужого актива легла бы в основу размера книги. '
                        f'Обновить реестр first_connect.py')
    return c


def _bars(ib, contract, days=10):
    from ib_insync import util
    b = ib.reqHistoricalData(contract, endDateTime='', durationStr=f'{days} D',
                             barSizeSetting='1 day', whatToShow='TRADES', useRTH=True)
    if not b:
        raise FeedError(f'{contract.localSymbol or contract.symbol}: история пуста')
    return util.df(b)


def prev_session(d, holidays=()):
    """Предыдущая БИРЖЕВАЯ сессия: рабочие дни минус праздники."""
    import pandas as pd
    t = pd.Timestamp(d).normalize() - pd.Timedelta(days=1)
    hol = {pd.Timestamp(h).normalize() for h in holidays}
    while t.weekday() >= 5 or t in hol:
        t -= pd.Timedelta(days=1)
    return t


def closes(ib, contract, today, expected_prev=None):
    """Закрытия ПРЕДЫДУЩЕЙ завершённой сессии и текущей, если она уже закрыта.

    Возвращает (px_prev, date_prev, px_today | None, date_today | None).
    """
    import pandas as pd
    df = _bars(ib, contract)
    if len(df) < 2:
        raise FeedError(f'{contract.symbol}: менее двух дневных баров')
    dates = [pd.Timestamp(x).normalize() for x in df['date']]
    t = pd.Timestamp(today).normalize()
    last_is_today = dates[-1] == t
    i_prev = -2 if last_is_today else -1
    px = float(df.iloc[i_prev]['close'])
    if not math.isfinite(px) or px <= 0:
        raise FeedError(f'{contract.symbol}: недостоверное закрытие {px}')
    d_prev = dates[i_prev]
    gap = (t - d_prev).days
    if gap <= 0:
        raise FeedError(f'{contract.symbol}: последнее закрытие {d_prev:%d.%m.%Y} не раньше '
                        f'даты сессии {t:%d.%m.%Y} — источник опережает календарь')
    if gap > MAX_BAR_GAP_D:
        raise FeedError(f'[STALE_BAR] {contract.symbol}: последнее закрытие {d_prev:%d.%m.%Y}, '
                        f'это {gap} дней назад — источник отстал, сессия не считается')
    # ТОЧНАЯ ПРЕДЫДУЩАЯ СЕССИЯ, А НЕ ДОПУСК. Пятничный бар во вторник имеет возраст четыре
    # дня и проходил пятидневный допуск, хотя понедельничная сессия уже состоялась: цель
    # обеих ног считалась по позапрошлому закрытию. Календарь известен — сверяется он.
    if expected_prev is not None and d_prev != expected_prev:
        raise FeedError(f'[STALE_BAR] {contract.symbol}: закрытие {d_prev:%d.%m.%Y}, а '
                        f'предыдущая сессия биржи — {expected_prev:%d.%m.%Y}; источник '
                        f'пропустил сессию, вход недостоверен')
    px_t = float(df.iloc[-1]['close']) if last_is_today else None
    if px_t is not None and not (math.isfinite(px_t) and px_t > 0):
        # Сегодняшнее закрытие идёт в ЗАМЫКАНИЕ (§1, триггер капа): NaN сравнивается ложно,
        # и кап следующей сессии молча отключился бы при формально целой книге (№10).
        raise FeedError(f'{contract.symbol}: недостоверное сегодняшнее закрытие {px_t}')
    return px, d_prev, px_t, (dates[-1] if last_is_today else None)


def es_to_unit(es_px):
    """Приведение котировки ES к десятой доле индекса (для сверки базиса и запасного пути).

    Основная цена ноги А берётся у САМОГО SPY: модельная единица — 500 x SPY, а ES/10
    отличается от SPY фьючерсным базисом (ставка минус дивиденды). Девятая рецензия, №3:
    прежняя редакция размеряла ногу по 50 x ES и выдавала тождество 500x(ES/10) == 50xES
    за проверку соответствия модели.
    """
    return es_px / 10.0


BASIS_MAX = 0.02              # |ES/10 / SPY − 1| больше 2% — данные врут, а не базис


def dref_from_yield(y_frac):
    """Дюрация референсной ноты по доходности В ДОЛЯХ ЕДИНИЦЫ — та же функция, что в расчётах."""
    if not math.isfinite(y_frac) or not (YIELD_MIN <= y_frac <= YIELD_MAX):
        raise FeedError(f'доходность десятилетней ноты {y_frac} вне правдоподобных границ '
                        f'[{YIELD_MIN}, {YIELD_MAX}] долей единицы — проверить единицы источника')
    d = float(S.dur(y_frac))
    if not (DUR_MIN <= d <= DUR_MAX):
        raise FeedError(f'дюрация {d:.3f} вне границ [{DUR_MIN}, {DUR_MAX}] — вход недостоверен')
    return d


def _strict_bool(v, where):
    """Строгий разбор состояния ноги. bool() принимал 'False', '0' и пустое за истину."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        if v != v:
            raise FeedError(f'{where}: пустое значение состояния ноги')
        if float(v) in (0.0, 1.0):
            return bool(v)
        raise FeedError(f'{where}: состояние ноги {v!r} — ожидается 0 или 1')
    t = str(v).strip().lower()
    if t in TRUE_SET:
        return True
    if t in FALSE_SET:
        return False
    raise FeedError(f'{where}: состояние ноги {v!r} не распознано')


def yield_pct(ib, today, expected_prev=None):
    """Доходность десятилетней ноты в процентах за предыдущую сессию, и её дата."""
    from ib_insync import Index
    ti = Index('TNX', 'CBOE')
    ib.qualifyContracts(ti)
    y, d, _, _ = closes(ib, ti, today, expected_prev=expected_prev)
    return y / 10.0, d               # TNX котируется в десятых долях процента


def signal_state(today, path=None):
    """Состояние сигнала §1 на сессию — из месячного ряда, без ежедневного пересчёта.

    КОНВЕНЦИЯ РЯДА ДОКАЗАНА НА ИСТОРИИ (2022, 12/12): строка помечена месяцем ДЕЙСТВИЯ, а
    решение принято закрытием ПРЕДЫДУЩЕГО месяца. Строка 2026-08-31 = состояние НА август,
    решённое июльским закрытием. Прежняя редакция брала «последний завершённый месяц» — то
    есть весь август торговала ИЮЛЬСКИМ состоянием; на бумажном счёте это открыло ногу Б,
    которую августовское состояние выключает. Отсутствие строки текущего месяца — отказ:
    значит signal_update не отработал, и торговать не по чему.
    """
    import pandas as pd
    live = Path(os.environ.get('ADDFUT_SIGNALS') or
                (Path(os.path.expanduser('~/.addfut')) / 'signals_live.csv'))
    if path:
        p = Path(path)
    elif live.exists():
        p = live
    else:
        # ОТКАТ НА ИССЛЕДОВАТЕЛЬСКИЙ ФАЙЛ ЗАПРЕЩЁН: потеря живого ряда — событие, а не повод
        # молча читать снимок исследований. Разрешается только явной переменной окружения.
        if os.environ.get('ADDFUT_SIGNALS_FALLBACK_OK') == '1':
            p = ROOT / 'data' / 'signals_monthly.csv'
        else:
            raise FeedError(f'[SIGNAL_STALE] нет живого ряда {live}; торговля по пакетному '
                            f'снимку запрещена (переопределить можно только явно)')
    df = pd.read_csv(p, parse_dates=[0])
    df = df.set_index(df.columns[0]).sort_index()
    cols = {c.lower(): c for c in df.columns}
    ce = cols.get('leg_eq') or cols.get('st_eq')
    cb = cols.get('leg_bond') or cols.get('st_bd')
    if not ce or not cb:
        raise FeedError(f'{p}: нет столбцов состояния ног, есть {list(df.columns)}')
    t = pd.Timestamp(today)
    # СДВИГ НА ОДНУ СЕССИЮ, КАК В ЗАМОРОЖЕННОМ ДВИЖКЕ: strict_states сдвигает дневной ряд
    # состояний на один день, поэтому ПЕРВАЯ сессия месяца торгует ещё ПРЕЖНИМ состоянием, а
    # переключение исполняется второй. Живая читалка без этого сдвига расходилась бы с
    # доказанной историей ровно на день при каждом переключении (девятая рецензия, №2).
    import daily as _DL
    hol = _DL.holidays_for(t.year)
    if prev_session(t, hol).month != t.month:
        t = t - pd.offsets.MonthEnd(1)
    cur = df[(df.index.year == t.year) & (df.index.month == t.month)]
    if len(cur) != 1:
        last = df.index[-1] if len(df) else None
        raise FeedError(f'[SIGNAL_STALE] {p.name}: нет строки на {t:%Y-%m} (последняя '
                        f'{last}) — signal_update не отработал, '
                        f'торговать не по чему')
    row, when = cur.iloc[0], cur.index[0]
    dec_close = when - pd.offsets.MonthEnd(2) + pd.offsets.MonthEnd(1)   # закрытие прошлого месяца
    age = (t - dec_close).days
    return (_strict_bool(row[ce], f'{p.name} {when:%Y-%m} нога А'),
            _strict_bool(row[cb], f'{p.name} {when:%Y-%m} нога Б'), when, age)


def _series_of(book, today):
    import pandas as pd
    import daily as DL
    d0 = pd.Timestamp(today)
    hol = DL.holidays_for(d0.year, d0.year + 1)
    return getattr(book, 'ser_a', None) or DL.target_tag(
        None, d0, DL.is_roll_day(d0, hol), DL.roll_passed_for(d0, hol))


def build_market(ib, today, book, *, route='F', roll_today=None, roll_passed=None):
    """Собрать Market (маршрут Ф) или MarketE (маршрут Е). Любой пробел — FeedError."""
    import pandas as pd
    import daily as DL
    d0 = pd.Timestamp(today)
    # СЛЕДУЮЩИЙ ГОД — ЕСЛИ ПОКРЫТ (№20): требование year+1 останавливало торговлю уже
    # 1 января последнего покрытого года. Сам по себе год без таблицы по-прежнему отказ.
    try:
        hol = DL.holidays_for(d0.year, d0.year + 1)
    except RuntimeError:
        hol = DL.holidays_for(d0.year)
    st_eq, st_bd, sig_date, age = signal_state(d0)
    reg = registry()
    for name, row in reg.items():
        root = ''.join(ch for ch in name if ch.isalpha())
        want = MODEL_MULT.get(root if root in MODEL_MULT else root.rstrip('UZHM'))
        if want is None:
            continue
        if str(row.get('multiplier', '')) != want:
            raise FeedError(f'{name}: множитель у биржи {row.get("multiplier")!r}, модель '
                            f'заложила {want}. Спецификация контракта ИЗМЕНИЛАСЬ — торговля '
                            f'останавливается до пересмотра констант расчёта (§2)')

    if route == 'E':
        # МАРШРУТ Е СОБИРАЕТСЯ СВОИМИ ЦЕНАМИ. Прежде сюда безусловно шёл фьючерсный сборщик:
        # он обращался к book.ser_a, которого у BookE нет, и возвращал Market с ES и TNX,
        # тогда как step_e требует MarketE с ценами фондов. Живой маршрут Е был неисполним,
        # а сессионный перебор этого не видел — он строил MarketE вручную, минуя сборку.
        # ТОЧНАЯ ПРЕДЫДУЩАЯ СЕССИЯ И ДЛЯ ФОНДОВ (десятый круг, №6): равенство дат двух
        # одинаково отставших источников проходило раньше. Календарь берём CME-шный:
        # праздник LSE/EBS при открытых США даст отказ дня — консервативно и вслух.
        _exp_e = prev_session(d0, hol)
        pe, de, pe_t, _ = closes(ib, contract_of(ib, 'CSPX', reg), d0, expected_prev=_exp_e)
        pb, db, pb_t, _ = closes(ib, contract_of(ib, 'CBU0', reg), d0, expected_prev=_exp_e)
        if de != db:
            raise FeedError(f'даты закрытий не совпадают: CSPX {de:%d.%m.%Y}, '
                            f'CBU0 {db:%d.%m.%Y} — один из источников отстал')
        m = DL.MarketE(date=d0, px_eq_prev=pe, px_bd_prev=pb,
                       px_eq_today=pe_t if pe_t is not None else pe,
                       px_bd_today=pb_t if pb_t is not None else pb,
                       st_eq=st_eq, st_bd=st_bd)
        src = dict(CSPX=(pe, str(de.date())), CBU0=(pb, str(db.date())),
                   signal_date=str(sig_date.date()), signal_age_d=age)
        return m, src

    ser = _series_of(book, d0)
    exp_prev = prev_session(d0, hol)
    es_prev, d_es, _, _ = closes(ib, contract_of(ib, f'ES{ser}', reg), d0,
                                 expected_prev=exp_prev)
    # КАЛЕНДАРЬ CME НЕ НАВЯЗЫВАЕТСЯ CBOE (№19): у TNX своя биржа, точная дата требуется
    # только от ES; TNX обязан лишь СОВПАСТЬ с ES той же сессией.
    y_p, d_tnx = yield_pct(ib, d0)
    if d_es != d_tnx:
        raise FeedError(f'даты закрытий не совпадают: ES {d_es:%d.%m.%Y}, TNX '
                        f'{d_tnx:%d.%m.%Y} — цена и дюрация относились бы к разным сессиям')
    # ЦЕНА НОГИ А — САМ SPY (модельная единица 500 x SPY). ES/10 — только сверка базиса.
    from ib_insync import Stock
    spyc = Stock('SPY', 'SMART', 'USD', primaryExchange='ARCA')
    ib.qualifyContracts(spyc)
    px_prev, d_spy, _, _ = closes(ib, spyc, d0, expected_prev=None)
    if d_spy != d_es:
        raise FeedError(f'даты закрытий не совпадают: SPY {d_spy:%d.%m.%Y}, ES '
                        f'{d_es:%d.%m.%Y} — источники разошлись')
    basis = es_to_unit(es_prev) / px_prev - 1.0
    if abs(basis) > BASIS_MAX:
        raise FeedError(f'базис ES/10 к SPY {basis:+.2%} превышает {BASIS_MAX:.0%} — '
                        f'один из источников недостоверен')
    notional = S.ES_MULT * px_prev
    dref = dref_from_yield(y_p / 100.0)
    m = DL.Market(date=d0, px_eq_prev=px_prev, dref_prev=dref, dref_today=dref,
                  px_eq_today=px_prev,
                  roll_today=(DL.is_roll_day(d0, hol) if roll_today is None else roll_today),
                  st_eq=st_eq, st_bd=st_bd,
                  roll_passed=(DL.roll_passed_for(d0, hol) if roll_passed is None
                               else roll_passed),
                  holidays=hol)
    src = dict(es_close=(es_prev, str(d_es.date())), spy_close=px_prev,
               basis=f'{basis:+.3%}', px_eq_prev=px_prev,
               nominal_ES=notional, nominal_MES=notional / 10.0, yield_10y_pct=y_p,
               dref=dref, signal_date=str(sig_date.date()), signal_age_d=age, series=ser)
    return m, src


def reference_prices(ib, route='F'):
    """Цены-ориентиры для журнала §7: закрытие предыдущей сессии по КАЖДОМУ инструменту.

    Без них живой журнал писал пустое поле px_order, сверка §7 такие строки отбрасывает, и
    реальные сессии НЕ НАКАПЛИВАЛИ наблюдений вовсе: издержки 5 б.п. и ролл 1 б.п. остались
    бы непроверенными навсегда, притом что §7 объявляет журнал единственным основанием для
    их пересмотра. Тесты этого не показывали — макет подставляет цену сам.

    Котировка берётся В БИРЖЕВЫХ ЕДИНИЦАХ (ES ~7747, ZN ~108,5): журнал считает недостачу
    через множитель контракта, а не через расчётную единицу ноги.
    """
    today = exchange_today()
    reg = registry()
    want = ('CSPX', 'CBU0') if route == 'E' else ('ES', 'MES', 'ZN')
    out = {}
    for name, r in reg.items():
        root = ''.join(ch for ch in name if not ch.isdigit())[:3]
        if not any(name.startswith(w) for w in want):
            continue
        try:
            px, _, _, _ = closes(ib, contract_of(ib, name, reg), today)
            out[name] = px
        except FeedError:
            continue                 # дальняя серия может ещё не торговаться — не повод падать
    return out


def closing_values(ib, route, book):
    """Фактические цены ЗАКРЫТИЯ текущей сессии — для замыкания §1.

    Требуется именно СЕГОДНЯШНИЙ завершённый бар. Пока его нет, замыкать нечем, и молча
    подставить вчерашнее закрытие означало бы посчитать триггер капа от неверной величины —
    ровно тот дефект, ради которого замыкание вынесено в отдельный запуск.
    """
    import pandas as pd
    now = pd.Timestamp.now(tz=EXCHANGE_TZ)
    if now.hour < CLOSE_AFTER_H:
        raise FeedError(f'сейчас {now:%H:%M} по бирже ({EXCHANGE_TZ}) — торги ещё идут; '
                        f'дневной бар с сегодняшней датой существует, но НЕ ЗАВЕРШЁН. '
                        f'Замыкание возможно после {CLOSE_AFTER_H}:00')
    today = exchange_today()
    reg = registry()
    if route == 'E':
        _, _, pe_t, d1 = closes(ib, contract_of(ib, 'CSPX', reg), today)
        _, _, pb_t, d2 = closes(ib, contract_of(ib, 'CBU0', reg), today)
        if pe_t is None or pb_t is None:
            raise FeedError('сегодняшний бар фондов ещё не закрыт — замыкать рано')
        return pe_t, None, pb_t, dict(CSPX=(pe_t, str(d1.date())), CBU0=(pb_t, str(d2.date())))
    ser = _series_of(book, today)
    # НОГА А ЗАМЫКАЕТСЯ ПО SPY (десятый круг, №1): размер считается в единицах 500 x SPY, и
    # замыкание по ES/10 вносило фьючерсный базис (до 2%) прямо в триггер капа — достаточно,
    # чтобы ложно включить или пропустить кап 2,00. ES остаётся сверкой базиса.
    from ib_insync import Stock
    _spyc = Stock('SPY', 'SMART', 'USD', primaryExchange='ARCA')
    ib.qualifyContracts(_spyc)
    _, _, spy_t, d_spy = closes(ib, _spyc, today)
    if spy_t is None:
        raise FeedError('сегодняшний бар SPY ещё не закрыт — замыкать рано')
    _, _, es_t, d1 = closes(ib, contract_of(ib, f'ES{ser}', reg), today)
    if es_t is None:
        raise FeedError('сегодняшний бар ES ещё не закрыт — замыкать рано')
    if d_spy != d1:
        raise FeedError(f'даты закрытий не совпадают: SPY {d_spy:%d.%m.%Y}, ES {d1:%d.%m.%Y}')
    if abs(es_to_unit(es_t) / spy_t - 1.0) > BASIS_MAX:
        raise FeedError(f'базис на закрытии {es_to_unit(es_t)/spy_t-1:+.2%} превышает '
                        f'{BASIS_MAX:.0%} — источники недостоверны')
    from ib_insync import Index
    ti = Index('TNX', 'CBOE')
    ib.qualifyContracts(ti)
    _, _, y_t, d2 = closes(ib, ti, today)
    if y_t is None:
        raise FeedError('сегодняшний бар TNX ещё не закрыт — замыкать рано')
    if d1 != d2:
        raise FeedError(f'даты закрытий не совпадают: ES {d1:%d.%m.%Y}, TNX {d2:%d.%m.%Y}')
    dref = dref_from_yield((y_t / 10.0) / 100.0)
    return es_to_unit(es_t), dref, None, dict(es_close=(es_t, str(d1.date())),
                                         yield_10y_pct=y_t / 10.0, dref=dref, series=ser)


if __name__ == '__main__':
    from ib_insync import IB
    import daily as DL
    ib = IB(); ib.connect('127.0.0.1', 4002, clientId=32, timeout=30); ib.reqMarketDataType(3)
    try:
        t = exchange_today()
        print(f'дата сессии в зоне биржи ({EXCHANGE_TZ}): {t:%d.%m.%Y}')
        for route, book in (('F', DL.Book()), ('E', DL.BookE())):
            try:
                m, src = build_market(ib, t, book, route=route)
                print(f'\nмаршрут {route}:')
                for k, v in src.items():
                    print(f'  {k:<16} {v}')
                print(f'  сигнал           А={m.st_eq} Б={m.st_bd}')
            except FeedError as ex:
                print(f'\nмаршрут {route}: ОТКАЗ — {ex}')
    finally:
        ib.disconnect()
