import pandas as pd, numpy as np
COST=0.0005
mk = pd.read_csv('us-market-daily-1885-2025.csv', parse_dates=['Date']).set_index('Date').sort_index()
px = mk['Adjusted Close']; rf=(1+mk['Risk Free Rate']/100)**(1/252)-1
cpi = mk['CPI'].resample('ME').last().ffill()

# дневные ряды стратегий -> месячные
me = px.resample('ME').last().ffill(); sma=me.rolling(12).mean(); sig=(me>sma)[sma.notna()]
sm = pd.Series(sig.values, index=(sig.index+pd.offsets.MonthBegin(1)).to_period('M'))
days = px.index[px.index>=sig.index[0]+pd.offsets.MonthBegin(1)]
st = sm.reindex(days.to_period('M')).ffill().astype(bool); st.index=days
ret = px.pct_change().reindex(days)
r = np.where(st.values, ret.values, rf.reindex(days).values)
sw = st.ne(st.shift(1).fillna(st.iloc[0])).values
cv = np.where(sw,COST,0.0); cv[0]+= COST if st.iloc[0] else 0
f_d = pd.Series((1+r)*(1-cv)-1, index=days)
h_d = ret.copy(); h_d.iloc[0]-=COST

fm = (1+f_d).resample('ME').prod()-1
hm = (1+h_d).resample('ME').prod()-1
idx = fm.index.intersection(hm.index).intersection(cpi.index)
fm, hm, c = fm.reindex(idx), hm.reindex(idx), cpi.reindex(idx)

def sim(rm, w_annual, horizon_m=360):
    fails=0; ends=[]; mins=[]
    n=len(rm)
    for s in range(0, n-horizon_m):
        V=1.0; vmin=1.0; dead=False
        base_cpi=c.iloc[s]
        for t in range(s, s+horizon_m):
            wd = w_annual/12 * (c.iloc[t]/base_cpi)
            V = (V - wd)*(1+rm.iloc[t])
            real = V * base_cpi/c.iloc[t]
            vmin=min(vmin, real)
            if V<=0: dead=True; break
        if dead: fails+=1; ends.append(0.0); mins.append(0.0)
        else: ends.append(real); mins.append(vmin)
    ends=np.array(ends); mins=np.array(mins)
    return fails/(n-horizon_m)*100, np.percentile(ends,5), np.median(ends), np.percentile(mins,5)

print('30-летние окна, изъятие — % от СТАРТОВОГО капитала, индексируется инфляцией, помесячно')
print(f"{'изъятие':>8} | {'провалов держать':>17} | {'провалов Ф-SMA12':>17} | {'5% худших итогов (реально): держать / фильтр':>44}")
for w in [0.02,0.03,0.04,0.05]:
    fh,e5h,emh,m5h = sim(hm,w)
    ff,e5f,emf,m5f = sim(fm,w)
    print(f"{w*100:6.0f}%  | {fh:16.1f}% | {ff:16.1f}% |  {e5h:5.2f} / {e5f:5.2f}   (медианы {emh:.1f} / {emf:.1f})")
