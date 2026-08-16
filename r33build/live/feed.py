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

# ЕВРОПЕЙСКИЙ КАЛЕНДАРЬ МАРШРУТА Е (одиннадцатый круг, №5): CSPX торгуется на LSE, CBU0 —
# на SIX/EBS; календарь CME им не указ. Берётся ОБЪЕДИНЕНИЕ закрытий обеих площадок (торгуем
# только когда открыты обе). Непокрытый год — отказ, как и у CME-таблицы.
HOLIDAYS_EU = {
    2026: ('2026-01-01', '2026-01-02', '2026-04-03', '2026-04-06', '2026-05-01',
           '2026-05-04', '2026-05-14', '2026-05-25', '2026-08-31', '2026-12-24',
           '2026-12-25', '2026-12-28', '2026-12-31'),
}


def eu_holidays(*years):
    import pandas as pd
    out = []
    for y in sorted(set(int(x) for x in years)):
        if y not in HOLIDAYS_EU:
            raise FeedError(f'[EU_CAL] европейский календарь не покрывает {y} год — '
                            f'маршрут Е не торгуется до продления таблицы')
        out.extend(pd.Timestamp(x) for x in HOLIDAYS_EU[y])
    return tuple(out)

CAL_HORIZON_DAYS = 90      # за сколько дней до обрыва календаря начинать тревожить


def calendar_horizon(route='F', today=None, days=CAL_HORIZON_DAYS):
    """Покрыт ли календарь маршрута на ближайшие `days` дней (двадцать первый круг, №12).

    Таблицы праздников продлеваются ВРУЧНУЮ по официальным календарям бирж, и обрыв
    обнаруживался лишь в тот день, когда торговать уже нельзя: у маршрута Е таблица
    кончается 2026 годом, и с первой сессии 2027 контур встал бы целиком — маржинальная
    книга CSPX/CBU0 осталась бы без ежедневного О-3-Е, сигналов и замыкания.

    Возвращает (ok, сообщение). Проверяется ГОРИЗОНТ, а не сегодняшний день: тревога
    приходит за три месяца, когда её ещё можно спокойно закрыть.
    """
    import pandas as pd
    import daily as _DL
    if today is None:
        today = exchange_today()
    edge = pd.Timestamp(today) + pd.Timedelta(days=days)
    years = sorted({pd.Timestamp(today).year, edge.year})
    try:
        if route == 'E':
            eu_holidays(*years)
        else:
            _DL.holidays_for(*years)
    except Exception as ex:
        return False, (f'календарь маршрута {route} не покрывает горизонт {days} дней '
                       f'(до {edge:%Y-%m-%d}): {ex}')
    return True, f'календарь маршрута {route} покрывает {years}'


REG_HORIZON_DAYS = 10      # за сколько дней до ролла требовать целевую серию в реестре


def registry_horizon(book, today=None, days=REG_HORIZON_DAYS):
    """Несёт ли реестр ЦЕЛЕВЫЕ серии ближайшего ролла (двадцать второй круг, №12).

    Реестр пишет только first_connect, а автопилот его не запускает: после августовского
    ролла в книге Z26, ноябрьский перенос потребует H27 — реестр её не знает, ориентиров
    дальней серии не будет, и торговля встанет В ДЕНЬ РОЛЛА, оставив Z26 у декабрьской
    поставки. Проверка смотрит вперёд на `days` дней и даёт оператору время запустить
    first_connect утром, пока whatIf отдаёт и маржу.

    Возвращает (ok, сообщение).
    """
    import pandas as pd
    import daily as _DL
    if today is None:
        today = exchange_today()
    d0 = pd.Timestamp(today)
    held = [t for t in (getattr(book, 'ser_a', None), getattr(book, 'ser_b', None)) if t]
    if not held:
        return True, 'книга пуста — ролл не предстоит'
    try:
        hol = _DL.holidays_for(d0.year, d0.year + 1)
    except RuntimeError:
        hol = _DL.holidays_for(d0.year)
    reg = registry()
    problemy = []
    for tag in sorted(set(held)):
        try:
            dl = _DL.roll_deadline(tag, hol)
        except Exception as ex:
            problemy.append(f'{tag}: срок ролла не вычислен ({ex})')
            continue
        if not (0 <= (dl - d0).days <= days):
            continue                      # ролл не в горизонте — реестр ещё успеет
        target = _DL.target_tag(tag, dl, True)
        # серии книги не несут корня: одна и та же буква (U26) стоит у обеих ног, поэтому
        # целевую серию требуем у ВСЕХ трёх корней — сетка ноги А держит ES и MES, нога Б
        # держит ZN; лишний корень в требовании не вредит (реестр first_connect пишет все).
        need = [f'{r}{target}' for r in ('ES', 'MES', 'ZN')]
        miss = [x for x in need if x not in reg]
        if miss:
            problemy.append(f'ролл {tag} {dl:%d.%m} требует {need}, в реестре нет {miss}')
    if problemy:
        return False, ('реестр не готов к роллу: ' + '; '.join(problemy) +
                       ' — запустить first_connect (утром: whatIf отдаёт и маржу)')
    return True, f'реестр покрывает роллы ближайших {days} дней'


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
    raw = reg.read_bytes()
    out = {r['instrument']: r for r in csv.DictReader(raw.decode('utf-8').splitlines())}
    # ОДНО ПОКОЛЕНИЕ РЕЕСТРА НА СЕССИЮ (двадцать третий круг, №21). Реестр читается в
    # сессии ТРИЖДЫ: при создании IBBroker, в build_market и в reference_prices. Между
    # чтениями first_connect может атомарно заменить файл — он не берёт ни книжного, ни
    # сессионного замка. Тогда решение и ориентиры относятся к con_id поколения B, а
    # заявка уходит по con_id поколения A; особенно опасна именно регенерация, ИСПРАВЛЯЮЩАЯ
    # ошибочную серию или листинговую линию. Хэш первого чтения запоминается в процессе;
    # смена содержимого внутри одного процесса — отказ, а не тихая склейка поколений.
    # ПИН ПО ФАЙЛУ, А НЕ ПО ПРОЦЕССУ (двадцать первый круг №21; уточнено 24-м). Опасность —
    # ПОДМЕНА ОДНОГО И ТОГО ЖЕ реестра между чтениями внутри сессии (first_connect не берёт
    # замка). Разные пути — законный случай: батарея и SAME_API заводят свои фикстуры, и
    # запрет «один реестр на процесс» ронял их при СОВПАДАЮЩЕМ содержимом. Помним хэш
    # каждого пути отдельно.
    import hashlib as _hl
    _h = _hl.sha256(raw).hexdigest()
    _pins = globals().setdefault('_REG_PIN', {})
    _prev = _pins.get(str(reg))
    if _prev is None:
        _pins[str(reg)] = _h
    elif _prev != _h:
        raise FeedError(
            f'реестр {reg} ИЗМЕНИЛСЯ во время сессии (было {_prev[:12]}, стало '
            f'{_h[:12]}): решение и заявка ушли бы по con_id разных поколений — '
            f'сессия остановлена; перезапуск после завершения first_connect')
    return out


def contract_of(ib, name, reg=None):
    from ib_insync import Contract
    reg = reg or registry()
    if name not in reg:
        raise FeedError(f'{name}: нет в реестре — обновить first_connect.py')
    c = Contract(conId=int(reg[name]['con_id']))
    ib.qualifyContracts(c)
    # ИМЯ СВЕРЯЕТСЯ С ПОСТАВКОЙ НЕЗАВИСИМО ОТ СТРОКИ РЕЕСТРА (тридцатый круг, №3):
    # mismatches сравнивает контракт с той же строкой, откуда взят con_id, — согласованно
    # подменённая строка проходит её целиком. Серию имени проверяет только series_mismatch.
    bad = (CT.mismatches(c, reg[name]) + CT.series_mismatch(name, c, reg[name])
           + CT.verify_isin(ib, c, reg[name]))
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


def closes(ib, contract, today, expected_prev=None, min_prev=None):
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
    # ДОПУСК В КАЛЕНДАРНЫХ ДНЯХ — ТОЛЬКО КОГДА КАЛЕНДАРЬ НЕИЗВЕСТЕН (двадцать третий круг,
    # №8). Плоские пять дней отвергали ПРАВИЛЬНЫЙ бар на длинных праздничных связках: для
    # 29.12.2026 предыдущая европейская сессия — 23.12 (24, 25 и 28 декабря — праздники
    # LSE/SIX, между ними выходные), разрыв ШЕСТЬ дней, и маршрут Е встал бы детерминированно
    # ещё до сверки с expected_prev. Когда календарь известен (expected_prev задан), он и
    # есть точная проверка — грубый допуск в этом случае не нужен и только мешает.
    if expected_prev is None and gap > MAX_BAR_GAP_D:
        raise FeedError(f'[STALE_BAR] {contract.symbol}: последнее закрытие {d_prev:%d.%m.%Y}, '
                        f'это {gap} дней назад — источник отстал, сессия не считается')
    # ТОЧНАЯ ПРЕДЫДУЩАЯ СЕССИЯ, А НЕ ДОПУСК. Пятничный бар во вторник имеет возраст четыре
    # дня и проходил пятидневный допуск, хотя понедельничная сессия уже состоялась: цель
    # обеих ног считалась по позапрошлому закрытию. Календарь известен — сверяется он.
    # НИЖНЯЯ ГРАНИЦА (№9): бар обязан быть НЕ СТАРШЕ общей сессии; более свежий бар
    # площадки, работавшей в одиночку, законен.
    if min_prev is not None and d_prev < min_prev:
        raise FeedError(f'[STALE_BAR] {contract.symbol}: закрытие {d_prev:%d.%m.%Y} '
                        f'СТАРШЕ последней общей сессии {min_prev:%d.%m.%Y} — источник '
                        f'отстал, вход недостоверен')
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


def signal_state(today, path=None, holidays=None):
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
    # СВЕРКА DIGEST ПРИ КАЖДОМ ЧТЕНИИ (двадцать девятый круг, №2): ряд определяет позицию
    # целиком, и до сих пор был единственным источником истины без защиты целостности.
    # Отсутствие файла digest — не отказ (старые ряды и исследовательский снимок его не
    # имеют), но РАСХОЖДЕНИЕ — отказ: значит ряд правили мимо signal_update.
    _dp = Path(str(p) + '.sha256')
    if _dp.exists():
        import hashlib as _hl
        _want = _dp.read_text(encoding='utf-8').strip()
        _got = _hl.sha256(Path(p).read_bytes()).hexdigest()
        if _want and _want != _got:
            raise FeedError(f'{p}: сигнальный ряд не совпадает с digest (записан '
                            f'{_want[:12]}, факт {_got[:12]}) — ряд правили мимо '
                            f'signal_update, торговля запрещена (О-5)')
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
    # календарь МАРШРУТА (двенадцатый круг, №5): для Е подаются европейские праздники
    hol = holidays if holidays is not None else _DL.holidays_for(t.year)
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
    # УДЕРЖИВАЕМАЯ СЕРИЯ НЕ ТРЕБУЕТ КАЛЕНДАРЯ ВОВСЕ (двадцать второй круг, №14): прежний
    # порядок сначала запрашивал holidays_for(год, год+1) и в 2028 валил и сбор рынка, и
    # замыкание отсутствием таблицы 2029 — при уже ИЗВЕСТНОЙ ser_a. Календарь нужен
    # только ветке вычисления серии с нуля; ей достаточно СВОЕГО года, если следующий не
    # покрыт (расчёт серии смотрит вперёд не дальше ролловой границы своего года).
    _known = getattr(book, 'ser_a', None)
    if _known:
        return _known
    try:
        hol = DL.holidays_for(d0.year, d0.year + 1)
    except RuntimeError:
        hol = DL.holidays_for(d0.year)
    return DL.target_tag(
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
    if route == 'E':
        try:
            _sig_hol = eu_holidays(d0.year, d0.year + 1)
        except FeedError:
            _sig_hol = eu_holidays(d0.year)
    else:
        _sig_hol = hol
    st_eq, st_bd, sig_date, age = signal_state(d0, holidays=_sig_hol)
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
        try:
            _hol_eu = eu_holidays(d0.year, d0.year + 1)
        except FeedError:
            _hol_eu = eu_holidays(d0.year)
        _exp_e = prev_session(d0, _hol_eu)
        # ОБЩАЯ СЕССИЯ — НИЖНЯЯ ГРАНИЦА, А НЕ ТОЧНОЕ РАВЕНСТВО (двадцать пятый круг, №9).
        # Контур пропускает день, когда закрыта хотя бы одна площадка, но затем требовал,
        # чтобы последний бар КАЖДОГО фонда был именно последней ОБЩЕЙ сессией. У площадки,
        # работавшей в одиночку, бар СВЕЖЕЕ — и он отвергался. После 1 и 4 мая 2026 общая
        # предыдущая сессия — 30 апреля, но у CSPX есть бар 1 мая, у CBU0 — 4 мая, и 5 мая
        # маршрут Е встал бы детерминированно. Требуем: бар НЕ СТАРШЕ общей сессии (иначе
        # источник отстал) и не новее сегодняшнего дня — а совпадение дат двух фондов
        # проверяется отдельно ниже и остаётся обязательным.
        pe, de, pe_t, _ = closes(ib, contract_of(ib, 'CSPX', reg), d0, expected_prev=None,
                                 min_prev=_exp_e)
        pb, db, pb_t, _ = closes(ib, contract_of(ib, 'CBU0', reg), d0, expected_prev=None,
                                 min_prev=_exp_e)
        # РАЗНЫЕ ДАТЫ ЗАКОННЫ ПРИ ОДНОСТОРОННЕМ ПРАЗДНИКЕ (двадцать шестой круг, №9).
        # Требование de == db отменяло исправление общей сессии: на 05.05.2026 у CSPX бар
        # 01.05 (LSE работала), у CBU0 — 04.05 (SIX работала), общая сессия — 30.04, и
        # маршрут Е вставал. Обе даты обязаны быть НЕ СТАРШЕ общей сессии — это уже
        # проверено выше через min_prev; здесь остаётся запрет на РАЗБЕГ больше недели,
        # который означал бы, что один источник действительно отстал.
        if abs((de - db).days) > 7:
            raise FeedError(f'даты закрытий разошлись более чем на неделю: CSPX '
                            f'{de:%d.%m.%Y}, CBU0 {db:%d.%m.%Y} — источник отстал')
        # календарь ОБЪЕДИНЕНИЯ площадок (№13): проверка пропущенной сессии в run_session
        # обязана знать европейские праздники, иначе после Пасхи контур встал бы.
        try:
            _hol_e = eu_holidays(d0.year, d0.year + 1)
        except FeedError:
            _hol_e = eu_holidays(d0.year)
        m = DL.MarketE(date=d0, px_eq_prev=pe, px_bd_prev=pb,
                       px_eq_today=pe_t if pe_t is not None else pe,
                       px_bd_today=pb_t if pb_t is not None else pb,
                       st_eq=st_eq, st_bd=st_bd, holidays=tuple(_hol_e))
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
    # ЛИЧНОСТЬ УДЕРЖИВАЕМОГО ZN ПРОВЕРЯЕТСЯ КАЖДУЮ СЕССИЮ (двадцать четвёртый круг, №20).
    # Прежде build_market квалифицировал только ES: ошибочный con_id или чужая поставка ноги
    # Б молча считались допустимыми в дни без заявок, и отказ приходил лишь В ДЕНЬ РОЛЛА,
    # когда старую серию уже пора закрывать. Контракт удерживаемой серии обязан сходиться
    # с реестром ежедневно — это дешёвый запрос и он ловит подмену задолго до поставки.
    if getattr(book, 'ser_b', None):
        contract_of(ib, f'ZN{book.ser_b}', reg)
    # И ФАКТИЧЕСКИ УДЕРЖИВАЕМЫЙ MES (двадцать шестой круг, №8). Ежедневно сверялись ES и
    # ZN, а MES — нет, хотя сетка ноги А держится именно в нём: ошибочный conId проходил
    # сверку по внутреннему имени, получал last_session и замыкался по SPY вплоть до ролла
    # или поставочной зоны. Ошибка в reference_prices превращалась лишь в «ОРИЕНТИР-НЕТ»,
    # который сессия без заявок по MES игнорирует.
    if getattr(book, 'ser_a', None) and getattr(book, 'unit_is_mes', False):
        contract_of(ib, f'MES{book.ser_a}', reg)
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
    # ТОЧНАЯ ПРЕДЫДУЩАЯ СЕССИЯ И ДЛЯ ОРИЕНТИРОВ (девятнадцатый круг, №16): без неё ориентир
    # дальней MES/ZN-серии мог быть на сессию старше — пятидневный допуск это пропускал, и
    # сверка §7 на роллах систематически мерила издержки от чужого закрытия.
    if route == 'E':
        try:
            _hol = eu_holidays(today.year, today.year + 1)
        except FeedError:
            _hol = eu_holidays(today.year)
    else:
        import daily as _DL
        try:
            _hol = _DL.holidays_for(today.year, today.year + 1)
        except RuntimeError:
            _hol = _DL.holidays_for(today.year)
    _exp_prev = prev_session(today, _hol)
    out = {}
    for name, r in reg.items():
        root = ''.join(ch for ch in name if not ch.isdigit())[:3]
        if not any(name.startswith(w) for w in want):
            continue
        try:
            # НИЖНЯЯ ГРАНИЦА, А НЕ ТОЧНОЕ РАВЕНСТВО (двадцать шестой круг, №9): здесь
            # осталось точное expected_prev, и при одностороннем празднике оба СВЕЖИХ
            # ориентира отбрасывались — маршрут Е получал «ОРИЕНТИР-НЕТ» на всё сразу.
            px, _, _, _ = closes(ib, contract_of(ib, name, reg), today,
                                 expected_prev=None, min_prev=_exp_prev)
            out[name] = px
        except FeedError as ex:
            # НЕ МОЛЧА (восемнадцатый круг, №17): пропавший ориентир выбрасывал строку из
            # §7 без следа — выборка редела выборочно. Пропуск объявляется; ТРЕБОВАТЬ
            # ориентир обязан ВЫЗЫВАЮЩИЙ для тех инструментов, которыми торгует.
            out[f'ОРИЕНТИР-НЕТ:{name}'] = str(ex)[:80]
            continue
    return out


def e_close_gate(today=None):
    """Ворота замыкания маршрута Е на ДАТУ: максимум лондонского 16:35 и цюрихского 17:35,
    приведённых к Чикаго (DST-безопасно). ЕДИНСТВЕННЫЙ источник и для замыкателя, и для
    автопилота (семнадцатый круг, №5): фиксированное 10:40 в тике ломалось в недели
    рассинхронизации DST — в марте фактические ворота ~11:35, тик получал отказ «рано» и
    ставил тревогу вместо ожидания."""
    import pandas as pd
    if today is None:
        today = exchange_today()
    lon = pd.Timestamp(f'{today:%Y-%m-%d} 16:35', tz='Europe/London').tz_convert(EXCHANGE_TZ)
    zur = pd.Timestamp(f'{today:%Y-%m-%d} 17:35', tz='Europe/Zurich').tz_convert(EXCHANGE_TZ)
    return max(lon, zur)


def common_window_till(today=None):
    """Край ОБЩЕГО окна LSE/SIX и CME для перехода между маршрутами (двадцатый круг, №7).

    Симметрична e_close_gate и намеренно зеркальна ей: там max (обе площадки уже
    закрылись — можно замыкать), здесь min (обе ещё открыты — можно переводить книгу).
    Связывающее ограничение — европейская сторона: фьючерсы CME торгуются почти
    круглосуточно, а CSPX (LSE, 16:30) и CBU0 (EBS/SIX, 17:30) закрываются первыми.

    Прежде окно перехода было БУЛЕВЫМ аргументом in_common_window, проверенным ОДИН раз
    до preview, preflight и сотен заявок; 15-минутный тайм-аут относился к отдельному
    лоту, а не к закрытию площадки. Переход, начатый перед границей, продолжал продавать
    фьючерсы и покупать фонды после закрытия европейской стороны — непарная рыночная
    позиция ровно того рода, ради которой существует лимит §8б.
    """
    import pandas as pd
    if today is None:
        today = exchange_today()
    lon = pd.Timestamp(f'{today:%Y-%m-%d} 16:30', tz='Europe/London').tz_convert(EXCHANGE_TZ)
    zur = pd.Timestamp(f'{today:%Y-%m-%d} 17:30', tz='Europe/Zurich').tz_convert(EXCHANGE_TZ)
    return min(lon, zur)


def common_window(today=None):
    """ОБЩЕЕ окно LSE/SIX и CME: (начало, конец) в биржевой зоне, либо отказ.

    Двадцать первый круг, №2: прежде считался только ВЕРХНИЙ край, а нижний и торговый
    день не проверялись вовсе — переход мог пойти в праздник, в выходной или до открытия
    Европы, продав фьючерс и оставив GTC-покупку фонда до следующей сессии.

    Начало — позднейшее из открытий (LSE 08:00 Лондона, SIX 09:00 Цюриха): работать можно,
    когда открыты ОБЕ. Конец — ранейшее из закрытий (common_window_till). Праздник любой
    из сторон или выходной — отказ, а не пустое окно.
    """
    import pandas as pd
    import daily as _DL
    if today is None:
        today = exchange_today()
    d = pd.Timestamp(today).normalize()
    if d.weekday() >= 5:
        raise FeedError(f'{d:%Y-%m-%d} — выходной: общего окна LSE/CME нет')
    if d in {pd.Timestamp(x).normalize() for x in _DL.holidays_for(d.year)}:
        raise FeedError(f'{d:%Y-%m-%d} — праздник CME: общего окна нет')
    if d in {pd.Timestamp(x).normalize() for x in eu_holidays(d.year)}:
        raise FeedError(f'{d:%Y-%m-%d} — праздник LSE/SIX: общего окна нет')
    lon_o = pd.Timestamp(f'{d:%Y-%m-%d} 08:00', tz='Europe/London').tz_convert(EXCHANGE_TZ)
    zur_o = pd.Timestamp(f'{d:%Y-%m-%d} 09:00', tz='Europe/Zurich').tz_convert(EXCHANGE_TZ)
    return max(lon_o, zur_o), common_window_till(d)


TRADE_TILL = {'F': '1530', 'E': '0945'}      # конец торгового окна по маршруту, Чикаго

# СОКРАЩЁННЫЕ СЕССИИ (двадцать первый круг, №6). Календарь знает только ПОЛНЫЕ выходные, а
# CME закрывается рано в ряд дней — после Дня благодарения, в сочельник, иногда 3 июля; у
# ES и ZN часы при этом РАЗНЫЕ. После фактического раннего закрытия контур продолжал
# считать окно открытым до 15:30, и заявка с tif=GTC + outsideRth могла дожить до вечерней
# переоткрытой сессии или до следующего дня.
#
# ТАБЛИЦА ПУСТА НАМЕРЕННО. Часы сокращённых сессий — данные биржи, и выдумывать их по
# памяти нельзя ровно по той же причине, что и праздники (см. HOLIDAYS_EU): ошибка на
# полчаса означает заявку в закрытый рынок. Заполняется по официальному календарю
# CME/CBOT перед живым этапом; до тех пор пробел назван в §12, а механизм уже работает.
SHORT_SESSIONS = {}          # 'YYYY-MM-DD' -> 'HHMM' конца окна маршрута Ф


def trade_till(route='F', today=None):
    """Конец торгового окна маршрута в виде HHMM — ЕДИНЫЙ источник для автопилота и для
    контура (двадцатый круг, №6).

    Прежде окно жило ТОЛЬКО в autopilot.sh и проверялось один раз перед запуском
    session.py, а внутри сессии идут исторические запросы, ориентиры, снимки позиций и
    последовательные ожидания заявок до 120 с каждое. Заявка второй ноги могла уйти
    брокеру уже за краем окна, и при tif=GTC + outsideRth она либо висела до чужой
    сессии, либо исполнялась отдельно от первой — непарная позиция на ночь.

    Сокращённая сессия (двадцать первый круг, №6) переопределяет край на свою дату.
    """
    t = TRADE_TILL.get(route)
    if not t:
        raise FeedError(f'неизвестный маршрут {route!r}: конец торгового окна не определён')
    if route == 'F':
        d = exchange_today() if today is None else today
        short = SHORT_SESSIONS.get(f'{d:%Y-%m-%d}')
        if short:
            return short
    return t
# Запас до края окна, требуемый ПЕРЕД началом подачи заявок. Операционная величина (как
# 35 дней давности замера маржи, 390 заявок в день и 15 минут на пару перехода), а не
# параметр стратегии: сессия из нескольких последовательных заявок с ожиданием
# терминального статуса не имеет права начинаться за минуту до края окна.
# ЗАПАС ДО КРАЯ ОКНА — ПО ХУДШЕМУ ЗАКОННОМУ ПУТИ (двадцать четвёртый круг, №10). Три
# минуты не покрывали НИЧЕГО: полный ролл упаковки ES+MES+ZN — до шести последовательных
# заявок, каждая place() ждёт подтверждения до 120 с плюс двухсекундный снимок котировки и
# квалификация контракта. Законный худший путь превышает 12 минут, и сессия, начатая за
# четыре минуты до края, закрывала старую серию, а новую подавала уже после закрытия либо
# оставляла GTC на ночь. Берём 15 минут: больше худшего пути, заведомо меньше окна.
TRADE_MARGIN_MIN = 15


def trade_deadline(route='F', today=None):
    """Момент конца торгового окна на СЕГОДНЯ в биржевой зоне (DST-безопасно)."""
    import pandas as pd
    if today is None:
        today = exchange_today()
    hhmm = trade_till(route, today)
    return pd.Timestamp(f'{today:%Y-%m-%d} {hhmm[:2]}:{hhmm[2:]}', tz=EXCHANGE_TZ)


def closing_values(ib, route, book):
    """Фактические цены ЗАКРЫТИЯ текущей сессии — для замыкания §1.

    Требуется именно СЕГОДНЯШНИЙ завершённый бар. Пока его нет, замыкать нечем, и молча
    подставить вчерашнее закрытие означало бы посчитать триггер капа от неверной величины —
    ровно тот дефект, ради которого замыкание вынесено в отдельный запуск.
    """
    import pandas as pd
    now = pd.Timestamp.now(tz=EXCHANGE_TZ)
    today = exchange_today()          # нужен воротам Е ДО остальной логики
    # ВОРОТА ПО МАРШРУТУ (двенадцатый круг, №3): Е замыкается ПОСЛЕ ЕВРОПЕЙСКОГО закрытия
    # (~10:30 Чикаго), а не после американского — прежнее безусловное «после 16:00» делало
    # заявленное окно Е технически недостижимым: первый же вызов в 10:40 падал, ставил
    # тревогу и оставлял маржинальную книгу без ежедневного управления.
    if route == 'E':
        # ОТ ФАКТИЧЕСКОГО ЕВРОПЕЙСКОГО ЗАКРЫТИЯ (тринадцатый круг, №4): фиксированные 10:35
        # Чикаго ломаются в недели рассинхронизации DST (США уже перешли, Европа ещё нет —
        # закрытие уезжает на час, и незавершённый бар шёл в триггер капа). Единый источник
        # ворот — e_close_gate (его же читает автопилот, семнадцатый круг, №5).
        _gate_ts = e_close_gate(today)
        ok_time = now >= _gate_ts
        gate = f'{_gate_ts:%H:%M} (европейское закрытие)'
    else:
        ok_time = now.hour >= CLOSE_AFTER_H
        gate = f'{CLOSE_AFTER_H}:00'
    if not ok_time:
        raise FeedError(f'сейчас {now:%H:%M} по бирже ({EXCHANGE_TZ}) — торги маршрута ещё '
                        f'идут; дневной бар существует, но НЕ ЗАВЕРШЁН. Замыкание после {gate}')
    reg = registry()
    if route == 'E':
        # ОБЩАЯ СЕССИЯ — НИЖНЯЯ ГРАНИЦА И ПРИ ЗАМЫКАНИИ (двадцать восьмой круг, №18;
        # открытый долг №11 двадцать седьмого). Здесь closes звался БЕЗ календаря, поэтому
        # применялся плоский допуск в пять дней: после связки LSE/SIX (29.12.2026 — разрыв
        # шесть дней; 1 и 4 мая 2026) замыкание падало ровно там же, где падал сбор рынка.
        # Исправление сбора без замыкания оставляло книгу Е незамкнутой — то есть кап §1
        # считался бы по чужому закрытию.
        try:
            _hol_c = eu_holidays(today.year, today.year + 1)
        except FeedError:
            _hol_c = eu_holidays(today.year)
        _exp_c = prev_session(today, _hol_c)
        _, _, pe_t, d1 = closes(ib, contract_of(ib, 'CSPX', reg), today,
                                expected_prev=None, min_prev=_exp_c)
        _, _, pb_t, d2 = closes(ib, contract_of(ib, 'CBU0', reg), today,
                                expected_prev=None, min_prev=_exp_c)
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
    return spy_t, dref, None, dict(spy_close=(spy_t, str(d_spy.date())),
                                   es_close=(es_t, str(d1.date())),
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
