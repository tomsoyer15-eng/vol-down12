# -*- coding: utf-8 -*-
"""Синтетическая ADD-FUT до появления фьючерсов ES/ZN — раздел 3 протокола.

«Построено из индекса SPX и синтетической десятилетней облигации по доходности
TNX». Данные — СЫРЫЕ, независимые от существующей реализации:
- SPX: месячный индекс Роберта Шиллера (shiller-monthly-1871-2026.csv), цена
  без дивидендов; дивиденды НЕ берутся из его же колонки Dividend (это была бы
  собственная оценка Шиллера, произвольная относительно допущения протокола),
  а подставлены плоские 3% годовых — ровно допущение протокола раздела 3.
- Облигация: месячная доходность 10-летних казначейских (DGS10, FRED),
  переведённая в полную доходность через дюрацию облигации, торгуемой по
  номиналу (текстбучная формула Маколея для купонной облигации с купоном,
  равным доходности) — не код проекта, общая финансовая формула.

Файл us-market-daily-1885-2025.csv в репозитории НЕ используется: в нём уже
посчитаны чужие Swap Rate/Total Real Return — это готовый чужой результат,
а не сырые данные.
"""
import numpy as np, pandas as pd
import konfig as K

SHILLER = K.REPO / 'r33build' / 'data' / 'shiller-monthly-1871-2026.csv'
DGS10 = K.REPO / 'r33build' / 'data' / 'dgs10.csv'
DIVIDEND_GOD = 0.03


def spx_mesyachnyj():
    """Полная доходность SPX (цена + плоские 3% годовых), база=100 в первый месяц."""
    sh = pd.read_csv(SHILLER, parse_dates=['Date']).set_index('Date')['SP500']
    sh.index = sh.index.to_period('M')
    r_polnaya = (sh.pct_change() + DIVIDEND_GOD / 12.0).dropna()
    return 100.0 * (1 + r_polnaya).cumprod()


def _macaulay_dur_par(y, let=10, m=2):
    """Дюрация Маколея купонной облигации, торгуемой по номиналу (купон = y),
    полугодовые купоны, срок `let` лет, годовая доходность y (доли единицы)."""
    y = np.asarray(y, dtype=float)
    n = let * m
    c = y / m
    per = np.arange(1, n + 1)
    # цена по номиналу: PV каждого купона = c*100/(1+c)^t (при купоне=доходности
    # цена равна номиналу по определению; веса на этом и строятся)
    w = (c[:, None] * 100.0) / (1 + c[:, None]) ** per[None, :]
    w[:, -1] += 100.0 / (1 + c) ** n
    t_let = per / m
    d_mac = (w * t_let[None, :]).sum(axis=1) / 100.0
    return d_mac


def obligaciya_mesyachnaya(let=10):
    """Месячная полная доходность синтетической облигации по доходности DGS10."""
    d = pd.read_csv(DGS10, parse_dates=['observation_date']).set_index('observation_date')['DGS10'] / 100.0
    d = d.resample('ME').last().dropna()
    d.index = d.index.to_period('M')
    y_prev = d.shift(1)
    d_mod = _macaulay_dur_par(y_prev.values, let=let) / (1 + y_prev.values / 2.0)
    r = y_prev.values / 12.0 - d_mod * (d.values - y_prev.values)
    r = pd.Series(r, index=d.index).dropna()
    return 100.0 * (1 + r).cumprod()


def dva_nogi_mesyachno(nachalo=None, konec=None):
    """Месячные индексы SPX и синтетической 10-летней облигации, общий период.

    База 100 в ПЕРВЫЙ месяц общего пересечения обоих рядов (не в `nachalo` —
    если там ещё нет данных по одному из рядов, база 100 там даст NaN).
    """
    spx, obl = spx_mesyachnyj(), obligaciya_mesyachnaya()
    start = max(spx.index[0], obl.index[0], pd.Period(nachalo, 'M') if nachalo else spx.index[0])
    end = min(spx.index[-1], obl.index[-1], pd.Period(konec, 'M') if konec else spx.index[-1])
    per = pd.period_range(start, end, freq='M')
    spx = (spx.reindex(per) / spx.loc[start] * 100.0)
    obl = (obl.reindex(per) / obl.loc[start] * 100.0)
    return spx, obl


if __name__ == '__main__':
    spx, obl = dva_nogi_mesyachno('1962-01', '2026-07')
    print('SPX синтетика:', spx.index[0], '..', spx.index[-1], '| значения', spx.iloc[0], '->', round(spx.iloc[-1]))
    print('Облигация синтетика:', obl.index[0], '..', obl.index[-1], '| значения', obl.iloc[0], '->', round(obl.iloc[-1], 1))
    r_spx = spx.pct_change().dropna()
    r_obl = obl.pct_change().dropna()
    god = len(r_spx) / 12
    print(f'SPX: CAGR {(spx.iloc[-1]/spx.iloc[0])**(1/god)-1:.4f}  '
          f'просадка {(spx/spx.cummax()-1).min():.4f}')
    print(f'Облигация: CAGR {(obl.iloc[-1]/obl.iloc[0])**(1/god)-1:.4f}  '
          f'просадка {(obl/obl.cummax()-1).min():.4f}')
