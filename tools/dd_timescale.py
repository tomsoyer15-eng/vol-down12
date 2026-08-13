#!/usr/bin/env python3
"""Диагностика: задаёт ли окно средней собственный масштаб просадок ядра?

Гипотеза владельца: две худшие современные просадки длились 186 и 462 дня, то есть
порядка года — ровно столько, сколько составляет окно скользящей средней. Если просадки
порождает сам фильтр (запоздалый вход, запоздалый выход, пила), их длительность должна
двигаться вслед за окном. Если это свойство рынка, длительность от окна не зависит.

ВАЖНО: это диагностика механизма, а не подбор параметра. Таблицы намеренно не содержат
ранжирования по доходности — окно 12 месяцев зафиксировано в спецификации априори,
и менять его по результатам прогонов запрещено правилом проекта.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'r32build'))
import sim_v13 as S


def sigs_n(px, days, n):
    """Тот же фильтр, что в ядре, но с произвольным окном n месяцев."""
    me = px.resample('ME').last().ffill()
    sma = me.rolling(n).mean()
    s = (me > sma)[sma.notna()]
    sm = pd.Series(s.values, index=(s.index + pd.offsets.MonthBegin(1)).to_period('M'))
    return sm.reindex(days.to_period('M')).ffill().astype(bool).values


def build_n(n):
    """Копия S.build_splice с окном средней n месяцев."""
    D = S.D
    spy = pd.read_csv(D / 'spy_daily.csv', parse_dates=['date'], index_col='date')
    ief = pd.read_csv(D / 'ief_daily.csv', parse_dates=['date'], index_col='date')['adj']
    mk = pd.read_csv(D / 'us-market-daily-1885-2025.csv', parse_dates=['Date']).set_index('Date').sort_index()
    eq_r = pd.concat([mk['Adjusted Close'].pct_change(), spy['adj'].pct_change()[spy.index > mk.index[-1]]])
    recon = pd.read_csv(D / 'bond10_recon_daily.csv', parse_dates=['Date'], index_col='Date')['ret']
    bond_idx = (1 + pd.concat([recon[recon.index < ief.index[0]],
                               ief.pct_change().dropna()])).cumprod().reindex(eq_r.index).ffill()
    days = eq_r.index[eq_r.index >= pd.Timestamp('1963-03-01')]
    re = eq_r.reindex(days).fillna(0).values
    rb = bond_idx.pct_change().reindex(days).fillna(0).values
    rfv = S.rf_series(days).values
    eq_lvl = (1 + pd.Series(re, index=days)).cumprod()
    synth_close = (spy['close'].iloc[-1] * eq_lvl / eq_lvl.iloc[-1]).values
    y = pd.read_csv(D / 'dgs10.csv', parse_dates=['observation_date']).set_index('observation_date')['DGS10'].dropna() / 100
    Dref = pd.Series([S.dur(v) for v in y.values], index=y.index).reindex(days).ffill().bfill()
    eq_full = (1 + eq_r.fillna(0)).cumprod()
    return (days, re, rb, rfv, synth_close, Dref.values,
            sigs_n(eq_full, days, n), sigs_n(bond_idx.dropna(), days, n))


def episodes(r, thresh=0.10):
    """Эпизоды просадки глубже thresh: (начало, дно, конец, глубина, дней до дна)."""
    eq = (1 + r).cumprod(); dd = eq / eq.cummax() - 1
    out = []; inep = False
    for t, v in dd.items():
        if not inep and v < -1e-9:
            inep = True; a = t; b = t; lo = v
        elif inep:
            if v < lo: lo = v; b = t
            if v >= -1e-9:
                out.append((a, b, t, lo)); inep = False
    if inep: out.append((a, b, dd.index[-1], lo))
    res = []
    for a, b, c, lo in out:
        if lo < -thresh:
            res.append((a, b, c, lo, len(r[(r.index >= a) & (r.index <= b)])))
    return res


if __name__ == '__main__':
    print("окно средней -> масштаб просадок ядра (эпизоды глубже 10%)\n")
    print(f"{'окно, мес':>10}{'эпизодов':>10}{'медиана до дна':>16}{'в месяцах':>11}"
          f"{'средняя':>10}{'глубже 20%':>12}{'сделок/год':>12}{'MaxDD':>9}")
    rows = []
    for n in [3, 6, 9, 12, 18, 24, 36]:
        d = build_n(n)
        days = d[0]; yrs = (days[-1] - days[0]).days / 365.25
        r, nav, tr = S.sim(*d)
        ep = episodes(r)
        dur = np.array([e[4] for e in ep])
        deep = sum(1 for e in ep if e[3] < -0.20)
        st_e = S.strict_states(d[6], days); st_b = S.strict_states(d[7], days)
        sw = (np.diff(st_e.astype(int)) != 0).sum() + (np.diff(st_b.astype(int)) != 0).sum()
        mdd = ((1 + r).cumprod() / (1 + r).cumprod().cummax() - 1).min() * 100
        rows.append((n, len(ep), np.median(dur), np.median(dur) / 21, dur.mean(), deep, sw / yrs, mdd))
        print(f"{n:>10}{len(ep):>10}{np.median(dur):>15.0f}д{np.median(dur)/21:>11.1f}"
              f"{dur.mean():>10.0f}{deep:>12}{sw/yrs:>12.1f}{mdd:>8.1f}%")

    print("\nсоотношение медианной длительности просадки к окну средней:")
    for n, ne, md, mm, mean, deep, sw, mdd in rows:
        print(f"  окно {n:>2} мес -> просадка {mm:>4.1f} мес, отношение {mm/n:>4.2f}")

    print("\nпила внутри просадок против спокойного времени (окно 12 месяцев):")
    d = build_n(12); days = d[0]; r, nav, tr = S.sim(*d)
    st_e = S.strict_states(d[6], days); st_b = S.strict_states(d[7], days)
    swd = pd.Series(np.r_[False, (np.diff(st_e.astype(int)) != 0) | (np.diff(st_b.astype(int)) != 0)], index=days)
    ep = episodes(r)
    mask = pd.Series(False, index=days)
    for a, b, c, lo, nd in ep:
        mask[(days >= a) & (days <= c)] = True
    for lbl, m in [('внутри эпизодов просадки', mask), ('вне их', ~mask)]:
        print(f"  {lbl:<28} дней {m.sum():>6} ({m.mean()*100:>4.1f}% времени), "
              f"переключений {swd[m].sum():>3}, то есть {swd[m].sum()/(m.sum()/252):>4.2f} в год")
