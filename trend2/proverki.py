# -*- coding: utf-8 -*-
"""Проверки самого движка: каждая обязана сначала доказать, что достигает того,
что проверяет (счётчиком сработок или отличием от базлайна)."""
import pickle
import numpy as np, pandas as pd
import konfig as K, dvizhok as D, metriki as M

p = pickle.load(open(K.KESH / 'paneli.pkl', 'rb'))
m = D.podgotovit(p)
ekv, poz = D.prognoz(p, m)
kal = ekv.index
pm = pd.PeriodIndex(kal, freq='M')
posl = pd.Series(np.r_[pm[:-1] != pm[1:], True], index=kal)

# A. позиция меняется только в последний торговый день месяца
izm = (poz.diff().abs().sum(axis=1) > 0)
izm.iloc[0] = False
plohie = izm & ~posl
print(f'A. дней смены позиции {int(izm.sum())}, из них не в конце месяца {int(plohie.sum())} '
      f'(достижимость: месяцев в окне {pm.nunique()})')

# B. номинал одного рынка не выше потолка 3 капитала части 2
stoim = pd.DataFrame(m['P'] * m['PV'] * m['FX'], index=m['kal'], columns=m['syms']).reindex(kal)
nom = (poz.abs() * stoim[poz.columns])
wide = [s for s in poz.columns if s in K.SPISOK_WIDE]
otn = nom[wide].div(ekv['chast2'], axis=0)
print(f'B. максимум номинала одного рынка к капиталу части 2: {otn.max().max():.3f} '
      f'(потолок {K.NASTROJKI["potolok_nominala"]}); достигших 90% потолка рынко-дней: '
      f'{int((otn > 0.9 * K.NASTROJKI["potolok_nominala"]).sum().sum())}')

# C. реконструкция капитала из позиций, цен, ставки и издержек
A = pd.DataFrame(m['A'], index=m['kal'], columns=m['syms']).reindex(kal)
dA = A.diff().fillna(0.0)
pv_fx = pd.Series(m['PV'] * m['FX'], index=m['syms'])
pnl = (poz.shift().fillna(0.0) * dA[poz.columns] * pv_fx[poz.columns]).sum(axis=1)
dni = pd.Series(np.r_[0.0, np.diff(kal.values).astype('timedelta64[D]').astype(float)], index=kal)
st = p['stavka'].reindex(kal).shift().fillna(0.0)
kap = ekv['kapital'].shift().fillna(K.SCHET)
proc = kap * st * dni / K.NASTROJKI['baza_nachisleniya']
rekon = (K.SCHET + (pnl + proc - ekv['izderzhki']).cumsum())
otkl = (rekon - ekv['kapital']).abs().max() / ekv['kapital'].max()
print(f'C. реконструкция капитала из позиций: макс. отклонение {otkl:.2e} от максимума капитала')

# D. зонд достижимости: сигнал «всегда деньги» обязан дать ровно денежный счёт
pusto = p['sig_mes'] * 0.0
e0, z0 = D.prognoz(p, m, signal_mes=pusto)
den = M.denezhnyj_schet(p['stavka'], kal) * K.SCHET
print(f'D. сигнал «всегда деньги»: позиций ненулевых {int((z0 != 0).sum().sum())}, '
      f'капитал {e0["kapital"].iloc[-1]:,.0f} против денежного счёта {den.iloc[-1]:,.0f}, '
      f'расхождение {abs(e0["kapital"].iloc[-1]/den.iloc[-1]-1):.2e}')

# E. издержки по годам: роллы дороже там, где счёт крупнее
e_b, _ = D.prognoz(p, m, nastr=dict(platim_rolly=False))
god = pd.DataFrame(dict(vse=ekv['izderzhki'], bez=e_b['izderzhki'],
                        kapital=ekv['kapital'])).groupby(kal.year).agg(
    dict(vse='sum', bez='sum', kapital='mean'))
god['rolly'] = god['vse'] - god['bez']
god['rolly_pct'] = (god['rolly'] / god['kapital'] * 100).round(3)
print('E. издержки по годам (первые и последние три):')
print(pd.concat([god.head(3), god.tail(3)]).round(0).to_string())
