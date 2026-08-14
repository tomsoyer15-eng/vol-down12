#!/usr/bin/env python3
"""Пила 12-месячной средней: сколько раз ВНУТРИ месяца сигнал §1 сменил бы ответ и чем
месяц кончился.

Исследование, а не часть контура: замороженные движки, живое состояние и книга не
трогаются — только чтение рядов из r33build/data.

ОПРЕДЕЛЕНИЕ. Нормативный сигнал §1 (sim_v13.sigs) сравнивает закрытие месяца с
12-месячной SMA, в которую ЭТО ЖЕ закрытие входит: state = me > (S11 + me)/12, где
S11 — сумма одиннадцати предыдущих закрытий месяцев. Провизорное состояние дня d
месяца p получается подстановкой сегодняшнего уровня вместо закрытия месяца:

    state(d) = lvl(d) > (S11(p) + lvl(d))/12   <=>   lvl(d) > S11(p)/11

то есть внутри месяца ПОРОГ ПОСТОЯНЕН и равен средней одиннадцати предыдущих месячных
закрытий, а в последний день месяца определение тождественно нормативному. Тождество
проверяется утверждениями (см. _sverka).

Переключения месяца считаются по пути [состояние на конец p-1, дни p...]: столько раз
ответ «держим ногу / не держим» сменился бы при ежедневной проверке. Чётность k
однозначно задаёт исход месяца (k нечётно <=> месяц кончился сменой сигнала).

Строка ряда §1 помечена месяцем ДЕЙСТВИЯ (решение месяца M действует в M+1) — здесь
изучается сам ряд решений, лаг исполнения на статистику пилы не влияет.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'r33build'))
import sim_v13 as S                                                    # noqa: E402

D = ROOT / 'r33build/data'


# ---------------------------------------------------------------- ряды (как в движке)

def ryady_modern():
    """Современный набор: ровно то, чем кормится build_modern (SPY adj / IEF adj)."""
    spy = pd.read_csv(D / 'spy_daily.csv', parse_dates=['date'], index_col='date')
    ief = pd.read_csv(D / 'ief_daily.csv', parse_dates=['date'], index_col='date')['adj']
    days = spy.index[spy.index >= pd.Timestamp('2003-07-01')]
    return days, {'А (SPY)': spy['adj'], 'Б (IEF)': ief}


def ryady_splice():
    """Длинная склейка: те же ряды уровней, что build_splice подаёт в sigs()."""
    spy = pd.read_csv(D / 'spy_daily.csv', parse_dates=['date'], index_col='date')
    ief = pd.read_csv(D / 'ief_daily.csv', parse_dates=['date'], index_col='date')['adj']
    mk = pd.read_csv(D / 'us-market-daily-1885-2025.csv',
                     parse_dates=['Date']).set_index('Date').sort_index()
    eq_r = pd.concat([mk['Adjusted Close'].pct_change(),
                      spy['adj'].pct_change()[spy.index > mk.index[-1]]])
    recon = pd.read_csv(D / 'bond10_recon_daily.csv',
                        parse_dates=['Date'], index_col='Date')['ret']
    bond_idx = (1 + pd.concat([recon[recon.index < ief.index[0]],
                               ief.pct_change().dropna()])).cumprod().reindex(eq_r.index).ffill()
    days = eq_r.index[eq_r.index >= pd.Timestamp('1963-03-01')]
    eq_full = (1 + eq_r.fillna(0)).cumprod()
    return days, {'А (акции)': eq_full, 'Б (10 лет)': bond_idx.dropna()}


# ---------------------------------------------------------------- ядро исследования

def _mesyachnye(px):
    """Месячные закрытия ровно как в sigs(): resample('ME').last().ffill()."""
    me = px.resample('ME').last().ffill()
    return pd.Series(me.values, index=me.index.to_period('M'))


def razbor(px, days):
    """Возвращает (помесячная таблица, подённая таблица) по одному ряду уровней."""
    mep = _mesyachnye(px)
    s11 = mep.rolling(11).sum().shift(1)                  # сумма 11 месяцев ДО текущего
    sma12 = mep.rolling(12).mean()
    fakt = (mep > sma12).where(sma12.notna())             # нормативное состояние месяца

    lvl = px.reindex(days).ffill()
    per = days.to_period('M')
    thr = s11.reindex(per).values / 11.0                  # порог дня — постоянный в месяце
    den = pd.DataFrame({'уровень': lvl.values, 'порог': thr}, index=days)
    den['месяц'] = per
    den = den[np.isfinite(den['порог'].values)]
    den['состояние'] = den['уровень'].values > den['порог'].values
    den['отрыв'] = den['уровень'].values / den['порог'].values - 1.0

    stroki = []
    for p, g in den.groupby('месяц', sort=True):
        vhod = fakt.get(p - 1, np.nan)                    # состояние на конец прошлого месяца
        if not isinstance(vhod, (bool, np.bool_)):
            continue
        put = np.concatenate([[bool(vhod)], g['состояние'].values])
        k = int((put[1:] != put[:-1]).sum())              # переключений за месяц
        sost = g['состояние'].values
        k_vnutri = int((sost[1:] != sost[:-1]).sum())     # строго между днями месяца
        chuzhie = g['состояние'].values != bool(vhod)     # дни на «другой стороне»
        glub = float(np.max(np.abs(g['отрыв'].values[chuzhie]))) if chuzhie.any() else 0.0
        stroki.append(dict(месяц=p, вход=bool(vhod), итог=bool(fakt[p]), переключений=k,
                           внутри=k_vnutri, сессий=len(g), дней_на_той_стороне=int(chuzhie.sum()),
                           глубина=glub, сменился=bool(fakt[p] != bool(vhod))))
    return pd.DataFrame(stroki).set_index('месяц'), den


def _sverka(px, days, tab):
    """«Правка без проверки не правка»: тождество определения нормативному сигналу."""
    mep = _mesyachnye(px)
    sma12 = mep.rolling(12).mean()
    fakt = (mep > sma12).where(sma12.notna())

    # 1. Провизорное состояние ПОСЛЕДНЕГО дня месяца = нормативное состояние месяца.
    _, den = razbor(px, days)
    posl = den.groupby('месяц')['состояние'].last()
    obshch = [p for p in posl.index if isinstance(fakt.get(p, np.nan), (bool, np.bool_))]
    assert len(obshch) > 100, f'сверка пуста: общих месяцев {len(obshch)}'
    rashod = [p for p in obshch if bool(posl[p]) != bool(fakt[p])]
    assert not rashod, f'провизорное != нормативного в {len(rashod)} мес: {rashod[:5]}'

    # 2. Ряд sigs() на дне месяца p равен нормативному состоянию месяца p-1 (месяц ДЕЙСТВИЯ).
    sg = pd.Series(S.sigs(px, days), index=days)
    pr = pd.Series(days.to_period('M'), index=days)
    prov = 0
    for d in days[::37]:
        p = pr[d]
        if isinstance(fakt.get(p - 1, np.nan), (bool, np.bool_)):
            assert bool(sg[d]) == bool(fakt[p - 1]), f'конвенция месяца действия сломана на {d}'
            prov += 1
    assert prov > 50, f'конвенция проверена лишь {prov} раз'

    # 3. Чётность переключений обязана задавать исход месяца.
    assert (tab['сменился'] == (tab['переключений'] % 2 == 1)).all(), 'чётность не сходится'
    return len(obshch), prov


# ---------------------------------------------------------------- сцепление месяцев

SEED = 20260814          # фиксированное зерно: перестановочный тест обязан воспроизводиться


def _serii(flags):
    """Длины серий подряд идущих True."""
    import itertools
    return [len(list(g)) for k, g in itertools.groupby(flags) if k]


def sceplenie(tab, porog=2, n_perm=4000):
    """Тяготеют ли дёрганые месяцы друг к другу.

    Меряется тремя независимыми способами, чтобы одна метрика не убедила сама себя:
      1. Условная вероятность P(пила в t+h | пила в t) против базовой доли — подъём на
         h=1 означает сцепление соседей;
      2. серии подряд идущих дёрганых месяцев против геометрического ожидания
         независимых испытаний (1/(1-p) — средняя длина серии при независимости);
      3. перестановочный тест: те же месяцы, перемешанные с фиксированным зерном,
         дают распределение максимальной серии — доля перестановок не хуже факта есть
         честное p-значение без предположения о нормальности.
    """
    import random
    per = list(tab.index)
    # непрерывность ряда месяцев — иначе «соседи» считались бы через дыру
    razryv = [i for i in range(1, len(per)) if (per[i] - per[i-1]).n != 1]
    flags = [bool(v >= porog) for v in tab['переключений'].values]
    p = sum(flags)/len(flags)

    lag = {}
    for h in range(1, 7):
        par = [(flags[i], flags[i+h]) for i in range(len(flags)-h)
               if not any(j in razryv for j in range(i+1, i+h+1))]
        posle = [b for a, b in par if a]
        lag[h] = (sum(posle)/len(posle) if posle else float('nan'), len(posle))

    ser = _serii(flags)
    fakt_max = max(ser) if ser else 0
    fakt_dlinnyh = sum(1 for s in ser if s >= 3)
    ozhid_sred = 1/(1-p) if p < 1 else float('inf')

    rnd = random.Random(SEED)
    perem = list(flags)
    huzhe_max = huzhe_dl = 0
    for _ in range(n_perm):
        rnd.shuffle(perem)
        s2 = _serii(perem)
        if (max(s2) if s2 else 0) >= fakt_max:
            huzhe_max += 1
        if sum(1 for s in s2 if s >= 3) >= fakt_dlinnyh:
            huzhe_dl += 1
    return dict(p=p, lag=lag, serii=ser, max_serii=fakt_max, dlinnyh=fakt_dlinnyh,
                sred_serii=(sum(ser)/len(ser) if ser else 0.0), ozhid_sred=ozhid_sred,
                p_max=huzhe_max/n_perm, p_dl=huzhe_dl/n_perm, razryvov=len(razryv))


def otchet_sceplenie(tab, imya, out, porog=2):
    sc = sceplenie(tab, porog)
    p = out.append
    import collections
    raspr = collections.Counter(sc['serii'])
    p(f'\n**Сцепление дёрганых месяцев ({imya}).** Базовая доля пилы '
      f'{100*sc["p"]:.1f}%; разрывов в ряду месяцев: {sc["razryvov"]}.\n')
    p('| лаг h, мес | P(пила в t+h \\| пила в t) | против базовой |')
    p('|---|---|---|')
    for h, (v, n) in sc['lag'].items():
        p(f'| {h} | {100*v:.1f}% (n={n}) | ×{v/sc["p"]:.2f} |')
    p('')
    p(f'- Серий подряд идущих дёрганых месяцев: {len(sc["serii"])}; '
      f'распределение длин: ' + ', '.join(f'{k}×{raspr[k]}' for k in sorted(raspr)) + '.')
    p(f'- Средняя длина серии {sc["sred_serii"]:.2f} против {sc["ozhid_sred"]:.2f} '
      f'при независимости; самая длинная — {sc["max_serii"]} мес.')
    p(f'- Перестановочный тест ({SEED}): максимальная серия не короче фактической '
      f'встречается в {100*sc["p_max"]:.1f}% перемешиваний; серий длиной 3+ не меньше '
      f'фактических — в {100*sc["p_dl"]:.1f}%.')
    dlin = [s for s in sc['serii'] if s >= 3]
    if dlin:
        idx = []
        flags = [bool(v >= porog) for v in tab['переключений'].values]
        i = 0
        while i < len(flags):
            if flags[i]:
                j = i
                while j+1 < len(flags) and flags[j+1]:
                    j += 1
                if j-i+1 >= 3:
                    idx.append(f'{tab.index[i]}…{tab.index[j]} ({j-i+1} мес)')
                i = j+1
            else:
                i += 1
        p(f'- Длинные полосы: {"; ".join(idx)}.')
    return sc


# ---------------------------------------------------------------- отчёт

def otchet(tab, imya, out):
    n = len(tab)
    let = (tab.index[-1].ordinal - tab.index[0].ordinal + 1) / 12
    raspr = tab['переключений'].value_counts().sort_index()
    tihie = int((tab['переключений'] == 0).sum())
    odno = int((tab['переключений'] == 1).sum())
    pila = tab[tab['переключений'] >= 2]
    pila_vernulis = pila[~pila['сменился']]
    pila_smena = pila[pila['сменился']]
    smen = int(tab['сменился'].sum())

    p = out.append
    p(f'\n### {imya}: {tab.index[0]}–{tab.index[-1]}, месяцев {n} ({let:.1f} года)\n')
    p('| переключений за месяц | месяцев | доля | из них кончились сменой |')
    p('|---|---|---|---|')
    for k, c in raspr.items():
        sm = int(tab.loc[tab['переключений'] == k, 'сменился'].sum())
        p(f'| {k} | {c} | {100*c/n:.1f}% | {sm} |')
    p('')
    p(f'- **Месяцев без единого пересечения:** {tihie} ({100*tihie/n:.1f}%).')
    p(f'- **Ровно одно пересечение** (сигнал ушёл и остался): {odno} ({100*odno/n:.1f}%).')
    p(f'- **Пила, 2 и более пересечений:** {len(pila)} ({100*len(pila)/n:.1f}%); '
      f'из них {len(pila_vernulis)} кончились ТЕМ ЖЕ состоянием, что и вошли '
      f'({100*len(pila_vernulis)/max(len(pila),1):.0f}% пилы), и {len(pila_smena)} — сменой.')
    p(f'- **Итоговых смен сигнала:** {smen} ({100*smen/n:.1f}% месяцев, '
      f'{smen/let:.2f} в год).')
    p(f'- **Переключений при ЕЖЕДНЕВНОЙ проверке:** {int(tab["переключений"].sum())} '
      f'({tab["переключений"].sum()/let:.2f} в год) — против {smen/let:.2f} в год '
      f'у месячной проверки; месячная проверка гасит '
      f'{100*(1 - smen/max(int(tab["переключений"].sum()),1)):.0f}% движений.')
    if len(pila_vernulis):
        d = pila_vernulis['дней_на_той_стороне']
        g = pila_vernulis['глубина']
        p(f'- **Ложная тревога в месяцах-возвратах:** на «другой стороне» медиана '
          f'{d.median():.0f} сессий из ~21, максимум {d.max()}; глубина захода за среднюю '
          f'медиана {100*g.median():.2f}%, максимум {100*g.max():.2f}%.')
    hud = tab.nlargest(5, 'переключений')[['переключений', 'вход', 'итог',
                                           'дней_на_той_стороне', 'глубина']]
    p('\nСамые дёрганые месяцы:\n')
    p('| месяц | переключений | вход | итог | дней на той стороне | глубина захода |')
    p('|---|---|---|---|---|---|')
    for m, r in hud.iterrows():
        p(f'| {m} | {r["переключений"]} | {"держим" if r["вход"] else "нет"} | '
          f'{"держим" if r["итог"] else "нет"} | {r["дней_на_той_стороне"]} | '
          f'{100*r["глубина"]:.2f}% |')
    return dict(имя=imya, месяцев=n, лет=let, тихих=tihie, одно=odno,
                пила=len(pila), пила_возврат=len(pila_vernulis), смен=smen,
                переключений=int(tab['переключений'].sum()))


if __name__ == '__main__':
    out = ['# Пила 12-месячной средней (исследование, вне контура)', '',
           'Провизорное состояние дня тождественно нормативному сигналу §1 в последний день',
           'месяца (проверено утверждениями). Порог внутри месяца постоянен и равен средней',
           'одиннадцати предыдущих месячных закрытий.']
    svod = []
    for nabor, (days, ryady) in [('современный (SPY/IEF)', ryady_modern()),
                                 ('склейка', ryady_splice())]:
        out.append(f'\n## Набор: {nabor}')
        for imya, px in ryady.items():
            tab, _ = razbor(px, days)
            nm, np_ = _sverka(px, days, tab)
            print(f'[OK  ] {nabor} / {imya}: тождество определения на {nm} мес., '
                  f'конвенция месяца действия на {np_} днях, чётность сходится')
            svod.append(otchet(tab, f'{nabor} — нога {imya}', out))
            sc = otchet_sceplenie(tab, f'{nabor} — нога {imya}', out)
            svod[-1]['сцепление'] = sc
    dst = ROOT / 'docs/pila-sma-analiz.md'
    dst.write_text('\n'.join(out) + '\n', encoding='utf-8')
    print(f'\nзаписано: {dst}')
    for s in svod:
        print(f"{s['имя']:<34} мес {s['месяцев']:>4}  тихих {s['тихих']:>4}  "
              f"одно {s['одно']:>3}  пила {s['пила']:>3} (возврат {s['пила_возврат']:>3})  "
              f"смен {s['смен']:>3}  переключений {s['переключений']:>4}")
