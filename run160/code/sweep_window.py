import pandas as pd, numpy as np
COST=0.0005
px = pd.read_csv('spy_daily.csv', parse_dates=['date'], index_col='date')['adj']
rfr = pd.read_csv('rfr_daily.csv', parse_dates=['Date'], index_col='Date')['Risk Free Rate']
ad = px.index
rf_a = rfr.reindex(ad).ffill().bfill(); rf_a[ad>rfr.index[-1]]=rfr.iloc[-1]
rf = (1+rf_a/100)**(1/252)-1
days = px.index[px.index>=pd.Timestamp('2003-07-01')]
ret = px.pct_change().reindex(days)

def run(N):
    me = px.resample('ME').last()
    sma = me.rolling(N).mean()
    sig = (me > sma)[sma.notna()]
    m2s = dict(zip((sig.index+pd.offsets.MonthBegin(1)).to_period('M'), sig.values))
    state = pd.Series([m2s[m] for m in days.to_period('M')], index=days).astype(bool)
    r = np.where(state.values, ret.values, rf.reindex(days).values)
    sw = state.ne(state.shift(1).fillna(state.iloc[0])).values
    cv = np.where(sw, COST, 0.0)
    if state.iloc[0]: cv[0]+=COST
    net = pd.Series((1+r)*(1-cv)-1, index=days)
    eq=(1+net).cumprod(); yrs=(days[-1]-days[0]).days/365.25
    ex=net-rf.reindex(days)
    return ((eq.iloc[-1]**(1/yrs)-1)*100, net.std()*np.sqrt(252)*100,
            ex.mean()/ex.std()*np.sqrt(252), (eq/eq.cummax()-1).min()*100,
            (sw.sum()+(1 if state.iloc[0] else 0))/yrs)

print(f"{'окно':>5} {'CAGR':>7} {'волат':>7} {'Шарп':>6} {'MaxDD':>8} {'перекл/год':>11}")
for N in [3,6,9,10,12,15,18,24]:
    c,v,s,dd,swy = run(N)
    print(f"{N:>3}м {c:7.2f} {v:7.2f} {s:6.2f} {dd:8.1f} {swy:11.1f}")
eq=(1+px.pct_change().reindex(days).fillna(0)).cumprod(); yrs=(days[-1]-days[0]).days/365.25
print(f"держать: CAGR {(eq.iloc[-1]**(1/yrs)-1)*100:.2f}, MaxDD {((eq/eq.cummax()-1).min())*100:.1f}")
