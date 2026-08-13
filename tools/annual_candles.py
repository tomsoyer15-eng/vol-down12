#!/usr/bin/env python3
"""Годовые доходности ADD-FUT (маршрут Ф, ред. 32, окно 1963–2026) свечами.

Тело свечи — доходность года. Фитили — максимум и минимум накопленной внутригодовой
доходности (насколько счёт уходил вверх и вниз от уровня 1 января). Нижняя панель —
худшая просадка внутри года от собственного пика. Нижний блок — CAGR по десятилетиям.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'r32build'))
import sim_v13 as S, sim_v164 as S164


def build():
    d = S.build_splice()
    r, nav, st = S164.sim164(*d)
    r.name = 'ret'
    rows = []
    for y, g in r.groupby(r.index.year):
        cum = (1 + g).cumprod()
        dd = (cum / cum.cummax() - 1)
        rows.append(dict(year=y, close=(cum.iloc[-1] - 1) * 100,
                         high=(cum.max() - 1) * 100, low=(cum.min() - 1) * 100,
                         maxdd=dd.min() * 100, n=len(g)))
    df = pd.DataFrame(rows).set_index('year')
    full = df[df.n >= 200]                     # только полные годы
    dec = []
    for y0 in range(1970, 2030, 10):
        sl = r[(r.index.year >= y0 - 10) & (r.index.year < y0)]
        if len(sl) < 500: continue
        eq = (1 + sl).cumprod(); yrs = (sl.index[-1] - sl.index[0]).days / 365.25
        dec.append((f'{max(y0-10,1963)}–{y0-1}', (eq.iloc[-1] ** (1 / yrs) - 1) * 100))
    sl = r[r.index.year >= 2020]
    eq = (1 + sl).cumprod(); yrs = (sl.index[-1] - sl.index[0]).days / 365.25
    dec.append(('2020–2026', (eq.iloc[-1] ** (1 / yrs) - 1) * 100))
    eq = (1 + r).cumprod(); yrs = (r.index[-1] - r.index[0]).days / 365.25
    total = dict(cagr=(eq.iloc[-1] ** (1 / yrs) - 1) * 100,
                 maxdd=(eq / eq.cummax() - 1).min() * 100,
                 vol=r.std() * np.sqrt(252) * 100, years=yrs,
                 first=r.index[0], last=r.index[-1])
    return df, full, dec, total


if __name__ == '__main__':
    df, full, dec, tot = build()
    pd.set_option('display.width', 200)
    print(df.round(2).to_string())
    print('\nдесятилетия:', [(a, round(b, 2)) for a, b in dec])
    print('итого:', {k: (round(v, 2) if isinstance(v, float) else v) for k, v in tot.items()})
    print(f"\nлучший год: {full.close.idxmax()} {full.close.max():.1f}%   "
          f"худший: {full.close.idxmin()} {full.close.min():.1f}%")
    print(f"глубочайшая внутригодовая просадка: {full.maxdd.idxmin()} {full.maxdd.min():.1f}%")
    print(f"лет положительных: {(full.close>0).sum()} из {len(full)} ({(full.close>0).mean()*100:.0f}%)")
