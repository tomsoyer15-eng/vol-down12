#!/usr/bin/env python3
"""Третья нога — золото, равные веса. Проверочный прогон, не часть спецификации.

Движок ядра v1.5.3.1 (sim_v13) расширен на третью ногу:
  * сигнал ноги — тот же фильтр 12-месячной средней по месячным закрытиям, строгая
    конвенция (сигнал на закрытии T, сделка T+1);
  * целое число контрактов между сделками, полоса 3%, издержки 5 б.п. на изменение
    экспозиции, роллы 1 б.п., спред 0,5 п.п. — как в ядре;
  * кап суммарного номинала 2,00 (v1.6.0): если сумма целей выше капа, все цели
    масштабируются ПРОПОРЦИОНАЛЬНО, то есть равенство весов сохраняется.

Два режима данных:
  splice (по умолчанию) — ряды ядра 1963+ и лондонский фиксинг LBMA с 01.04.1968,
      контракт GC = 100 унций по фактической цене унции. Сигнал золота определён
      с апреля 1969 года (12 месяцев на среднюю).
  modern — ряды ядра с 2003 года и GLD (ETF) с 18.11.2004, окно с ноября 2005.
      Размер контракта приближён как 100 x 10 x цена GLD.

Даты, о которых нужно помнить при чтении результата: до 15.03.1968 цена золота была
закреплена Бреттон-Вудсом, фьючерс GC на COMEX запущен 31.12.1974, ZN — 1982, ES — 1997.
До этих дат ноги считаются по синтетическому контрактному эквиваленту — та же
конвенция, что ядро уже применяет к своему окну с 1963 года.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'r32build'))
import sim_v13 as S

GC_MULT = 100.0          # унций золота в контракте GC
CAP = 2.00
COMEX_GC_START = '1975-01-01'


def gold_rolls(index):
    """3 торговых дня до конца янв/мар/май/июл/сен/ноя (перед поставочными месяцами GC)."""
    out = set()
    for y in range(index[0].year, index[-1].year + 1):
        for m in [1, 3, 5, 7, 9, 11]:
            nm = pd.Timestamp(y + (1 if m == 11 else 0), (m % 12) + 1, 1)
            month = index[(index.year == y) & (index.month == m)]
            if len(month) == 0 or index[index >= nm].size == 0:
                continue
            pos = index.get_loc(month[-1])
            if pos >= 3:
                out.add(index[pos - 3])
    return out


def build(mode='splice', start=None):
    """Общее окно ядра и золота, с первого месяца, где сигнал золота определён."""
    D = ROOT / 'r32build' / 'data'
    if mode == 'splice':
        base = S.build_splice()
        g = pd.read_csv(ROOT / 'data' / 'gold_lbma_pm_daily.csv',
                        parse_dates=['date'], index_col='date')
        oz_per_unit = 1.0                      # ряд уже в долларах за унцию
    else:
        base = S.build_modern()
        g = pd.read_csv(D / 'gld_daily.csv', parse_dates=['date'], index_col='date')
        oz_per_unit = 10.0                     # 1 GLD ~ 1/10 унции
    days_f, re, rb, rfv, spy_close, dref, st_eq, st_bd = base
    st_eq = S.strict_states(st_eq, days_f)
    st_bd = S.strict_states(st_bd, days_f)

    me = g['adj'].resample('ME').last().ffill()
    sig = (me > me.rolling(12).mean()).loc[me.rolling(12).mean().notna()]
    first = (sig.index[0] + pd.offsets.MonthBegin(1)).to_period('M')

    st_gd = S.strict_states(S.sigs(g['adj'], days_f), days_f)
    gp = g['adj'].reindex(days_f).ffill()
    rg = gp.pct_change().fillna(0).values
    gpx = (g['close'].reindex(days_f).ffill() * oz_per_unit).values

    m = (days_f.to_period('M') >= first)
    if start is not None:
        m &= (days_f >= pd.Timestamp(start))
    days = days_f[m]
    sl = lambda a: np.asarray(a)[m]
    return (days, sl(re), sl(rb), sl(rfv), sl(spy_close), sl(dref), sl(st_eq), sl(st_bd),
            sl(rg), sl(gpx), sl(st_gd))


def sim(d, w=(1.0, 1.0, 1.0), gold=True, cap=CAP, spread_pp=0.5, cap0=10_000_000.0):
    days, re, rb, rfv, spy_close, dref, st_eq, st_bd, rg, gpx, st_gd = d
    if not gold:
        st_gd = np.zeros(len(days), dtype=bool)
    n = len(days)
    sd = spread_pp / 100 / 252
    e = cap0
    n_e = n_b = n_g = 0
    unit_is_mes = False
    nav = np.empty(n)
    rolls = S.roll_days_calendar(days)
    grolls = gold_rolls(days)
    prev = (None, None, None)
    D_fix = dref[0]
    exec_days = 0
    maxlev = 0.0
    prev_lev = 0.0
    lev_sum = 0.0
    for i, dte in enumerate(days):
        j = max(i - 1, 0)
        if (not unit_is_mes) and dte >= S.MES_START:
            n_e *= 10
            unit_is_mes = True
        mult = S.ES_MULT / 10 if unit_is_mes else S.ES_MULT
        unit_e = mult * spy_close[j]
        sw = (st_eq[i] != prev[0], st_bd[i] != prev[1], st_gd[i] != prev[2])
        roll_b = dte in rolls
        roll_g = dte in grolls
        if roll_b or sw[1]:
            D_fix = dref[j]
        unit_b = (S.ZN_MODEL_PX_EQ * S.CTD_RATIO * D_fix * 1e-4) / (dref[j] * 1e-4)
        units = np.array([unit_e, unit_b, GC_MULT * gpx[j]])

        tgt = np.array([w[0] * st_eq[i], w[1] * st_bd[i], w[2] * st_gd[i]]) * e
        gross = tgt.sum()
        if gross > cap * e:                       # пропорциональное урезание — веса равны
            tgt *= cap * e / gross
        n0 = np.array([n_e, n_b, n_g], dtype=float)
        cur = n0.copy()
        exp = cur * units
        for k, force in enumerate([sw[0], sw[1] or roll_b, sw[2] or roll_g]):
            if force or abs(tgt[k] - exp[k]) > S.BAND * e:
                new = round(tgt[k] / units[k])
                if new != cur[k]:
                    e -= S.COST * abs(new - cur[k]) * units[k]
                    cur[k] = new
                    exp[k] = new * units[k]

        def e_after(x):
            ea = e - S.COST * ((np.abs(x - n0) - np.abs(cur - n0)) * units).sum()
            if roll_b: ea -= S.ROLL_BP * x[1] * units[1]
            if roll_g: ea -= S.ROLL_BP * x[2] * units[2]
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
        n_e, n_b, n_g = cur
        prev = (st_eq[i], st_bd[i], st_gd[i])
        exp = cur * units
        if (cur != n0).any() or (roll_b and n_b) or (roll_g and n_g):
            exec_days += 1
        if roll_b: e -= S.ROLL_BP * exp[1]
        if roll_g: e -= S.ROLL_BP * exp[2]
        if i == 0:
            nav[i] = e
        else:
            rr = np.array([re[i], rb[i], rg[i]])
            e = e * (1 + rfv[i]) + (exp * (rr - rfv[i] - sd)).sum()
            nav[i] = e
        mc = S.ES_MULT / 10 if unit_is_mes else S.ES_MULT
        uc = np.array([mc * spy_close[i],
                       (S.ZN_MODEL_PX_EQ * S.CTD_RATIO * D_fix * 1e-4) / (dref[i] * 1e-4),
                       GC_MULT * gpx[i]])
        prev_lev = (cur * uc).sum() / e
        lev_sum += prev_lev
        maxlev = max(maxlev, prev_lev)
    s = pd.Series(nav, index=days)
    r = s.pct_change(); r.iloc[0] = nav[0] / cap0 - 1
    return r, dict(exec_days=exec_days, maxlev=maxlev, avglev=lev_sum / n)


def met(r, lo=None, hi=None):
    x = r if lo is None else r[(r.index >= lo) & (r.index < hi)]
    eq = (1 + x).cumprod()
    yrs = (x.index[-1] - x.index[0]).days / 365.25
    v = x.std() * np.sqrt(252) * 100
    c = (eq.iloc[-1] ** (1 / yrs) - 1) * 100
    return c, v, (eq / eq.cummax() - 1).min() * 100, c / v


VARIANTS = [
    ('две ноги 1:1 (ядро)',             (1.0, 1.0, 0.0), False),
    ('три ноги 1:1:1, кап 2,00',        (1.0, 1.0, 1.0), True),
    ('три ноги 2/3 каждая (номинал 2)', (2/3, 2/3, 2/3), True),
]

PERIODS = [('1969-01-01', '1981-01-01', '1969-1980'),
           ('1981-01-01', '2001-01-01', '1981-2000'),
           ('2001-01-01', '2012-01-01', '2001-2011'),
           ('2012-01-01', '2019-01-01', '2012-2018'),
           ('2019-01-01', '2027-01-01', '2019-2026')]


def report(d, title, periods=PERIODS):
    days = d[0]
    yrs = (days[-1] - days[0]).days / 365.25
    print(f"\n=== {title}: {days[0].date()} - {days[-1].date()} ({yrs:.1f} лет) ===")
    print(f"доля дней «нога включена»: акции {d[6].mean()*100:.0f}%, "
          f"облигации {d[7].mean()*100:.0f}%, золото {d[10].mean()*100:.0f}%")
    rr = pd.DataFrame({'акции': d[1], 'обл': d[2], 'золото': d[8]}, index=days)
    print("корреляция дневных ходов: " + ", ".join(
        f"{a}/{b} {rr[a].corr(rr[b]):+.2f}" for a, b in
        [('акции', 'обл'), ('акции', 'золото'), ('обл', 'золото')]))
    print(f"качка ног: акции {d[1].std()*np.sqrt(252)*100:.1f}%, "
          f"облигации {d[2].std()*np.sqrt(252)*100:.1f}%, "
          f"золото {d[8].std()*np.sqrt(252)*100:.1f}%\n")
    print(f"{'вариант':<34}{'CAGR':>8}{'качка':>8}{'MaxDD':>9}{'D/V':>7}"
          f"{'ср.плечо':>10}{'макс':>7}{'сд/год':>8}")
    out = {}
    for name, w, gold in VARIANTS:
        r, st = sim(d, w=w, gold=gold)
        out[name] = r
        c, v, dd, dv = met(r)
        print(f"{name:<34}{c:>7.2f}%{v:>7.2f}%{dd:>8.1f}%{dv:>7.2f}"
              f"{st['avglev']:>10.2f}{st['maxlev']:>7.2f}{st['exec_days']/yrs:>8.1f}")
    per = [p for p in periods if (days >= pd.Timestamp(p[0])).any()
           and (days < pd.Timestamp(p[1])).any()]
    print("\nпо подпериодам, CAGR / MaxDD:")
    print(f"{'вариант':<34}" + ''.join(f"{p[2]:>18}" for p in per))
    for name in out:
        row = ''.join(f"{met(out[name], a, b)[0]:>10.2f}% /{met(out[name], a, b)[2]:>5.0f}%"
                      for a, b, _ in per)
        print(f"{name:<34}{row}")
    return out


if __name__ == '__main__':
    mode = 'modern' if '--modern' in sys.argv else 'splice'
    if mode == 'modern':
        report(build('modern'), 'GLD, ряды ядра с 2003 года',
               periods=[p for p in PERIODS if p[0] >= '2001-01-01'])
    else:
        report(build('splice'), 'LBMA + ряды ядра, весь доступный ряд золота')
        report(build('splice', start=COMEX_GC_START),
               'то же, но с запуска фьючерса GC на COMEX (31.12.1974)')
