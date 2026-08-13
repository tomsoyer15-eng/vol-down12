import pandas as pd, numpy as np
from pathlib import Path
ROOT = Path(__file__).resolve().parent
D = ROOT/'data' if (ROOT/'data').exists() else ROOT
def run(strict=True, L=2.0, spread_pp=0.5):
    px = pd.read_csv(D/'gspc_1928_1935.csv', parse_dates=['date'], index_col='date')['close']['1927-12-30':'1935-12-31']
    sh = pd.read_csv(D/'shiller-monthly-1871-2026.csv', parse_dates=['Date']).set_index('Date').sort_index()
    dy_d = (sh['Dividend']/sh['SP500']).reindex(px.index, method='ffill')/252
    eq_r = (px.pct_change()+dy_d).dropna(); eq_idx=(1+eq_r).cumprod()
    y10=sh['Long Interest Rate']/100
    def dc(yv):
        c=yv/2; n=20; t=np.arange(1,n+1)
        cf=np.full(n,c); cf[-1]+=1.0; disc=(1+c)**(-t); pv=cf*disc; P=pv.sum()
        return (t*pv).sum()/P/2/(1+c), (t*(t+1)*pv).sum()/P/(1+c)**2/4
    Dv,Cv=zip(*[dc(v) for v in y10.values])
    Dv=pd.Series(Dv,index=y10.index); Cv=pd.Series(Cv,index=y10.index)
    dyy=y10.diff()
    bdm=(y10.shift(1)/12 - Dv.shift(1)*dyy + 0.5*Cv.shift(1)*dyy**2).dropna()
    bd_d = pd.Series(index=eq_r.index, dtype=float)
    for p in eq_r.index.to_period('M').unique():
        mv = bdm[bdm.index.to_period('M')==p]; val = mv.iloc[0] if len(mv) else 0.0
        n=(eq_r.index.to_period('M')==p).sum()
        bd_d[eq_r.index.to_period('M')==p]=(1+val)**(1/n)-1
    bd_idx=(1+bd_d).cumprod()
    mk = pd.read_csv(D/'us-market-daily-1885-2025.csv', parse_dates=['Date']).set_index('Date').sort_index()
    rf_d = ((1+mk['Risk Free Rate']/100)**(1/252)-1).reindex(eq_r.index).ffill()
    def sig(idx_s):
        me=idx_s.resample('ME').last().ffill(); sma=me.rolling(12).mean(); s=(me>sma)[sma.notna()]
        sm=pd.Series(s.values, index=(s.index+pd.offsets.MonthBegin(1)).to_period('M'))
        return sm.reindex(eq_r.index.to_period('M')).ffill().fillna(False).astype(bool).values
    se,sb = sig(eq_idx), sig(bd_idx)
    if strict:
        se=pd.Series(se,index=eq_r.index).shift(1).bfill().astype(bool).values
        sb=pd.Series(sb,index=eq_r.index).shift(1).bfill().astype(bool).values
    sd=spread_pp/100/252
    Ee=L*0.5*se; Eb=L*0.5*sb
    r = rf_d.values + Ee*(eq_r.values-rf_d.values-sd) + Eb*(bd_d.reindex(eq_r.index).values-rf_d.values-sd)
    dE=np.abs(np.diff(Ee,prepend=0))+np.abs(np.diff(Eb,prepend=0))
    r=pd.Series(r-0.0005*dE, index=eq_r.index)
    w=r['1929-01-02':'1934-12-31']; e=(1+w).cumprod()
    return ((e.iloc[-1]-1)*100, (e/e.cummax()-1).min()*100)
if __name__=='__main__':
    for strict,nm in [(False,'идеализированный T+1'),(True,'строгий T+2')]:
        tot,dd = run(strict)
        print(f"1929–1934, {nm}: итог {tot:+.1f}%, MaxDD {dd:.1f}%")
