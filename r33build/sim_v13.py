import pandas as pd, numpy as np
from pathlib import Path
ROOT = Path(__file__).resolve().parent
D = ROOT/'data' if (ROOT/'data').exists() else ROOT
COST=0.0005; ROLL_BP=0.0001
# BAND — ЗАМОРОЖЕННАЯ полоса ред. 4.1. Не меняется никогда: на ней стоят эталонные NAV
# sim_v13 и регрессия 1e-12 маршрута Ф в режиме first_day='legacy', cap=None.
BAND=0.03
# BAND_OP — ОПЕРАЦИОННАЯ полоса, принята заказчиком 11.08.2026 (ред. 33, §2).
# Действует в маршрутах Ф и Е; на регрессию к замороженной ред. 4.1 не влияет.
BAND_OP=0.10
CTD_RATIO=0.88; ZN_MODEL_PX_EQ=112000.0; ES_MULT=500.0; MES_START=pd.Timestamp('2019-05-06')

def rf_series(index):
    rfr = pd.read_csv(D/'us-market-daily-1885-2025.csv', parse_dates=['Date']).set_index('Date')['Risk Free Rate']
    dtb = pd.read_csv(D/'dtb3.csv', parse_dates=['observation_date']).set_index('observation_date')['DTB3'].dropna()
    dtb_eff = 365*dtb/100/(360-91*dtb/100)*100
    full = pd.concat([rfr, dtb_eff[dtb_eff.index>rfr.index[-1]]]).reindex(index).ffill()
    return (1+full/100)**(1/252)-1

def roll_days_calendar(index):
    """FND = последний торговый день фев/мая/авг/ноя; ролл = FND минус 3 торговых дня.
    Роллы по месяцам, не завершённым в данных, не создаются."""
    out=set()
    for y in range(index[0].year, index[-1].year+1):
        for m in [2,5,8,11]:
            # ИСПРАВЛЕНО ред. 33: инкремент года был применён к НОЯБРЮ, хотя декабрь идёт
            # в том же году. Из-за этого ноябрьский ролл создавался, только если данные
            # заходили за 1 декабря СЛЕДУЮЩЕГО года; в живой торговле это означало ПРОПУСК
            # ноябрьского ролла ежегодно, то есть вход ZN в зону поставки.
            nm = pd.Timestamp(y + (1 if m == 12 else 0), (m % 12) + 1, 1)
            month = index[(index.year==y)&(index.month==m)]
            if len(month)==0 or index[index>=nm].size==0: continue
            pos = index.get_loc(month[-1])
            if pos>=3: out.add(index[pos-3])
    return out

def strict_states(st, days):
    return pd.Series(st, index=days).shift(1).bfill().astype(bool).values

def dur(yv):
    c=yv/2; n=20; t=np.arange(1,n+1)
    cf=np.full(n,c); cf[-1]+=1.0; disc=(1+c)**(-t); pv=cf*disc
    return (t*pv).sum()/pv.sum()/2/(1+c)

def sigs(px, days):
    me=px.resample('ME').last().ffill(); sma=me.rolling(12).mean(); s=(me>sma)[sma.notna()]
    sm=pd.Series(s.values, index=(s.index+pd.offsets.MonthBegin(1)).to_period('M'))
    return sm.reindex(days.to_period('M')).ffill().astype(bool).values

def sim(days, re, rb, rfv, spy_close, dref, st_eq, st_bd, spread_pp=0.5, strict=True, cap0=10_000_000.0):
    """Контрактное состояние (v1.5.3): между сделками хранятся ЦЕЛЫЕ n_e (ES до 06.05.2019,
    далее MES) и n_b (ZN); экспозиция ноги = n × контрактный эквивалент по ЗАКРЫТИЮ
    ПРЕДЫДУЩЕЙ СЕССИИ (данные T+1 для сделки со стартом начисления T+1→T+2 — без подсмотра);
    полоса 3% проверяется на этой экспозиции; P&L дня = экспозиция × (доход − ставка − спред).
    ZN: DV01_ZN = B×0,88×D_fix×1e-4, D_fix фиксируется при роллах и переключениях ноги Б
    по D_ref предыдущей сессии; референсный эквивалент контракта q(t) = DV01_ZN/(D_ref(t)×1e-4)."""
    sd = spread_pp/100/252
    if strict:
        st_eq = strict_states(st_eq, days); st_bd = strict_states(st_bd, days)
    e=cap0; n_e=0; n_b=0; unit_is_mes=False
    nav=np.empty(len(days)); trades=0
    rolls = roll_days_calendar(days)
    prev_se=prev_sb=None; D_fix=dref[0]
    for i,dte in enumerate(days):
        j = max(i-1,0)                                   # данные предыдущей сессии
        if (not unit_is_mes) and dte>=MES_START:
            n_e *= 10; unit_is_mes=True                  # конверсия единицы ES→MES, без сделки
        mult = ES_MULT/10 if unit_is_mes else ES_MULT
        unit_e = mult*spy_close[j]
        eq_switch = (st_eq[i]!=prev_se); bd_switch = (st_bd[i]!=prev_sb); roll_today = (dte in rolls)
        if roll_today or bd_switch:
            D_fix = dref[j]
        q_b = (ZN_MODEL_PX_EQ*CTD_RATIO*D_fix*1e-4)/(dref[j]*1e-4)
        tgt_e=1.0*st_eq[i]*e; tgt_b=1.0*st_bd[i]*e
        exp_e = n_e*unit_e; exp_b = n_b*q_b
        if eq_switch or abs(tgt_e-exp_e)>BAND*e:
            new = round(tgt_e/unit_e)
            if new!=n_e:
                e -= COST*abs(new-n_e)*unit_e; trades+=1; n_e=new
        if bd_switch or roll_today or abs(tgt_b-exp_b)>BAND*e:
            new = round(tgt_b/q_b)
            if new!=n_b:
                e -= COST*abs(new-n_b)*q_b; trades+=1; n_b=new
        prev_se,prev_sb=st_eq[i],st_bd[i]
        exp_e = n_e*unit_e; exp_b = n_b*q_b
        if roll_today: e -= ROLL_BP*(exp_e+exp_b)
        e = e*(1+rfv[i]) + exp_e*(re[i]-rfv[i]-sd) + exp_b*(rb[i]-rfv[i]-sd)
        nav[i]=e
    s=pd.Series(nav,index=days); r=s.pct_change(); r.iloc[0]=nav[0]/cap0-1
    return r,s,trades

def met(r):
    eqc=(1+r).cumprod(); yrs=(r.index[-1]-r.index[0]).days/365.25
    dd=(eqc/eqc.cummax()-1)
    return (eqc.iloc[-1]**(1/yrs)-1)*100, dd.min()*100

def build_modern():
    spy = pd.read_csv(D/'spy_daily.csv', parse_dates=['date'], index_col='date')
    ief = pd.read_csv(D/'ief_daily.csv', parse_dates=['date'], index_col='date')['adj']
    y = pd.read_csv(D/'dgs10.csv', parse_dates=['observation_date']).set_index('observation_date')['DGS10'].dropna()/100
    days = spy.index[spy.index>=pd.Timestamp('2003-07-01')]
    re = spy['adj'].pct_change().reindex(days).fillna(0).values
    rb = ief.pct_change().reindex(days).fillna(0).values
    rfv = rf_series(days).values
    spy_close = spy['close'].reindex(days).values
    Dref = pd.Series([dur(v) for v in y.values], index=y.index).reindex(days).ffill()
    return days, re, rb, rfv, spy_close, Dref.values, sigs(spy['adj'],days), sigs(ief,days)

def build_splice():
    spy = pd.read_csv(D/'spy_daily.csv', parse_dates=['date'], index_col='date')
    ief = pd.read_csv(D/'ief_daily.csv', parse_dates=['date'], index_col='date')['adj']
    mk = pd.read_csv(D/'us-market-daily-1885-2025.csv', parse_dates=['Date']).set_index('Date').sort_index()
    eq_r = pd.concat([mk['Adjusted Close'].pct_change(), spy['adj'].pct_change()[spy.index>mk.index[-1]]])
    recon = pd.read_csv(D/'bond10_recon_daily.csv', parse_dates=['Date'], index_col='Date')['ret']
    bond_idx=(1+pd.concat([recon[recon.index<ief.index[0]], ief.pct_change().dropna()])).cumprod().reindex(eq_r.index).ffill()
    days = eq_r.index[eq_r.index>=pd.Timestamp('1963-03-01')]
    re=eq_r.reindex(days).fillna(0).values; rb=bond_idx.pct_change().reindex(days).fillna(0).values
    rfv = rf_series(days).values
    eq_lvl=(1+pd.Series(re,index=days)).cumprod()
    synth_close=(spy['close'].iloc[-1]*eq_lvl/eq_lvl.iloc[-1]).values
    y = pd.read_csv(D/'dgs10.csv', parse_dates=['observation_date']).set_index('observation_date')['DGS10'].dropna()/100
    Dref = pd.Series([dur(v) for v in y.values], index=y.index).reindex(days).ffill().bfill()
    eq_full=(1+eq_r.fillna(0)).cumprod()
    return days, re, rb, rfv, synth_close, Dref.values, sigs(eq_full,days), sigs(bond_idx.dropna(),days)

if __name__=='__main__':
    dm = build_modern()
    for nm,strict in [('строгая',True),('идеализ.',False)]:
        r,nav,tr=sim(*dm,strict=strict); c,dd=met(r); yrs=(dm[0][-1]-dm[0][0]).days/365.25
        print(f"2003–2026 {nm}: {c:.2f}% / {dd:.2f}% / {tr/yrs:.1f} сд/год")
        if strict: nav.to_frame('nav').to_csv(ROOT/'nav_v13_modern.csv')
    r,_,_=sim(*dm,spread_pp=1.5,strict=True); c,dd=met(r); print(f"стресс 1,5: {c:.2f}% / {dd:.2f}%")
    ds=build_splice()
    r,nav,tr=sim(*ds,strict=True); c,dd=met(r); yrs=(ds[0][-1]-ds[0][0]).days/365.25
    print(f"1963–2026: {c:.2f}% / {dd:.2f}% / {tr/yrs:.1f} сд/год")
    nav.to_frame('nav').to_csv(ROOT/'nav_v13_splice.csv')
