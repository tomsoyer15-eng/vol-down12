#!/usr/bin/env python3
"""Пересечение двух скользящих средних вместо «цена против средней». Проверочный прогон.

Ядро — частный случай: быстрая средняя длиной 1 месяц (сама месячная цена) против
медленной в 12. Поэтому тест ставится так, чтобы не плодить свободных параметров:

  семейство А — медленная зафиксирована на 12 месяцах (значение спецификации),
                быстрая = 1 (ядро), 2, 3, 4, 6. Вопрос: помогает ли сгладить
                быструю сторону, не трогая масштаб системы.
  семейство Б — отношение быстрой к медленной держится 1:4, меняется масштаб пары:
                (2,8), (3,12), (4,16), (6,24). Вопрос: зависит ли ответ от масштаба,
                то есть механизм это или случайность конкретной пары.

Ни одна пара не выбиралась по результату. Механика сделок, полоса 3%, издержки, роллы,
кап 2,00 — как в ядре (движок из tools/smooth.py).

Окно с 01.06.1964: ряд облигаций начинается 03.01.1962, и 24-месячной средней нужен
разгон. Все варианты считаются на одном окне, иначе сравнение нечестное.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'r32build'))
import smooth as M

START = pd.Timestamp('1964-06-01')
FAMILY_A = [(1, 12), (2, 12), (3, 12), (4, 12), (6, 12)]
FAMILY_B = [(2, 8), (3, 12), (4, 16), (6, 24)]


def cross_weights(px, days, fast, slow):
    me = px.resample('ME').last().ffill()
    f = me.rolling(fast).mean() if fast > 1 else me
    s = me.rolling(slow).mean()
    ok = f.notna() & s.notna()
    return M.to_days((f > s).astype(float)[ok], days)


def slice_d(d, m):
    return (d[0][m],) + tuple(np.asarray(a)[m] for a in d[1:6])


def switches(w):
    return int((np.diff((np.asarray(w) > 0).astype(int)) != 0).sum())


def run(d, days, w_eq, w_bd, mult=1.0):
    r, st = M.sim(d, np.clip(w_eq * mult, 0, 1.5), np.clip(w_bd * mult, 0, 1.5))
    return r, st


def match(d, days, w_eq, w_bd, target):
    lo, hi = 0.2, 3.0
    for _ in range(16):
        m = (lo + hi) / 2
        _, s = run(d, days, w_eq, w_bd, m)
        lo, hi = (m, hi) if s['avglev'] < target else (lo, m)
    return (lo + hi) / 2


def table(d, days, pairs, W, title, base_key, matched=False, target=None):
    yrs = (days[-1] - days[0]).days / 365.25
    print(f"\n{title}")
    print(f"{'быстрая/медленная':<20}{'множ.':>7}{'CAGR':>8}{'качка':>8}{'MaxDD':>9}{'D/V':>7}"
          f"{'плечо':>8}{'сигн/год':>10}{'сд/год':>8}{'эп>15%':>8}")
    out = {}
    for p in pairs:
        we, wb = W[p]
        m = match(d, days, we, wb, target) if matched else 1.0
        r, st = run(d, days, we, wb, m)
        out[p] = r
        c, v, dd, dv = M.met(r)
        sw = (switches(we) + switches(wb)) / yrs
        tag = f"{p[0]} / {p[1]}" + (" — ядро" if p == base_key else "")
        print(f"{tag:<20}{m:>7.2f}{c:>7.2f}%{v:>7.2f}%{dd:>8.1f}%{dv:>7.2f}"
              f"{st['avglev']:>8.2f}{sw:>10.2f}{st['exec_days']/yrs:>8.1f}{len(M.episodes(r)):>8}")
    return out


def rolling_vs(out, base_key, days, label):
    Wn = 2520
    cs = lambda r: np.concatenate([[0], np.cumsum(np.log1p(r.values))])
    i0 = np.arange(0, len(days) - Wn); i1 = i0 + Wn
    yy = np.array([(days[b] - days[a]).days / 365.25 for a, b in zip(i0, i1)])
    f = lambda r: (np.exp((cs(r)[i1 + 1] - cs(r)[i0 + 1]) / yy) - 1) * 100
    base = f(out[base_key])
    print(f"\nскользящие 10-летние окна против ядра ({label}):")
    for p, r in out.items():
        if p == base_key: continue
        dd_ = f(r) - base
        print(f"  {str(p[0])+'/'+str(p[1]):<10} впереди {np.mean(dd_>0)*100:>5.1f}% окон, "
              f"медиана {np.median(dd_):+6.2f} п.п., худшее {dd_.min():+6.2f}, лучшее {dd_.max():+6.2f}")


if __name__ == '__main__':
    d0 = M.build()
    days_f = d0[0]; m = days_f >= START
    d = slice_d(d0, m); days = d[0]
    pairs = sorted(set(FAMILY_A) | set(FAMILY_B))
    W = {p: (cross_weights(d0[6], days_f, *p)[m], cross_weights(d0[7], days_f, *p)[m]) for p in pairs}

    print(f"окно {days[0].date()} - {days[-1].date()} ({(days[-1]-days[0]).days/365.25:.1f} лет)")
    _, st_core = run(d, days, *W[(1, 12)])
    target = st_core['avglev']

    a1 = table(d, days, FAMILY_A, W, "СЕМЕЙСТВО А: медленная 12, быстрая меняется — своё плечо", (1, 12))
    a2 = table(d, days, FAMILY_A, W, "то же, плечо уравнено с ядром", (1, 12), matched=True, target=target)
    rolling_vs(a2, (1, 12), days, "уравненное плечо")

    b1 = table(d, days, FAMILY_B, W, "СЕМЕЙСТВО Б: отношение 1:4, масштаб меняется — своё плечо", (3, 12))
    b2 = table(d, days, FAMILY_B, W, "то же, плечо уравнено с ядром", (3, 12), matched=True, target=target)
