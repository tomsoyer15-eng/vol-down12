#!/usr/bin/env python3
"""Ядро ADD-FUT глазами инвестора с базой в франке или евро: без хеджа и с хеджем.

Торговые решения ядра от базовой валюты не зависят — стратегия и маржа долларовые,
поэтому прогон ядра делается один раз, а дальше пересчитывается результат:

  без хеджа:  (1 + r_долл) x (1 + изменение курса) - 1
  с хеджем:   r_долл - долларовая ставка + ставка базовой валюты

Вторая строка — это покрытый процентный паритет: полностью захеджированный инвестор
получает те же избыточные доходы ног, но денежную часть капитала уже по своей ставке.
Стоимость хеджа тут и сидит — в дифференциале ставок.

Ставки берутся по ЦБ (см. tools/fetch_fx_bis.py), это приближение к денежному рынку;
кросс-валютный базис не учтён и добавляет евро-инвестору ещё 0,1-0,3 п.п. в год.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'r32build'))
import sim_v13 as S

W = 2520  # ~10 лет торговых дней


def core_usd():
    d = S.build_splice()
    r, nav, tr = S.sim(*d)
    return r, pd.Series(S.rf_series(d[0]), index=d[0])


def variants(r_usd, rf_usd, cur):
    fx = pd.read_csv(ROOT / 'data' / f'fx_{cur}usd_daily.csv',
                     parse_dates=['date'], index_col='date')['per_usd']
    pol = pd.read_csv(ROOT / 'data' / f'rate_{cur}_policy.csv',
                      parse_dates=['date'], index_col='date')['rate']
    days = r_usd.index[r_usd.index >= fx.index[0]]
    s = fx.reindex(days.union(fx.index)).ffill().reindex(days)
    fx_ret = s.pct_change().fillna(0)
    rf_base = ((1 + pol.reindex(pol.index.union(days)).ffill().reindex(days) / 100) ** (1 / 252) - 1)
    r_u = r_usd.reindex(days); rfu = rf_usd.reindex(days)
    return pd.DataFrame({
        'долларовый результат': r_u,
        'без хеджа': (1 + r_u) * (1 + fx_ret) - 1,
        'с хеджем': r_u - rfu + rf_base,
    }), s, (rfu - rf_base)


def met(x):
    eq = (1 + x).cumprod(); yrs = (x.index[-1] - x.index[0]).days / 365.25
    v = x.std() * np.sqrt(252) * 100; c = (eq.iloc[-1] ** (1 / yrs) - 1) * 100
    return c, v, (eq / eq.cummax() - 1).min() * 100, c / v


def rolling(df, days):
    cs = {k: np.concatenate([[0], np.cumsum(np.log1p(df[k].values))]) for k in df}
    i0 = np.arange(0, len(days) - W); i1 = i0 + W
    yrs = np.array([(days[b] - days[a]).days / 365.25 for a, b in zip(i0, i1)])
    return {k: (np.exp((c[i1 + 1] - c[i0 + 1]) / yrs) - 1) * 100 for k, c in cs.items()}, days[i0]


if __name__ == '__main__':
    r_usd, rf_usd = core_usd()
    print(f"ядро в долларах: {r_usd.index[0].date()} - {r_usd.index[-1].date()}, "
          f"CAGR {met(r_usd)[0]:.2f}%, качка {met(r_usd)[1]:.2f}%, MaxDD {met(r_usd)[2]:.1f}%")

    for cur, nm in [('chf', 'ФРАНК'), ('eur', 'ЕВРО')]:
        df, s, diff = variants(r_usd, rf_usd, cur)
        days = df.index; yrs = (days[-1] - days[0]).days / 365.25
        fxc = (s.iloc[-1] / s.iloc[0]) ** (1 / yrs) * 100 - 100
        print(f"\n=== БАЗА — {nm}: {days[0].date()} - {days[-1].date()} ({yrs:.1f} лет) ===")
        print(f"курс: {s.iloc[0]:.4f} -> {s.iloc[-1]:.4f} за доллар, то есть доллар "
              f"{'слабел' if fxc < 0 else 'крепчал'} на {abs(fxc):.2f}% в год")
        print(f"средний дифференциал ставок (долларовая минус базовая): "
              f"{diff.mean()*252*100:+.2f} п.п. в год — столько в среднем стоил хедж")
        print(f"\n{'вариант':<24}{'CAGR':>8}{'качка':>8}{'MaxDD':>9}{'D/V':>7}")
        for k in df:
            c, v, dd, dv = met(df[k])
            print(f"{k:<24}{c:>7.2f}%{v:>7.2f}%{dd:>8.1f}%{dv:>7.2f}")

        g, starts = rolling(df, days)
        d = g['с хеджем'] - g['без хеджа']
        print(f"\nскользящие 10-летние окна ({len(d)} шт.): хедж впереди {np.mean(d>0)*100:.1f}%, "
              f"медиана {np.median(d):+.2f} п.п., худшее {d.min():+.2f}, лучшее {d.max():+.2f}")
        for lo, lbl in [('1980-01-01', 'старт 1980+'), ('2000-01-01', 'старт 2000+')]:
            m = starts >= pd.Timestamp(lo)
            if m.sum():
                print(f"   {lbl:<14} окон {m.sum():>5}  хедж впереди {np.mean(d[m]>0)*100:>5.1f}%  "
                      f"медиана {np.median(d[m]):+6.2f} п.п.")
        for k in ['без хеджа', 'с хеджем']:
            q = np.percentile(g[k], [10, 50, 90])
            print(f"   {k:<12} 10-летняя доходность: 10% {q[0]:>6.2f}  медиана {q[1]:>6.2f}  90% {q[2]:>6.2f}")
