#!/usr/bin/env python3
"""Загрузка лондонского золотого фиксинга (LBMA) в data/gold_lbma_pm_daily.csv.

Источник: https://prices.lbma.org.uk/json/gold_pm.json — официальный ряд LBMA Gold
Price PM (до 2015 года — London Gold Fixing PM), доллары за тройскую унцию,
с 01.04.1968. Файл gold_am.json (AM-фиксинг) идёт с 02.01.1968 и берётся только
для сверки: до 15.03.1968 цена была административно закреплена Бреттон-Вудсом
($35/унция), лондонский рынок был закрыт 15.03–01.04.1968, поэтому осмысленный
рыночный ряд начинается 01.04.1968.

Формат выхода — как у прочих рядов пакета: date,adj,close (у спота adj = close).
"""
import json, sys
from pathlib import Path
from urllib.request import Request, urlopen
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36'
URLS = {'pm': 'https://prices.lbma.org.uk/json/gold_pm.json',
        'am': 'https://prices.lbma.org.uk/json/gold_am.json'}


def load(kind):
    with urlopen(Request(URLS[kind], headers={'User-Agent': UA}), timeout=90) as f:
        j = json.load(f)
    s = pd.Series({pd.Timestamp(r['d']): (r['v'][0] if r['v'] else None) for r in j},
                  name='usd').astype(float).dropna().sort_index()
    return s[s > 0]


if __name__ == '__main__':
    pm = load('pm')
    out = ROOT / 'data' / 'gold_lbma_pm_daily.csv'
    pd.DataFrame({'date': pm.index, 'adj': pm.values, 'close': pm.values}).to_csv(out, index=False)
    print(f"{out}: {len(pm)} строк, {pm.index[0].date()} - {pm.index[-1].date()}, "
          f"{pm.iloc[0]:.2f} -> {pm.iloc[-1]:.2f} USD/унция")
    if '--check' in sys.argv:
        am = load('am')
        both = pd.concat([pm.rename('pm'), am.rename('am')], axis=1).dropna()
        d = (both['pm'] / both['am'] - 1).abs()
        print(f"сверка с AM-фиксингом: {len(both)} общих дней, "
              f"медиана |PM/AM-1| {d.median()*100:.3f}%, максимум {d.max()*100:.2f}% "
              f"({d.idxmax().date()})")
