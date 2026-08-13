import pandas as pd, numpy as np
# --- дневная нога акций 1928–1935: цена ^GSPC + дивидендная доходность Шиллера, размазанная по дням ---
px = pd.read_csv('gspc_1928_1935.csv', parse_dates=['date'], index_col='date')['close']
px = px['1927-12-30':'1935-12-31']
sh = pd.read_csv('shiller-monthly-1871-2026.csv', parse_dates=['Date']).set_index('Date').sort_index()
dy_m = (sh['Dividend']/sh['SP500'])  # годовая див.доходность
dy_d = dy_m.reindex(px.index, method='ffill')/252
eq_r = px.pct_change() + dy_d
eq_r = eq_r.dropna()
eq_idx = (1+eq_r).cumprod()

# --- нога облигаций: месячная реконструкция из GS10, равномерно в дни месяца (волатильность занижена — оговорка) ---
y10 = sh['Long Interest Rate']/100
def dc(yv):
    c=yv/2; n=20; t=np.arange(1,n+1)
    cf=np.full(n,c); cf[-1]+=1.0; disc=(1+c)**(-t); pv=cf*disc; P=pv.sum()
    return (t*pv).sum()/P/2/(1+c), (t*(t+1)*pv).sum()/P/(1+c)**2/4
D=[];C=[]
for v in y10.values:
    a,b=dc(v); D.append(a); C.append(b)
D=pd.Series(D,index=y10.index); C=pd.Series(C,index=y10.index)
dy=y10.diff()
bdm=(y10.shift(1)/12 - D.shift(1)*dy + 0.5*C.shift(1)*dy**2).dropna()
mdays = eq_r.groupby(eq_r.index.to_period('M')).size()
bd_d = pd.Series(index=eq_r.index, dtype=float)
for p,n in mdays.items():
    v = bdm.reindex(pd.PeriodIndex([p],freq='M').to_timestamp()).values
    mv = bdm[bdm.index.to_period('M')==p]
    val = mv.iloc[0] if len(mv) else 0.0
    bd_d[eq_r.index.to_period('M')==p] = (1+val)**(1/n)-1
bd_idx=(1+bd_d).cumprod()

# --- деньги ---
mk = pd.read_csv('us-market-daily-1885-2025.csv', parse_dates=['Date']).set_index('Date').sort_index()
rf_d = ((1+mk['Risk Free Rate']/100)**(1/252)-1).reindex(eq_r.index).ffill()

# --- сигналы SMA12 по НАСТОЯЩИМ месячным закрытиям (не средним) ---
def sig(idx_s):
    me=idx_s.resample('ME').last().ffill(); sma=me.rolling(12).mean(); s=(me>sma)[sma.notna()]
    return pd.Series(s.values, index=(s.index+pd.offsets.MonthBegin(1)).to_period('M'))
# для SMA нужна предыстория с 1927 — есть с 1928; первые сигналы с начала 1929. Для полноты добавим 1928 из Шиллера в индекс? 
# Начало данных 12.1927 → первый сигнал на конец 12.1928 → позиции с 01.1929. Депрессию покрывает.
se_m=sig(eq_idx); sb_m=sig(bd_idx)
days = eq_r.index[eq_r.index>=pd.Timestamp('1929-01-02')]
se = se_m.reindex(days.to_period('M')).ffill().astype(bool).values
sb = sb_m.reindex(days.to_period('M')).ffill().astype(bool).values
re=eq_r.reindex(days).values; rb=bd_d.reindex(days).values; rfv=rf_d.reindex(days).values
sd=0.5/100/252
print('=== Депрессия на ДНЕВНЫХ данных (акции — дневные, облигации — месячные равномерно, издержки 5 б.п.) ===')
for L in [1.0,1.5,1.75,2.0]:
    Ee=L*0.5*se; Eb=L*0.5*sb
    r = rfv + Ee*(re-rfv-sd) + Eb*(rb-rfv-sd)
    dE=np.abs(np.diff(Ee,prepend=0))+np.abs(np.diff(Eb,prepend=0))
    r = pd.Series(r-0.0005*dE, index=days)
    w=r['1929-01-02':'1934-12-31']; e=(1+w).cumprod()
    dd=(e/e.cummax()-1).min()*100
    print(f"L={L:.2f}: 1929–34 итог {((e.iloc[-1]-1)*100):+7.1f}%, MaxDD {dd:6.1f}%")
hh=eq_r['1929-01-02':'1934-12-31']; he=(1+hh).cumprod()
print(f"акции (дневн., с дивид.): итог {((he.iloc[-1]-1)*100):+.1f}%, MaxDD {((he/he.cummax()-1).min())*100:.1f}%")
# состояния ноги акций в ключевые месяцы
tl = pd.Series(se, index=days).groupby(days.to_period('M')).first()
print('\nнога акций по месяцам 1929: ', {str(p): int(v) for p,v in tl['1929'].items()})
