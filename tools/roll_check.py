#!/usr/bin/env python3
"""Календарь роллов §1 против того, где реально стоит ликвидность.

Ядро роллит за 3 торговые сессии до последнего рабочего дня февраля/мая/августа/ноября
(это первый день поставки следующего контракта). Рынок переезжает в дальний контракт
раньше — по открытому интересу это видно прямо. Здесь измеряется разрыв между
правилом спецификации и фактическим переездом, и во что он обходится.
"""
import pickle, sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'r32build'))
import sim_v13 as S

MONTH = dict(zip('FGHJKMNQUVXZ', range(1, 13)))
expiry_key = lambda c: int(c[:4]) * 100 + MONTH[c[4]]


def crossover(near, far, field='Open Interest'):
    """Первый день, когда дальний контракт перевесил ближний по этому показателю."""
    common = near.index.intersection(far.index)
    if len(common) < 10:
        return None
    d = (far.loc[common, field] > near.loc[common, field])
    d = d & d.shift(-1).fillna(False)                 # два дня подряд, чтобы не ловить шум
    idx = d[d].index
    return idx[0] if len(idx) else None


def front_by_oi(contracts):
    """Передний контракт = наибольший открытый интерес на эту дату."""
    codes = sorted(contracts, key=expiry_key)
    rows = []
    for c in codes:
        t = contracts[c].copy(); t['code'] = c
        rows.append(t)
    all_ = pd.concat(rows)
    all_ = all_.sort_values('Open Interest', ascending=False).groupby(level=0).first()
    return all_.sort_index()


if __name__ == '__main__':
    D = pickle.load(open(ROOT / 'data/norgate/full.pkl', 'rb'))
    data, cont = D['contracts'], D['continuous']

    for sym in ('ZN', 'ES'):
        cs = data[sym]; codes = sorted(cs, key=expiry_key)
        idx = cont[sym]['adj'].index
        spec_rolls = sorted(S.roll_days_calendar(idx))
        print(f'\n=== {sym}: правило §1 против переезда ликвидности ===')
        print(f"{'пара':<18}{'переезд по ОИ':>16}{'переезд по объёму':>20}"
              f"{'ролл по §1':>14}{'разрыв, сессий':>16}")
        for i in range(len(codes) - 1):
            a, b = cs[codes[i]], cs[codes[i + 1]]
            co = crossover(a, b, 'Open Interest')
            cv = crossover(a, b, 'Volume')
            if co is None: continue
            later = [d for d in spec_rolls if d >= co - pd.Timedelta(days=40)]
            sp = later[0] if later else None
            if sp is None: continue
            gap = len(idx[(idx > co) & (idx <= sp)])
            print(f"{codes[i]+'→'+codes[i+1]:<18}{co:%d.%m.%Y:>16}"
                  f"{(cv.strftime('%d.%m.%Y') if cv is not None else '—'):>20}"
                  f"{sp:%d.%m.%Y:>14}{gap:>16}")

    # доля объёма, остающаяся в ближнем контракте на дату ролла по §1
    print('\n=== сколько ликвидности остаётся в контракте, которым ядро торгует в день ролла ===')
    for sym in ('ZN', 'ES', 'GC'):
        cs = data[sym]; codes = sorted(cs, key=expiry_key)
        idx = cont[sym]['adj'].index
        spec_rolls = sorted(S.roll_days_calendar(idx))
        sh = []
        for d in spec_rolls:
            live = {c: cs[c] for c in codes if d in cs[c].index}
            if len(live) < 2: continue
            near = min(live, key=expiry_key)
            tot = sum(live[c].loc[d, 'Volume'] for c in live)
            if tot > 0:
                sh.append(live[near].loc[d, 'Volume'] / tot)
        if sh:
            print(f'   {sym}: медиана {np.median(sh)*100:5.1f}% объёма дня, '
                  f'минимум {min(sh)*100:4.1f}%, роллов {len(sh)}')

    # сверка моего переднего ряда с их back-adjusted
    print('\n=== мой передний ряд (по открытому интересу) против &SYM_CCB ===')
    for sym in ('ZN', 'ES', 'GC'):
        f = front_by_oi(data[sym])
        mine = np.log(f['Close']).diff()[f['code'] == f['code'].shift()]
        theirs = np.log(cont[sym]['adj']['Close']).diff()
        j = mine.index.intersection(theirs.index)
        m = mine.reindex(j).dropna(); t = theirs.reindex(m.index)
        print(f'   {sym}: корреляция {m.corr(t):.4f}, '
              f'медиана |расхождения| {np.median(np.abs(m-t))*1e4:.2f} б.п., '
              f'дней {len(m)}')
