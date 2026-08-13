import pandas as pd, numpy as np, pickle
d=pickle.load(open('engine.pkl','rb'))
days,L2,st_eq,st_bd,rfv = d['days'],d['L2'],d['st_eq'],d['st_bd'],d['rfv']

print('=== БЛОК 3. Маржинальная механика (допущения: ES 5% номинала, ZN 2%; проверить живьём) ===')
worst_day = L2.min(); wd_date = L2.idxmin()
print(f"худший день 2× за 63 года: {worst_day*100:.1f}% ({wd_date.date()})")
# капитал после худшего дня до ребаланса: E'=1+r, номинал 2E → отношение E'/(2E)
for stress,mult in [('база',1.0),('стресс ×2',2.0),('стресс ×3',3.0)]:
    m_req = (0.05+0.02)*mult  # обе ноги вкл: требование как доля капитала (номинал каждой = 1×E)
    ratio_normal = 1.0/m_req
    ratio_worst = (1+worst_day)/m_req
    print(f"{stress:9s}: требование {m_req*100:4.1f}% капитала | запас в норме {ratio_normal:4.1f}× | после худшего дня {ratio_worst:4.1f}×")

# худшая неделя без ребаланса (месячный ребаланс плеча) — минимальный E/номинал по истории
rM_ret=[]
months=days.to_period('M'); starts=np.r_[0,np.flatnonzero(months[1:]!=months[:-1])+1]
spy_r = pd.read_csv('us-market-daily-1885-2025.csv', parse_dates=['Date']).set_index('Date')['Adjusted Close'].pct_change()
minratio=9; mind=None
e=1.0
d3=pickle.load(open('engine.pkl','rb'))
# восстановим дневные ноги из pkl-ингредиентов
mk = pd.read_csv('us-market-daily-1885-2025.csv', parse_dates=['Date']).set_index('Date').sort_index()
eq_r = mk['Adjusted Close'].pct_change()
spy = pd.read_csv('spy_daily.csv', parse_dates=['date'], index_col='date')['adj'].pct_change()
eq_r = pd.concat([eq_r, spy[spy.index>eq_r.index[-1]]])
recon = pd.read_csv('bond10_recon_daily.csv', parse_dates=['Date'], index_col='Date')['ret']
ief = pd.read_csv('ief_daily.csv', parse_dates=['date'], index_col='date')['adj'].pct_change().dropna()
bond_r = pd.concat([recon[recon.index<ief.index[0]], ief])
bond_idx=(1+bond_r).cumprod().reindex(eq_r.index).ffill()
rb = bond_idx.pct_change().reindex(days).fillna(0).values
re = eq_r.reindex(days).fillna(0).values
sd=0.5/100/252
for j,s0 in enumerate(starts):
    e1=starts[j+1] if j+1<len(starts) else len(days)
    Ne=2*0.5*st_eq[s0]*e; Nb=2*0.5*st_bd[s0]*e; N=Ne+Nb
    for i in range(s0,e1):
        e = e*(1+rfv[i]) + Ne*(re[i]-rfv[i]-sd) + Nb*(rb[i]-rfv[i]-sd)
        if N>0:
            ratio = e/(0.07*N)   # запас к базовому требованию
            if ratio<minratio: minratio=ratio; mind=days[i]
print(f"месячный ребаланс плеча: минимальный запас к базовой марже за 63 года: {minratio:.1f}× ({mind.date()}); при стрессе ×3: {minratio/3:.1f}×")

print('\n=== ДОВЕСОК. 1929–1933 на месячных данных Шиллера (без внутримесячных гэпов) ===')
sh = pd.read_csv('shiller-monthly-1871-2026.csv', parse_dates=['Date']).set_index('Date').sort_index()
p=sh['SP500']; div=sh['Dividend']; y10=sh['Long Interest Rate']/100
eqm = ((p + div/12)/p.shift(1) - 1).dropna()
def dur_conv(yv):
    c=yv/2; n=20; t=np.arange(1,n+1)
    cf=np.full(n,c); cf[-1]+=1.0; disc=(1+c)**(-t); pv=cf*disc; P=pv.sum()
    Dh=(t*pv).sum()/P; Ch=(t*(t+1)*pv).sum()/P/(1+c)**2
    return Dh/2/(1+c), Ch/4
D=[];C=[]
for v in y10.values:
    a,b=dur_conv(v); D.append(a); C.append(b)
D=pd.Series(D,index=y10.index); C=pd.Series(C,index=y10.index)
dy=y10.diff()
bdm=(y10.shift(1)/12 - D.shift(1)*dy + 0.5*C.shift(1)*dy**2).dropna()
rf_m = (1+mk['Risk Free Rate'].resample('ME').last().ffill()/100)**(1/12)-1
rf_m.index=rf_m.index.to_period('M').to_timestamp('M')
idx = eqm.index.intersection(bdm.index)
eqm,bdm = eqm.reindex(idx), bdm.reindex(idx)
rfm = rf_m.reindex(pd.PeriodIndex(idx,freq='M').to_timestamp('M')).ffill().values
eqi=(1+eqm).cumprod(); bdi=(1+bdm).cumprod()
def sigm(ix): 
    s=(ix>ix.rolling(12).mean()); return s.shift(1).fillna(False)  # сигнал прошлого месяца
se=sigm(eqi).values; sb=sigm(bdi).values
sd_m=0.5/100/12
r2 = rfm + 1.0*se*(eqm.values-rfm-sd_m) + 1.0*sb*(bdm.values-rfm-sd_m)
r2 = pd.Series(r2, index=idx)
sw = np.abs(np.diff(se.astype(float),prepend=0))+np.abs(np.diff(sb.astype(float),prepend=0))
r2 = r2 - 0.0005*sw
win=r2['1929-01-01':'1934-12-31']; eqw=(1+win).cumprod()
print(f"2× в 1929–1934: итог {((eqw.iloc[-1]-1)*100):+.1f}%, MaxDD {((eqw/eqw.cummax()-1).min())*100:.1f}%")
h29=eqm['1929-01-01':'1934-12-31']; he=(1+h29).cumprod()
print(f"акции в 1929–1934: итог {((he.iloc[-1]-1)*100):+.1f}%, MaxDD {((he/he.cummax()-1).min())*100:.1f}%")
full=(1+r2).cumprod(); yrs=(idx[-1]-idx[0]).days/365.25
print(f"справка, 2× на месячных 1872–2026: CAGR {(full.iloc[-1]**(1/yrs)-1)*100:.2f}%, MaxDD {((full/full.cummax()-1).min())*100:.1f}%")
