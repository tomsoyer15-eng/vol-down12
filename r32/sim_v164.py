# -*- coding: utf-8 -*-
"""Маршрут Ф спецификации ADD-FUT v1.6.0 ред. 4. Исправления по вердикту ревью ред. 3:
  1) триггер капа = БУКВАЛЬНО сохранённое плечо фактического закрытия предыдущей
     сессии (prev_close_lev > cap), без пересчёта через обновлённый D_fix;
  2) неравенство аллокатора учитывает ВСЕ известные собственные расходы сессии:
     комиссии (нетто) и ожидаемую ролловую издержку;
  3) один нетто-ордер на ногу за сессию: если кап корректирует ногу после штатной
     логики, комиссия пересчитывается на |конечная − стартовая| позицию (переплата
     штатного шага возвращается), число изменений ноги считается по сессии;
  4) инвариант: на каждой сессии исполнения (сделки либо ролл с позицией)
     плечо по ценам предыдущего закрытия / капитал ПОСЛЕ всех известных
     собственных расходов ≤ cap; счётчик нарушений обязан быть нулём;
  5) map_mes(n) — детерминированный execution-mapper внутренней MES-сетки в книгу.
Режим first_day='legacy', cap=None воспроизводит замороженный sim_v13
(численная регрессия 1e-12 к эталонным NAV)."""
import math
import pandas as pd, numpy as np
import sim_v13 as S

def map_mes(n):
    """Внутренняя сетка n (в MES) -> книга: (целые ES, остаток MES), n = 10*ES + MES."""
    return (n // 10, n % 10)

def sim164(days, re, rb, rfv, spy_close, dref, st_eq, st_bd,
           spread_pp=0.5, strict=True, cap0=10_000_000.0, cap=2.0, first_day='fixed'):
    sd = spread_pp/100/252
    if strict:
        st_eq = S.strict_states(st_eq, days); st_bd = S.strict_states(st_bd, days)
    e = cap0; n_e = 0; n_b = 0; unit_is_mes = False
    nav = np.empty(len(days))
    leg_changes = 0; cap_events = 0; cap_max_pct = 0.0
    exec_days = 0; viol_after_costs = 0
    max_close_lev = 0.0; days_close_above = 0
    rolls = S.roll_days_calendar(days)
    prev_se = prev_sb = None; D_fix = dref[0]; prev_close_lev = 0.0
    for i, dte in enumerate(days):
        j = max(i-1, 0)
        if (not unit_is_mes) and dte >= S.MES_START:
            n_e *= 10; unit_is_mes = True
        mult = S.ES_MULT/10 if unit_is_mes else S.ES_MULT
        unit_e = mult*spy_close[j]
        n0_e, n0_b = n_e, n_b
        eq_switch = (st_eq[i] != prev_se); bd_switch = (st_bd[i] != prev_sb); roll_today = (dte in rolls)
        if roll_today or bd_switch:
            D_fix = dref[j]
        q_b = (S.ZN_MODEL_PX_EQ*S.CTD_RATIO*D_fix*1e-4)/(dref[j]*1e-4)
        breach_at_open = (cap is not None) and (i > 0) and (prev_close_lev > cap)
        tgt_e = 1.0*st_eq[i]*e; tgt_b = 1.0*st_bd[i]*e
        # штатная логика ног (замороженная; издержки как в v1.5.3.1)
        exp_e = n_e*unit_e; exp_b = n_b*q_b
        if eq_switch or abs(tgt_e-exp_e) > S.BAND*e:
            new = round(tgt_e/unit_e)
            if new != n_e:
                e -= S.COST*abs(new-n_e)*unit_e; n_e = new
        if bd_switch or roll_today or abs(tgt_b-exp_b) > S.BAND*e:
            new = round(tgt_b/q_b)
            if new != n_b:
                e -= S.COST*abs(new-n_b)*q_b; n_b = new
        # кап-блок
        if cap is not None:
            def e_after(ne, nb):
                # капитал после всех известных собственных расходов сессии при конечной позиции (ne, nb):
                # нетто-пересчёт комиссий относительно стартовой позиции + ожидаемый ролл
                ea = e - S.COST*((abs(ne-n0_e)-abs(n_e-n0_e))*unit_e + (abs(nb-n0_b)-abs(n_b-n0_b))*q_b)
                if roll_today:
                    ea -= S.ROLL_BP*(ne*unit_e + nb*q_b)
                return ea
            def breach(ne, nb):
                return (ne*unit_e + nb*q_b) > cap*e_after(ne, nb)
            if breach_at_open or breach(n_e, n_b):
                pe0, pb0 = n_e, n_b
                ne = min(n_e, math.floor(tgt_e/unit_e)) if breach_at_open else math.floor(tgt_e/unit_e)
                nb = min(n_b, math.floor(tgt_b/q_b)) if breach_at_open else math.floor(tgt_b/q_b)
                while breach(ne, nb) and (ne > 0 or nb > 0):
                    if ne > 0 and (unit_e >= q_b or nb == 0):
                        ne -= 1
                    else:
                        nb -= 1
                if (ne, nb) != (pe0, pb0):
                    # нетто-ордер: вернуть переплату штатного шага, списать по |конечная - стартовая|
                    e -= S.COST*((abs(ne-n0_e)-abs(n_e-n0_e))*unit_e + (abs(nb-n0_b)-abs(n_b-n0_b))*q_b)
                    dpct = (abs(ne-pe0)*unit_e + abs(nb-pb0)*q_b)/e
                    if dpct > cap_max_pct: cap_max_pct = dpct
                    n_e, n_b = ne, nb
                    cap_events += 1
        prev_se, prev_sb = st_eq[i], st_bd[i]
        if n_e != n0_e: leg_changes += 1
        if n_b != n0_b: leg_changes += 1
        exp_e = n_e*unit_e; exp_b = n_b*q_b
        session = (n_e != n0_e) or (n_b != n0_b) or (roll_today and (n_e != 0 or n_b != 0))
        if session: exec_days += 1
        if roll_today: e -= S.ROLL_BP*(exp_e+exp_b)
        # инвариант: после всех известных собственных расходов, до рыночного P&L
        if cap is not None and session and (exp_e+exp_b) > cap*e:
            viol_after_costs += 1
        if i == 0 and first_day == 'fixed':
            nav[i] = e
        else:
            e = e*(1+rfv[i]) + exp_e*(re[i]-rfv[i]-sd) + exp_b*(rb[i]-rfv[i]-sd)
            nav[i] = e
        mult_c = S.ES_MULT/10 if unit_is_mes else S.ES_MULT
        q_b_c = (S.ZN_MODEL_PX_EQ*S.CTD_RATIO*D_fix*1e-4)/(dref[i]*1e-4)
        lc = (n_e*mult_c*spy_close[i] + n_b*q_b_c)/e
        if lc > max_close_lev: max_close_lev = lc
        if cap is not None and lc > cap: days_close_above += 1
        prev_close_lev = lc
    s = pd.Series(nav, index=days); r = s.pct_change(); r.iloc[0] = nav[0]/cap0-1
    stats = dict(leg_changes=leg_changes, cap_events=cap_events, exec_days=exec_days,
                 max_close_lev=max_close_lev, days_close_above=days_close_above,
                 cap_max_pct=cap_max_pct, viol_after_costs=viol_after_costs)
    return r, s, stats
