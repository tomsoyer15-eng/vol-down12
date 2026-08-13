#!/usr/bin/env python3
"""ВТ-ВНИЗ поверх движка v1.6.0 ред.4 (sim_v164, кап плеча 2,00).
Единственная правка sim164: цель включённой ноги = m*k*капитал вместо 1,0*капитал."""
import sys, math
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT/'run160'))
import sim_v13 as S, sim_v164 as S164
import vd12 as V

def sim164_vd(days, re, rb, rfv, spy_close, dref, st_eq, st_bd, m=1.0, target=12.0,
              spread_pp=0.5, strict=True, cap0=10_000_000.0, cap=2.0, first_day='fixed'):
    sd = spread_pp/100/252
    if strict:
        st_eq = S.strict_states(st_eq, days); st_bd = S.strict_states(st_bd, days)
    combo = rfv + st_eq*(re-rfv) + st_bd*(rb-rfv)
    ws = V.week_starts(days)
    e = cap0; n_e = n_b = 0; unit_is_mes = False
    nav = np.empty(len(days)); kk = np.empty(len(days))
    exec_days = 0; cap_events = 0; max_close_lev = 0.0; viol = 0
    rolls = S.roll_days_calendar(days)
    prev_se = prev_sb = None; D_fix = dref[0]; prev_close_lev = 0.0; k = 1.0
    for i, dte in enumerate(days):
        j = max(i-1, 0)
        if target is not None and ws[i]:
            w = combo[max(0,i-V.VOL_WIN):i]
            if len(w) >= V.VOL_WIN:
                v = w.std(ddof=1)*100*V.ANN
                k = min(1.0, target/v) if v > 0 else 1.0
        if target is None: k = 1.0
        kk[i] = k
        if (not unit_is_mes) and dte >= S.MES_START:
            n_e *= 10; unit_is_mes = True
        mult = S.ES_MULT/10 if unit_is_mes else S.ES_MULT
        unit_e = mult*spy_close[j]
        n0_e, n0_b = n_e, n_b
        eq_switch = (st_eq[i] != prev_se); bd_switch = (st_bd[i] != prev_sb); roll_today = (dte in rolls)
        if roll_today or bd_switch: D_fix = dref[j]
        q_b = (S.ZN_MODEL_PX_EQ*S.CTD_RATIO*D_fix*1e-4)/(dref[j]*1e-4)
        breach_at_open = (cap is not None) and (i > 0) and (prev_close_lev > cap)
        size = m*k
        tgt_e = size*st_eq[i]*e; tgt_b = size*st_bd[i]*e
        exp_e = n_e*unit_e; exp_b = n_b*q_b
        if eq_switch or abs(tgt_e-exp_e) > S.BAND*e:
            new = round(tgt_e/unit_e)
            if new != n_e: e -= S.COST*abs(new-n_e)*unit_e; n_e = new
        if bd_switch or roll_today or abs(tgt_b-exp_b) > S.BAND*e:
            new = round(tgt_b/q_b)
            if new != n_b: e -= S.COST*abs(new-n_b)*q_b; n_b = new
        if cap is not None:
            def e_after(ne, nb):
                ea = e - S.COST*((abs(ne-n0_e)-abs(n_e-n0_e))*unit_e + (abs(nb-n0_b)-abs(n_b-n0_b))*q_b)
                if roll_today: ea -= S.ROLL_BP*(ne*unit_e + nb*q_b)
                return ea
            def breach(ne, nb): return (ne*unit_e + nb*q_b) > cap*e_after(ne, nb)
            if breach_at_open or breach(n_e, n_b):
                pe0, pb0 = n_e, n_b
                ne = min(n_e, math.floor(tgt_e/unit_e)) if breach_at_open else math.floor(tgt_e/unit_e)
                nb = min(n_b, math.floor(tgt_b/q_b)) if breach_at_open else math.floor(tgt_b/q_b)
                while breach(ne, nb) and (ne > 0 or nb > 0):
                    if ne > 0 and (unit_e >= q_b or nb == 0): ne -= 1
                    else: nb -= 1
                if (ne, nb) != (pe0, pb0):
                    e -= S.COST*((abs(ne-n0_e)-abs(n_e-n0_e))*unit_e + (abs(nb-n0_b)-abs(n_b-n0_b))*q_b)
                    n_e, n_b = ne, nb; cap_events += 1
        prev_se, prev_sb = st_eq[i], st_bd[i]
        exp_e = n_e*unit_e; exp_b = n_b*q_b
        session = (n_e != n0_e) or (n_b != n0_b) or (roll_today and (n_e != 0 or n_b != 0))
        if session: exec_days += 1
        if roll_today: e -= S.ROLL_BP*(exp_e+exp_b)
        if cap is not None and session and (exp_e+exp_b) > cap*e: viol += 1
        if i == 0 and first_day == 'fixed':
            nav[i] = e
        else:
            e = e*(1+rfv[i]) + exp_e*(re[i]-rfv[i]-sd) + exp_b*(rb[i]-rfv[i]-sd)
            nav[i] = e
        mult_c = S.ES_MULT/10 if unit_is_mes else S.ES_MULT
        q_b_c = (S.ZN_MODEL_PX_EQ*S.CTD_RATIO*D_fix*1e-4)/(dref[i]*1e-4)
        lc = (n_e*mult_c*spy_close[i] + n_b*q_b_c)/e
        if lc > max_close_lev: max_close_lev = lc
        prev_close_lev = lc
    s = pd.Series(nav, index=days); r = s.pct_change(); r.iloc[0] = nav[0]/cap0-1
    return r, s, dict(exec_days=exec_days, cap_events=cap_events,
                      max_close_lev=max_close_lev, viol=viol, k_mean=kk.mean())

def met(r, yrs):
    eq = (1+r).cumprod()
    return (eq.iloc[-1]**(1/yrs)-1)*100, (eq/eq.cummax()-1).min()*100

if __name__ == '__main__':
    for wn, build in [('1963–2026', S.build_splice), ('2003–2026', S.build_modern)]:
        d = build(); days = d[0]; yrs = (days[-1]-days[0]).days/365.25
        # регрессия: без ручки повторяем sim164 в точности
        a = sim164_vd(*d, m=1.0, target=None)[0]
        b = S164.sim164(*d)[0]
        assert np.allclose(a.values, b.values, rtol=1e-12), 'порт не сходится с sim164'
        print(f"\n### {wn}   [регрессия к sim_v164: OK]")
        print(f"{'вариант':<34}{'CAGR':>8}{'MaxDD':>9}{'сесс/год':>10}{'кап-корр':>10}{'макс плечо':>12}")
        rows = [
            ('ядро, замороженный v1.5.3.1', S.sim(*d, strict=True)[0], None),
            ('ядро, v1.6.0 (кап 2,00)', b, S164.sim164(*d)[2]),
            ('ВТ-ВНИЗ-12, старый движок', V.sim_vd12(*d, m=1.0)[0], None),
            ('ВТ-ВНИЗ-12, v1.6.0 (кап 2,00)', *sim164_vd(*d, m=1.0)[::2]),
        ]
        for nm, r, st in rows:
            c, dd = met(r, yrs)
            ex = f"{st['exec_days']/yrs:>9.1f}" if st else f"{'—':>9}"
            ce = f"{st['cap_events']:>9}" if st else f"{'—':>9}"
            ml = f"{st['max_close_lev']:>11.4f}" if st else f"{'—':>11}"
            print(f"{nm:<34}{c:>7.2f}%{dd:>8.2f}%{ex} {ce} {ml}")
