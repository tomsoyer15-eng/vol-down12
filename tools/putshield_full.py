#!/usr/bin/env python3
"""Защитный пут: проверка на трёх окнах при выравненном риске.

Пут снижает и просадку, и качку. Сравнивать его с ядром напрямую нельзя: за меньшую качку
всегда платят доходностью. Поэтому вариант с путом усиливается множителем позиции до той же
качки, что у ядра, и лишь тогда сравнивается. Так же проверялись стоп и все прочие кандидаты.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'r32build'))
import sim_v13 as S
import smooth as M
import putshield as P
import build_1885 as B

STRIKES = [None, 1.00, 0.90, 0.85]
LBL = {None: 'без пута (ядро)', 1.00: 'пут на деньгах', 0.90: 'пут −10%', 0.85: 'пут −15%'}


def match(d, we, wb, strike, target_v, cap=2.0):
    """Подобрать множитель позиции так, чтобы качка совпала с ядром."""
    lo, hi = 1.0, 2.5
    for _ in range(14):
        k = (lo + hi) / 2
        r, _ = P.sim(d, we * k, wb * k, strike=strike, cap=cap * k)
        v = M.met(r)[1]
        if v < target_v: lo = k
        else: hi = k
    r, paid = P.sim(d, we * k, wb * k, strike=strike, cap=cap * k)
    return r, paid, k


def window(name, d, cap=2.0):
    days = d[0]
    if isinstance(d[6], np.ndarray):           # полное окно: сигналы уже дневные
        we = S.strict_states(d[6] > 0, days).astype(float)
        wb = S.strict_states(d[7] > 0, days).astype(float)
    else:
        we = M.weights(d[6], days, 'бинарное (ядро)')
        wb = M.weights(d[7], days, 'бинарное (ядро)')
    base, _ = P.sim(d, we, wb, cap=cap)
    tv = M.met(base)[1]
    print(f"\n=== {name}: {days[0]:%Y}–{days[-1]:%Y} ===")
    print(f"{'вариант':<18}{'CAGR':>8}{'качка':>8}{'MaxDD':>9}{'D/V':>7}"
          f"{'плечо':>8}{'премия/год':>12}")
    out = {}
    for st in STRIKES:
        r, paid, k = match(d, we, wb, st, tv, cap) if st is not None else (base, 0.0, 1.0)
        c, v, dd, dv = M.met(r)
        yrs = (days[-1] - days[0]).days / 365.25
        print(f"{LBL[st]:<18}{c:>7.2f}%{v:>7.2f}%{dd:>8.1f}%{dv:>7.2f}"
              f"{k:>8.2f}{paid/yrs*100:>11.2f}%")
        out[st] = k
    return out


if __name__ == '__main__':
    KMAP = window('дневное окно ядра', M.build())
    window('полное окно', B.build())

    # --- синтетика ---
    import stress as X
    df = pd.read_csv(ROOT / 'data/stress_scenarios.csv')
    print(f"\n=== синтетические сценарии (n={len(df)}, взвешены по частоте) ===")
    print(f"{'вариант':<18}{'ср.просадка':>13}{'худшая':>9}{'взв.медиана':>13}{'ср.итог':>10}")
    for st in STRIKES:
        dds, rets, ws = [], [], []
        for idx, row in df.iterrows():
            days, re, rb = X.scenario(row.depth, row.months, row['shape'], row.bond,
                                      3 if row['shape'] == 'saw' else 0, 1000 + idx + 1)
            rf = np.full(len(days), 0.03 / 252)
            lvl = (1 + pd.Series(re, index=days)).cumprod()
            d = (days, re, rb, rf, (600.0 * lvl / lvl.iloc[-1]).values, np.full(len(days), 8.0),
                 None, None)
            we = S.strict_states(S.sigs(lvl, days) > 0, days).astype(float)
            wb = S.strict_states(S.sigs((1 + pd.Series(rb, index=days)).cumprod(), days) > 0,
                                 days).astype(float)
            k = 1.0 if st is None else KMAP[st]
            r, _ = P.sim(d, we * k, wb * k, strike=st, cap=2.0 * k)
            e = (1 + r).cumprod()
            dds.append((e / e.cummax() - 1).min() * 100); rets.append((e.iloc[-1] - 1) * 100)
            ws.append(row.w)
        ws = np.array(ws); dds = np.array(dds); rets = np.array(rets)
        o = np.argsort(dds); med = dds[o][np.searchsorted(np.cumsum(ws[o]), 0.5)]
        print(f"{LBL[st]:<18}{(dds*ws).sum():>12.1f}%{dds.min():>8.1f}%"
              f"{med:>12.1f}%{(rets*ws).sum():>9.1f}%")
