import pandas as pd, numpy as np, pickle

COST = 0.0005

def load(t):
    df = pd.read_csv(f'{t.lower()}_daily.csv', parse_dates=['date'], index_col='date')
    return df['adj']

px = {t: load(t) for t in ['SPY','TLT','IEF','GLD']}
rfr = pd.read_csv('rfr_daily.csv', parse_dates=['Date'], index_col='Date')['Risk Free Rate']
all_dates = px['SPY'].index
rf_annual = rfr.reindex(all_dates).ffill().bfill()
rf_annual[all_dates > rfr.index[-1]] = rfr.iloc[-1]
rf_daily = (1 + rf_annual/100)**(1/252) - 1

def month_end_signal(p):
    me = p.resample('ME').last()
    me = me[me.index >= p.index[0]]
    sma = me.rolling(12).mean()
    return (me > sma)[sma.notna()]

def filtered_strategy(p, start=None):
    sig = month_end_signal(p)
    days = p.index[p.index >= sig.index[0] + pd.offsets.MonthBegin(1)]
    if start is not None: days = days[days >= start]
    ret = p.pct_change().reindex(days)
    m2s = dict(zip((sig.index + pd.offsets.MonthBegin(1)).to_period('M'), sig.values))
    state = pd.Series([m2s[m] for m in days.to_period('M')], index=days).astype(bool)
    r = np.where(state.values, ret.values, rf_daily.reindex(days).values)
    switch = state.ne(state.shift(1).fillna(state.iloc[0])).values
    cost_vec = np.where(switch, COST, 0.0)
    if state.iloc[0]: cost_vec[0] += COST
    return pd.Series((1+r)*(1-cost_vec)-1, index=days), state, int(switch.sum() + (1 if state.iloc[0] else 0))

def bh(p, days):
    r = p.pct_change().reindex(days).fillna(0.0)
    r.iloc[0] -= COST
    return r

def mix(ret_dict, weights, days):
    keys = list(ret_dict)
    R = np.column_stack([ret_dict[k].reindex(days).fillna(0.0).values for k in keys])
    W = np.array([weights[k] for k in keys])
    months = days.to_period('M')
    starts = np.r_[0, np.flatnonzero(months[1:] != months[:-1]) + 1]
    V = W.copy(); out = np.empty(len(days))
    for j, s in enumerate(starts):
        e = starts[j+1] if j+1 < len(starts) else len(days)
        if j > 0:
            tot = V.sum(); turnover = np.abs(W*tot - V).sum()/tot
            V = W * tot * (1 - COST*turnover)
        G = np.cumprod(1 + R[s:e], axis=0)
        seg = G @ V
        out[s:e] = seg
        V = V * G[-1]
    eq = pd.Series(out, index=days)
    r = eq.pct_change(); r.iloc[0] = eq.iloc[0] - 1
    return r

def metrics(r, name):
    eq = (1+r).cumprod()
    yrs = (r.index[-1]-r.index[0]).days/365.25
    ex = r - rf_daily.reindex(r.index)
    return {'name':name,'cagr':(eq.iloc[-1]**(1/yrs)-1)*100,'vol':r.std()*np.sqrt(252)*100,
            'sharpe':ex.mean()/ex.std()*np.sqrt(252),'maxdd':(eq/eq.cummax()-1).min()*100,'r':r}

START_A = pd.Timestamp('2003-07-01'); START_B = pd.Timestamp('2005-11-01')
strat, states, switches = {}, {}, {}
for t in ['SPY','TLT','IEF','GLD']:
    strat[t], states[t], switches[t] = filtered_strategy(px[t], start=(START_A if t!='GLD' else START_B))

days_A = strat['SPY'].index.intersection(strat['TLT'].index)
days_B = days_A[days_A>=START_B].intersection(strat['GLD'].index)
yrsA = (days_A[-1]-days_A[0]).days/365.25
print('Окно A:', days_A[0].date(),'→',days_A[-1].date(), f'({yrsA:.1f} лет)')
print('Окно B:', days_B[0].date(),'→',days_B[-1].date())
print('Переключений/год:', {t: round(switches[t]/yrsA,2) for t in ['SPY','TLT','IEF']}, '| GLD:', round(switches['GLD']/((days_B[-1]-days_B[0]).days/365.25),2))

bh_r = {t: bh(px[t], days_A) for t in ['SPY','TLT','IEF']}
res = [metrics(bh_r['SPY'],'Держать SPY'), metrics(bh_r['TLT'],'Держать TLT'), metrics(bh_r['IEF'],'Держать IEF'),
       metrics(mix(bh_r,{'SPY':0.6,'TLT':0.4,'IEF':0.0},days_A),'60/40 SPY/TLT'),
       metrics(mix(bh_r,{'SPY':0.6,'TLT':0.0,'IEF':0.4},days_A),'60/40 SPY/IEF'),
       metrics(mix(bh_r,{'SPY':0.5,'TLT':0.5,'IEF':0.0},days_A),'50/50 держать SPY+TLT'),
       metrics(strat['SPY'].reindex(days_A),'Фильтр SPY'),
       metrics(strat['TLT'].reindex(days_A),'Фильтр TLT'),
       metrics(strat['IEF'].reindex(days_A),'Фильтр IEF'),
       metrics(mix({'SPY':strat['SPY'],'TLT':strat['TLT']},{'SPY':0.5,'TLT':0.5},days_A),'СМЕСЬ 50/50 Ф-SPY+Ф-TLT'),
       metrics(mix({'SPY':strat['SPY'],'IEF':strat['IEF']},{'SPY':0.5,'IEF':0.5},days_A),'СМЕСЬ 50/50 Ф-SPY+Ф-IEF')]

print('\n=== ОКНО A: 01.07.2003–06.08.2026 ===')
for m in res:
    print(f"{m['name']:28s} CAGR {m['cagr']:6.2f}%  vol {m['vol']:5.2f}%  Sharpe {m['sharpe']:5.2f}  MaxDD {m['maxdd']:7.2f}%")

bh_rB = {t: bh(px[t], days_B) for t in ['SPY','TLT','GLD']}
res_B = [metrics(bh_rB['GLD'],'Держать GLD'),
         metrics(strat['GLD'].reindex(days_B),'Фильтр GLD'),
         metrics(mix({'SPY':strat['SPY'],'TLT':strat['TLT']},{'SPY':0.5,'TLT':0.5},days_B),'СМЕСЬ 50/50 (окно B)'),
         metrics(mix({'SPY':strat['SPY'],'TLT':strat['TLT'],'GLD':strat['GLD']},{'SPY':1/3,'TLT':1/3,'GLD':1/3},days_B),'СМЕСЬ 1/3 Ф-SPY+TLT+GLD'),
         metrics(mix({'SPY':bh_rB['SPY'],'TLT':bh_rB['TLT']},{'SPY':0.6,'TLT':0.4},days_B),'60/40 SPY/TLT (окно B)')]
print('\n=== ОКНО B: 01.11.2005–06.08.2026 (с золотом) ===')
for m in res_B:
    print(f"{m['name']:28s} CAGR {m['cagr']:6.2f}%  vol {m['vol']:5.2f}%  Sharpe {m['sharpe']:5.2f}  MaxDD {m['maxdd']:7.2f}%")

pickle.dump({'res':res,'res_B':res_B,'states':states,'switches':switches,'strat':strat}, open('stage1.pkl','wb'))
