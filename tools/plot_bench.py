#!/usr/bin/env python3
"""ADD-FUT против S&P 500: годовые свечи обеих кривых и сводка показателей.

Тело свечи — доходность года, фитили — максимум и минимум накопленной внутригодовой
доходности от уровня 1 января. Обе панели в одной шкале, поэтому сравнимы глазом.

Палитра проверена tools/validate_palette.py: зелёный #1ca600 / красный #a52020 —
CVD dE 15.3 при пороге 8.0, нормальное зрение 35.8, контраст 3.15 и 7.24 на #fcfcfb.
Знак дублируется положением относительно нулевой линии, поэтому цвет избыточен.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'r33build'))
import sim_v13 as S, sim_v164 as S164

SURF, INK, INK2, MUTED = '#fcfcfb', '#0b0b0b', '#52514e', '#898781'
GRID, BASE = '#e1e0d9', '#c3c2b7'
UP, DOWN = '#1ca600', '#a52020'
plt.rcParams.update({'font.family': 'DejaVu Sans', 'figure.facecolor': SURF,
                     'axes.facecolor': SURF, 'savefig.facecolor': SURF,
                     'pdf.fonttype': 42, 'pdf.compression': 6})
ru = lambda x: x.replace('.', ',')


def candles(r):
    rows = []
    for y, g in r.groupby(r.index.year):
        cum = (1 + g).cumprod()
        rows.append(dict(year=y, close=(cum.iloc[-1] - 1) * 100,
                         high=(cum.max() - 1) * 100, low=(cum.min() - 1) * 100, n=len(g)))
    df = pd.DataFrame(rows).set_index('year')
    # Полный год — не менее 240 сессий. Порог 200 засчитывал март–декабрь 1963
    # как полный год, из-за чего число прибыльных лет и свеча 1963 были неверны.
    return df[df.n >= 240]


def stats(r, rf):
    eq = (1 + r).cumprod(); yrs = (r.index[-1] - r.index[0]).days / 365.25
    ex = r - rf.reindex(r.index)
    c = (eq.iloc[-1] ** (1 / yrs) - 1) * 100
    v = r.std() * np.sqrt(252) * 100
    return dict(cagr=c, vol=v, sharpe=ex.mean() / ex.std() * np.sqrt(252),
                maxdd=(eq / eq.cummax() - 1).min() * 100, dv=c / v, mult=eq.iloc[-1])


if __name__ == '__main__':
    d = S.build_splice()
    days = d[0]
    rs, nav, st = S164.sim164(*d)                      # стратегия, маршрут Ф
    rb = pd.Series(d[1], index=days)                   # S&P 500 полной доходности, без плеча
    rf = pd.Series(S.rf_series(days).values, index=days)
    cs, cb = candles(rs), candles(rb)
    ss, sb = stats(rs, rf), stats(rb, rf)
    yrs = (days[-1] - days[0]).days / 365.25
    corr = rs.corr(rb)

    fig = plt.figure(figsize=(16.5, 12.6), dpi=150)
    gs = fig.add_gridspec(3, 1, height_ratios=[2.4, 2.4, 1.15], hspace=0.30,
                          left=0.065, right=0.985, top=0.795, bottom=0.045)
    a1, a2, a3 = (fig.add_subplot(gs[i]) for i in range(3))

    lim = (min(cs.low.min(), cb.low.min()) - 8, max(cs.high.max(), cb.high.max()) + 8)
    for ax, df, name in [(a1, cs, 'ADD-FUT, маршрут Ф'), (a2, cb, 'S&P 500 полной доходности')]:
        for y, row in df.iterrows():
            c = UP if row.close >= 0 else DOWN
            ax.plot([y, y], [row.low, row.high], color=c, linewidth=1.5,
                    solid_capstyle='round', zorder=2)
            ax.bar(y, row.close, width=0.62, bottom=0, color=c, linewidth=0, zorder=3)
        ax.axhline(0, color=BASE, linewidth=1.2, zorder=4)
        ax.set_facecolor(SURF); [sp.set_visible(False) for sp in ax.spines.values()]
        ax.grid(axis='y', color=GRID, linewidth=0.8, zorder=0); ax.set_axisbelow(True)
        ax.tick_params(colors=MUTED, labelsize=10.5, length=0)
        ax.set_ylabel('доходность года, %', color=INK2, fontsize=11, labelpad=10)
        ax.set_xlim(df.index[0] - 1.3, df.index[-1] + 1.3); ax.set_ylim(*lim)
        ax.set_xticks(range(1965, 2027, 5))
        ax.set_yticks(range(-40, 81, 20))
        ax.yaxis.set_major_formatter(lambda v, p: f'{v:+.0f}' if v else '0')
        ax.text(0.004, 0.955, name, transform=ax.transAxes, fontsize=13.5, color=INK,
                va='top', weight='bold')
        b, w = df.close.idxmax(), df.close.idxmin()
        ax.annotate(ru(f'{b}  {df.close[b]:+.1f}%'), xy=(b, df.high[b]), xytext=(8, 8),
                    textcoords='offset points', fontsize=10, color=INK2)
        ax.annotate(ru(f'{w}  {df.close[w]:+.1f}%'), xy=(w, df.low[w]), xytext=(8, -4),
                    textcoords='offset points', va='top', fontsize=10, color=INK2)

    a3.axis('off')
    rows = [('CAGR за весь срок', f"{ss['cagr']:.2f}%", f"{sb['cagr']:.2f}%"),
            ('годовая качка', f"{ss['vol']:.2f}%", f"{sb['vol']:.2f}%"),
            ('коэффициент Шарпа', f"{ss['sharpe']:.2f}", f"{sb['sharpe']:.2f}"),
            ('худшая просадка', f"{ss['maxdd']:.1f}%", f"{sb['maxdd']:.1f}%"),
            ('доходность к качке', f"{ss['dv']:.2f}", f"{sb['dv']:.2f}"),
            ('капитал вырос в', f"{ss['mult']:.0f} раз", f"{sb['mult']:.0f} раз"),
            ('прибыльных лет', f"{(cs.close>0).sum()} из {len(cs)}", f"{(cb.close>0).sum()} из {len(cb)}"),
            ('лучший год', f"{cs.close.max():+.1f}%", f"{cb.close.max():+.1f}%"),
            ('худший год', f"{cs.close.min():+.1f}%", f"{cb.close.min():+.1f}%")]
    x0, x1, x2 = 0.02, 0.44, 0.66
    a3.text(x1, 1.02, 'ADD-FUT', fontsize=12.5, color=INK, weight='bold', ha='right',
            transform=a3.transAxes)
    a3.text(x2, 1.02, 'S&P 500', fontsize=12.5, color=INK2, ha='right', transform=a3.transAxes)
    for i, (k, v1, v2) in enumerate(rows):
        y = 0.90 - i * 0.105
        a3.text(x0, y, k, fontsize=11.5, color=MUTED, transform=a3.transAxes, va='center')
        a3.text(x1, y, ru(v1), fontsize=12.5, color=INK, ha='right', transform=a3.transAxes, va='center')
        a3.text(x2, y, ru(v2), fontsize=12.5, color=INK2, ha='right', transform=a3.transAxes, va='center')
    a3.text(0.72, 0.90, f'корреляция дневных ходов с индексом: {ru(f"{corr:.2f}")}',
            fontsize=11.5, color=INK2, transform=a3.transAxes, va='center')
    a3.text(0.72, 0.795, 'бенчмарк — индекс полной доходности США без плеча,',
            fontsize=10.5, color=MUTED, transform=a3.transAxes, va='center')
    a3.text(0.72, 0.71, 'на том же окне; бенчмарк без издержек',
            fontsize=10.5, color=MUTED, transform=a3.transAxes, va='center')

    fig.text(0.065, 0.978, 'ADD-FUT против S&P 500', fontsize=27, color=INK, va='top')
    fig.text(0.065, 0.944, f'маршрут Ф, ред. 33 · {days[0]:%m.%Y}–{days[-1]:%m.%Y} · '
             + ru(f'{yrs:.1f}') + ' года · строгая конвенция, кап плеча 2,00×',
             fontsize=12.5, color=INK2, va='top')
    hero = [('CAGR', ru(f"{ss['cagr']:.2f}") + '%', ru(f"{sb['cagr']:.2f}") + '%'),
            ('Шарп', ru(f"{ss['sharpe']:.2f}"), ru(f"{sb['sharpe']:.2f}")),
            ('худшая просадка', ru(f"{ss['maxdd']:.1f}") + '%', ru(f"{sb['maxdd']:.1f}") + '%'),
            ('доходность к качке', ru(f"{ss['dv']:.2f}"), ru(f"{sb['dv']:.2f}"))]
    for i, (k, v1, v2) in enumerate(hero):
        x = 0.065 + i * 0.175
        fig.text(x, 0.908, k, fontsize=10.5, color=MUTED, va='top')
        fig.text(x, 0.892, v1, fontsize=21, color=INK, va='top')
        fig.text(x, 0.858, 'S&P ' + v2, fontsize=11, color=MUTED, va='top')
    fig.text(0.065, 0.840, 'Тело свечи — результат года: зелёное вверх от нуля — прибыль, красное вниз — убыток. '
             'Фитиль — куда счёт уходил внутри года от уровня 1 января, вверх и вниз.\n'
             'Обе панели в одной шкале. Свечи по полным годам; 2026-й неполный и в них не входит, '
             'но учтён в CAGR, Шарпе и просадке за весь срок.',
             fontsize=11, color=MUTED, va='top')

    for ext in ('pdf', 'png'):
        fig.savefig(ROOT / f'docs/addfut-vs-sp500.{ext}', bbox_inches='tight', pad_inches=0.3)
        print(f'docs/addfut-vs-sp500.{ext}')
    print(f"\nADD-FUT: CAGR {ss['cagr']:.2f}%, Шарп {ss['sharpe']:.2f}, "
          f"просадка {ss['maxdd']:.1f}%, качка {ss['vol']:.2f}%")
    print(f"S&P 500: CAGR {sb['cagr']:.2f}%, Шарп {sb['sharpe']:.2f}, "
          f"просадка {sb['maxdd']:.1f}%, качка {sb['vol']:.2f}%")
    print(f"корреляция {corr:.3f}")
