#!/usr/bin/env python3
"""Из чего держать ЗАЛОГ: короткие векселя против десятилетних нот.

ВОПРОС ЗАКАЗЧИКА (14.08.2026). Внешний расчёт давал по стратегии с залогом в векселях
13,52% / −24,9%, а с залогом в десятилетках 14,47% / −43,3%, и вывод «длинные облигации в
залоге запрещены». Числа не сходились ни с чем в проекте, поэтому проверяются на НАШИХ
рядах и НАШИМ движком.

КАК ЭТО ЧЕСТНО СЧИТАТЬ. Замороженный sim_v13 править запрещено (регрессия 1e-12), поэтому
здесь стоит КОПИЯ его цикла с одним добавленным параметром. Копия сначала ДОКАЗЫВАЕТСЯ:
на базовом режиме она обязана совпасть с sim_v13.sim до 1e-12 на каждой сессии. Только
после этого меняется ровно одно слагаемое — доход на собственный капитал.

ЧТО ИМЕННО МЕНЯЕТСЯ. В движке NAV растёт так:

    e = e*(1+rf) + exp_A*(r_A - rf - spread) + exp_B*(r_B - rf - spread)

Первое слагаемое — доход на СВОБОДНЫЕ ДЕНЬГИ (залог). В движке это rf, то есть ставка
трёхмесячных векселей DTB3: модель проекта УЖЕ предполагает залог в векселях, и все
опубликованные цифры (11,00% / −33,7%) посчитаны при этой посылке. Вариант «залог в
десятилетках» заменяет rf на полную доходность десятилетней ноты r_B — ту же, что
получает нога Б. Плата за финансирование фьючерсов (−rf у каждой ноги) НЕ меняется: она
сидит в цене самого фьючерса и от состава залога не зависит.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'r33build'))
import sim_v13 as S                                                    # noqa: E402


def sim_zalog(days, re, rb, rfv, spy_close, dref, st_eq, st_bd, spread_pp=0.5,
              strict=True, cap0=10_000_000.0, zalog='векселя'):
    """Копия цикла sim_v13.sim с выбором залога. Ни строкой больше."""
    sd = spread_pp / 100 / 252
    if strict:
        st_eq = S.strict_states(st_eq, days)
        st_bd = S.strict_states(st_bd, days)
    e = cap0
    n_e = 0
    n_b = 0
    unit_is_mes = False
    nav = np.empty(len(days))
    trades = 0
    rolls = S.roll_days_calendar(days)
    prev_se = prev_sb = None
    D_fix = dref[0]
    for i, dte in enumerate(days):
        j = max(i - 1, 0)
        if (not unit_is_mes) and dte >= S.MES_START:
            n_e *= 10
            unit_is_mes = True
        mult = S.ES_MULT / 10 if unit_is_mes else S.ES_MULT
        unit_e = mult * spy_close[j]
        eq_switch = (st_eq[i] != prev_se)
        bd_switch = (st_bd[i] != prev_sb)
        roll_today = (dte in rolls)
        if roll_today or bd_switch:
            D_fix = dref[j]
        q_b = (S.ZN_MODEL_PX_EQ * S.CTD_RATIO * D_fix * 1e-4) / (dref[j] * 1e-4)
        tgt_e = 1.0 * st_eq[i] * e
        tgt_b = 1.0 * st_bd[i] * e
        exp_e = n_e * unit_e
        exp_b = n_b * q_b
        if eq_switch or abs(tgt_e - exp_e) > S.BAND * e:
            new = round(tgt_e / unit_e)
            if new != n_e:
                e -= S.COST * abs(new - n_e) * unit_e
                trades += 1
                n_e = new
        if bd_switch or roll_today or abs(tgt_b - exp_b) > S.BAND * e:
            new = round(tgt_b / q_b)
            if new != n_b:
                e -= S.COST * abs(new - n_b) * q_b
                trades += 1
                n_b = new
        prev_se, prev_sb = st_eq[i], st_bd[i]
        exp_e = n_e * unit_e
        exp_b = n_b * q_b
        if roll_today:
            e -= S.ROLL_BP * (exp_e + exp_b)
        # ЕДИНСТВЕННОЕ ОТЛИЧИЕ ОТ ДВИЖКА — доход на собственный капитал (залог).
        coll = rfv[i] if zalog == 'векселя' else rb[i]
        e = e * (1 + coll) + exp_e * (re[i] - rfv[i] - sd) + exp_b * (rb[i] - rfv[i] - sd)
        nav[i] = e
    s = pd.Series(nav, index=days)
    r = s.pct_change()
    r.iloc[0] = nav[0] / cap0 - 1
    return r, s, trades


def _mery(r):
    eqc = (1 + r).cumprod()
    yrs = (r.index[-1] - r.index[0]).days / 365.25
    dd = eqc / eqc.cummax() - 1
    mes = eqc.resample('ME').last().pct_change().dropna()
    return dict(cagr=(eqc.iloc[-1] ** (1 / yrs) - 1) * 100, dd=dd.min() * 100,
                худший_месяц=mes.min() * 100, худший_дата=mes.idxmin(),
                месяцев_хуже_10=int((mes < -0.10).sum()))


def _sverka(nabor, imya):
    """КОПИЯ ДОКАЗЫВАЕТСЯ ДО ЛЮБЫХ ВЫВОДОВ: базовый режим обязан совпасть с движком."""
    r0, nav0, tr0 = S.sim(*nabor, strict=True)
    r1, nav1, tr1 = sim_zalog(*nabor, strict=True, zalog='векселя')
    d = float(np.max(np.abs(nav0.values - nav1.values)))
    rel = d / float(np.max(np.abs(nav0.values)))
    ok = rel < 1e-12 and tr0 == tr1
    print(f'[{"OK  " if ok else "ПРОВАЛ"}] {imya}: копия против движка — расхождение NAV '
          f'{rel:.2e} (сделок {tr0} против {tr1})')
    if not ok:
        raise SystemExit('копия не воспроизводит движок — считать по ней нельзя')
    return nav0


if __name__ == '__main__':
    print('ПРОВЕРКА КОПИИ (без неё выводы недействительны)\n')
    nabory = [('склейка 1963–2026', S.build_splice()),
              ('современное окно 2003–2026', S.build_modern())]
    for imya, nabor in nabory:
        _sverka(nabor, imya)

    print('\n\nЗАЛОГ: ВЕКСЕЛЯ ПРОТИВ ДЕСЯТИЛЕТНИХ НОТ\n')
    for imya, nabor in nabory:
        print(f'--- {imya}')
        print(f'{"залог":<14}{"CAGR":>8}{"просадка":>11}{"худший мес":>13}'
              f'{"когда":>9}{"мес хуже -10%":>15}')
        for z in ('векселя', 'десятилетки'):
            r, nav, tr = sim_zalog(*nabor, strict=True, zalog=z)
            m = _mery(r)
            print(f'{z:<14}{m["cagr"]:>7.2f}%{m["dd"]:>10.1f}%{m["худший_месяц"]:>12.1f}%'
                  f'{m["худший_дата"]:%Y-%m}'.rjust(9)
                  + f'{m["месяцев_хуже_10"]:>15}')
        print()
