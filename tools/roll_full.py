#!/usr/bin/env python3
"""Роллы и ликвидность на полной истории: ZN с 1982, ES с 1997.

Три вопроса:
  1. Календарь роллов §1 против фактического переезда открытого интереса — на всех роллах,
     а не на семи.
  2. Сколько дневного объёма остаётся в контракте, которым ядро торгует в день ролла.
  3. Исполнимость книги §3 (26 ES + 101 ZN на 10 млн) против фактического объёма и
     открытого интереса по годам. В день ролла оборот удваивается: выйти и войти.
"""
import pickle, sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'r32build'))
import sim_v13 as S
import ctd_regimes as C

BOOK = {'ZN': 101, 'ES': 26}          # книга §3 на 10 млн


def crossover(near, far, field='Open Interest'):
    common = near.index.intersection(far.index)
    if len(common) < 10: return None
    d = far.loc[common, field] > near.loc[common, field]
    d = d & d.shift(-1).fillna(False)
    i = d[d].index
    return i[0] if len(i) else None


if __name__ == '__main__':
    D = pickle.load(open(ROOT / 'data/norgate/full_history.pkl', 'rb'))
    for sym in ('ZN', 'ES'):
        cs = D['contracts'][sym]
        codes = sorted(cs, key=C.expiry_key)
        idx = D['continuous'][sym]['adj'].index
        spec = sorted(S.roll_days_calendar(idx))
        gaps, shares = [], []
        for i in range(len(codes) - 1):
            a, b = cs[codes[i]], cs[codes[i + 1]]
            if 'Open Interest' not in a or a['Open Interest'].max() < 1000: continue
            co = crossover(a, b)
            if co is None: continue
            cand = [d for d in spec if abs((d - co).days) <= 45]
            if not cand: continue
            sp = min(cand, key=lambda d: abs((d - co).days))
            g = len(idx[(idx > min(co, sp)) & (idx <= max(co, sp))]) * (1 if sp > co else -1)
            gaps.append((co, sp, g))
        for d in spec:
            live = {c: cs[c] for c in codes if d in cs[c].index and 'Volume' in cs[c]}
            if len(live) < 2: continue
            near = min(live, key=C.expiry_key)
            tot = sum(live[c].loc[d, 'Volume'] for c in live)
            if tot > 0: shares.append((d, live[near].loc[d, 'Volume'] / tot))
        g = np.array([x[2] for x in gaps])
        print(f'\n=== {sym}: роллы, {len(gaps)} наблюдений '
              f'({gaps[0][0]:%Y}-{gaps[-1][0]:%Y}) ===')
        print(f'  опоздание §1 против переезда ОИ: медиана {np.median(g):+.0f} сессий, '
              f'среднее {g.mean():+.1f}, диапазон {g.min():+.0f}..{g.max():+.0f}')
        print(f'  доля роллов, где §1 позже рынка: {(g > 0).mean()*100:.0f}%')
        sh = np.array([x[1] for x in shares])
        print(f'  объём в торгуемом контракте в день ролла: медиана {np.median(sh)*100:.1f}%, '
              f'10-й проц. {np.percentile(sh,10)*100:.1f}%, наблюдений {len(sh)}')

    print('\n=== Исполнимость книги §3 (26 ES + 101 ZN на 10 млн) ===')
    print(f"{'год':<7}{'ZN объём':>12}{'101 к объёму':>14}{'ZN ОИ':>12}{'101 к ОИ':>11}"
          f"{'ES объём':>12}{'26 к объёму':>13}")
    for yr in range(1982, 2027, 3):
        row = [str(yr)]
        for sym, n in (('ZN', 101), ('ES', 26)):
            f = C.front(D['contracts'][sym])
            m = (f.index.year >= yr) & (f.index.year < yr + 3)
            if m.sum() < 50 or 'oi' not in f:
                row += ['—', '—'] if sym == 'ES' else ['—', '—', '—', '—']
                continue
            vol = pd.concat([v['Volume'] for v in D['contracts'][sym].values()])
            vol = vol.groupby(level=0).sum()
            vm = vol[(vol.index.year >= yr) & (vol.index.year < yr + 3)].median()
            oi = f['oi'][m].median()
            if sym == 'ZN':
                row += [f'{vm:,.0f}', f'{n/vm*100:.3f}%', f'{oi:,.0f}', f'{n/oi*100:.4f}%']
            else:
                row += [f'{vm:,.0f}', f'{n/vm*100:.3f}%']
        if len(row) == 7:
            print(f"{row[0]:<7}{row[1]:>12}{row[2]:>14}{row[3]:>12}{row[4]:>11}"
                  f"{row[5]:>12}{row[6]:>13}")
