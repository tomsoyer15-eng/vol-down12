#!/usr/bin/env python3
"""Развес ног: 1:1 (как в ядре) против балансировки по волатильности.
Веса пересчитываются в первый торговый день недели по 60-дневной качке каждой ноги,
позиция ноги = w_i x капитал; суммарный номинал ограничен капом 2,00."""
import sys, math
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'r32build'))
import sim_v13 as S
sys.path.insert(0, str(ROOT / 'tools'))
from vd12 import week_starts

VOL_WIN, ANN = 60, 15.87


def weights(re, rb, days, scheme, gross=2.0, eq_target=12.0):
    """Возвращает массивы w_eq, w_bd (доли капитала на ногу)."""
    n = len(days); ws = week_starts(days)
    w_e = np.ones(n); w_b = np.ones(n)
    ce = cb = 1.0
    for i in range(n):
        if ws[i] and i >= VOL_WIN:
            ve = re[i-VOL_WIN:i].std(ddof=1)*100*ANN
            vb = rb[i-VOL_WIN:i].std(ddof=1)*100*ANN
            if scheme == 'invvol':                       # обратная качка, сумма = gross
                if ve > 0 and vb > 0:
                    a, b = 1/ve, 1/vb
                    ce, cb = gross*a/(a+b), gross*b/(a+b)
            elif scheme == 'eqvol_only':                 # ноге акций свой таргет, облигации 1,0
                ce = min(1.0, eq_target/ve) if ve > 0 else 1.0
                cb = 1.0
            elif scheme == 'invvol_capped':              # обратная качка, но ни одна нога > 1,25
                if ve > 0 and vb > 0:
                    a, b = 1/ve, 1/vb
                    ce, cb = gross*a/(a+b), gross*b/(a+b)
                    ce, cb = min(ce, 1.25), min(cb, 1.25)
        w_e[i], w_b[i] = ce, cb
    return w_e, w_b


def sim(d, scheme, cap=2.0, spread_pp=0.5, cap0=10_000_000.0, **kw):
    days, re, rb, rfv, spy_close, dref, st_eq0, st_bd0 = d
    st_eq = S.strict_states(st_eq0, days); st_bd = S.strict_states(st_bd0, days)
    w_e, w_b = (np.ones(len(days)), np.ones(len(days))) if scheme == 'base' \
        else weights(re, rb, days, scheme, **kw)
    sd = spread_pp/100/252
    e = cap0; n_e = n_b = 0; unit_is_mes = False
    nav = np.empty(len(days)); rolls = S.roll_days_calendar(days)
    prev_se = prev_sb = None; D_fix = dref[0]; prev_lev = 0.0
    exec_days = 0; maxlev = 0.0
    for i, dte in enumerate(days):
        j = max(i-1, 0)
        if (not unit_is_mes) and dte >= S.MES_START:
            n_e *= 10; unit_is_mes = True
        mult = S.ES_MULT/10 if unit_is_mes else S.ES_MULT
        unit_e = mult*spy_close[j]
        n0_e, n0_b = n_e, n_b
        eq_sw = (st_eq[i] != prev_se); bd_sw = (st_bd[i] != prev_sb); roll = (dte in rolls)
        if roll or bd_sw: D_fix = dref[j]
        q_b = (S.ZN_MODEL_PX_EQ*S.CTD_RATIO*D_fix*1e-4)/(dref[j]*1e-4)
        breach0 = (i > 0) and (prev_lev > cap)
        tgt_e = w_e[i]*st_eq[i]*e; tgt_b = w_b[i]*st_bd[i]*e
        exp_e, exp_b = n_e*unit_e, n_b*q_b
        if eq_sw or abs(tgt_e-exp_e) > S.BAND*e:
            new = round(tgt_e/unit_e)
            if new != n_e: e -= S.COST*abs(new-n_e)*unit_e; n_e = new
        if bd_sw or roll or abs(tgt_b-exp_b) > S.BAND*e:
            new = round(tgt_b/q_b)
            if new != n_b: e -= S.COST*abs(new-n_b)*q_b; n_b = new
        def e_after(ne, nb):
            ea = e - S.COST*((abs(ne-n0_e)-abs(n_e-n0_e))*unit_e + (abs(nb-n0_b)-abs(n_b-n0_b))*q_b)
            if roll: ea -= S.ROLL_BP*(ne*unit_e + nb*q_b)
            return ea
        def br(ne, nb): return (ne*unit_e + nb*q_b) > cap*e_after(ne, nb)
        if breach0 or br(n_e, n_b):
            ne = min(n_e, math.floor(tgt_e/unit_e)) if breach0 else math.floor(tgt_e/unit_e)
            nb = min(n_b, math.floor(tgt_b/q_b)) if breach0 else math.floor(tgt_b/q_b)
            while br(ne, nb) and (ne > 0 or nb > 0):
                if ne > 0 and (unit_e >= q_b or nb == 0): ne -= 1
                else: nb -= 1
            if (ne, nb) != (n_e, n_b):
                e -= S.COST*((abs(ne-n0_e)-abs(n_e-n0_e))*unit_e + (abs(nb-n0_b)-abs(n_b-n0_b))*q_b)
                n_e, n_b = ne, nb
        prev_se, prev_sb = st_eq[i], st_bd[i]
        exp_e, exp_b = n_e*unit_e, n_b*q_b
        if (n_e != n0_e) or (n_b != n0_b) or (roll and (n_e or n_b)): exec_days += 1
        if roll: e -= S.ROLL_BP*(exp_e+exp_b)
        if i == 0:
            nav[i] = e
        else:
            e = e*(1+rfv[i]) + exp_e*(re[i]-rfv[i]-sd) + exp_b*(rb[i]-rfv[i]-sd)
            nav[i] = e
        mc = S.ES_MULT/10 if unit_is_mes else S.ES_MULT
        qc = (S.ZN_MODEL_PX_EQ*S.CTD_RATIO*D_fix*1e-4)/(dref[i]*1e-4)
        prev_lev = (n_e*mc*spy_close[i] + n_b*qc)/e
        maxlev = max(maxlev, prev_lev)
    s = pd.Series(nav, index=days); r = s.pct_change(); r.iloc[0] = nav[0]/cap0-1
    return r, dict(exec_days=exec_days, maxlev=maxlev,
                   w_e=np.mean(w_e), w_b=np.mean(w_b))


def met(r, lo=None, hi=None):
    x = r if lo is None else r[(r.index >= lo) & (r.index < hi)]
    eq = (1+x).cumprod(); yrs = (x.index[-1]-x.index[0]).days/365.25
    v = x.std()*np.sqrt(252)*100; c = (eq.iloc[-1]**(1/yrs)-1)*100
    return c, v, (eq/eq.cummax()-1).min()*100, c/v


if __name__ == '__main__':
    d = S.build_splice(); days = d[0]; yrs = (days[-1]-days[0]).days/365.25
    print("качка ног по всему окну: акции "
          f"{d[1].std()*np.sqrt(252)*100:.1f}%, облигации {d[2].std()*np.sqrt(252)*100:.1f}%\n")
    print(f"{'развес':<34}{'CAGR':>8}{'качка':>8}{'MaxDD':>9}{'D/V':>7}{'ср.вес А/Б':>13}{'макс плечо':>12}{'сд/год':>8}")
    for scheme, name in [('base', '1:1 — как в ядре'),
                         ('invvol', 'обратная качка, сумма 2,0'),
                         ('invvol_capped', 'обратная качка, нога ≤1,25'),
                         ('eqvol_only', 'таргет 12 только на акции')]:
        r, st = sim(d, scheme)
        c, v, dd, dv = met(r)
        print(f"{name:<34}{c:>7.2f}%{v:>7.2f}%{dd:>8.1f}%{dv:>7.2f}"
              f"{st['w_e']:>7.2f}/{st['w_b']:.2f}{st['maxlev']:>11.3f}{st['exec_days']/yrs:>8.1f}")
    print("\nразбивка по эпохам ставок (ключевая проверка):")
    print(f"{'развес':<34}{'1963–1981 CAGR/DD':>22}{'1982–2026 CAGR/DD':>22}")
    for scheme, name in [('base', '1:1 — как в ядре'), ('invvol', 'обратная качка, сумма 2,0')]:
        r, _ = sim(d, scheme)
        a = met(r, '1963-01-01', '1982-01-01'); b = met(r, '1982-01-01', '2027-01-01')
        print(f"{name:<34}{a[0]:>13.2f}% /{a[2]:>6.1f}%{b[0]:>13.2f}% /{b[2]:>6.1f}%")
