# -*- coding: utf-8 -*-
"""Полное окно 1979-2026 через ДНЕВНОЙ движок: синтетика ES/ZN (раздел 3 протокола)
до появления реальных фьючерсов, склеенная с реальными данными, плюс широкая нога
на всей доступной истории (рынки входят по мере появления, как в разделе 18
протокола). Один прогон, тот же движок, что и в основном отчёте — не отдельная
модель.
"""
import pickle
import numpy as np, pandas as pd
import konfig as K, zagruzka as Z, podgotovka as PG, dvizhok as D, metriki as M, sintetika as S

NACHALO = '1979-01-02'
KONEC = '2026-08-07'

dan = pickle.load(open(K.KESH / 'dannye.pkl', 'rb'))
dan_sint = S.skleit(dan)
for sym in ('ES', 'ZN'):
    print(f'{sym}: было с {dan["ryady"][sym].index[0].date()}, '
          f'стало с {dan_sint["ryady"][sym].index[0].date()} '
          f'(+{len(dan_sint["ryady"][sym])-len(dan["ryady"][sym])} синтетических строк)')

p = PG.sobrat(dan_sint, nastr={'nachalo': NACHALO, 'konec': KONEC})
m = D.podgotovit(p)
e, z = D.prognoz(p, m, nastr={'nachalo': NACHALO, 'konec': KONEC})
e_bez, z_bez = D.prognoz(p, m, nastr={'nachalo': NACHALO, 'konec': KONEC, 'platim_rolly': False})

K.VYVOD.mkdir(parents=True, exist_ok=True)
for c in ('kapital', 'chast1', 'chast2', 'protsent', 'izderzhki', 'nominal_chast2',
          'nominal_chast1', 'zalog'):
    e[c] = e[c].round(2)
e.to_csv(K.VYVOD / 'equity_polnoe_okno.csv')
z.to_csv(K.VYVOD / 'pozicii_polnoe_okno.csv')

god = M.let(e['kapital'])
print(f'\nокно {e.index[0].date()} .. {e.index[-1].date()} ({god:.2f} года)')
for imya, x in (('счёт', e['kapital']), ('часть 1 (ES+ZN)', e['chast1']), ('часть 2 (широкая)', e['chast2'])):
    print(f'{imya:20s}: CAGR {M.cagr(x)*100:.2f}%  просадка {M.prosadka(x)*100:.1f}%  '
          f'колебания {M.kolebaniya(x)*100:.1f}%  худшая 5-летка {M.hudshaya_pyatiletka(x)*100:+.2f}%')
print(f'\nбез оплаты роллов: CAGR {M.cagr(e_bez["kapital"])*100:.2f}%  '
      f'просадка {M.prosadka(e_bez["kapital"])*100:.1f}%')

d = pd.DatetimeIndex(pd.date_range(e.index[0], e.index[-1]))
den = M.denezhnyj_schet(p['stavka'], e.index) * K.SCHET
print(f'сверх денежного рынка (векселей): {M.cagr(e["kapital"])*100 - M.cagr(den)*100:.2f}%')

s_es = pd.Series(z['ES'].values, index=z.index)
s_zn = pd.Series(z['ZN'].values, index=z.index)
print(f'\nконтракты ES: старт {s_es.iloc[0]}, на стыке синтетики (1997-09-08/09) '
      f'{s_es.loc[:"1997-09-08"].iloc[-1] if (s_es.index<"1997-09-09").any() else "н/д"} -> '
      f'{s_es.loc["1997-09-09":].iloc[0]}')
