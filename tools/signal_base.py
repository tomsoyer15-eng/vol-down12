#!/usr/bin/env python3
"""Две проверки самого сигнала, а не надстроек над ним.

А. ФАЗА НАБЛЮДЕНИЯ. Сигнал ядра снимается на закрытии последнего дня календарного месяца.
Это условность календаря, а не свойство рынка. Если результат держится только на этой
условности, он случаен. Здесь месяц заменяется периодом в 21 торговый день, а точка отсчёта
сдвигается по всем 21 возможным фазам. Разброс по фазам — прямая мера хрупкости ядра.
Подгонки нет: ни одна фаза не выбирается, смотрится только разброс.

Б. БАЗА СИГНАЛА. Ядро снимает сигнал с индекса ПОЛНОЙ доходности, а торгует фьючерс, доход
которого — доходность СВЕРХ денежной ставки. При ставке 10% годовых индекс полной
доходности растёт на 10% в год из одной только ставки, и правило «цена выше средней за 12
месяцев» смещено во включённое состояние тем сильнее, чем выше ставка. Проверяется снятие
сигнала с индекса избыточной доходности. Основание априорное: тайминг должен применяться к
той величине, которая торгуется.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'r32build'))
import sim_v13 as S
import smooth as M
import rebal as R
import build_1885 as B

PER, LB = 21, 12          # период в торговых днях, окно средней в периодах


def w_phase(px, days, phase):
    """Веса при наблюдении каждые 21 торговый день, начиная с фазы phase."""
    v = px.reindex(days).ffill().values
    obs = np.arange(phase, len(days), PER)
    w = np.zeros(len(days))
    for k in range(LB, len(obs)):
        i = obs[k]
        # Текущее наблюдение ВХОДИТ в среднюю — как в ядре (me > me.rolling(12).mean()).
        # Прежняя редакция брала 12 ПРЕДЫДУЩИХ, то есть окно на период длиннее.
        on = v[i] > v[obs[k - LB + 1:k + 1]].mean()
        j = obs[k + 1] if k + 1 < len(obs) else len(days) - 1
        w[i + 1:j + 1] = float(on)        # сделка на следующий день после наблюдения
    return w


def run(d, we, wb):
    r, st = R.sim(d, we, wb)
    return M.met(r)


def prep(d):
    days = d[0]
    if isinstance(d[6], np.ndarray):
        eq = (1 + pd.Series(d[1], index=days)).cumprod()
        bd = (1 + pd.Series(d[2], index=days)).cumprod()
    else:
        eq, bd = d[6].reindex(days).ffill(), d[7].reindex(days).ffill()
    return days, eq, bd


if __name__ == '__main__':
    for nm, d in (('дневное окно ядра', M.build()), ('полное окно', B.build())):
        days, eq, bd = prep(d)
        print(f"\n=== {nm}: {days[0]:%Y}–{days[-1]:%Y} ===")
        base = run(d, M.weights(d[6], days, 'бинарное (ядро)') if not isinstance(d[6], np.ndarray)
                   else S.strict_states(d[6] > 0, days).astype(float),
                   M.weights(d[7], days, 'бинарное (ядро)') if not isinstance(d[7], np.ndarray)
                   else S.strict_states(d[7] > 0, days).astype(float))
        print(f"календарный месяц (ядро): CAGR {base[0]:.2f}%, качка {base[1]:.2f}%, "
              f"MaxDD {base[2]:.1f}%, D/V {base[3]:.2f}")

        # --- А: фазы ---
        res = [run(d, w_phase(eq, days, p), w_phase(bd, days, p)) for p in range(PER)]
        c = np.array([x[0] for x in res]); dd = np.array([x[2] for x in res])
        dv = np.array([x[3] for x in res])
        print(f"21 фаза наблюдения:  CAGR {c.min():.2f}–{c.max():.2f}% "
              f"(медиана {np.median(c):.2f}%, разброс {c.max()-c.min():.2f} п.п.)")
        print(f"                     MaxDD {dd.min():.1f}…{dd.max():.1f}%, "
              f"D/V {dv.min():.2f}–{dv.max():.2f}, доля фаз с CAGR ниже 9%: "
              f"{(c < 9).mean()*100:.0f}%")

        # --- Б: база сигнала ---
        rf = pd.Series(d[3], index=days)
        ex_eq = (1 + pd.Series(d[1], index=days) - rf).cumprod()
        ex_bd = (1 + pd.Series(d[2], index=days) - rf).cumprod()
        for lbl, a, b in (('полная доходность (ядро)', eq, bd),
                          ('избыточная доходность', ex_eq, ex_bd),
                          ('нога А избыточная, Б полная', ex_eq, bd)):
            m = run(d, M.weights(a, days, 'бинарное (ядро)'), M.weights(b, days, 'бинарное (ядро)'))
            print(f"  {lbl:<28} CAGR {m[0]:>6.2f}%  качка {m[1]:>5.2f}%  "
                  f"MaxDD {m[2]:>6.1f}%  D/V {m[3]:.2f}")
