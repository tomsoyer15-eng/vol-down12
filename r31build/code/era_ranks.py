import pandas as pd, numpy as np
from scipy.stats import spearmanr
COST=0.0005
mk = pd.read_csv('us-market-daily-1885-2025.csv', parse_dates=['Date']).set_index('Date').sort_index()
px = mk['Adjusted Close']; rf=(1+mk['Risk Free Rate']/100)**(1/252)-1

def monthly(N):
    me=px.resample('ME').last().ffill(); sma=me.rolling(N).mean(); sig=(me>sma)[sma.notna()]
    sm=pd.Series(sig.values, index=(sig.index+pd.offsets.MonthBegin(1)).to_period('M'))
    days=px.index[px.index>=sig.index[0]+pd.offsets.MonthBegin(1)]
    st=sm.reindex(days.to_period('M')).ffill().astype(bool); st.index=days
    return st, days

def daily(mode):
    if mode=='sma200':
        line=px.rolling(200).mean()
    else:
        n=11 if mode=='fly' else 12
        me=px.resample('ME').last().ffill(); l=me.rolling(n).mean()
        lm=pd.Series(l.values, index=(l.index+pd.offsets.MonthBegin(1)).to_period('M'))
        line=pd.Series(lm.reindex(px.index.to_period('M')).values, index=px.index)
    raw=(px>line); st=raw.shift(1)
    days=px.index[st.notna().values & line.notna().values]
    return st.reindex(days).astype(bool), days

def strat(st, days):
    ret=px.pct_change().reindex(days)
    r=np.where(st.values, ret.values, rf.reindex(days).values)
    sw=st.ne(st.shift(1).fillna(st.iloc[0])).values
    cv=np.where(sw,COST,0.0)
    if st.iloc[0]: cv[0]+=COST
    return pd.Series((1+r)*(1-cv)-1, index=days)

def cagr(rr,a,b):
    s=rr[a:b]; 
    if len(s)<100: return np.nan
    eq=(1+s).cumprod(); yrs=(s.index[-1]-s.index[0]).days/365.25
    return (eq.iloc[-1]**(1/yrs)-1)*100
def mdd(rr,a,b):
    s=rr[a:b]; eq=(1+s).cumprod(); return (eq/eq.cummax()-1).min()*100

variants={}
for N in [3,6,9,10,12,15,18,24]:
    st,days=monthly(N); variants[f'{N}м мес']=strat(st,days)
for m,nm in [('vs12','ежедн-12'),('fly','ежедн-налету'),('sma200','200-дневка')]:
    st,days=daily(m); variants[nm]=strat(st,days)

eras=[('1947–1966','1947-01-01','1966-12-31'),('1967–1986','1967-01-01','1986-12-31'),
      ('1987–2006','1987-01-01','2006-12-31'),('2007–2025','2007-01-01','2025-12-19')]
hold=px.pct_change().dropna()

tab=pd.DataFrame({nm:{e[0]:cagr(rr,e[1],e[2]) for e in eras} for nm,rr in variants.items()}).T
tab['DD 2007–25']=[mdd(variants[nm],'2007-01-01','2025-12-19') for nm in tab.index]
print(tab.round(2).to_string())
print('\nДержание по эпохам:', {e[0]:round(cagr(hold,e[1],e[2]),2) for e in eras}, 'DD 2007–25:', round(mdd(hold,'2007-01-01','2025-12-19'),1))

print('\nРанговая корреляция «лучшие прошлой эпохи → лучшие следующей» (Спирмен):')
for i in range(len(eras)-1):
    r,_=spearmanr(tab[eras[i][0]], tab[eras[i+1][0]])
    print(f"{eras[i][0]} → {eras[i+1][0]}: {r:+.2f}")

r,_=spearmanr(tab['2007–2025'], tab['DD 2007–25'])
print(f"\n2007–2025: корреляция CAGR ↔ глубина просадки по вариантам: {r:+.2f}")
best=tab['2007–2025'].idxmax()
print(f"«Лучший» вариант 2007–2025: {best} ({tab.loc[best,'2007–2025']:.2f}%, DD {tab.loc[best,'DD 2007–25']:.1f}%)")
