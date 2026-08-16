#!/usr/bin/env python3
"""Мутационная проверка: измеряет СИЛУ инвариантов вместо спора о ней.

ЗАЧЕМ. Пять кругов внешней рецензии закончились обвинением, что перебор инвариантов лжёт:
одни утверждения вакуумны, другие круговые. Спорить об этом бесполезно — вакуумность
измеряется. Здесь код НАРОЧНО ЛОМАЕТСЯ известным способом, и проверяется, поймает ли это
хоть один инвариант. Мутация, которую не поймал никто, — дыра в сетке, и она печатается
именно так. Инвариант, которого не убивает ни одна мутация, — кандидат в тождества.

Это не заменяет инварианты, а измеряет их: сетка, ловящая все мутации, ещё может быть
неполной, но сетка, пропускающая мутацию, неполна ТОЧНО.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'live'))
import daily as DL
import invariants as I


def _mut_orders_doubled():
    o = DL.orders_from_books
    return (lambda: setattr(DL, 'book_to_orders',
                            lambda dec, bb: [(i, q * 2) for i, q in o(bb, dec.book_after)]),
            lambda bt=DL.book_to_orders: setattr(DL, 'book_to_orders', bt))


def _mut_zero_order():
    o = DL.orders_from_books
    return (lambda: setattr(DL, 'book_to_orders',
                            lambda dec, bb: o(bb, dec.book_after) + [('ZNZ26', 0)]),
            lambda bt=DL.book_to_orders: setattr(DL, 'book_to_orders', bt))


def _mut_no_cap():
    s = DL.step
    return (lambda: setattr(DL, 'step', lambda b, m, c, **kw: s(b, m, c, **{**kw, 'cap': None})),
            lambda: setattr(DL, 'step', s))


def _mut_no_guards():
    g = DL.guards
    return (lambda: setattr(DL, 'guards', lambda *a, **k: []),
            lambda: setattr(DL, 'guards', g))


def _mut_no_price_check():
    v = DL.validate_inputs
    return (lambda: setattr(DL, 'validate_inputs', lambda *a, **k: []),
            lambda: setattr(DL, 'validate_inputs', v))


def _mut_series_frozen():
    t = DL.target_tag
    return (lambda: setattr(DL, 'target_tag', lambda held, dte, roll, passed=False:
                            held if held is not None else DL.first_tag(dte)),
            lambda: setattr(DL, 'target_tag', t))


def _mut_canonical_packing():
    p = DL.pack_es
    return (lambda: setattr(DL, 'pack_es', lambda es, n, mes, roll: (n // 10) if mes else None),
            lambda: setattr(DL, 'pack_es', p))


def _mut_refusal_grows():
    """Отказ §8 не сокращает, а НАРАЩИВАЕТ позицию — проверяет утверждение об экспозиции."""
    st = DL.step
    def broken(b, m, c, **kw):
        d = st(b, m, c, **kw)
        if d.refusals and d.book_after is not None:
            from dataclasses import replace as _r
            d.book_after = _r(d.book_after, n_e=abs(b.n_e) + 10, n_b=abs(b.n_b) + 5)
        return d
    return (lambda: setattr(DL, 'step', broken), lambda: setattr(DL, 'step', st))


def _mut_roll_keeps_old():
    """При смене серии старая закрывается НЕ полностью — остаётся хвост."""
    o = DL.orders_from_books
    def broken(before, after):
        out = o(before, after)
        return [(i, q + 1 if q < 0 else q) for i, q in out if (q + 1 if q < 0 else q)]
    return (lambda: setattr(DL, 'orders_from_books', broken),
            lambda: setattr(DL, 'orders_from_books', o))


def _mut_negative_mes():
    """Упаковка даёт больше целых ES, чем есть единиц сетки — скрытый short по MES."""
    p = DL.pack_es
    return (lambda: setattr(DL, 'pack_es', lambda es, n, mes, roll: (n // 10 + 1) if mes and n else p(es, n, mes, roll)),
            lambda: setattr(DL, 'pack_es', p))


def _wrap_after(fn_name, fix):
    """Общая форма мутаций класса «сделано, но не то или не столько»: решение считается
    штатно, а затем книга-результат ПОРТИТСЯ. Такие поломки не ломают ни одну двоичную
    проверку — книга остаётся согласованной, заявки по-прежнему её достигают, кап и полоса
    формально соблюдены, — и до появления инвариантов достаточности не ловились ничем."""
    orig = getattr(DL, fn_name)
    def patched(b, m, c, **kw):
        d = orig(b, m, c, **kw)
        if not d.refusals:
            fix(d, b, m, c)
        return d
    return (lambda: setattr(DL, fn_name, patched), lambda: setattr(DL, fn_name, orig))


def _mut_no_restore():
    """Восстановление после сбоя удалено целиком: книга у брокера остаётся какой вышла,
    и расхождение НЕ НАЗЫВАЕТСЯ. Прежнее сессионное утверждение это пропускало — оно
    смотрело только на сохранённый файл, а он при исключении и так остаётся прежним."""
    orig = DL.restore_to
    return (lambda: setattr(DL, 'restore_to',
                            lambda broker, target, route='F': (True, {}, [])),
            lambda: setattr(DL, 'restore_to', orig))


def _mut_leg_halved():
    """Выключенная сигналом нога сокращается вдвое вместо закрытия."""
    def f(d, b, m, c):
        import dataclasses as dc
        ch = {}
        # Половина берётся от ИСХОДНОЙ книги: после штатного шага выключенная нога уже
        # нулевая, и «половина нуля» была бы пустой мутацией — ровно та ловушка, которую
        # ловит правило «мутация, никого не убившая, подозрительна сама».
        if not m.st_eq and b.n_e:
            ch['n_e'] = b.n_e // 2
            ch['es_held'] = (b.n_e // 2) // 10 if b.unit_is_mes else None
        if not m.st_bd and b.n_b:
            ch['n_b'] = b.n_b // 2
        if ch:
            d.book_after = dc.replace(d.book_after, **ch)
    return _wrap_after('step', f)


def _mut_halfway_to_target():
    """Выравнивание идёт лишь на полпути к цели: внутрь полосы, но не к ней (нога Б)."""
    def f(d, b, m, c):
        import dataclasses as dc
        if d.book_after.n_b != b.n_b and m.st_bd:
            d.book_after = dc.replace(d.book_after, n_b=(d.book_after.n_b + b.n_b) // 2)
    return _wrap_after('step', f)


def _mut_halfway_leg_a():
    """То же по НОГЕ А. Прежнее утверждение смотрело только на ZN и такую поломку
    пропускало — мутация написана специально, чтобы это больше не было возможным."""
    def f(d, b, m, c):
        import dataclasses as dc
        if d.book_after.n_e != b.n_e and m.st_eq:
            ne = (d.book_after.n_e + b.n_e) // 2
            ch = dict(n_e=ne)
            if d.book_after.es_held is not None:
                ch['es_held'] = min(d.book_after.es_held, ne // 10)
            d.book_after = dc.replace(d.book_after, **ch)
    return _wrap_after('step', f)


def _mut_roll_vanishes():
    """Перенос серии исчезает ЦЕЛИКОМ. Прежняя нужда утверждения выводилась из d.roll_pairs,
    поэтому такой дефект отключал само утверждение — оно молчало ровно в том случае, ради
    которого написано."""
    def f(d, b, m, c):
        import dataclasses as dc
        if d.roll_pairs:
            d.roll_pairs = []
            d.book_after = dc.replace(d.book_after, ser_a=b.ser_a, ser_b=b.ser_b)
    return _wrap_after('step', f)


def _mut_cap_overcut():
    """Кап срезает на одну единицу глубже необходимого."""
    def f(d, b, m, c):
        import dataclasses as dc
        if d.cap_correction and d.book_after.n_b > 0:
            d.book_after = dc.replace(d.book_after, n_b=d.book_after.n_b - 1)
    return _wrap_after('step', f)


def _mut_roll_one_leg():
    """Ролл переносит только ногу А, нога Б остаётся в старой серии."""
    def f(d, b, m, c):
        import dataclasses as dc
        if d.roll_pairs and b.n_b and b.ser_b:
            d.roll_pairs = [p for p in d.roll_pairs if p['leg'] != 'Б']
            d.book_after = dc.replace(d.book_after, ser_b=b.ser_b)
    return _wrap_after('step', f)


MUTATIONS = [
    ('нога сокращается вдвое вместо закрытия', _mut_leg_halved),
    ('выравнивание идёт лишь на полпути к цели', _mut_halfway_to_target),
    ('полпути к цели по ноге А', _mut_halfway_leg_a),
    ('перенос серии исчезает целиком', _mut_roll_vanishes),
    ('кап срезает на единицу глубже нужного', _mut_cap_overcut),
    ('ролл переносит только одну ногу', _mut_roll_one_leg),
    ('отказ §8 наращивает позицию', _mut_refusal_grows),
    ('старая серия закрывается не полностью', _mut_roll_keeps_old),
    ('упаковка даёт отрицательное число MES', _mut_negative_mes),
    ('заявки удваиваются', _mut_orders_doubled),
    ('в заявки добавлена нулевая', _mut_zero_order),
    ('кап плеча отключён', _mut_no_cap),
    ('отказы §8 отключены', _mut_no_guards),
    ('проверка цен отключена', _mut_no_price_check),
    ('серия не переключается на ролле', _mut_series_frozen),
    ('упаковка всегда каноническая', _mut_canonical_packing),
]


def run_once():
    """Прогон перебора; возвращает множество имён сработавших инвариантов."""
    fired = set()
    for b, m, cap0, paper in I.states():
        try:
            d = DL.step(b, m, cap0, paper=paper)
        except Exception:
            fired.add('step падает'); continue
        try:
            o = DL.book_to_orders(d, b)
        except Exception:
            fired.add('построение заявок падает'); continue
        u_e, u_b = DL.units(b, m)
        for name, fn, needs in I.INVARIANTS:
            if needs is not None:
                try:
                    if not needs(b, m, cap0, d, o, u_e, u_b):
                        continue
                except Exception:
                    fired.add(name); continue
            try:
                if not fn(b, m, cap0, d, o, u_e, u_b):
                    fired.add(name)
            except Exception:
                fired.add(name)
    return fired


# ---------------------------------------------------------------- живой адаптер
# Мутации ЖИВОГО кода, а не расчётчика: каждая воспроизводит поведение адаптера ДО правок
# шестого круга. Если такая мутация никого не убивает — соответствующее утверждение пусто,
# а прежний дефект остался бы незамеченным ровно так же, как остался в первый раз.
def _adapter_mutations():
    import ib_broker as B

    def truncating_place():
        """Дробные доли фондов усекаются до целого (как было)."""
        orig = B.IBBroker.place
        def patched(self, instrument, qty, px_order=None):
            return orig(self, instrument, int(qty) or (1 if qty > 0 else -1), px_order)
        return orig, patched

    def orderid_matching():
        """Исполнения сопоставляются по orderId, а не по permId (как было)."""
        orig = B.IBBroker._executed
        def patched(self, tr):
            n = 0.0
            for f in self.ib.fills():
                if f.execution.orderId == tr.order.orderId:
                    n += float(f.execution.shares) * (1 if f.execution.side == 'BOT' else -1)
            return n or float(tr.orderStatus.filled or 0)
        return orig, patched

    def single_snapshot():
        """Позиции читаются одним снимком после паузы (как было)."""
        orig = B.IBBroker.refresh
        def patched(self, wait_s=None, tries=4):
            self.ib.reqPositions(); self.ib.sleep(0)
            return {p.contract.conId: float(p.position)
                    for p in self.ib.positions() if p.position}
        return orig, patched

    def ignore_account():
        """Поле счёта игнорируется: позиции всех managed accounts смешиваются (как было)."""
        orig = B.IBBroker._snapshot
        def patched(self):
            self._exec_barrier()
            pos = self.ib.reqPositions() or self.ib.positions()
            return {p.contract.conId: float(p.position) for p in (pos or []) if p.position}
        return orig, patched

    def cancel_by_instrument():
        """Принадлежность отмены — «или инструмент из реестра» (как было): чужая защитная
        заявка на нашем ES снимается."""
        orig = B.IBBroker.cancel_order
        def patched(self, oid):
            for t in self.ib.openTrades():
                if t.order.orderId == oid:
                    ref = getattr(t.order, 'orderRef', '') or ''
                    if ref != 'ADDFUT' and t.contract.conId not in self._con.values():
                        return dict(terminal=False, cancelled=False, status='чужая',
                                    filled=0.0, foreign=True)
                    self.ib.cancelOrder(t.order)
                    return dict(terminal=True, cancelled=True, status='cancelled', filled=0.0)
            return dict(terminal=True, cancelled=True, status='отсутствует', filled=0.0)
        return orig, patched

    def claim_zero_fill():
        """При статусе «исполнена» без отчётов возвращается нулевое исполнение вместо
        отказа: незнание выдаётся за факт, а книга у брокера уже изменилась."""
        orig = B.IBBroker.place
        def patched(self, instrument, qty, px_order=None):
            try:
                return orig(self, instrument, qty, px_order)
            except B.BrokerError as ex:
                if 'НЕИЗВЕСТЕН' in str(ex):
                    return dict(order_id=0, instrument=instrument, qty=qty, filled=0.0,
                                px_order=px_order, px_fill=None, commission=0.0,
                                status='Filled')
                raise
        return orig, patched

    def cancel_foreign():
        """Чужой инструмент снимается наравне со своими (как было): авария отменяет ручную
        защитную заявку."""
        orig = B.IBBroker.cancel_order
        def patched(self, oid):
            for t in self.ib.openTrades():
                if t.order.orderId == oid:
                    self.ib.cancelOrder(t.order)
                    return dict(terminal=True, cancelled=True, status='cancelled', filled=0.0)
            return dict(terminal=True, cancelled=True, status='отсутствует', filled=0.0)
        return orig, patched

    def orders_all_accounts():
        """Заявки всех счетов видимы и снимаемы (как было)."""
        orig = B.IBBroker.open_orders
        def patched(self):
            return sorted({t.order.orderId for t in self.ib.openTrades()})
        return orig, patched

    def no_exec_barrier():
        """Барьер отчётов об исполнении снят: исход заявки определяется тем, что успело
        разнестись за паузу, — окно ошибки сдвигается, но не закрывается."""
        orig = B.IBBroker._exec_barrier
        return orig, (lambda self: None)

    def no_identity_check():
        """Контракт берётся по con_id без сверки поставки и множителя (как было)."""
        orig = B.IBBroker._contract
        def patched(self, instrument):
            from ib_insync import Contract
            c = Contract(conId=self._con[instrument]); self.ib.qualifyContracts(c); return c
        return orig, patched

    def cancel_is_failure():
        """Статус Cancelled считается неисполнением (как было)."""
        orig = B.IBBroker.place
        def patched(self, instrument, qty, px_order=None):
            r = orig(self, instrument, qty, px_order)
            if isinstance(r, dict) and r.get('status') in B.TERMINAL_BAD:
                raise B.BrokerError('статус Cancelled — заявка не исполнена')
            return r
        return orig, patched

    def orders_req_swallow():
        """Ошибка запроса заявок глотается (как было до шестнадцатого круга, №2): сверка
        дубликатов идёт по заведомо неполному локальному кэшу."""
        orig = B.IBBroker.open_orders

        def patched(self):
            try:
                self.ib.reqAllOpenOrders()
                self.ib.sleep(1.0)
            except Exception:
                pass
            out = set()
            for t in self.ib.openTrades():
                acct = getattr(t.order, 'account', '') or ''
                if self.account and acct and acct != self.account:
                    continue
                out.add(t.order.orderId)
            return sorted(out)
        return orig, patched

    def bare_order_ids():
        """Ключ заявки — голый orderId (как было до восемнадцатого круга, №2): заявки
        разных clientId схлопываются, отмена бьёт по первой найденной."""
        orig = B.IBBroker.open_orders

        def patched(self):
            try:
                self.ib.reqAllOpenOrders()
                self.ib.sleep(1.0)
            except Exception as ex:
                raise B.BrokerError(f'запрос заявок: {ex}')
            out = set()
            for t in self.ib.openTrades():
                acct = getattr(t.order, 'account', '') or ''
                if self.account and acct and acct != self.account:
                    continue
                out.add(t.order.orderId)
            return sorted(out)
        return orig, patched

    def nan_cushion_ok():
        """NaN в тегах запаса принимается (как было до семнадцатого круга, №7):
        cushion=NaN, «NaN < 1,40» ложно — сокращение отключено молча."""
        orig = B.IBBroker.margin_cushion

        def patched(self):
            vals = (self.ib.accountSummary(self.account) if self.account
                    else self.ib.accountSummary())
            ewl = maint = None
            for v in vals:
                if v.tag == 'EquityWithLoanValue' and v.currency == 'USD':
                    ewl = float(v.value)
                if v.tag == 'MaintMarginReq' and v.currency == 'USD':
                    maint = float(v.value)
            if ewl is None:
                raise B.BrokerError('нет EWL')
            if not maint:
                return None
            return ewl / maint
        return orig, patched

    def restore_swallows_unknown():
        """Неизвестность компенсации глотается (как было до семнадцатого круга, №4):
        except-pass вокруг place, совпавший снимок объявляется восстановлением."""
        import daily as DLm
        import state as STm
        orig = DLm.restore_to

        def patched(broker, target_book, route='F'):
            stuck = DLm._cancel_all(broker)
            if stuck:
                have0 = {k: v for k, v in (broker.net_positions() or {}).items() if v}
                return False, have0, stuck
            want = STm.expected_positions(target_book, route)
            have = {k: v for k, v in (broker.net_positions() or {}).items() if v}
            for inst in sorted(set(want) | set(have)):
                d = want.get(inst, 0) - have.get(inst, 0)
                if d:
                    try:
                        broker.place(inst, d)
                    except Exception:
                        pass
            stuck += DLm._cancel_all(broker)
            have2 = {k: float(v) for k, v in (broker.net_positions() or {}).items() if v}
            want2 = {k: float(v) for k, v in want.items()}
            return (have2 == want2 and not stuck), have2, stuck
        return orig, patched, DLm

    def isin_empty_ok():
        """Пустой ISIN реестра пропускается (как было до пятнадцатого круга, №6): строка STK
        без ISIN означала «проверка не нужна», а не повреждённый реестр."""
        import contracts as CT
        orig = CT.verify_isin

        def patched(ib, c, row):
            if not (row.get('isin') or '').strip():
                return []
            return orig(ib, c, row)
        return orig, patched, CT

    def summary_from_cache():
        """Сводка счёта читается из кэша подписки (как было до девятнадцатого круга, №4):
        request/end-барьер снят, доторговый NLV выдаётся за свежий."""
        orig = B.IBBroker._summary_barrier
        return orig, (lambda self: None)

    def rec_from_status():
        """Цена и комиссия — из статуса и без execId-фильтра (как было до девятнадцатого
        круга, №16): нулевая статусная цена давала пустой px_fill, непришедшая комиссия
        писалась нулём."""
        orig = B.IBBroker._rec

        def patched(self, tr, instrument, qty, px_order):
            filled = self._executed(tr)
            px_fill = float(tr.orderStatus.avgFillPrice or 0) or None
            perm = getattr(tr.order, 'permId', 0)
            comm = sum(float(f.commissionReport.commission or 0) for f in self.ib.fills()
                       if getattr(f.execution, 'permId', 0) == perm
                       and f.contract.conId == tr.contract.conId)
            rec = dict(order_id=tr.order.orderId, instrument=instrument, qty=qty,
                       filled=filled, px_order=px_order, px_fill=px_fill, commission=comm,
                       status=tr.orderStatus.status)
            if abs(filled - qty) > 1e-9:
                rec['incident'] = 'недобор'
            return rec
        return orig, patched

    def etf_line_isin_only():
        """Линия ETF заверяется одним ISIN (как было до девятнадцатого круга, №6):
        площадка и тикер копируются из ответа без сверки с ожиданиями."""
        import first_connect as FC
        orig = FC.check_etf_line

        def patched(contract, sym, ticker, prim, cur, isin_exch, want_isin):
            if isin_exch and isin_exch == want_isin:
                return []
            return [f'{sym}: ISIN не совпал']
        return orig, patched, FC

    def future_identity_copied():
        """Поставка копируется из ответа биржи (как было до восемнадцатого круга, №7)."""
        import first_connect as FC
        orig = FC.check_future_identity
        return orig, (lambda contract, root, tag: []), FC

    return [('дробная доля усекается до целого', 'place', truncating_place),
            ('сводка счёта из кэша подписки', '_summary_barrier', summary_from_cache),
            ('цена и комиссия из статуса', '_rec', rec_from_status),
            ('линия ETF по одному ISIN', 'check_etf_line', etf_line_isin_only),
            ('поставка копируется из ответа', 'check_future_identity',
             future_identity_copied),
            ('исполнения по orderId, не по permId', '_executed', orderid_matching),
            ('позиции одним снимком', 'refresh', single_snapshot),
            ('счёт в позициях игнорируется', '_snapshot', ignore_account),
            ('чужой инструмент снимается', 'cancel_order', cancel_foreign),
            ('счёт в заявках игнорируется', 'open_orders', orders_all_accounts),
            ('нулевое исполнение вместо отказа', 'place', claim_zero_fill),
            ('отмена по инструменту, а не по метке', 'cancel_order', cancel_by_instrument),
            ('барьер отчётов об исполнении снят', '_exec_barrier', no_exec_barrier),
            ('контракт без сверки личности', '_contract', no_identity_check),
            ('пустой ISIN реестра пропускается', 'verify_isin', isin_empty_ok),
            ('ошибка запроса заявок глотается', 'open_orders', orders_req_swallow),
            ('неизвестность компенсации глотается', 'restore_to', restore_swallows_unknown),
            ('ключ заявки — голый orderId', 'open_orders', bare_order_ids),
            ('NaN-запас О-3-Е принимается', 'margin_cushion', nan_cushion_ok),
            ('Cancelled считается неисполнением', 'place', cancel_is_failure)]


def _intent_mutations():
    """Мутации разбора НАМЕРЕНИЯ. Каждая воспроизводит поведение до правки: без намерения
    все три исхода обрыва между сделкой и записью состояния выглядели одинаково."""
    import daily as DL

    def no_intent():
        """Намерение не разбирается вовсе (как было)."""
        orig = DL._resume_intent
        # ПАРА, А НЕ КНИГА (двадцать третий круг, №25): одиночный Book ловился ошибкой
        # распаковки у ВЫЗЫВАЮЩЕГО, то есть мутация доказывала несовместимость API, а не
        # работу защиты намерения.
        return orig, (lambda ST, bp, cls, route, book, sess, broker, dry: (book, None))

    def adopt_always():
        """Намеченная книга принимается ВСЕГДА, даже при промежуточной у брокера."""
        orig = DL._resume_intent
        def patched(ST, bp, cls, route, book, sess, broker, dry):
            it = ST.load_intent(bp)
            if not it:
                return book, None
            after = cls(**it['book_after'])
            ST.save(bp, after, route, sess + 1, note='принято без сверки')
            ST.clear_intent(bp)
            # ПАРА, КАК У БОЕВОЙ ФУНКЦИИ (двадцать четвёртый круг, №25): одиночный Book
            # ронял вызывающего ошибкой распаковки, и «мутация поймана» доказывало лишь
            # несовместимость API — ровно тот класс, который объявлен исправленным. Дата
            # сессии берётся из намерения, как в боевом коде.
            return after, (it.get('session_date') or None)
        return orig, patched

    def not_finishing():
        """Принятое намерение НЕ завершает сессию: тот же запуск считает решение заново по
        тому же рынку, и в день ролла уже перенесённая серия уезжает на квартал вперёд."""
        orig = DL._resume_intent
        return orig, (lambda *a: (orig(*a)[0], None))

    def drop_intent_always():
        """Намерение снимается всегда, книга не принимается никогда."""
        orig = DL._resume_intent
        def patched(ST, bp, cls, route, book, sess, broker, dry):
            ST.clear_intent(bp)
            return book, None
        return orig, patched

    def snapshot_proves():
        """Снимок позиций объявляется доказательством «заявок не было», дописанное
        состояние не распознаётся (как было до семнадцатого круга, №3): барьер исполнений
        не спрашивается, обрыв между save() и clear_intent() пишет книгу второй раз."""
        orig = DL._resume_intent

        def patched(ST, bp, cls, route, book, sess, broker, dry_run):
            import dataclasses as dc
            it = ST.load_intent(bp)
            if not it:
                return book, None
            if it.get('route') != route:
                raise RuntimeError('намерение чужого маршрута — ручной разбор')
            before = cls(**it['book_before'])
            after = cls(**it['book_after'])
            pos = broker.net_positions()
            live = getattr(broker, 'open_orders', lambda: [])()
            if live:
                raise RuntimeError(f'живые заявки {live} — О-5')
            if not ST.reconcile(before, route, pos):
                if not dry_run:
                    ST.clear_intent(bp)
                return book, None
            if not ST.reconcile(after, route, pos):
                d = it.get('session_date') or ''
                after = dc.replace(after, last_session=d or after.last_session,
                                   close_provisional=True)
                if dry_run:
                    return after, d
                ST.save(bp, after, route, sess + 1, note='принято по снимку (старый разбор)')
                ST.clear_intent(bp)
                return after, d
            raise RuntimeError('промежуточное состояние — О-5')
        return orig, patched

    return [('принятое намерение не завершает сессию', not_finishing),
            ('намерение не разбирается', no_intent),
            ('намеченная книга принимается всегда', adopt_always),
            ('снимок доказывает отсутствие заявок', snapshot_proves),
            ('намерение всегда снимается', drop_intent_always)]


def _session_mutations():
    """Мутации СЕССИОННОГО уровня: они ломают не решение, а поведение при сбое, и потому
    видны только на прогоне сессий с плохим брокером."""
    def no_restore():
        orig = DL.restore_to
        return orig, (lambda broker, target, route='F': (True, {}, []))

    def swallow_stuck():
        """Неснятые заявки игнорируются: восстановление объявляется удавшимся."""
        orig = DL._cancel_all
        return orig, (lambda broker: [])

    return [('восстановление после сбоя удалено', 'restore_to', no_restore),
            ('неснятые заявки игнорируются', '_cancel_all', swallow_stuck)]


def _feed_mutations():
    """Мутации СБОРЩИКА ВХОДОВ — каждая воспроизводит поведение до правок шестого круга.
    Здесь сидели четыре из пяти дефектов первой живой сессии, и ни один не был пойман."""
    import feed as FD

    def raw_es_price():
        """Цена ES подаётся КАК ЕСТЬ, без приведения к единице расчёта (дефект первой живой
        сессии). Прежняя редакция этой мутации умножала цену в источнике, а сборщик тут же
        делил обратно — поломка сокращалась сама с собой и не проверяла ничего."""
        orig = FD.es_to_unit
        return orig, (lambda es_px: es_px), 'es_to_unit'

    def loose_bool():
        """Состояние ноги через bool() (дефект №21): 'False' и пустое дают ИСТИНУ."""
        orig = FD._strict_bool
        return orig, (lambda v, where: bool(v)), '_strict_bool'

    def no_date_check():
        """Даты баров не проверяются вовсе. Сигнатура обязана совпадать с боевой (с
        expected_prev): прежняя мутация роняла штатный сценарий TypeError'ом и объявлялась
        «пойманной» по поломке стенда, а не по принятию устаревшего бара (№28)."""
        orig = FD.closes
        def patched(ib, contract, today, expected_prev=None):
            import math
            import pandas as pd
            df = FD._bars(ib, contract)
            dates = [pd.Timestamp(x).normalize() for x in df['date']]
            t = pd.Timestamp(today).normalize()
            last_is_today = dates[-1] == t
            i = -2 if last_is_today else -1
            return (float(df.iloc[i]['close']), dates[i],
                    float(df.iloc[-1]['close']) if last_is_today else None,
                    dates[-1] if last_is_today else None)
        return orig, patched, 'closes'

    def fallback_last_row():
        """При отсутствии строки текущего месяца берётся последняя имеющаяся (как было ДО
        исправления конвенции): август торгуется июльским состоянием — на бумажном счёте
        это открыло ногу Б, которую августовское состояние выключает."""
        orig = FD.signal_state
        def patched(today, path=None, holidays=()):    # №23: совместимая сигнатура
            try:
                return orig(today, path, holidays)
            except FD.FeedError:
                import pandas as pd
                import os as _o
                from pathlib import Path as _P
                pp = _P(path) if path else (_P(_o.path.expanduser('~/.addfut')) / 'signals_live.csv')
                df = pd.read_csv(pp, parse_dates=[0]).set_index('Unnamed: 0' if False else None)
                df = pd.read_csv(pp, parse_dates=[0]); df = df.set_index(df.columns[0]).sort_index()
                last = df.iloc[-1]
                return bool(last.iloc[0]), bool(last.iloc[1]), df.index[-1], 0
        return orig, patched, 'signal_state'

    def no_mult_check():
        """Сверка множителей с моделью отключена: смена спецификации биржей проходит молча,
        и размер книги меняется без единого отказа."""
        orig = FD.MODEL_MULT
        return orig, {}, 'MODEL_MULT'

    def refs_loose_age():
        """Ориентиры без точной предыдущей сессии (как было до девятнадцатого круга,
        №16): дальняя серия, отставшая на сессию, проходит пятидневный допуск."""
        orig = FD.reference_prices

        def patched(ib, route='F'):
            today = FD.exchange_today()
            reg = FD.registry()
            want = ('CSPX', 'CBU0') if route == 'E' else ('ES', 'MES', 'ZN')
            out = {}
            for name, r in reg.items():
                if not any(name.startswith(w) for w in want):
                    continue
                try:
                    px, _, _, _ = FD.closes(ib, FD.contract_of(ib, name, reg), today)
                    out[name] = px
                except FD.FeedError as ex:
                    out[f'ОРИЕНТИР-НЕТ:{name}'] = str(ex)[:80]
                    continue
            return out
        return orig, patched, 'reference_prices'

    return [('множители биржи не сверяются с моделью', no_mult_check),
            ('цена ES без приведения к единице', raw_es_price),
            ('состояние ноги через bool()', loose_bool),
            ('даты баров не проверяются', no_date_check),
            ('ориентиры без точной сессии', refs_loose_age),
            ('нет строки месяца — берётся последняя', fallback_last_row)]


def _run_mutations():
    """Мутации ЗАПУСКА СЕССИИ. Ломается не арифметика, а порядок и условия: наблюдение
    начинает торговать, отказ по незамкнутой сессии снимается, ориентиры не снимаются,
    маршрут игнорируется. Ни одно утверждение уровня решения этого не видит."""
    import session as SS
    import feed as FD

    def dry_trades():
        """Наблюдение подаёт заявки: режим П-2 перестаёт быть наблюдением."""
        orig = SS.do_trade
        return orig, (lambda ib, route, dry: orig(ib, route, False)), 'do_trade'

    def no_refs():
        """Ориентиры не снимаются — журнал §7 снова перестаёт копить наблюдения."""
        orig = FD.reference_prices
        return orig, (lambda ib, route='F': {}), 'reference_prices'

    def ignore_provisional():
        """Отказ по незамкнутой предыдущей сессии снимается: торговля идёт по фиктивному
        триггеру капа."""
        orig = SS.do_trade
        import daily as DL
        src = orig.__code__
        def patched(ib, route, dry):
            import state as ST
            bp = SS._book_path(route)
            cls = DL.BookE if route == 'E' else DL.Book
            b, sess, _ = ST.load(bp, cls)
            if b is not None and getattr(b, 'close_provisional', False):
                from dataclasses import replace
                ST.save(bp, replace(b, close_provisional=False), route, sess)
            return orig(ib, route, dry)
        return orig, patched, 'do_trade'

    def force_route_f():
        """Маршрут игнорируется: маршрут Е считается фьючерсным."""
        orig = SS.do_trade
        return orig, (lambda ib, route, dry: orig(ib, 'F', dry)), 'do_trade'

    def no_reconcile():
        """Входная сверка книги отключена. Позднее исполнение, легшее ПОСЛЕ сессии, тогда не
        обнаруживается, и следующая сессия торгует по расходящейся книге. Прежняя редакция
        этой мутации подменяла reconcile в модуле daily, где его нет вовсе: правка уходила
        в пустоту, мутация ничего не ломала и честно объявлялась непойманной."""
        import state as ST
        orig = ST.reconcile
        return orig, (lambda book, route, pos, open_orders=None: []), None

    def handover_wrong_path():
        """Книга после перехода пишется по ДРУГОМУ пути (как было): ежедневный контур
        продолжает читать старую, источник истины раздваивается ровно в момент перевода
        маршрута. Мутация СУЖЕНА до самой передачи: прежняя редакция ломала путь книги
        глобально и «ловилась» посторонними утверждениями про замыкание — то есть по чужой
        причине, а значит ничего не доказывала.
        """
        import sys as _s
        from pathlib import Path as _P
        _s.path.insert(0, str(_P(__file__).resolve().parent.parent))
        import state as ST
        import transition as TRN
        orig = TRN.hand_over_book

        def patched(broker, from_route, to_route):
            real = ST.book_path
            ST.book_path = lambda route, override=None: real(route, override).with_name('book.json')
            try:
                return orig(broker, from_route, to_route)
            finally:
                ST.book_path = real
        return orig, patched, TRN, 'hand_over_book'

    def no_session_total():
        """Итоговая строка сессии не пишется (семнадцатый круг, №15 наоборот): журнал
        выглядит полным при потерянных строках исполнения."""
        import journal as JJ
        orig = JJ.append

        def patched(path, row):
            if row.get('instrument') == 'ИТОГ':
                return ''
            return orig(path, row)
        return orig, patched, JJ, 'append'

    def no_journal_verify():
        """Журнал не проверяется перед торговлей (как было): правка задним числом молча
        продолжается новыми строками."""
        import journal as JJ
        orig = JJ.verify
        return orig, (lambda path: 0), JJ, 'verify'

    def o3e_stale_target():
        """Цель О-3-Е — от капитала ДО расходов сокращения (как было до семнадцатого
        круга, №16): фактические комиссии не пересчитываются, L остаётся выше 1."""
        import daily as DLm

        orig = DLm.o3e_reduce

        def patched(capital, m, p_e, p_b, n_eq, n_bd, n0_eq, n0_bd, share):
            e = float(capital) - DLm.S.COST * (abs(n_eq - n0_eq) * p_e
                                               + abs(n_bd - n0_bd) * p_b)
            ne = min(n_eq, n0_eq, DLm.math.floor(share * (1.0 * m.st_eq * e) / p_e))
            nb = min(n_bd, n0_bd, DLm.math.floor(share * (1.0 * m.st_bd * e) / p_b))
            return ne, nb, e          # капитал не пересчитан по финальному обороту
        return orig, patched, DLm, 'o3e_reduce'

    def unknown_provable():
        """Неизвестный исход объявляется доказуемо восстановленным (шестнадцатый круг, №5
        наоборот): совпавший снимок засчитывается, ролл автопереносится, состояние пишется."""
        import daily as DLm
        orig = DLm.RollGap

        class patched(orig):
            def __init__(self, msg, provable=False):
                super().__init__(msg, provable=True)
        return orig, patched, DLm, 'RollGap'

    def post_o3e_swallow_none():
        """Пост-трейд None глотается (как было до девятнадцатого круга, №10): тревога
        только при известно-низком запасе, неизвестный принимается молча."""
        import daily as DLm
        orig = SS.post_o3e_alarm
        return orig, (lambda pc, ba: pc is not None and pc < DLm.O3E_MIN), SS, 'post_o3e_alarm'

    def post_o3e_removed():
        """Пост-трейд проверка О-3-Е удалена целиком (как было до восемнадцатого круга,
        №1): запас после исполнений не смотрит никто."""
        orig = SS.post_o3e_alarm
        return orig, (lambda pc, ba: False), SS, 'post_o3e_alarm'

    def dry_writes_journal():
        """Наблюдение пишет строки §7 без итоговой (как было до девятнадцатого круга,
        №15): следующая живая сессия видит незакрытый журнал и отказывает."""
        import daily as DLm
        import journal as JJ
        orig = DLm.run_session

        def patched(broker, market, **kw):
            out = orig(broker, market, **kw)
            if kw.get('dry_run') and kw.get('journal_path'):
                JJ.append(kw['journal_path'], dict(
                    date=f'{market.date:%Y-%m-%d}', leg='А', instrument='ESU26', qty=1,
                    px_order='7747.5', px_fill='', commission='', reason='наблюдение',
                    nav='', leverage='', roll_spread_near='', roll_spread_far='', note=''))
            return out
        return orig, patched, DLm, 'run_session'

    def statedir_own_home():
        """Каталог журнала/тревог живёт своей жизнью (как было до девятнадцатого круга,
        №17): без ADDFUT_DIR — жёсткий ~/.addfut вместо каталога замка."""
        import os as _os
        from pathlib import Path as _P
        orig = SS.state_dir
        return (orig,
                (lambda: _P(_os.environ.get('ADDFUT_DIR', _os.path.expanduser('~/.addfut')))),
                SS, 'state_dir')

    def worm_missing_ok():
        """Отсутствие обязательного файла — строка «ФАЙЛА НЕТ» при успешном снимке
        (как было до девятнадцатого круга, №18)."""
        import worm_anchor as WA
        import hashlib as _hl
        from pathlib import Path as _P
        orig = WA._sha

        def patched(p, required=False):
            try:
                return _hl.sha256(_P(p).read_bytes()).hexdigest()
            except OSError:
                return 'ФАЙЛА НЕТ'
        return orig, patched, WA, '_sha'

    def worm_git_name_only():
        """HEAD проверяется по ИМЕНИ файла (как было до девятнадцатого круга, №19):
        подмена содержимого pre-commit hook'ом проходит заверение."""
        import subprocess as _sp
        import worm_anchor as WA
        orig = WA._git_commit_verified

        def patched(out):
            rel = out.relative_to(WA.ROOT)
            r1 = _sp.run(['git', '-C', str(WA.ROOT), 'add', str(rel)],
                         capture_output=True, text=True)
            if r1.returncode != 0:
                raise RuntimeError('git add отказал')
            r2 = _sp.run(['git', '-C', str(WA.ROOT), 'commit', '-q', '-m', 'w'],
                         capture_output=True, text=True)
            if r2.returncode != 0:
                raise RuntimeError('git commit отказал')
            r3 = _sp.run(['git', '-C', str(WA.ROOT), 'ls-tree', '--name-only', 'HEAD',
                          str(rel)], capture_output=True, text=True)
            if r3.returncode != 0 or not r3.stdout.strip():
                raise RuntimeError('якорь не виден в HEAD')
        return orig, patched, WA, '_git_commit_verified'

    def pin_not_required():
        """ДВАДЦАТЫЙ КРУГ, №5: торговый счёт не пинуется — адаптер берёт тот, что дал шлюз."""
        import session as SS
        return SS._account_pin, (lambda: None), '_account_pin'

    def window_gate_off():
        """ДВАДЦАТЫЙ КРУГ, №6: ворота торгового окна отключены — заявка уходит за краем
        окна с tif=GTC и висит до чужой сессии."""
        return (DL._window_gate, (lambda deadline, what='', margin_min=False: None),
                DL, '_window_gate')

    def rejected_archive_stays():
        """ДВАДЦАТЫЙ КРУГ, №22: отвергнутый архив остаётся под рабочим именем, и им можно
        восстановиться."""
        import worm_anchor as WA
        return WA.reject_archive, (lambda dst: ''), WA, 'reject_archive'

    return [('пин торгового счёта не требуется', pin_not_required),
            ('ворота торгового окна отключены', window_gate_off),
            ('отвергнутый архив остаётся рабочим', rejected_archive_stays),
            ('книга после перехода пишется мимо контура', handover_wrong_path),
            ('входная сверка книги отключена', no_reconcile),
            ('наблюдение подаёт заявки', dry_trades),
            ('ориентиры не снимаются', no_refs),
            ('отказ по незамкнутой сессии снят', ignore_provisional),
            ('неизвестный исход объявляется восстановленным', unknown_provable),
            ('цель О-3-Е от старого капитала', o3e_stale_target),
            ('итог сессии не пишется', no_session_total),
            ('журнал не проверяется перед торговлей', no_journal_verify),
            ('пост-трейд None глотается', post_o3e_swallow_none),
            ('пост-трейд проверка О-3-Е удалена', post_o3e_removed),
            ('наблюдение пишет строки §7', dry_writes_journal),
            ('каталог тревог живёт своей жизнью', statedir_own_home),
            ('нет файла — «ФАЙЛА НЕТ» при успехе', worm_missing_ok),
            ('HEAD проверяется по имени', worm_git_name_only),
            ('маршрут игнорируется', force_route_f)]


def _transition_mutations():
    """Мутации ПЕРЕХОДНОГО ИСПОЛНИТЕЛЯ. Переход — единственное место, где книга существует
    разорванной, и §8б ограничивает разрыв одним процентом капитала. Ломается ровно то, что
    этот разрыв удерживает."""
    import sys as _sys
    from pathlib import Path as _P
    _sys.path.insert(0, str(_P(__file__).resolve().parent.parent))
    import transition as TRN

    def limit_off():
        """Лимит непарной дельты снят: разрыв книги ничем не ограничен."""
        orig = TRN.MAX_UNPAIRED_PCT
        return orig, 1.0, 'MAX_UNPAIRED_PCT'


    def round_up_tail():
        """Дробный остаток источника округляется ВВЕРХ — источник уходит в короткую."""
        orig = TRN.plan_lots
        def patched(legs, capital):
            lots = orig(legs, capital)
            for l in lots:
                l['units'] = float(int(l['units']) + 1) if l['units'] % 1 else l['units']
            return lots
        return orig, patched, 'plan_lots'

    def frac_fut_ok():
        """Дробное исполнение фьючерса принимается вместо инцидента."""
        orig = TRN._int_fill
        return orig, (lambda fill, what: float(fill)), '_int_fill'

    def price_band_off():
        """Полоса цен плана снята: заниженная в десять раз цена цели проходит, и число
        долей цели, лимит §8б и маржа считаются по числу вызывающего (29-й круг, №3)."""
        orig = TRN.check_plan_prices
        return orig, (lambda broker, legs, src_cls: True), 'check_plan_prices'

    def replay_done():
        """Завершённые лоты исполняются повторно: обрыв даёт ДВОЙНУЮ продажу."""
        orig = TRN._run_lots
        # СИГНАТУРА СОВМЕСТИМА С БОЕВОЙ (двадцать третий круг, №25): без window_till
        # боевой вызов падал TypeError, и «мутация поймана» означало крах вызова на
        # ПОСТОРОННЕМ оконном сценарии, а не работу проверяемой защиты.
        def patched(broker, plan, st, state_path, lim, unp, dst_bought, fail,
                    _M=None, journal=None, window_till=None):
            st = dict(st); st['done'] = []
            return orig(broker, plan, st, state_path, lim, unp, dst_bought, fail, _M,
                        journal, window_till)
        return orig, patched, '_run_lots'

    def no_frac():
        """Дробность источника не признаётся: хвост 0,5 округляется вверх до 1 и уводит
        источник в короткую позицию."""
        orig = TRN._frac
        return orig, (lambda instr: False), '_frac'

    def margins_unbound():
        """Привязка замера к реестру отключена (как было до шестнадцатого круга, №4):
        файл читается без _meta и без сверки серий с живым реестром."""
        orig = TRN._live_margins

        def patched():
            import json as _json
            import os as _os
            from pathlib import Path as _P
            cand = _os.environ.get('ADDFUT_MARGINS')
            p = _P(cand) if cand else (_P(TRN.__file__).resolve().parent / 'live'
                                       / 'margins_live.json')
            if not p.exists():
                return {}
            raw = _json.loads(p.read_text(encoding='utf-8'))
            raw.pop('_meta', None)
            out = {}
            for k, v in raw.items():
                root = k.rstrip('0123456789').rstrip('UZHM') or k
                out[root] = max(out.get(root, 0.0), float(v.get('maint') or v.get('init')))
            return out
        return orig, patched, '_live_margins'

    def margin_gap_constants():
        """Дыры существующего замера добираются константами (как было ДО привязки к
        сериям): старый разбор файла без _meta и без сверки с реестром плюс .get с
        запасным значением — неполнота при повышенном требовании пряталась целиком."""
        orig = TRN.book_margin

        def patched(book, reg, prices=None):
            import json as _json
            import os as _os
            from pathlib import Path as _P
            cand = _os.environ.get('ADDFUT_MARGINS')
            p = _P(cand) if cand else (_P(TRN.__file__).resolve().parent / 'live'
                                       / 'margins_live.json')
            lm = {}
            if p.exists():
                raw = _json.loads(p.read_text(encoding='utf-8'))
                raw.pop('_meta', None)
                for k, v in raw.items():
                    root = k.rstrip('0123456789').rstrip('UZHM') or k
                    lm[root] = max(lm.get(root, 0.0),
                                   float(v.get('maint') or v.get('init')))
            total = 0.0
            for instr, units in book.items():
                if instr not in reg:
                    raise TRN.Incident(f'{instr}: инструмента нет в реестре')
                if reg[instr]['sec_type'] == 'FUT':
                    total += abs(int(units)) * lm.get(instr, TRN.FUT_MARGIN[instr])
                else:
                    total += abs(int(units)) * float((prices or {})[instr]) * TRN.ETF_MAINT
            return total
        return orig, patched, 'book_margin'

    def partial_ignored():
        """Частичный прогресс лота выбрасывается (как было до семнадцатого круга, №1):
        повтор продаёт лот целиком поверх уже исполненной части."""
        orig = TRN._run_lots

        # СИГНАТУРА СОВМЕСТИМА С БОЕВОЙ (двадцать третий круг, №25): без window_till
        # боевой вызов падал TypeError, и «мутация поймана» означало крах вызова на
        # ПОСТОРОННЕМ оконном сценарии, а не работу проверяемой защиты.
        def patched(broker, plan, st, state_path, lim, unp, dst_bought, fail,
                    _M=None, journal=None, window_till=None):
            st.pop('partial', None)
            return orig(broker, plan, st, state_path, lim, unp, dst_bought, fail,
                        _M, journal)
        return orig, patched, '_run_lots'

    def resume_unp_zeroed():
        """Остаток непарной дельты после resume обнуляется (как было до семнадцатого
        круга, №2): лимит §8б считается свободным."""
        orig = TRN._run_lots

        # СИГНАТУРА СОВМЕСТИМА С БОЕВОЙ (двадцать третий круг, №25): без window_till
        # боевой вызов падал TypeError, и «мутация поймана» означало крах вызова на
        # ПОСТОРОННЕМ оконном сценарии, а не работу проверяемой защиты.
        def patched(broker, plan, st, state_path, lim, unp, dst_bought, fail,
                    _M=None, journal=None, window_till=None):
            for k in unp:
                unp[k] = 0.0
            return orig(broker, plan, st, state_path, lim, unp, dst_bought, fail,
                        _M, journal)
        return orig, patched, '_run_lots'

    def gate_sell_only():
        """Лимит 390 — только перед продажей (как было до девятнадцатого круга, №8):
        покупка №391 и компенсации уходят брокеру без проверки."""
        orig = TRN._order_gate

        def patched(st, broker, fail, where='', window_till=None):   # №25
            if str(where).startswith('продажа'):
                return orig(st, broker, fail, where, window_till)
        return orig, patched, '_order_gate'

    def mapped_only():
        """Маржа preflight — только по отображённой книге (как было до девятнадцатого
        круга, №2): безопасность доказывается для ДРУГОЙ физической книги."""
        orig = TRN.target_book
        return orig, (lambda legs, mapped=True: orig(legs, True)), 'target_book'

    def maint_first():
        """Требование серии — maint прежде init (как было до восемнадцатого круга, №13):
        поддерживающее занижает маржу открытия."""
        orig = TRN._margin_of
        return orig, (lambda v: float(v.get('maint') or v.get('init'))), '_margin_of'

    def age_unchecked():
        """Давность замера не проверяется (как было до восемнадцатого круга, №13)."""
        orig = TRN._meta_age_ok
        return orig, (lambda md: (True, 0)), '_meta_age_ok'

    def alarm_silent():
        """Тревога перехода не пишется (как было до девятнадцатого круга, №13): MIXED
        после публикации книги остаётся невидимым автопилоту."""
        orig = TRN._alarm_transition
        return orig, (lambda asof, reason: ''), '_alarm_transition'

    def gate_no_window():
        """ДВАДЦАТЫЙ КРУГ, №7: ворота заявки не смотрят на край общего окна LSE/CME —
        переход, начатый перед границей, продолжает торговать на закрытой площадке."""
        orig = TRN._order_gate

        def patched(st, broker, fail, where='', window_till=None):
            return orig(st, broker, fail, where=where, window_till=None)
        return orig, patched, '_order_gate'

    def comp_unchecked():
        """ДВАДЦАТЫЙ КРУГ, №2: исполнение компенсации НЕ сверяется с заказанным (как было).
        Недостача ровно одного контракта проходит и доходит до COMPLETE.

        Мутируется именно сверка, а не допуск pair_tol: допуск — вторая линия, и в одиночку
        он не наблюдаем (см. comp_fill_ok). Мутация, которую нельзя поймать, — не защита,
        а самообман; про допуск сказано в §12 как о защите в глубину."""
        return (TRN.compensation_ok,
                (lambda filled, want, ostatok, dprice: ''), TRN, 'compensation_ok')

    def asof_trusted():
        """asof принимается от вызывающего без сверки с биржевым сегодня (как было до
        двадцать второго круга, №1): вчерашняя дата отключает часы, праздники и барьер
        «resume в той же сессии», позволяя продолжить старый план по старым ценам."""
        import feed as _FDm
        orig = _FDm.exchange_today
        # подмена «сегодня» на дату стенда: проверка сойдётся при ЛЮБОМ asof стенда,
        # то есть защита перестанет отличать вчерашний план от сегодняшнего
        return orig, (lambda: __import__('pandas').Timestamp('2020-01-02').date()), \
               _FDm, 'exchange_today'

    return [('дата перехода принимается на веру', asof_trusted),
            ('край общего окна не проверяется', gate_no_window),
            ('исполнение компенсации не сверяется', comp_unchecked),
            ('дробность источника не признаётся', no_frac),
            ('лимит непарной дельты снят', limit_off),
            ('дробный остаток округляется вверх', round_up_tail),
            ('дробное исполнение фьючерса принимается', frac_fut_ok),
            ('полоса цен плана снята', price_band_off),
            ('привязка замера маржи отключена', margins_unbound),
            ('дыры замера добираются константами', margin_gap_constants),
            ('частичный прогресс лота выбрасывается', partial_ignored),
            ('остаток непарной дельты обнуляется', resume_unp_zeroed),
            ('лимит 390 только перед продажей', gate_sell_only),
            ('маржа только по отображённой книге', mapped_only),
            ('maint прежде init', maint_first),
            ('давность замера не проверяется', age_unchecked),
            ('тревога перехода не пишется', alarm_silent),
            ('завершённые лоты исполняются повторно', replay_done)]


def _roll_mutations():
    """Мутации ПОСЛЕДОВАТЕЛЬНОСТЕЙ ВОКРУГ РОЛЛА. Одношаговый перебор их не видит: там всё
    считается верно, ошибка появляется только на второй и третьей сессии."""
    import state as ST

    def pending_lost():
        """Признак отложенного ролла теряется при смене маршрута (как было)."""
        orig = ST.book_from_broker
        def patched(cls, positions, route, **kw):
            kw.pop('roll_pending', None)
            return orig(cls, positions, route, **kw)
        return orig, patched, ST, 'book_from_broker'

    def pending_sticky():
        """Признак не гаснет после исполнения: серия уезжает каждую сессию."""
        orig = DL.step
        def patched(book, m, capital, **kw):
            import dataclasses as dc
            d = orig(book, m, capital, **kw)
            if not d.refusals:
                d.book_after = dc.replace(d.book_after, roll_pending=True)
            return d
        return orig, patched, DL, 'step'

    def holidays_off():
        """Праздники не учитываются: дата ролла считается по пятидневке."""
        orig = DL.holidays_for
        return orig, (lambda *y: ()), DL, 'holidays_for'

    def ser_a_only():
        """Навёрстывание видит только ногу А (как было): книга из одного ZN доезжает до
        поставочной зоны."""
        orig = DL.leg_roll_overdue
        def patched(held, m):
            # срок проверяется только у ноги А: для вызова с серией ноги Б всегда False —
            # эмулируется тем, что признак Б гасится (вторая нога «невидима»)
            return orig(held, m) if held and held == getattr(m, '_probe_a', held) else False
        def patched2(held, m):
            return False if held == 'Z26_B_MARK' else orig(held, m)
        # надёжная эмуляция: гасим просрочку, если серия совпадает с ser_b смешанных книг
        def patched3(held, m):
            if held == 'U26' and getattr(m, 'roll_passed', True) is not None:
                # нога Б в наших сценариях держит U26 — «не видим» её
                return False
            return orig(held, m)
        return orig, patched3, DL, 'leg_roll_overdue'

    def global_overdue():
        """Просрочка ЛЮБОЙ ноги роллит ОБЕ (как было до пер-ножного признака)."""
        orig = DL.leg_roll_overdue
        def patched(held, m):
            return DL.missed_roll_check(  # общий признак вместо срока конкретной серии
                type('B', (), {'ser_a': held, 'ser_b': held})() if held else
                type('B', (), {'ser_a': None, 'ser_b': None})(), m) or (
                held is not None and orig(held, m))
        # проще и честнее: любая нога «просрочена», если просрочена хоть одна в книге —
        # эмулируем через замыкание на сам step? Нет: подменяем так, чтобы обе ноги дали True
        # при одной настоящей просрочке.
        def patched2(held, m):
            if held is None:
                return False
            b_fake = type('B', (), {'ser_a': held, 'ser_b': held})()
            return orig(held, m) or orig('U26', m)   # U26 просрочен в сентябре 2026
        return orig, patched2, DL, 'leg_roll_overdue'

    def roll_cost_both():
        """Стоимость переноса списывается с ОБЕИХ ног (как было до пятнадцатого круга, №2):
        при просрочке одной лишь Б исправная А тоже «платит» за ролл."""
        orig = DL.step

        def patched(book, m, capital, **kw):
            d = orig(book, m, capital, **kw)
            if d.refusals:
                return d
            rn = bool(m.roll_today) or bool(getattr(book, 'roll_pending', False))
            ra = rn or ((not rn) and DL.leg_roll_overdue(book.ser_a, m))
            rb = rn or ((not rn) and DL.leg_roll_overdue(book.ser_b, m))
            if ra != rb:                     # роллится ровно одна — добираем с исправной
                ba = d.book_after
                ue, ub = DL.units(ba, m)
                d.capital_after_costs -= DL.S.ROLL_BP * (
                    (ba.n_e * ue) if not ra else (ba.n_b * ub))
            return d
        return orig, patched, DL, 'step'

    def pending_global():
        """Признак отложенного ролла — ОБЩИЙ для ног (как было до девятнадцатого круга,
        №1): pending='Б' на повторе роллит и исправную А — Z26 уезжает в H27."""
        orig = DL.step

        def patched(book, m, capital, **kw):
            rp = getattr(book, 'roll_pending', False)
            if isinstance(rp, str) and rp:
                import dataclasses as dc
                book = dc.replace(book, roll_pending=True)
            return orig(book, m, capital, **kw)
        return orig, patched, DL, 'step'

    def pack_not_trade():
        """Смена упаковки не считается сделкой (как было до девятнадцатого круга, №3):
        заявок нет, финальная сверка падает каждую сессию."""
        orig = DL.Decision.trade
        patched = property(lambda self: bool(self.orders or self.roll_pairs))
        return orig, patched, DL.Decision, 'trade'

    def aliens_swallowed():
        """Посторонняя позиция выбрасывается при построении книги (как было до
        девятнадцатого круга, №12): чужой инструмент остаётся неуправляемым."""
        orig = ST.book_from_broker

        def patched(cls, positions, route, *, ser_a=None, ser_b=None, unit_is_mes=True,
                    d_fix=0.0, st_eq=None, st_bd=None, roll_pending=False):
            pos = {k: float(v) for k, v in (positions or {}).items() if float(v) != 0.0}
            _pend = roll_pending if isinstance(roll_pending, str) else bool(roll_pending)
            if route == 'E':
                return cls(n_eq=pos.get('CSPX', 0), n_bd=pos.get('CBU0', 0),
                           prev_st_eq=st_eq, prev_st_bd=st_bd, roll_pending=_pend)
            es = mes = zn = 0
            for k, v in pos.items():
                if k.startswith('MES'):
                    mes += int(v); ser_a = ser_a or k[3:]
                elif k.startswith('ES'):
                    es += int(v); ser_a = ser_a or k[2:]
                elif k.startswith('ZN'):
                    zn += int(v); ser_b = ser_b or k[2:]
            return cls(n_e=(es * 10 + mes) if unit_is_mes else es, n_b=zn,
                       unit_is_mes=unit_is_mes, d_fix=d_fix, ser_a=ser_a, ser_b=ser_b,
                       es_held=es if unit_is_mes else None,
                       prev_st_eq=st_eq, prev_st_bd=st_bd, roll_pending=_pend)
        return orig, patched, ST, 'book_from_broker'

    def repack_on_refusal():
        """ДВАДЦАТЫЙ КРУГ, №4: упаковка перекладывается и на ветке отказа §8 — уходит
        встречная ПОКУПКА ES там, где наращивание запрещено."""
        return (DL.keep_pack_on_refusal,
                (lambda refusals, roll_a, b, n_e, es_after: es_after),
                DL, 'keep_pack_on_refusal')

    def repack_free():
        """ДВАДЦАТЫЙ КРУГ, №3: смена упаковки не списывает издержек — кап и плечо
        считаются так, будто встречные заявки бесплатны."""
        return DL.repack_cost, (lambda grid, u_e: 0.0), DL, 'repack_cost'

    def series_merged():
        """ДВАДЦАТЫЙ КРУГ, №10: серии одного корня складываются в одну выдуманную —
        поставочный контракт исчезает из состояния, оставаясь у брокера."""
        import state as _ST
        return _ST.check_one_series, (lambda pos: None), _ST, 'check_one_series'

    def pending_to_bool():
        """ДВАДЦАТЫЙ КРУГ, №11: пер-ножный признак приводится к bool — 'Б' становится
        «обе ноги», и после возврата Ф->Е->Ф исправная нога А уходит в дальнюю серию."""
        import sys as _s2
        from pathlib import Path as _P2
        _s2.path.insert(0, str(_P2(__file__).resolve().parent.parent))
        import transition as _TRN2
        return (_TRN2.carry_pending,
                (lambda pb: bool(getattr(pb, 'roll_pending', False)) if pb else False),
                _TRN2, 'carry_pending')

    def resume_any_day():
        """ДВАДЦАТЫЙ КРУГ, №1: resume принимается из ЛЮБОЙ сессии — продолжение идёт по
        вчерашнему капиталу, вчерашнему лимиту §8б и вчерашним ценам."""
        import sys as _s3
        from pathlib import Path as _P3
        _s3.path.insert(0, str(_P3(__file__).resolve().parent.parent))
        import transition as _TRN3
        return (_TRN3.resume_same_session, (lambda st, asof: True),
                _TRN3, 'resume_same_session')

    return [('пер-ножный ролл приводится к bool', pending_to_bool),
            ('resume принимается из любой сессии', resume_any_day),
            ('серии одного корня складываются', series_merged),
            ('упаковка перекладывается при отказе §8', repack_on_refusal),
            ('смена упаковки бесплатна', repack_free),
            ('просрочка одной ноги роллит обе', global_overdue),
            ('roll_pending общий для ног', pending_global),
            ('смена упаковки не сделка', pack_not_trade),
            ('посторонняя позиция глотается', aliens_swallowed),
            ('навёрстывание видит только ногу А', ser_a_only),
            ('стоимость ролла списывается с обеих ног', roll_cost_both),
            ('признак отложенного ролла теряется', pending_lost),
            ('признак ролла не гаснет', pending_sticky),
            ('праздники не учитываются', holidays_off)]


def _signal_mutations():
    """Мутации ОБНОВЛЯТОРА СИГНАЛОВ: каждая отключает одну из его защит."""
    import signal_update as SU

    def overlap_off():
        """Сверка перекрытия с историей отключена: молча уехавший источник проходит."""
        orig = SU._verify_overlap
        return orig, (lambda sym, col, ref, st: None), '_verify_overlap'

    def tail_off():
        """Проверка последней сессии месяца отключена: полумесячное «закрытие» проходит."""
        orig = SU._verify_month_tail
        # СИГНАТУРА СОВМЕСТИМА (двадцать второй круг, №23): подмена без months= падала
        # TypeError, и «мутация поймана» означало крах вызова, а не работу защиты.
        return orig, (lambda sym, df, me, months=None: None), '_verify_month_tail'

    def levels_off():
        """Сверка уровней отключена: неоднородный пересчёт истории поставщиком проходит."""
        orig = SU._check_levels
        return orig, (lambda sym, live, me: None), '_check_levels'

    def partial_ok():
        """Частичный сайдкар принимается (как было до пятнадцатого круга, №5): нет столбца
        ноги или общих месяцев — молчаливый возврат к слабым 12 битам."""
        orig = SU._check_levels

        def patched(sym, live, me):
            try:
                return orig(sym, live, me)
            except SU.SignalError as ex:
                if 'столбца' in str(ex) or 'общих месяцев' in str(ex):
                    return None
                raise
        return orig, patched, '_check_levels'

    def fresh_off():
        """Сверка свежего месяца с TRADES отключена (как было до шестнадцатого круга, №3):
        порча последнего закрытия скорректированного ряда проходит молча."""
        orig = SU._verify_fresh
        return orig, (lambda ib, sym, primary, me, months: None), '_verify_fresh'

    def border_fixed():
        """Пограничная зона НЕ расширена до дивидендной границы (как было): занижение
        размером с купон, меняющее знак, дописывается без подтверждения."""
        orig = SU._verify_border

        def patched(sym, me, covered=None):    # №23: совместимая сигнатура
            import os as _os
            sma = me.rolling(SU.SMA).mean()
            d = me.index[-1]
            if sma.isna().loc[d]:
                return
            margin = abs(me.loc[d] / sma.loc[d] - 1.0)
            if margin <= SU.BORDER_TOL and _os.environ.get('ADDFUT_SIGNAL_CONFIRM') != '1':
                raise SU.SignalError(f'{sym}: решение месяца {d:%Y-%m} пограничное')
        return orig, patched, '_verify_border'

    def fresh_last_only():
        """Сверяется только ПОСЛЕДНИЙ месяц (как было до семнадцатого круга, №12):
        порча промежуточного месяца пропущенного окна проходит в SMA молча."""
        orig = SU._verify_fresh

        def patched(ib, sym, primary, me, months):
            return orig(ib, sym, primary, me, months[-1:] if months else [])
        return orig, patched, '_verify_fresh'

    def short_base_ok():
        """Короткая уровневая база принимается (как было): один общий месяц = «база»."""
        orig = SU._check_levels

        def patched(sym, live, me):
            try:
                return orig(sym, live, me)
            except SU.SignalError as ex:
                if 'коротка' in str(ex):
                    return set()
                raise
        return orig, patched, '_check_levels'

    def quotes_off():
        """Срез котировок не сверяется (как было до девятнадцатого круга, №5): общая
        порча сделочных срезов (ADJUSTED и TRADES — один бар) проходит молча."""
        orig = SU._verify_quotes
        return orig, (lambda ib, c, sym, mt, months, dur: None), '_verify_quotes'

    def tail_last_only():
        """ДВАДЦАТЫЙ КРУГ, №9: календарный хвост проверяется ТОЛЬКО у последнего месяца —
        дыра в промежуточном входит в SMA целой, а TRADES и MIDPOINT её не ловят: тот же
        поставщик, та же дыра."""
        orig = SU._verify_month_tail

        def patched(sym, df, me, months=None):
            return orig(sym, df, me, months=[me.index[-1]] if len(me) else [])
        return orig, patched, '_verify_month_tail'

    return [('хвост только у последнего месяца', tail_last_only),
            ('сверка уровней отключена', levels_off),
            ('частичный сайдкар принимается', partial_ok),
            ('сверка свежего месяца отключена', fresh_off),
            ('срез котировок не сверяется', quotes_off),
            ('сверяется только последний месяц', fresh_last_only),
            ('короткая база уровней принимается', short_base_ok),
            ('пограничная зона не расширена', border_fixed),
            ('сверка перекрытия отключена', overlap_off),
            ('потерянный хвост месяца принимается', tail_off)]


def _j7_mutations():
    """Мутации СВЕРКИ §7 (девятнадцатый круг, №16; долг пар восемнадцатого, №15/№16):
    каждая воспроизводит поведение до правки."""
    import journal as J

    def _excl(mark=True, counter=True, empty=True, no_total=True, comm=True):
        import re as _re

        def patched(rows):
            skip = {}
            data = [r for r in rows if r.get('instrument') != 'ИТОГ']
            per_date = {}
            for r in data:
                per_date[r['date']] = per_date.get(r['date'], 0) + 1
            for r in rows:
                if r.get('instrument') != 'ИТОГ':
                    continue
                if mark and 'исключ' in (r.get('note') or ''):
                    skip.setdefault(r['date'], 'пометка')
                m = _re.search(r'строк (\d+)', r.get('note') or '')
                if counter and m and per_date.get(r['date'], 0) != int(m.group(1)):
                    skip.setdefault(r['date'], 'счётчик')
            if empty:
                for r in data:
                    if r['qty'] and (not r['px_fill'] or not r['px_order']):
                        skip.setdefault(r['date'], 'пустая цена')
            if no_total:                       # двадцатый круг, №20
                _wt = {r['date'] for r in rows if r.get('instrument') == 'ИТОГ'}
                for d in {r['date'] for r in data}:
                    if d not in _wt:
                        skip.setdefault(d, 'нет ИТОГ')
            if comm:                           # двадцатый круг, №18
                for r in data:
                    if r['qty'] and not r['commission']:
                        skip.setdefault(r['date'], 'комиссия неизвестна')
            return skip
        return patched

    def marks_unread():
        """Пометки исключения не читаются (как было до восемнадцатого круга, №15)."""
        return J._excluded_dates, _excl(mark=False), '_excluded_dates'

    def counter_unchecked():
        """Счётчик строк ИТОГ не сверяется (как было до девятнадцатого круга, №16)."""
        return J._excluded_dates, _excl(counter=False), '_excluded_dates'

    def empty_dropped_silently():
        """Пустые цены отбрасываются молча, дата остаётся в выборке (как было)."""
        return J._excluded_dates, _excl(empty=False), '_excluded_dates'

    def nan_passes():
        """NaN проходит арифметику (как было до девятнадцатого круга, №16)."""
        return J._num, (lambda x, what: float(x)), '_num'

    def roll_two_sided():
        """Ролловый номинал двусторонний (как было до восемнадцатого круга, №16):
        измеренная ставка занижается вдвое."""
        orig = J._roll_block

        def patched(sub):
            if not sub:
                return dict(label='ролл', n=0, verdict='наблюдений нет')
            bp, notional = J._cost_bp(sub)
            ratio = bp / J.MODEL_ROLL_BP if J.MODEL_ROLL_BP else float('inf')
            return dict(label='ролл', n=len(sub), bp=bp, model_bp=J.MODEL_ROLL_BP,
                        notional=notional, ratio=ratio, verdict=J._verdict(ratio))
        return orig, patched, '_roll_block'

    def no_total_ok():
        """Дата без строки ИТОГ считается полной (как было до двадцатого круга, №20)."""
        return J._excluded_dates, _excl(no_total=False), '_excluded_dates'

    def commission_zero():
        """Пустая комиссия считается нулевой (как было до двадцатого круга, №18):
        измеренный расход систематически занижен."""
        return J._excluded_dates, _excl(comm=False), '_excluded_dates'

    def threshold_union():
        """Порог двадцати — по ОБЪЕДИНЕНИЮ классов, счёт по строкам (как было до
        двадцатого круга, №19): единственный ролл получает полноценный вердикт."""
        def patched(sub):
            if not sub:
                return dict(label='ролл', n=0, verdict='наблюдений нет')
            bp2, _ = J._cost_bp(sub)
            closes = [r for r in sub if float(r['qty']) < 0]
            _, notional_one = J._cost_bp(closes)
            if not notional_one:
                _, notional_one = J._cost_bp(sub)
                notional_one /= 2.0
            loss = bp2 / 1e4 * J._cost_bp(sub)[1]
            bp = loss / notional_one * 1e4 if notional_one else 0.0
            ratio = bp / J.MODEL_ROLL_BP if J.MODEL_ROLL_BP else float('inf')
            return dict(label='ролл', n=len(sub), n_rows=len(sub), bp=bp,
                        model_bp=J.MODEL_ROLL_BP, notional=notional_one, ratio=ratio,
                        verdict=J._verdict(ratio))
        return J._roll_block, patched, '_roll_block'

    return [('пометки исключения не читаются', marks_unread),
            ('дата без ИТОГ считается полной', no_total_ok),
            ('пустая комиссия считается нулевой', commission_zero),
            ('порог двадцати по объединению классов', threshold_union),
            ('счётчик итога не сверяется', counter_unchecked),
            ('пустые цены отбрасываются молча', empty_dropped_silently),
            ('NaN проходит арифметику', nan_passes),
            ('ролловый номинал двусторонний', roll_two_sided)]


def run_j7_mutations():
    import journal as J
    import invariants as I
    miss = _clean_baseline('сверка §7', lambda: I.run_j7())
    if miss:
        return miss
    print(f"\n{'мутация сверки §7':<40}{'поймана':>9}  какими утверждениями")
    for label, make in _j7_mutations():
        orig, patched, attr = make()
        setattr(J, attr, patched)
        try:
            _, bad = I.run_j7()
        finally:
            setattr(J, attr, orig)
        print(f'{label:<40}{"да" if bad else "НЕТ":>9}  {", ".join(sorted(bad))[:56]}')
        if not bad:
            miss.append(label)
    return miss


def run_signal_mutations():
    import signal_update as SU
    import invariants as I
    miss = _clean_baseline('сигнал', lambda: I.run_signal())
    if miss:
        return miss
    print(f"\n{'мутация обновлятора':<40}{'поймана':>9}  какими утверждениями")
    for label, make in _signal_mutations():
        orig, patched, attr = make()
        setattr(SU, attr, patched)
        try:
            _, bad = I.run_signal()
        finally:
            setattr(SU, attr, orig)
        print(f'{label:<40}{"да" if bad else "НЕТ":>9}  {", ".join(sorted(bad))[:56]}')
        if not bad:
            miss.append(label)
    return miss


def run_roll_mutations():
    import invariants as I
    miss = _clean_baseline('ролл', lambda: I.run_roll())
    if miss:
        return miss
    _ = []
    print(f"\n{'мутация ролла':<40}{'поймана':>9}  какими утверждениями")
    for label, make in _roll_mutations():
        orig, patched, holder, attr = make()
        setattr(holder, attr, patched)
        try:
            _, bad = I.run_roll()
        finally:
            setattr(holder, attr, orig)
        print(f'{label:<40}{"да" if bad else "НЕТ":>9}  {", ".join(sorted(bad))[:56]}')
        if not bad:
            miss.append(label)
    return miss


def run_transition_mutations():
    import sys as _sys
    from pathlib import Path as _P
    _sys.path.insert(0, str(_P(__file__).resolve().parent.parent))
    import transition as TRN
    import invariants as I
    miss = _clean_baseline('переход', lambda: I.run_transition())
    if miss:
        return miss
    _ = []
    print(f"\n{'мутация перехода':<40}{'поймана':>9}  какими утверждениями")
    for label, make in _transition_mutations():
        # ДВА ВИДА КОРТЕЖА (двадцать второй круг, №21): часть мутаций указывает модуль явно
        # четвёртым элементом. Прежде распаковка была безусловно тройной, и первая же
        # четырёхэлементная роняла ВЕСЬ набор ValueError'ом — до остальных групп прогон не
        # доходил, selfcheck получал ненулевой код, а сообщение при этом гласило «все
        # мутации пойманы». Утверждение про «около 120 мутаций» этим файлом не
        # воспроизводилось вовсе.
        _got = make()
        if len(_got) == 4:
            orig, patched, _holder, attr = _got
        else:
            orig, patched, attr = _got
            _holder = TRN
        # МОДУЛЬ БЕРЁТСЯ ИЗ КОРТЕЖА, А НЕ ПОДРАЗУМЕВАЕТСЯ: патч в TRN при защите, живущей
        # в другом модуле, уходит в пустоту и даёт тихое «НЕ ПОЙМАНО» без объяснения —
        # ровно так три мутации оказались фиктивными (двадцатый круг, разбор 14.08).
        setattr(_holder, attr, patched)
        try:
            _, bad = I.run_transition()
        finally:
            setattr(_holder, attr, orig)
        print(f'{label:<40}{"да" if bad else "НЕТ":>9}  {", ".join(sorted(bad))[:56]}')
        if not bad:
            miss.append(label)
    return miss


def run_run_mutations():
    import session as SS
    import feed as FD
    import invariants as I
    miss = _clean_baseline('запуск', lambda: I.run_run())
    if miss:
        return miss
    _ = []
    print(f"\n{'мутация запуска сессии':<40}{'поймана':>9}  какими утверждениями")
    import state as ST
    for label, make in _run_mutations():
        got = make()
        if len(got) == 4:
            orig, patched, holder, attr = got
            setattr(holder, attr, patched)
            try:
                _, bad = I.run_run()
            finally:
                setattr(holder, attr, orig)
            print(f'{label:<40}{"да" if bad else "НЕТ":>9}  {", ".join(sorted(bad))[:56]}')
            if not bad:
                miss.append(label)
            continue
        orig, patched, attr = got
        if attr == 'margin_cushion*':       # мутация адаптера из прогона запуска
            import ib_broker as B
            attr, holder = 'margin_cushion', B.IBBroker
        elif attr is None:                  # мутация модуля состояния
            attr, holder = 'reconcile', ST
        else:
            holder = SS if hasattr(SS, attr) else FD
        setattr(holder, attr, patched)
        try:
            _, bad = I.run_run()
        finally:
            setattr(holder, attr, orig)
        print(f'{label:<40}{"да" if bad else "НЕТ":>9}  {", ".join(sorted(bad))[:56]}')
        if not bad:
            miss.append(label)
    return miss


def run_feed_mutations():
    import feed as FD
    import invariants as I
    miss = _clean_baseline('сборщик', lambda: I.run_feed())
    if miss:
        return miss
    _ = []
    print(f"\n{'мутация сборщика входов':<40}{'поймана':>9}  какими утверждениями")
    for label, make in _feed_mutations():
        orig, patched, attr = make()
        setattr(FD, attr, patched)
        try:
            _, bad = I.run_feed()
        finally:
            setattr(FD, attr, orig)
        print(f'{label:<40}{"да" if bad else "НЕТ":>9}  {", ".join(sorted(bad))[:56]}')
        if not bad:
            miss.append(label)
    return miss


def _sessions_bad():
    import invariants as I
    res = I.run_sessions(); bad = {}
    for r in res:
        for n, fn, needs in I.SESSION_INVARIANTS:
            if needs is not None and not needs(r):
                continue
            try:
                ok = fn(r)
            except Exception:
                ok = False
            if not ok:
                bad[n] = bad.get(n, 0) + 1
    return None, bad


def run_session_mutations():
    import invariants as I
    miss = _clean_baseline('сессии', _sessions_bad)
    if miss:
        return miss
    print(f"\n{'мутация сессии':<40}{'поймана':>9}  какими утверждениями")
    for label, attr, make in _session_mutations():
        orig, patched = make()
        setattr(DL, attr, patched)
        try:
            res = I.run_sessions()
            bad = {}
            for r in res:
                for n, fn, needs in I.SESSION_INVARIANTS:
                    if needs is not None and not needs(r):
                        continue
                    try:
                        ok = fn(r)
                    except Exception:
                        ok = False
                    if not ok:
                        bad[n] = bad.get(n, 0) + 1
        finally:
            setattr(DL, attr, orig)
        print(f'{label:<40}{"да" if bad else "НЕТ":>9}  {", ".join(sorted(bad))[:58]}')
        if not bad:
            miss.append(label)
    return miss


def run_intent_mutations():
    import daily as DL
    import invariants as I
    miss = _clean_baseline('намерение', lambda: I.run_intent())
    if miss:
        return miss
    _ = []
    print(f"\n{'мутация намерения':<40}{'поймана':>9}  какими утверждениями")
    for label, make in _intent_mutations():
        orig, patched = make()
        DL._resume_intent = patched
        try:
            _, bad = I.run_intent()
        finally:
            DL._resume_intent = orig
        print(f'{label:<40}{"да" if bad else "НЕТ":>9}  {", ".join(sorted(bad))[:60]}')
        if not bad:
            miss.append(label)
    return miss


def _clean_baseline(label, run):
    """ЧИСТЫЙ БАЗЛАЙН ОБЯЗАТЕЛЕН. Без него один старый отказ (например, после переформулировки
    сообщения) присутствовал бы при КАЖДОЙ мутации, и таблица печатала бы «поймана» для всех
    подряд — изменение текста тихо выключало бы доказательную силу всего прогона."""
    _, bad = run()
    if bad:
        print(f'\nБАЗЛАЙН ГРЯЗЕН ({label}): {sorted(bad)} — мутации не доказывают ничего')
        return [f'базлайн {label} грязен']
    return []


def run_pack_mutations():
    """ИЗБЫТОК УПАКОВКИ И ВОРОТА КАПА (двадцать девятый круг, №6). Своя группа, а не
    адаптерная: обе защиты видны только точечным стендам PACK — перебор состояний до
    нарушенной упаковки и до границы капа не достаёт, и первый прогон обеих мутаций
    честно показал «не поймал никто», хотя утверждения держались на тысячах состояний."""
    import daily as DLm
    import invariants as I
    miss = _clean_baseline('упаковка', lambda: I.run_pack())
    if miss:
        return miss
    print(f"\n{'мутация упаковки':<40}{'поймана':>9}  какими утверждениями")

    def excess_counts_roll():
        """ДВАДЦАТЬ ДЕВЯТЫЙ КРУГ, №6: избыток перекладки снова меряется ВМЕСТЕ со сменой
        серии — на каждом ролле ложная тревога о недосписанных деньгах и ложный срез капа."""
        orig = DLm.repack_excess
        def patched(before, after, unit_is_mes):
            phys = DLm.orders_from_books(before, after)          # БЕЗ приведения к общей серии
            g_all = DLm.repack_grid(phys, unit_is_mes) if phys else 0
            net_g = abs(after.n_e - before.n_e) * (1 if unit_is_mes else 10)
            return max(0, g_all - net_g), g_all, net_g
        return orig, patched, 'repack_excess'

    def excess_cap_gate_off():
        """ДВАДЦАТЬ ДЕВЯТЫЙ КРУГ, №6: избыток объявлен нулевым — ворота капа снова считают
        по завышенному капиталу, и книга проходит 2,00 с уже известными издержками."""
        orig = DLm.repack_excess
        return orig, (lambda before, after, unit_is_mes: (0, 0, 0)), 'repack_excess'

    miss = []
    for label, make in (('избыток перекладки считает оборот ролла', excess_counts_roll),
                        ('ворота капа без вычета избытка перекладки', excess_cap_gate_off)):
        orig, patched, attr = make()
        setattr(DLm, attr, patched)
        try:
            _, bad = I.run_pack()
        finally:
            setattr(DLm, attr, orig)
        print(f'{label:<40}{"да" if bad else "НЕТ":>9}  {", ".join(sorted(bad))[:56]}')
        if not bad:
            miss.append(label)
    return miss


def run_refusal_mutations():
    """Отказ §8 с ростом (шестнадцатый круг, №1/№6): пара к пересчёту под запретом."""
    import daily as DLm
    import invariants as I
    miss = _clean_baseline('отказ с ростом', lambda: I.run_refusal())
    if miss:
        return miss
    print(f"\n{'мутация отказа с ростом':<40}{'поймана':>9}  какими утверждениями")

    def old_late_filter():
        """Поздний фильтр вместо пересчёта (как было): решение считается БЕЗ запрета,
        рост откатывается после капа — срез исправной ноги и комиссии неподанных заявок
        остаются в решении."""
        orig = DLm.step

        def patched(book, m, capital, **kw):
            d = orig(book, m, capital, **kw)
            if not d.refusals:
                return d
            kw2 = dict(kw)
            kw2['check_guards'] = False
            u = orig(book, m, capital, **kw2)
            n0e, n0b = book.n_e, book.n_b
            ne, nb = u.book_after.n_e, u.book_after.n_b
            keep_e, keep_b = abs(ne) <= abs(n0e), abs(nb) <= abs(n0b)
            if keep_e and keep_b:
                return d
            fe = ne if keep_e else n0e
            fb = nb if keep_b else n0b
            import dataclasses as dc
            u.refusals = list(d.refusals)
            u.orders = {k: v for k, v in (('А', fe - n0e), ('Б', fb - n0b)) if v}
            u.book_after = dc.replace(u.book_after, n_e=fe, n_b=fb)
            return u
        return orig, patched

    miss = []
    for label, make in (('поздний фильтр вместо пересчёта', old_late_filter),):
        orig, patched = make()
        DLm.step = patched
        try:
            _, bad = I.run_refusal()
        finally:
            DLm.step = orig
        print(f'{label:<40}{"да" if bad else "НЕТ":>9}  {", ".join(sorted(bad))[:56]}')
        if not bad:
            miss.append(label)
    return miss


def run_adapter_mutations():
    import ib_broker as B
    import invariants as I
    miss = _clean_baseline('адаптер', lambda: I.run_adapter())
    if miss:
        return miss
    _ = []
    print(f"\n{'мутация адаптера':<40}{'поймана':>9}  какими утверждениями")
    for label, attr, make in _adapter_mutations():
        got = make()
        # Носитель по умолчанию — класс адаптера; мутация может назвать свой модуль
        # (сверка личности живёт в contracts и патчится там, где её ищут вызовы).
        orig, patched = got[0], got[1]
        holder = got[2] if len(got) == 3 else B.IBBroker
        setattr(holder, attr, patched)
        try:
            _, bad = I.run_adapter()
        finally:
            setattr(holder, attr, orig)
        killers = ', '.join(sorted(bad))[:70]
        print(f'{label:<40}{"да" if bad else "НЕТ":>9}  {killers}')
        if not bad:
            miss.append(label)
    return miss


if __name__ == '__main__':
    killers = {}
    missed = []
    base = run_once()
    if base:
        print('БАЗЛАЙН ГРЯЗЕН: на неизменённом коде уже нарушено:', sorted(base))
        missed.append('базлайн решения грязен — мутации не доказывают ничего')
    print(f"{'мутация':<36}{'поймана':>9}  какими утверждениями")
    for name, make in MUTATIONS:
        apply_, undo = make()
        apply_()
        try:
            fired = run_once() - base
        finally:
            undo()
        for f in fired:
            killers.setdefault(f, set()).add(name)
        mark = 'да' if fired else 'НЕТ'
        if not fired:
            missed.append(name)
        print(f"{name:<36}{mark:>9}  {', '.join(sorted(fired))[:70] or '—'}")
    print(f"\n{'инвариант':<52}{'убит мутациями':>16}")
    weak = []
    for nm, _, _ in I.INVARIANTS:
        k = killers.get(nm, set())
        if not k:
            weak.append(nm)
        print(f"{nm:<52}{len(k):>16}")
    if missed:
        print(f"\nМУТАЦИИ, КОТОРЫХ НЕ ПОЙМАЛ НИКТО ({len(missed)}) — дыры в сетке:")
        for m_ in missed:
            print(f'   {m_}')
    if weak:
        print(f"\nИНВАРИАНТЫ, КОТОРЫХ НЕ УБИЛА НИ ОДНА МУТАЦИЯ ({len(weak)}) — кандидаты в тождества:")
        for w in weak:
            print(f'   {w}')
    # МУТАЦИИ ЖИВОГО АДАПТЕРА — та же дисциплина на границе с брокером.
    miss_a = (run_adapter_mutations() + run_intent_mutations()
              + run_session_mutations() + run_feed_mutations()
              + run_run_mutations() + run_transition_mutations()
              + run_roll_mutations() + run_signal_mutations()
              + run_refusal_mutations() + run_j7_mutations()
              + run_pack_mutations())
    if miss_a:
        print(f"\nМУТАЦИИ АДАПТЕРА, КОТОРЫХ НЕ ПОЙМАЛ НИКТО ({len(miss_a)}):")
        for m_ in miss_a:
            print(f'   {m_}')
    sys.exit(1 if (missed or miss_a) else 0)
