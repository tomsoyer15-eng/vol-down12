#!/usr/bin/env python3
"""Расширение окна ядра до 1885 года.

Нога А — дневной индекс полной доходности США из пакета (35 254 наблюдения с 20.03.1885,
252 в год, нулевых изменений нет: данные наблюдённые, а не интерполированные).
Ставка без риска — оттуда же, с марта 1885.

Нога Б до 1962 года восстанавливается из месячной длинной ставки Шиллера: доход
постоянного десятилетнего пара = купон/252 − дюрация × изменение доходности, дюрация по
формуле ядра S.dur. С 1962 года — существующая реконструкция пакета, далее IEF.

ОГОВОРКА: ставка Шиллера месячная и усреднённая по месяцу, поэтому дневная качка ноги Б
до 1962 занижена, а её просадки сглажены. Нога А этим не затронута.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'r32build'))
import sim_v13 as S

START = pd.Timestamp('1886-01-01')          # год на прогрев 12-месячной средней


def build():
    D = S.D
    mk = pd.read_csv(D / 'us-market-daily-1885-2025.csv', parse_dates=['Date']).set_index('Date').sort_index()
    spy = pd.read_csv(D / 'spy_daily.csv', parse_dates=['date'], index_col='date')
    ief = pd.read_csv(D / 'ief_daily.csv', parse_dates=['date'], index_col='date')['adj']
    eq_r = pd.concat([mk['Adjusted Close'].pct_change(),
                      spy['adj'].pct_change()[spy.index > mk.index[-1]]])
    days_all = eq_r.index

    # ставка без риска: колонка пакета, далее DTB3
    rfa = mk['Risk Free Rate'].reindex(days_all).ffill()
    dtb = pd.read_csv(D / 'dtb3.csv', parse_dates=['observation_date']).set_index('observation_date')['DTB3'].dropna()
    dtb_eff = 365 * dtb / 100 / (360 - 91 * dtb / 100) * 100
    rfa[rfa.index > mk.index[-1]] = dtb_eff.reindex(rfa.index[rfa.index > mk.index[-1]]).ffill()
    rfv = (1 + rfa / 100) ** (1 / 252) - 1

    # длинная ставка: Шиллер месячно -> дневная линейной интерполяцией
    sh = pd.read_csv(D / 'shiller-monthly-1871-2026.csv', parse_dates=['Date'], index_col='Date')
    lr = sh['Long Interest Rate']
    lr = lr[lr > 0] / 100
    y10 = pd.read_csv(D / 'dgs10.csv', parse_dates=['observation_date']).set_index('observation_date')['DGS10'].dropna() / 100
    ycomb = pd.concat([lr[lr.index < y10.index[0]], y10]).sort_index()
    yd = ycomb.reindex(days_all.union(ycomb.index)).interpolate('time').reindex(days_all)

    dur = pd.Series([S.dur(v) for v in yd.values], index=days_all)
    # доход постоянного десятилетнего пара из ставки
    rb_syn = yd.shift(1) / 252 - dur.shift(1) * yd.diff()
    # существующая реконструкция и IEF там, где они есть
    recon = pd.read_csv(D / 'bond10_recon_daily.csv', parse_dates=['Date'], index_col='Date')['ret']
    rb = rb_syn.copy()
    rb.loc[recon.index.intersection(days_all)] = recon.reindex(days_all).dropna()
    ief_r = ief.pct_change()
    rb.loc[ief_r.index.intersection(days_all)] = ief_r.reindex(days_all).dropna()
    rb = rb.fillna(0.0)

    m = days_all >= START
    days = days_all[m]
    re = eq_r.reindex(days).fillna(0).values
    rbv = rb.reindex(days).values
    eq_lvl = (1 + pd.Series(re, index=days)).cumprod()
    synth_close = (spy['close'].iloc[-1] * eq_lvl / eq_lvl.iloc[-1]).values
    dref = dur.reindex(days).ffill().bfill().values
    eq_full = (1 + eq_r.fillna(0)).cumprod()
    bond_idx = (1 + rb).cumprod()
    return (days, re, rbv, rfv.reindex(days).values, synth_close, dref,
            S.sigs(eq_full, days), S.sigs(bond_idx, days))


if __name__ == '__main__':
    d = build()
    days = d[0]
    print(f'окно {days[0]:%d.%m.%Y} – {days[-1]:%d.%m.%Y}, {len(days)} дней, '
          f'{(days[-1]-days[0]).days/365.25:.1f} года')
    print(f'качка ноги А {pd.Series(d[1]).std()*np.sqrt(252)*100:.2f}%, '
          f'ноги Б {pd.Series(d[2]).std()*np.sqrt(252)*100:.2f}%')
    for a, b, l in [('1886-01-01','1930-01-01','до 1930'),('1930-01-01','1962-01-01','1930-1961'),
                    ('1962-01-01','2027-01-01','1962+')]:
        m = (days >= a) & (days < b)
        print(f'  {l}: нога А {pd.Series(d[1][m]).std()*np.sqrt(252)*100:5.2f}%, '
              f'нога Б {pd.Series(d[2][m]).std()*np.sqrt(252)*100:5.2f}%, '
              f'сигнал А включён {d[6][m].mean()*100:.0f}%, Б {d[7][m].mean()*100:.0f}%')
