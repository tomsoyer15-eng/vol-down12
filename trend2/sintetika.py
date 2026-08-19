# -*- coding: utf-8 -*-
"""Синтетическая ADD-FUT до появления фьючерсов ES/ZN — раздел 3 протокола,
ДНЕВНАЯ гранулярность (ред. от 19.08 — заменяет прежнюю месячную версию).

Источники — СЫРЫЕ и публичные, независимые от существующей реализации:
- Акции: Kenneth French Data Library (Tuck School, Dartmouth) — дневные факторы
  Mkt-RF и RF с 1926-07-01, построены на CRSP (все акции NYSE/AMEX/NASDAQ с
  учётом дивидендов). Mkt-RF+RF = дневная ПОЛНАЯ доходность широкого рынка
  США — общепринятый академический прокси S&P 500 (корреляция дневных
  доходностей с SPX превышает 0.99 на всём периоде их совместной истории).
  Полная доходность уже включает дивиденды — допущение «плоские 3% годовых»
  (использованное в прежней месячной версии и протоколом) здесь не нужно
  вовсе, что не только даёт дневную гранулярность, но и снимает произвольное
  допущение.
  Файл: data_sintetiki/F-F_Research_Data_Factors_daily.csv (скачан
  18.08.2026 с https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html,
  copyright Eugene F. Fama and Kenneth R. French).
- Облигация: дневная доходность 10-летних казначейских (DGS10, FRED),
  переведённая в полную доходность через дюрацию облигации, торгуемой по
  номиналу (текстбучная формула Маколея для купонной облигации с купоном,
  равным доходности) — общая финансовая формула, не код проекта.

Файл us-market-daily-1885-2025.csv в репозитории НЕ используется: в нём уже
посчитаны чужие Swap Rate/Total Real Return — готовый чужой результат, а не
сырые данные.
"""
import copy
import numpy as np, pandas as pd
import konfig as K

FAMA_FRENCH = K.KORENJ / 'data_sintetiki' / 'F-F_Research_Data_Factors_daily.csv'
DGS10 = K.REPO / 'r33build' / 'data' / 'dgs10.csv'


def rynok_dnevnoj():
    """Дневной индекс полной доходности широкого рынка США (прокси SPX), база=100."""
    d = pd.read_csv(FAMA_FRENCH, skiprows=3, skipfooter=1, engine='python')
    d.columns = ['data'] + list(d.columns[1:])
    d['data'] = pd.to_datetime(d['data'], format='%Y%m%d', errors='coerce')
    d = d.dropna(subset=['data']).set_index('data')
    r = (d['Mkt-RF'] + d['RF']) / 100.0
    return 100.0 * (1 + r).cumprod()


def _macaulay_dur_par(y, let=10, m=2):
    """Дюрация Маколея купонной облигации, торгуемой по номиналу (купон = y),
    полугодовые купоны, срок `let` лет, годовая доходность y (доли единицы)."""
    y = np.asarray(y, dtype=float)
    n = let * m
    c = y / m
    per = np.arange(1, n + 1)
    w = (c[:, None] * 100.0) / (1 + c[:, None]) ** per[None, :]
    w[:, -1] += 100.0 / (1 + c) ** n
    t_let = per / m
    return (w * t_let[None, :]).sum(axis=1) / 100.0


def obligaciya_dnevnaya(let=10):
    """Дневная полная доходность синтетической облигации по доходности DGS10."""
    d = pd.read_csv(DGS10, parse_dates=['observation_date']).set_index('observation_date')['DGS10']
    d = (d / 100.0).ffill().dropna()
    y_prev = d.shift(1)
    d_mod = _macaulay_dur_par(y_prev.values, let=let) / (1 + y_prev.values / 2.0)
    r = y_prev.values / 252.0 - d_mod * (d.values - y_prev.values)
    r = pd.Series(r, index=d.index).dropna()
    return 100.0 * (1 + r).cumprod()


def dva_nogi_dnevno(nachalo=None, konec=None):
    """Дневные индексы (акции, облигация), пересечение календарей обоих рядов."""
    ak, obl = rynok_dnevnoj(), obligaciya_dnevnaya()
    start = max(ak.index[0], obl.index[0], pd.Timestamp(nachalo) if nachalo else ak.index[0])
    end = min(ak.index[-1], obl.index[-1], pd.Timestamp(konec) if konec else ak.index[-1])
    ak, obl = ak.loc[start:end], obl.loc[start:end]
    ak = ak / ak.iloc[0] * 100.0
    obl = obl / obl.iloc[0] * 100.0
    return ak, obl


def dva_nogi_mesyachno(nachalo=None, konec=None):
    """Месячные срезы (последний торговый день месяца) — для идеализированной
    месячной проверки sintetika_sim.py; сами ряды всё равно дневные."""
    ak, obl = dva_nogi_dnevno(nachalo, konec)
    ak_m = ak.groupby(ak.index.to_period('M')).last()
    obl_m = obl.groupby(obl.index.to_period('M')).last()
    per = ak_m.index.intersection(obl_m.index)
    return ak_m.loc[per], obl_m.loc[per]


def stroki_dlya_dvizhka(ryady_real):
    """Синтетические ДНЕВНЫЕ строки ES и ZN для склейки с реальными рядами.

    По каждой ноге — своя точка стыка (первая дата реальных данных: 1997-09-09
    у ES, 1982-05-03 у ZN — реальный ZN есть уже с 1982, синтетика ему нужна
    только на 1962-1982). Синтетический уровень отмасштабирован так, чтобы
    ПОСЛЕДНЕЕ синтетическое значение точно совпало с ПЕРВОЙ реальной ценой
    (backadj) — иначе на стыке возникнет ложный скачок цены и фиктивный П/У.
    Масштаб — умножение на константу, доходностей внутри участка не меняет.
    """
    ak, obl = dva_nogi_dnevno()
    out = {}
    for sym, indeks in (('ES', ak), ('ZN', obl)):
        real = ryady_real[sym]
        styk_data = real.index[0]
        styk_cena = float(real['cena_adj'].iloc[0])
        uch = indeks[indeks.index < styk_data]
        if len(uch) == 0:
            continue
        uch = uch * (styk_cena / uch.iloc[-1])
        out[sym] = pd.DataFrame({'cena': uch.values, 'cena_adj': uch.values,
                                 'post_mes': np.nan, 'oborot': np.nan}, index=uch.index)
    return out


def skleit(dan):
    """Новый словарь данных (формата zagruzka.sobrat()) с синтетическими
    дневными строками ES/ZN, приклеенными спереди к реальным рядам. Календарь,
    курсы и ставка ПЕРЕСЧИТЫВАЮТСЯ на расширенный диапазон дат."""
    import zagruzka as Z
    dan2 = copy.copy(dan)
    dan2['ryady'] = dict(dan['ryady'])
    stroki = stroki_dlya_dvizhka(dan['ryady'])
    for sym, sint in stroki.items():
        dan2['ryady'][sym] = pd.concat([sint, dan['ryady'][sym]]).sort_index()
    kal = pd.DatetimeIndex(sorted(set().union(*[set(v.index) for v in dan2['ryady'].values()])))
    dan2['kalendar'] = kal
    dan2['fx'] = Z.kursy(kal)
    dan2['stavka'] = Z.stavka_vekselja(kal)
    return dan2


if __name__ == '__main__':
    ak, obl = dva_nogi_dnevno('1962-01-01', '2026-07-31')
    for imya, x in (('рынок США (прокси SPX)', ak), ('синтетич. облигация', obl)):
        god = (x.index[-1] - x.index[0]).days / 365.25
        cagr = (x.iloc[-1] / x.iloc[0]) ** (1 / god) - 1
        dd = (x / x.cummax() - 1).min()
        print(f'{imya}: {x.index[0].date()}..{x.index[-1].date()} ({god:.1f} г, n={len(x)}) '
              f'CAGR {cagr*100:.2f}%  просадка {dd*100:.1f}%')
