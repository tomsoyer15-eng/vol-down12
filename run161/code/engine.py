import pandas as pd, numpy as np, pickle
COST=0.0005

# --- сборка ног, календарь акций ---
mk = pd.read_csv('us-market-daily-1885-2025.csv', parse_dates=['Date']).set_index('Date').sort_index()
eq_r = mk['Adjusted Close'].pct_change()
rf_ann = mk['Risk Free Rate']
# продлеваем календарь до 08.2026 хвостом SPY (тот же индекс полной доходности)
spy = pd.read_csv('spy_daily.csv', parse_dates=['date'], index_col='date')['adj'].pct_change()
tail = spy[spy.index > eq_r.index[-1]]
eq_r = pd.concat([eq_r, tail])
rfr2 = pd.read_csv('rfr_daily.csv', parse_dates=['Date'], index_col='Date')['Risk Free Rate']
rf_ann = rf_ann.reindex(eq_r.index).ffill()
rf_d = (1+rf_ann/100)**(1/252)-1

recon = pd.read_csv('bond10_recon_daily.csv', parse_dates=['Date'], index_col='Date')['ret']
ief = pd.read_csv('ief_daily.csv', parse_dates=['date'], index_col='date')['adj'].pct_change().dropna()
splice_date = ief.index[0]
bond_r = pd.concat([recon[recon.index < splice_date], ief])
bond_idx = (1+bond_r).cumprod()
bond_idx = bond_idx.reindex(eq_r.index).ffill()
bond_r_al = bond_idx.pct_change()

start_data = pd.Timestamp('1962-01-03')
days_all = eq_r.index[eq_r.index>=start_data]
eq_idx = (1+eq_r.reindex(days_all).fillna(0)).cumprod()

def sig_sma12(idx_series):
    me = idx_series.resample('ME').last().ffill()
    sma = me.rolling(12).mean(); s=(me>sma)[sma.notna()]
    return pd.Series(s.values, index=(s.index+pd.offsets.MonthBegin(1)).to_period('M'))

sm_eq = sig_sma12(eq_idx); sm_bd = sig_sma12(bond_idx[bond_idx.index>=start_data].dropna())
START = pd.Timestamp('1963-03-01')
days = days_all[days_all>=START]
st_eq = sm_eq.reindex(days.to_period('M')).ffill().astype(bool).values
st_bd = sm_bd.reindex(days.to_period('M')).ffill().astype(bool).values
re = eq_r.reindex(days).fillna(0).values
rb = bond_r_al.reindex(days).fillna(0).values
rfv = rf_d.reindex(days).ffill().values

def lever_daily(L, s_ann, cost=COST):
    sd = s_ann/100/252
    Ee = L*0.5*st_eq; Eb = L*0.5*st_bd
    r = rfv + Ee*(re-rfv-sd) + Eb*(rb-rfv-sd)
    dE = np.abs(np.diff(Ee, prepend=0)) + np.abs(np.diff(Eb, prepend=0))
    return pd.Series(r - cost*dE, index=days)

def lever_monthly(L, s_ann, cost=COST):
    sd = s_ann/100/252
    months = days.to_period('M')
    starts = np.r_[0, np.flatnonzero(months[1:]!=months[:-1])+1]
    e=1.0; Ne=Nb=0.0; out=np.empty(len(days))
    for j,s0 in enumerate(starts):
        e0=e
        tgt_e = L*0.5*(st_eq[s0]); tgt_b = L*0.5*(st_bd[s0])
        newNe, newNb = tgt_e*e0, tgt_b*e0
        e -= cost*(abs(newNe-Ne)+abs(newNb-Nb)); Ne,Nb=newNe,newNb
        e1 = starts[j+1] if j+1<len(starts) else len(days)
        for i in range(s0,e1):
            e = e*(1+rfv[i]) + Ne*(re[i]-rfv[i]-sd) + Nb*(rb[i]-rfv[i]-sd)
            out[i]=e
    eqs=pd.Series(out,index=days); r=eqs.pct_change(); r.iloc[0]=out[0]-1
    return r

def unlev_mix():
    return lever_daily(1.0, 0.0)

def bh6040():
    W=np.array([0.6,0.4]); R=np.column_stack([re,rb])
    months=days.to_period('M'); starts=np.r_[0,np.flatnonzero(months[1:]!=months[:-1])+1]
    V=W.copy(); out=np.empty(len(days))
    for j,s0 in enumerate(starts):
        e1=starts[j+1] if j+1<len(starts) else len(days)
        if j>0:
            tot=V.sum(); tu=np.abs(W*tot-V).sum()/tot; V=W*tot*(1-COST*tu)
        G=np.cumprod(1+R[s0:e1],axis=0); out[s0:e1]=G@V; V=V*G[-1]
    eqs=pd.Series(out,index=days); r=eqs.pct_change(); r.iloc[0]=out[0]-1
    return r

def met(r, a=None, b=None):
    rr = r if a is None else r[a:b]
    eqc=(1+rr).cumprod(); yrs=(rr.index[-1]-rr.index[0]).days/365.25
    ex=rr-pd.Series(rfv,index=days).reindex(rr.index)
    dd=(eqc/eqc.cummax()-1)
    return (eqc.iloc[-1]**(1/yrs)-1)*100, rr.std()*np.sqrt(252)*100, ex.mean()/ex.std()*np.sqrt(252), dd.min()*100, dd.idxmin()

hold = pd.Series(re, index=days); hold.iloc[0]-=COST
p6040 = bh6040(); mix1 = unlev_mix(); L2 = lever_daily(2.0, 0.5)

print('=== БЛОК 1. Полное окно 03.1963–06.08.2026 (63,4 года) ===')
for nm,r in [('Держать акции',hold),('60/40 акц/обл',p6040),('Смесь Ф+Ф (1×)',mix1),('Фьючерсы 2× (спред 0,5)',L2)]:
    c,v,s,dd,ddt = met(r)
    print(f"{nm:26s} CAGR {c:6.2f}%  vol {v:5.2f}%  Шарп {s:5.2f}  MaxDD {dd:6.1f}% (дно {ddt.date()})")

print('\n=== Эпизоды, фьючерсы 2× ===')
eps=[('1969–70 (медведь+ставки)','1969-01-01','1970-12-31'),('1973–74 (нефть, обвал)','1973-01-01','1974-12-31'),
     ('1977–81 (инфляция, Волкер)','1977-01-01','1981-12-31'),('окт.1987','1987-10-01','1987-10-31'),
     ('1994 (шок ставок)','1994-01-01','1994-12-31'),('2008','2008-01-01','2008-12-31'),
     ('2015–16 (пила)','2015-06-01','2016-06-30'),('COVID фев–апр 2020','2020-02-15','2020-04-30'),('2022','2022-01-01','2022-12-31')]
for nm,a,b in eps:
    rr=L2[a:b]; tot=((1+rr).prod()-1)*100
    eqc=(1+rr).cumprod(); ddl=((eqc/eqc.cummax()-1).min())*100
    hh=((1+hold[a:b]).prod()-1)*100
    print(f"{nm:28s} 2×: {tot:+7.1f}% (DD внутри {ddl:6.1f}%)   акции: {hh:+7.1f}%")

# совместный обвал при включённых ногах
both_on = st_eq & st_bd
rm = pd.Series(np.where(both_on, L2.values, np.nan), index=days).dropna()
wm = (1+L2).resample('ME').prod()-1
on_month = pd.Series(both_on, index=days).resample('ME').first()
wm_on = wm[on_month.reindex(wm.index).fillna(False)]
worst5 = wm_on.nsmallest(5)
print('\nХудшие месяцы 2× при ОБЕИХ включённых ногах:')
for d,v in worst5.items(): print(f"  {d.date()}: {v*100:+.2f}%")

print('\n=== БЛОК 2. Спред финансирования, L=2 ===')
for s_ in [0.0,0.5,1.0,1.5]:
    r=lever_daily(2.0,s_)
    cF,_,_,ddF,_ = met(r)
    cM,_,_,ddM,_ = met(r,'2003-07-01','2026-08-06')
    print(f"спред {s_:.1f} п.п.: 1963–2026 CAGR {cF:5.2f}% (DD {ddF:5.1f}%)  |  2003–2026 CAGR {cM:5.2f}% (DD {ddM:5.1f}%)")

print('\n=== БЛОК 4. Сетка (дневной ребаланс, 5 б.п.): CAGR 2003–26 / DD 1963–2026 ===')
hdr='L\\спред |' + ''.join([f'   {s_:.1f} п.п.  ' for s_ in [0.5,1.0,1.5]])
print(hdr)
grid={}
for L in [1.5,1.75,2.0]:
    row=f"{L:4.2f}   |"
    for s_ in [0.5,1.0,1.5]:
        r=lever_daily(L,s_)
        cM,_,_,_,_ = met(r,'2003-07-01','2026-08-06'); _,_,_,ddF,_=met(r)
        grid[(L,s_,'D',5)]=(cM,ddF)
        row+=f" {cM:5.2f}/{ddF:5.1f}"
    print(row)
print('Месячный ребаланс плеча и издержки 10 б.п. (L=2, спред 0,5):')
rM=lever_monthly(2.0,0.5); c1,_,_,d1,_=met(rM,'2003-07-01','2026-08-06'); c1f,_,_,d1f,_=met(rM)
r10=lever_daily(2.0,0.5,cost=0.0010); c2,_,_,d2,_=met(r10,'2003-07-01','2026-08-06'); c2f,_,_,d2f,_=met(r10)
print(f"  ребаланс M: 2003–26 {c1:.2f}% / DD полн. {d1f:.1f}%   |   10 б.п.: {c2:.2f}% / DD {d2f:.1f}%")

pickle.dump({'days':days,'hold':hold,'p6040':p6040,'mix1':mix1,'L2':L2,'st_eq':st_eq,'st_bd':st_bd,
             'rfv':rfv}, open('engine.pkl','wb'))
