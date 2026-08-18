# -*- coding: utf-8 -*-
"""Основной прогон, самопроверки и вывод CSV."""
import pickle, sys
import numpy as np, pandas as pd
import konfig as K, dvizhok as D, metriki as M


def sluchajnyj_signal(sig_m, seed):
    rng = np.random.default_rng(seed)
    v = rng.integers(0, 2, size=sig_m.shape).astype(float)
    return pd.DataFrame(np.where(sig_m.isna(), np.nan, v), index=sig_m.index, columns=sig_m.columns)


def zapisat(ekv, poz, imya):
    K.VYVOD.mkdir(parents=True, exist_ok=True)
    e = ekv.copy()
    for c in ('kapital', 'chast1', 'chast2', 'protsent', 'izderzhki',
              'nominal_chast2', 'nominal_chast1', 'zalog'):
        e[c] = e[c].round(2)
    e['rynkov_v_pozicii'] = e['rynkov_v_pozicii'].astype(int)
    e.to_csv(K.VYVOD / f'equity_{imya}.csv')
    poz.to_csv(K.VYVOD / f'pozicii_{imya}.csv')


def main():
    p = pickle.load(open(K.KESH / 'paneli.pkl', 'rb'))
    m = D.podgotovit(p)
    st = p['stavka']
    otchet = []

    # --- основной прогон -----------------------------------------------------
    ekv, poz = D.prognoz(p, m)
    zapisat(ekv, poz, 'osnovnoj')
    svod = [M.svodka(ekv, st, 'основной (n = рынки с данными, роллы оплачены)')]

    # --- ряд для сверки с заказчиком: роллы не оплачиваются (ответ 7.5) ------
    e_bez, z_bez = D.prognoz(p, m, nastr=dict(platim_rolly=False))
    zapisat(e_bez, z_bez, 'bez_oplaty_rollov')
    svod.append(M.svodka(e_bez, st, 'сверочный (роллы не оплачены)'))

    # --- второй прогон: n = число рынков в позиции (ответ 5.1) ---------------
    e_np, z_np = D.prognoz(p, m, nastr=dict(n_v_formule='v_pozicii'))
    zapisat(e_np, z_np, 'n_v_pozicii')
    svod.append(M.svodka(e_np, st, 'улучшенный (n = рынки в позиции)'))

    # --- самопроверка 1: позиции x0.7 ---------------------------------------
    e07, z07 = D.prognoz(p, m, mnozhitel=0.7)
    svod.append(M.svodka(e07, st, 'самопроверка 1: позиции x0.7'))

    # --- самопроверка 3: сигнал вперёд на месяц ------------------------------
    evp, _ = D.prognoz(p, m, sdvig=-1)
    svod.append(M.svodka(evp, st, 'самопроверка 3: заглядывание вперёд'))

    # --- самопроверка 2: случайный сигнал, 20 зёрен --------------------------
    sluch = []
    for seed in range(20):
        es, _ = D.prognoz(p, m, signal_mes=sluchajnyj_signal(p['sig_mes'], seed))
        sluch.append(dict(seed=seed, CAGR=M.cagr(es['kapital']),
                          prosadka=M.prosadka(es['kapital']),
                          kolebaniya=M.kolebaniya(es['kapital'])))
    sluch = pd.DataFrame(sluch).set_index('seed')

    t = M.tablica_svodok(svod)
    ryn = M.po_rynkam(poz, p['ceny'])
    nikogda = [s for s in poz.columns if int(poz[s].abs().max()) == 0]

    god = M.let(ekv['kapital'])
    stroki = []
    stroki.append('# Итог независимого прогона\n')
    stroki.append(f"Окно {ekv.index[0].date()} … {ekv.index[-1].date()} ({god:.2f} года), "
                  f"счёт {K.SCHET:,.0f} USD, рынков в модели {len(m['syms'])} "
                  f"(широкая нога {int(m['wide'].sum())} из 62, части 1 — ES и ZN).\n")
    stroki.append('## Итоговые меры (в процентах)\n')
    stroki.append(t.to_string())
    stroki.append('\n## Самопроверка 1: позиции x0.7')
    a, b = t.loc['основной (n = рынки с данными, роллы оплачены)'], t.loc['самопроверка 1: позиции x0.7']
    stroki.append(f"доход {a.CAGR}% -> {b.CAGR}%, просадка {a.prosadka}% -> {b.prosadka}%, "
                  f"колебания {a.kolebaniya}% -> {b.kolebaniya}%")
    stroki.append(f"отношение доход-к-просадке {abs(a.CAGR/a.prosadka):.3f} -> {abs(b.CAGR/b.prosadka):.3f} "
                  f"(растёт: часть счёта лежит в векселях и доход от них не уменьшается)")
    stroki.append('\n## Самопроверка 2: случайный сигнал, 20 зёрен')
    stroki.append(f"CAGR: мин {sluch.CAGR.min()*100:.2f}%, медиана {sluch.CAGR.median()*100:.2f}%, "
                  f"макс {sluch.CAGR.max()*100:.2f}% (эталон {a.CAGR}%)")
    stroki.append(f"просадка: мин {sluch.prosadka.min()*100:.2f}%, макс {sluch.prosadka.max()*100:.2f}%")
    stroki.append(f"прогонов лучше эталона: {int((sluch.CAGR*100 > a.CAGR).sum())} из 20; "
                  f"все 20 прогонов различны: {sluch.CAGR.nunique() == 20}")
    stroki.append('\n## Самопроверка 3: сигнал сдвинут вперёд на месяц')
    c = t.loc['самопроверка 3: заглядывание вперёд']
    stroki.append(f"CAGR {a.CAGR}% -> {c.CAGR}%, просадка {a.prosadka}% -> {c.prosadka}% — детектор утечки жив"
                  if c.CAGR > a.CAGR else 'ЗАГЛЯДЫВАНИЕ НЕ УЛУЧШИЛО РЕЗУЛЬТАТ — сигнал где-то не применяется')
    stroki.append('\n## Самопроверка 4: реализованные колебания')
    stroki.append(f"весь счёт {M.kolebaniya(ekv['kapital'])*100:.2f}%, часть 1 {M.kolebaniya(ekv['chast1'])*100:.2f}%, "
                  f"часть 2 {M.kolebaniya(ekv['chast2'])*100:.2f}% при цели 13%")
    stroki.append(f"при n = рынки в позиции: часть 2 {M.kolebaniya(e_np['chast2'])*100:.2f}%, "
                  f"весь счёт {M.kolebaniya(e_np['kapital'])*100:.2f}%")
    stroki.append('\n## Самопроверка 5: доля времени в позиции и число контрактов')
    stroki.append(ryn.to_string())
    stroki.append(f"\nРынки, где ни разу не набирается целый контракт: {nikogda if nikogda else 'нет'}")
    stroki.append('\n## Самопроверка 6: номинал и залог')
    stroki.append(f"номинал широкой ноги к её капиталу: медиана {(ekv.nominal_chast2/ekv.chast2).median():.2f}, "
                  f"5-й проц. {(ekv.nominal_chast2/ekv.chast2).quantile(.05):.2f}, "
                  f"95-й {(ekv.nominal_chast2/ekv.chast2).quantile(.95):.2f}")
    stroki.append(f"номинал всего счёта к счёту: медиана {((ekv.nominal_chast2+ekv.nominal_chast1)/ekv.kapital).median():.2f}")
    stroki.append(f"залог к счёту: медиана {(ekv.zalog/ekv.kapital).median():.3f} "
                  f"(по сегодняшним депозитам, применённым ко всей истории)")
    stroki.append(f"\nИздержки: {ekv.izderzhki.sum()/god:,.0f} USD в год в среднем; "
                  f"из них роллы {(ekv.izderzhki.sum()-e_bez.izderzhki.sum())/god:,.0f} USD в год")
    txt = '\n'.join(stroki)
    (K.VYVOD / 'otchet.md').write_text(txt)
    print(txt)
    sluch.round(4).to_csv(K.VYVOD / 'sluchajnyj_signal.csv')
    ryn.to_csv(K.VYVOD / 'po_rynkam.csv')


if __name__ == '__main__':
    main()
