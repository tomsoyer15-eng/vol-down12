# -*- coding: utf-8 -*-
"""Движок симуляции живого счёта.

Порядок дня t:
  1) прибыль и убыток по позиции, установленной на закрытии дня t−1;
  2) начисление по векселям за календарные дни между t−1 и t;
  3) комиссия за роллы, если сегодня рынок сменил контракт;
  4) если t — последний торговый день месяца (или самый первый день прогона),
     капитал делится пополам между частями и позиция переустанавливается;
     она действует уже с первого дня следующего месяца (ответ 4.4).
"""
import pickle
import numpy as np, pandas as pd
import konfig as K
import podgotovka as PG


def komissiya(sym, birzha):
    if sym in K.MIKRO_CME:
        return K.KOMISSIYA_MIKRO_CME
    if sym in K.VALJUTNYE_FJUCHERSY:
        return K.KOMISSIYA_VALJUTNYE
    return K.KOMISSIYA_BIRZHA.get(birzha, K.KOMISSIYA_PROCHEE)


def podgotovit(p):
    kal = p['kalendar']
    syms = [s for s in p['ceny'].columns if s in K.SPISOK_WIDE or s in K.NOGI_ADDFUT]
    meta = p['meta']
    delitel = np.array([K.MIKRO.get(s, 1) for s in syms], dtype=float)
    m = dict(
        kal=kal, syms=syms, delitel=delitel,
        A=p['ceny_adj'][syms].values, P=p['ceny'][syms].values,
        SG=p['sigma'][syms].values, PL=p['pol'][syms].values,
        FX=np.array([p['fx'][meta.loc[s, 'val']].iloc[0] for s in syms], dtype=float),
        PV=np.array([float(meta.loc[s, 'pv']) for s in syms]) / delitel,
        KOM=np.array([komissiya(s, meta.loc[s, 'birzha']) for s in syms]),
        MARZHA=np.array([K.MARZHA_DOP.get(s, meta.loc[s, 'marzha']) for s in syms],
                        dtype=float) / delitel
               * np.array([p['fx'][meta.loc[s, 'val']].iloc[0] for s in syms], dtype=float),
        wide=np.array([s in K.SPISOK_WIDE for s in syms]),
        i1=[syms.index(s) for s in K.NOGI_ADDFUT if s in syms],
    )
    R = np.zeros_like(m['A'], dtype=bool)
    poz = {s: i for i, s in enumerate(syms)}
    for s, d in p['rolly'].items():
        if s in poz:
            R[kal.get_indexer(d), poz[s]] = True
    m['ROLL'] = R
    return m


def prognoz(p, m=None, nastr=None, mnozhitel=1.0, signal_mes=None, sdvig=0):
    """sdvig=0 — рабочий режим: позиция ставится на закрытии последнего торгового
    дня месяца M по сигналу месяца M и действует весь месяц M+1 (ответ 4.4).
    sdvig=-1 — заглядывание вперёд: в тот же момент берётся сигнал месяца M+1."""
    n = dict(K.NASTROJKI, **(nastr or {}))
    m = m or podgotovit(p)
    kal, syms = m['kal'], m['syms']
    sig_m = p['sig_mes'] if signal_mes is None else signal_mes
    SIG = PG.signal_dnevnoj(sig_m, kal, sdvig).reindex(columns=syms).values
    # в самый первый день прогона месяц ещё не закрыт — берём сигнал прошлого месяца
    SIG0 = PG.signal_dnevnoj(sig_m, kal, sdvig + 1).reindex(columns=syms).values
    T, M = m['A'].shape
    wide, i1 = m['wide'], m['i1']

    rho = p['rho'].reindex(pd.PeriodIndex(kal, freq='M')).values
    rho = np.where(np.isnan(rho), n['rho_do_nakopleniya'], rho)
    stavka = p['stavka'].values
    dni = np.r_[0.0, np.diff(kal.values).astype('timedelta64[D]').astype(float)]

    sig_eff = np.fmax(m['SG'], m['PL'])                     # пол на оценку колебаний
    dostup = (~np.isnan(m['P'])) & (~np.isnan(sig_eff))
    stoim = m['P'] * m['PV'] * m['FX']                      # номинал торгуемого контракта, USD

    t0 = int(kal.get_indexer([pd.Timestamp(n['nachalo'])], method='bfill')[0])
    t1 = int(kal.get_indexer([pd.Timestamp(n['konec'])], method='ffill')[0])
    pm = pd.PeriodIndex(kal, freq='M')
    posl_den = np.r_[pm[:-1] != pm[1:], True]

    poz = np.zeros(M)
    eq1 = K.SCHET * K.DOLYA_ADDFUT
    eq2 = K.SCHET * K.DOLYA_WIDE
    zp = np.zeros((T, M))
    zz = np.zeros((T, 9))

    for t in range(t0, t1 + 1):
        pnl1 = pnl2 = pr = c1 = c2 = 0.0
        if t > t0:
            d = np.nan_to_num(m['A'][t] - m['A'][t - 1])
            v = poz * d * m['PV'] * m['FX']
            pnl1, pnl2 = float(v[i1].sum()), float(v[wide].sum())
            pr = stavka[t - 1] * dni[t] / n['baza_nachisleniya']
            if n['platim_rolly']:
                c = 2.0 * np.abs(poz) * m['KOM'] * (m['ROLL'][t] & (poz != 0))
                c1, c2 = float(c[i1].sum()), float(c[wide].sum())
        pr1, pr2 = eq1 * pr, eq2 * pr
        eq1 += pnl1 + pr1 - c1
        eq2 += pnl2 + pr2 - c2

        if posl_den[t] or t == t0:
            if n['delim_kapital_popolam']:                  # ответ 5.7
                eq1 = eq2 = 0.5 * (eq1 + eq2)
            sg = SIG0[t] if t == t0 else SIG[t]
            est_t = dostup[t] & (~np.isnan(sg))
            dlin = est_t & (sg == 1)
            nn = int((est_t & wide).sum()) if n['n_v_formule'] == 'vse_dostupnye' \
                else int((dlin & wide).sum())
            cel = np.zeros(M)
            if nn > 0:
                s = K.CEL_KOLEBANIJ / np.sqrt(nn + nn * (nn - 1) * rho[t])
                sgm = np.where(sig_eff[t] > 0, sig_eff[t], np.inf)
                nom = np.where(dlin & wide, s * eq2 / sgm, 0.0)
                nom = np.minimum(nom, n['potolok_nominala'] * eq2)
                cel = np.where(stoim[t] > 0, nom / stoim[t], 0.0)
            for i in i1:
                if dlin[i]:
                    cel[i] = eq1 / stoim[t, i]              # 0.5 * плечо 2.00 * капитал
            nov = np.floor(np.nan_to_num(cel * mnozhitel) + 1e-9)
            nov = np.where(dlin, np.maximum(nov, 0.0), 0.0)
            c = np.abs(nov - poz) * m['KOM']
            eq1 -= float(c[i1].sum()); eq2 -= float(c[wide].sum())
            c1 += float(c[i1].sum()); c2 += float(c[wide].sum())
            poz = nov

        zp[t] = poz
        nm = np.abs(poz) * np.nan_to_num(stoim[t])
        zz[t] = (eq1 + eq2, eq1, eq2, pr1 + pr2, c1 + c2,
                 float(nm[wide].sum()), float(nm[i1].sum()),
                 float(np.nansum(np.abs(poz) * m['MARZHA'])), int((poz != 0).sum()))

    ind = kal[t0:t1 + 1]
    ekv = pd.DataFrame(zz[t0:t1 + 1], index=ind, columns=[
        'kapital', 'chast1', 'chast2', 'protsent', 'izderzhki',
        'nominal_chast2', 'nominal_chast1', 'zalog', 'rynkov_v_pozicii'])
    ekv.index.name = 'data'
    poz_df = pd.DataFrame(zp[t0:t1 + 1], index=ind, columns=syms).astype(int)
    poz_df.index.name = 'data'
    return ekv, poz_df


if __name__ == '__main__':
    p = pickle.load(open(K.KESH / 'paneli.pkl', 'rb'))
    m = podgotovit(p)
    e, z = prognoz(p, m)
    g = (e.index[-1] - e.index[0]).days / 365.25
    print('рынков в модели', len(m['syms']), '| окно', e.index[0].date(), e.index[-1].date())
    print('капитал:', round(e['kapital'].iloc[0]), '->', round(e['kapital'].iloc[-1]))
    print('CAGR', round((e['kapital'].iloc[-1] / e['kapital'].iloc[0]) ** (1 / g) - 1, 4))
    r = e['kapital'].pct_change().dropna()
    print('колебания год', round(r.std() * np.sqrt(252), 4),
          '| просадка', round((e['kapital'] / e['kapital'].cummax() - 1).min(), 4))
    print('номинал/капитал (медиана):', round((e['nominal_chast2'] / e['chast2']).median(), 2),
          '| залог/счёт:', round((e['zalog'] / e['kapital']).median(), 3),
          '| рынков в позиции:', round(e['rynkov_v_pozicii'].mean(), 1))
