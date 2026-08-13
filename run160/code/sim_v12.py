import pandas as pd, numpy as np
from pathlib import Path
ROOT = Path(__file__).resolve().parent
D = ROOT/'data' if (ROOT/'data').exists() else ROOT
COST=0.0005; SPREAD=0.5/100/252; BAND=0.03; ROLL_BP=0.0001
CTD_RATIO=0.88; ZN_FACE=112000.0; ES_MULT=500.0; MES_START=pd.Timestamp('2019-05-06')

def rf_series(index):
    rfr = pd.read_csv(D/'us-market-daily-1885-2025.csv', parse_dates=['Date']).set_index('Date')['Risk Free Rate']
    dtb = pd.read_csv(D/'dtb3.csv', parse_dates=['observation_date']).set_index('observation_date')['DTB3'].dropna()
    dtb_eff = 365*dtb/100/(360-91*dtb/100)*100
    tail = dtb_eff[dtb_eff.index>rfr.index[-1]]
    full = pd.concat([rfr, tail]).reindex(index).ffill()
    return (1+full/100)**(1/252)-1

def roll_days(index):
    out=[]
    for y in range(index[0].year, index[-1].year+1):
        for m in [2,5,8,11]:
            month = index[(index.year==y)&(index.month==m)]
            if len(month)>=4: out.append(month[-4])
    return set(out)

def sim(days, re, rb, rfv, es_notional, zn_quant, st_eq, st_bd, cap0=10_000_000.0):
    e=cap0; Ne=Nb=0.0; nav=np.empty(len(days)); trades=0; rolls=roll_days(days)
    prev_se=prev_sb=None
    for i,dte in enumerate(days):
        tgt_e=1.0*st_eq[i]*e; tgt_b=1.0*st_bd[i]*e
        qe = es_notional[i]/10 if dte>=MES_START else es_notional[i]
        qb = zn_quant[i]
        for which in ('e','b'):
            cur,tgt,q,changed = (Ne,tgt_e,qe,st_eq[i]!=prev_se) if which=='e' else (Nb,tgt_b,qb,st_bd[i]!=prev_sb)
            if changed or abs(tgt-cur)>BAND*e:
                snapped=round(tgt/q)*q
                if abs(snapped-cur)>1e-9:
                    e-=COST*abs(snapped-cur); trades+=1
                if which=='e': Ne=snapped
                else: Nb=snapped
        prev_se,prev_sb=st_eq[i],st_bd[i]
        if dte in rolls: e-=ROLL_BP*(abs(Ne)+abs(Nb))
        e = e*(1+rfv[i]) + Ne*(re[i]-rfv[i]-SPREAD) + Nb*(rb[i]-rfv[i]-SPREAD)
        Ne*=(1+re[i]); Nb*=(1+rb[i]); nav[i]=e
    s=pd.Series(nav,index=days); r=s.pct_change(); r.iloc[0]=nav[0]/cap0-1
    return r,s,trades

def met(r):
    eqc=(1+r).cumprod(); yrs=(r.index[-1]-r.index[0]).days/365.25
    dd=(eqc/eqc.cummax()-1)
    return (eqc.iloc[-1]**(1/yrs)-1)*100, dd.min()*100

# ================= СОВРЕМЕННОЕ ОКНО: настоящие контракты =================
spy = pd.read_csv(D/'spy_daily.csv', parse_dates=['date'], index_col='date')
ief = pd.read_csv(D/'ief_daily.csv', parse_dates=['date'], index_col='date')['adj']
recon_y = pd.read_csv(D/'dgs10.csv', parse_dates=['observation_date']).set_index('observation_date')['DGS10'].dropna()/100
days_m = spy.index[spy.index>=pd.Timestamp('2003-07-01')]
re_m = spy['adj'].pct_change().reindex(days_m).fillna(0).values
rb_m = ief.pct_change().reindex(days_m).fillna(0).values
rfv_m = rf_series(days_m).values
es_not = (ES_MULT*spy['close']).reindex(days_m).values
def dur(yv):
    c=yv/2; n=20; t=np.arange(1,n+1)
    cf=np.full(n,c); cf[-1]+=1.0; disc=(1+c)**(-t); pv=cf*disc
    return (t*pv).sum()/pv.sum()/2/(1+c)
Dref = pd.Series([dur(v) for v in recon_y.values], index=recon_y.index).reindex(days_m).ffill()
zn_quant_m = (ZN_FACE*CTD_RATIO*(Dref/7.3)).values
# сигналы по ногам современного окна (SPY/IEF месячные закрытия)
def sigs(px, days):
    me=px.resample('ME').last().ffill(); sma=me.rolling(12).mean(); s=(me>sma)[sma.notna()]
    sm=pd.Series(s.values, index=(s.index+pd.offsets.MonthBegin(1)).to_period('M'))
    return sm.reindex(days.to_period('M')).ffill().astype(bool).values
st_e_m = sigs(spy['adj'], days_m); st_b_m = sigs(ief, days_m)
r_m, nav_m, tr_m = sim(days_m, re_m, rb_m, rfv_m, es_not, zn_quant_m, st_e_m, st_b_m)
c,dd = met(r_m); yrs=(days_m[-1]-days_m[0]).days/365.25
print(f"СОВРЕМЕННОЕ ОКНО (настоящие контракты, DV01, роллы, реальный хвост ставки):")
print(f"  2003–2026: CAGR {c:.2f}%  MaxDD {dd:.1f}%  сделок/год {tr_m/yrs:.1f}")
n_es = round(10e6/es_not[-1]); n_zn = round(10e6/zn_quant_m[-1])
print(f"  контракты на 10 млн сегодня: ~{n_es} ES (+MES добор), ~{n_zn} ZN (DV01-сайзинг)")

# ================= СКЛЕЙКА 1963–2026: прокси с квантами и роллами =================
mk = pd.read_csv(D/'us-market-daily-1885-2025.csv', parse_dates=['Date']).set_index('Date').sort_index()
eq_r = mk['Adjusted Close'].pct_change()
eq_r = pd.concat([eq_r, spy['adj'].pct_change()[spy.index>eq_r.index[-1]]])
recon = pd.read_csv(D/'bond10_recon_daily.csv', parse_dates=['Date'], index_col='Date')['ret']
bond_idx=(1+pd.concat([recon[recon.index<ief.index[0]], ief.pct_change().dropna()])).cumprod().reindex(eq_r.index).ffill()
days_l = eq_r.index[eq_r.index>=pd.Timestamp('1963-03-01')]
re_l=eq_r.reindex(days_l).fillna(0).values; rb_l=bond_idx.pct_change().reindex(days_l).fillna(0).values
rfv_l = rf_series(days_l).values
eq_lvl=(1+pd.Series(re_l,index=days_l)).cumprod()
es_not_l=(es_not[-1]*eq_lvl/eq_lvl.iloc[-1]).values
Dref_l = pd.Series([dur(v) for v in recon_y.values], index=recon_y.index).reindex(days_l).ffill().bfill()
zn_quant_l=(ZN_FACE*CTD_RATIO*(Dref_l/7.3)).values
st_e_l = sigs(mk['Adjusted Close'].combine_first((1+eq_r).cumprod()), days_l)
# корректно: сигнал по полному eq-индексу
eq_full_idx=(1+eq_r.fillna(0)).cumprod()
st_e_l = sigs(eq_full_idx, days_l); st_b_l = sigs(bond_idx.dropna(), days_l)
r_l, nav_l, tr_l = sim(days_l, re_l, rb_l, rfv_l, es_not_l, zn_quant_l, st_e_l, st_b_l)
c,dd=met(r_l); c2,dd2=met(r_l['2003-07-01':]); yrsl=(days_l[-1]-days_l[0]).days/365.25
print(f"СКЛЕЙКА (прокси-активы, синтетические кванты, роллы):")
print(f"  1963–2026: CAGR {c:.2f}%  MaxDD {dd:.1f}%  сделок/год {tr_l/yrsl:.1f}")
nav_m.to_frame('nav').to_csv(ROOT/'nav_v12_modern.csv'); nav_l.to_frame('nav').to_csv(ROOT/'nav_v12_splice.csv')
