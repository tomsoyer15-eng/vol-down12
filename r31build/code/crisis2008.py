import pandas as pd, numpy as np, pickle
d1=pickle.load(open('stage1.pkl','rb')); d3=pickle.load(open('stage3.pkl','rb'))
mixI=d3['mix5050I']; states=d1['states']
rfr = pd.read_csv('rfr_daily.csv', parse_dates=['Date'], index_col='Date')['Risk Free Rate']
ad=mixI.index; rf_a=rfr.reindex(ad).ffill().bfill(); rf_a[ad>rfr.index[-1]]=rfr.iloc[-1]
borrow=(1+(rf_a+1.0)/100)**(1/252)-1
r = 2.0*mixI - 1.0*borrow
eq=(1+r).cumprod()

s08 = r['2008-01-01':'2009-06-30']
eq08=(1+s08).cumprod()
print('=== 2× смесь Ф-SPY+Ф-IEF в кризис 2008 ===')
print(f"итог 2008 года: {(((1+r['2008']).prod())-1)*100:+.1f}%")
print(f"итог сент–ноя 2008 (ядро паники): {(((1+r['2008-09-01':'2008-11-30']).prod())-1)*100:+.1f}%")
print(f"худший день 2008: {s08['2008'].min()*100:.2f}% ({s08['2008'].idxmin().date()})")
wm=(1+r['2008']).resample('ME').prod()-1
print(f"худший месяц 2008: {wm.min()*100:.2f}% ({wm.idxmin().date()})")
dd08=(eq08/eq08.cummax()-1)
print(f"макс. просадка внутри 01.2008–06.2009: {dd08.min()*100:.1f}%")

# состояния фильтров вокруг 2008
tl={t: states[t].groupby(states[t].index.to_period('M')).first() for t in ['SPY','IEF']}
per=pd.period_range('2007-10','2009-09',freq='M')
print('\nмесяц | SPY | IEF (1=в активе)')
for p in per:
    print(f"{p} |  {int(tl['SPY'].get(p,0))}  |  {int(tl['IEF'].get(p,0))}")

# где вообще худшая просадка 2× за 23 года
dd=(eq/eq.cummax()-1)
print(f"\nглобальная просадка 2×: {dd.min()*100:.1f}%, дно {dd.idxmin().date()}")
# худшая для 2x неделя за 23 года
ww=(1+r).resample('W').prod()-1
print(f"худшая неделя за 23 года: {ww.min()*100:.2f}% ({ww.idxmin().date()})")
