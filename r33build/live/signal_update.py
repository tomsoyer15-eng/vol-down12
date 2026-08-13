#!/usr/bin/env python3
"""Ежемесячное обновление сигнала §1 из живого источника — последний непрограммный слой.

ЧТО СЧИТАЕТСЯ. Сигнал §1 — «месячное закрытие выше 12-месячной средней» по индексу каждой
ноги. Исторический индекс — склейка источников, но с 1993 (нога А, SPY) и с 2002 (нога Б,
IEF) он продолжается ДОХОДНОСТЯМИ этих фондов: внутри любого 13-месячного окна нашей эпохи
склейка пропорциональна скорректированной цене фонда, поэтому сравнение с SMA по
ADJUSTED_LAST из IBKR воспроизводит модель ТОЧНО. Никакой новой конструкции здесь нет —
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


def monthly_adjusted(ib, sym, primary):
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
    _verify_month_tail(sym, df, me)
    return me


def _verify_month_tail(sym, df, me):
    """ПОСЛЕДНИЙ МЕСЯЦ РЕШЕНИЯ ОБЯЗАН КОНЧАТЬСЯ ПОСЛЕДНЕЙ СЕССИЕЙ (№16): источник,
    потерявший одну-две последние сессии месяца, дал бы «месячное закрытие» серединой
    месяца, и из него записался бы сигнал."""
    import pandas as pd
    import daily as _DL
    import feed as _FD
    if not len(me):
        return
    dm = me.index[-1]
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


def _verify_levels(sym, live, me):
    """УРОВНИ, А НЕ ТОЛЬКО БИТЫ (десятый круг, №9). Пересчёт дивидендов поставщиком может
    сдвинуть весь 13-месячный уровень, сохранить прежние 12 битов и перевернуть новый
    пограничный месяц. Сайдкар хранит месячные закрытия; новые обязаны совпасть со старыми
    с точностью LEVEL_TOL после снятия ОДНОГО общего множителя (перепривязка adjusted-ряда
    к новой дате легальна и меняет все уровни пропорционально)."""
    import pandas as pd
    lp = _levels_path(live)
    old = {}
    if lp.exists():
        df = pd.read_csv(lp, parse_dates=[0], index_col=0)
        if sym in df.columns:
            old = df[sym].dropna().to_dict()
    common = [d for d in me.index if d in old]
    if common:
        ratios = [me.loc[d] / old[d] for d in common]
        k = sorted(ratios)[len(ratios) // 2]
        bad = [f'{d:%Y-%m}: {me.loc[d]/old[d]/k-1:+.3%}' for d in common
               if abs(me.loc[d] / old[d] / k - 1) > LEVEL_TOL]
        if bad:
            raise SignalError(f'{sym}: УРОВНИ ряда разошлись с записанными сверх общего '
                              f'множителя — {bad[:4]}; поставщик пересчитал историю, сигнал '
                              f'пограничного месяца недостоверен')
    # запись/обновление сайдкара — всегда, атомарно
    import tempfile
    rows = {}
    if lp.exists():
        df = pd.read_csv(lp, parse_dates=[0], index_col=0)
        rows = {d: dict(r) for d, r in df.iterrows()}
    for d, v in me.items():
        rows.setdefault(d, {})[sym] = float(v)
    cols = sorted({c for r in rows.values() for c in r})
    fd, tmp = tempfile.mkstemp(dir=str(lp.parent), suffix='.tmp')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(',' + ','.join(cols) + '\n')
        for d in sorted(rows):
            f.write(f'{d:%Y-%m-%d},' + ','.join(
                ('' if cols_c not in rows[d] else f'{rows[d][cols_c]:.6f}')
                for cols_c in cols) + '\n')
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
    for col, sym, primary in LEGS:
        me = monthly_adjusted(ib, sym, primary)
        _verify_levels(sym, LIVE, me)      # уровни против сайдкара (десятый круг, №9)
        st = states(me)
        _verify_overlap(sym, col, ref, st)
        fresh[col] = st

    new_months = sorted(set(fresh['leg_eq'].index) & set(fresh['leg_bond'].index)
                        - set(ref.index))
    new_months = [d for d in new_months if d > ref.index[-1]]
    if not new_months:
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
