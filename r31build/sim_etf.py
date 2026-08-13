# -*- coding: utf-8 -*-
"""Маршрут Е спецификации ADD-FUT v1.6.0 ред. 30: UCITS-ETF + маржинальный займ IBKR.
Исполнение: доли. PRE-LAUNCH PROXY: цены — SPY и индекс на IEF-доходностях (база 100) как прокси
CSPX (запущен 05.2010) и CBU0 (запущен 06.2009); окно 2003+ до запусков — модель издержек, не фактическое
округление UCITS-инструментов; гранулярность прокси и фактических фондов одного порядка ($100–700). Издержки: комиссия 5 б.п. на изменение экспозиции; TER 0,07%/год на каждую ногу;
дивидендный drag 0,20%/год на фондовую ногу (физический фонд, нормативный вариант);
займ — на ФАКТИЧЕСКИЙ дебетовый остаток по лесенке IBKR Pro USD
(100 тыс. @ +1,50%, до 1 млн @ +1,00%, до 50 млн @ +0,75%, далее @ +0,50%) + трение бенчмарка 0,10%/год;
дебет включает денежный буфер 5% капитала: debit = max(P + 0,05*e - e, 0);
положительный остаток размещается по ставке денег модели. Роллов нет.
Кап 2,00 — то же нормативное определение, что в sim_v164 (триггер по сохранённому
плечу фактического закрытия, односторонний аллокатор в долях, нетто-ордер,
неравенство после всех известных собственных расходов сессии).
О-3-Е: собственный капитал ≥ 1,40 × поддерживающего требования (25% позиций);
минимум отношения по окну — контрольная метрика."""
import math
import pandas as pd, numpy as np
import sim_v13 as S

TER = 0.0007
DRAG_PHYS = 0.0020
BM_BASIS = 0.0010
TIERS = [(100_000.0, 0.0150), (900_000.0, 0.0100), (49_000_000.0, 0.0075), (float('inf'), 0.0050)]
MAINT = 0.25

def loan_spread_amt(debit):
    """Годовая долларовая стоимость СПРЕДА займа (сверх бенчмарка) по лесенке + трение бенчмарка."""
    rest = debit; amt = 0.0
    for size, sp in TIERS:
        take = min(rest, size)
        amt += take*sp; rest -= take
        if rest <= 0: break
    return amt + debit*BM_BASIS

def sim_etf(days, re, rb, rfv, spy_close, dref, st_eq, st_bd,
            strict=True, cap0=10_000_000.0, cap=2.0, first_day='fixed', drag=DRAG_PHYS):
    if strict:
        st_eq = S.strict_states(st_eq, days); st_bd = S.strict_states(st_bd, days)
    pb = 100.0*np.cumprod(1.0+np.asarray(rb))          # прокси-цена облигационной ноги
    e = cap0; n_e = 0; n_b = 0
    nav = np.empty(len(days))
    cap_events = 0; exec_days = 0; viol_after_costs = 0
    max_close_lev = 0.0; days_close_above = 0; min_ratio = float('inf')
    loan_pp = ter_pp = drag_pp = 0.0
    prev_se = prev_sb = None; prev_close_lev = 0.0
    for i, dte in enumerate(days):
        j = max(i-1, 0)
        p_e = spy_close[j]; p_b = pb[j]
        n0_e, n0_b = n_e, n_b
        eq_switch = (st_eq[i] != prev_se); bd_switch = (st_bd[i] != prev_sb)
        breach_at_open = (cap is not None) and (i > 0) and (prev_close_lev > cap)
        tgt_e = 1.0*st_eq[i]*e; tgt_b = 1.0*st_bd[i]*e
        exp_e = n_e*p_e; exp_b = n_b*p_b
        if eq_switch or abs(tgt_e-exp_e) > S.BAND*e:
            new = round(tgt_e/p_e)
            if new != n_e:
                e -= S.COST*abs(new-n_e)*p_e; n_e = new
        if bd_switch or abs(tgt_b-exp_b) > S.BAND*e:
            new = round(tgt_b/p_b)
            if new != n_b:
                e -= S.COST*abs(new-n_b)*p_b; n_b = new
        if cap is not None:
            def e_after(ne, nb):
                ea = e - S.COST*((abs(ne-n0_e)-abs(n_e-n0_e))*p_e + (abs(nb-n0_b)-abs(n_b-n0_b))*p_b)
                P = ne*p_e + nb*p_b
                ea -= (TER*P + drag*ne*p_e + loan_spread_amt(max(0.0, P+0.05*ea-ea)))/252.0
                return ea
            def breach(ne, nb):
                return (ne*p_e + nb*p_b) > cap*e_after(ne, nb)
            if breach_at_open or breach(n_e, n_b):
                pe0, pb0 = n_e, n_b
                ne = min(n_e, math.floor(tgt_e/p_e)) if breach_at_open else math.floor(tgt_e/p_e)
                nb = min(n_b, math.floor(tgt_b/p_b)) if breach_at_open else math.floor(tgt_b/p_b)
                while breach(ne, nb) and (ne > 0 or nb > 0):
                    if ne > 0 and (p_e >= p_b or nb == 0):
                        ne -= 1
                    else:
                        nb -= 1
                if (ne, nb) != (pe0, pb0):
                    e -= S.COST*((abs(ne-n0_e)-abs(n_e-n0_e))*p_e + (abs(nb-n0_b)-abs(n_b-n0_b))*p_b)
                    n_e, n_b = ne, nb
                    cap_events += 1
        prev_se, prev_sb = st_eq[i], st_bd[i]
        exp_e = n_e*p_e; exp_b = n_b*p_b; P = exp_e + exp_b
        session = (n_e != n0_e) or (n_b != n0_b)
        if session: exec_days += 1
        # известные собственные расходы дня: TER, drag, спред займа
        day_ter = TER*P/252.0; day_drag = drag*exp_e/252.0
        debit = max(0.0, P + 0.05*e - e)
        day_loan = loan_spread_amt(debit)/252.0
        e -= (day_ter + day_drag + day_loan)
        ter_pp += day_ter/e; drag_pp += day_drag/e; loan_pp += day_loan/e
        if cap is not None and session and P > cap*e:
            viol_after_costs += 1
        if i == 0 and first_day == 'fixed':
            nav[i] = e
        else:
            cash = e - P
            e = e + cash*rfv[i] + exp_e*re[i] + exp_b*rb[i]
            nav[i] = e
        lc = (n_e*spy_close[i] + n_b*pb[i])/e
        if lc > max_close_lev: max_close_lev = lc
        if cap is not None and lc > cap: days_close_above += 1
        if lc > 0:
            ratio = 1.0/(MAINT*lc)
            if ratio < min_ratio: min_ratio = ratio
        prev_close_lev = lc
    s = pd.Series(nav, index=days); r = s.pct_change(); r.iloc[0] = nav[0]/cap0-1
    yrs = (days[-1]-days[0]).days/365.25
    stats = dict(cap_events=cap_events, exec_days=exec_days, viol_after_costs=viol_after_costs,
                 max_close_lev=max_close_lev, days_close_above=days_close_above,
                 min_ratio=min_ratio, loan_pp_yr=loan_pp/yrs*100, ter_pp_yr=ter_pp/yrs*100,
                 drag_pp_yr=drag_pp/yrs*100)
    return r, s, stats
