#!/usr/bin/env python3
"""Одна торговая сессия на живом счёте: вход, решение, заявки, журнал — и её замыкание.

СОБИРАЕТ ПРОВЕРЕННЫЕ ЧАСТИ И НИЧЕГО НЕ РЕШАЕТ САМ. Решение считает daily.step, заявки
строятся как разность двух книг, восстановление после сбоя идёт по ФАКТИЧЕСКОЙ позиции у
брокера. Здесь только подключение, сбор входов (feed) и адаптер (ib_broker).

ТРИ РЕЖИМА, И ЭТО НЕ УДОБСТВО, А ТРЕБОВАНИЕ §1.
  --dry    считать и сверить, заявок не подавать (наблюдение П-2);
  --live   подать заявки;
  --close  ЗАМКНУТЬ сессию после закрытия биржи.

Замыкание отдельным запуском потому, что плечо закрытия — единственный источник триггера
капа §1, а посчитать его можно только по ФАКТИЧЕСКИМ ценам закрытия и ФАКТИЧЕСКОМУ NLV.
В момент подачи заявки этих величин не существует: подставлять туда утренний NLV и
вчерашние цены значит систематически считать триггер капа от неверных величин и не
выполнить обязательное сокращение. До замыкания следующая сессия НЕ ТОРГУЕТ.

КАПИТАЛ БЕРЁТСЯ У БРОКЕРА. §1 считает цель от NAV, и подставлять сюда задуманную сумму
нельзя: расхождение с фактическим счётом — это и есть то, что бумажный этап проверяет.
"""
import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
import tz                       # noqa: F401  — устаревшие зоны IBKR, до любых дат
import daily as DL
import feed as FD
import ib_broker as IBB

class Refused(RuntimeError):
    """Отказ по существу: условие §1/§8 не выполнено, сессия не считается.

    ОТКАЗ — НЕ ЗАВЕРШЕНИЕ ПРОЦЕССА. Прежде do_trade и do_close поднимали SystemExit, и любой
    вызывающий — планировщик, надзорный процесс, стенд проверок — молча УМИРАЛ вместо того,
    чтобы отказ обработать. Обнаружено проверкой последовательности из трёх сессий: стенд
    ловил Exception, SystemExit им не является, и прогон обрывался без единого сообщения.
    Код возврата ставит только main().
    """


HOST = os.environ.get('IB_HOST', '127.0.0.1')
PORT = int(os.environ.get('IB_PORT', '4002'))


def state_dir():
    """Каталог состояния читается ПРИ ВЫЗОВЕ, а не при импорте. Иначе переменная окружения,
    выставленная после импорта модуля, молча игнорируется — и запуск идёт по чужой книге."""
    return Path(os.environ.get('ADDFUT_DIR', os.path.expanduser('~/.addfut')))


def _connect(client_id):
    from ib_insync import IB
    ib = IB()
    ib.connect(HOST, PORT, clientId=client_id, timeout=30)
    ib.reqMarketDataType(3)                  # задержанные: сделка идёт по вчерашнему закрытию
    return ib


def paper_mode():
    """Бумажный этап определяется ПОРТОМ шлюза (4002 — paper), а не константой в коде:
    прежний безусловный paper=True обошёл бы порог §8 и на живом счёте (№5)."""
    if PORT == 4002:
        return True
    if os.environ.get('ADDFUT_LIVE_OK') == '1':
        return False
    raise Refused(f'порт {PORT} не бумажный, а разрешения ADDFUT_LIVE_OK нет — '
                  f'торговля на живом контуре без явного решения запрещена')


def _book_path(route):
    import state as ST
    return ST.book_path(route)


def do_close(ib, route):
    """Замкнуть сессию по факту закрытия: фактические цены закрытия и фактический NLV.

    ВСЁ ПОД ОДНОЙ БЛОКИРОВКОЙ, И КНИГА ПЕРЕЧИТЫВАЕТСЯ ПЕРЕД ЗАПИСЬЮ. Прежняя редакция
    читала книгу, ходила к брокеру за NLV и ценами и только потом брала замок: между
    чтением и записью переходный исполнитель успевал перевести маршрут и сохранить новую
    книгу, а замыкание затирало её старой — классическая потерянная запись при фактической
    позиции уже на другом маршруте.

    ЗАМЫКАЕТСЯ ИМЕННО ТА СЕССИЯ, КОТОРАЯ ТОРГОВАЛА. Проверяется, что замыкание ещё не
    выполнялось и что дата книги совпадает с сегодняшней биржевой: иначе пропущенное в
    понедельник замыкание, запущенное во вторник, посчитало бы триггер капа по вторничным
    ценам для понедельничной книги, а повторный запуск переписал бы его ещё раз.
    """
    import state as ST
    br = IBB.IBBroker(ib)
    bp = _book_path(route)
    cls = DL.BookE if route == 'E' else DL.Book
    with ST.hold_book_lock():
        book, sess, saved = ST.load(bp, cls)
        if book is None:
            raise Refused('нет сохранённой книги — замыкать нечего')
        if saved != route:
            raise Refused(f'книга записана для маршрута {saved}, запрошен {route}')
        if not getattr(book, 'close_provisional', False):
            raise Refused(f'сессия №{sess} уже замкнута (плечо закрытия '
                             f'{book.prev_close_lev:.4f}); повторное замыкание переписало бы '
                             f'триггер капа §1 по ценам другой сессии')
        today = FD.exchange_today().strftime('%Y-%m-%d')
        if book.last_session and book.last_session != today:
            raise Refused(
                f'книга относится к сессии {book.last_session}, а сегодня {today}: замкнуть '
                f'её сегодняшними ценами значит посчитать триггер капа §1 по чужому '
                f'закрытию. Требуется ручной разбор (О-5)')
        # СВЕРКА С БРОКЕРОМ ПЕРЕД ФИКСАЦИЕЙ ПЛЕЧА (№9): поздний фил, чужая позиция или
        # живая заявка не должны попадать в триггер капа как «чистое закрытие».
        diff = ST.reconcile(book, route, br.net_positions(), open_orders=br.open_orders())
        if diff:
            raise Refused('замыкание отменено — книга расходится с брокером:\n  '
                          + '\n  '.join(diff))
        px_eq, dref, px_bd, src = FD.closing_values(ib, route, book)
        # NLV — ПОСЛЕ цен закрытия (тринадцатый круг, №3): триггер капа обязан делить цены
        # и капитал ОДНОГО момента; ранний NAV с поздними ценами искажал prev_close_lev.
        nav = br.net_liquidation()
        # ПОВТОРНАЯ СВЕРКА ПОСЛЕ ДОЛГИХ ЗАПРОСОВ (десятый круг, №11): поздний фил или новая
        # заявка в окне запросов цен не должны попадать в триггер капа незамеченными.
        diff2 = ST.reconcile(book, route, br.net_positions(), open_orders=br.open_orders())
        if diff2:
            raise Refused('замыкание отменено — брокер изменился за время запросов:\n  '
                          + '\n  '.join(diff2))
        book2, sess2, saved2 = ST.load(bp, cls)
        if (vars(book2) != vars(book)) or sess2 != sess or saved2 != saved:
            raise Refused('книга изменилась, пока снимались цены закрытия — замыкание '
                             'отменено во избежание потерянной записи')
        closed = DL.finalize_close(book, px_eq, dref, nav, route=route, px_bd_close=px_bd)
        ST.save(bp, closed, route, sess, note='замыкание сессии по факту закрытия')
    print(f'замкнуто: NLV закрытия {nav:,.2f}, вход {src}')
    print(f'плечо закрытия {closed.prev_close_lev:.4f} — триггер капа следующей сессии')
    return closed


def do_trade(ib, route, dry):
    import state as ST

    br = IBB.IBBroker(ib)
    nlv = br.net_liquidation()
    pos = br.net_positions()
    alien = [k for k in pos if k.startswith('НЕИЗВЕСТНЫЙ')]
    if alien:
        raise Refused(f'на счёте посторонние позиции {alien} — сессия не считается')

    bp = _book_path(route)
    cls = DL.BookE if route == 'E' else DL.Book
    book, sess, _ = ST.load(bp, cls)
    book = book or cls()

    # НЕЗАМКНУТАЯ ПРЕДЫДУЩАЯ СЕССИЯ — ОТКАЗ, А НЕ ПРЕДУПРЕЖДЕНИЕ. Плечо закрытия в такой
    # книге посчитано по ценам открытия и утреннему NLV, то есть триггер капа §1 заведомо
    # неверен. Торговать по нему хуже, чем пропустить сессию.
    # БЕЗ условия sess>0: книга, принятая от перехода, приходит с провизорным замыканием и
    # обязана его пройти; прежняя проверка «if sess and ...» на ней отключалась.
    if getattr(book, 'close_provisional', False) and book.last_session and not dry:
        raise Refused(f'сессия №{sess} не замкнута: плечо закрытия предварительное, '
                         f'триггер капа §1 недостоверен. Выполнить: session.py --close')

    # ДАТА СЕССИИ — БИРЖЕВАЯ. По часам машины запуск около полуночи UTC пометил бы
    # сделку соседней датой, и last_session либо заблокировал бы правильный запуск,
    # либо допустил второй под другой локальной датой.
    m, src = FD.build_market(ib, FD.exchange_today(), book, route=route)
    print(f'счёт {br.account}: NAV {nlv:,.2f}; позиции {pos or "нет"}; сессия №{sess + 1}')
    print('вход: ' + ', '.join(f'{k}={v}' for k, v in src.items()))

    # ПОРОГ §8 НЕ ОТКЛЮЧАЕТСЯ, А ЯВНО ИСКЛЮЧАЕТСЯ НА БУМАЖНОМ ЭТАПЕ, и это пишется в
    # журнал: молчаливый обход политики был бы ровно тем, ради чего этап и заведён.
    # ЦЕНЫ-ОРИЕНТИРЫ СНИМАЮТСЯ ДО ЗАЯВОК: без них журнал §7 не накапливает наблюдений.
    refs = FD.reference_prices(ib, route=route)
    _miss = {k: v for k, v in refs.items() if k.startswith('ОРИЕНТИР-НЕТ:')}
    refs = {k: v for k, v in refs.items() if not k.startswith('ОРИЕНТИР-НЕТ:')}
    print('ориентиры:', {k: round(v, 4) for k, v in refs.items()})
    for _k, _v in _miss.items():
        print(f'  {_k}: {_v}')
    kw = {}
    if route == 'E':
        # ЖИВОЙ ЗАПАС О-3-Е — ОТ БРОКЕРА (десятый круг, №2): расчётная прокси при капе 2,00
        # не опускается ниже порога, и аварийное сокращение было недостижимо в бою.
        _lc = br.margin_cushion()
        if _lc is None and any(v for v in (pos or {}).values()):
            # НУЛЕВОЕ/ОТСУТСТВУЮЩЕЕ ТРЕБОВАНИЕ ПРИ СУЩЕСТВУЮЩИХ ПОЗИЦИЯХ (семнадцатый
            # круг, №7) — неполный ответ брокера, а не «позиций нет»: прокси 25% дала бы
            # запас 2,0 и оставила бы полную позицию вместо аварийного сокращения.
            raise Refused('живой запас О-3-Е недоступен при существующих позициях — '
                          'торговля Е запрещена до восстановления данных брокера')
        kw['live_cushion'] = _lc
    # NLV НЕ ПЕРЕДАЁТСЯ снаружи (тринадцатый круг, №2): между ранним чтением и заявками —
    # серия долгих запросов; run_session сам читает NLV ПОД ЗАМКОМ прямо перед решением.
    dec, orders, diff = DL.run_session(
        br, m, dirpath=str(state_dir()), route=route, capital=None, closing_nav=None,
        journal_path=str(state_dir() / f'journal-{route}.csv'), dry_run=dry,
        paper=paper_mode(),
        ref_prices=refs, book_path=str(bp), series_a=src.get('series'), **kw)
    if route == 'E' and not dry and dec.trade and orders:
        # ЗАПАС ПОСЛЕ ИСПОЛНЕНИЙ (восемнадцатый круг, №1): предторговый замер относится к
        # СТАРОЙ книге; удвоение позиции могло увести фактический запас под порог, а
        # состояние сохранялось успешным. Пост-фактум сокращать нельзя (повторная торговля
        # в тот же запуск запрещена) — ставится ТРЕВОГА ФАЙЛОМ: ворота автопилота её видят
        # независимо от кода возврата, benign-ветка «день отторгован» её не подавляет.
        _pc = br.margin_cushion()
        if _pc is not None and _pc < DL.O3E_MIN:
            _day = f'{m.date:%Y-%m-%d}'
            _af = state_dir() / f'ALARM-o3e-{_day}.txt'
            _af.write_text(f'запас О-3-Е ПОСЛЕ исполнений {_pc:.2f}x ниже {DL.O3E_MIN} — '
                           f'книга сохранена, сокращение до L=1 обязано пройти следующей '
                           f'сессией; ручной разбор (О-5)\n', encoding='utf-8')
            print(f'ТРЕВОГА: запас после исполнений {_pc:.2f}x < {DL.O3E_MIN} — '
                  f'файл {_af.name}')

    print(f'\nрешение: плечо {dec.leverage:.4f}, книга '
          f'{getattr(dec.book_after, "n_e", "-")}/{getattr(dec.book_after, "n_b", "-")}')
    for r in dec.reasons:
        print('  причина:', r)
    for r in dec.refusals:
        print('  ОТКАЗ:', r)
    print('заявки:', orders or 'нет')
    if diff:
        print('РАСХОЖДЕНИЯ:', diff)
    print('режим:', 'наблюдение, заявки НЕ подавались' if dry else 'ЖИВОЙ')
    # ОТКАЗ ДНЯ — ОТКАЗ, А НЕ УСПЕХ (десятый круг, №5): прежде сессия с отказом решения
    # завершалась кодом 0, автопилот ставил «отторговано», и требуемый вход просто
    # отсутствовал при журнале «ок».
    if dec.refusals and not dry:
        raise Refused('решение дня отвергнуто: ' + '; '.join(dec.refusals))
    if not dry:
        print('ПОСЛЕ ЗАКРЫТИЯ БИРЖИ выполнить: session.py --close')
    return dec


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry', action='store_true', help='считать и сверить, заявок не подавать')
    g.add_argument('--live', action='store_true', help='подать заявки на счёт')
    g.add_argument('--close', action='store_true', help='замкнуть сессию после закрытия биржи')
    ap.add_argument('--route', default='F', choices=('F', 'E'))
    ap.add_argument('--client-id', type=int, default=33)
    a = ap.parse_args()

    state_dir().mkdir(parents=True, exist_ok=True)
    ib = _connect(a.client_id)
    try:
        if a.close:
            do_close(ib, a.route)
        else:
            do_trade(ib, a.route, dry=a.dry)
    except Refused as ex:
        print(f'ОТКАЗ: {ex}')
        return 2                      # код возврата ставит ТОЛЬКО точка входа
    finally:
        ib.disconnect()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
