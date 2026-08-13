#!/usr/bin/env python3
"""Сверка допущений спецификации с реальными фьючерсными ценами Norgate (2 года триала).

Проверяются три числа, которые в ядре стоят как допущения и живыми данными не сверялись:
  CTD_RATIO = 0,88   — переводной коэффициент дюрации ноги Б (§6)
  ROLL_BP   = 1 б.п. — издержка ролла от номинала (§2)
  COST      = 5 б.п. — издержка на изменение экспозиции (§2)
  спред 0,5 п.п./год — постоянный вычет из дохода каждой ноги (§2)

Данных по объёму и открытому интересу в выгрузке нет, поэтому передний контракт
определяется по ближайшему сроку, а не по перетеканию ликвидности. Спред оценивается
по максимумам и минимумам дня методом Корвина-Шульца (2012) — он для того и создан,
чтобы получать эффективный спред без книги заявок.
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


def expiry_key(code):
    return int(code[:4]) * 100 + MONTH[code[4]]


def front(contracts, gap=3):
    """Передний контракт: наименьший срок из доступных на эту дату."""
    codes = sorted(contracts, key=expiry_key)
    rows = []
    for rank, c in enumerate(codes):
        d = contracts[c]
        stop = d.index[max(len(d) - 1 - gap, 0)]
        t = d[d.index <= stop].copy()
        t['code'] = c
        t['rank'] = rank
        rows.append(t[['code', 'rank', 'Close', 'High', 'Low']])
    all_ = pd.concat(rows)
    all_ = all_.sort_values('rank').groupby(level=0).first().sort_index()
    return all_


def corwin_schultz(H, L):
    """Эффективный спред по дневным максимумам и минимумам, доля цены."""
    h2 = np.log(H / L) ** 2
    beta = h2 + h2.shift(-1)
    H2 = np.maximum(H, H.shift(-1)); L2 = np.minimum(L, L.shift(-1))
    gamma = np.log(H2 / L2) ** 2
    k = 3 - 2 * np.sqrt(2)
    alpha = (np.sqrt(2 * beta) - np.sqrt(beta)) / k - np.sqrt(gamma / k)
    s = 2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))
    s = s.clip(lower=0).dropna()
    live = (H > L) & (H.shift(-1) > L.shift(-1))
    return s[live.reindex(s.index).fillna(False)]


if __name__ == '__main__':
    data = pickle.load(open(ROOT / 'data/norgate/contracts.pkl', 'rb'))
    F = {s: front(data[s]) for s in data}
    for s in F:
        print(f'{s}: передний ряд {len(F[s])} дней, {F[s].index[0]:%d.%m.%Y}-{F[s].index[-1]:%d.%m.%Y}, '
              f'смен контракта {(F[s]["code"] != F[s]["code"].shift()).sum() - 1}')

    # ── 1. Дюрация ноги Б: проверка CTD_RATIO 0,88 ────────────────────────────
    y = B.curve()
    zn = F['ZN']
    same = zn['code'] == zn['code'].shift()
    r = np.log(zn['Close']).diff()[same]                    # без дней смены контракта
    print('\n=== 1. Дюрация ZN: коэффициент 0,88 из §6 ===')
    print(f"{'по какой ставке':<16}{'подраз. дюрация':>18}{'R2':>8}{'D_ref ядра':>13}{'k = D/D_ref':>13}")
    for ten, col in [(5, 'SVENY05'), (7, 'SVENY07'), (10, 'SVENY10')]:
        dy = y[col].reindex(r.index).ffill().diff().reindex(r.index)
        m = r.notna() & dy.notna()
        b, a = np.polyfit(dy[m], r[m], 1)
        R2 = np.corrcoef(dy[m], r[m])[0, 1] ** 2
        dref = pd.Series([S.dur(v) for v in y['SVENY10'].reindex(r.index).ffill().values],
                         index=r.index).mean()
        print(f"{str(ten)+'-летняя':<16}{-b:>18.2f}{R2:>8.3f}{dref:>13.2f}{-b/dref:>13.3f}")
    print(f"   в спецификации CTD_RATIO = 0,880")

    # ── 2. Спред и издержки ───────────────────────────────────────────────────
    print('\n=== 2. Эффективный спред по методу Корвина-Шульца ===')
    print(f"{'инструмент':<12}{'медиана':>12}{'среднее':>12}{'90-й проц.':>13}{'в б.п.':>10}")
    for s in ('ES', 'ZN', 'GC'):
        d = F[s]
        cs = corwin_schultz(d['High'], d['Low'])
        print(f"{s:<12}{cs.median()*100:>11.4f}%{cs.mean()*100:>11.4f}%"
              f"{cs.quantile(0.9)*100:>12.4f}%{cs.median()*1e4:>10.2f}")
    print('   в спецификации COST = 5 б.п. на изменение экспозиции, ROLL_BP = 1 б.п. на ролл')

    # ── 3. Стоимость ролла по фактическим ценам соседних контрактов ───────────
    print('\n=== 3. Календарный спред соседних контрактов (в день смены) ===')
    print(f"{'инстр.':<8}{'роллов':>8}{'медиана |спред|':>18}{'в б.п. от цены':>17}")
    for s in ('ES', 'ZN', 'GC'):
        cs = data[s]; codes = sorted(cs, key=expiry_key)
        gaps = []
        for i in range(len(codes) - 1):
            a, b = cs[codes[i]], cs[codes[i + 1]]
            common = a.index.intersection(b.index)
            if len(common) < 5: continue
            t = common[-4]                                   # за 3 сессии до конца ближнего
            gaps.append(abs(b.loc[t, 'Close'] / a.loc[t, 'Close'] - 1))
        g = np.array(gaps)
        if len(g):
            print(f"{s:<8}{len(g):>8}{np.median(g)*100:>17.4f}%{np.median(g)*1e4:>17.1f}")
    print('   это не издержка, а карри срочной структуры — сравнение со ставкой ниже')

    # ── 4. Подразумеваемая ставка фондирования из спреда ES ───────────────────
    print('\n=== 4. Фондирование, зашитое в срочную структуру ES ===')
    cs = data['ES']; codes = sorted(cs, key=expiry_key)
    rows = []
    for i in range(len(codes) - 1):
        a, b = cs[codes[i]], cs[codes[i + 1]]
        common = a.index.intersection(b.index)
        if len(common) < 30: continue
        dt = (expiry_key(codes[i + 1]) - expiry_key(codes[i]))
        dt = ((dt // 100) * 12 + dt % 100) / 12.0
        imp = np.log(b.loc[common, 'Close'] / a.loc[common, 'Close']) / dt
        rows.append(imp)
    imp = pd.concat(rows).groupby(level=0).median() * 100
    rf = pd.Series(S.rf_series(imp.index).values, index=imp.index)
    rf_ann = ((1 + rf) ** 252 - 1) * 100
    print(f'   подразумеваемая (ставка - дивиденды) из спреда ES: {imp.median():.2f}% годовых')
    print(f'   векселя за тот же период:                          {rf_ann.median():.2f}%')
    print(f'   => подразумеваемая дивидендная доходность S&P:     {rf_ann.median()-imp.median():.2f}%')
    print('   (фактическая у S&P 500 около 1,2% — если сходится, спреда в срочной структуре нет)')
