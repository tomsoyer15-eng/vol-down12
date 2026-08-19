# -*- coding: utf-8 -*-
"""Подготовка выровненных панелей: цены, сигнал, колебания, пол, связь, роллы."""
import pickle
import numpy as np, pandas as pd
import konfig as K


def _panel(ryady, kal, pole):
    d = pd.DataFrame({s: v[pole].reindex(kal) for s, v in ryady.items()})
    for s, v in ryady.items():
        m = (kal >= v.index[0]) & (kal <= v.index[-1])
        d.loc[m, s] = d.loc[m, s].ffill()
    return d


def mesyachnye_zakrytiya(ryady, pole):
    """Закрытие последнего торгового дня месяца по календарю самого рынка (ответ 4.5)."""
    out = {}
    for s, v in ryady.items():
        g = v[pole].groupby([v.index.year, v.index.month]).last()
        idx = pd.PeriodIndex([pd.Period(year=a, month=b, freq='M') for a, b in g.index], freq='M')
        out[s] = pd.Series(g.values, index=idx)
    return pd.DataFrame(out).sort_index()


def signal_mesyachnyj(mz, n=K.MESYACEV_SMA):
    """Ответы 4.2, 4.3: средняя из n закрытий включая текущее, строгое «больше»."""
    sma = mz.rolling(n, min_periods=n).mean()
    return (mz > sma).astype(float).where(sma.notna())


def signal_dnevnoj(sig_m, kal, sdvig=1):
    """Сигнал месяца M действует весь месяц M+sdvig."""
    per = pd.PeriodIndex(kal, freq='M')
    src = sig_m.copy()
    src.index = src.index + sdvig
    return src.reindex(per).set_axis(kal)


def kolebaniya(ryady, kal, okno=K.OKNO_VOL, min_dnej=K.MIN_DNEJ_VOL):
    """Годовые колебания в долях номинала (ответы 5.3, 1.2).

    Считается по СОБСТВЕННЫМ торговым дням рынка, а не по общему календарю
    портфеля: реиндексация всех 64 рынков на объединённый календарь с ffill
    (нужная для дневного П/У и сигнала) на дне без торгов конкретного рынка
    даёт нулевую дневную доходность вместо «дня нет» — это разбавляет
    дисперсию лишними нулями и занижает σ. Обнаружено 18.08.2026 построчной
    сверкой с заказчиком: у ES 244 таких дня (3.2% истории) — все американские
    биржевые праздники, когда CME закрыт, а другой из 64 рынков торгует;
    занижение σ ровно объясняло разницу 19.08% против верных 19.40% годовых.
    Итоговый ряд переносится (ffill) на общий календарь — решения принимаются
    по нему на дату месячного пересчёта, а не по родному календарю рынка.
    """
    r_panel = pd.DataFrame(index=kal, columns=list(ryady.keys()), dtype=float)
    sig = pd.DataFrame(index=kal, columns=list(ryady.keys()), dtype=float)
    for s_, v in ryady.items():
        r = v['cena_adj'].diff() / v['cena'].shift()
        god = r.rolling(okno, min_periods=min_dnej).std() * np.sqrt(252.0)
        r_panel[s_] = r.reindex(kal)                 # NaN на нерабочих для рынка днях — не нули
        sig[s_] = god.reindex(kal).ffill()
    return r_panel, sig


def pol_kolebanij(sig_god, procentil=None, min_dnej=None):
    """Ответ 5.4: накопительный процентиль собственной истории оценки, со сдвигом на день."""
    procentil = K.NASTROJKI['pol_procentil'] if procentil is None else procentil
    min_dnej = K.NASTROJKI['pol_min_dnej'] if min_dnej is None else min_dnej
    pol = pd.DataFrame(np.nan, index=sig_god.index, columns=sig_god.columns)
    for s in sig_god.columns:
        v = sig_god[s]
        q = v.expanding(min_periods=min_dnej).quantile(procentil / 100.0)
        pol[s] = q.shift(1)
    return pol


def svyaz_nakopitelno(r_dnevnye, nastr=None):
    """Ответ 5.2: средняя парная связь месячных доходностей, только по прошлому."""
    n = dict(K.NASTROJKI, **(nastr or {}))
    per = pd.PeriodIndex(r_dnevnye.index, freq='M')
    rm = r_dnevnye.groupby(per).sum(min_count=1)
    znach = {}
    for i, mes in enumerate(rm.index):
        if i + 1 < n['rho_min_istorii']:
            znach[mes] = n['rho_do_nakopleniya']
            continue
        x = rm.iloc[:i + 1]                       # все месяцы строго ДО следующего
        c = x.corr(min_periods=n['rho_min_par_mesyacev']).values
        v = c[np.triu_indices_from(c, 1)]
        v = v[~np.isnan(v)]
        znach[mes] = max(0.0, float(np.mean(v))) if len(v) else n['rho_do_nakopleniya']
    s = pd.Series(znach)
    s.index = pd.PeriodIndex(s.index, freq='M') + 1           # применяется в следующем месяце
    return s


def daty_rollov(ryady, posl_torgi, tablica, kal):
    """Ответ 0.2: даты роллов из смены delivery_month; ES и ZN — по правилу §21 (N=1/N=21).

    Для ES/ZN колонка delivery_month тоже заполнена (выгрузка №1 — обычная
    склейка Norgate), поэтому ветку по правилу §21 нужно форсировать явно —
    иначе она недостижима и для этих двух рынков даты ролла молча берутся
    так же, как для остальных 62 (смена delivery_month), а не по N дням от
    последних торгов, как подтвердил заказчик в ответе 7.4. Найдено ревью
    18.08.2026 (роли не пуст. §21-веткой ни разу не проходили).
    """
    out = {}
    for s, v in ryady.items():
        dm = v['post_mes'].dropna()
        if len(dm) and s not in K.NOGI_ADDFUT:
            dm = dm.astype(int).astype(str)
            smena = dm[(dm != dm.shift()) & dm.shift().notna()]
            out[s] = pd.DatetimeIndex(smena.index)
            continue
        N = int(tablica.loc[s, 'roll_offset_days'])            # ответ 7.4: ES N=1, ZN N=21
        ltd = posl_torgi.get(s, {})
        mes = tablica.loc[s, 'delivery_months']
        kod = sorted([(k, t) for k, t in ltd.items() if k[4] in mes], key=lambda x: x[1])
        svoj, d = v.index, []
        for _, L in kod:
            k = svoj[(svoj <= L) & ((L - svoj).days <= N)]
            if len(k):
                d.append(k[0])
        out[s] = pd.DatetimeIndex(sorted(set(d)))
    return out


def sobrat(dan=None, nastr=None, put=None):
    """put — куда писать кеш панелей; по умолчанию kesh/paneli.pkl (общий для
    zapusk.py/dvizhok.py/proverki.py). Другой путь обязателен для АЛЬТЕРНАТИВНЫХ
    наборов данных (например, склеенных с синтетикой в polnoe_okno.py) — иначе
    вызов молча подменяет общий кеш, и следующий standalone-запуск dvizhok.py
    или proverki.py читает не те панели без единой ошибки (найдено ревью 18.08)."""
    n = dict(K.NASTROJKI, **(nastr or {}))
    if dan is None:
        dan = pickle.load(open(K.KESH / 'dannye.pkl', 'rb'))
    r, kal = dan['ryady'], dan['kalendar']
    ceny = _panel(r, kal, 'cena')
    ceny_adj = _panel(r, kal, 'cena_adj')
    mz = mesyachnye_zakrytiya(r, n['ryad_signala'])            # ответ 4.1 — по unadj
    sig_m = signal_mesyachnyj(mz)
    rd, sig_god = kolebaniya(r, kal)
    p = dict(ceny=ceny, ceny_adj=ceny_adj, mes_zakr=mz, sig_mes=sig_m,
             r_dnevnye=rd, sigma=sig_god, pol=pol_kolebanij(sig_god),
             rho=svyaz_nakopitelno(rd, nastr),
             rolly=daty_rollov(r, dan['posl_torgi'], dan['tablica'], kal),
             kalendar=kal, meta=dan['meta'], fx=dan['fx'], stavka=dan['stavka'],
             tablica=dan['tablica'])
    put = put or (K.KESH / 'paneli.pkl')
    put.parent.mkdir(parents=True, exist_ok=True)
    with open(put, 'wb') as f:
        pickle.dump(p, f)
    return p


if __name__ == '__main__':
    p = sobrat()
    print('рынков', p['ceny'].shape[1], '| дней', p['ceny'].shape[0])
    print('доля длинных по сигналу (unadj):', round(float(p['sig_mes'].stack().mean()), 4))
    r = p['rho']
    print('rho:', {str(k): round(v, 4) for k, v in r.iloc[[0, len(r)//2, -1]].items()})
    print('роллов', sum(len(v) for v in p['rolly'].values()), '| ES', len(p['rolly']['ES']),
          '| ZN', len(p['rolly']['ZN']))
