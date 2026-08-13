#!/usr/bin/env python3
"""Ядро на фактических биржевых ценах против модельных рядов.

Ядро моделирует ноги синтетикой: нога А — индекс полной доходности США со сплайсом на SPY,
нога Б — реконструкция десятилетней бумаги со сплайсом на IEF. Контракты входят только
через «контрактный эквивалент» — доллары на контракт. Здесь эта конструкция проверяется
фактическими фьючерсами: ZN с 03.05.1982, ES с 09.09.1997.

Метод. У обратно-скорректированного непрерывного ряда ход цены есть P&L непрерывно
роллируемой позиции, то есть уже избыточный доход над денежной ставкой. Значит:

    фактический доход ноги на единицу МОДЕЛЬНОЙ экспозиции = множитель x d(adj) / q,

где q — контрактный эквивалент ядра (для ноги Б q_b = 112000 x 0,88 x D_fix/D_ref,
для ноги А unit_e = 500 x цена SPY). Модель для той же величины даёт (r - ставка - спред).
Совпадение означает, что расчётный аппарат §6 воспроизводит биржу.
"""
import pickle, sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'r32build'))
import sim_v13 as S

ZN_MULT, ES_MULT_REAL = 1000.0, 50.0


def met(x):
    eq = (1 + x).cumprod(); yrs = (x.index[-1] - x.index[0]).days / 365.25
    return ((eq.iloc[-1] ** (1 / yrs) - 1) * 100, x.std() * np.sqrt(252) * 100,
            (eq / eq.cummax() - 1).min() * 100)


if __name__ == '__main__':
    D = pickle.load(open(ROOT / 'data/norgate/full_history.pkl', 'rb'))
    d = S.build_splice()
    days, re, rb, rfv, spy_close, dref = d[0], d[1], d[2], d[3], d[4], d[5]
    rf = pd.Series(rfv, index=days)
    model_b = pd.Series(rb, index=days) - rf          # модельный избыточный доход ноги Б
    model_a = pd.Series(re, index=days) - rf
    dref_s = pd.Series(dref, index=days)

    print('=== НОГА Б: ZN против модельной реконструкции ===')
    adj = D['continuous']['ZN']['adj']['Close']
    raw = D['continuous']['ZN']['raw']['Close']
    j = adj.index.intersection(days)
    q_b = S.ZN_MODEL_PX_EQ * S.CTD_RATIO                     # долларов на контракт, D_fix≈D_ref
    real_b = (ZN_MULT * adj.diff() / q_b).reindex(j).dropna()
    mb = model_b.reindex(real_b.index)
    ok = real_b.notna() & mb.notna()
    real_b, mb = real_b[ok], mb[ok]
    print(f'окно {real_b.index[0]:%d.%m.%Y}-{real_b.index[-1]:%d.%m.%Y}, {len(real_b)} дней')
    print(f"{'ряд':<28}{'годовая качка':>15}{'накопл. избыт.':>17}")
    for nm, x in [('модель (реконструкция)', mb), ('факт (ZN)', real_b)]:
        print(f'{nm:<28}{x.std()*np.sqrt(252)*100:>14.2f}%{((1+x).prod()-1)*100:>16.1f}%')
    print(f'корреляция дневных ходов: {real_b.corr(mb):.4f}')
    print(f'отношение качки факт/модель: {real_b.std()/mb.std():.3f}')
    b, a = np.polyfit(mb, real_b, 1)
    print(f'регрессия факт на модель: наклон {b:.3f}, R2 {np.corrcoef(mb, real_b)[0,1]**2:.3f}')
    for lo, hi, lbl in [('1982-05-03', '1990-01-01', '1982-1989'), ('1990-01-01', '2000-01-01', '1990-1999'),
                        ('2000-01-01', '2010-01-01', '2000-2009'), ('2010-01-01', '2020-01-01', '2010-2019'),
                        ('2020-01-01', '2027-01-01', '2020-2026')]:
        m = (real_b.index >= lo) & (real_b.index < hi)
        if m.sum() < 200: continue
        bb, _ = np.polyfit(mb[m], real_b[m], 1)
        print(f'   {lbl}: наклон {bb:.3f}, качка факт/модель {real_b[m].std()/mb[m].std():.3f}')

    print('\n=== НОГА А: ES против модельной реконструкции ===')
    adje = D['continuous']['ES']['adj']['Close']
    rawe = D['continuous']['ES']['raw']['Close']
    je = adje.index.intersection(days)
    unit_e = pd.Series(S.ES_MULT * spy_close, index=days).reindex(je)
    real_a = (ES_MULT_REAL * adje.diff().reindex(je) / unit_e).dropna()
    ma = model_a.reindex(real_a.index)
    ok = real_a.notna() & ma.notna()
    real_a, ma = real_a[ok], ma[ok]
    print(f'окно {real_a.index[0]:%d.%m.%Y}-{real_a.index[-1]:%d.%m.%Y}, {len(real_a)} дней')
    print(f"{'ряд':<28}{'годовая качка':>15}{'накопл. избыт.':>17}")
    for nm, x in [('модель (индекс - ставка)', ma), ('факт (ES)', real_a)]:
        print(f'{nm:<28}{x.std()*np.sqrt(252)*100:>14.2f}%{((1+x).prod()-1)*100:>16.1f}%')
    print(f'корреляция дневных ходов: {real_a.corr(ma):.4f}')
    print(f'отношение качки факт/модель: {real_a.std()/ma.std():.3f}')
    b2, _ = np.polyfit(ma, real_a, 1)
    print(f'регрессия факт на модель: наклон {b2:.3f}, R2 {np.corrcoef(ma, real_a)[0,1]**2:.3f}')
    pickle.dump({'real_b': real_b, 'model_b': mb, 'real_a': real_a, 'model_a': ma},
                open(ROOT / 'data/norgate/legs_real_vs_model.pkl', 'wb'))
