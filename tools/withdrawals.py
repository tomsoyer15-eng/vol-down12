#!/usr/bin/env python3
"""Совместимы ли изъятия 2% в год с признанной просадкой −58,5%.

§5 утверждает совместимость изъятий около 2% годовых, но это заявлено без моделирования
денежных потоков. Возражение обоих рецензентов справедливо: изымать 2% из счёта, который
шесть-восемь лет стоит ниже пика, значит продавать на дне много лет подряд, и риск
последовательности доходностей здесь может оказаться существеннее самой глубины.

ЧТО СЧИТАЕТСЯ. Изъятие ежемесячно, 1/12 годовой ставки. Две конвенции, потому что они
дают разный ответ и выбор между ними — решение владельца, а не расчётчика:
  «от текущего счёта» — доля от NAV, сумма падает вместе с рынком (самоограничение);
  «от пика»           — сумма фиксируется от максимума счёта и не снижается в просадку
                        (постоянный уровень жизни, худший случай для портфеля).
Измеряется: глубина просадки, годы под водой, конечный капитал против варианта без
изъятий и — главное — доля изъятого капитала, которая пришлась на просадку.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'r33build'))
import sim_v13 as S
import smooth as M
import build_1885 as B
import band_recheck as BR
import signal_base as SB

RATES = [0.0, 0.02, 0.03, 0.04]


def with_draw(r, days, rate, mode):
    """Начислить доходность и изымать помесячно. r — ряд дневных доходностей ядра."""
    nav = 1.0; peak = 1.0
    out = np.empty(len(days)); taken = 0.0; taken_in_dd = 0.0
    month = days.to_period('M')
    for i in range(len(days)):
        nav *= (1 + r.iloc[i])
        if i and month[i] != month[i - 1] and rate:
            base = nav if mode == 'от текущего счёта' else peak
            w = base * rate / 12
            w = min(w, nav)                      # изъять больше, чем есть, нельзя
            in_dd = nav < peak * (1 - 1e-9)
            nav -= w; taken += w
            if in_dd: taken_in_dd += w
        peak = max(peak, nav)
        out[i] = nav
        if nav <= 0:
            out[i:] = 0.0
            break
    s = pd.Series(out, index=days)
    return s, taken, taken_in_dd


def underwater(s):
    dd = s / s.cummax() - 1
    i = dd.idxmin(); pk = s[:i].idxmax()
    after = s[s.index > i]; rec = after[after >= s[pk]]
    yrs = ((rec.index[0] - pk).days / 365.25) if len(rec) else float('nan')
    return dd.min() * 100, pk, i, yrs, (dd < -1e-9).mean() * 100


def run(name, r, days):
    print(f"\n=== {name} ===")
    print(f"{'изъятие':<10}{'режим':<20}{'MaxDD':>9}{'лет под водой':>15}"
          f"{'доля времени ниже пика':>24}{'капитал ×':>12}{'изъято на просадке':>20}")
    for rate in RATES:
        for mode in (('—',) if rate == 0 else ('от текущего счёта', 'от пика')):
            s, tk, tdd = with_draw(r, days, rate, mode)
            dd, pk, lo, yrs, share = underwater(s)
            end = s.iloc[-1]
            frac = (tdd / tk * 100) if tk else 0.0
            print(f"{rate*100:>7.0f}%  {mode:<20}{dd:>8.1f}%"
                  f"{(f'{yrs:.1f}' if yrs == yrs else 'не вернулся'):>15}"
                  f"{share:>23.0f}%{end:>12.1f}{frac:>19.0f}%")


if __name__ == '__main__':
    d = B.build(); days = d[0]
    we, wb = BR.weights_of(d)
    r_base, _ = BR.sim_stats(d, we, wb, 0.10)
    run('полное окно 1886–2026, календарный конец месяца', r_base, days)

    # худшая фаза наблюдения — та, на которой признан хвост −58,5%
    eq = (1 + pd.Series(d[1], index=days)).cumprod()
    bd = (1 + pd.Series(d[2], index=days)).cumprod()
    r_worst, _ = BR.sim_stats(d, SB.w_phase(eq, days, 6), SB.w_phase(bd, days, 6), 0.10)
    run('ХУДШАЯ фаза наблюдения (та, что даёт −58,4%)', r_worst, days)

    # только Депрессия: вход в пике, чтобы увидеть чистый эффект последовательности
    m = (days >= '1929-09-01') & (days <= '1945-12-31')
    run('вход на пике 09.1929, окно по 1945 год', r_worst[m], days[m])
