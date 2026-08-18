# -*- coding: utf-8 -*-
"""Загрузка исходных данных в единый кеш.

Собирает: дневные ряды (нескорректированный и скорректированный закрытия,
поставочный месяц), спецификации контрактов, курсы валют, ставку по
трёхмесячному векселю, таблицу правил роллирования.
"""
import os, pickle
import pandas as pd, numpy as np
import konfig as K


def _spec():
    fr = [pd.read_csv(K.VYGRUZKI / d / 'contract_specs.csv') for d in K.PAPKI_VYGRUZOK
          if (K.VYGRUZKI / d / 'contract_specs.csv').exists()]
    return pd.concat(fr).drop_duplicates('symbol').set_index('symbol')


def _csv(sym, kind):
    for d in K.PAPKI_VYGRUZOK:
        p = K.VYGRUZKI / d / f'{sym}_{kind}.csv'
        if p.exists():
            return pd.read_csv(p, parse_dates=['date']).set_index('date').sort_index()
    return None


def tablica_rollov():
    t = pd.read_csv(K.TABLICA_ROLLOV).dropna(subset=['symbol']).set_index('symbol')
    t['roll_offset_days'] = t['roll_offset_days'].astype(int)
    return t


def _es_zn():
    """Непрерывные ряды и поконтрактные файлы ES/ZN/GC из данных проекта."""
    with open(K.PKL_ES_ZN, 'rb') as f:
        o = pickle.load(f)
    return o


def ryady():
    """Словарь символ -> DataFrame[cena, cena_adj, post_mes] и таблица спецификаций."""
    sp = _spec()
    tab = tablica_rollov()
    out, meta = {}, {}
    for sym in tab.index:
        u, b = _csv(sym, 'unadj'), _csv(sym, 'backadj')
        if u is None:
            continue
        df = pd.DataFrame({'cena': u['close'], 'cena_adj': b['close'],
                           'post_mes': u['delivery_month'], 'oborot': u['volume']})
        out[sym] = df.dropna(subset=['cena', 'cena_adj'])
        meta[sym] = dict(pv=sp.loc[sym, 'point_value'], val=sp.loc[sym, 'currency'],
                         birzha=sp.loc[sym, 'exchange_name'], shag=sp.loc[sym, 'tick_size'],
                         marzha=sp.loc[sym, 'margin'], istochnik='norgate_csv')
    # ES и ZN — из данных проекта (в выгрузках 2/3/4 их нет)
    o = _es_zn()
    pv_ez = {'ES': 50.0, 'ZN': 1000.0}
    for sym in ('ES', 'ZN'):
        c = o['continuous'][sym]
        df = pd.DataFrame({'cena': c['raw']['Close'], 'cena_adj': c['adj']['Close'],
                           'post_mes': np.nan, 'oborot': c['raw']['Volume']})
        out[sym] = df.dropna(subset=['cena', 'cena_adj'])
        meta[sym] = dict(pv=pv_ez[sym], val='USD', birzha='CME' if sym == 'ES' else 'CBOT',
                         shag=0.25 if sym == 'ES' else 1 / 64, marzha=np.nan,
                         istochnik='pkl_proekta')
    return out, pd.DataFrame(meta).T


def poslednie_torgi():
    """Последняя дата торгов по каждому контракту — там, где есть поконтрактные файлы."""
    o = _es_zn()
    res = {}
    for sym, d in o['contracts'].items():
        res[sym] = {k: v.index.max() for k, v in d.items() if len(v)}
    return res


def kursy(kalendar):
    """Долларов за единицу валюты, дневной ряд по общему календарю.

    Валютные фьючерсы Norgate: номинал = цена * множитель. Для иены множитель
    занижен в 100 раз, поэтому курс = цена * множитель / истинный размер.
    """
    fx = pd.DataFrame(index=kalendar)
    fx['USD'] = 1.0
    for val, (sym, razmer) in K.FX_FJUCHERSY.items():
        u = _csv(sym, 'unadj')
        if u is None:
            continue
        sp = _spec()
        # ответ 2.2: постоянный курс — последняя цена фьючерса, на всю историю
        k = float(u['close'].iloc[-1]) * sp.loc[sym, 'point_value'] / razmer
        fx[val] = k
    for val, kurs in K.FX_POSTOYANNYE.items():
        fx[val] = kurs
    return fx


def stavka_vekselja(kalendar):
    """Годовая ставка трёхмесячного векселя (DTB3), доли единицы, по календарю."""
    d = pd.read_csv(K.DTB3, parse_dates=['observation_date']).set_index('observation_date')['DTB3']
    d = pd.to_numeric(d, errors='coerce') / 100.0
    return d.reindex(kalendar.union(d.index)).ffill().reindex(kalendar)


def sobrat(put=None):
    r, meta = ryady()
    kal = pd.DatetimeIndex(sorted(set().union(*[set(v.index) for v in r.values()])))
    dan = dict(ryady=r, meta=meta, kalendar=kal, fx=kursy(kal),
               stavka=stavka_vekselja(kal), tablica=tablica_rollov(),
               posl_torgi=poslednie_torgi())
    put = put or (K.KESH / 'dannye.pkl')
    put.parent.mkdir(parents=True, exist_ok=True)
    with open(put, 'wb') as f:
        pickle.dump(dan, f)
    return dan


if __name__ == '__main__':
    d = sobrat()
    print('рынков:', len(d['ryady']), '| календарь:', d['kalendar'][0].date(), '..', d['kalendar'][-1].date(),
          '| дней:', len(d['kalendar']))
    print('валюты:', sorted(d['meta']['val'].unique()))
    print('курсы без данных на конец:', [c for c in d['fx'] if pd.isna(d['fx'][c].iloc[-1])])
    print('ставка: ', d['stavka'].iloc[0], '..', d['stavka'].iloc[-1], '| пропусков:', int(d['stavka'].isna().sum()))
