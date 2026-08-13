#!/usr/bin/env python3
"""Переключение дешевейшей к поставке: проверка на полной истории ZN с 1982 года.

Гипотеза (из механики контракта): при доходностях НИЖЕ условного купона дешевейшей
становится короткий край корзины и дюрация контракта падает; при доходностях ВЫШЕ —
переключается на длинный край и дюрация растёт. Условный купон ZN: 8% до контрактов
марта 2000 года, 6% начиная с них.

Проверяется отношение k = D_подразумеваемая / D_ref, где D_ref — дюрация десятилетней
купонной бумаги по формуле ядра. В спецификации k зафиксировано равным 0,88.
"""
import pickle, sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'r32build'))
import sim_v13 as S
import bondleg as B

MONTH = dict(zip('FGHJKMNQUVXZ', range(1, 13)))
expiry_key = lambda c: int(c[:4]) * 100 + MONTH[c[4]]
SWITCH = pd.Timestamp('2000-03-01')          # с этой поставки условный купон 6%


def front(contracts):
    """Передний контракт по открытому интересу; при его отсутствии — по ближайшему сроку."""
    rows = []
    for rank, c in enumerate(sorted(contracts, key=expiry_key)):
        t = contracts[c].copy()
        t['code'] = c
        t['rank'] = rank
        t['oi'] = t['Open Interest'] if 'Open Interest' in t else 0
        rows.append(t[['Close', 'code', 'rank', 'oi']])
    a = pd.concat(rows)
    has_oi = a.groupby(level=0)['oi'].max() > 0
    a['key'] = np.where(a.index.map(has_oi).values, -a['oi'].values, a['rank'].values)
    return a.sort_values('key').groupby(level=0).first().sort_index()


def implied_duration(px_code, y, lo=None, hi=None, col='SVENY07'):
    same = px_code['code'] == px_code['code'].shift()
    r = np.log(px_code['Close']).diff()[same]
    if lo is not None:
        r = r[(r.index >= lo) & (r.index < hi)]
    dy = y[col].reindex(r.index).ffill().diff()
    m = r.notna() & dy.notna() & (dy != 0)
    if m.sum() < 60:
        return np.nan, np.nan, int(m.sum())
    b, _ = np.polyfit(dy[m], r[m], 1)
    R2 = np.corrcoef(dy[m], r[m])[0, 1] ** 2
    return -b, R2, int(m.sum())


if __name__ == '__main__':
    D = pickle.load(open(ROOT / 'data/norgate/full_history.pkl', 'rb'))
    zn = front(D['contracts']['ZN'])
    y = B.curve()
    y10 = y['SVENY10'].reindex(zn.index).ffill()
    dref = pd.Series([S.dur(v) for v in y10.values], index=zn.index)
    notional = pd.Series(np.where(zn.index < SWITCH, 8.0, 6.0), index=zn.index)
    above = (y10 * 100) > notional

    print(f'ZN, передний контракт: {zn.index[0]:%d.%m.%Y}-{zn.index[-1]:%d.%m.%Y}, '
          f'{len(zn)} дней, смен контракта {(zn["code"] != zn["code"].shift()).sum()-1}')
    print(f'доля времени с доходностью ВЫШЕ условного купона: {above.mean()*100:.0f}%\n')

    print(f"{'период':<14}{'ставка 10л':>12}{'режим':>10}{'дюрация':>10}{'R2':>7}"
          f"{'D_ref':>8}{'k':>8}{'набл.':>8}")
    rows = []
    for a in range(1982, 2027, 3):
        lo, hi = pd.Timestamp(f'{a}-01-01'), pd.Timestamp(f'{min(a+3,2027)}-01-01')
        m = (zn.index >= lo) & (zn.index < hi)
        if m.sum() < 100: continue
        d, R2, n = implied_duration(zn, y, lo, hi)
        if np.isnan(d): continue
        yy = y10[m].mean() * 100
        reg = 'выше' if above[m].mean() > 0.5 else 'ниже'
        dr = dref[m].mean()
        rows.append((f'{a}-{min(a+2,2026)}', yy, reg, d, R2, dr, d/dr, n))
        print(f"{rows[-1][0]:<14}{yy:>11.2f}%{reg:>10}{d:>10.2f}{R2:>7.2f}{dr:>8.2f}{d/dr:>8.3f}{n:>8}")

    print('\nсводка по режимам:')
    for lbl, msk in [('доходность ВЫШЕ условного купона', above),
                     ('доходность НИЖЕ условного купона', ~above)]:
        idx = zn.index[msk]
        if len(idx) < 200: continue
        sub = zn.loc[idx]
        same = sub['code'] == sub['code'].shift()
        r = np.log(sub['Close']).diff()[same]
        dy = y['SVENY07'].reindex(r.index).ffill().diff()
        m = r.notna() & dy.notna() & (dy != 0)
        b, _ = np.polyfit(dy[m], r[m], 1)
        k = -b / dref.reindex(r.index)[m].mean()
        print(f'   {lbl:<36} дюрация {-b:5.2f}, D_ref {dref.reindex(r.index)[m].mean():5.2f}, '
              f'k = {k:.3f}, наблюдений {int(m.sum())}')
    print(f'\n   в спецификации k = 0,880')
