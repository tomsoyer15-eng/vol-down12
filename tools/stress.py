#!/usr/bin/env python3
"""Синтетические сценарии падений: как ядро ведёт себя на формах, которых в истории не было.

История дала несколько форм обвала — вертикальный 1987, затяжной 1929–33, медленный
2000–02. Их мало, чтобы судить о механизме. Здесь строится сетка искусственных падений с
управляемой формой и на каждом прогоняется ядро.

Реалистичность удерживается привязкой к истории: глубина 20–60% (исторический диапазон
−34% в 1987, −49% в 2000–02, −56% в 2007–09, −86% в 1929–32), дневной ход ограничен 12%
(исторический максимум по модулю −20,4% в 1987), амплитуда медвежьих отскоков 8–25%
(в 1929–33 их было четыре в диапазоне 20–50%, в 2000–02 три по 15–20%).

Каждый сценарий: 3 года спокойного рынка (чтобы сигнал был включён и средняя устоялась),
затем падение заданной формы, затем 2 года восстановления. Ядро сравнивается с покупкой
и удержанием на ТОМ ЖЕ пути — это отделяет свойство стратегии от тяжести сценария.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'r32build'))
import sim_v13 as S
import rebal as R

DAY = 1 / 252
CAP_MOVE = 0.12                      # предел дневного хода


def path_calm(n, mu=0.08, sig=0.14, rng=None):
    return rng.normal(mu * DAY, sig * np.sqrt(DAY), n)


def path_crash(n, depth, shape, rallies, sig, rng):
    """Траектория падения заданной формы. depth < 0, доля."""
    t = np.linspace(0, 1, n)
    if shape == 'front':      w = 1 - (1 - t) ** 0.35        # обвал сразу, потом сползание
    elif shape == 'back':     w = t ** 2.5                    # ускорение к концу
    elif shape == 'linear':   w = t
    else:                     w = t                            # пила строится ниже
    base = np.diff(np.r_[0.0, np.log1p(depth) * w])
    if shape == 'saw' and rallies:
        seg = n // (rallies + 1)
        for k in range(rallies):
            a = (k + 1) * seg - seg // 3
            b = min(a + seg // 3, n - 1)
            if b > a:
                amp = rng.uniform(0.08, 0.25)
                base[a:b] += np.log1p(amp) / (b - a)
                after = min(b + seg // 3, n)
                if after > b:
                    base[b:after] -= np.log1p(amp) / (after - b)
    noise = rng.normal(0, sig * np.sqrt(DAY), n)
    return np.clip(base + noise, np.log1p(-CAP_MOVE), np.log1p(CAP_MOVE))


def scenario(depth, months, shape, bond, rallies, seed):
    rng = np.random.default_rng(seed)
    n_pre, n_cr, n_post = 756, max(int(months * 21), 21), 504
    eq = np.r_[path_calm(n_pre, rng=rng),
               path_crash(n_cr, depth, shape, rallies, 0.28, rng),
               path_calm(n_post, mu=0.10, sig=0.18, rng=rng)]
    eq = np.expm1(eq)
    n = len(eq)
    # нога Б: спокойный доход, во время падения ведёт себя по параметру
    rb = rng.normal(0.04 * DAY, 0.06 * np.sqrt(DAY), n)
    sl = slice(n_pre, n_pre + n_cr)
    drift = {'rally': 0.12, 'flat': 0.0, 'fall': -0.10}[bond]
    rb[sl] += np.log1p(1 + drift) / n_cr if drift > -1 else 0
    rb[sl] += (np.log1p(1 + drift) - 1) * 0
    rb[sl] = rng.normal(drift / n_cr, 0.07 * np.sqrt(DAY), n_cr)
    days = pd.bdate_range('2000-01-03', periods=n)
    return days, eq, rb


def run_one(depth, months, shape, bond, rallies, seed, band=0.03):
    days, re, rb = scenario(depth, months, shape, bond, rallies, seed)
    rf = np.full(len(days), 0.03 * DAY)
    eq_lvl = (1 + pd.Series(re, index=days)).cumprod()
    synth = (600.0 * eq_lvl / eq_lvl.iloc[-1]).values
    dref = np.full(len(days), 8.0)
    se = S.sigs(eq_lvl, days).astype(float)
    sb = S.sigs((1 + pd.Series(rb, index=days)).cumprod(), days).astype(float)
    d = (days, re, rb, rf, synth, dref)
    r, st = R.sim(d, S.strict_states(se > 0, days).astype(float),
                  S.strict_states(sb > 0, days).astype(float), band=band)
    e = (1 + r).cumprod(); dd = (e / e.cummax() - 1).min() * 100
    bh = (1 + pd.Series(re, index=days)).cumprod()
    ddb = (bh / bh.cummax() - 1).min() * 100
    return dd, ddb, (e.iloc[-1] - 1) * 100, (bh.iloc[-1] - 1) * 100


if __name__ == '__main__':
    DEPTH = [-0.20, -0.30, -0.40, -0.50, -0.60]
    MONTHS = [1, 3, 6, 12, 24, 36]
    SHAPE = ['front', 'linear', 'back', 'saw']
    BOND = ['rally', 'flat', 'fall']
    rows = []
    sid = 0
    for dep in DEPTH:
        for mo in MONTHS:
            for sh in SHAPE:
                for bo in BOND:
                    sid += 1
                    dd, ddb, ret, retb = run_one(dep, mo, sh, bo, 3 if sh == 'saw' else 0, 1000 + sid)
                    rows.append(dict(depth=dep, months=mo, shape=sh, bond=bo,
                                     dd=dd, dd_bh=ddb, ret=ret, ret_bh=retb))
    df = pd.DataFrame(rows)
    df.to_csv(ROOT / 'data/stress_scenarios.csv', index=False)
    print(f'сценариев: {len(df)}\n')
    print('просадка ядра против покупки и удержания, по форме падения:')
    print(f"{'форма':<10}{'ядро':>10}{'держать':>10}{'разница':>10}{'худший случай ядра':>22}")
    NAME = {'front': 'обвал сразу', 'linear': 'ровное', 'back': 'ускорение', 'saw': 'пила'}
    for sh in SHAPE:
        g = df[df.shape_ if False else df['shape'] == sh]
        print(f"{NAME[sh]:<10}{g.dd.mean():>9.1f}%{g.dd_bh.mean():>9.1f}%"
              f"{g.dd.mean()-g.dd_bh.mean():>+9.1f}{g.dd.min():>21.1f}%")
    print('\nпо длительности падения:')
    for mo in MONTHS:
        g = df[df.months == mo]
        print(f"   {mo:>2} мес: ядро {g.dd.mean():>6.1f}%, держать {g.dd_bh.mean():>6.1f}%, "
              f"разница {g.dd.mean()-g.dd_bh.mean():>+5.1f}")
    print('\nпо поведению ноги Б в кризис:')
    for bo in BOND:
        g = df[df.bond == bo]
        print(f"   {bo:<6}: ядро {g.dd.mean():>6.1f}%, держать {g.dd_bh.mean():>6.1f}%")
