#!/usr/bin/env python3
"""Свечи годовых доходностей ADD-FUT. Палитра проверена tools/validate_palette.py:
зелёный #1ca600 / красный #a52020 — CVD ΔE 15.3 (порог 8.0), нормальное зрение 35.8,
контраст 3.15 и 7.24 на поверхности #fcfcfb. Знак дублируется положением
относительно нулевой линии, поэтому цвет здесь избыточен, а не единственный канал."""
import sys
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from annual_candles import build

SURF, INK, INK2, MUTED = '#fcfcfb', '#0b0b0b', '#52514e', '#898781'
GRID, BASE = '#e1e0d9', '#c3c2b7'
UP, DOWN = '#1ca600', '#a52020'

plt.rcParams.update({'font.family': 'DejaVu Sans', 'figure.facecolor': SURF,
                     'axes.facecolor': SURF, 'savefig.facecolor': SURF,
                     'pdf.fonttype': 42, 'pdf.compression': 6})   # 42 = TrueType: текст ищется и копируется

df, full, dec, tot = build()

fig = plt.figure(figsize=(16.5, 11.8), dpi=150)
gs = fig.add_gridspec(3, 1, height_ratios=[3.0, 0.95, 1.15], hspace=0.34,
                      left=0.065, right=0.985, top=0.805, bottom=0.055)
axC, axD, axE = (fig.add_subplot(gs[i]) for i in range(3))


def chrome(ax, ylab, ticks=None):
    ax.set_facecolor(SURF)
    for sp in ax.spines.values(): sp.set_visible(False)
    ax.grid(axis='y', color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(colors=MUTED, labelsize=10.5, length=0)
    ax.set_ylabel(ylab, color=INK2, fontsize=11, labelpad=10)
    if ticks is not None: ax.set_yticks(ticks)


# ── панель 1: свечи ─────────────────────────────────────────────────────────
for y, row in full.iterrows():
    c = UP if row.close >= 0 else DOWN
    axC.plot([y, y], [row.low, row.high], color=c, linewidth=1.6,
             solid_capstyle='round', zorder=2)
    axC.bar(y, row.close, width=0.64, bottom=0, color=c, linewidth=0, zorder=3)
axC.axhline(0, color=BASE, linewidth=1.2, zorder=4)
chrome(axC, 'доходность года, %', ticks=range(-20, 81, 20))
axC.set_xlim(full.index[0] - 1.3, full.index[-1] + 1.3)
axC.set_xticks(range(1965, 2027, 5))
axC.set_ylim(full.low.min() - 15, full.high.max() + 13)
axC.yaxis.set_major_formatter(lambda v, p: f'{v:+.0f}' if v else '0')

b, w = full.close.idxmax(), full.close.idxmin()
axC.annotate(f'{b}   {full.close[b]:+.1f}%  — лучший год',
             xy=(b, full.high[b]), xytext=(9, 10), textcoords='offset points',
             ha='left', fontsize=10.5, color=INK2)
axC.annotate(f'{w}   {full.close[w]:+.1f}%  — худший год',
             xy=(w, full.low[w]), xytext=(9, -6), textcoords='offset points',
             ha='left', va='top', fontsize=10.5, color=INK2)

# ── панель 2: худшая просадка внутри года ───────────────────────────────────
axD.bar(full.index, full.maxdd, width=0.64, color=DOWN, linewidth=0, zorder=3)
axD.axhline(0, color=BASE, linewidth=1.2, zorder=4)
chrome(axD, 'худшая просадка\nвнутри года, %', ticks=[0, -10, -20, -30])
axD.set_xlim(axC.get_xlim()); axD.set_xticks(range(1965, 2027, 5))
axD.set_ylim(full.maxdd.min() - 11, 1.5)
d = full.maxdd.idxmin()
axD.annotate(f'{d}   {full.maxdd.min():.1f}%  — чёрный понедельник',
             xy=(d, full.maxdd.min()), xytext=(9, 1), textcoords='offset points',
             ha='left', va='top', fontsize=10.5, color=INK2)

# ── панель 3: CAGR по десятилетиям ──────────────────────────────────────────
names = [x[0] for x in dec]; vals = [x[1] for x in dec]; xs = np.arange(len(dec))
axE.bar(xs, vals, width=0.52, color=[UP if v >= 0 else DOWN for v in vals],
        linewidth=0, zorder=3)
for x, v in zip(xs, vals):
    axE.text(x, v + 0.55, f'{v:.1f}%', ha='center', va='bottom', fontsize=12, color=INK)
axE.axhline(tot['cagr'], color=BASE, linewidth=1.2, zorder=2)
axE.text(-0.55, tot['cagr'] + 0.45, f"весь срок {tot['cagr']:.2f}%", ha='left',
         va='bottom', fontsize=10.5, color=MUTED)
chrome(axE, 'CAGR, %', ticks=[0, 5, 10, 15, 20])
axE.set_xticks(xs); axE.set_xticklabels(names, fontsize=11.5, color=INK2)
axE.set_xlim(-0.62, len(dec) - 0.38); axE.set_ylim(0, max(vals) * 1.20)

# ── шапка ───────────────────────────────────────────────────────────────────
fig.text(0.065, 0.977, 'ADD-FUT — годовые доходности', fontsize=26, color=INK, va='top')
fig.text(0.065, 0.941, f"маршрут Ф, ред. 33 · {tot['first']:%m.%Y}–{tot['last']:%m.%Y} · "
         f"{tot['years']:.1f} года · строгая конвенция, кап плеча 2,00×",
         fontsize=12.5, color=INK2, va='top')
stats = [('CAGR за весь срок', f"{tot['cagr']:.2f}%"), ('худшая просадка', f"{tot['maxdd']:.1f}%"),
         ('качка', f"{tot['vol']:.2f}%"), ('прибыльных лет', f"{(full.close>0).sum()} из {len(full)}")]
for i, (k, v) in enumerate(stats):
    x = 0.065 + i * 0.155
    fig.text(x, 0.905, k, fontsize=10.5, color=MUTED, va='top')
    fig.text(x, 0.888, v, fontsize=20, color=INK, va='top')
fig.text(0.065, 0.845, 'Тело свечи — результат года: зелёное вверх от нуля — прибыль, '
         'красное вниз — убыток. Фитиль — куда счёт уходил внутри года от уровня 1 января.\n'
         'Свечи — по полным годам 1963–2025; 2026-й (данные по 06.08) в них не входит, '
         'но учтён в CAGR за весь срок и в столбце 2020–2026.',
         fontsize=11, color=MUTED, va='top')

for ext in ('png', 'pdf'):
    fig.savefig(f'docs/addfut-godovye.{ext}', bbox_inches='tight', pad_inches=0.3)
    print(f'docs/addfut-godovye.{ext}')
