import pandas as pd, numpy as np, pickle
d=pickle.load(open('engine.pkl','rb'))
days,st_eq,st_bd,rfv = d['days'],d['st_eq'],d['st_bd'],d['rfv']
mk = pd.read_csv('us-market-daily-1885-2025.csv', parse_dates=['Date']).set_index('Date').sort_index()
eq_r = mk['Adjusted Close'].pct_change()
spy = pd.read_csv('spy_daily.csv', parse_dates=['date'], index_col='date')['adj'].pct_change()
eq_r = pd.concat([eq_r, spy[spy.index>eq_r.index[-1]]])
recon = pd.read_csv('bond10_recon_daily.csv', parse_dates=['Date'], index_col='Date')['ret']
ief = pd.read_csv('ief_daily.csv', parse_dates=['date'], index_col='date')['adj'].pct_change().dropna()
bond_idx=(1+pd.concat([recon[recon.index<ief.index[0]], ief])).cumprod().reindex(eq_r.index).ffill()
re=eq_r.reindex(days).fillna(0).values; rb=bond_idx.pct_change().reindex(days).fillna(0).values
sd=0.5/100/252; COST=0.0005
Q_E=0.0034; Q_B=0.0112   # квант MES и ZN как доля капитала (уровни 2026)
BAND=0.03

def executable(L=2.0):
    e=1.0; Ne=Nb=0.0
    months=days.to_period('M'); prev_m=None
    nav=np.empty(len(days)); trades=0
    for i in range(len(days)):
        m=months[i]
        tgt_e = L*0.5*st_eq[i]*e; tgt_b = L*0.5*st_bd[i]*e
        new_month = (m!=prev_m)
        for which in ('e','b'):
            if which=='e':
                cur,tgt,q = Ne,tgt_e,Q_E*e
            else:
                cur,tgt,q = Nb,tgt_b,Q_B*e
            need = (abs(tgt-cur) > BAND*e) or (new_month and abs(tgt-cur)>q/2)
            if need:
                snapped = round(tgt/q)*q if q>0 else tgt
                e -= COST*abs(snapped-cur); trades+=1 if abs(snapped-cur)>1e-12 else 0
                if which=='e': Ne=snapped
                else: Nb=snapped
        prev_m=m
        pnl = e*rfv[i] + Ne*(re[i]-rfv[i]-sd) + Nb*(rb[i]-rfv[i]-sd)
        e += pnl
        Ne *= (1+re[i]); Nb *= (1+rb[i])
        nav[i]=e
    s=pd.Series(nav,index=days); r=s.pct_change(); r.iloc[0]=nav[0]-1
    return r, s, trades

def met(r,a=None,b=None):
    rr=r if a is None else r[a:b]
    eqc=(1+rr).cumprod(); yrs=(rr.index[-1]-rr.index[0]).days/365.25
    dd=(eqc/eqc.cummax()-1)
    return (eqc.iloc[-1]**(1/yrs)-1)*100, dd.min()*100

r_ex, nav, ntr = executable()
L2=d['L2']
yrs=(days[-1]-days[0]).days/365.25
print('=== Исполняемая версия (полоса 3%, кванты MES/ZN, целочисленность) против непрерывной ===')
for nm,(a,b) in [('1963–2026',(None,None)),('2003–2026',('2003-07-01','2026-08-06'))]:
    c1,d1=met(L2,a,b); c2,d2=met(r_ex,a,b)
    print(f"{nm}: непрерывная {c1:.2f}%/{d1:.1f}%  |  исполняемая {c2:.2f}%/{d2:.1f}%  |  Δ {c2-c1:+.2f} п.п.")
print(f"сделок ребалансировки/переключений всего: {ntr} (~{ntr/yrs:.1f}/год)")

# маржа по числам рецензента
cap=10_000_000
mE,mB=34_800,2_160
nES,nZN=29,89
req=nES*mE+nZN*mB
print(f"\nмаржа IBKR (числа рецензента): {req/1e6:.3f} млн = {req/cap*100:.1f}% капитала; запас в норме {cap/req:.1f}×")
eq_after=cap*(1-0.204)
print(f"после дня −20,4% при ×3: запас {eq_after/(req*3):.2f}×  (порог О-3: 2,0×)")

nav.to_frame('nav').to_csv('nav_executable_daily.csv')
sig=pd.DataFrame({'leg_eq':st_eq,'leg_bond':st_bd}, index=days).resample('ME').first()
sig.astype(int).to_csv('signals_monthly.csv')
print('nav и сигналы выгружены')
