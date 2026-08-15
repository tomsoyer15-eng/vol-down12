#!/usr/bin/env python3
"""Доказательство эквивалентности боевого контура симулятору.

Боевой контур daily.step() написан НЕЗАВИСИМО от sim164: другая структура, капитал
подаётся извне, состояние переносимо через перезапуск. Совпадение поэтому не
предполагается, а проверяется: контур прогоняется сессия за сессией по всей истории,
рыночный P&L добавляется снаружи ровно по формуле §2, и полученный NAV сравнивается с
NAV симулятора.

Сверяются не только итоговые цифры, но и ПОЗИЦИИ на каждой сессии: два кода могут дать
одинаковый NAV разными книгами, и это было бы совпадением, а не эквивалентностью.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'live'))
import sim_v13 as S
from sim_v164 import sim164
import daily as DL


def replay(d, band=None, cap=2.0, spread_pp=0.5, cap0=10_000_000.0):
    days, re, rb, rfv, spy_close, dref, st_eq, st_bd = d
    st_eq = S.strict_states(st_eq, days); st_bd = S.strict_states(st_bd, days)
    sd = spread_pp / 100 / 252
    rolls = S.roll_days_calendar(days)
    book = DL.Book(d_fix=dref[0])
    e = cap0
    nav = np.empty(len(days)); pos = np.empty((len(days), 2), dtype=np.int64)
    extras = []
    for i, dte in enumerate(days):
        j = max(i - 1, 0)
        m = DL.Market(date=dte, px_eq_prev=spy_close[j], dref_prev=dref[j],
                      dref_today=dref[i], px_eq_today=spy_close[i],
                      roll_today=(dte in rolls), st_eq=bool(st_eq[i]), st_bd=bool(st_bd[i]))
        dec = DL.step(book, m, e, band=band, cap=cap, check_guards=False)
        book = dec.book_after
        e = dec.capital_after_costs
        pos[i] = (book.n_e, book.n_b)
        extras.append((book.ser_a, book.ser_b, book.es_held, book.roll_pending))
        if i == 0:
            nav[i] = e                       # first_day='fixed', как в ред. 4
        else:
            e = (e * (1 + rfv[i])
                 + dec.exposure['А'] * (re[i] - rfv[i] - sd)
                 + dec.exposure['Б'] * (rb[i] - rfv[i] - sd))
            nav[i] = e
        # замыкание сессии по факту закрытия — в бою здесь читается NLV брокера
        book = DL.close_out(book, spy_close[i], dref[i], nav[i])
    return pd.Series(nav, index=days), pos, extras


def positions_of_sim(d, band=None, cap=2.0):
    """NAV И РЯД ПОЗИЦИЙ симулятора. Одинаковый NAV при разных книгах в принципе возможен,
    поэтому сверять только NAV недостаточно: sim164 отдаёт книгу на конец каждой сессии."""
    rec = []
    r, nav, st = sim164(*d, cap=cap, first_day='fixed', band=band, record=rec)
    return nav, st, np.array(rec, dtype=np.int64)


def replay_e(d, band=None, cap=2.0, cap0=10_000_000.0):
    """Прогон контура маршрута Е против sim_etf. О-3-Е отключается: в бэктесте его нет,
    и сверяется именно арифметика книги, а не поведение критерия."""
    days, re, rb, rfv, spy_close, dref, st_eq, st_bd = d
    st_eq = S.strict_states(st_eq, days); st_bd = S.strict_states(st_bd, days)
    pb = 100.0 * np.cumprod(1.0 + np.asarray(rb))
    book = DL.BookE(); e = cap0
    nav = np.empty(len(days)); pos = np.empty((len(days), 2), dtype=np.int64)
    extras = []
    for i, dte in enumerate(days):
        j = max(i - 1, 0)
        m = DL.MarketE(date=dte, px_eq_prev=spy_close[j], px_bd_prev=pb[j],
                       px_eq_today=spy_close[i], px_bd_today=pb[i],
                       st_eq=bool(st_eq[i]), st_bd=bool(st_bd[i]))
        dec = DL.step_e(book, m, e, band=band, cap=cap, check_o3e=False)
        book = dec.book_after
        e = dec.capital_after_costs
        c = dec.daily_costs
        e -= (c['ter'] + c['drag'] + c['loan'])
        pos[i] = (book.n_eq, book.n_bd)
        P = dec.exposure['А'] + dec.exposure['Б']
        if i == 0:
            nav[i] = e
        else:
            cash = e - P
            e = e + cash * rfv[i] + dec.exposure['А'] * re[i] + dec.exposure['Б'] * rb[i]
            nav[i] = e
        book = DL.close_out_e(book, spy_close[i], pb[i], nav[i])
    return pd.Series(nav, index=days), pos


if __name__ == '__main__':
    ok = True
    for nm, d in (('современное окно 2003–2026', S.build_modern()),
                  ('склейка 1963–2026', S.build_splice())):
        nav_live, pos, extras = replay(d)
        nav_sim, st, pos_sim = positions_of_sim(d)
        same_idx = nav_live.index.equals(nav_sim.index)
        close = np.allclose(nav_live.values, nav_sim.values, rtol=1e-12, atol=0.0)
        rel = np.max(np.abs(nav_live.values / nav_sim.values - 1))
        c_l, dd_l = S.met(nav_live.pct_change().fillna(0))
        c_s, dd_s = S.met(nav_sim.pct_change().fillna(0))
        print(f"\n=== {nm} ===")
        print(f"  NAV: индексы {'совпали' if same_idx else 'РАЗОШЛИСЬ'}, "
              f"максимальное относительное расхождение {rel:.2e} "
              f"-> {'ЭКВИВАЛЕНТНО (1e-12)' if close else 'РАСХОЖДЕНИЕ'}")
        print(f"  боевой контур: {c_l:.4f}% / {dd_l:.4f}%")
        print(f"  симулятор:     {c_s:.4f}% / {dd_s:.4f}%")
        same_pos = pos.shape == pos_sim.shape and bool((pos == pos_sim).all())
        nbad = 0 if same_pos else int((pos != pos_sim).any(axis=1).sum())
        print(f"  КНИГИ: {'совпали на каждой сессии' if same_pos else f'РАЗОШЛИСЬ на {nbad} сессиях'}"
              f" ({len(pos):,} сессий)")
        print(f"  сессий с заявкой: {int((np.diff(pos, axis=0) != 0).any(axis=1).sum()) + 1}")
        # СЕРИИ, УПАКОВКА, ПРИЗНАК РОЛЛА (восемнадцатый круг, №20; девятнадцатый, №20):
        # совпадение количеств не доказывает верную ПОСТАВКУ — контур, навсегда оставшийся
        # в U26 при верных числах, печатал бы «КНИГИ совпали» и ехал в поставку. Симулятор
        # серий не ведёт, поэтому серии сверяются с КАЛЕНДАРЁМ роллов И НЕЗАВИСИМЫМИ
        # границами: буква/близость поставки (произвольные H99->M99 больше не проходят),
        # начальная серия — из календаря первого входа, смена — ровно на следующую
        # поставку цикла; упаковка — с ВЕРХНЕЙ границей остатка MES (es_held=0 при сотнях
        # MES прежде считалось «ЧИСТО»).
        # КАЛЕНДАРЬ РОЛЛОВ ПЕРЕСЧИТЫВАЕТСЯ НЕЗАВИСИМО (двадцать первый круг, №19).
        # Прежде даты роллов приходили из S.roll_days_calendar И в step, И в sim164, И в
        # эту сверку — то есть три места брали ОДИН источник, и сдвиг всех роллов на
        # неверную дату сохранял бы совпадение кодов и вердикт «ЧИСТО». Ни праздничная
        # таблица, ни правило «за три сессии» этим не проверялись.
        #
        # Здесь календарь строится ЗАНОВО прямо из индекса сессий: последний торговый день
        # фев/мая/авг/ноя минус ТРИ сессии. Индекс — факт рынка, а не наша функция.
        _rolls = S.roll_days_calendar(d[0])
        _idx = d[0]
        _nezav = set()
        for _y in range(_idx[0].year, _idx[-1].year + 1):
            for _m in (2, 5, 8, 11):
                _mes = _idx[(_idx.year == _y) & (_idx.month == _m)]
                if len(_mes) == 0:
                    continue
                _nm = _idx[_idx > _mes[-1]]
                if len(_nm) == 0:
                    continue          # месяц не завершён данными — ролла нет
                _pos = _idx.get_loc(_mes[-1])
                if _pos >= 3:
                    _nezav.add(_idx[_pos - 3])
        _cal_ok = (_nezav == _rolls)
        # ЯКОРЬ ИЗ ВНЕШНЕГО ИСТОЧНИКА: 24.11.2026 — дата ноябрьского ролла по календарю
        # CME/CBOT (праздник сдвигает с 25-го), записана в §1 спецификации ДО этой
        # проверки. Совпадение двух наших вычислений её не доказывает — она проверяется
        # отдельно и жёстко.
        import pandas as _pdr
        _yakor = _pdr.Timestamp('2026-11-24')
        # ЯКОРЬ НЕ МОЖЕТ БЫТЬ «НА МЕСТЕ» ПО ПРИЧИНЕ ОТСУТСТВИЯ (двадцать четвёртый круг,
        # №28). Условие «даты нет в индексе ИЛИ она ролл» на данных, кончающихся до ноября
        # 2026, печатало «якорь на месте», ни разу не вызвав проверку: выпуск заявлял
        # доказанной поставочную дату, которой не касался. Якорь применим ТОЛЬКО когда дата
        # покрыта данными; иначе он объявляется НЕПРИМЕНИМЫМ вслух, а не пройденным.
        _yakor_scope = _yakor in set(_idx)
        _yakor_ok = (_yakor in _rolls) if _yakor_scope else None
        # ПРЯМАЯ ПРОВЕРКА ЯКОРЯ ЖИВОЙ ФУНКЦИЕЙ, НЕ ЗАВИСЯЩАЯ ОТ ДАННЫХ (№28). Историческая
        # выборка кончается августом 2026, поэтому ноябрьский якорь по ней недостижим в
        # принципе. Дата ролла — свойство КАЛЕНДАРЯ, а не наших рядов: спрашиваем живой
        # is_roll_day с живой таблицей праздников напрямую. Это и есть внешняя сверка,
        # ради которой якорь заводился.
        import daily as _DLy
        try:
            _yakor_live = bool(_DLy.is_roll_day(_yakor, _DLy.holidays_for(2026)))
        except Exception as _exy:
            _yakor_live = False
            print(f'  якорь 24.11.2026: живая функция недоступна ({_exy})')
        print(f'  якорь 24.11.2026 живой функцией: '
              f'{"ПОДТВЕРЖДЁН" if _yakor_live else "НЕ ПОДТВЕРЖДЁН"}')
        print(f'  календарь роллов: независимый пересчёт '
              f'{"совпал" if _cal_ok else "РАЗОШЁЛСЯ"} ({len(_rolls)} дат); '
              f'якорь 24.11.2026 '
              f'{"НЕПРИМЕНИМ (дата вне данных окна)" if _yakor_ok is None else ("на месте" if _yakor_ok else "НЕ СОВПАЛ")}')
        # ЖИВОЙ ПУТЬ ПРОВЕРЯЕТСЯ ОТДЕЛЬНО (двадцать третий круг, №26). Прежде сверялись две
        # НАШИ формулы по одному и тому же историческому индексу: живая праздничная таблица
        # HOLIDAYS_CME, daily.is_roll_day() и путь feed -> Market.roll_today в доказательство
        # не входили вовсе, и общий сдвиг роллов в покрытом году прошёл бы незамеченным.
        # Здесь ЖИВАЯ функция гоняется по СВОЕЙ таблице и сверяется с пересчётом из индекса
        # сессий на пересечении лет. Область пересечения объявляется вслух — молчаливой
        # «полной независимости» тут нет и быть не может.
        import daily as _DLr
        _live_bad = []
        _yrs = sorted({_y for _y in range(_idx[0].year, _idx[-1].year + 1)
                       if _y in getattr(_DLr, 'HOLIDAYS_CME', {})})
        for _y in _yrs:
            try:
                _hol = _DLr.holidays_for(_y)
            except Exception as _ex:
                _live_bad.append(f'{_y}: календарь недоступен ({_ex})')
                continue
            _ours = {d for d in _nezav if d.year == _y}
            _theirs = {d for d in _idx[_idx.year == _y] if _DLr.is_roll_day(d, _hol)}
            if _ours != _theirs:
                _live_bad.append(f'{_y}: живая {sorted(map(str, _theirs))[:4]} против '
                                 f'пересчёта {sorted(map(str, _ours))[:4]}')
        print(f'  живой путь роллов: годы пересечения {_yrs or "нет"} — '
              f'{"совпал" if not _live_bad else "РАЗОШЁЛСЯ: " + "; ".join(_live_bad)}')
        _cal_bad = 0 if (_cal_ok and (_yakor_ok is not False) and _yakor_live
                         and not _live_bad) else 1
        _ser_bad = _pack_bad = _pend_bad = 0
        _prev = None
        _first_seen = False
        for _i, (_sa, _sb, _es, _rp) in enumerate(extras):
            _dte = d[0][_i]
            if _sa != _sb:
                _ser_bad += 1
            if _sa is not None:
                # НЕЗАВИСИМЫЕ ГРАНИЦЫ ПОСТАВКИ (№20): буква квартального цикла; месяц
                # поставки СТРОГО впереди; не дальше четырёх месяцев (максимум сразу после
                # ролла февраля: держим июнь). Произвольный тег H99 ловится здесь.
                if _sa[0] not in 'HMUZ' or len(_sa) != 3:
                    _ser_bad += 1
                else:
                    # ВЕК — ОТ ДАТЫ СЕССИИ: tag_month прибавляет 2000 (живой контур), а
                    # реплей идёт с 1963 года; двухзначный год трактуется ближайшим
                    # НЕ ПРОШЕДШИМ (поставка всегда впереди даты — §1).
                    _m = {v: k for k, v in DL.DELIVERY.items()}[_sa[0]]
                    _y = (_dte.year // 100) * 100 + int(_sa[1:])
                    if _y < _dte.year:
                        _y += 100
                    _ahead = (_y * 12 + _m) - (_dte.year * 12 + _dte.month)
                    if not (1 <= _ahead <= 4):
                        _ser_bad += 1
                if not _first_seen:
                    # НАЧАЛЬНАЯ СЕРИЯ — ИЗ КАЛЕНДАРЯ ПЕРВОГО ВХОДА (№20).
                    _first_seen = True
                    _want0 = DL.first_tag(_dte)
                    if _dte in _rolls or DL.roll_passed_for(_dte):
                        _want0 = DL.next_tag(_want0)
                    if _sa != _want0:
                        _ser_bad += 1
            if _prev is not None:
                _was_roll = _dte in _rolls
                if _was_roll and _sa == _prev and pos[_i][0]:
                    _ser_bad += 1            # день ролла не сменил серию
                if not _was_roll and _sa != _prev and pos[_i - 1][0]:
                    _ser_bad += 1            # серия сменилась вне дня ролла
                if _sa != _prev and _sa is not None and _prev is not None:
                    # СМЕНА — РОВНО НА СЛЕДУЮЩУЮ ПОСТАВКУ ЦИКЛА (№20); двойной шаг
                    # допустим только при самоисправлении пропуска (в реплее не бывает).
                    if _sa not in (DL.next_tag(_prev), DL.next_tag(DL.next_tag(_prev))):
                        _ser_bad += 1
            _prev = _sa
            if _es is not None:
                _mes_rest = pos[_i][0] - 10 * _es
                # ВЕРХНЯЯ ГРАНИЦА ОСТАТКА (№20): вне ролла минимальная упаковка держит
                # 0..19 MES; «вся сетка в MES» (es=0 при сотнях единиц) — нарушение.
                if _es < 0 or _mes_rest < 0 or _mes_rest > 19:
                    _pack_bad += 1
            if _rp:
                _pend_bad += 1
        print(f"  серии/упаковка/признак: {'ЧИСТО' if not (_ser_bad or _pack_bad or _pend_bad) else f'серии {_ser_bad}, упаковка {_pack_bad}, pending {_pend_bad}'}")
        ok &= bool(same_idx and close and same_pos
                   and not (_ser_bad or _pack_bad or _pend_bad or _cal_bad))

    # --- маршрут Е: собственный контур против sim_etf ---
    from sim_etf import sim_etf as _se
    de = S.build_modern()
    nav_e, pos_e = replay_e(de)
    _sim_pos = []
    r_s, nav_s, _st = _se(*de, record_pos=_sim_pos)
    rel_e = np.max(np.abs(nav_e.values / nav_s.values - 1))
    print(f"\n=== маршрут Е, современное окно ===")
    print(f"  NAV: расхождение {rel_e:.2e} -> "
          f"{'ЭКВИВАЛЕНТНО (1e-12)' if rel_e < 1e-12 else 'РАСХОЖДЕНИЕ'}")
    print(f"  контур Е: {S.met(nav_e.pct_change().fillna(0))[0]:.4f}%, "
          f"sim_etf: {S.met(nav_s.pct_change().fillna(0))[0]:.4f}%")
    _pe = np.array(_sim_pos, dtype=np.int64)
    same_pos_e = pos_e.shape == _pe.shape and bool((pos_e == _pe).all())
    print(f"  КНИГИ Е: {'совпали на каждой сессии' if same_pos_e else 'РАЗОШЛИСЬ'}"
          f" ({len(_pe):,} сессий)")
    ok &= bool(rel_e < 1e-12 and same_pos_e)

    # --- отказы §8 проверяются отдельно: в истории капитал 10 млн, они не срабатывают ---
    print("\n=== отказы §8 (проверяются на подставленных величинах) ===")
    d = S.build_modern()
    m = DL.Market(date=d[0][-1], px_eq_prev=d[4][-2], dref_prev=d[5][-2], dref_today=d[5][-1],
                  px_eq_today=d[4][-1], roll_today=False, st_eq=True, st_bd=True)
    for capital, lbl in ((10_000_000.0, '10 млн'), (3_000_000.0, '3 млн (порог)'),
                         (2_999_999.0, 'на доллар ниже порога'), (400_000.0, '400 тыс.')):
        dec = DL.step(DL.Book(d_fix=d[5][-2]), m, capital)
        print(f"  {lbl:<22} {'ОТКАЗ: ' + '; '.join(dec.refusals) if dec.refusals else 'заявка допускается'}")

    print('\nИТОГ:', 'ЭКВИВАЛЕНТНОСТЬ ДОКАЗАНА' if ok else 'ЕСТЬ РАСХОЖДЕНИЯ')
    sys.exit(0 if ok else 1)
