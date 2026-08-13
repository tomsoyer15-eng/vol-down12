#!/usr/bin/env python3
"""Взвешивание синтетических сценариев по исторической частоте форм падения.

Равномерная сетка параметров даёт долю точек сетки, а не вероятность. Здесь из 141 года
дневной истории извлекаются все эпизоды просадки индекса глубже 15%, для каждого измеряются
те же четыре признака, что задают сценарий (глубина, длительность, форма, поведение
облигаций), и по ним строятся ЧАСТОТЫ. Сценарий получает вес как произведение частот.

ДОПУЩЕНИЕ НЕЗАВИСИМОСТИ: веса перемножаются по признакам, потому что эпизодов слишком
мало для оценки совместного распределения (их порядка двух десятков на четыре признака).
Реальная связь между признаками (например, длинные падения чаще пилообразны) при этом
теряется. Это ослабляет, но не отменяет вывод: веса нужны, чтобы отличить редкое от
частого, а не чтобы получить точную вероятность.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'r32build'))
import sim_v13 as S

DEPTH = [-0.20, -0.30, -0.40, -0.50, -0.60]
MONTHS = [1, 3, 6, 12, 24, 36]
SHAPE = ['front', 'linear', 'back', 'saw']
BOND = ['rally', 'flat', 'fall']


def nearest(v, grid):
    return min(grid, key=lambda g: abs(g - v))


def episodes():
    D = S.D
    mk = pd.read_csv(D / 'us-market-daily-1885-2025.csv', parse_dates=['Date']).set_index('Date').sort_index()
    spy = pd.read_csv(D / 'spy_daily.csv', parse_dates=['date'], index_col='date')
    eq = pd.concat([mk['Adjusted Close'].pct_change(),
                    spy['adj'].pct_change()[spy.index > mk.index[-1]]]).dropna()
    lvl = (1 + eq).cumprod()
    dd = lvl / lvl.cummax() - 1
    # облигации: месячная длинная ставка (до 1962 Шиллер, далее DGS10)
    sh = pd.read_csv(D / 'shiller-monthly-1871-2026.csv', parse_dates=['Date'], index_col='Date')
    lr = sh['Long Interest Rate']; lr = lr[lr > 0] / 100
    y10 = pd.read_csv(D / 'dgs10.csv', parse_dates=['observation_date']).set_index('observation_date')['DGS10'].dropna() / 100
    ym = pd.concat([lr[lr.index < y10.index[0]], y10.resample('ME').last()]).sort_index().resample('ME').last().ffill()
    dur = pd.Series([S.dur(v) for v in ym.values], index=ym.index)
    rbm = (ym.shift(1) / 12 - dur.shift(1) * ym.diff()).dropna()
    bidx = (1 + rbm).cumprod()

    out = []; inep = False
    for t, v in dd.items():
        if not inep and v < -1e-9:
            inep = True; a = t; lo = v; b = t
        elif inep:
            if v < lo: lo = v; b = t
            if v >= -1e-9:
                if lo < -0.15: out.append((a, b, lo))
                inep = False
    if inep and lo < -0.15: out.append((a, b, lo))

    rows = []
    for a, b, lo in out:
        seg = lvl[(lvl.index >= a) & (lvl.index <= b)]
        mo = max((b - a).days / 30.44, 1.0)
        # доля падения, пройденная в первой трети эпизода
        k = max(len(seg) // 3, 1)
        part = (seg.iloc[k] / seg.iloc[0] - 1) / lo if lo else 0
        # число отскоков >=8% внутри эпизода
        s = seg / seg.cummax() - 1
        rall = 0; run_lo = 0
        for x in (seg / seg.iloc[0]).values:
            run_lo = min(run_lo if run_lo else x, x)
            if run_lo and x / run_lo - 1 >= 0.08:
                rall += 1; run_lo = x
        if rall >= 2:      shape = 'saw'
        elif part > 0.55:  shape = 'front'
        elif part < 0.25:  shape = 'back'
        else:              shape = 'linear'
        bs = bidx[(bidx.index >= a) & (bidx.index <= b)]
        chg = (bs.iloc[-1] / bs.iloc[0] - 1) if len(bs) > 1 else 0.0
        bond = 'rally' if chg > 0.03 else ('fall' if chg < -0.03 else 'flat')
        rows.append(dict(start=a, end=b, depth=lo, months=mo, shape=shape, bond=bond,
                         d_cell=nearest(lo, DEPTH), m_cell=nearest(mo, MONTHS)))
    return pd.DataFrame(rows)


if __name__ == '__main__':
    ep = episodes()
    print(f'эпизодов просадки индекса глубже 15% за 1886–2026: {len(ep)}\n')
    print(ep[['start', 'end', 'depth', 'months', 'shape', 'bond']]
          .assign(start=lambda d: d.start.dt.strftime('%m.%Y'),
                  end=lambda d: d.end.dt.strftime('%m.%Y'),
                  depth=lambda d: (d.depth * 100).round(1),
                  months=lambda d: d.months.round(0)).to_string(index=False))
    w = {}
    for col, grid in (('d_cell', DEPTH), ('m_cell', MONTHS), ('shape', SHAPE), ('bond', BOND)):
        c = ep[col].value_counts()
        w[col] = {g: (c.get(g, 0) + 0.5) / (len(ep) + 0.5 * len(grid)) for g in grid}
    print('\nчастоты (со сглаживанием, чтобы невиданное имело малый, но не нулевой вес):')
    for col, nm in (('d_cell', 'глубина'), ('m_cell', 'длительность'), ('shape', 'форма'), ('bond', 'нога Б')):
        print(f'   {nm:<13}' + ', '.join(f'{k}: {v:.3f}' for k, v in w[col].items()))

    df = pd.read_csv(ROOT / 'data/stress_scenarios.csv')
    # ВЕС ПО РЕАЛИЗОВАННОЙ ГЛУБИНЕ, а не по параметру сетки. Параметр depth задаёт форму
    # траектории, но поверх него ложатся шум и отскоки, поэтому фактическая просадка глубже
    # заявленной — на мелких ячейках до 19 п.п. Взвешивание по метке относило сценарий не к
    # той ячейке частоты; берётся dd_bh, то есть просадка того же пути при покупке и удержании.
    df['depth_real'] = [nearest(r.dd_bh / 100.0, DEPTH) for _, r in df.iterrows()]
    df['w'] = [w['d_cell'][r.depth_real] * w['m_cell'][r.months] * w['shape'][r['shape']] * w['bond'][r.bond]
               for _, r in df.iterrows()]
    df['w'] /= df['w'].sum()
    df.to_csv(ROOT / 'data/stress_scenarios.csv', index=False)
    print('\nдоля сценариев против взвешенной вероятности:')
    for thr in (-35, -45, -55):
        raw = (df.dd < thr).mean() * 100
        wtd = df.loc[df.dd < thr, 'w'].sum() * 100
        print(f'   просадка хуже {thr}%: по сетке {raw:>5.1f}%, взвешенно {wtd:>5.1f}%')
    print(f"\nвзвешенная медиана просадки: "
          f"{df.sort_values('dd').assign(c=lambda d: d.w.cumsum()).query('c>=0.5').dd.iloc[0]:.1f}%")
