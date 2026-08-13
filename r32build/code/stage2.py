import pandas as pd, numpy as np, pickle
d = pickle.load(open('stage1.pkl','rb'))
res, res_B, states, strat = d['res'], d['res_B'], d['states'], d['strat']

def yearly(r):
    return (1+r).groupby(r.index.year).prod()-1

def dd_in(r, y):
    rr = r[r.index.year==y]; eq=(1+rr).cumprod()
    return (eq/eq.cummax()-1).min()

names_A = ['Держать SPY','Держать TLT','Держать IEF','60/40 SPY/TLT','60/40 SPY/IEF',
           '50/50 держать SPY+TLT','Фильтр SPY','Фильтр TLT','Фильтр IEF',
           'СМЕСЬ 50/50 Ф-SPY+Ф-TLT','СМЕСЬ 50/50 Ф-SPY+Ф-IEF']
print('=== 2022 год (календарный): доходность / внутригодовая просадка ===')
rows2022=[]
for m in res:
    yr = yearly(m['r']); v = yr.loc[2022]*100; ddv = dd_in(m['r'],2022)*100
    rows2022.append((m['name'], v, ddv))
    print(f"{m['name']:28s} {v:+7.2f}%   DD {ddv:7.2f}%")
m3 = [m for m in res_B if '1/3' in m['name']][0]
v = yearly(m3['r']).loc[2022]*100; ddv = dd_in(m3['r'],2022)*100
rows2022.append((m3['name'], v, ddv))
print(f"{m3['name']:28s} {v:+7.2f}%   DD {ddv:7.2f}%")

print('\n=== Состояния фильтра (1=в активе, 0=в деньгах), месяц ПОЗИЦИИ ===')
tl = {}
for t in ['SPY','TLT','IEF','GLD']:
    s = states[t]; ms = s.groupby(s.index.to_period('M')).first()
    tl[t] = ms
per = pd.period_range('2021-06','2023-06',freq='M')
hdr = 'месяц   ' + '  '.join(f'{t:>3s}' for t in ['SPY','TLT','IEF','GLD'])
print(hdr)
for p in per:
    print(f"{p}  " + '  '.join(f"{int(tl[t].get(p, np.nan)) if p in tl[t].index else '-':>3}" for t in ['SPY','TLT','IEF','GLD']))

# когда TLT-фильтр ушёл в деньги перед 2022
s = tl['TLT']; pre = s[(s.index>=pd.Period('2020-01'))&(s.index<=pd.Period('2023-12'))]
print('\nTLT: месяцы в деньгах 2020–2023:', [str(p) for p,v in pre.items() if not v][:20])

# === Корреляции дневных доходностей ===
def load(t):
    return pd.read_csv(f'{t.lower()}_daily.csv', parse_dates=['date'], index_col='date')['adj']
px = {t: load(t) for t in ['SPY','TLT','IEF','GLD']}
rets = pd.DataFrame({t: px[t].pct_change() for t in ['SPY','TLT','IEF','GLD']}).dropna()
print('\n=== Корреляции ДНЕВНЫХ доходностей, общий период', rets.index[0].date(),'–',rets.index[-1].date(),'===')
print(rets.corr().round(3).to_string())
mret = pd.DataFrame({t: px[t].resample('ME').last().pct_change() for t in ['SPY','TLT','IEF','GLD']}).dropna()
print('\n=== Корреляции МЕСЯЧНЫХ доходностей, тот же период ===')
print(mret.corr().round(3).to_string())

print('\n=== SPY–TLT дневная корреляция по годам ===')
byy = rets.groupby(rets.index.year).apply(lambda x: x['SPY'].corr(x['TLT']))
for y,v in byy.items(): print(f"{y}: {v:+.2f}")

pickle.dump({'rows2022':rows2022,'tl':tl,'byy':byy,'rets':rets,'mret':mret}, open('stage2.pkl','wb'))
