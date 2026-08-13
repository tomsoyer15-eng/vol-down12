#!/usr/bin/env python3
"""Выравнивание ног: по полосе 3% (как в ядре) против календарного расписания.

В ядре нога возвращается к цели, когда отклонение превысило 3% капитала. Здесь
проверяется альтернатива: возвращать по календарю — еженедельно, ежемесячно,
ежеквартально — независимо от величины отклонения. И их сочетание.

Принудительные сделки при смене состояния ноги и при ролле сохраняются во всех
вариантах: это отдельные механизмы, а не выравнивание.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'r32build'))
import sim_v13 as S
import smooth as M


def schedule(days, rule):
    if rule is None:
        return np.zeros(len(days), dtype=bool)
    p = days.to_period(rule)
    return np.r_[True, p[1:] != p[:-1]]


def sim(d, w_eq, w_bd, band=0.03, sched=None, cap=2.0, spread_pp=0.5, cap0=10_000_000.0):
    days, re, rb, rfv, spy_close, dref = d[:6]
    n = len(days); sd = spread_pp / 100 / 252
    e = cap0; n_e = n_b = 0; unit_is_mes = False
    nav = np.empty(n); rolls = S.roll_days_calendar(days)
    D_fix = dref[0]; exec_days = 0; prev_lev = 0.0; lev_sum = 0.0
    sc = np.zeros(n, dtype=bool) if sched is None else sched
    for i, dte in enumerate(days):
        j = max(i - 1, 0)
        if (not unit_is_mes) and dte >= S.MES_START:
            n_e *= 10; unit_is_mes = True
        mult = S.ES_MULT / 10 if unit_is_mes else S.ES_MULT
        unit_e = mult * spy_close[j]
        roll = dte in rolls
        edge_e = (w_eq[i] == 0) != (w_eq[j] == 0) if i else w_eq[i] > 0
        edge_b = (w_bd[i] == 0) != (w_bd[j] == 0) if i else w_bd[i] > 0
        if roll or edge_b:
            D_fix = dref[j]
        unit_b = (S.ZN_MODEL_PX_EQ * S.CTD_RATIO * D_fix * 1e-4) / (dref[j] * 1e-4)
        units = np.array([unit_e, unit_b])
        tgt = np.array([w_eq[i], w_bd[i]]) * e
        if tgt.sum() > cap * e:
            tgt *= cap * e / tgt.sum()
        n0 = np.array([n_e, n_b], dtype=float); cur = n0.copy(); exp = cur * units
        for k, force in enumerate([edge_e, edge_b or roll]):
            hit = force or sc[i] or (band is not None and abs(tgt[k] - exp[k]) > band * e)
            if hit:
                new = round(tgt[k] / units[k])
                if new != cur[k]:
                    e -= S.COST * abs(new - cur[k]) * units[k]
                    cur[k] = new; exp[k] = new * units[k]

        def e_after(x):
            ea = e - S.COST * ((np.abs(x - n0) - np.abs(cur - n0)) * units).sum()
            if roll: ea -= S.ROLL_BP * x[1] * units[1]
            return ea

        if (i > 0 and prev_lev > cap) or (cur * units).sum() > cap * e_after(cur):
            x = np.floor(tgt / units)
            if i > 0 and prev_lev > cap:
                x = np.minimum(cur, x)
            while (x * units).sum() > cap * e_after(x) and x.sum() > 0:
                x[int(np.argmax(np.where(x > 0, units, -np.inf)))] -= 1
            if not np.array_equal(x, cur):
                e -= S.COST * ((np.abs(x - n0) - np.abs(cur - n0)) * units).sum()
                cur = x
        n_e, n_b = cur; exp = cur * units
        if (cur != n0).any() or (roll and n_b): exec_days += 1
        if roll: e -= S.ROLL_BP * exp[1]
        if i == 0:
            nav[i] = e
        else:
            e = e * (1 + rfv[i]) + (exp * (np.array([re[i], rb[i]]) - rfv[i] - sd)).sum()
            nav[i] = e
        mc = S.ES_MULT / 10 if unit_is_mes else S.ES_MULT
        uc = np.array([mc * spy_close[i],
                       (S.ZN_MODEL_PX_EQ * S.CTD_RATIO * D_fix * 1e-4) / (dref[i] * 1e-4)])
        prev_lev = (cur * uc).sum() / e; lev_sum += prev_lev
    s = pd.Series(nav, index=days); r = s.pct_change(); r.iloc[0] = nav[0] / cap0 - 1
    return r, dict(exec_days=exec_days, avglev=lev_sum / n)


if __name__ == '__main__':
    d = M.build(); days = d[0]
    we = M.weights(d[6], days, 'бинарное (ядро)')
    wb = M.weights(d[7], days, 'бинарное (ядро)')
    yrs = (days[-1] - days[0]).days / 365.25
    VAR = [('полоса 3% — как в ядре', 0.03, None),
           ('только календарь: неделя', None, 'W'),
           ('только календарь: месяц', None, 'M'),
           ('только календарь: квартал', None, 'Q'),
           ('только календарь: год', None, 'Y'),
           ('полоса 3% + месяц', 0.03, 'M'),
           ('полоса 3% + квартал', 0.03, 'Q'),
           ('без выравнивания вовсе', None, None)]
    print(f"{'вариант':<30}{'CAGR':>8}{'качка':>8}{'MaxDD':>9}{'D/V':>7}{'ср.плечо':>10}{'сд/год':>8}")
    for lbl, band, rule in VAR:
        r, st = sim(d, we, wb, band=band, sched=schedule(days, rule))
        c, v, dd, dv = M.met(r)
        print(f"{lbl:<30}{c:>7.2f}%{v:>7.2f}%{dd:>8.1f}%{dv:>7.2f}"
              f"{st['avglev']:>10.2f}{st['exec_days']/yrs:>8.1f}")
