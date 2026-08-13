import pandas as pd, numpy as np, pickle
d3 = pickle.load(open('stage3.pkl','rb')); mixI = d3['mix5050I']; mixT = d3['mix5050T']
rfr = pd.read_csv('rfr_daily.csv', parse_dates=['Date'], index_col='Date')['Risk Free Rate']
ad = mixI.index
rf_annual = rfr.reindex(ad).ffill().bfill(); rf_annual[ad>rfr.index[-1]] = rfr.iloc[-1]
rf = (1+rf_annual/100)**(1/252)-1
borrow = (1+(rf_annual+1.0)/100)**(1/252)-1   # ставка займа = безрисковая + 1 п.п.

def met(r):
    eq=(1+r).cumprod(); yrs=(r.index[-1]-r.index[0]).days/365.25
    ex=r-rf
    return ((eq.iloc[-1]**(1/yrs)-1)*100, r.std()*np.sqrt(252)*100, ex.mean()/ex.std()*np.sqrt(252), (eq/eq.cummax()-1).min()*100)
def y22(r):
    rr=r[r.index.year==2022]; eq=(1+rr).cumprod()
    return ((1+rr).prod()-1)*100, (eq/eq.cummax()-1).min()*100

print('=== ИЛЛЮСТРАЦИЯ: плечо на смеси 50/50 Ф-SPY+Ф-IEF, займ = безрисковая+1 п.п., ребаланс плеча ежедневный ===')
for L in [1.0,1.25,1.5,1.75,2.0]:
    r = L*mixI - (L-1)*borrow
    c,v,s,dd = met(r); r22,dd22 = y22(r)
    print(f"L={L:.2f}:  CAGR {c:5.2f}%  vol {v:5.2f}%  Sharpe {s:4.2f}  MaxDD {dd:7.2f}%  | 2022: {r22:+6.2f}% (DD {dd22:6.2f}%)")
print()
print('=== То же на смеси 50/50 Ф-SPY+Ф-TLT ===')
for L in [1.5]:
    r = L*mixT - (L-1)*borrow
    c,v,s,dd = met(r); r22,dd22 = y22(r)
    print(f"L={L:.2f}:  CAGR {c:5.2f}%  vol {v:5.2f}%  Sharpe {s:4.2f}  MaxDD {dd:7.2f}%  | 2022: {r22:+6.2f}% (DD {dd22:6.2f}%)")
