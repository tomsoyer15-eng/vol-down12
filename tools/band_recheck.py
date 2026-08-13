#!/usr/bin/env python3
"""Перепроверка полосы по замечаниям внешней критики ред. 33.

ТРИ ВОПРОСА, на которые ред. 33 ответа не давала.

(1) НЕДОЭКСПОЗИЦИЯ. Снижение числа кап-коррекций при широкой полосе можно объяснить двояко:
меньше суеты — или позиция систематически недотянута до цели. Если верно второе, потеря
доходности есть цена недоэкспозиции, а не оборота, и сравнение полос некорректно.
Измеряется прямо: средняя экспозиция, средний модуль отрыва от цели, доля времени вне цели.

(2) ВЫРАВНЕННЫЙ РИСК. Ко всем надстройкам он применялся, к полосе — нет. Если широкая
полоса просто держит меньше риска, её надо усилить множителем до качки базы и сравнивать
после этого.

(3) ПОЛНОЕ ОКНО И ФАЗЫ. Дневной тест шёл на 1963–2026, месячный на полном окне. Здесь
полоса проверяется на дневном полном окне 1886–2026 и на 21 фазе наблюдения.
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

BANDS = [0.03, 0.05, 0.10, 0.15]


def sim_stats(d, we, wb, band, cap=2.0, spread_pp=0.5, cap0=10_000_000.0):
    """Как rebal.sim, но дополнительно копит статистику отрыва от цели."""
    days, re, rb, rfv, spy_close, dref = d[:6]
    n = len(days); sd = spread_pp / 100 / 252
    e = cap0; ne = nb = 0; mes = False; nav = np.empty(n)
    rolls = S.roll_days_calendar(days); D = dref[0]
    off_sum = 0.0; off_days = 0; exp_sum = 0.0; lev_sum = 0.0; trades = 0
    for i, dte in enumerate(days):
        j = max(i - 1, 0)
        if (not mes) and dte >= S.MES_START:
            ne *= 10; mes = True
        m = S.ES_MULT / 10 if mes else S.ES_MULT
        ue = m * spy_close[j]; roll = dte in rolls
        ee = (we[i] == 0) != (we[j] == 0) if i else we[i] > 0
        eb = (wb[i] == 0) != (wb[j] == 0) if i else wb[i] > 0
        if roll or eb: D = dref[j]
        ub = (S.ZN_MODEL_PX_EQ * S.CTD_RATIO * D * 1e-4) / (dref[j] * 1e-4)
        u = np.array([ue, ub]); tgt = np.array([we[i], wb[i]]) * e
        if tgt.sum() > cap * e: tgt *= cap * e / tgt.sum()
        cur = np.array([ne, nb], float); ex = cur * u
        for k, f in enumerate([ee, eb or roll]):
            if f or abs(tgt[k] - ex[k]) > band * e:
                nw = round(tgt[k] / u[k])
                if nw != cur[k]:
                    e -= S.COST * abs(nw - cur[k]) * u[k]; cur[k] = nw; ex[k] = nw * u[k]; trades += 1
        ne, nb = cur; ex = cur * u
        if roll: e -= S.ROLL_BP * ex[1]
        # отрыв от цели: только там, где цель ненулевая (выключенная нога не «вне цели»)
        act = (tgt > 0)
        if act.any():
            off_sum += np.abs(tgt - ex)[act].sum() / e
            off_days += 1
        exp_sum += ex.sum() / e
        if i == 0:
            nav[i] = e
        else:
            e = e * (1 + rfv[i]) + (ex * (np.array([re[i], rb[i]]) - rfv[i] - sd)).sum()
            nav[i] = e
        lev_sum += ex.sum() / e
    s = pd.Series(nav, index=days); r = s.pct_change(); r.iloc[0] = nav[0] / cap0 - 1
    return r, dict(off=off_sum / max(off_days, 1), exp=exp_sum / n,
                   lev=lev_sum / n, trades=trades)


def match(d, we, wb, band, target_v, cap=2.0):
    lo, hi = 0.9, 1.4
    for _ in range(13):
        k = (lo + hi) / 2
        r, _ = sim_stats(d, we * k, wb * k, band, cap=cap * k)
        if M.met(r)[1] < target_v: lo = k
        else: hi = k
    r, st = sim_stats(d, we * k, wb * k, band, cap=cap * k)
    return M.met(r), k, st


def weights_of(d):
    days = d[0]
    if isinstance(d[6], np.ndarray):
        return (S.strict_states(d[6] > 0, days).astype(float),
                S.strict_states(d[7] > 0, days).astype(float))
    return (M.weights(d[6], days, 'бинарное (ядро)'), M.weights(d[7], days, 'бинарное (ядро)'))


if __name__ == '__main__':
    for nm, d in (('дневное окно ядра 1963–2026', M.build()),
                  ('ПОЛНОЕ дневное окно 1886–2026', B.build())):
        days = d[0]; we, wb = weights_of(d)
        yrs = (days[-1] - days[0]).days / 365.25
        print(f"\n=== {nm} ===")
        base_r, base_st = sim_stats(d, we, wb, 0.03)
        tv = M.met(base_r)[1]
        print(f"{'полоса':<9}{'CAGR':>8}{'качка':>8}{'MaxDD':>9}{'D/V':>7}"
              f"{'ср.эксп.':>10}{'отрыв':>9}{'сд/год':>8}{'CAGR при равном риске':>24}")
        for b in BANDS:
            r, st = sim_stats(d, we, wb, b)
            c, v, dd, dv = M.met(r)
            if b == 0.03:
                mc, k = (c, v, dd, dv), 1.0
            else:
                mc, k, _ = match(d, we, wb, b, tv)
            print(f"{b*100:>6.0f}%  {c:>7.2f}%{v:>7.2f}%{dd:>8.1f}%{dv:>7.2f}"
                  f"{st['exp']:>10.3f}{st['off']*100:>8.2f}%{st['trades']/yrs:>8.1f}"
                  f"{mc[0]:>18.2f}% (×{k:.3f})")

    # --- фазы наблюдения: 3% против 10% ---
    d = B.build(); days = d[0]
    eq = (1 + pd.Series(d[1], index=days)).cumprod()
    bd = (1 + pd.Series(d[2], index=days)).cumprod()
    print("\n=== 21 фаза наблюдения, полное окно: 3% против 10% ===")
    print(f"{'полоса':<9}{'CAGR медиана':>14}{'CAGR худшая':>13}"
          f"{'MaxDD медиана':>15}{'MaxDD худшая':>14}")
    for b in (0.03, 0.10):
        res = [M.met(sim_stats(d, SB.w_phase(eq, days, p), SB.w_phase(bd, days, p), b)[0])
               for p in range(SB.PER)]
        c = np.array([x[0] for x in res]); dd = np.array([x[2] for x in res])
        print(f"{b*100:>6.0f}%  {np.median(c):>13.2f}%{c.min():>12.2f}%"
              f"{np.median(dd):>14.1f}%{dd.min():>13.1f}%")
