import pandas as pd, numpy as np
from pathlib import Path
D=Path('.')
COST=0.0005; SD=0.5/100/252
spy = pd.read_csv('spy_daily.csv', parse_dates=['date'], index_col='date')
ief = pd.read_csv('ief_daily.csv', parse_dates=['date'], index_col='date')['adj']
mk = pd.read_csv('us-market-daily-1885-2025.csv', parse_dates=['Date']).set_index('Date').sort_index()
eq_r = pd.concat([mk['Adjusted Close'].pct_change(), spy['adj'].pct_change()[spy.index>mk.index[-1]]])
recon = pd.read_csv('bond10_recon_daily.csv', parse_dates=['Date'], index_col='Date')['ret']
bond_idx=(1+pd.concat([recon[recon.index<ief.index[0]], ief.pct_change().dropna()])).cumprod().reindex(eq_r.index).ffill()
eq_idx=(1+eq_r.fillna(0)).cumprod()
rfr = mk['Risk Free Rate']
dtb = pd.read_csv('dtb3.csv', parse_dates=['observation_date']).set_index('observation_date')['DTB3'].dropna()
rf_full = pd.concat([rfr, (365*dtb/100/(360-91*dtb/100)*100)[dtb.index>rfr.index[-1]]])

def leg_exposures(idx, days, mode):
    """mode: 'base' (сигнал T, доход с T+1→T+2), 'ant3' (сигнал T-3, доход с T-2→T-1, поправка с T+1→T+2), 'ant1'"""
    me = idx.resample('ME').last().ffill()
    S11 = me.rolling(11).mean().shift(1)   # линия: средняя 11 ЗАВЕРШЁННЫХ месяцев, известна весь месяц
    months = pd.PeriodIndex(days, freq='M')
    E = np.zeros(len(days)); state=False
    wrong=0; trans=0
    pos_by_day = pd.Series(idx.reindex(days).values, index=days)
    dlist=list(days); month_last={}
    for i,dd in enumerate(days): month_last[months[i]]=i
    first_valid = S11.dropna().index[0].to_period('M')
    for m in sorted(set(months)):
        if m<=first_valid: 
            continue
        Ti = month_last[m]
        line = S11.get(m.to_timestamp('M'))
        if pd.isna(line): continue
        S_actual = pos_by_day.iloc[Ti] > line
        k = 3 if mode=='ant3' else 1
        Ai = max(Ti-k, 0)
        A = pos_by_day.iloc[Ai] > line if mode!='base' else S_actual
        # применение: base: state=S с Ti+2 (доход интервала Ti+1→Ti+2); ant: state=A с Ai+2, поправка на S с Ti+2
        if mode=='base':
            start=Ti+2
            for j in range(start, len(days)):
                if months[j]!=m and months[j]!=m+1: break
                E[j]=None
            # проще: маркеры переходов соберём ниже единым проходом
        if mode!='base':
            if A!=S_actual: wrong+=1
            if A!=state or S_actual!=A: trans+=1
    # единый проход: строим дневную экспозицию
    E = np.zeros(len(days)); state=False
    events=[]  # (index_of_effect, new_state)
    for m in sorted(set(months)):
        if m<=first_valid: continue
        Ti = month_last[m]; line = S11.get(m.to_timestamp('M'))
        if pd.isna(line): continue
        S_actual = pos_by_day.iloc[Ti] > line
        if mode=='base':
            events.append((Ti+2, bool(S_actual)))
        else:
            k = 3 if mode=='ant3' else 1
            Ai = max(Ti-k,0)
            A = pos_by_day.iloc[Ai] > line
            events.append((Ai+2, bool(A)))
            events.append((Ti+2, bool(S_actual)))
    events.sort()
    cur=False; ptr=0
    for j in range(len(days)):
        while ptr<len(events) and events[ptr][0]==j:
            cur=events[ptr][1]; ptr+=1
        E[j]=cur
    return E, wrong, max(trans,1)

for label, days0 in [('СКЛЕЙКА 1963–2026', eq_idx.index[eq_idx.index>=pd.Timestamp('1963-03-01')]),
                     ('СОВРЕМЕННОЕ 2003–2026', eq_idx.index[eq_idx.index>=pd.Timestamp('2003-07-01')])]:
    days=days0
    re=eq_r.reindex(days).fillna(0).values; rb=bond_idx.pct_change().reindex(days).fillna(0).values
    rfv=((1+rf_full.reindex(days).ffill()/100)**(1/252)-1).values
    print(f"\n=== {label} ===")
    for mode,nm in [('base','базовая строгая (T месяц-энд)'),('ant1','упреждение T-1'),('ant3','упреждение T-3')]:
        Ee,we,te = leg_exposures(eq_idx, days, mode)
        Eb,wb,tb = leg_exposures(bond_idx.dropna(), days, mode)
        E1, E2 = 1.0*Ee, 1.0*Eb
        r = rfv + E1*(re-rfv-SD) + E2*(rb-rfv-SD)
        dE = np.abs(np.diff(E1,prepend=0))+np.abs(np.diff(E2,prepend=0))
        r = pd.Series(r-COST*dE, index=days)
        eqc=(1+r).cumprod(); yrs=(days[-1]-days[0]).days/365.25
        dd=(eqc/eqc.cummax()-1).min()*100
        c=(eqc.iloc[-1]**(1/yrs)-1)*100
        extra = f" | ошибочных упреждений: акции {we}, облиг {wb}" if mode!='base' else ""
        print(f"{nm:32s} CAGR {c:6.2f}%  MaxDD {dd:6.1f}%  сделок/год {dE.astype(bool).sum()/yrs:4.1f}{extra}")
