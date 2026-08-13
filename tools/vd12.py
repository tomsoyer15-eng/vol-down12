#!/usr/bin/env python3
"""ADD-FUT-ВТ-ВНИЗ поверх движка ядра v1.5.3.1, на рядах ядра.

Движок — sim_v13.sim ядра без изменений по существу: строгая конвенция (сигнал на
закрытии T, сделка T+1), целое число контрактов между сделками, DV01-сайзинг ноги Б,
полоса 3%, издержки 5 б.п. на изменение экспозиции, роллы 1 б.п., спред 0,5 п.п.
Единственная правка — цель экспозиции включённой ноги: было 1,0 × капитал,
стало m × k × капитал.

k пересчитывается в первый торговый день недели по 60 последним дневным ходам связки
(данные по закрытие предыдущей сессии включительно), внутри недели не меняется.
Дневной ход связки (§1.13) = ставка кэша + сумма по включённым ногам (доход ноги −
ставка кэша); состав связки берётся тот, какой фактически был в тот день.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'pkg'))
import sim_v13 as S

TARGET_VOL = 12.0      # §1.16 цель, % годовых
VOL_WIN = 60           # §3 окно качки, торговых дней
ANN = 15.87            # §1.15 корень из 252


def week_starts(days):
    w = days.to_period('W')
    return np.r_[True, w[1:] != w[:-1]]


def sim_vd12(days, re, rb, rfv, spy_close, dref, st_eq, st_bd,
             m=1.25, spread_pp=0.5, strict=True, cap0=10_000_000.0,
             weekly_trade=False, k_fixed=None):
    """weekly_trade=False — сделка по правилу ядра (смена ноги / выход за полосу 3% / ролл).
       weekly_trade=True  — плюс безусловный ребаланс в каждый недельный пересчёт (§4.2д)."""
    sd = spread_pp / 100 / 252
    if strict:
        st_eq = S.strict_states(st_eq, days)
        st_bd = S.strict_states(st_bd, days)

    # дневной ход связки при штатном размере 1,0x на включённую ногу
    combo = rfv + st_eq * (re - rfv) + st_bd * (rb - rfv)
    ws = week_starts(days)

    e = cap0; n_e = 0; n_b = 0; unit_is_mes = False
    nav = np.empty(len(days)); kk = np.empty(len(days)); gross = np.empty(len(days))
    trades = 0
    rolls = S.roll_days_calendar(days)
    prev_se = prev_sb = None
    D_fix = dref[0]
    k = 1.0

    for i, dte in enumerate(days):
        j = max(i - 1, 0)
        if k_fixed is not None:
            k = k_fixed
        elif (ws[i] or i == 0):
            lo = max(0, i - VOL_WIN)
            win = combo[lo:i]
            if len(win) >= VOL_WIN:
                vol = win.std(ddof=1) * 100 * ANN
                k = min(1.0, TARGET_VOL / vol) if vol > 0 else 1.0
            else:
                k = 1.0                      # прогрев 60 дней: потолок
        kk[i] = k
        if (not unit_is_mes) and dte >= S.MES_START:
            n_e *= 10; unit_is_mes = True
        mult = S.ES_MULT / 10 if unit_is_mes else S.ES_MULT
        unit_e = mult * spy_close[j]
        eq_switch = (st_eq[i] != prev_se); bd_switch = (st_bd[i] != prev_sb)
        roll_today = (dte in rolls)
        if roll_today or bd_switch:
            D_fix = dref[j]
        q_b = (S.ZN_MODEL_PX_EQ * S.CTD_RATIO * D_fix * 1e-4) / (dref[j] * 1e-4)

        size = m * k
        tgt_e = size * st_eq[i] * e; tgt_b = size * st_bd[i] * e
        exp_e = n_e * unit_e; exp_b = n_b * q_b
        force = weekly_trade and ws[i]
        if eq_switch or force or abs(tgt_e - exp_e) > S.BAND * e:
            new = round(tgt_e / unit_e)
            if new != n_e:
                e -= S.COST * abs(new - n_e) * unit_e; trades += 1; n_e = new
        if bd_switch or roll_today or force or abs(tgt_b - exp_b) > S.BAND * e:
            new = round(tgt_b / q_b)
            if new != n_b:
                e -= S.COST * abs(new - n_b) * q_b; trades += 1; n_b = new
        prev_se, prev_sb = st_eq[i], st_bd[i]
        exp_e = n_e * unit_e; exp_b = n_b * q_b
        gross[i] = (exp_e + exp_b) / e
        if roll_today:
            e -= S.ROLL_BP * (exp_e + exp_b)
        e = e * (1 + rfv[i]) + exp_e * (re[i] - rfv[i] - sd) + exp_b * (rb[i] - rfv[i] - sd)
        nav[i] = e

    s = pd.Series(nav, index=days)
    r = s.pct_change(); r.iloc[0] = nav[0] / cap0 - 1
    return r, s, trades, pd.Series(kk, index=days), pd.Series(gross, index=days)


def report(name, days, r, trades, k, gross):
    cagr, dd = S.met(r)
    yrs = (days[-1] - days[0]).days / 365.25
    vol = r.std() * np.sqrt(252) * 100
    eqc = (1 + r).cumprod()
    ddser = eqc / eqc.cummax() - 1
    print(f"{name:<30}{cagr:>7.2f}%{vol:>8.2f}%{dd:>9.1f}%{cagr/vol:>7.2f}"
          f"{gross.mean():>8.2f}x{gross.max():>7.2f}x{trades/yrs:>7.1f}"
          f"   {ddser.idxmin():%Y-%m-%d}")
    return cagr, vol, dd


HDR = (f"{'вариант':<30}{'CAGR':>8}{'качка':>8}{'MaxDD':>9}{'D/V':>7}"
       f"{'ном.ср':>8}{'ном.max':>7}{'сд/год':>7}   дно MaxDD")

if __name__ == '__main__':
    for wname, build in [('1963–2026 (splice, весь срок)', S.build_splice),
                         ('2003–2026 (modern)', S.build_modern)]:
        d = build()
        days = d[0]
        yrs = (days[-1] - days[0]).days / 365.25
        print(f"\n{'='*118}\n{wname}: {days[0]:%Y-%m-%d} … {days[-1]:%Y-%m-%d}, {yrs:.1f} года")
        print('=' * 118)
        print(HDR)
        r_core, _, tr_core = S.sim(*d, strict=True)
        out = sim_vd12(*d, m=1.0, k_fixed=1.0)
        assert np.allclose(out[0].values, r_core.values, rtol=1e-12), 'ручка k=1 не сводится к ядру'
        report('ядро без ручки (m=1, k=1)', days, r_core, tr_core, out[3], out[4])
        for m in (1.0, 1.25):
            for wt in (False, True):
                tag = f"ВТ-ВНИЗ m={m:g}" + (" (недел. ребаланс)" if wt else "")
                out = sim_vd12(*d, m=m, weekly_trade=wt)
                report(tag, days, out[0], out[2], out[3], out[4])
