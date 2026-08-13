#!/usr/bin/env python3
"""Общая позиция ADD-FUT: нога акций и нога облигаций во времени.

Палитра проверена tools/validate_palette.py: синий #2a78d6 / оранжевый #eb6834 —
CVD ΔE 24.7 (порог 8.0), нормальное зрение 33.6, оба выше 3:1 на поверхности #fcfcfb.
Ноги дополнительно различаются положением в стопке и подписаны прямо на полотне,
поэтому цвет здесь не единственный канал.

Экспозиция считается по ценам закрытия того же дня и делится на капитал того же дня —
поэтому видно и штатное «1,0 на ногу», и снос внутри полосы 3%, и превышения капа 2,00
между проверками (спецификация раскрывает максимум 2,0974 по склейке).
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'r33build'))
import sim_v13 as S
import smooth as M

SURF, INK, INK2, MUTED = '#fcfcfb', '#0b0b0b', '#52514e', '#898781'
GRID, BASE = '#e1e0d9', '#c3c2b7'
EQ, BD = '#2a78d6', '#eb6834'
CAP = 2.00

plt.rcParams.update({'font.family': 'DejaVu Sans', 'figure.facecolor': SURF,
                     'axes.facecolor': SURF, 'savefig.facecolor': SURF,
                     'pdf.fonttype': 42, 'pdf.compression': 6})


def run(d, w_eq, w_bd, cap=CAP, spread_pp=0.5, cap0=10_000_000.0):
    """Движок ядра с записью дневной экспозиции каждой ноги (доля капитала)."""
    days, re, rb, rfv, spy_close, dref = d[:6]
    n = len(days); sd = spread_pp / 100 / 252
    e = cap0; n_e = n_b = 0; unit_is_mes = False
    ee = np.empty(n); eb = np.empty(n)
    rolls = S.roll_days_calendar(days); D_fix = dref[0]; prev_lev = 0.0
    for i, dte in enumerate(days):
        j = max(i - 1, 0)
        if (not unit_is_mes) and dte >= S.MES_START:
            n_e *= 10; unit_is_mes = True
        mult = S.ES_MULT / 10 if unit_is_mes else S.ES_MULT
        unit_e = mult * spy_close[j]
        roll = dte in rolls
        edge_e = (w_eq[i] == 0) != (w_eq[j] == 0) if i else w_eq[i] > 0
        edge_b = (w_bd[i] == 0) != (w_bd[j] == 0) if i else w_bd[i] > 0
        if roll or edge_b:
            D_fix = dref[j]
        unit_b = (S.ZN_MODEL_PX_EQ * S.CTD_RATIO * D_fix * 1e-4) / (dref[j] * 1e-4)
        units = np.array([unit_e, unit_b])
        tgt = np.array([w_eq[i], w_bd[i]]) * e
        if tgt.sum() > cap * e:
            tgt *= cap * e / tgt.sum()
        n0 = np.array([n_e, n_b], dtype=float); cur = n0.copy(); exp = cur * units
        for q, force in enumerate([edge_e, edge_b or roll]):
            if force or abs(tgt[q] - exp[q]) > S.BAND * e:
                new = round(tgt[q] / units[q])
                if new != cur[q]:
                    e -= S.COST * abs(new - cur[q]) * units[q]
                    cur[q] = new; exp[q] = new * units[q]

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
        n_e, n_b = cur; exp = cur * units
        if roll: e -= S.ROLL_BP * exp[1]
        if i > 0:
            e = e * (1 + rfv[i]) + (exp * (np.array([re[i], rb[i]]) - rfv[i] - sd)).sum()
        mc = S.ES_MULT / 10 if unit_is_mes else S.ES_MULT
        uc = np.array([mc * spy_close[i],
                       (S.ZN_MODEL_PX_EQ * S.CTD_RATIO * D_fix * 1e-4) / (dref[i] * 1e-4)])
        ee[i], eb[i] = cur[0] * uc[0] / e, cur[1] * uc[1] / e
        prev_lev = ee[i] + eb[i]
    return pd.DataFrame({'A': ee, 'B': eb}, index=days)


d0 = M.build(); days = d0[0]
d = d0[:6]
w_eq = M.weights(d0[6], days, 'бинарное (ядро)')
w_bd = M.weights(d0[7], days, 'бинарное (ядро)')
P = run(d, w_eq, w_bd)
P['tot'] = P['A'] + P['B']

st_e, st_b = w_eq > 0, w_bd > 0
shares = [('обе ноги', (st_e & st_b).mean()), ('только акции', (st_e & ~st_b).mean()),
          ('только облигации', (~st_e & st_b).mean()), ('вне рынка', (~st_e & ~st_b).mean())]
ru = lambda x: x.replace('.', ',')


def mid_of_longest(mask, index):
    """Середина самого длинного непрерывного участка — куда класть прямую подпись."""
    best = (0, None); cur = 0; start = 0
    for i, v in enumerate(mask):
        if v:
            if cur == 0: start = i
            cur += 1
            if cur > best[0]: best = (cur, (start, i))
        else:
            cur = 0
    return None if best[1] is None else index[sum(best[1]) // 2]


fig = plt.figure(figsize=(16.5, 11.6), dpi=150)
gs = fig.add_gridspec(3, 1, height_ratios=[2.5, 1.5, 0.85], hspace=0.30,
                      left=0.065, right=0.985, top=0.855, bottom=0.115)
axA, axB, axC = (fig.add_subplot(gs[i]) for i in range(3))


def chrome(ax, ylab, ticks):
    ax.set_facecolor(SURF)
    for sp in ax.spines.values(): sp.set_visible(False)
    ax.grid(axis='y', color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(colors=MUTED, labelsize=10.5, length=0)
    ax.set_ylabel(ylab, color=INK2, fontsize=11, labelpad=10)
    ax.set_yticks(ticks)


def stack(ax, df):
    ax.fill_between(df.index, 0, df['A'], color=EQ, linewidth=0, zorder=2)
    ax.fill_between(df.index, df['A'], df['tot'], color=BD, linewidth=0, zorder=2)
    ax.plot(df.index, df['A'], color=SURF, linewidth=1.4, zorder=3)
    ax.axhline(CAP, color=BASE, linewidth=1.2, linestyle=(0, (5, 3)), zorder=4)


ZOOM = P.index >= '2019-01-01'
for ax, sl, ylab in [(axA, P, 'экспозиция, доли капитала'), (axB, P[ZOOM], 'то же, крупно')]:
    stack(ax, sl)
    chrome(ax, ylab, [0, 0.5, 1.0, 1.5, 2.0])
    ax.set_xlim(sl.index[0], sl.index[-1])
    ax.set_ylim(0, 2.28)

axA.set_xticks([pd.Timestamp(f'{y}-01-01') for y in range(1965, 2027, 5)])
axA.set_xticklabels([str(y) for y in range(1965, 2027, 5)])
axB.set_xticks([pd.Timestamp(f'{y}-01-01') for y in range(2019, 2027)])
axB.set_xticklabels([str(y) for y in range(2019, 2027)])
for ax in (axA, axB):
    ax.text(0.997, 2.05, 'кап 2,00×', transform=ax.get_yaxis_transform(),
            ha='right', va='bottom', fontsize=10.5, color=MUTED)

# прямые подписи ног — в крупной панели, где блоки широкие
both = np.asarray(st_e & st_b)[np.asarray(ZOOM)]
c = mid_of_longest(both, P[ZOOM].index)
if c is not None:
    for y, lbl in [(0.5, 'акции'), (1.5, 'облигации')]:
        axB.text(c, y, lbl, fontsize=13, color='#ffffff', ha='center', va='center', weight='bold')

names = [s[0] for s in shares]; vals = [s[1] * 100 for s in shares]
xs = np.arange(len(shares))
axC.bar(xs, vals, width=0.5, color=INK2, linewidth=0, zorder=3)
for x, v in zip(xs, vals):
    axC.text(x, v + 1.6, f'{v:.0f}%', ha='center', va='bottom', fontsize=14, color=INK)
chrome(axC, 'доля времени', [0, 20, 40, 60])
axC.set_xticks(xs); axC.set_xticklabels(names, fontsize=11.5, color=INK2)
axC.set_xlim(-0.6, len(shares) - 0.4); axC.set_ylim(0, max(vals) * 1.30)

h = [plt.Rectangle((0, 0), 1, 1, color=col) for col in (EQ, BD)]
fig.legend(h, ['нога А — акции S&P 500', 'нога Б — казначейские бумаги'],
           loc='upper left', bbox_to_anchor=(0.065, 0.884), ncol=2, frameon=False,
           fontsize=12, labelcolor=INK2, handlelength=1.1, handleheight=1.1)

yrs = (days[-1] - days[0]).days / 365.25
fig.text(0.065, 0.982, 'ADD-FUT — общая позиция', fontsize=26, color=INK, va='top')
fig.text(0.065, 0.948, f'маршрут Ф, ред. 33 · {days[0]:%m.%Y}–{days[-1]:%m.%Y} · '
         + ru(f'{yrs:.1f}') + ' года · включённая нога 1,0 × капитал, кап 2,00×',
         fontsize=12.5, color=INK2, va='top')
stats = [('среднее плечо', ru(f"{P['tot'].mean():.2f}") + '×'),
         ('максимум', ru(f"{P['tot'].max():.3f}") + '×'),
         ('дней выше капа', f"{(P['tot'] > CAP).sum()} из {len(P)}"),
         ('вне рынка', f'{shares[3][1]*100:.0f}% времени')]
for i, (k, v) in enumerate(stats):
    x = 0.065 + i * 0.170
    fig.text(x, 0.918, k, fontsize=10.5, color=MUTED, va='top')
    fig.text(x, 0.901, v, fontsize=20, color=INK, va='top')
fig.text(0.065, 0.072,
         'Высота стопки — суммарный номинал в долях капитала. Ровные площадки на 1,0 и 2,0 — штатное состояние; '
         'мелкие зубцы внутри них — снос экспозиции в полосе 3% между сделками.\n'
         'Провалы до нуля — месяцы, когда фильтр 12-месячной средней выключил ногу. Пунктир — кап 2,00×; '
         f"короткие выходы над ним (всего {(P['tot'] > CAP).sum()} дней, максимум "
         + ru(f"{P['tot'].max():.3f}") + f"× — {P['tot'].idxmax():%d.%m.%Y})\n"
         'создаются движением рынка между проверками и убираются сделкой следующей сессии.',
         fontsize=11, color=MUTED, va='top')

for ext in ('png', 'pdf'):
    fig.savefig(ROOT / f'docs/addfut-poziciya.{ext}', bbox_inches='tight', pad_inches=0.3)
    print(f'docs/addfut-poziciya.{ext}')
print(f"\nсреднее плечо {P['tot'].mean():.3f}, максимум {P['tot'].max():.4f} ({P['tot'].idxmax():%d.%m.%Y})")
print(f"дней с плечом выше {CAP}: {(P['tot'] > CAP).sum()} из {len(P)}")
for k, v in shares: print(f"  {k:<18}{v*100:>5.1f}%")
