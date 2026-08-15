#!/usr/bin/env python3
"""Вес ноги Б по текущей корреляции акций и облигаций (идея заказчика 14.08.2026).

ИДЕЯ. Нога Б нужна не сама по себе, а потому что хеджирует ногу А. Когда корреляция
отрицательная — хедж работает, ногу держим полностью. Когда корреляция растёт (2022-й:
акции и облигации падали вместе) — нога перестаёт быть хеджем, и её вес снижаем.

БЕЗ ПОДБОРА ПАРАМЕТРОВ. Отображение корреляции в вес взято БЕЗ свободных чисел:

    w = (1 - corr) / 2         corr=-1 -> вес 1;  corr=0 -> 0,5;  corr=+1 -> 0

Порогов, множителей и отсечек нет — иначе это была бы подгонка, запрещённая правилами
проекта. Единственный выбор — окно корреляции, и оно показано сразу тремя вариантами
(3, 6, 12 месяцев), чтобы видеть чувствительность, а не выбирать лучший.

КОНВЕНЦИЯ ТА ЖЕ, ЧТО У СИГНАЛА. Корреляция считается по данным ДО конца месяца M и
действует весь месяц M+1: подсмотра будущего нет, ритм торговли не меняется.

ВЕС НЕ ЗАМЕНЯЕТ СИГНАЛ, А УМНОЖАЕТ ЕГО. Включение и выключение ноги по-прежнему решает
§1; вес лишь задаёт, насколько полно нога загружена, когда сигнал её включил. Смена веса
НЕ считается переключением состояния: подаёт ли она заявку, решает полоса — иначе книга
торговалась бы каждый месяц независимо от величины изменения.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'r33build'))
import sim_v13 as S                                                    # noqa: E402
sys.path.insert(0, str(ROOT / 'tools'))
import sma_nogi as SN                                                  # noqa: E402


def ves_po_korrelyacii(days, re, rb, okno_mes):
    """Месячный вес ноги Б из корреляции дневных доходностей за okno_mes месяцев."""
    n = int(round(okno_mes * 21))
    a = pd.Series(re, index=days)
    b = pd.Series(rb, index=days)
    corr = a.rolling(n).corr(b)
    me = corr.resample('ME').last().dropna()
    w = ((1.0 - me) / 2.0).clip(0.0, 1.0)
    # действует в СЛЕДУЮЩЕМ месяце — та же конвенция, что у сигнала §1
    wm = pd.Series(w.values, index=(w.index + pd.offsets.MonthBegin(1)).to_period('M'))
    out = wm.reindex(days.to_period('M')).ffill()
    return out.fillna(1.0).values          # до накопления окна — полный вес


def sim_ves(days, re, rb, rfv, spy_close, dref, st_eq, st_bd, w_b=None,
            spread_pp=0.5, strict=True, cap0=10_000_000.0):
    """Копия цикла sim_v13.sim с весом ноги Б. Отличие ровно одно — множитель tgt_b."""
    sd = spread_pp / 100 / 252
    if strict:
        st_eq = S.strict_states(st_eq, days)
        st_bd = S.strict_states(st_bd, days)
    if w_b is None:
        w_b = np.ones(len(days))
    e = cap0
    n_e = n_b = 0
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
        tgt_b = 1.0 * st_bd[i] * e * w_b[i]        # <-- единственное отличие
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
        e = e * (1 + rfv[i]) + exp_e * (re[i] - rfv[i] - sd) + exp_b * (rb[i] - rfv[i] - sd)
        nav[i] = e
    s = pd.Series(nav, index=days)
    r = s.pct_change()
    r.iloc[0] = nav[0] / cap0 - 1
    return r, s, trades


def _mery(r, trades):
    eqc = (1 + r).cumprod()
    yrs = (r.index[-1] - r.index[0]).days / 365.25
    dd = eqc / eqc.cummax() - 1
    mes = eqc.resample('ME').last().pct_change().dropna()
    return dict(cagr=(eqc.iloc[-1] ** (1 / yrs) - 1) * 100, dd=dd.min() * 100,
                худший=mes.min() * 100, сделок=trades / yrs)


if __name__ == '__main__':
    print('ПРОВЕРКА КОПИИ (при весе 1 обязана совпасть с движком)\n')
    nabory = [('склейка 1963–2026', SN.nabor_splice(12, 12)),
              ('современное окно 2003–2026', SN.nabor_modern(12, 12))]
    for imya, nabor in nabory:
        r0, nav0, tr0 = S.sim(*nabor, strict=True)
        r1, nav1, tr1 = sim_ves(*nabor, w_b=None, strict=True)
        rel = float(np.max(np.abs(nav0.values - nav1.values))) / float(np.max(np.abs(nav0.values)))
        ok = rel < 1e-12 and tr0 == tr1
        print(f'[{"OK  " if ok else "ПРОВАЛ"}] {imya}: расхождение {rel:.2e}, '
              f'сделок {tr0}/{tr1}')
        if not ok:
            raise SystemExit('копия не воспроизводит движок')

    print('\n\nВЕС НОГИ Б ПО КОРРЕЛЯЦИИ (w = (1-corr)/2, без свободных чисел)\n')
    for imya, nabor in nabory:
        days, re, rb = nabor[0], nabor[1], nabor[2]
        print(f'--- {imya}')
        print(f'{"вариант":<22}{"CAGR":>8}{"просадка":>11}{"худший мес":>13}{"сделок/год":>12}')
        r, nav, tr = sim_ves(*nabor, w_b=None, strict=True)
        m = _mery(r, tr)
        print(f'{"норматив (вес 1)":<22}{m["cagr"]:>7.2f}%{m["dd"]:>10.1f}%'
              f'{m["худший"]:>12.1f}%{m["сделок"]:>12.1f}')
        for okno in (3, 6, 12):
            w = ves_po_korrelyacii(days, re, rb, okno)
            r, nav, tr = sim_ves(*nabor, w_b=w, strict=True)
            m = _mery(r, tr)
            print(f'{f"корреляция {okno} мес":<22}{m["cagr"]:>7.2f}%{m["dd"]:>10.1f}%'
                  f'{m["худший"]:>12.1f}%{m["сделок"]:>12.1f}   средний вес {w.mean():.2f}')
        print()
