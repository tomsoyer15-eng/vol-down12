#!/usr/bin/env python3
"""Нога Б на другой точке кривой: ZT (2 года) / ZF (5 лет) вместо ZN (10 лет).

Спецификация размеряет ногу Б по DV01: N = капитал x D_ref x 0,0001 / DV01_ZN, то есть
целевая величина — дюрационная экспозиция, а не номинал. Поэтому «заменить ZN на ZT»
означает набрать ТУ ЖЕ дюрацию коротким концом. Для срока T это множитель k = 10 / T
к номиналу: двухлетними бумагами той же дюрации нужно впятеро больше.

Отсюда два разных вопроса, и считаются оба:
  равный DV01   — тот же риск, номинал в k раз больше. Издержки 5 б.п. и спред 0,5 п.п.
                  берутся от НОМИНАЛА, поэтому в этом варианте они умножаются на k.
                  Учёт экспозиции спецификации (§1) DV01-нормирован, поэтому кап 2,00
                  от замены срока не меняется.
  равный номинал— 1,0 x капитал в бумаге срока T. Риск ноги падает примерно как T/10.

Данные: дневная бескупонная кривая ФРС (Gurkaynak-Sack-Wright, feds200628), SVENY01..10.
Доход постоянного срока T за день: P_t(T - dt) / P_{t-1}(T) - 1, где P(tau)=exp(-y(tau)*tau),
y интерполируется по сетке сроков. Формула включает и купонный доход, и скатывание по
кривой, и переоценку — одинаково для всех сроков.

Окно с 1971-09: SVENY10 начинается 16.08.1971 (более короткие сроки есть с 1961 года).
Сигнал — тот же фильтр 12-месячной средней по собственному ценовому ряду ноги.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'r32build'))
import sim_v13 as S
import smooth as M

GSW = Path('/tmp/gsw.csv')
TENORS = [2, 5, 7, 10]
REF = 10
START = pd.Timestamp('1971-09-01')


def curve():
    d = pd.read_csv(GSW, skiprows=9, parse_dates=['Date'], index_col='Date', low_memory=False)
    cols = [f'SVENY{n:02d}' for n in range(1, 11)]
    y = d[cols].apply(pd.to_numeric, errors='coerce') / 100.0
    return y.dropna(how='all').sort_index()


def cm_return(y, T, dt=1.0 / 252):
    """Доход постоянного срока T: продать бумагу, состарившуюся на день, купить новую."""
    grid = np.arange(1, 11, dtype=float)
    Y = y.values
    yT = np.array([np.interp(T, grid, row) for row in Y])
    yTm = np.array([np.interp(T - dt, grid, row) for row in Y])
    lnP_prev = -yT[:-1] * T
    lnP_now = -yTm[1:] * (T - dt)
    r = np.exp(lnP_now - lnP_prev) - 1
    return pd.Series(np.r_[0.0, r], index=y.index)


def sim(d, w_eq, w_bd, rb_leg, k=1.0, cap=2.0, spread_pp=0.5, cap0=10_000_000.0):
    """Движок ядра; k — множитель номинала ноги Б при неизменной DV01-экспозиции.
    P&L ноги Б = экспозиция x k x (доход - ставка - спред); издержки ноги Б x k."""
    days, re, rb0, rfv, spy_close, dref = d[:6]
    n = len(days); sd = spread_pp / 100 / 252
    e = cap0; n_e = n_b = 0; unit_is_mes = False
    nav = np.empty(n); rolls = S.roll_days_calendar(days)
    D_fix = dref[0]; exec_days = 0; maxlev = 0.0; prev_lev = 0.0; lev_sum = 0.0
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
        kc = np.array([1.0, k])                      # издержки ноги Б — от номинала
        tgt = np.array([w_eq[i], w_bd[i]]) * e
        if tgt.sum() > cap * e:
            tgt *= cap * e / tgt.sum()
        n0 = np.array([n_e, n_b], dtype=float); cur = n0.copy(); exp = cur * units
        for q, force in enumerate([edge_e, edge_b or roll]):
            if force or abs(tgt[q] - exp[q]) > S.BAND * e:
                new = round(tgt[q] / units[q])
                if new != cur[q]:
                    e -= S.COST * abs(new - cur[q]) * units[q] * kc[q]
                    cur[q] = new; exp[q] = new * units[q]

        def e_after(x):
            ea = e - S.COST * ((np.abs(x - n0) - np.abs(cur - n0)) * units * kc).sum()
            if roll: ea -= S.ROLL_BP * x[1] * units[1] * k
            return ea

        if (i > 0 and prev_lev > cap) or (cur * units).sum() > cap * e_after(cur):
            x = np.floor(tgt / units)
            if i > 0 and prev_lev > cap:
                x = np.minimum(cur, x)
            while (x * units).sum() > cap * e_after(x) and x.sum() > 0:
                x[int(np.argmax(np.where(x > 0, units, -np.inf)))] -= 1
            if not np.array_equal(x, cur):
                e -= S.COST * ((np.abs(x - n0) - np.abs(cur - n0)) * units * kc).sum()
                cur = x
        n_e, n_b = cur; exp = cur * units
        if (cur != n0).any() or (roll and n_b): exec_days += 1
        if roll: e -= S.ROLL_BP * exp[1] * k
        if i == 0:
            nav[i] = e
        else:
            pnl_e = exp[0] * (re[i] - rfv[i] - sd)
            pnl_b = exp[1] * k * (rb_leg[i] - rfv[i] - sd)
            e = e * (1 + rfv[i]) + pnl_e + pnl_b
            nav[i] = e
        mc = S.ES_MULT / 10 if unit_is_mes else S.ES_MULT
        uc = np.array([mc * spy_close[i],
                       (S.ZN_MODEL_PX_EQ * S.CTD_RATIO * D_fix * 1e-4) / (dref[i] * 1e-4)])
        prev_lev = (cur * uc).sum() / e
        lev_sum += prev_lev; maxlev = max(maxlev, prev_lev)
    s = pd.Series(nav, index=days); r = s.pct_change(); r.iloc[0] = nav[0] / cap0 - 1
    return r, dict(exec_days=exec_days, maxlev=maxlev, avglev=lev_sum / n)


if __name__ == '__main__':
    d0 = M.build(); days_f = d0[0]
    y = curve()
    legs = {}
    for T in TENORS:
        r = cm_return(y, T).reindex(days_f).fillna(0.0)
        legs[T] = r
    first = max(y.dropna().index[0], START)
    m = days_f >= first
    d = (days_f[m],) + tuple(np.asarray(a)[m] for a in d0[1:6])
    days = d[0]; yrs = (days[-1] - days[0]).days / 365.25
    w_eq = M.weights(d0[6], days_f, 'бинарное (ядро)')[m]

    print(f"окно {days[0].date()} - {days[-1].date()} ({yrs:.1f} лет)\n")
    print(f"{'срок':>6}{'качка ноги':>13}{'k номинала':>12}")
    W = {}
    for T in TENORS:
        idx = (1 + legs[T]).cumprod()
        W[T] = M.weights(idx, days_f, 'бинарное (ядро)')[m]
        print(f"{T:>5}л{legs[T][m].std()*np.sqrt(252)*100:>12.2f}%{REF/T:>12.2f}")

    # сверка: нога 10 лет по кривой ФРС против ряда ядра (IEF/реконструкция)
    rb_core = pd.Series(d[2], index=days)
    print(f"\nсверка: качка ноги ядра {rb_core.std()*np.sqrt(252)*100:.2f}%, "
          f"корреляция с моей 10-летней {rb_core.corr(legs[10][m].reset_index(drop=True).set_axis(days)):.3f}")

    for mode, kf in [('РАВНЫЙ DV01 (тот же риск, номинал x k)', lambda T: REF / T),
                     ('РАВНЫЙ НОМИНАЛ (1,0 x капитал)', lambda T: 1.0)]:
        print(f"\n=== {mode} ===")
        print(f"{'нога Б':>8}{'CAGR':>9}{'качка':>8}{'MaxDD':>9}{'D/V':>7}{'ср.плечо':>10}{'сд/год':>9}")
        out = {}
        for T in TENORS:
            r, st = sim(d, w_eq, W[T], legs[T][m].values, k=kf(T))
            out[T] = r
            c, v, dd, dv = M.met(r)
            tag = f"{T}л" + (" — ZN" if T == REF else (" — ZT" if T == 2 else (" — ZF" if T == 5 else "")))
            print(f"{tag:>8}{c:>8.2f}%{v:>7.2f}%{dd:>8.1f}%{dv:>7.2f}"
                  f"{st['avglev']:>10.2f}{st['exec_days']/yrs:>9.1f}")
        Wn = 2520
        cs = lambda r: np.concatenate([[0], np.cumsum(np.log1p(r.values))])
        i0 = np.arange(0, len(days) - Wn); i1 = i0 + Wn
        yy = np.array([(days[b] - days[a]).days / 365.25 for a, b in zip(i0, i1)])
        f = lambda r: (np.exp((cs(r)[i1 + 1] - cs(r)[i0 + 1]) / yy) - 1) * 100
        base = f(out[REF])
        print("  скользящие 10 лет против 10-летней ноги:")
        for T in TENORS:
            if T == REF: continue
            dif = f(out[T]) - base
            print(f"    {T}л: впереди {np.mean(dif>0)*100:>5.1f}% окон, медиана {np.median(dif):+6.2f} п.п.")
