#!/usr/bin/env python3
"""Защитный пут поверх ноги А: цена страховки от гэпа.

Замена фьючерса опционом на страйке эквивалентна покупке фьючерса плюс пут (паритет),
поэтому проверяется именно докупка пута. Пут переоценивается ЕЖЕДНЕВНО — иначе не видно
главного его свойства: он отыгрывает убыток в момент обвала, а не на экспирации.

Подразумеваемая волатильность строится из скользящей реализованной (окно 60 дней) с двумя
надбавками: 1,248 — измеренное превышение VIX над последующей реализованной качкой на
1990–2026, и скос по страйку, поскольку опционы ниже денег торгуются дороже паритетных.
Опционных котировок в проекте нет, поэтому это МОДЕЛЬ, а не измерение; направление
допущений выбрано в пользу пута там, где выбор неоднозначен.
"""
import sys
from pathlib import Path
from math import log, sqrt, exp
from statistics import NormalDist
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'r32build'))
import sim_v13 as S
import smooth as M

N = NormalDist().cdf
VRP = 1.248                       # надбавка подразумеваемой над реализованной
SKEW = {1.00: 0.0, 0.95: 0.02, 0.90: 0.04, 0.85: 0.06}   # добавка к vol за страйк ниже денег
TERM = 63                         # квартал в торговых днях


def bs_put(Sp, K, T, r, sig):
    if T <= 1e-6 or sig <= 1e-6:
        return max(K - Sp, 0.0)
    d1 = (log(Sp / K) + (r + sig * sig / 2) * T) / (sig * sqrt(T))
    return K * exp(-r * T) * N(-(d1 - sig * sqrt(T))) - Sp * N(-d1)


def sim(d, w_eq, w_bd, strike=None, band=0.03, cap=2.0, spread_pp=0.5, cap0=10_000_000.0):
    days, re, rb, rfv, spy_close, dref = d[:6]
    n = len(days); sd = spread_pp / 100 / 252
    lvl = (1 + pd.Series(re, index=days)).cumprod().values
    rv = pd.Series(re, index=days).rolling(60).std().mul(np.sqrt(252)).shift(1).bfill().values
    e = cap0; ne = nb = 0; mes = False; nav = np.empty(n)
    rolls = S.roll_days_calendar(days); D = dref[0]
    K = None; exp_i = -1; qty = 0.0; pv = 0.0; paid = 0.0
    for i, dte in enumerate(days):
        j = max(i - 1, 0)
        if (not mes) and dte >= S.MES_START:
            ne *= 10; mes = True
        m = S.ES_MULT / 10 if mes else S.ES_MULT
        ue = m * spy_close[j]; roll = dte in rolls
        ee = (w_eq[i] == 0) != (w_eq[j] == 0) if i else w_eq[i] > 0
        eb = (w_bd[i] == 0) != (w_bd[j] == 0) if i else w_bd[i] > 0
        if roll or eb: D = dref[j]
        ub = (S.ZN_MODEL_PX_EQ * S.CTD_RATIO * D * 1e-4) / (dref[j] * 1e-4)
        u = np.array([ue, ub]); tgt = np.array([w_eq[i], w_bd[i]]) * e
        if tgt.sum() > cap * e: tgt *= cap * e / tgt.sum()
        cur = np.array([ne, nb], float); ex = cur * u
        for k, f in enumerate([ee, eb or roll]):
            if f or abs(tgt[k] - ex[k]) > band * e:
                nw = round(tgt[k] / u[k])
                if nw != cur[k]:
                    e -= S.COST * abs(nw - cur[k]) * u[k]; cur[k] = nw; ex[k] = nw * u[k]
        ne, nb = cur; ex = cur * u
        if roll: e -= S.ROLL_BP * ex[1]
        # ---- защитный пут ----
        if strike is not None:
            iv = max(rv[i], 0.06) * VRP + SKEW[strike]
            if i >= exp_i or (qty > 0 and w_eq[i] == 0):        # экспирация или нога выключена
                if qty > 0:
                    e += qty * max(K - lvl[i], 0.0) - pv        # расчёт по внутренней
                qty = 0.0; pv = 0.0
            if qty == 0 and w_eq[i] > 0:                        # покупка нового
                K = lvl[i] * strike
                px = bs_put(lvl[i], K, TERM / 252, rfv[i] * 252, iv)
                qty = ex[0] / lvl[i]                            # номинал равен экспозиции ноги
                pv = qty * px; paid += pv / e; exp_i = i + TERM
            elif qty > 0:
                T = max((exp_i - i) / 252, 1e-6)
                nv = qty * bs_put(lvl[i], K, T, rfv[i] * 252, iv)
                e += nv - pv; pv = nv
        if i == 0:
            nav[i] = e
        else:
            e = e * (1 + rfv[i]) + (ex * (np.array([re[i], rb[i]]) - rfv[i] - sd)).sum()
            nav[i] = e
    s = pd.Series(nav, index=days); r = s.pct_change(); r.iloc[0] = nav[0] / cap0 - 1
    return r, paid


if __name__ == '__main__':
    d = M.build(); days = d[0]
    we = M.weights(d[6], days, 'бинарное (ядро)')
    wb = M.weights(d[7], days, 'бинарное (ядро)')
    yrs = (days[-1] - days[0]).days / 365.25
    print(f"окно {days[0]:%Y}–{days[-1]:%Y}\n")
    print(f"{'вариант':<20}{'CAGR':>8}{'качка':>8}{'MaxDD':>9}{'D/V':>7}{'просадка 1987':>16}")
    for strike, lbl in ((None, 'без пута'), (1.00, 'пут на деньгах'),
                        (0.90, 'пут −10%'), (0.85, 'пут −15%')):
        r, paid = sim(d, we, wb, strike=strike)
        c, v, dd, dv = M.met(r)
        eq = (1 + r).cumprod()
        m = (days >= '1987-08-25') & (days <= '1987-10-19')
        loc = (eq[m] / eq[m].cummax() - 1).min() * 100
        print(f"{lbl:<20}{c:>7.2f}%{v:>7.2f}%{dd:>8.1f}%{dv:>7.2f}{loc:>15.1f}%")
