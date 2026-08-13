import pandas as pd, numpy as np
dg = pd.read_csv('dgs10.csv', parse_dates=['observation_date']).rename(columns={'observation_date':'Date'}).set_index('Date')['DGS10'].dropna()
y = dg/100.0
# точные дюрация и выпуклость 10-летней парной облигации (полугодовые купоны)
def dur_conv(yv):
    c=yv/2; n=20
    t=np.arange(1,n+1)
    cf=np.full(n, c); cf[-1]+=1.0
    disc=(1+c)**(-t)
    pv=cf*disc; P=pv.sum()
    D_half=(t*pv).sum()/P
    C_half=(t*(t+1)*pv).sum()/P/(1+c)**2
    D_mod=D_half/2/(1+c)
    C=C_half/4
    return D_mod, C
Ds=[]; Cs=[]
for v in y.values:
    d,c=dur_conv(v); Ds.append(d); Cs.append(c)
D=pd.Series(Ds,index=y.index); C=pd.Series(Cs,index=y.index)
dy=y.diff()
dt=y.index.to_series().diff().dt.days.fillna(1)/365.25
r=(y.shift(1)*dt) - D.shift(1)*dy + 0.5*C.shift(1)*dy**2
r=r.dropna()
recon=pd.DataFrame({'ret':r})
recon.to_csv('bond10_recon_daily.csv')
print('реконструкция:', r.index[0].date(),'→',r.index[-1].date(), len(r))

# валидация против IEF 2002–2025
ief=pd.read_csv('ief_daily.csv',parse_dates=['date'],index_col='date')['adj'].pct_change().dropna()
common=r.index.intersection(ief.index)
a,b=r.reindex(common),ief.reindex(common)
print(f"корреляция дневных с IEF: {a.corr(b):.3f}")
print(f"волатильность: рекон {a.std()*np.sqrt(252)*100:.2f}%  IEF {b.std()*np.sqrt(252)*100:.2f}%")
ca=((1+a).prod())**(252/len(a))-1; cb=((1+b).prod())**(252/len(b))-1
print(f"CAGR на общем окне: рекон {ca*100:.2f}%  IEF {cb*100:.2f}%")
