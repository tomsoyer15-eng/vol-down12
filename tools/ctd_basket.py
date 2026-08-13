#!/usr/bin/env python3
"""Корзинный расчёт дешевейшей к поставке для ZN: список бумаг, конверсионные факторы,
DV01 контракта. Ответ на встречный запрос автора спецификации.

Корзина ZN (CME): казначейские ноты с исходным сроком не более 10 лет и остаточным
от 6,5 до 10 лет на первый день месяца поставки.

Конверсионный фактор — цена бумаги при доходности ровно 6% на первый день месяца
поставки, со сроком, округлённым ВНИЗ до целого квартала. Формула CME:
    v  = z, если z < 7, иначе z - 6
    a  = 1 / 1,03^(v/6)
    b  = (c/2) x (6-v)/6
    cc = 1 / 1,03^(2n), если z < 7, иначе 1 / 1,03^(2n+1)
    d  = (c/0,06) x (1 - cc)
    CF = a x [c/2 + cc + d] - b
где n — целые годы, z — целые месяцы сверх n лет (0, 3, 6 или 9), c — купон.

Бумаги оцениваются по бескупонной кривой ФРС (Gurkaynak-Sack-Wright, непрерывное
начисление). Дешевейшая определяется по минимуму отношения цены к конверсионному
фактору — стандартный признак при отсутствии ставок репо.
"""
import json, sys
from pathlib import Path
from urllib.request import Request, urlopen
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'r32build'))
import bondleg as B

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36'
API = ('https://www.treasurydirect.gov/TA_WS/securities/search?format=json&type=Note'
       '&dateFieldName=maturityDate&startDate={a}&endDate={b}&pagesize=500')


def basket(delivery_first_day):
    """Ноты с остаточным сроком 6,5-10 лет на первый день месяца поставки."""
    d0 = pd.Timestamp(delivery_first_day)
    lo, hi = d0 + pd.DateOffset(years=6, months=6), d0 + pd.DateOffset(years=10)
    url = API.format(a=lo.strftime('%Y-%m-%d'), b=hi.strftime('%Y-%m-%d'))
    with urlopen(Request(url, headers={'User-Agent': UA}), timeout=90) as f:
        js = json.load(f)
    rows = {}
    for s in js:
        term = s.get('securityTerm', '')
        yrs = int(term.split('-')[0]) if term and term.split('-')[0].isdigit() else 99
        if yrs > 10:                                   # исходный срок не более 10 лет
            continue
        c = s.get('interestRate') or ''
        if not c:                                      # ещё не размещённая бумага
            continue
        m = pd.Timestamp(s['maturityDate'][:10])
        if not (lo <= m <= hi):
            continue
        rows[s['cusip']] = dict(cusip=s['cusip'], term=term, maturity=m,
                                coupon=float(c) / 100, issue=pd.Timestamp(s['issueDate'][:10]))
    return pd.DataFrame(rows.values()).sort_values('maturity').reset_index(drop=True)


def conv_factor(coupon, maturity, delivery_first_day):
    d0 = pd.Timestamp(delivery_first_day)
    months = (maturity.year - d0.year) * 12 + (maturity.month - d0.month)
    if maturity.day < d0.day:
        months -= 1
    months = (months // 3) * 3                          # вниз до целого квартала
    n, z = months // 12, months % 12
    v = z if z < 7 else z - 6
    c = coupon
    a = 1 / 1.03 ** (v / 6)
    b = (c / 2) * (6 - v) / 6
    cc = 1 / 1.03 ** (2 * n) if z < 7 else 1 / 1.03 ** (2 * n + 1)
    d = (c / 0.06) * (1 - cc)
    return a * (c / 2 + cc + d) - b


def price_and_duration(coupon, maturity, asof, curve_row, grid):
    """Цена за 100 номинала и модифицированная дюрация по бескупонной кривой."""
    t = []
    m = maturity
    while m > asof:
        t.append((m - asof).days / 365.25)
        m -= pd.DateOffset(months=6)
    t = np.array(sorted(t))
    if len(t) == 0:
        return np.nan, np.nan
    cf = np.full(len(t), coupon / 2 * 100)
    cf[-1] += 100.0
    yv = np.interp(t, grid, curve_row)
    df = np.exp(-yv * t)
    P = float((cf * df).sum())
    D = float((t * cf * df).sum() / P)                  # непрерывное начисление
    return P, D


if __name__ == '__main__':
    DELIV = sys.argv[1] if len(sys.argv) > 1 else '2026-09-01'
    bk = basket(DELIV)
    y = B.curve()
    asof = y.dropna().index[-1]
    grid = np.arange(1, 11, dtype=float)
    row = y.loc[asof, [f'SVENY{n:02d}' for n in range(1, 11)]].values.astype(float)

    print(f'Корзина поставки ZN, месяц поставки {pd.Timestamp(DELIV):%m.%Y}, '
          f'кривая на {asof:%d.%m.%Y}\n')
    print(f"{'CUSIP':<12}{'срок':<10}{'погашение':>12}{'купон':>8}{'CF':>9}"
          f"{'цена':>9}{'дюрация':>9}{'цена/CF':>10}")
    out = []
    for _, r in bk.iterrows():
        cf = conv_factor(r.coupon, r.maturity, DELIV)
        P, D = price_and_duration(r.coupon, r.maturity, asof, row, grid)
        out.append((r.cusip, r.term, r.maturity, r.coupon, cf, P, D, P / cf))
        print(f"{r.cusip:<12}{r.term:<10}{r.maturity:%d.%m.%Y:>12}{r.coupon*100:>7.3f}%"
              f"{cf:>9.4f}{P:>9.3f}{D:>9.3f}{P/cf:>10.3f}")
    o = pd.DataFrame(out, columns=['cusip', 'term', 'mat', 'coupon', 'cf', 'P', 'D', 'adj'])
    ctd = o.loc[o['adj'].idxmin()]
    print(f"\nдешевейшая к поставке: {ctd.cusip}, погашение {ctd.mat:%d.%m.%Y}, "
          f"купон {ctd.coupon*100:.3f}%, CF {ctd.cf:.4f}")
    dv01_bond = ctd.D * ctd.P / 100 * 1e-4 * 100_000    # доллары на б.п. на $100 тыс. номинала
    dv01_fut = dv01_bond / ctd.cf
    print(f"DV01 самой бумаги:      ${dv01_bond:,.2f} на б.п. (на $100 000 номинала)")
    print(f"DV01 контракта = DV01/CF: ${dv01_fut:,.2f} на б.п.")
    print(f"дюрация, подразумеваемая контрактом: {ctd.D / ctd.cf * ctd.P / (ctd.P / ctd.cf) :.2f} "
          f"(= дюрация дешевейшей {ctd.D:.2f})")
