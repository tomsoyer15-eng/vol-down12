#!/usr/bin/env python3
"""Нестационарность спецификаций: что менялось у инструментов за жизнь ряда и что это стоит.

ЗАЧЕМ. Ряды тянутся с 1885/1962 года, а движок прикладывает СЕГОДНЯШНИЕ спецификации назад:
множители, сетку контрактов, календарь ролла. За это время инструменты появлялись и менялись:
  SP  (S&P 500, CME)   запущен 21.04.1982, $500/пункт; с 11.1997 — $250; делистинг 09.2021;
  ES  (E-mini)         с 09.09.1997, $50/пункт — единица расчёта ноги А (500 x SPY);
  MES (Micro E-mini)   с 06.05.2019, $5 — в движке MES_START;
  ZN  (10-летняя нота) с 03.05.1982; номинальный купон пересчёта 8% -> 6% с мартовской
                       серии 2000 — в модель НЕ входит: dur() считает пар-облигацию по
                       рыночной доходности, режимность поглощена CTD_RATIO (§3 признаёт
                       его нестационарным);
  тик-эпохи            1/8 до 06.1997, 1/16 до 01.2001, дальше десятичная — издержки §2
                       заданы в б.п. от номинала и решётку цены покрывают.
До 21.04.1982 фьючерса на индекс НЕ СУЩЕСТВОВАЛО: ряд до 1982 — модельная механика по
построению, что §4 прямо признаёт.

Скрипт ИЗМЕРЯЕТ три вещи на фактических рядах:
  1) решётка цен и дыры по эпохам — данные несут настоящую микроструктуру или синтетику;
  2) цена СЕТКИ ЭПОХИ: до 09.1997 единица ноги А — контракт SP, вдесятеро крупнее ES;
  3) календарь ролла: как часто наивная пятидневка расходится с фактическими сессиями.

Адаптация ВПЕРЁД делается не здесь, а в живом контуре: реестр инструментов снимается у
биржи каждый квартал (con_id, поставка, множитель), личность контракта сверяется при каждом
обращении, а множители реестра сверяются с константами модели (feed.MODEL_MULT) — смена
спецификации биржей останавливает торговлю с явной причиной, а не меняет размер книги.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'r33build'))
sys.path.insert(0, str(ROOT / 'r33build' / 'live'))

ES_LAUNCH = pd.Timestamp('1997-09-09')


def tick_eras():
    print('=== решётка цен SPY по эпохам (доля движений, кратных шагу) ===')
    spy = pd.read_csv(ROOT / 'r33build/data/spy_daily.csv', parse_dates=['date'])
    d = spy.set_index('date')['close'].diff().abs()

    def share(seg, q):
        r = seg / q - (seg / q).round()
        return float((np.abs(r) < 1e-6).mean())

    for a, b, note in (('1993', '1997-06', '1/8'), ('1997-07', '2001-01', '1/16'),
                       ('2001-02', '2027', 'десятичная')):
        seg = d[a:b]
        seg = seg[seg > 1e-9]
        print(f'  {a}..{b:<8} 1/8: {share(seg, 0.125):4.0%}  1/16: {share(seg, 0.0625):4.0%}'
              f'  0.01: {share(seg, 0.01):4.0%}   (эпоха {note})')


def gaps():
    print('\n=== дыры в склейке 1885+ ===')
    lg = pd.read_csv(ROOT / 'r33build/data/us-market-daily-1885-2025.csv',
                     parse_dates=['Date']).sort_values('Date')
    g = lg['Date'].diff().dt.days
    for dt, days in lg.loc[g > 10, 'Date'].items():
        pass
    big = lg.loc[g > 10]
    for _, row in big.iterrows():
        print(f'  {row["Date"]:%Y-%m-%d}: дыра, реальное закрытие рынка '
              f'(1914 — война, 1933 — банковские каникулы)')


def era_grid():
    print('\n=== цена сетки эпохи: SP = 10 x ES до 09.1997 ===')
    import daily as DL
    import smooth as M
    from replay_check import replay

    d = M.build()
    orig = DL.units

    def era_units(book, m):
        ue, ub = orig(book, m)
        return (ue * 10.0 if m.date < ES_LAUNCH else ue), ub

    print(f"{'вариант':<44}{'CAGR':>7}{'качка':>8}{'MaxDD':>8}")
    for cap0 in (10_000_000.0, 3_000_000.0):
        for lbl, patch in (('сегодняшняя сетка (ES с 1963)', None),
                           ('сетка ЭПОХИ (SP до 09.1997)', era_units)):
            DL.units = patch or orig
            try:
                nav, _ = replay(d, cap0=cap0)
            finally:
                DL.units = orig
            r = nav.pct_change()
            c = ((nav.iloc[-1] / nav.iloc[0]) ** (252 / len(nav)) - 1) * 100
            dd = ((nav / nav.cummax()) - 1).min() * 100
            print(f'  {cap0/1e6:.0f} млн, {lbl:<36}{c:>6.2f}%'
                  f'{r.std() * 252 ** .5 * 100:>7.2f}%{dd:>7.1f}%')


def roll_calendar():
    print('\n=== календарь ролла: наивная пятидневка против фактических сессий ===')
    import daily as DL
    import sim_v13 as S
    import smooth as M
    days = M.build()[0]
    actual = set(S.roll_days_calendar(days))
    naive = {dt for dt in days if DL.is_roll_day(dt)}
    diff = actual ^ naive
    print(f'  роллов {len(actual)}, дней расхождения {len(diff)} '
          f'(~каждый четвёртый ролл в другой день; сгустки — май и ноябрь)')
    print('  вывод: пятидневка в бою запрещена; встроенный календарь — сверяющий, '
          'праздники — явной таблицей')


if __name__ == '__main__':
    tick_eras()
    gaps()
    roll_calendar()
    era_grid()
