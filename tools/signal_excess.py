#!/usr/bin/env python3
"""Сигнал по избыточной доходности: четыре проверки кандидата.

(1) при выравненном риске; (2) на всех 21 фазе наблюдения — чтобы преимущество не оказалось
свойством календаря; (3) по эпохам ставки — механизм предсказывает, что вся разница должна
сидеть в периоды высокой денежной ставки, и если её там нет, объяснение неверно;
(4) доля включённого состояния — прямая проверка самого смещения.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'r32build'))
import sim_v13 as S
import smooth as M
import rebal as R
import build_1885 as B
import signal_base as SB


def bases(d):
    days = d[0]; rf = pd.Series(d[3], index=days)
    tr_e = (1 + pd.Series(d[1], index=days)).cumprod()
    tr_b = (1 + pd.Series(d[2], index=days)).cumprod()
    ex_e = (1 + pd.Series(d[1], index=days) - rf).cumprod()
    ex_b = (1 + pd.Series(d[2], index=days) - rf).cumprod()
    return days, (tr_e, tr_b), (ex_e, ex_b), rf


def match(d, a, b, target_v):
    lo, hi = 0.8, 1.6
    for _ in range(12):
        k = (lo + hi) / 2
        r, _ = R.sim(d, M.weights(a, d[0], 'бинарное (ядро)') * k,
                     M.weights(b, d[0], 'бинарное (ядро)') * k, cap=2.0 * k)
        if M.met(r)[1] < target_v: lo = k
        else: hi = k
    r, _ = R.sim(d, M.weights(a, d[0], 'бинарное (ядро)') * k,
                 M.weights(b, d[0], 'бинарное (ядро)') * k, cap=2.0 * k)
    return M.met(r), k


if __name__ == '__main__':
    for nm, d in (('дневное окно ядра', M.build()), ('полное окно', B.build())):
        days, TR, EX, rf = bases(d)
        print(f"\n=== {nm}: {days[0]:%Y}–{days[-1]:%Y} ===")

        # (1) равный риск
        rb, _ = R.sim(d, M.weights(*[TR[0], days, 'бинарное (ядро)']),
                      M.weights(TR[1], days, 'бинарное (ядро)'))
        tv = M.met(rb)[1]
        print(f"{'база сигнала':<26}{'CAGR':>8}{'качка':>8}{'MaxDD':>9}{'D/V':>7}{'плечо':>8}")
        print(f"{'полная (ядро)':<26}{M.met(rb)[0]:>7.2f}%{tv:>7.2f}%"
              f"{M.met(rb)[2]:>8.1f}%{M.met(rb)[3]:>7.2f}{1.0:>8.2f}")
        m, k = match(d, EX[0], EX[1], tv)
        print(f"{'избыточная, равный риск':<26}{m[0]:>7.2f}%{m[1]:>7.2f}%"
              f"{m[2]:>8.1f}%{m[3]:>7.2f}{k:>8.2f}")

        # (2) фазы
        for lbl, (a, b) in (('полная', TR), ('избыточная', EX)):
            res = [M.met(R.sim(d, SB.w_phase(a, days, p), SB.w_phase(b, days, p))[0])
                   for p in range(SB.PER)]
            c = np.array([x[0] for x in res]); dv = np.array([x[3] for x in res])
            print(f"  21 фаза, {lbl:<12} CAGR медиана {np.median(c):.2f}% "
                  f"({c.min():.2f}–{c.max():.2f}), D/V медиана {np.median(dv):.2f}")

        # (3) по эпохам ставки и (4) доля включённого
        we_t = M.weights(TR[0], days, 'бинарное (ядро)'); wb_t = M.weights(TR[1], days, 'бинарное (ядро)')
        we_x = M.weights(EX[0], days, 'бинарное (ядро)'); wb_x = M.weights(EX[1], days, 'бинарное (ядро)')
        rt, _ = R.sim(d, we_t, wb_t); rx, _ = R.sim(d, we_x, wb_x)
        rfy = (rf * 252 * 100)
        cuts = [(0, 3, 'ставка < 3%'), (3, 6, '3–6%'), (6, 99, 'выше 6%')]
        print(f"  {'эпоха':<14}{'лет':>6}{'полная':>9}{'избыт.':>9}{'разница':>9}"
              f"{'вкл. А: полн./избыт.':>24}")
        for lo, hi, lbl in cuts:
            m = (rfy >= lo) & (rfy < hi)
            if m.sum() < 250: continue
            yy = m.sum() / 252
            ct = (1 + rt[m]).prod() ** (252 / m.sum()) - 1
            cx = (1 + rx[m]).prod() ** (252 / m.sum()) - 1
            print(f"  {lbl:<14}{yy:>6.0f}{ct*100:>8.2f}%{cx*100:>8.2f}%"
                  f"{(cx-ct)*100:>+8.2f}{we_t[m].mean()*100:>16.0f}% /{we_x[m].mean()*100:>4.0f}%")
