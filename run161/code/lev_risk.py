import pandas as pd, numpy as np, pickle
d3 = pickle.load(open('stage3.pkl','rb')); mixI = d3['mix5050I']
rfr = pd.read_csv('rfr_daily.csv', parse_dates=['Date'], index_col='Date')['Risk Free Rate']
ad = mixI.index
rf_a = rfr.reindex(ad).ffill().bfill(); rf_a[ad>rfr.index[-1]]=rfr.iloc[-1]
borrow=(1+(rf_a+1.0)/100)**(1/252)-1
L=2.0
r = L*mixI - (L-1)*borrow
eq=(1+r).cumprod()

print(f"Плечо 2× на смеси Ф-SPY+Ф-IEF, 2003–2026:")
print(f"худший день: {r.min()*100:.2f}%  ({r.idxmin().date()})")
wm=(1+r).resample('ME').prod()-1
print(f"худший месяц: {wm.min()*100:.2f}%  ({wm.idxmin().date()})")
print(f"макс. просадка: {((eq/eq.cummax()-1).min())*100:.1f}%")

# маржинальная механика: активы = 2×капитал, долг = 1×старт капитала сегмента (ежедневный ребаланс плеча)
# при дневном ребалансе доля собственных средств каждый день = 50%; риск — внутридневной гэп.
# критично: дневное падение СМЕСИ на -50% = обнуление. худший день смеси:
print(f"\nхудший день самой смеси (без плеча): {mixI.min()*100:.2f}%")
print(f"до обнуления при 2× нужен день смеси −50%, т.е. в {abs(50/ (mixI.min()*100)):.0f} раз хуже худшего за 23 года")

# изъятия 2%/год из 2× смеси, путь 2003–2026 (один путь, не 140 лет!)
V=1.0; vmin=1.0
wd_m = 0.02/12
rm=(1+r).resample('ME').prod()-1
for x in rm.values:
    V=(V-wd_m)*(1+x); vmin=min(vmin,V)
print(f"\nизъятие 2%/год из 2×-смеси, путь 2003–2026: минимум капитала {vmin:.2f}, итог {V:.2f} (старт 1.00)")
