# -*- coding: utf-8 -*-
"""Аудит исходных данных: что есть, чего нет, правдоподобны ли номиналы.
Ничего не считает по стратегии — только инвентаризация."""
import os, glob, pickle
import pandas as pd, numpy as np

EXP = '/home/user/norgate'
REPO = '/home/user/vol-down12'
TAB = '/root/.claude/uploads/9105309a-064d-505a-ae0f-d18949dd988f/746c7056-roll_table_64_markets.csv'

def specs():
    fr = []
    for d in ('exp2','exp3','exp4'):
        fr.append(pd.read_csv(f'{EXP}/{d}/contract_specs.csv'))
    s = pd.concat(fr).drop_duplicates('symbol').set_index('symbol')
    return s

def seriya(sym, kind='unadj'):
    for d in ('exp2','exp3','exp4'):
        p = f'{EXP}/{d}/{sym}_{kind}.csv'
        if os.path.exists(p):
            df = pd.read_csv(p, parse_dates=['date']).set_index('date')
            return df
    return None

tab = pd.read_csv(TAB).dropna(subset=['symbol']).set_index('symbol')
sp = specs()

rows = []
for sym, r in tab.iterrows():
    u = seriya(sym, 'unadj')
    b = seriya(sym, 'backadj')
    if u is None:
        rows.append(dict(symbol=sym, est='НЕТ', n=0)); continue
    pv = sp.loc[sym,'point_value'] if sym in sp.index else np.nan
    cur = sp.loc[sym,'currency'] if sym in sp.index else '?'
    # число роллов по смене delivery_month
    dm = u['delivery_month'].dropna()
    smeny = (dm != dm.shift()).sum() - 1
    let = (u.index[-1]-u.index[0]).days/365.25
    rows.append(dict(symbol=sym, est='да', n=len(u), s=u.index[0].date(), e=u.index[-1].date(),
                     cur=cur, pv=pv, cena=u['close'].iloc[-1],
                     nominal_loc=pv*u['close'].iloc[-1],
                     rolls_god=round(smeny/let,2), tab_rolls=r['rolls_per_year'],
                     N=r['roll_offset_days'], birzha=r['exchange']))
d = pd.DataFrame(rows).set_index('symbol')
pd.set_option('display.width',250, 'display.max_rows',100)
print(d.to_string())
print('\nНЕТ ДАННЫХ:', list(d[d.est=='НЕТ'].index))
print('\nВалюты в наборе:', sorted(d['cur'].dropna().unique()))
