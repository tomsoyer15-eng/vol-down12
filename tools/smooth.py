#!/usr/bin/env python3
"""Плавное подключение ноги вместо ступеньки. Проверочный прогон, не часть спецификации.

Ядро включает ногу бинарно: цена выше 12-месячной средней — вся нога, ниже — ноль.
Просадки ядра оказались в основном пилой (внутри них фильтр переключается втрое чаще,
чем в спокойное время), поэтому проверяется, снимает ли пилу плавный вход.

ПРАВИЛА ЗАДАНЫ АПРИОРИ, параметры не подбирались:

  ансамбль      w = доля окон из {6,9,12,15,18} мес, выше средней по которым стоит цена.
                Окна симметричны вокруг зафиксированных в спецификации 12 месяцев с шагом 3.
                Обоснование: снимает произвол выбора одного окна, не вводя нового числа.
  лестница      w = среднее трёх последних месячных сигналов 12-месячной средней.
                Обоснование: квартал подтверждения; ступенька превращается в три ступени.
  наклон        w = clip((P/SMA - 1)/s, 0, 1), s — типичный разброс (P/SMA-1) по прошлым
                данным. Обоснование: подключаться пропорционально силе сигнала в его же
                собственных единицах, полностью — при одном типичном отклонении.
  логистика     w = 1/(1+exp(-(P/SMA-1)/s)). То же, но гладко и симметрично вокруг
                пересечения, где w = 0,5. Это и есть «экспоненциальное подключение».

Механика сделок ядра сохранена: целое число контрактов, полоса 3%, издержки 5 б.п.,
роллы 1 б.п., спред 0,5 п.п., кап суммарного номинала 2,00. Сделка принудительна только
на полном входе и полном выходе (w пересекает ноль) — промежуточные изменения веса
проходят через полосу 3%, как и положено.

s оценивается только по прошлым данным (расширяющееся окно, минимум 60 месяцев, сдвиг
на месяц), подсмотра вперёд нет.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'r32build'))
import sim_v13 as S

SPEC_LB = 12
ENSEMBLE = [6, 9, 12, 15, 18]
LADDER = 3
MIN_HIST = 60


def build():
    D = S.D
    spy = pd.read_csv(D / 'spy_daily.csv', parse_dates=['date'], index_col='date')
    ief = pd.read_csv(D / 'ief_daily.csv', parse_dates=['date'], index_col='date')['adj']
    mk = pd.read_csv(D / 'us-market-daily-1885-2025.csv', parse_dates=['Date']).set_index('Date').sort_index()
    eq_r = pd.concat([mk['Adjusted Close'].pct_change(), spy['adj'].pct_change()[spy.index > mk.index[-1]]])
    recon = pd.read_csv(D / 'bond10_recon_daily.csv', parse_dates=['Date'], index_col='Date')['ret']
    bond_idx = (1 + pd.concat([recon[recon.index < ief.index[0]],
                               ief.pct_change().dropna()])).cumprod().reindex(eq_r.index).ffill()
    days = eq_r.index[eq_r.index >= pd.Timestamp('1963-03-01')]
    re = eq_r.reindex(days).fillna(0).values
    rb = bond_idx.pct_change().reindex(days).fillna(0).values
    rfv = S.rf_series(days).values
    eq_lvl = (1 + pd.Series(re, index=days)).cumprod()
    synth_close = (spy['close'].iloc[-1] * eq_lvl / eq_lvl.iloc[-1]).values
    y = pd.read_csv(D / 'dgs10.csv', parse_dates=['observation_date']).set_index('observation_date')['DGS10'].dropna() / 100
    Dref = pd.Series([S.dur(v) for v in y.values], index=y.index).reindex(days).ffill().bfill()
    return (days, re, rb, rfv, synth_close, Dref.values,
            (1 + eq_r.fillna(0)).cumprod(), bond_idx.dropna())


def to_days(mw, days):
    """Месячный вес -> дневной, по конвенции ядра: сигнал месяца M действует с M+1,
    затем сдвиг на день (сигнал на закрытии T, сделка T+1)."""
    sm = pd.Series(mw.values, index=(mw.index + pd.offsets.MonthBegin(1)).to_period('M'))
    v = sm.reindex(days.to_period('M')).ffill()
    # НЕ bfill. Он заполнял начало окна ПЕРВЫМ ВЫЧИСЛЕННЫМ В БУДУЩЕМ весом — до 589 дней
    # на окне ядра, то есть подсматривание вперёд. Пока веса нет, нога выключена.
    return pd.Series(v.values, index=days).shift(1).fillna(0).clip(0, 1).values


def weights(px, days, kind):
    me = px.resample('ME').last().ffill()
    if kind == 'бинарное (ядро)':
        w = (me > me.rolling(SPEC_LB).mean()).astype(float)[me.rolling(SPEC_LB).mean().notna()]
    elif kind == 'ансамбль окон':
        parts = [(me > me.rolling(n).mean()).astype(float) for n in ENSEMBLE]
        w = pd.concat(parts, axis=1).mean(axis=1)[me.rolling(max(ENSEMBLE)).mean().notna()]
    elif kind == 'лестница, 3 мес':
        b = (me > me.rolling(SPEC_LB).mean()).astype(float)[me.rolling(SPEC_LB).mean().notna()]
        w = b.rolling(LADDER).mean().dropna()
    else:
        sma = me.rolling(SPEC_LB).mean()
        g = (me / sma - 1).dropna()
        s = g.expanding(MIN_HIST).std().shift(1)          # только прошлые данные
        z = (g / s).dropna()
        w = z.clip(0, 1) if kind == 'наклон по силе' else 1 / (1 + np.exp(-z))
    return to_days(w, days)


def sim(d, w_eq, w_bd, cap=2.0, spread_pp=0.5, cap0=10_000_000.0):
    days, re, rb, rfv, spy_close, dref = d[:6]
    n = len(days); sd = spread_pp / 100 / 252
    e = cap0; n_e = n_b = 0; unit_is_mes = False
    nav = np.empty(n); rolls = S.roll_days_calendar(days)
    D_fix = dref[0]; exec_days = 0; maxlev = 0.0; prev_lev = 0.0; lev_sum = 0.0
    for i, dte in enumerate(days):
        j = max(i - 1, 0)
        if (not unit_is_mes) and dte >= S.MES_START:
            n_e *= 10; unit_is_mes = True
        mult = S.ES_MULT / 10 if unit_is_mes else S.ES_MULT
        unit_e = mult * spy_close[j]
        roll = dte in rolls
        # полный вход/выход ноги — принудительная сделка, как переключение в ядре
        edge_e = (w_eq[i] == 0) != (w_eq[j] == 0) if i else w_eq[i] > 0
        edge_b = (w_bd[i] == 0) != (w_bd[j] == 0) if i else w_bd[i] > 0
        if roll or edge_b:
            D_fix = dref[j]
        unit_b = (S.ZN_MODEL_PX_EQ * S.CTD_RATIO * D_fix * 1e-4) / (dref[j] * 1e-4)
        units = np.array([unit_e, unit_b])
        tgt = np.array([w_eq[i], w_bd[i]]) * e
        gross = tgt.sum()
        if gross > cap * e:
            tgt *= cap * e / gross
        n0 = np.array([n_e, n_b], dtype=float); cur = n0.copy(); exp = cur * units
        for k, force in enumerate([edge_e, edge_b or roll]):
            if force or abs(tgt[k] - exp[k]) > S.BAND * e:
                new = round(tgt[k] / units[k])
                if new != cur[k]:
                    e -= S.COST * abs(new - cur[k]) * units[k]
                    cur[k] = new; exp[k] = new * units[k]

        def e_after(x):
            ea = e - S.COST * ((np.abs(x - n0) - np.abs(cur - n0)) * units).sum()
            if roll: ea -= S.ROLL_BP * x[1] * units[1]
            return ea

        if (i > 0 and prev_lev > cap) or (cur * units).sum() > cap * e_after(cur):
            x = np.floor(tgt / units)
            if i > 0 and prev_lev > cap:
                x = np.minimum(cur, x)
            while (x * units).sum() > cap * e_after(x) and x.sum() > 0:
                x[int(np.argmax(np.where(x > 0, units, -np.inf)))] -= 1
            if not np.array_equal(x, cur):
                e -= S.COST * ((np.abs(x - n0) - np.abs(cur - n0)) * units).sum()
                cur = x
        n_e, n_b = cur
        exp = cur * units
        if (cur != n0).any() or (roll and n_b):
            exec_days += 1
        if roll: e -= S.ROLL_BP * exp[1]
        if i == 0:
            nav[i] = e
        else:
            e = e * (1 + rfv[i]) + (exp * (np.array([re[i], rb[i]]) - rfv[i] - sd)).sum()
            nav[i] = e
        mc = S.ES_MULT / 10 if unit_is_mes else S.ES_MULT
        uc = np.array([mc * spy_close[i],
                       (S.ZN_MODEL_PX_EQ * S.CTD_RATIO * D_fix * 1e-4) / (dref[i] * 1e-4)])
        prev_lev = (cur * uc).sum() / e
        lev_sum += prev_lev; maxlev = max(maxlev, prev_lev)
    s = pd.Series(nav, index=days); r = s.pct_change(); r.iloc[0] = nav[0] / cap0 - 1
    return r, dict(exec_days=exec_days, maxlev=maxlev, avglev=lev_sum / n)


def met(r, lo=None, hi=None):
    x = r if lo is None else r[(r.index >= lo) & (r.index < hi)]
    eq = (1 + x).cumprod(); yrs = (x.index[-1] - x.index[0]).days / 365.25
    v = x.std() * np.sqrt(252) * 100; c = (eq.iloc[-1] ** (1 / yrs) - 1) * 100
    return c, v, (eq / eq.cummax() - 1).min() * 100, c / v


def episodes(r, thresh=0.15):
    eq = (1 + r).cumprod(); dd = eq / eq.cummax() - 1
    out = []; inep = False
    for t, v in dd.items():
        if not inep and v < -1e-9: inep = True; a = t; lo = v; b = t
        elif inep:
            if v < lo: lo = v; b = t
            if v >= -1e-9: out.append((a, b, lo)); inep = False
    if inep: out.append((a, b, lo))
    return [o for o in out if o[2] < -thresh]


KINDS = ['бинарное (ядро)', 'ансамбль окон', 'лестница, 3 мес', 'наклон по силе', 'логистика']

if __name__ == '__main__':
    d = build()
    days = d[0]; yrs = (days[-1] - days[0]).days / 365.25
    W = {k: (weights(d[6], days, k), weights(d[7], days, k)) for k in KINDS}

    r0, st0 = sim(d, *W['бинарное (ядро)'])
    rc, _, tr = S.sim(*S.build_splice())
    print(f"сверка с ядром: CAGR {met(r0)[0]:.2f}% против {met(rc)[0]:.2f}%, "
          f"макс расхождение дневного хода {(r0 - rc).abs().max():.1e}\n")

    print(f"{'правило':<20}{'ср.вес':>8}{'CAGR':>8}{'качка':>8}{'MaxDD':>9}{'D/V':>7}"
          f"{'ср.плечо':>10}{'сд/год':>8}{'эпизодов>15%':>14}")
    res = {}
    for k in KINDS:
        r, st = sim(d, *W[k]); res[k] = (r, st)
        c, v, dd, dv = met(r)
        aw = (W[k][0].mean() + W[k][1].mean()) / 2
        print(f"{k:<20}{aw:>8.2f}{c:>7.2f}%{v:>7.2f}%{dd:>8.1f}%{dv:>7.2f}"
              f"{st['avglev']:>10.2f}{st['exec_days']/yrs:>8.1f}{len(episodes(r)):>14}")

    print("\nто же, но с уравненным средним плечом (веса умножены на константу):")
    print(f"{'правило':<20}{'множитель':>11}{'CAGR':>8}{'качка':>8}{'MaxDD':>9}{'D/V':>7}"
          f"{'ср.плечо':>10}{'сд/год':>8}{'эпизодов>15%':>14}")
    target = st0['avglev']; matched = {}
    for k in KINDS:
        lo, hi = 0.5, 3.0
        for _ in range(14):
            m = (lo + hi) / 2
            _, s2 = sim(d, np.clip(W[k][0] * m, 0, 1.5), np.clip(W[k][1] * m, 0, 1.5))
            lo, hi = (m, hi) if s2['avglev'] < target else (lo, m)
        m = (lo + hi) / 2
        r, st = sim(d, np.clip(W[k][0] * m, 0, 1.5), np.clip(W[k][1] * m, 0, 1.5))
        matched[k] = r
        c, v, dd, dv = met(r)
        print(f"{k:<20}{m:>11.2f}{c:>7.2f}%{v:>7.2f}%{dd:>8.1f}%{dv:>7.2f}"
              f"{st['avglev']:>10.2f}{st['exec_days']/yrs:>8.1f}{len(episodes(r)):>14}")

    print("\nскользящие 10-летние окна против ядра (при уравненном плече):")
    Wn = 2520
    cs = lambda r: np.concatenate([[0], np.cumsum(np.log1p(r.values))])
    i0 = np.arange(0, len(days) - Wn); i1 = i0 + Wn
    yy = np.array([(days[b] - days[a]).days / 365.25 for a, b in zip(i0, i1)])
    f = lambda r: (np.exp((cs(r)[i1 + 1] - cs(r)[i0 + 1]) / yy) - 1) * 100
    base = f(matched['бинарное (ядро)'])
    for k in KINDS[1:]:
        dd_ = f(matched[k]) - base
        print(f"  {k:<20} впереди {np.mean(dd_>0)*100:>5.1f}% окон, "
              f"медиана {np.median(dd_):+6.2f} п.п., худшее {dd_.min():+6.2f}, лучшее {dd_.max():+6.2f}")
