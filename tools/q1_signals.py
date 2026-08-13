#!/usr/bin/env python3
"""Открытый вопрос №1 (ADD-FUT-ВТ-ВНИЗ §7): сверка сигналов выключателя.

Спецификация ВТ-ВНИЗ испытана на SPY / VFITX; пакет ядра ADD-FUT v1.5.3.1 считает
сигналы по своим прокси-рядам (акции: us-market Adjusted Close, срощенный с SPY adj;
облигации: реконструкция DGS10 → IEF adj). Скрипт считает месячные сигналы обоих
наборов ОДНИМ И ТЕМ ЖЕ правилом (функция sigs из sim_v13) и сравнивает помесячно.
"""
import sys
from pathlib import Path
import pandas as pd, numpy as np

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / 'pkg'
sys.path.insert(0, str(PKG))
import sim_v13 as S

D = PKG / 'data'


def monthly_state(px):
    """Правило §1 ядра: закрытие месяца выше средней из 12 последних месячных закрытий
    → нога включена в СЛЕДУЮЩЕМ месяце. Возвращает Series[bool], индекс — месяц действия."""
    me = px.resample('ME').last().ffill()
    sma = me.rolling(12).mean()
    s = (me > sma)[sma.notna()]
    return pd.Series(s.values, index=s.index + pd.offsets.MonthEnd(1))


def load():
    spy = pd.read_csv(D / 'spy_daily.csv', parse_dates=['date'], index_col='date')
    ief = pd.read_csv(D / 'ief_daily.csv', parse_dates=['date'], index_col='date')['adj']
    mk = pd.read_csv(D / 'us-market-daily-1885-2025.csv', parse_dates=['Date'],
                     index_col='Date').sort_index()
    recon = pd.read_csv(D / 'bond10_recon_daily.csv', parse_dates=['Date'], index_col='Date')['ret']
    vfitx = pd.read_csv(ROOT / 'data' / 'vfitx_daily.csv', parse_dates=['date'], index_col='date')['adj']

    # ряды ядра — ровно как в sim_v13.build_splice
    eq_core_r = pd.concat([mk['Adjusted Close'].pct_change(),
                           spy['adj'].pct_change()[spy.index > mk.index[-1]]])
    eq_core = (1 + eq_core_r.fillna(0)).cumprod()
    bond_core = (1 + pd.concat([recon[recon.index < ief.index[0]],
                                ief.pct_change().dropna()])).cumprod()
    return dict(spy=spy['adj'], eq_core=eq_core, vfitx=vfitx, bond_core=bond_core, ief=ief)


def compare(a, b, name_a, name_b, start=None, label=''):
    sa, sb = monthly_state(a), monthly_state(b)
    idx = sa.index.intersection(sb.index)
    if start is not None:
        idx = idx[idx >= pd.Timestamp(start)]
    sa, sb = sa.reindex(idx), sb.reindex(idx)
    diff = sa != sb
    n, nd = len(idx), int(diff.sum())
    print(f"\n=== {label}: {name_a}  vs  {name_b}")
    print(f"общая история: {idx[0]:%Y-%m} … {idx[-1]:%Y-%m}, {n} месяцев")
    print(f"расхождений: {nd} ({nd/n*100:.1f}%), совпадение {100-nd/n*100:.1f}%")
    print(f"доля 'включено': {name_a} {sa.mean()*100:.1f}%  |  {name_b} {sb.mean()*100:.1f}%")
    print(f"смен состояния: {name_a} {int((sa != sa.shift(1)).sum()) - 1}  |  "
          f"{name_b} {int((sb != sb.shift(1)).sum()) - 1}")
    if nd:
        d = diff[diff]
        runs, cur = [], [d.index[0]]
        for t in d.index[1:]:
            if (t.to_period('M') - cur[-1].to_period('M')).n == 1:
                cur.append(t)
            else:
                runs.append(cur); cur = [t]
        runs.append(cur)
        print(f"эпизодов расхождения: {len(runs)}, самый длинный {max(len(r) for r in runs)} мес")
        print("список эпизодов (месяц действия, кто включён):")
        for r in runs:
            who = f"{name_a}={'вкл' if sa[r[0]] else 'выкл'}/{name_b}={'вкл' if sb[r[0]] else 'выкл'}"
            print(f"  {r[0]:%Y-%m}" + (f" … {r[-1]:%Y-%m} ({len(r)} мес)" if len(r) > 1 else " (1 мес)")
                  + f"  {who}")
    return sa, sb


if __name__ == '__main__':
    d = load()
    print("Правило одно и то же для всех рядов: месячное закрытие > средней за 12 месяцев.")
    print("Окно испытаний ВТ-ВНИЗ: июнь 1994 — август 2026.\n")

    # самопроверка: воспроизводим ли эталонные сигналы ядра
    ref = pd.read_csv(D / 'signals_monthly.csv', parse_dates=[0], index_col=0)
    mine_eq = monthly_state(d['eq_core']).reindex(ref.index)
    mine_bd = monthly_state(d['bond_core']).reindex(ref.index)
    okeq = (mine_eq.astype(int).values == ref['leg_eq'].values).all()
    okbd = (mine_bd.astype(int).values == ref['leg_bond'].values).all()
    print(f"[самопроверка] сигналы ядра воспроизведены: акции {okeq}, облигации {okbd}"
          f"  ({len(ref)} мес)")

    compare(d['vfitx'], d['bond_core'], 'VFITX', 'ядро(recon+IEF)',
            start='1994-06-01', label='НОГА ОБЛИГАЦИЙ')
    compare(d['vfitx'], d['ief'], 'VFITX', 'IEF',
            label='НОГА ОБЛИГАЦИЙ (только современное окно)')
    compare(d['spy'], d['eq_core'], 'SPY', 'ядро(mkt+SPY)',
            start='1994-06-01', label='НОГА АКЦИЙ')
