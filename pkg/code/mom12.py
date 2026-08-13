import pandas as pd, numpy as np
COST=0.0005
mk = pd.read_csv('us-market-daily-1885-2025.csv', parse_dates=['Date']).set_index('Date').sort_index()
px = mk['Adjusted Close']; rf=(1+mk['Risk Free Rate']/100)**(1/252)-1

def build_state(rule):
    me = px.resample('ME').last().ffill()
    if rule=='sma':  sig = (me > me.rolling(12).mean()).where(me.rolling(12).mean().notna())
    else:            sig = (me > me.shift(12)).where(me.shift(12).notna())
    sig = sig.dropna().astype(bool)
    sm = pd.Series(sig.values, index=(sig.index+pd.offsets.MonthBegin(1)).to_period('M'))
    days = px.index[px.index >= sig.index[0]+pd.offsets.MonthBegin(1)]
    st = sm.reindex(days.to_period('M')).ffill().astype(bool); st.index=days
    return st, days

def strat(st, days):
    ret=px.pct_change().reindex(days)
    r=np.where(st.values, ret.values, rf.reindex(days).values)
    sw=st.ne(st.shift(1).fillna(st.iloc[0])).values
    cv=np.where(sw,COST,0.0)
    if st.iloc[0]: cv[0]+=COST
    return pd.Series((1+r)*(1-cv)-1, index=days), sw.sum()

def cagr(rr,a,b):
    s=rr[a:b]; eq=(1+s).cumprod(); yrs=(s.index[-1]-s.index[0]).days/365.25
    return (eq.iloc[-1]**(1/yrs)-1)*100
def mdd(rr,a,b):
    s=rr[a:b]; eq=(1+s).cumprod(); return (eq/eq.cummax()-1).min()*100

st_s, d_s = build_state('sma'); st_m, d_m = build_state('mom')
common = d_s.intersection(d_m)
f_s,_ = strat(st_s.reindex(common), common); f_m,_ = strat(st_m.reindex(common), common)
hold = px.pct_change().reindex(common).fillna(0)
agree = (st_s.reindex(common)==st_m.reindex(common)).mean()*100
print(f'Совпадение состояний SMA12 и MOM12: {agree:.1f}% дней ({common[0].date()}–{common[-1].date()})')

eras=[('1947–1966','1947-01-01','1966-12-31'),('1967–1986','1967-01-01','1986-12-31'),
      ('1987–2006','1987-01-01','2006-12-31'),('2007–2025','2007-01-01','2025-12-19')]
print(f"\n{'эпоха':>10} | {'держать':>8} | {'SMA12 (наша)':>12} | {'MOM12 (их)':>11}")
for nm,a,b in eras:
    print(f"{nm:>10} | {cagr(hold,a,b):7.2f}% | {cagr(f_s,a,b):11.2f}% | {cagr(f_m,a,b):10.2f}%")
print(f"\nDD 2007–2025: держать {mdd(hold,'2007-01-01','2025-12-19'):.1f}%, SMA12 {mdd(f_s,'2007-01-01','2025-12-19'):.1f}%, MOM12 {mdd(f_m,'2007-01-01','2025-12-19'):.1f}%")

# эпизоды
for nm,a,b in [('COVID фев–апр 2020','2020-02-15','2020-04-30'),('2020 год','2020-01-01','2020-12-31'),
               ('окт.1987','1987-10-01','1987-10-31'),('2022 год','2022-01-01','2022-12-31')]:
    hs=((1+hold[a:b]).prod()-1)*100; ss=((1+f_s[a:b]).prod()-1)*100; ms=((1+f_m[a:b]).prod()-1)*100
    print(f"{nm:20s} держать {hs:+6.1f}%  SMA12 {ss:+6.1f}%  MOM12 {ms:+6.1f}%")

# полный период и после Фабера-2007 / TSMOM-2012
print(f"\nПолный {common[0].year}–2025: SMA12 {cagr(f_s,'1886-01-01','2025-12-19'):.2f}% (DD {mdd(f_s,'1886-01-01','2025-12-19'):.1f}%), MOM12 {cagr(f_m,'1886-01-01','2025-12-19'):.2f}% (DD {mdd(f_m,'1886-01-01','2025-12-19'):.1f}%)")
print(f"2013–2025 (после публикации TSMOM-2012): держать {cagr(hold,'2013-01-01','2025-12-19'):.2f}%, SMA12 {cagr(f_s,'2013-01-01','2025-12-19'):.2f}%, MOM12 {cagr(f_m,'2013-01-01','2025-12-19'):.2f}%")
