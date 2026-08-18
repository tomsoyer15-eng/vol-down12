# -*- coding: utf-8 -*-
"""Месячная симуляция ADD-FUT на синтетике (раздел 3 протокола) — «идеализированный
расчёт», без реальных издержек и округления контрактов, для сверки с таблицей
протокола.

Модель ноги: сигнал месяца M действует в M+1 (та же логика, что в дневном
движке); пока сигнал=1 — нога получает полную доходность своего индекса
(включая допущение по дивидендам/купону); пока сигнал=0 — капитал ноги лежит
в векселях (DTB3). Обе ноги по 100% капитала части каждая (что и даёт
суммарное плечо 2.00 при обоих сигналах включённых) — то же определение,
что в дневном движке для ES/ZN.
"""
import numpy as np, pandas as pd
import konfig as K
import sintetika as S


def stavka_mesyachnaya():
    d = pd.read_csv(K.DTB3, parse_dates=['observation_date']).set_index('observation_date')['DTB3'] / 100.0
    m = d.resample('ME').last()
    m.index = m.index.to_period('M')
    return m


def signal(indeks, n=12):
    sma = indeks.rolling(n, min_periods=n).mean()
    return (indeks > sma).astype(float).where(sma.notna())


def progon_nogi(indeks, stavka, vsegda=False):
    r_indeks = indeks.pct_change()
    sig = pd.Series(1.0, index=indeks.index) if vsegda else signal(indeks)
    sig_deistv = sig.shift(1)                       # решение месяца M -> действует в M+1
    r = np.where(sig_deistv == 1, r_indeks, stavka.reindex(indeks.index) / 12.0)
    r = pd.Series(r, index=indeks.index)
    start = sig_deistv.first_valid_index() if not vsegda else indeks.index[1]
    r = r.loc[start:]
    eq = 100.0 * (1 + r.fillna(0)).cumprod()
    return eq


def itog(eq):
    god = len(eq) / 12
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / god) - 1
    prosadka = float((eq / eq.cummax() - 1).min())
    return cagr, prosadka, god


def progon_addfut(spx, obl, stavka, vsegda=False):
    e1 = progon_nogi(spx, stavka, vsegda)
    e2 = progon_nogi(obl, stavka, vsegda)
    kal = e1.index.intersection(e2.index)
    kap = 0.5 * (e1.reindex(kal) / e1.reindex(kal).iloc[0]) + 0.5 * (e2.reindex(kal) / e2.reindex(kal).iloc[0])
    kap *= 100.0
    return kap


if __name__ == '__main__':
    stavka = stavka_mesyachnaya()
    for nachalo, metka in (('1962-01', '1962–2026 (протокол: 12.25% / 12.44%, −51.6% / −32.9%)'),
                           ('1997-09', '1997–2026, синтетика на реальном окне фьючерсов')):
        spx, obl = S.dva_nogi_mesyachno(nachalo, '2026-07')
        baza = progon_addfut(spx, obl, stavka, vsegda=True)
        sma = progon_addfut(spx, obl, stavka, vsegda=False)
        cb, db, gb = itog(baza)
        cs, ds, gs = itog(sma)
        print(f'\n=== {metka} ===')
        print(f'окно {baza.index[0]}..{baza.index[-1]} ({gb:.1f} года)')
        print(f'  База (всегда):  CAGR {cb*100:.2f}%   просадка {db*100:.1f}%')
        print(f'  SMA12:          CAGR {cs*100:.2f}%   просадка {ds*100:.1f}%')
