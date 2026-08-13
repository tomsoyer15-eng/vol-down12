import pandas as pd, numpy as np, pickle
d1 = pickle.load(open('stage1.pkl','rb')); strat = d1['strat']; res = d1['res']
COST=0.0005

def load(t): return pd.read_csv(f'{t.lower()}_daily.csv', parse_dates=['date'], index_col='date')['adj']
px = {t: load(t) for t in ['SPY','TLT','IEF','GLD']}
rfr = pd.read_csv('rfr_daily.csv', parse_dates=['Date'], index_col='Date')['Risk Free Rate']
ad = px['SPY'].index
rf_annual = rfr.reindex(ad).ffill().bfill(); rf_annual[ad>rfr.index[-1]] = rfr.iloc[-1]
rf_daily = (1+rf_annual/100)**(1/252)-1

def mix(ret_dict, weights, days):
    keys=list(ret_dict); R=np.column_stack([ret_dict[k].reindex(days).fillna(0.0).values for k in keys])
    W=np.array([weights[k] for k in keys]); months=days.to_period('M')
    starts=np.r_[0,np.flatnonzero(months[1:]!=months[:-1])+1]; V=W.copy(); out=np.empty(len(days))
    for j,s in enumerate(starts):
        e=starts[j+1] if j+1<len(starts) else len(days)
        if j>0:
            tot=V.sum(); tu=np.abs(W*tot-V).sum()/tot; V=W*tot*(1-COST*tu)
        G=np.cumprod(1+R[s:e],axis=0); out[s:e]=G@V; V=V*G[-1]
    eq=pd.Series(out,index=days); r=eq.pct_change(); r.iloc[0]=eq.iloc[0]-1; return r

def met(r):
    eq=(1+r).cumprod(); yrs=(r.index[-1]-r.index[0]).days/365.25
    ex=r-rf_daily.reindex(r.index)
    return ((eq.iloc[-1]**(1/yrs)-1)*100, r.std()*np.sqrt(252)*100, ex.mean()/ex.std()*np.sqrt(252), (eq/eq.cummax()-1).min()*100)

days_A = strat['SPY'].index.intersection(strat['TLT'].index)

print('=== Развёртка по весу акций (Ф-SPY + Ф-облигации), окно A — ДИАГНОСТИКА, не выбор ===')
sweep=[]
for bond in ['TLT','IEF']:
    for w in [0.5,0.6,0.7,0.8,0.9,1.0]:
        r = mix({'SPY':strat['SPY'],bond:strat[bond]},{'SPY':w,bond:1-w},days_A)
        c,v,s,dd = met(r); sweep.append((bond,w,c,v,s,dd))
        print(f"Ф-SPY {int(w*100)}% + Ф-{bond} {int((1-w)*100)}%:  CAGR {c:5.2f}%  vol {v:5.2f}%  Sharpe {s:4.2f}  MaxDD {dd:7.2f}%")

print('\n=== 2008 год ===')
def yr_ret(r,y):
    rr=r[r.index.year==y]; return ((1+rr).prod()-1)*100
def dd_in(r,y):
    rr=r[r.index.year==y]; eq=(1+rr).cumprod(); return (eq/eq.cummax()-1).min()*100
mix5050T = mix({'SPY':strat['SPY'],'TLT':strat['TLT']},{'SPY':0.5,'TLT':0.5},days_A)
mix5050I = mix({'SPY':strat['SPY'],'IEF':strat['IEF']},{'SPY':0.5,'IEF':0.5},days_A)
for nm,r in [('Держать SPY',res[0]['r']),('60/40 SPY/TLT',res[3]['r']),('Ф-SPY',res[6]['r']),
             ('СМЕСЬ 50/50 Ф-SPY+Ф-TLT',mix5050T),('СМЕСЬ 50/50 Ф-SPY+Ф-IEF',mix5050I)]:
    print(f"{nm:26s} 2008: {yr_ret(r,2008):+7.2f}%  DD {dd_in(r,2008):7.2f}%")

print('\n=== Скользящий 5-летний CAGR (мин / медиана / доля окон ≥10%) ===')
def roll5(r):
    eq=(1+r).cumprod(); n=252*5
    rr=(eq/eq.shift(n))**(1/5)-1; rr=rr.dropna()
    return rr.min()*100, rr.median()*100, (rr>=0.10).mean()*100
for nm,r in [('Держать SPY',res[0]['r']),('60/40 SPY/TLT',res[3]['r']),('Ф-SPY',res[6]['r']),
             ('СМЕСЬ 50/50 Ф-SPY+Ф-TLT',mix5050T),('СМЕСЬ 50/50 Ф-SPY+Ф-IEF',mix5050I)]:
    mn,md,sh = roll5(r); print(f"{nm:26s} min {mn:+6.2f}%  медиана {md:5.2f}%  ≥10%: {sh:4.1f}% окон")

print('\n=== Годовые доходности главных вариантов ===')
tab = pd.DataFrame({'SPY':(1+res[0]['r']).groupby(res[0]['r'].index.year).prod()-1,
                    '60/40':(1+res[3]['r']).groupby(res[3]['r'].index.year).prod()-1,
                    'Ф-SPY':(1+res[6]['r']).groupby(res[6]['r'].index.year).prod()-1,
                    'СМЕСЬ_TLT':(1+mix5050T).groupby(mix5050T.index.year).prod()-1,
                    'СМЕСЬ_IEF':(1+mix5050I).groupby(mix5050I.index.year).prod()-1})
print((tab*100).round(1).to_string())
tab.to_csv('yearly_returns.csv')
pickle.dump({'sweep':sweep,'mix5050T':mix5050T,'mix5050I':mix5050I,'tab':tab},open('stage3.pkl','wb'))
