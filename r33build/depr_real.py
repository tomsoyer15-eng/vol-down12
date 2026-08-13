#!/usr/bin/env python3
"""Депрессия на НАСТОЯЩЕЙ механике ред. 33, а не на экспозиционной модели.

ЗАМЕЧАНИЕ, КОТОРОЕ ЗАКРЫВАЕТСЯ. depr_v13.py задаёт позиции непрерывно (Ee = L*0,5*se): там
нет полосы, целых контрактов, кап-аллокатора и ролла. Спецификация оправдывала это тем, что
депрессионная модель «капа и полосы не касается». Внешняя рецензия справедливо назвала это
отговоркой: именно при резком падении капитал меняется быстрее всего, и контрактная
экспозиция, кап и широкая полоса значимы максимально.

ЧТО ПРИХОДИТСЯ ЗАДАТЬ РУКАМИ. В 1929 году фьючерсов не существовало, поэтому размер
контракта относительно капитала не определён историей — а гранулярность зависит именно от
него. Задаётся явно и проверяется на чувствительность:
  «как сегодня»  — на входе в окно один контракт ноги А составляет ту же долю капитала,
                   что сегодня (MES 38 489 $ на 10 млн = 0,385%); нога Б — как сегодня
                   (98 560 $ = 0,986%). Это торговый случай: такой книгой мы и торговали бы;
  «мелкая сетка» — та же механика при контракте в десять раз мельче. Разница между двумя
                   строками и есть чистый вклад ГРАНУЛЯРНОСТИ;
  «без капа»     — кап отключён, чтобы отделить его вклад от вклада полосы.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'r33build'))
import sim_v13 as S
from sim_v164 import sim164
import depr_v13 as DP

CAP0 = 10_000_000.0
UNIT_A_TODAY = 38_489.0      # шаг ноги А сегодня (MES при SPY≈600 на счёте 10 млн)
UNIT_B_TODAY = 98_560.0      # шаг ноги Б сегодня (ZN)


def build():
    """Те же данные, что у depr_v13, но в форме, которую принимает боевой движок."""
    D = S.D
    px = pd.read_csv(D / 'gspc_1928_1935.csv', parse_dates=['date'],
                     index_col='date')['close']['1927-12-30':'1935-12-31']
    sh = pd.read_csv(D / 'shiller-monthly-1871-2026.csv', parse_dates=['Date']).set_index('Date').sort_index()
    dy_d = (sh['Dividend'] / sh['SP500']).reindex(px.index, method='ffill') / 252
    eq_r = (px.pct_change() + dy_d).dropna()
    eq_idx = (1 + eq_r).cumprod()
    y10 = sh['Long Interest Rate'] / 100
    Dv = pd.Series([S.dur(v) for v in y10.values], index=y10.index)
    dyy = y10.diff()
    bdm = (y10.shift(1) / 12 - Dv.shift(1) * dyy).dropna()
    bd_d = pd.Series(index=eq_r.index, dtype=float)
    for p in eq_r.index.to_period('M').unique():
        mv = bdm[bdm.index.to_period('M') == p]
        val = mv.iloc[0] if len(mv) else 0.0
        n = (eq_r.index.to_period('M') == p).sum()
        bd_d[eq_r.index.to_period('M') == p] = (1 + val) ** (1 / n) - 1
    bd_idx = (1 + bd_d).cumprod()
    mk = pd.read_csv(D / 'us-market-daily-1885-2025.csv', parse_dates=['Date']).set_index('Date').sort_index()
    rf_d = ((1 + mk['Risk Free Rate'] / 100) ** (1 / 252) - 1).reindex(eq_r.index).ffill()
    days = eq_r.index
    # дюрация референсного пара: месячная длинная ставка -> дневная
    dref = Dv.reindex(days.union(Dv.index)).interpolate('time').reindex(days).ffill().bfill()
    return days, eq_r, bd_d.reindex(days), rf_d, eq_idx, bd_idx, dref


def pack(days, re, rb, rfv, eq_idx, bd_idx, dref, unit_a, unit_b):
    """Синтетическая цена ноги А калибруется так, чтобы НА ВХОДЕ в окно один контракт
    стоил заданную величину; коэффициент CTD подгоняется под заданный шаг ноги Б."""
    lvl = (1 + re).cumprod()
    synth = (unit_a / S.ES_MULT) * (lvl / lvl.iloc[0]).values
    ctd = unit_b / S.ZN_MODEL_PX_EQ
    return (days, re.values, rb.values, rfv.values, synth, dref.values,
            S.sigs(eq_idx, days), S.sigs(bd_idx, days)), ctd


def metrics(r, days):
    w = r[(r.index >= '1929-01-02') & (r.index <= '1934-12-31')]
    e = (1 + w).cumprod()
    return (e.iloc[-1] - 1) * 100, (e / e.cummax() - 1).min() * 100


if __name__ == '__main__':
    days, re, rb, rfv, eq_idx, bd_idx, dref = build()
    print(f'окно {days[0]:%d.%m.%Y}–{days[-1]:%d.%m.%Y}, {len(days)} дней\n')

    tot0, dd0 = DP.run(strict=True)
    print(f"{'вариант':<38}{'итог 1929–34':>14}{'MaxDD':>9}{'сессий/год':>12}"
          f"{'кап-корр.':>11}{'макс.плечо':>12}")
    print(f"{'экспозиционная модель (ред. 32)':<38}{tot0:>13.1f}%{dd0:>8.1f}%{'—':>12}{'—':>11}{'—':>12}")

    yrs = (pd.Timestamp('1934-12-31') - pd.Timestamp('1929-01-02')).days / 365.25
    VAR = [('настоящая механика, полоса 10%', UNIT_A_TODAY, UNIT_B_TODAY, 0.10, 2.0),
           ('настоящая механика, полоса 3%', UNIT_A_TODAY, UNIT_B_TODAY, 0.03, 2.0),
           ('то же, сетка в 10 раз мельче', UNIT_A_TODAY / 10, UNIT_B_TODAY / 10, 0.10, 2.0),
           ('то же, БЕЗ капа', UNIT_A_TODAY, UNIT_B_TODAY, 0.10, None)]
    for lbl, ua, ub, band, cap in VAR:
        d, ctd = pack(days, re, rb, rfv, eq_idx, bd_idx, dref, ua, ub)
        old = S.CTD_RATIO
        S.CTD_RATIO = ctd                     # шаг ноги Б задаётся через контрактный эквивалент
        try:
            r, nav, st = sim164(*d, cap=cap, first_day='fixed', band=band, cap0=CAP0)
        finally:
            S.CTD_RATIO = old
        tot, dd = metrics(r, days)
        print(f"{lbl:<38}{tot:>13.1f}%{dd:>8.1f}%{st['exec_days']/yrs:>12.1f}"
              f"{st['cap_events']:>11}{st['max_close_lev']:>12.4f}")
