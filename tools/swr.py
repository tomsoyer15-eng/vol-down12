#!/usr/bin/env python3
"""Безопасная норма изъятия: скользящие окна с изъятиями, индексируемыми ФАКТИЧЕСКИМ CPI США.

Правило изъятия — как в классических исследованиях: в первый год снимается rate × стартовый
капитал, дальше сумма индексируется на реальную инфляцию (не на плоские 3%). Съём ежемесячный.
Итог приводится к ценам старта окна, то есть считается РЕАЛЬНЫЙ капитал.
"""
import sys, math
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'r32build'))
import sim_v13 as S


def load():
    d = S.build_splice()
    days = d[0]
    mk = pd.read_csv(ROOT / 'r32build/data/us-market-daily-1885-2025.csv',
                     parse_dates=['Date']).set_index('Date').sort_index()
    cpi = mk['CPI']; cpi = cpi[cpi > 0].reindex(days).ffill().bfill()
    return d, cpi.values


def run(d, cpi, i0, i1, rate, cap0=10_000_000.0, cap=2.0, spread_pp=0.5):
    days, re, rb, rfv, spy_close, dref, st_eq, st_bd = d
    sl = slice(i0, i1)
    days = days[sl]; re = re[sl]; rb = rb[sl]; rfv = rfv[sl]
    spy_close = spy_close[sl]; dref = dref[sl]; cpi = cpi[sl]
    st_eq = S.strict_states(st_eq, d[0])[sl]; st_bd = S.strict_states(st_bd, d[0])[sl]
    sd = spread_pp/100/252
    first = {}
    for k, dte in enumerate(days):
        first.setdefault((dte.year, dte.month), k)
    wd_days = set(first.values())
    e = cap0; n_e = n_b = 0; unit_is_mes = False
    rolls = S.roll_days_calendar(days)
    prev_se = prev_sb = None; D_fix = dref[0]; prev_lev = 0.0
    real_min = 1e18; depleted = False; taken = 0.0
    for i, dte in enumerate(days):
        j = max(i-1, 0)
        if i in wd_days and not depleted:
            amt = rate/12*cap0*(cpi[i]/cpi[0])
            if amt >= e:
                depleted = True; taken += e; e = 0.0
            else:
                e -= amt; taken += amt
        if depleted:
            real_min = 0.0; break
        if (not unit_is_mes) and dte >= S.MES_START:
            n_e *= 10; unit_is_mes = True
        mult = S.ES_MULT/10 if unit_is_mes else S.ES_MULT
        unit_e = mult*spy_close[j]
        n0_e, n0_b = n_e, n_b
        eq_sw = (st_eq[i] != prev_se); bd_sw = (st_bd[i] != prev_sb); roll = (dte in rolls)
        if roll or bd_sw: D_fix = dref[j]
        q_b = (S.ZN_MODEL_PX_EQ*S.CTD_RATIO*D_fix*1e-4)/(dref[j]*1e-4)
        breach0 = (i > 0) and (prev_lev > cap)
        tgt_e = st_eq[i]*e; tgt_b = st_bd[i]*e
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
        if roll: e -= S.ROLL_BP*(exp_e+exp_b)
        if i > 0:
            e = e*(1+rfv[i]) + exp_e*(re[i]-rfv[i]-sd) + exp_b*(rb[i]-rfv[i]-sd)
        if e <= 0:
            depleted = True; real_min = 0.0; break
        real = e/(cpi[i]/cpi[0])/cap0
        real_min = min(real_min, real)
        mc = S.ES_MULT/10 if unit_is_mes else S.ES_MULT
        qc = (S.ZN_MODEL_PX_EQ*S.CTD_RATIO*D_fix*1e-4)/(dref[i]*1e-4)
        prev_lev = (n_e*mc*spy_close[i] + n_b*qc)/e
    real_end = 0.0 if depleted else e/(cpi[min(i, len(cpi)-1)]/cpi[0])/cap0
    return dict(real_end=real_end, real_min=real_min, depleted=depleted, taken=taken/cap0)


def windows(d, cpi, horizon):
    days = d[0]
    out = []
    for y in range(days[0].year + 1, days[-1].year - horizon + 1):
        i0 = int(np.searchsorted(days, pd.Timestamp(f'{y}-01-01')))
        i1 = int(np.searchsorted(days, pd.Timestamp(f'{y+horizon}-01-01')))
        if i1 - i0 < horizon*200: continue
        out.append((y, i0, i1))
    return out


if __name__ == '__main__':
    d, cpi = load()
    for hor in (20, 30):
        ws = windows(d, cpi, hor)
        print(f"\n{'='*78}\nОКНА ПО {hor} ЛЕТ — стартов {len(ws)} "
              f"({ws[0][0]}…{ws[-1][0]}), изъятия индексируются фактическим CPI\n{'='*78}")
        print(f"{'ставка':>7}{'провалов':>10}{'худший старт':>14}{'реальный капитал в конце':>26}{'медиана':>10}")
        for rate in (0.02, 0.03, 0.04, 0.05):
            res = [(y, run(d, cpi, i0, i1, rate)) for y, i0, i1 in ws]
            dep = [y for y, r in res if r['depleted']]
            ends = sorted((r['real_end'], y) for y, r in res)
            med = np.median([r['real_end'] for _, r in res])
            print(f"{rate*100:>6.0f}%{len(dep):>10}{ends[0][1]:>14}"
                  f"{ends[0][0]:>20.2f}x{med:>10.2f}x")
