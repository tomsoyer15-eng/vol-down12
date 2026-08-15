#!/usr/bin/env python3
"""Ежемесячное обновление сигнала §1 из живого источника — последний непрограммный слой.

ЧТО СЧИТАЕТСЯ. Сигнал §1 — «месячное закрытие выше 12-месячной средней» по индексу каждой
ноги. Исторический индекс — склейка источников, но с 1993 (нога А, SPY) и с 2002 (нога Б,
IEF) он продолжается ДОХОДНОСТЯМИ этих фондов: внутри любого 13-месячного окна нашей эпохи
склейка пропорциональна скорректированной цене фонда, поэтому сравнение с SMA по
ADJUSTED_LAST воспроизводит модель ПРИ НЕИЗМЕННОМ источнике; дрейф ловится сверками
уровней и перекрытия, пограничный свежий месяц требует подтверждения. Никакой новой конструкции здесь нет —
только те же ряды из живого источника.

ЗАЧЕМ ОТДЕЛЬНЫЙ ЖИВОЙ ФАЙЛ. Пакетный signals_monthly.csv — артефакт исследовательского
конвейера: он сверяется самопроверкой побитово и содержит строку текущего месяца,
посчитанную по неполным данным. Живой ряд (~/.addfut/signals_live.csv) содержит ТОЛЬКО
завершённые месяцы и дописывается здесь.

ЗАЩИТА ОТ ДРЕЙФА ИСТОЧНИКА. Перед дописыванием пересчитываются ПОСЛЕДНИЕ 12 УЖЕ ЗАПИСАННЫХ
месяцев, и каждый обязан совпасть с файлом. Расхождение — отказ и тревога: молча уехавший
источник изменил бы стратегию без единой ошибки. Незавершённый месяц не дописывается.
"""
import math
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tz                      # noqa: F401 — зоны IBKR до любых дат

def live_path():
    """Путь живого ряда ЧИТАЕТСЯ ПРИ ВЫЗОВЕ: модульная константа игнорировала окружение,
    выставленное после импорта, — стенды и autopilot работали бы по чужому файлу."""
    return Path(os.environ.get('ADDFUT_SIGNALS') or
                (Path(os.path.expanduser('~/.addfut')) / 'signals_live.csv'))
LEGS = (('leg_eq', 'SPY', 'ARCA'), ('leg_bond', 'IEF', 'NASDAQ'))
SMA = 12


class SignalError(RuntimeError):
    pass


def monthly_adjusted(ib, sym, primary, daily_out=None):
    """Месячные закрытия по ADJUSTED_LAST; текущий незавершённый месяц отбрасывается."""
    import pandas as pd
    from ib_insync import Stock, util
    c = Stock(sym, 'SMART', 'USD', primaryExchange=primary)
    ib.qualifyContracts(c)
    if not c.conId:
        raise SignalError(f'{sym}: контракт не подтверждён')
    # ДВА ГОДА: сроки свыше 12 месяцев IBKR принимает только в годах (ошибка 321);
    # запрос в днях длиннее года возвращает ПУСТУЮ историю без ошибки вовсе
    bars = ib.reqHistoricalData(c, endDateTime='', durationStr='2 Y',
                                barSizeSetting='1 day', whatToShow='ADJUSTED_LAST',
                                useRTH=True)
    if not bars:
        raise SignalError(f'{sym}: история пуста')
    df = util.df(bars).set_index('date')['close']
    df.index = __import__('pandas').to_datetime(df.index)
    me = df.resample('ME').last().dropna()
    import feed as FD
    today = FD.exchange_today()
    me = me[me.index < today.replace(day=1)]          # только ЗАВЕРШЁННЫЕ месяцы решения
    bad = [f'{d:%Y-%m}' for d, v in me.items() if not (math.isfinite(v) and v > 0)]
    if bad:
        raise SignalError(f'{sym}: недостоверные месячные закрытия {bad}')
    # Последний месяц проверяется ВСЕГДА (№16). Остальные ДОПИСЫВАЕМЫЕ месяцы проверяет
    # _update_locked через daily_out: только там известно, какие месяцы не заморожены
    # сайдкаром (двадцатый круг, №9). Вся история сюда не годится — праздничная таблица
    # покрывает 2026-2028, и проверка старых лет отказала бы на каждом обновлении.
    _verify_month_tail(sym, df, me, months=[me.index[-1]] if len(me) else [])
    if daily_out is not None:
        daily_out['df'] = df
    return me


def _verify_month_tail(sym, df, me, months=None):
    """КАЖДЫЙ ПРОВЕРЯЕМЫЙ МЕСЯЦ ОБЯЗАН КОНЧАТЬСЯ ПОСЛЕДНЕЙ СЕССИЕЙ (№16; двадцатый
    круг, №9): источник, потерявший одну-две последние сессии месяца, дал бы «месячное
    закрытие» серединой месяца, и из него записался бы сигнал.

    ДВАДЦАТЫЙ КРУГ, №9: прежде смотрелся ТОЛЬКО me.index[-1], хотя дописываться может до
    двенадцати месяцев сразу (перерыв в работе, бутстрап). Дыра в промежуточном месяце
    входила бы в SMA нетронутой, а перекрёстные срезы её не ловят: TRADES и MIDPOINT
    приходят от ТОГО ЖЕ поставщика и имеют ту же дыру. Цена ошибки — переключение целой
    ноги, то есть величина порядка NLV.
    """
    import pandas as pd
    import daily as _DL
    import feed as _FD
    if not len(me):
        return
    _no_cal = []
    for dm in (list(me.index) if months is None else list(months)):
        # КАЛЕНДАРЬ СПРАШИВАЕТСЯ ОТДЕЛЬНО. Ловить RuntimeError вокруг всей проверки нельзя:
        # SignalError — его НАСЛЕДНИК, и такой except глотал бы ровно тот отказ, ради
        # которого проверка существует (поймано батареей на стенде «хвост месяца потерян»).
        try:
            # ДВАДЦАТЬ ПЕРВЫЙ КРУГ, №11: спрашивался год+1, и для любого месяца 2028
            # падал запрос 2029 — весь 2028 объявлялся непокрытым, хотя он в таблице
            # ЕСТЬ. Покрытие проверяется по СВОЕМУ году; заглядывание в следующий —
            # забота _verify_one_month, у неё свой запасной путь.
            _DL.holidays_for(dm.year)
        except RuntimeError:
            # ПРАЗДНИЧНАЯ ТАБЛИЦА ПОКРЫВАЕТ 2026-2028 (§8, признанный предел). Окно SMA
            # уходит глубже, и требовать календарь на каждый месяц окна значит отказывать
            # на КАЖДОМ обновлении. Непроверенный месяц не проглатывается молча: он
            # называется вслух, а его достоверность держат независимые срезы TRADES и
            # MIDPOINT (_verify_fresh). Предел записан в §12.
            _no_cal.append(f'{dm:%Y-%m}')
            continue
        _verify_one_month(sym, df, dm, pd, _DL, _FD)
    if _no_cal:
        print(f'  {sym}: хвост месяцев {_no_cal} НЕ проверен — праздничный календарь их '
              f'не покрывает; достоверность держат только срезы TRADES/MIDPOINT')


def _verify_one_month(sym, df, dm, pd, _DL, _FD):
    try:
        hol = _DL.holidays_for(dm.year, dm.year + 1)
    except RuntimeError:
        hol = _DL.holidays_for(dm.year)
    expected_last = _FD.prev_session(dm + pd.Timedelta(days=1), hol)
    got_last = df.index[(df.index.year == dm.year) & (df.index.month == dm.month)].max()
    if pd.Timestamp(got_last).normalize() != expected_last:
        raise SignalError(f'{sym}: последний бар месяца {dm:%Y-%m} — '
                          f'{pd.Timestamp(got_last):%d.%m}, а последняя сессия '
                          f'{expected_last:%d.%m}; источник потерял хвост месяца')


BORDER_TOL = 0.001     # |закрытие/SMA − 1| в этой зоне — решение в пределах шума источника


DIV_YIELD_CAP = 0.06       # потолок ГОДОВОЙ дивидендной доходности SPY/IEF (история: SPY
                           # 1,2-1,8%, IEF 3-4,5%; 6% — консервативный запас). Не параметр
                           # стратегии, а физическая граница различимости: дивиденды окна
                           # «конец месяца - сегодня» не могут превысить её pro rata.


def _div_bound(d):
    """Максимальная дивидендная поправка окна (d, сегодня]: один квартальный купон
    (DIV_YIELD_CAP/4) плюс pro-rata хвост окна плюс допуск уровней."""
    import feed as FD
    days = max(0, int((FD.exchange_today() - d).days))
    return DIV_YIELD_CAP / 4.0 + DIV_YIELD_CAP * days / 365.0 + LEVEL_TOL


def _verify_border(sym, me, covered=None):
    """ПОГРАНИЧНЫЙ НОВЫЙ МЕСЯЦ НЕ ДОВЕРЯЕТСЯ АВТОМАТИЧЕСКИ (тринадцатый круг, №8): самый
    свежий месяц отсутствует в сайдкаре и уровневой сверкой не покрыт; если его закрытие
    стоит к SMA ближе зоны неопределённости, знак решения неотличим от погрешности
    поставщика. ЗОНА РАСШИРЕНА до дивидендной границы окна (шестнадцатый круг, №3):
    занижение свежего закрытия в пределах купона неотличимо от самого купона TRADES-сверкой,
    и если такое искажение способно сменить знак — знак стоит внутри этой зоны, а тогда
    дописывание требует явного подтверждения оператора."""
    import os as _os
    sma = me.rolling(SMA).mean()
    d = me.index[-1]
    if sma.isna().loc[d]:
        return
    # УЖЕ ЗАМОРОЖЕННЫЙ МЕСЯЦ ПЕРЕПОДТВЕРЖДАТЬ НЕ НАДО (найдено ЖИВЫМ КОНТУРОМ 14.08.2026).
    # Проверка стояла на последнем месяце решения БЕЗУСЛОВНО, в том числе когда он уже
    # записан в ряд и заморожен сайдкаром уровней. Из-за этого пограничный месяц блокировал
    # контур ВСЕ свои тридцать дней: 14.08 автопилот встал тревогой на июльском решении
    # (IEF -1,268% при зоне 1,83%), хотя строка 2026-08-31 была записана 13.08, исполнена
    # и подтверждена. Смысл защиты — не дать ДОПИСАТЬ сомнительный знак; к уже
    # записанному она отношения не имеет.
    if covered and d in covered:
        return
    zone = max(BORDER_TOL, _div_bound(d))
    margin = abs(me.loc[d] / sma.loc[d] - 1.0)
    if margin <= zone and _os.environ.get('ADDFUT_SIGNAL_CONFIRM') != '1':
        raise SignalError(f'{sym}: решение месяца {d:%Y-%m} пограничное '
                          f'({margin:+.3%} к SMA при зоне неопределённости {zone:.2%}) — '
                          f'автодописывание запрещено; подтвердить ADDFUT_SIGNAL_CONFIRM=1')


def _verify_fresh(ib, sym, primary, me, months):
    """КАЖДЫЙ МЕСЯЦ ВНЕ ЗАМОРОЖЕННОГО САЙДКАРА сверяется независимым срезом TRADES
    (шестнадцатый круг, №3; семнадцатый, №12): при пропуске нескольких месяцев в SMA
    свежего решения входят точки, не покрытые ни уровнями, ни прежней сверкой одного
    последнего месяца. Скорректированное закрытие НЕ СМЕЕТ превышать сырое сверх допуска
    уровней, а отставать — сверх дивидендной границы СВОЕГО окна (для старших месяцев
    граница честно шире: купонов в окне больше). Занижение в пределах купона добирает
    расширенная пограничная зона _verify_border. Сплит в окне даст кратное расхождение и
    честный отказ (SPY/IEF — событие редчайшее, разбор ручной)."""
    if not months:
        return
    import pandas as pd
    from ib_insync import Stock, util
    import feed as FD
    c = Stock(sym, 'SMART', 'USD', primaryExchange=primary)
    ib.qualifyContracts(c)
    if not getattr(c, 'conId', 0):
        raise SignalError(f'{sym}: контракт для TRADES-сверки не подтверждён')
    span = max(95, int((FD.exchange_today() - min(months)).days) + 40)
    dur = f'{span} D' if span <= 350 else '2 Y'   # >12 мес IBKR принимает только в годах
    bars = ib.reqHistoricalData(c, endDateTime='', durationStr=dur,
                                barSizeSetting='1 day', whatToShow='TRADES', useRTH=True)
    if not bars:
        raise SignalError(f'{sym}: TRADES-история пуста — месяцы '
                          f'{[f"{d:%Y-%m}" for d in months]} без независимого подтверждения')
    df = util.df(bars).set_index('date')['close']
    df.index = pd.to_datetime(df.index)
    mt = df.resample('ME').last().dropna()
    for d in months:
        if d not in mt.index:
            raise SignalError(f'{sym}: в TRADES нет месяца {d:%Y-%m} — месяц вне сайдкара '
                              f'остался без независимого подтверждения')
        rel = float(mt.loc[d]) / float(me.loc[d]) - 1.0
        bound = _div_bound(d)
        if not (-LEVEL_TOL <= rel <= bound):
            raise SignalError(f'{sym}: месяц {d:%Y-%m} разошёлся с независимым '
                              f'TRADES-закрытием ({rel:+.3%}; допустимо от {-LEVEL_TOL:.1%} '
                              f'до +{bound:.2%} дивидендного окна) — закрытие источника '
                              f'недостоверно')
    _verify_quotes(ib, c, sym, mt, months, dur)


MID_TOL = 0.005    # котировочное закрытие против сделочного: спред SPY/IEF — центы, 0,5%
                   # покрывает худший спред с запасом, но ловит порчу, менявшую знак у SMA


def _verify_quotes(ib, c, sym, mt, months, dur):
    """Срез КОТИРОВОК против среза СДЕЛОК (девятнадцатый круг, №5). ADJUSTED_LAST и
    TRADES — производные ОДНОГО дневного бара сделок того же conId: общая порча печати
    закрытия проходила их сверку с отношением около нуля, а _verify_border ловит лишь
    близость к SMA — ошибка, далеко перебросившая цену ЧЕРЕЗ SMA, принималась увереннее
    малой. MIDPOINT строится из потока котировок (bid/ask) — это другой ряд данных, и
    порча сделочного закрытия в нём не воспроизводится. ПРИЗНАННЫЙ ПРЕДЕЛ: поставщик
    один (IBKR); общесистемную порчу всех срезов изнутри не поймать — внешние источники
    с этой машины недоступны (§12)."""
    import pandas as pd
    from ib_insync import util
    if not months:
        return
    bars = ib.reqHistoricalData(c, endDateTime='', durationStr=dur,
                                barSizeSetting='1 day', whatToShow='MIDPOINT', useRTH=True)
    if not bars:
        raise SignalError(f'{sym}: MIDPOINT-история пуста — котировочного подтверждения '
                          f'месяцев {[f"{d:%Y-%m}" for d in months]} нет, обновление '
                          f'запрещено')
    dm = util.df(bars).set_index('date')['close']
    dm.index = pd.to_datetime(dm.index)
    mm = dm.resample('ME').last().dropna()
    for d in months:
        if d not in mm.index:
            raise SignalError(f'{sym}: в MIDPOINT нет месяца {d:%Y-%m} — месяц без '
                              f'котировочного подтверждения')
        rel = float(mm.loc[d]) / float(mt.loc[d]) - 1.0
        if abs(rel) > MID_TOL:
            raise SignalError(f'{sym}: месяц {d:%Y-%m} — сделочное закрытие разошлось с '
                              f'котировочным ({rel:+.3%} при допуске {MID_TOL:.1%}); общая '
                              f'порча сделочного среза, знак сигнала недостоверен')


def states(me):
    """Состояния по КОНВЕНЦИИ РЯДА: решение закрытием месяца M действует в месяце M+1 и
    помечается ЕГО концом — ровно как sim_v13.sigs (сдвиг MonthBegin(1)). Проверено на
    2022 годе: 12/12 совпадений со сдвигом, 11/12 без."""
    import pandas as pd
    sma = me.rolling(SMA).mean()
    dec = (me > sma)[sma.notna()].astype(int)
    dec.index = dec.index + pd.offsets.MonthEnd(1)
    return dec


def update(ib):
    import fcntl
    import pandas as pd
    LIVE = live_path()
    if not LIVE.exists():
        raise SignalError(f'нет живого ряда {LIVE} — посеять из пакетного до первого запуска')
    # БЛОКИРОВКА И АТОМАРНАЯ ПУБЛИКАЦИЯ (№18): дозапись без замка позволяла двум процессам
    # прочитать один хвост и записать дубликат месяца, а читателю — увидеть полстроки.
    lk = open(LIVE.with_suffix('.lock'), 'w')
    fcntl.flock(lk, fcntl.LOCK_EX)
    try:
        return _update_locked(ib, LIVE, pd)
    finally:
        fcntl.flock(lk, fcntl.LOCK_UN)
        lk.close()


LEVEL_TOL = 0.001          # 0,1%: после общего масштаба уровни обязаны совпасть точнее


def _levels_path(live):
    return live.with_name('signals_levels.csv')


def _check_levels(sym, live, me):
    """ПРОВЕРКА уровней БЕЗ записи (одиннадцатый круг, №10). Прежняя редакция сразу
    перезаписывала сайдкар — ДО сверки битов второй ноги и до публикации ряда: при сбое
    дальше по циклу текущие уровни поставщика становились новой базой доверия, и повторный
    запуск сравнивал источник с ним самим."""
    import pandas as pd
    lp = _levels_path(live)
    if not lp.exists():
        # ОТСУТСТВИЕ САЙДКАРА — ОТКАЗ (двенадцатый круг, №6): без уровней сверка сводится к
        # 12 битам, и уехавший источник закреплялся бы как новая норма. Первый запуск —
        # только явным разрешением оператора.
        if os.environ.get('ADDFUT_LEVELS_BOOTSTRAP') == '1':
            return
        raise SignalError(f'нет сайдкара уровней {lp}: сверка только по битам запрещена; '
                          f'первый запуск — с ADDFUT_LEVELS_BOOTSTRAP=1')
    df = pd.read_csv(lp, parse_dates=[0], index_col=0)
    # ЧАСТИЧНЫЙ САЙДКАР — ТОЖЕ ОТКАЗ (пятнадцатый круг, №5): файл без столбца нужной ноги
    # или без общих месяцев по силе равен отсутствующему — сверка уровней молча выключалась,
    # оставляя слабые 12 битов. Повреждение опубликованного сайдкара — не повод доверять.
    if sym not in df.columns:
        if os.environ.get('ADDFUT_LEVELS_BOOTSTRAP') == '1':
            return set()
        raise SignalError(f'сайдкар {lp} не содержит столбца {sym}: сверка уровней этой '
                          f'ноги невозможна; починка файла или ADDFUT_LEVELS_BOOTSTRAP=1')
    old = df[sym].dropna().to_dict()
    common = [d for d in me.index if d in old]
    if not common:
        if os.environ.get('ADDFUT_LEVELS_BOOTSTRAP') == '1':
            return set()
        raise SignalError(f'сайдкар {lp}: по {sym} нет общих месяцев с рядом поставщика — '
                          f'база сравнения пуста; починка файла или ADDFUT_LEVELS_BOOTSTRAP=1')
    # БАЗА НЕ КОРОЧЕ ПОРОГА ПЕРЕКРЫТИЯ (семнадцатый круг, №12): один общий месяц с
    # одиннадцатью дырами «проходил» как полноценная уровневая база. Порог жёсткий — 6,
    # как у сверки битов; усечённый файл неотличим от «молодого», поэтому мягких скидок
    # нет: молодой сайдкар первых месяцев разрешает только оператор бутстрапом.
    if len(common) < 6:
        raise SignalError(f'{sym}: уровневая база коротка — общих месяцев {len(common)} '
                          f'при записанных {len(old)}; сверка неубедительна, починка '
                          f'сайдкара или ADDFUT_LEVELS_BOOTSTRAP=1')
    # ЗАПИСАННЫЙ МЕСЯЦ ИСЧЕЗ ИЗ РЯДА ПОСТАВЩИКА — источник дефектен, а не «не общий».
    lo, hi = me.index[0], me.index[-1]
    holes = [f'{d:%Y-%m}' for d in old if lo <= d <= hi and d not in me.index]
    if holes:
        raise SignalError(f'{sym}: в ряду поставщика пропали записанные месяцы '
                          f'{holes[:4]} — источник дефектен')
    ratios = [me.loc[d] / old[d] for d in common]
    k = sorted(ratios)[len(ratios) // 2]
    bad = [f'{d:%Y-%m}: {me.loc[d]/old[d]/k-1:+.3%}' for d in common
           if abs(me.loc[d] / old[d] / k - 1) > LEVEL_TOL]
    if bad:
        raise SignalError(f'{sym}: УРОВНИ ряда разошлись с записанными сверх общего '
                          f'множителя — {bad[:4]}; поставщик пересчитал историю, сигнал '
                          f'пограничного месяца недостоверен')
    return set(common)


def _commit_levels(live, pending):
    """Фиксация сайдкара — ТОЛЬКО после успешного обновления обеих ног и публикации ряда."""
    import pandas as pd
    import tempfile
    lp = _levels_path(live)
    rows = {}
    if lp.exists():
        df = pd.read_csv(lp, parse_dates=[0], index_col=0)
        rows = {d: dict(r) for d, r in df.iterrows()}
    for sym, me in pending.items():
        for d, v in me.items():
            # ЭТАЛОН ЗАМОРОЖЕН (четырнадцатый круг, №7): записанный месяц не обновляется —
            # иначе поставщик сдвигал бы историю по 0,09% за прогон, и накопленный дрейф
            # прошёл бы все пороги, ни разу их не нарушив. Дописываются только НОВЫЕ месяцы.
            if sym in rows.get(d, {}):
                continue
            rows.setdefault(d, {})[sym] = float(v)
    cols = sorted({c for r in rows.values() for c in r})
    fd, tmp = tempfile.mkstemp(dir=str(lp.parent), suffix='.tmp')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(',' + ','.join(cols) + '\n')
        for d in sorted(rows):
            f.write(f'{d:%Y-%m-%d},' + ','.join(
                ('' if c not in rows[d] else f'{rows[d][c]:.6f}') for c in cols) + '\n')
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, lp)


def _verify_overlap(sym, col, ref, st):
    """ВСЕ ДВЕНАДЦАТЬ последних записанных месяцев обязаны воспроизвестись (№17): порог
    «хотя бы шесть» позволял источнику с коротким окном пройти сверку половиной истории.
    Совпадение битов не доказывает пропорциональность рядов — предел признан; строгость
    окна хотя бы не даёт сузить его ещё и по длине."""
    overlap = [d for d in ref.index[-12:] if d in st.index]
    if len(overlap) < 12:
        raise SignalError(f'{sym}: перекрытие с живым рядом {len(overlap)} мес из 12 — '
                          f'источник слишком короток или ряд разрежен')
    diff = [f'{d:%Y-%m}: файл {int(ref.loc[d, col])}, источник {int(st.loc[d])}'
            for d in overlap if int(ref.loc[d, col]) != int(st.loc[d])]
    if diff:
        raise SignalError(f'{sym}: источник РАЗОШЁЛСЯ с записанной историей — {diff}; '
                          f'молча уехавший источник менял бы стратегию без ошибки')


def _update_locked(ib, LIVE, pd):
    ref = pd.read_csv(LIVE, parse_dates=[0], index_col=0).sort_index()

    fresh = {}
    pending_levels = {}
    for col, sym, primary in LEGS:
        _daily = {}
        me = monthly_adjusted(ib, sym, primary, daily_out=_daily)
        covered = _check_levels(sym, LIVE, me) or set()   # ПРОВЕРКА без записи (11-й, №10)
        _verify_border(sym, me, covered)   # пограничный свежий месяц (тринадцатый, №8)
        # ВСЕ месяцы SMA-окна вне замороженного сайдкара — под независимый срез TRADES
        # (шестнадцатый круг, №3; семнадцатый, №12 — пропуск нескольких месяцев).
        fresh_months = [d for d in me.index[-SMA:] if d not in covered]
        # И КАЖДЫЙ ИЗ НИХ — ПОД КАЛЕНДАРНУЮ ПРОВЕРКУ ХВОСТА (двадцатый круг, №9): прежде
        # смотрелся только последний месяц, а дыра в промежуточном входила в SMA целой.
        # Перекрёстные срезы её не ловят: TRADES и MIDPOINT — тот же поставщик, та же дыра.
        _verify_month_tail(sym, _daily['df'], me, months=fresh_months)
        _verify_fresh(ib, sym, primary, me, fresh_months)
        pending_levels[sym] = me
        st = states(me)
        _verify_overlap(sym, col, ref, st)
        fresh[col] = st

    new_months = sorted(set(fresh['leg_eq'].index) & set(fresh['leg_bond'].index)
                        - set(ref.index))
    new_months = [d for d in new_months if d > ref.index[-1]]
    if not new_months:
        _commit_levels(LIVE, pending_levels)
        return []
    # ПОЛНАЯ АТОМАРНАЯ ПЕРЕЗАПИСЬ (№18): временный файл, fsync, os.replace — читатель видит
    # либо старый файл целиком, либо новый целиком, и никогда полстроки.
    import tempfile
    rows = ref.copy()
    for d in new_months:
        rows.loc[d] = [int(fresh['leg_eq'].loc[d]), int(fresh['leg_bond'].loc[d])]
    rows = rows.sort_index()
    fd, tmp = tempfile.mkstemp(dir=str(LIVE.parent), suffix='.tmp')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(',' + ','.join(rows.columns) + '\n')
        for d, r in rows.iterrows():
            f.write(f'{d:%Y-%m-%d},{int(r.iloc[0])},{int(r.iloc[1])}\n')
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, LIVE)
    _commit_levels(LIVE, pending_levels)
    return [(f'{d:%Y-%m-%d}', int(fresh['leg_eq'].loc[d]), int(fresh['leg_bond'].loc[d]))
            for d in new_months]


if __name__ == '__main__':
    from ib_insync import IB
    ib = IB()
    ib.connect(os.environ.get('IB_HOST', '127.0.0.1'),
               int(os.environ.get('IB_PORT', '4002')), clientId=37, timeout=30)
    ib.reqMarketDataType(3)
    try:
        added = update(ib)
        if added:
            for d, e, b in added:
                print(f'дописан месяц {d}: нога А={e}, нога Б={b}')
        else:
            print('новых завершённых месяцев нет; перекрытие с историей сверено — совпало')
    finally:
        ib.disconnect()
