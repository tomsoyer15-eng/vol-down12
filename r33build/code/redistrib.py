import pandas as pd, numpy as np, pickle
d=pickle.load(open('engine.pkl','rb'))
days,st_eq,st_bd,rfv = d['days'],d['st_eq'],d['st_bd'],d['rfv']
# восстановление ног
mk = pd.read_csv('us-market-daily-1885-2025.csv', parse_dates=['Date']).set_index('Date').sort_index()
eq_r = mk['Adjusted Close'].pct_change()
spy = pd.read_csv('spy_daily.csv', parse_dates=['date'], index_col='date')['adj'].pct_change()
eq_r = pd.concat([eq_r, spy[spy.index>eq_r.index[-1]]])
recon = pd.read_csv('bond10_recon_daily.csv', parse_dates=['Date'], index_col='Date')['ret']
ief = pd.read_csv('ief_daily.csv', parse_dates=['date'], index_col='date')['adj'].pct_change().dropna()
bond_idx=(1+pd.concat([recon[recon.index<ief.index[0]], ief])).cumprod().reindex(eq_r.index).ffill()
re=eq_r.reindex(days).fillna(0).values; rb=bond_idx.pct_change().reindex(days).fillna(0).values
sd=0.5/100/252; COST=0.0005

def run(mode):
    if mode=='fixed':
        Ee=1.0*st_eq; Eb=1.0*st_bd
    else:  # перелив: суммарно 2× на включённые
        both=st_eq&st_bd; only_e=st_eq&~st_bd; only_b=st_bd&~st_eq
        Ee=np.where(both,1.0,np.where(only_e,2.0,0.0))
        Eb=np.where(both,1.0,np.where(only_b,2.0,0.0))
    r = rfv + Ee*(re-rfv-sd) + Eb*(rb-rfv-sd)
    dE=np.abs(np.diff(Ee,prepend=0))+np.abs(np.diff(Eb,prepend=0))
    r = pd.Series(r-COST*dE, index=days)
    eq=(1+r).cumprod(); yrs=(days[-1]-days[0]).days/365.25
    dd=eq/eq.cummax()-1
    print(f"{mode:8s}: CAGR {(eq.iloc[-1]**(1/yrs)-1)*100:5.2f}%  vol {r.std()*np.sqrt(252)*100:5.2f}%  MaxDD {dd.min()*100:6.1f}% (дно {dd.idxmin().date()})  худш.день {r.min()*100:6.2f}% ({r.idxmin().date()})")
    return r

print('1963–2026, склейка:')
r_f=run('fixed'); r_r=run('перелив')
# состояние облигационной ноги в октябре 1987
i87 = (days>=pd.Timestamp('1987-10-01'))&(days<=pd.Timestamp('1987-10-31'))
print('\nоктябрь 1987: нога акций вкл:', bool(st_eq[i87][0]), '| нога облигаций вкл:', bool(st_bd[i87][0]))
print(f"октябрь 1987 итог: фикс {((1+r_f[i87]).prod()-1)*100:+.1f}%  перелив {((1+r_r[i87]).prod()-1)*100:+.1f}%")
