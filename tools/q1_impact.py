#!/usr/bin/env python3
"""Цена расхождения сигналов: нога облигаций 1,0x, сигналы от VFITX против сигналов
от ряда ядра. Доходность в обоих случаях начисляется по ряду ЯДРА (реальная нога
торгует ZN, а не паи VFITX) — значит разница и есть цена прокси-подмены."""
import sys
from pathlib import Path
import pandas as pd, numpy as np
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT/'tools')); sys.path.insert(0, str(ROOT/'pkg'))
from q1_signals import load, monthly_state

d = load()
core_r = d['bond_core'].pct_change().dropna()
days = core_r.index[core_r.index >= pd.Timestamp('1994-06-01')]
core_r = core_r.reindex(days)

rf = pd.read_csv(ROOT/'pkg/data/us-market-daily-1885-2025.csv', parse_dates=['Date']).set_index('Date')['Risk Free Rate']
dtb = pd.read_csv(ROOT/'pkg/data/dtb3.csv', parse_dates=['observation_date']).set_index('observation_date')['DTB3'].dropna()
dtb_eff = 365*dtb/100/(360-91*dtb/100)*100
rfv = ((1+pd.concat([rf, dtb_eff[dtb_eff.index>rf.index[-1]]]).reindex(days).ffill()/100)**(1/252)-1)

def leg(state):
    st = state.reindex(days.to_period('M').to_timestamp('M')).ffill().astype(bool).values
    st = pd.Series(st, index=days).shift(1).bfill().values          # строгая конвенция T+1
    r = rfv.values + st*(core_r.values - rfv.values)
    return pd.Series(r, index=days)

sv, sc = monthly_state(d['vfitx']), monthly_state(d['bond_core'])
rv, rc = leg(sv), leg(sc)
def met(r):
    eq=(1+r).cumprod(); yrs=(r.index[-1]-r.index[0]).days/365.25
    return (eq.iloc[-1]**(1/yrs)-1)*100, (eq/eq.cummax()-1).min()*100, r.std()*np.sqrt(252)*100

print(f"окно {days[0]:%Y-%m-%d} … {days[-1]:%Y-%m-%d}, {len(days)} дней\n")
print(f"{'нога облигаций 1,0x':<34}{'CAGR':>8}{'MaxDD':>9}{'качка':>8}")
for nm, r in [('сигналы VFITX (как в §6 v0.2)', rv), ('сигналы ядра (recon+IEF)', rc)]:
    c,dd,v = met(r); print(f"{nm:<34}{c:>7.2f}%{dd:>8.1f}%{v:>7.2f}%")
c1,_,_=met(rv); c2,_,_=met(rc)
print(f"\nразница CAGR ноги: {c1-c2:+.2f} п.п.  → на уровне счёта (нога = 1 из 2) ≈ {(c1-c2):+.2f} п.п.")
print(f"корреляция дневных доходностей ног: {rv.corr(rc):.4f}")

idx = sv.index.intersection(sc.index); idx = idx[idx>=pd.Timestamp('1994-06-01')]
dis = idx[(sv.reindex(idx)!=sc.reindex(idx)).values]
mret = (1+core_r).resample('ME').prod()-1
print(f"\nмесячная доходность ряда ядра в {len(dis)} спорных месяцах:")
sel = mret.reindex(dis).dropna()
print(f"  средняя {sel.mean()*100:+.3f}%, медиана {sel.median()*100:+.3f}%, "
      f"размах [{sel.min()*100:+.2f}%, {sel.max()*100:+.2f}%]")
print(f"  для сравнения, все месяцы окна: средняя {mret.reindex(idx).dropna().mean()*100:+.3f}%")
