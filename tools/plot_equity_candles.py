#!/usr/bin/env python3
"""Годовые свечи на кривой капитала: ADD-FUT и S&P 500.

Свеча года — открытие (баланс на 1 января), закрытие (на 31 декабря), фитили —
максимум и минимум баланса внутри года. В отличие от свечей «от нуля», здесь видно
сам счёт: где он стоял и насколько глубоко проваливался в абсолютных деньгах.

Шкала логарифмическая: за 63 года капитал растёт в сотни раз, в линейной шкале первые
десятилетия неразличимы. На логарифмической равные вертикальные отрезки означают равные
проценты, поэтому наклон читается как скорость роста.

Палитра проверена tools/validate_palette.py (зелёный #1ca600 / красный #a52020).
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'r33build'))
import sim_v13 as S, sim_v164 as S164

SURF, INK, INK2, MUTED = '#fcfcfb', '#0b0b0b', '#52514e', '#898781'
GRID, BASE = '#e1e0d9', '#c3c2b7'
UP, DOWN = '#1ca600', '#a52020'
CAP0 = 10_000_000.0
plt.rcParams.update({'font.family': 'DejaVu Sans', 'figure.facecolor': SURF,
                     'axes.facecolor': SURF, 'savefig.facecolor': SURF,
                     'pdf.fonttype': 42, 'pdf.compression': 6})
ru = lambda x: x.replace('.', ',')


def money(v, _=None):
    for div, unit in ((1e9, ' млрд'), (1e6, ' млн'), (1e3, ' тыс')):
        if v >= div:
            x = v / div
            return ru(f'{x:.1f}'.rstrip('0').rstrip('.') if x < 10 else f'{x:.0f}') + unit
    return f'{v:.0f}'


def ohlc(r):
    eq = CAP0 * (1 + r).cumprod()
    rows = []
    prev = CAP0
    for y, g in eq.groupby(eq.index.year):
        n = (r.index.year == y).sum()
        rows.append(dict(year=y, open=prev, close=g.iloc[-1],
                         high=max(g.max(), prev), low=min(g.min(), prev), n=n))
        prev = g.iloc[-1]
    df = pd.DataFrame(rows).set_index('year')
    # Полный год — не менее 240 сессий. Порог 200 засчитывал март–декабрь 1963
    # как полный год, из-за чего число прибыльных лет и свеча 1963 были неверны.
    return df[df.n >= 240], eq


if __name__ == '__main__':
    d = S.build_splice(); days = d[0]
    rs, _, _ = S164.sim164(*d)
    rb = pd.Series(d[1], index=days)
    cs, eqs = ohlc(rs); cb, eqb = ohlc(rb)
    yrs = (days[-1] - days[0]).days / 365.25

    fig = plt.figure(figsize=(16.5, 11.4), dpi=150)
    gs = fig.add_gridspec(2, 1, hspace=0.22, left=0.075, right=0.985, top=0.815, bottom=0.055)
    a1, a2 = fig.add_subplot(gs[0]), fig.add_subplot(gs[1])
    lim = (min(cs.low.min(), cb.low.min()) * 0.75, max(cs.high.max(), cb.high.max()) * 1.5)

    for ax, df, eq, name, fin in [(a1, cs, eqs, 'ADD-FUT, маршрут Ф', eqs.iloc[-1]),
                                  (a2, cb, eqb, 'S&P 500 полной доходности', eqb.iloc[-1])]:
        for y, row in df.iterrows():
            c = UP if row.close >= row.open else DOWN
            ax.plot([y, y], [row.low, row.high], color=c, linewidth=1.4,
                    solid_capstyle='round', zorder=2)
            lo, hi = min(row.open, row.close), max(row.open, row.close)
            ax.bar(y, hi - lo, width=0.62, bottom=lo, color=c, linewidth=0, zorder=3)
        ax.axhline(CAP0, color=BASE, linewidth=1.1, linestyle=(0, (5, 3)), zorder=1)
        ax.set_yscale('log')
        ax.set_facecolor(SURF); [sp.set_visible(False) for sp in ax.spines.values()]
        ax.grid(axis='y', which='major', color=GRID, linewidth=0.8, zorder=0)
        ax.set_axisbelow(True)
        ax.tick_params(colors=MUTED, labelsize=10.5, length=0)
        ax.set_ylabel('баланс счёта', color=INK2, fontsize=11, labelpad=10)
        ax.set_xlim(df.index[0] - 1.3, df.index[-1] + 1.3); ax.set_ylim(*lim)
        ax.set_xticks(range(1965, 2027, 5))
        ax.set_yticks([1e7, 3e7, 1e8, 3e8, 1e9, 3e9, 1e10])
        ax.yaxis.set_major_formatter(FuncFormatter(money))
        ax.minorticks_off()
        ax.text(0.004, 0.965, name, transform=ax.transAxes, fontsize=13.5, color=INK,
                va='top', weight='bold')
        ax.text(0.004, 0.90, f'старт 10 млн → {money(fin)} · в {fin/CAP0:.0f} раз',
                transform=ax.transAxes, fontsize=11, color=INK2, va='top')
        # худшая просадка
        dd = eq / eq.cummax() - 1
        t = dd.idxmin(); pk = eq[:t].idxmax()
        ax.plot([pk.year, t.year], [eq[pk], eq[t]], color=INK2, linewidth=1.0,
                linestyle=(0, (2, 2)), zorder=5)
        ax.annotate('худшая просадка ' + ru(f'{dd.min()*100:.1f}') + f'%  {t:%m.%Y}',
                    xy=(t.year, eq[t]), xytext=(6, -14), textcoords='offset points',
                    fontsize=10.5, color=INK2, va='top')

    fig.text(0.075, 0.976, 'Баланс счёта: ADD-FUT против S&P 500', fontsize=26, color=INK, va='top')
    fig.text(0.075, 0.941, f'маршрут Ф, ред. 33 · {days[0]:%m.%Y}–{days[-1]:%m.%Y} · '
             + ru(f'{yrs:.1f}') + ' года · старт 10 млн, изъятий нет, логарифмическая шкала',
             fontsize=12.5, color=INK2, va='top')
    fig.text(0.075, 0.900,
             'Свеча года: тело — от баланса 1 января до баланса 31 декабря, фитиль — максимум и минимум счёта внутри года. '
             'Зелёная — год закрыт выше открытия.\n'
             'Пунктир — стартовые 10 млн. Наклонная штриховая — глубочайшая просадка от пика к дну. '
             'На логарифмической шкале равные отрезки по высоте означают равные проценты.\n'
             'Рост в сотни раз за 63 года — арифметика сложного процента без изъятий, а не план: '
             'книга такого размера в рынок не помещается, ликвидность мерилась для 10 млн.',
             fontsize=11, color=MUTED, va='top')

    for ext in ('pdf', 'png'):
        fig.savefig(ROOT / f'docs/addfut-balans.{ext}', bbox_inches='tight', pad_inches=0.3)
        print(f'docs/addfut-balans.{ext}')
    print(f'ADD-FUT: 10 млн → {money(eqs.iloc[-1])} (в {eqs.iloc[-1]/CAP0:.0f} раз)')
    print(f'S&P 500: 10 млн → {money(eqb.iloc[-1])} (в {eqb.iloc[-1]/CAP0:.0f} раз)')
