import pandas as pd, numpy as np
COST=0.0005

def build(px, rf, N, start):
    me = px.resample('ME').last().ffill()
    sma = me.rolling(N).mean(); sig=(me>sma)[sma.notna()]
    sig_m = pd.Series(sig.values, index=(sig.index+pd.offsets.MonthBegin(1)).to_period('M'))
    days = px.index[px.index >= max(start, (sig.index[0]+pd.offsets.MonthBegin(1)))]
    state = sig_m.reindex(days.to_period('M')).ffill().astype(bool); state.index=days
    ret = px.pct_change().reindex(days)
    r = np.where(state.values, ret.values, rf.reindex(days).values)
    sw = state.ne(state.shift(1).fillna(state.iloc[0])).values
    cv = np.where(sw, COST, 0.0)
    if state.iloc[0]: cv[0]+=COST
    return pd.Series((1+r)*(1-cv)-1, index=days), sig_m

def stats(rr, rf):
    eq=(1+rr).cumprod(); yrs=(rr.index[-1]-rr.index[0]).days/365.25
    ex=rr-rf.reindex(rr.index)
    return ((eq.iloc[-1]**(1/yrs)-1)*100, (eq/eq.cummax()-1).min()*100, ex.mean()/ex.std()*np.sqrt(252))

# --- SPY 2003–2026 ---
spy = pd.read_csv('spy_daily.csv', parse_dates=['date'], index_col='date')['adj']
rfr = pd.read_csv('rfr_daily.csv', parse_dates=['Date'], index_col='Date')['Risk Free Rate']
rf_a = rfr.reindex(spy.index).ffill().bfill(); rf_a[spy.index>rfr.index[-1]]=rfr.iloc[-1]
rf_spy = (1+rf_a/100)**(1/252)-1
print('=== SPY, старт 01.07.2003 ===')
for N in [11,12]:
    f,_ = build(spy, rf_spy, N, pd.Timestamp('2003-07-01'))
    c,dd,s = stats(f, rf_spy); c2,dd2,s2 = stats(f['2008-01-01':], rf_spy)
    print(f"{N}м: полный CAGR {c:.2f}% DD {dd:.1f}% Шарп {s:.2f}  |  2008–2026: CAGR {c2:.2f}% DD {dd2:.1f}% Шарп {s2:.2f}")

# --- 140 лет ---
mk = pd.read_csv('us-market-daily-1885-2025.csv', parse_dates=['Date']).set_index('Date').sort_index()
px = mk['Adjusted Close']; rf140 = (1+mk['Risk Free Rate']/100)**(1/252)-1
print('\n=== 140-летний ряд ===')
for N in [11,12]:
    f,_ = build(px, rf140, N, px.index[0])
    c,dd,s = stats(f, rf140); c2,dd2,s2 = stats(f['2007-01-01':], rf140)
    print(f"{N}м: 1886–2025 CAGR {c:.2f}% DD {dd:.1f}% Шарп {s:.2f}  |  после публикации 2007–2025: CAGR {c2:.2f}% DD {dd2:.1f}% Шарп {s2:.2f}")

# --- Совпадение сигналов и «опережение» на переходах (SPY) ---
_, s11 = build(spy, rf_spy, 11, pd.Timestamp('1994-02-01'))
_, s12 = build(spy, rf_spy, 12, pd.Timestamp('1994-02-01'))
idx = s11.index.intersection(s12.index)
a, b = s11.reindex(idx), s12.reindex(idx)
agree = (a==b).mean()*100
# переходы 12м: опередил ли 11м
ch = b.ne(b.shift(1)).fillna(False)
lead = same = late = 0
for i,(m,changed) in enumerate(ch.items()):
    if not changed or i==0: continue
    new = b.loc[m]
    prev_m = idx[i-1]
    if a.loc[prev_m]==new and a.loc[idx[i-2]]!=new if i>=2 else False:
        lead+=1
    elif a.loc[m]==new:
        same+=1
    else:
        late+=1
print(f"\nСигналы 11м и 12м совпадают в {agree:.1f}% месяцев ({len(idx)} мес, 1994–2026)")
print(f"Переходы 12м ({int(ch.sum())} шт): 11м раньше на ≥1 мес: {lead}, тем же месяцем: {same}, позже: {late}")
