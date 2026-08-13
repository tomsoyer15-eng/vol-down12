#!/usr/bin/env python3
"""Курсы и ставки для валютного хеджа — из открытого API Банка международных расчётов.

Курсы (WS_XRU, дневные, «единиц валюты за 1 доллар»):
  CHF с 01.09.1953, EUR с 28.06.1974 (до 1999 года — синтетический ряд ЭКЮ/евро).
Ставки центробанков (WS_CBPOL, месячные, конец периода):
  Швейцария с 1946 года (до 2000 — учётная ставка, далее целевой диапазон/ставка SNB);
  еврозона с 1999 года, сшита с учётной ставкой Бундесбанка 1948-1998.

Ставка ЦБ — не денежный рынок; для оценки процентного дифференциала за десятилетия
годится, но точную стоимость хеджа не даёт. Кросс-валютный базис (после 2008 года
он для пары EUR/USD устойчиво отрицательный) сюда не входит и добавляет
хеджирующему евро-инвестору порядка 0,1-0,3 п.п. в год сверх дифференциала.
"""
import io
from pathlib import Path
from urllib.request import Request, urlopen
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36'
API = 'https://stats.bis.org/api/v2/data/dataflow/BIS/{flow}/1.0/{key}?format=csv'


def get(flow, key):
    url = API.format(flow=flow, key=key)
    with urlopen(Request(url, headers={'User-Agent': UA}), timeout=120) as f:
        d = pd.read_csv(io.BytesIO(f.read()))
    s = d[['TIME_PERIOD', 'OBS_VALUE']].dropna()
    s['TIME_PERIOD'] = pd.to_datetime(s['TIME_PERIOD'], errors='coerce')
    return s.dropna().set_index('TIME_PERIOD')['OBS_VALUE'].sort_index()


if __name__ == '__main__':
    D = ROOT / 'data'
    for cur, key in [('chf', 'D.CH.CHF.A'), ('eur', 'D.XM.EUR.A')]:
        s = get('WS_XRU', key)
        s.rename('per_usd').rename_axis('date').to_csv(D / f'fx_{cur}usd_daily.csv')
        print(f"fx_{cur}usd_daily.csv: {len(s)} строк, {s.index[0].date()} - {s.index[-1].date()}")

    chf = get('WS_CBPOL', 'M.CH')
    chf.rename('rate').rename_axis('date').to_csv(D / 'rate_chf_policy.csv')
    print(f"rate_chf_policy.csv: {chf.index[0].date()} - {chf.index[-1].date()}")

    de, xm = get('WS_CBPOL', 'M.DE'), get('WS_CBPOL', 'M.XM')
    eur = pd.concat([de[de.index < xm.index[0]], xm])
    eur.rename('rate').rename_axis('date').to_csv(D / 'rate_eur_policy.csv')
    print(f"rate_eur_policy.csv: {eur.index[0].date()} - {eur.index[-1].date()} "
          f"(Бундесбанк до {de.index[-1].date()}, далее ЕЦБ)")
