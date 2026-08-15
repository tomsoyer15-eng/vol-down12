#!/usr/bin/env python3
"""Разные окна средней по ногам: индекс 12 месяцев, облигации 3 (запрос заказчика 14.08.2026).

ПОЧЕМУ ДВИЖОК НЕ ТРОГАЕТСЯ. sim_v13.sim принимает готовые массивы состояний st_eq и st_bd,
то есть окно средней в него НЕ зашито — оно живёт в sigs(). Достаточно построить сигналы
с другим окном и подать их в тот же замороженный движок: арифметика исполнения, полоса,
кап, роллы и издержки остаются те же самые, меняется ровно одно — правило включения ноги.

БАЗА СРАВНЕНИЯ — 12/12, то есть действующий норматив §1.
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'r33build'))
import sim_v13 as S                                                    # noqa: E402

D = ROOT / 'r33build/data'


def sigs_n(px, days, n):
    """sigs() с параметром окна. Тело — копия замороженной функции, отличие только в n."""
    me = px.resample('ME').last().ffill()
    sma = me.rolling(n).mean()
    s = (me > sma)[sma.notna()]
    sm = pd.Series(s.values, index=(s.index + pd.offsets.MonthBegin(1)).to_period('M'))
    return sm.reindex(days.to_period('M')).ffill().astype(bool).values


def _sverka_sigs():
    """Копия обязана совпасть с движком при n=12 — иначе сравнивать нечего."""
    spy = pd.read_csv(D / 'spy_daily.csv', parse_dates=['date'], index_col='date')
    days = spy.index[spy.index >= pd.Timestamp('2003-07-01')]
    a = S.sigs(spy['adj'], days)
    b = sigs_n(spy['adj'], days, 12)
    ok = (a == b).all()
    print(f'[{"OK  " if ok else "ПРОВАЛ"}] копия sigs при n=12 совпала с движком '
          f'({len(a):,} сессий)')
    if not ok:
        raise SystemExit('копия сигналов не воспроизводит движок')


def nabor_modern(n_eq, n_bd):
    spy = pd.read_csv(D / 'spy_daily.csv', parse_dates=['date'], index_col='date')
    ief = pd.read_csv(D / 'ief_daily.csv', parse_dates=['date'], index_col='date')['adj']
    y = pd.read_csv(D / 'dgs10.csv', parse_dates=['observation_date']).set_index(
        'observation_date')['DGS10'].dropna() / 100
    days = spy.index[spy.index >= pd.Timestamp('2003-07-01')]
    re = spy['adj'].pct_change().reindex(days).fillna(0).values
    rb = ief.pct_change().reindex(days).fillna(0).values
    rfv = S.rf_series(days).values
    spy_close = spy['close'].reindex(days).values
    Dref = pd.Series([S.dur(v) for v in y.values], index=y.index).reindex(days).ffill()
    return (days, re, rb, rfv, spy_close, Dref.values,
            sigs_n(spy['adj'], days, n_eq), sigs_n(ief, days, n_bd))


def nabor_splice(n_eq, n_bd):
    spy = pd.read_csv(D / 'spy_daily.csv', parse_dates=['date'], index_col='date')
    ief = pd.read_csv(D / 'ief_daily.csv', parse_dates=['date'], index_col='date')['adj']
    mk = pd.read_csv(D / 'us-market-daily-1885-2025.csv',
                     parse_dates=['Date']).set_index('Date').sort_index()
    eq_r = pd.concat([mk['Adjusted Close'].pct_change(),
                      spy['adj'].pct_change()[spy.index > mk.index[-1]]])
    recon = pd.read_csv(D / 'bond10_recon_daily.csv',
                        parse_dates=['Date'], index_col='Date')['ret']
    bond_idx = (1 + pd.concat([recon[recon.index < ief.index[0]],
                               ief.pct_change().dropna()])).cumprod().reindex(eq_r.index).ffill()
    days = eq_r.index[eq_r.index >= pd.Timestamp('1963-03-01')]
    re = eq_r.reindex(days).fillna(0).values
    rb = bond_idx.pct_change().reindex(days).fillna(0).values
    rfv = S.rf_series(days).values
    eq_lvl = (1 + pd.Series(re, index=days)).cumprod()
    synth_close = (spy['close'].iloc[-1] * eq_lvl / eq_lvl.iloc[-1]).values
    y = pd.read_csv(D / 'dgs10.csv', parse_dates=['observation_date']).set_index(
        'observation_date')['DGS10'].dropna() / 100
    Dref = pd.Series([S.dur(v) for v in y.values], index=y.index).reindex(days).ffill().bfill()
    eq_full = (1 + eq_r.fillna(0)).cumprod()
    return (days, re, rb, rfv, synth_close, Dref.values,
            sigs_n(eq_full, days, n_eq), sigs_n(bond_idx.dropna(), days, n_bd))


def _mery(r, trades):
    eqc = (1 + r).cumprod()
    yrs = (r.index[-1] - r.index[0]).days / 365.25
    dd = eqc / eqc.cummax() - 1
    mes = eqc.resample('ME').last().pct_change().dropna()
    return dict(cagr=(eqc.iloc[-1] ** (1 / yrs) - 1) * 100, dd=dd.min() * 100,
                худший=mes.min() * 100, когда=mes.idxmin(), сделок=trades / yrs)


if __name__ == '__main__':
    _sverka_sigs()
    print()
    # 12/12 — действующий норматив; 12/3 — запрос заказчика; соседние окна ноги Б показаны,
    # чтобы видеть, ПЛАТО это или одиночный выброс. Это НЕ подбор параметра: правило
    # проекта — параметры априори, и никакое окно отсюда нормативом не становится.
    pary = [(12, 12), (12, 3), (12, 6), (12, 9)]
    for imya, stroitel in (('склейка 1963–2026', nabor_splice),
                           ('современное окно 2003–2026', nabor_modern)):
        print(f'--- {imya}')
        print(f'{"окна А/Б":<12}{"CAGR":>8}{"просадка":>11}{"худший мес":>13}{"когда":>9}'
              f'{"сделок/год":>12}')
        for n_eq, n_bd in pary:
            r, nav, tr = S.sim(*stroitel(n_eq, n_bd), strict=True)
            m = _mery(r, tr)
            met = '  <- норматив' if (n_eq, n_bd) == (12, 12) else (
                  '  <- запрос' if (n_eq, n_bd) == (12, 3) else '')
            print(f'{f"{n_eq}/{n_bd}":<12}{m["cagr"]:>7.2f}%{m["dd"]:>10.1f}%'
                  f'{m["худший"]:>12.1f}%' + f'{m["когда"]:%Y-%m}'.rjust(9)
                  + f'{m["сделок"]:>12.1f}{met}')
        print()
