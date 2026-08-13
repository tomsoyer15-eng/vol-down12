#!/usr/bin/env python3
"""Фильтр по АСИММЕТРИИ пробоев вместо фильтра по величине качки.

ЧЕМ ОТЛИЧАЕТСЯ ОТ УЖЕ ОТКЛОНЁННОГО. Прежний фильтр гасил ногу при высокой качке и потому
резал не только обвалы, но и всплески роста: качка не знает знака. Здесь знак вводится
явно — считается, сколько раз за окно цена пробивала порог ВВЕРХ и сколько ВНИЗ. Если
верхних пробоев больше, качка считается «хорошей» и нога не трогается; если преобладают
нижние, нога срезается.

ПАРАМЕТРЫ ЗАДАНЫ АПРИОРИ, НЕ ПОДБИРАЮТСЯ. Окно берётся то же, что у сигнала ядра — 12
месяцев (252 сессии), плюс квартал (63) для проверки чувствительности к длине. Порог
пробоя — одна и две сигмы, обе величины показываются, ни одна не выбирается. Правило
сравнения простое: строгое большинство нижних пробоев.

ФИЛЬТР ТОЛЬКО СНИЖАЕТ. Он не может включить ногу, которую выключило ядро, и не может
поднять вес выше единицы: это надстройка над §1, а не замена сигнала.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'r33build'))
import sim_v13 as S
import smooth as M
import rebal as R
import build_1885 as B
import band_recheck as BR

LOOKBACKS = [252, 63]
SIGMAS = [1.0, 2.0]
CUTS = [0.0, 0.5]            # во сколько раз срезать ногу: полностью и наполовину


def skew_filter(ret, days, lb, k, cut):
    """Вес ноги по асимметрии пробоев. Только прошлое: окно сдвинуто на сессию."""
    r = pd.Series(ret, index=days)
    sd = r.rolling(lb).std()
    up = (r > k * sd).rolling(lb).sum()
    dn = (r < -k * sd).rolling(lb).sum()
    bad = (dn > up).shift(1).fillna(False)          # сделка на следующий день после наблюдения
    return np.where(bad.values, cut, 1.0)


def run(name, d):
    days = d[0]
    we, wb = BR.weights_of(d)
    base, _ = BR.sim_stats(d, we, wb, 0.10)
    c0, v0, dd0, dv0 = M.met(base)
    yrs = (days[-1] - days[0]).days / 365.25
    print(f"\n=== {name}: {days[0]:%Y}–{days[-1]:%Y} ===")
    print(f"{'вариант':<34}{'CAGR':>8}{'качка':>8}{'MaxDD':>9}{'D/V':>7}"
          f"{'доля времени срезано':>22}{'при равном риске':>18}")
    print(f"{'ядро (без фильтра)':<34}{c0:>7.2f}%{v0:>7.2f}%{dd0:>8.1f}%{dv0:>7.2f}"
          f"{0.0:>21.0f}%{c0:>17.2f}%")
    for lb in LOOKBACKS:
        for k in SIGMAS:
            for cut in CUTS:
                f = skew_filter(d[1], days, lb, k, cut)
                we2, wb2 = we * f, wb * f
                r, _ = BR.sim_stats(d, we2, wb2, 0.10)
                c, v, dd, dv = M.met(r)
                mc, kk, _ = BR.match(d, we2, wb2, 0.10, v0)
                lbl = f'окно {lb}, {k:.0f}σ, срез до {cut:.0%}'
                print(f"{lbl:<34}{c:>7.2f}%{v:>7.2f}%{dd:>8.1f}%{dv:>7.2f}"
                      f"{(f < 1).mean()*100:>21.0f}%{mc[0]:>16.2f}%")


if __name__ == '__main__':
    run('дневное окно ядра', M.build())
    run('ПОЛНОЕ окно', B.build())
