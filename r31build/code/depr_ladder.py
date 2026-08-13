import pandas as pd, numpy as np
mk = pd.read_csv('us-market-daily-1885-2025.csv', parse_dates=['Date']).set_index('Date').sort_index()
sh = pd.read_csv('shiller-monthly-1871-2026.csv', parse_dates=['Date']).set_index('Date').sort_index()
p=sh['SP500']; div=sh['Dividend']; y10=sh['Long Interest Rate']/100
eqm=((p+div/12)/p.shift(1)-1).dropna()
def dc(yv):
    c=yv/2; n=20; t=np.arange(1,n+1)
    cf=np.full(n,c); cf[-1]+=1.0; disc=(1+c)**(-t); pv=cf*disc; P=pv.sum()
    return (t*pv).sum()/P/2/(1+c), (t*(t+1)*pv).sum()/P/(1+c)**2/4
D=[];C=[]
for v in y10.values:
    a,b=dc(v); D.append(a); C.append(b)
D=pd.Series(D,index=y10.index);C=pd.Series(C,index=y10.index)
dy=y10.diff()
bdm=(y10.shift(1)/12 - D.shift(1)*dy + 0.5*C.shift(1)*dy**2).dropna()
rf_m=(1+mk['Risk Free Rate'].resample('ME').last().ffill()/100)**(1/12)-1
rf_m.index=rf_m.index.to_period('M').to_timestamp('M')
idx=eqm.index.intersection(bdm.index)
eqm,bdm=eqm.reindex(idx),bdm.reindex(idx)
rfm=rf_m.reindex(pd.PeriodIndex(idx,freq='M').to_timestamp('M')).ffill().values
eqi=(1+eqm).cumprod(); bdi=(1+bdm).cumprod()
sig=lambda ix:(ix>ix.rolling(12).mean()).shift(1).fillna(False)
se,sb=sig(eqi).values,sig(bdi).values
sd_m=0.5/100/12
print('Лестница плеча в депрессию (месячные данные Шиллера, спред 0,5):')
for L in [1.0,1.5,1.75,2.0]:
    r=rfm + L*0.5*se*(eqm.values-rfm-sd_m) + L*0.5*sb*(bdm.values-rfm-sd_m)
    sw=np.abs(np.diff(se.astype(float),prepend=0))+np.abs(np.diff(sb.astype(float),prepend=0))
    r=pd.Series(r-0.0005*sw*L*0.5, index=idx)
    w=r['1929-01-01':'1934-12-31']; e=(1+w).cumprod()
    full=(1+r).cumprod(); yrs=(idx[-1]-idx[0]).days/365.25
    print(f"L={L:.2f}: 1929–34 итог {((e.iloc[-1]-1)*100):+6.1f}%, DD {((e/e.cummax()-1).min())*100:6.1f}%  |  1872–2026: CAGR {(full.iloc[-1]**(1/yrs)-1)*100:5.2f}%, DD {((full/full.cummax()-1).min())*100:6.1f}%")
print('\nВАЖНО: цены Шиллера — среднемесячные, они сглаживают ямы; реальная просадка глубже показанной.')
