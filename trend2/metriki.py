# -*- coding: utf-8 -*-
"""Итоговые меры и самопроверки."""
import numpy as np, pandas as pd
import konfig as K


def let(x):
    return (x.index[-1] - x.index[0]).days / 365.25


def cagr(x):
    return (x.iloc[-1] / x.iloc[0]) ** (1 / let(x)) - 1


def kolebaniya(x):
    return x.pct_change().dropna().std() * np.sqrt(252.0)


def prosadka(x):
    return float((x / x.cummax() - 1.0).min())


def denezhnyj_schet(stavka, index, baza=None):
    """Капитал, пролежавший всё окно в трёхмесячных векселях.

    База начисления по умолчанию = K.NASTROJKI — та же, что использует
    движок, а не отдельная зашитая константа (найдено ревью 18.08: обе
    сейчас равны 360, но были бы независимы, если ответ 3.1 пересмотрят).
    """
    baza = K.NASTROJKI['baza_nachisleniya'] if baza is None else baza
    # ставка берётся на предыдущий день — так же, как её начисляет движок,
    # иначе зонд достижимости D в proverki.py разойдётся на своём же сдвиге
    s = stavka.reindex(index).ffill().shift().fillna(0.0)
    dni = np.r_[0.0, np.diff(index.values).astype('timedelta64[D]').astype(float)]
    return pd.Series(np.cumprod(1 + s.values * dni / baza), index=index)


def hudshaya_pyatiletka(x, let_okna=5):
    """Худший годовой доход по всем пятилетним отрезкам с любого месяца."""
    m = x.resample('ME').last()
    k = let_okna * 12
    if len(m) <= k:
        return np.nan
    otn = (m.shift(-k) / m).dropna() ** (1 / let_okna) - 1
    return float(otn.min())


def svodka(ekv, stavka, imya=''):
    x = ekv['kapital']
    d = denezhnyj_schet(stavka, x.index)
    return dict(imya=imya, let=round(let(x), 2), CAGR=cagr(x),
                sverh_stavki=cagr(x) - cagr(d), kolebaniya=kolebaniya(x),
                prosadka=prosadka(x), hudshaya_5let=hudshaya_pyatiletka(x),
                rost=x.iloc[-1] / x.iloc[0])


def tablica_svodok(spisok):
    t = pd.DataFrame(spisok).set_index('imya')
    for c in ('CAGR', 'sverh_stavki', 'kolebaniya', 'prosadka', 'hudshaya_5let'):
        t[c] = (t[c] * 100).round(2)
    t['rost'] = t['rost'].round(1)
    return t


def po_rynkam(poz, ceny):
    """Доля времени в позиции, среднее число контрактов, рынки без целого контракта."""
    dostupen = ceny.reindex(poz.index)[poz.columns].notna()
    v_pozicii = (poz != 0)
    dolya = (v_pozicii.sum() / dostupen.sum().replace(0, np.nan))
    sred = poz.where(v_pozicii).mean()
    maks = poz.max()
    t = pd.DataFrame(dict(dolya_vremeni=dolya.round(3), sredne_kontraktov=sred.round(1),
                          maks_kontraktov=maks.astype(int),
                          dnej_dostupen=dostupen.sum().astype(int)))
    return t.sort_values('dolya_vremeni', ascending=False)
