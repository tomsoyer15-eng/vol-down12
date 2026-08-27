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

# Ворота покрытия: этот файл батарея не запускает — покрытие объявлено здесь.
ADDFUT_ПОКРЫТИЕ = 'мутационный прогон live/mutation.py'
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

    def series_unchecked_at_order():
        """ТРИДЦАТЬ ПЕРВЫЙ КРУГ, №8: сверка личности на границе заявки снова БЕЗ разбора
        имени — ровно тот набор, что был у _contract (реестр + ISIN). Согласованно
        подменённая строка (ESU26 с полями ESZ26) проходит, и заявка уходит в декабрьскую
        поставку под именем сентябрьской. Патчится contracts.identity_bad — единая точка
        вызова обоих читателей: адаптера и сборщика (урок №3 тридцатого круга — раннер
        обязан патчить ТОТ модуль, где защита живёт)."""
        import contracts as CTm
        orig = CTm.identity_bad
        return orig, (lambda ib, name, c, row: CTm.mismatches(c, row)
                      + CTm.verify_isin(ib, c, row)), CTm

    def unit_ref_mes_x10():
        """ТРИДЦАТЬ ПЕРВЫЙ КРУГ, №6: полоса единицы MES снова считается как у ES (котировка
        умножается на 10). Правильный план Е->Ф отвергается «вне рыночной полосы»."""
        import ib_broker as Bm
        import sim_v13 as Sm
        orig = Bm.IBBroker.unit_ref
        def patched(self, instrument, cls):
            r = orig(self, instrument, cls)
            name = str(instrument)
            root = ''.join(ch for ch in name if not ch.isdigit()).rstrip('UZHM') or name
            if r and root == 'MES':
                return (r[0] * 10.0, r[1] * 10.0)
            return r
        return orig, patched

    def etf_expect_off():
        """ТРИДЦАТЬ ТРЕТИЙ КРУГ, №4: личность фонда снова доказывается строкой реестра —
        независимое пинованное ожидание не спрашивается, и согласованная подмена
        листинговой линии проходит на границе заявки."""
        # МУТАЦИЯ ВОСПРОИЗВОДИТ ПРЕЖНИЙ ДЕФЕКТ, А НЕ СНОСИТ ЗАЩИТУ ЦЕЛИКОМ (тридцать пятый
        # круг, №11): регрессия, которой надо бояться, — ранний выход по классу из строки
        # реестра. Полное удаление ловилось другими случаями и эту строку не доказывало.
        import contracts as CTe
        orig = CTe.etf_expectation_bad

        def patched(name, c, row):
            if (row or {}).get('sec_type') != 'STK':
                return []                       # прежний ранний выход
            return orig(name, c, row)
        return orig, patched, CTe

    def wild_maint_ok():
        """ТРИДЦАТЬ ТРЕТИЙ КРУГ, №13: проверяется снова только NaN — inf даёт cushion 0,
        отрицательное требование даёт отрицательный, и вахта продаёт половину книги."""
        orig = B.IBBroker.margin_cushion
        def patched(self):
            self._summary_barrier()
            vals = (self.ib.accountSummary(self.account) if self.account
                    else self.ib.accountSummary())
            ewl = maint = None
            for v in vals:
                if v.tag == 'EquityWithLoanValue' and v.currency == 'USD':
                    ewl = float(v.value)
                if v.tag == 'MaintMarginReq' and v.currency == 'USD':
                    maint = float(v.value)
            if ewl is None or ewl != ewl:
                raise B.BrokerError('нет EquityWithLoanValue')
            if maint is not None and maint != maint:
                raise B.BrokerError('MaintMarginReq = NaN')
            if not maint:
                return None
            return ewl / maint
        return orig, patched

    def mdt_none_live():
        """ТРИДЦАТЬ ТРЕТИЙ КРУГ, №12: отсутствие подтверждения биржи снова считается
        подтверждением реального времени — задержанная котировка попадает в §7 как live."""
        orig = B.IBBroker._quote_ref
        def patched(self, instrument):
            t = self.ib.reqMktData(self._contract(instrument), '', True, False)
            self.ib.sleep(0)
            _mdt = getattr(t, 'marketDataType', None)
            _rt = bool(getattr(self, 'realtime_md', False)) and (_mdt in (None, 1))
            for v, live in ((t.last, _rt), (t.close, False)):
                v = float(v) if v is not None else float('nan')
                if v == v and v > 0:
                    return v, live
            return None, False
        return orig, patched

    def future_identity_copied():
        """Поставка копируется из ответа биржи (как было до восемнадцатого круга, №7)."""
        import first_connect as FC
        orig = FC.check_future_identity
        return orig, (lambda contract, root, tag: []), FC

    def preview_tif_unset():
        """СОРОК ЧЕТВЁРТЫЙ КРУГ (саморецензия, угол «от отрицания»): строка _o.tif убрана,
        и заявка предпросмотра уходит на шлюз без TIF. Пресет счёта тогда переопределяет
        TIF, whatIfOrder отдаёт ПУСТОЙ СПИСОК, и preview на боевом шлюзе возвращает
        «отложить» при ЛЮБОМ плане: переход после трёх попыток уходит в ABORT.

        Ровно это и происходило с 37-го по 43-й круг, и ни один стенд не краснел, потому
        что стаб отвечал всегда. Мутация бьёт в саму причину: заявка, дошедшая до шлюза,
        лишается TIF — как если бы строку в коде забыли."""
        orig = B.IBBroker.preview

        # СИГНАТУРА НЕ ПОВТОРЯЕТСЯ, А ПРОБРАСЫВАЕТСЯ (разбор /code-review 21.08). Шесть
        # мутантов вручную повторяли боевые аргументы, а preview менял их дважды за четыре
        # круга: пропущенный аргумент роняет мутанта TypeError'ом, и прогон засчитывает это
        # как «поймана» — зелёный вердикт, ничего не доказывающий. *a/**k отстать не может.
        def patched(self, *a, **k):
            orders = a[0] if a else k.get('orders')
            _ib = self.ib
            _wif = _ib.whatIfOrder

            def _stripped(contract, order):
                try:
                    order.tif = ''
                except Exception:
                    pass
                return _wif(contract, order)

            _ib.whatIfOrder = _stripped
            try:
                return orig(self, *a, **k)
            finally:
                _ib.whatIfOrder = _wif

        return orig, patched

    def preview_always_ok():
        """СОРОК ЧЕТВЁРТЫЙ КРУГ: предпросмотр слепо разрешает. Прежде эта мутация не
        ловилась ничем — единственное утверждение о preview требовало только «можно»."""
        orig = B.IBBroker.preview
        return orig, (lambda self, *a, **k: True)

    def preview_wrong_form():
        """СОРОК ЧЕТВЁРТЫЙ КРУГ: маржа доказывается для DAY-заявки, а в рынок уходит
        GTC+outsideRth. Прежде подмена формы не роняла ничего."""
        orig = B.IBBroker.preview

        # СИГНАТУРА НЕ ПОВТОРЯЕТСЯ, А ПРОБРАСЫВАЕТСЯ (разбор /code-review 21.08). Шесть
        # мутантов вручную повторяли боевые аргументы, а preview менял их дважды за четыре
        # круга: пропущенный аргумент роняет мутанта TypeError'ом, и прогон засчитывает это
        # как «поймана» — зелёный вердикт, ничего не доказывающий. *a/**k отстать не может.
        def patched(self, *a, **k):
            orders = a[0] if a else k.get('orders')
            _ib = self.ib
            _w = _ib.whatIfOrder

            def swap(contract, order):
                order.tif = 'DAY'
                order.outsideRth = False
                return _w(contract, order)

            _ib.whatIfOrder = swap
            try:
                return orig(self, *a, **k)
            finally:
                _ib.whatIfOrder = _w

        return orig, patched

    def preview_unpinned():
        """СОРОК ЧЕТВЁРТЫЙ КРУГ: привязка к счёту снята — маржа доказывается для
        произвольного счёта под тем же логином (дефект 37-го круга, №4)."""
        orig = B.IBBroker.preview

        # СИГНАТУРА НЕ ПОВТОРЯЕТСЯ, А ПРОБРАСЫВАЕТСЯ (разбор /code-review 21.08). Шесть
        # мутантов вручную повторяли боевые аргументы, а preview менял их дважды за четыре
        # круга: пропущенный аргумент роняет мутанта TypeError'ом, и прогон засчитывает это
        # как «поймана» — зелёный вердикт, ничего не доказывающий. *a/**k отстать не может.
        def patched(self, *a, **k):
            orders = a[0] if a else k.get('orders')
            _ib = self.ib; _w = _ib.whatIfOrder

            def f(contract, order):
                order.account = ''
                return _w(contract, order)

            _ib.whatIfOrder = f
            try:
                return orig(self, *a, **k)
            finally:
                _ib.whatIfOrder = _w

        return orig, patched

    def preview_frac_fut():
        """СОРОК ЧЕТВЁРТЫЙ КРУГ: округление снято — дробная what-if заявка на фьючерс
        уходит на шлюз, ответ пуст, законный переход уходит в ABORT (38-й круг, №5)."""
        orig = B.IBBroker.preview

        # СИГНАТУРА НЕ ПОВТОРЯЕТСЯ, А ПРОБРАСЫВАЕТСЯ (разбор /code-review 21.08). Шесть
        # мутантов вручную повторяли боевые аргументы, а preview менял их дважды за четыре
        # круга: пропущенный аргумент роняет мутанта TypeError'ом, и прогон засчитывает это
        # как «поймана» — зелёный вердикт, ничего не доказывающий. *a/**k отстать не может.
        def patched(self, *a, **k):
            orders = a[0] if a else k.get('orders')
            _ib = self.ib; _w = _ib.whatIfOrder

            def f(contract, order):
                q = float(getattr(order, 'totalQuantity', 0) or 0)
                if q and abs(q - round(q)) < 1e-9:
                    order.totalQuantity = q + 0.295
                return _w(contract, order)

            _ib.whatIfOrder = f
            try:
                return orig(self, *a, **k)
            finally:
                _ib.whatIfOrder = _w

        return orig, patched

    def preview_emergency_bypass():
        """СОРОК ЧЕТВЁРТЫЙ КРУГ: аварийный признак становится обходом норматива О-3-Е —
        слово вызывающего заменяет доказательство. Прежде не ловилось ничем: ветка
        emergency в наборе адаптера не исполнялась ни разу."""
        orig = B.IBBroker.preview

        # СИГНАТУРА НЕ ПОВТОРЯЕТСЯ, А ПРОБРАСЫВАЕТСЯ (разбор /code-review 21.08). Шесть
        # мутантов вручную повторяли боевые аргументы, а preview менял их дважды за четыре
        # круга: пропущенный аргумент роняет мутанта TypeError'ом, и прогон засчитывает это
        # как «поймана» — зелёный вердикт, ничего не доказывающий. *a/**k отстать не может.
        def patched(self, *a, **k):
            if k.get('emergency', a[1] if len(a) > 1 else False):
                return True
            return orig(self, *a, **k)

        return orig, patched

    def gross_band_mid():
        """СОРОК ЧЕТВЁРТЫЙ КРУГ: единица ноги Б снова берётся серединой полосы вместо
        модельной по d_fix (дефект 37-го круга, №3) — плечо занижается примерно на треть,
        и книга больше капа 2,00 проходит ворота. Прежде у gross() не было НИ ОДНОЙ
        мутации: защита капа мутационным контролем не наблюдалась вовсе."""
        orig = B.IBBroker.gross

        def patched(self, d_fix=None):
            return orig(self, 8.0)      # d_fix книги игнорируется: величина от него не зависит

        return orig, patched

    def unit_a_from_es():
        """СОРОК ЧЕТВЁРТЫЙ КРУГ, №1 (P0): единица ноги А снова строится из цены ES вместо
        самого SPY. Базис (ставка минус дивиденды) сдвигает оценку, gross берёт середину
        полосы как цену, и при отрицательном базисе книга выше CLOSE_CAP=2,00 проходит
        ворота капа."""
        import feed as _FDm
        import sim_v13 as _Sm
        orig = B.IBBroker.unit_ref

        def patched(self, instrument, cls, at_close=False):
            name = str(instrument)
            root = ''.join(c for c in name if not c.isdigit()).rstrip('UZHM') or name
            if at_close and root in ('ES', 'MES'):
                import daily as _DLm
                _t = _FDm.exchange_today()
                _p = _FDm.prev_session(_t, holidays=_DLm.holidays_for(_t.year))
                px, _d, _, _ = _FDm.closes(self.ib, _FDm.contract_of(self.ib, name), _t,
                                           expected_prev=_p)
                mult = _Sm.ES_MULT / 10.0 if root == 'MES' else _Sm.ES_MULT
                u = mult * _FDm.es_to_unit(float(px))
                return (u * (1.0 - self.UNIT_BAND_EQ), u * (1.0 + self.UNIT_BAND_EQ))
            return orig(self, instrument, cls, at_close=at_close)

        return orig, patched

    def release_by_increments():
        """СОРОК ЧЕТВЁРТЫЙ КРУГ, №2 (P0): возврат к прежнему — «все приращения
        неположительны» снова объявляется доказательством разгрузки. Приращения считаются
        против ещё не проданной исходной книги, и неттинг с продаваемыми активами делает их
        отрицательными там, где целевая книга на деле дороже. При запасе ниже 1,0 это
        обходит последнюю живую проверку."""
        orig = B.IBBroker._release_by_measure
        return orig, (lambda self, orders: True)

    def preview_negative_sum_passes():
        """Ранний выход по отрицательной СУММЕ приращений — как было до 40-го круга,
        №1: смесь [-900k, +800k] выглядит освобождением маржи, хотя положительная
        часть требует 800k при NLV 1 млн (запас 1,25x против норматива 1,40)."""
        orig = B.IBBroker.preview

        # СИГНАТУРА НЕ ПОВТОРЯЕТСЯ, А ПРОБРАСЫВАЕТСЯ (разбор /code-review 21.08). Шесть
        # мутантов вручную повторяли боевые аргументы, а preview менял их дважды за четыре
        # круга: пропущенный аргумент роняет мутанта TypeError'ом, и прогон засчитывает это
        # как «поймана» — зелёный вердикт, ничего не доказывающий. *a/**k отстать не может.
        def patched(self, *a, **k):
            orders = a[0] if a else k.get('orders')
            if orders:
                try:
                    import contracts as _CTp
                    from ib_insync import MarketOrder as _MO
                    _ims = []
                    for _inst, _qty in orders:
                        _q = float(_qty)
                        if not _q:
                            continue
                        if str(_inst) not in _CTp.ETF_EXPECT:
                            _q = float(int(round(_q)))
                        _o = _MO("BUY" if _q > 0 else "SELL", abs(_q))
                        _o.whatIf = True
                        _o.tif = "GTC"
                        _o.outsideRth = True
                        if self.account:
                            _o.account = self.account
                        _st = self.ib.whatIfOrder(self._contract(str(_inst)), _o)
                        _im = getattr(_st, "initMarginChange", None) if _st is not None else None
                        if _im in (None, ""):
                            return False
                        _ims.append(float(_im))
                    if sum(_ims) <= 0:
                        return True                      # ВОТ ОН, ранний выход
                except Exception:
                    return False
            return orig(self, *a, **k)
        return orig, patched

    def measure_margin_no_tif():
        """TIF снят в ЗАМЕРЕ маржи (first_connect) — как было до 18.08.2026: пресет
        счёта переопределяет TIF, whatIfOrder отдаёт пустой список, замер молча не
        обновляется, и переход идёт по устаревшему файлу, пока тот проходит возраст."""
        import first_connect as _FCm
        orig = _FCm.measure_margin

        def patched(ib, con_id, exchange, account):
            from ib_insync import Contract as _C, MarketOrder as _MO
            c = _C(conId=con_id, exchange=exchange)
            ib.qualifyContracts(c)
            _o = _MO("BUY", 1)
            _o.account = account
            _o.outsideRth = True
            st = ib.whatIfOrder(c, _o)
            if st and getattr(st, "initMarginChange", None):
                return dict(init=float(st.initMarginChange),
                            maint=float(st.maintMarginChange))
            return None
        return orig, patched, _FCm      # носитель — модуль замера, не адаптер

    def preview_noplan_unit_threshold():
        """Предпросмотр без плана снова судит по единице, а не по нормативу О-3-Е — как
        было до 45-го круга, №2: полностью исполненный resume получает COMPLETE при живом
        запасе 1,20 против норматива 1,40, и книга уходит в ночь пробитой."""
        orig = B.IBBroker.preview

        def patched(self, *a, **k):
            orders = a[0] if a else k.get('orders')
            emergency = k.get('emergency', a[1] if len(a) > 1 else False)
            done_all = k.get('done_all', a[2] if len(a) > 2 else False)
            # ПОЛЯ ОТВЕТА ЗАВОДЯТСЯ И У МУТАНТА (разбор /code-review): без _preview_why
            # стенд падал AttributeError на сценарии normal, то есть «ловил» мутацию
            # поломкой обвязки, а не наблюдением подменённого порога.
            self._preview_why = ''
            self._preview_pass_why = ''
            if not orders:
                try:
                    _c = self.margin_cushion()
                except Exception:
                    self._preview_why = 'запас счёта неизвестен'
                    return False
                if _c is None:
                    self._preview_why = 'запас счёта не число (None)'
                    return False
                if float(_c) >= 1.0:             # ВОТ ОН, прежний порог
                    return True
                self._preview_why = 'плана нет, запас ниже 1.0'
                return False
            return orig(self, *a, **k)
        return orig, patched

    def buy_direction_inverted():
        """Покупка единиц идёт ПРОДАЖЕЙ (разбор /code-review 45-го круга): зеркало уже
        покрытой инверсии продажи. Денежная граница: вместо входа в цель книга уходит в
        короткую по цели, а источник остаётся целым — MIXED с двойной экспозицией."""
        orig = B.IBBroker.buy_units

        def patched(self, *a, **k):
            return B.IBBroker.sell_units(self, *a, **k)
        return orig, patched

    def pair_clock_never_advances():
        """Часы непарной позиции стоят: minutes_since всегда отдаёт ноль — как если бы
        разрыв ноги только что открылся. Ворота §8б, ограничивающие ДЛИТЕЛЬНОСТЬ непарного
        состояния, при этом не срабатывают никогда, и книга может стоять разорванной сколь
        угодно долго. mark_pair мутацию имеет, а его вторая половина — нет."""
        orig = B.IBBroker.minutes_since
        return orig, (lambda self, *a, **k: 0.0)

    def dref_cache_sticky():
        """Кэш дюрационной базы снова липнет и к ЖИВОМУ чтению — как было до разбора
        /code-review: unit_ref зовут и мимо gross(), где стоит единственный сброс, и
        доходность часовой давности выглядела бы исправной проверкой."""
        orig = B.IBBroker._dref_once

        def patched(self, today, expected_prev):
            _key = (str(today), str(expected_prev))
            _c = self._dref_cache
            if _c is not None and _c[0] == _key:
                return _c[1]
            _d = orig(self, today, expected_prev)
            self._dref_cache = (_key, _d)          # ВОТ ОНО: пишем и живое значение
            return _d
        return orig, patched

    def preview_drops_carveouts():
        """Беспланный предпросмотр снова игнорирует аварийность и «всё исполнено» — как
        было до разбора /code-review: аварийный выход Е->Ф запирается ровно при
        маржинальном стрессе, а завершённый resume через три POSTPONED уходит в MIXED на
        НЕразорванной книге."""
        orig = B.IBBroker.preview

        def patched(self, *a, **k):
            # Признаки гасятся ЯВНО, прочие аргументы пробрасываются как есть: мутация
            # обязана снимать исключения, а не отставать от сигнатуры (разбор /code-review).
            # Позиционно поданные признаки тоже гасятся: иначе имя и позиция задали бы
            # аргумент дважды и мутант упал бы TypeError'ом вместо наблюдения.
            a, k = a[:1], dict(k)
            k['emergency'] = False; k['done_all'] = False
            return orig(self, *a, **k)
        return orig, patched

    def roll_deadline_fail_open():
        """Неизвестный срок ролла снова отвечает «роллить не пора» — как было до 45-го
        круга, №4: любая ошибка календаря отключает поставочный сторож, сессия идёт дальше
        и может увеличить старую серию, а delivery_risk молчит до месяца поставки."""
        import daily as _DLm4
        orig = _DLm4._roll_deadline_or_stop

        def patched(held, hol, what):
            try:
                return _DLm4.roll_deadline(held, hol)
            except Exception:
                import pandas as _pd4
                return _pd4.Timestamp('2100-01-01')     # «не сегодня и не просрочено»
        return orig, patched, _DLm4

    def units_direction_inverted():
        """Продажа единиц идёт ПОКУПКОЙ (45-й круг, №13): инверсия направления
        удваивает источник вместо выхода из него — денежная граница без мутационного
        контроля."""
        orig = B.IBBroker.sell_units

        def patched(self, instrument, units):
            rec = self.place(instrument, abs(float(units)), self._px_hint(instrument))
            return rec.get('order_id'), float(rec.get('filled') or 0.0)
        return orig, patched

    def executions_empty():
        """Отчёты дня всегда пусты (45-й круг, №13): «заявок не было» разрешает
        повтор поверх уже исполненного — ABORT там, где обязан быть MIXED."""
        orig = B.IBBroker.todays_executions
        return orig, (lambda self: [])

    def sentinel_passes_as_margin():
        """Часовой шлюза «не посчитано» (UNSET_DOUBLE) снова суммируется как маржа —
        как было до рецензии 20.08: одна заявка даёт «запас 0.00x», две — inf, и
        законный переход уходит в ABORT с маржинальным объяснением."""
        import ib_broker as _Bs
        orig = _Bs.UNSET_DOUBLE_MIN
        return orig, float("inf"), _Bs      # ТРОЙНОЙ кортеж: носитель — модуль

    def code_error_dressed_again():
        """Запрет переодевания ошибки кода снят: state.CODE_ERRORS пуст, и широкие
        except в preview/_release_by_measure снова превращают TypeError в доменный
        вердикт — инцидент 19.08 в чистом виде."""
        import state as _STs
        orig = _STs.CODE_ERRORS
        return orig, (), _STs               # ТРОЙНОЙ кортеж: носитель — модуль state

    def preview_why_empty():
        """Причина отказа предпросмотра снова не называется (дефект №14 до правки):
        «маржа цели не проходит О-3-Е» подставляется на любой беде, включая молчание
        шлюза, и оператор ищет деньги там, где сломан справочник."""
        orig = B.IBBroker._preview_no

        def patched(self, why):
            self._preview_why = ""
            return False
        return orig, patched

    def diagnose_signs_merged():
        """Маржинальный якорь убран, а капитальный расширен до «ниже порога» — как было до
        44-го круга, №14(в). ЧТО ИМЕННО ЛОМАЕТСЯ, ПОСЛЕ ПОСТРОЧНОГО СОСЕДСТВА (разбор
        /code-review 45-го круга): тревога маршрута Е с пробитым запасом остаётся БЕЗ
        причины вовсе — «не классифицирована», — а на телах, где §8 стоит отдельной
        строкой, получает вместо маржинальной причину политики по капиталу. Прежняя
        формулировка докстроки описывала только второй исход; проверено прогоном, что
        первый и есть основной."""
        import diagnose as _DGm
        orig = _DGm.SIGNS
        # ЯКОРЬ ОПОЗНАЁТСЯ ПО ПРИЧИНЕ, А НЕ ПО ФОРМЕ СИГНАТУРЫ (разбор /code-review 45-го
        # круга). Отбор шёл через str(_s).startswith("О-3-Е"); после того как маржинальный
        # якорь стал ФУНКЦИЕЙ (голая подстрока ловила и здоровый замер), str() у него —
        # "<function ...>", мутация перестала находить своё место и падала бы на assert.
        # Причина в таблице стабильна, она и есть опознавательный признак.
        # ОБЕ ПОЛОВИНЫ ДЕЙСТВУЮТ (разбор /code-review 21.08). Вторая — «капитальный якорь
        # расширен до «ниже порога»» — сравнивала со строкой «ниже порога маршрута», а
        # такого якоря в таблице нет с тех пор, как капитальный признак стал ФУНКЦИЕЙ:
        # мутация обещала два эффекта и давала один, а assert считал только удаление.
        def _shirokiy(body):
            return 'ниже порога' in str(body)
        _mut = [((_shirokiy if _s is _DGm._kapital_nizhe_poroga else _s), _c, _t)
                for _s, _c, _t in orig
                if not str(_c).startswith("запас маржи ниже норматива О-3-Е")]
        assert len(_mut) == len(orig) - 1, "мутация диагноста не нашла своего места"
        assert any(_s is _shirokiy for _s, _, _ in _mut), \
            "капитальный якорь не расширен — половина мутации мертва"
        return orig, _mut, _DGm          # носитель — модуль диагноста, не адаптер

    def venue_prev_cme_for_all():
        """СОРОК ЧЕТВЁРТЫЙ КРУГ, №8: календарь CME снова навязан фондам. После праздника
        CME европейский бар новее «предыдущей сессии CME», closes требует точного
        совпадения — [STALE_BAR], gross падает уже после исполненной пары, переход в MIXED."""
        import feed as _FDm8, daily as _DLm8
        orig = B.IBBroker._venue_prev
        return orig, (lambda self, is_fund, today:
                      _FDm8.prev_session(today, holidays=_DLm8.holidays_for(today.year)))

    def pair_clock_wall():
        """СОРОК ЧЕТВЁРТЫЙ КРУГ, №10: часы пары снова настенные и не перезапускаются
        (setdefault). Перевод часов назад снимает предел 15 минут при одной проданной ноге;
        повторный ключ наследует часы прошлой пары и даёт ложный тайм-аут."""
        import time as _tm10
        orig = B.IBBroker.mark_pair

        def patched(self, key):
            _m = getattr(self, '_since', None)
            if _m is None:
                _m = self._since = {}
            _m.setdefault(str(key), _tm10.time())
            return True
        return orig, patched

    return [('продажа единиц идёт покупкой', 'sell_units', units_direction_inverted),
            ('покупка единиц идёт продажей', 'buy_units', buy_direction_inverted),
            ('часы непарной позиции стоят', 'minutes_since', pair_clock_never_advances),
            ('кэш доходности липнет и к живому', '_dref_once', dref_cache_sticky),
            ('беспланный предпросмотр без исключений', 'preview', preview_drops_carveouts),
            ('отчёты дня всегда пусты', 'todays_executions', executions_empty),
            ('предпросмотр без плана судит по единице', 'preview',
             preview_noplan_unit_threshold),
            ('срок ролла неизвестен — «роллить не пора»', '_roll_deadline_or_stop',
             roll_deadline_fail_open),
            ('часовой шлюза идёт в маржу', 'UNSET_DOUBLE_MIN', sentinel_passes_as_margin),
            ('запрет переодевания ошибки кода снят', 'CODE_ERRORS', code_error_dressed_again),
            ('причина отказа предпросмотра не называется', '_preview_no', preview_why_empty),
            ('сигнатуры диагноста слиты', 'SIGNS', diagnose_signs_merged),
            ('ранний выход по отрицательной сумме', 'preview', preview_negative_sum_passes),
            ('TIF снят в замере маржи', 'measure_margin', measure_margin_no_tif),
            ('календарь CME навязан фондам', '_venue_prev', venue_prev_cme_for_all),
            ('часы пары настенные и не перезапускаются', 'mark_pair', pair_clock_wall),
            ('дробная доля усекается до целого', 'place', truncating_place),
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
            ('серия имени не сверяется на границе заявки', 'identity_bad',
             series_unchecked_at_order),
            ('полоса единицы MES считается как у ES', 'unit_ref', unit_ref_mes_x10),
            ('пустой ISIN реестра пропускается', 'verify_isin', isin_empty_ok),
            ('ошибка запроса заявок глотается', 'open_orders', orders_req_swallow),
            ('неизвестность компенсации глотается', 'restore_to', restore_swallows_unknown),
            ('ключ заявки — голый orderId', 'open_orders', bare_order_ids),
            ('NaN-запас О-3-Е принимается', 'margin_cushion', nan_cushion_ok),
            ('inf и отрицательное требование маржи принимаются', 'margin_cushion',
             wild_maint_ok),
            ('отсутствие подтверждения биржи = реальное время', '_quote_ref', mdt_none_live),
            ('личность фонда без независимого ожидания', 'etf_expectation_bad',
             etf_expect_off),
            ('Cancelled считается неисполнением', 'place', cancel_is_failure),
            ('TIF заявки предпросмотра не задаётся', 'preview', preview_tif_unset),
            ('предпросмотр разрешает всё, ничего не спрашивая', 'preview', preview_always_ok),
            ('предпросмотр спрашивает про DAY, а не про форму place()', 'preview',
             preview_wrong_form),
            ('заявка предпросмотра не привязана к счёту', 'preview', preview_unpinned),
            ('дробное количество фьючерса не округляется', 'preview', preview_frac_fut),
            ('аварийный признак разрешает всё', 'preview', preview_emergency_bypass),
            ('единица ноги Б — середина полосы, а не d_fix', 'gross', gross_band_mid),
            ('единица ноги А считается по ES/10, а не по SPY', 'unit_ref', unit_a_from_es),
            ('разгрузка доказывается одними приращениями whatIf', '_release_by_measure',
             release_by_increments)]


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

    def shortcut_by_date():
        """ТРИДЦАТЬ ВТОРОЙ КРУГ, №13: ярлык «состояние уже дописано» снова срабатывает по
        одной ДАТЕ, без сверки позиций. Обрыв посреди внутридневного среза О-3-Е выдаётся
        за завершённую сессию: брокер сокращён, активная книга старая, разрыва не видит
        никто. Патчится в группе НАМЕРЕНИЯ — именно её стенд исполняет этот путь (урок №3
        тридцатого круга: раннер обязан гонять те стенды, где защита наблюдаема)."""
        orig = DL.same_positions
        return orig, (lambda a, b: True), DL, 'same_positions'

    return [('ярлык намерения по одной дате', shortcut_by_date),
            ('принятое намерение не завершает сессию', not_finishing),
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

    def series_name_unchecked():
        """ТРИДЦАТЫЙ КРУГ, №3: имя инструмента снова не сверяется с фактической поставкой —
        согласованно подменённая строка реестра (ESU26 с полями ESZ26) проходит целиком."""
        import contracts as CTm
        orig = CTm.series_mismatch
        return orig, (lambda name, c, row=None: []), CTm, 'series_mismatch'

    def signal_digest_optional():
        """ТРИДЦАТЬ ПЕРВЫЙ КРУГ, №7: сверка digest снова включается ТОЛЬКО при наличии
        непустого сайдкара — удаление файла .sha256 выключает защиту живого ряда."""
        orig = FD.verify_signal_digest
        def patched(p, live_series):
            import hashlib as _h
            from pathlib import Path as _P
            _dp = _P(str(p) + '.sha256')
            if not _dp.exists():
                return
            _w = _dp.read_text(encoding='utf-8').strip()
            if _w and _w != _h.sha256(_P(p).read_bytes()).hexdigest():
                raise FD.FeedError('ряд не совпадает с digest')
        return orig, patched, 'verify_signal_digest'

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
        # СИГНАТУРА ДОГОНЯЕТ БОЕВУЮ И ПО min_prev (разбор /code-review 45-го круга): полоса
        # единицы фонда зовёт closes(min_prev=...), и мутант падал TypeError'ом — то есть
        # снова объявлялся «пойманным» по поломке стенда, а не по принятию устаревшего бара.
        def patched(ib, contract, today, expected_prev=None, min_prev=None):
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
                # ТОТ ЖЕ КЛАСС: запасным путём стоял БОЕВОЙ ~/.addfut/signals_live.csv,
                # то есть исход мутации зависел от живого состояния машины. Берём путь
                # стенда; нет его — поднимаем исходный отказ, а не читаем чужое.
                _envp = _o.environ.get('ADDFUT_SIGNALS')
                pp = _P(path) if path else (_P(_envp) if _envp else None)
                if pp is None:
                    raise
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
            want = ('CSPX', 'CBU0') if route == 'E' else FD.CT.FUT_ROOTS
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

    return [('серия имени не сверяется с поставкой', series_name_unchecked),
            ('множители биржи не сверяются с моделью', no_mult_check),
            ('цена ES без приведения к единице', raw_es_price),
            ('состояние ноги через bool()', loose_bool),
            ('даты баров не проверяются', no_date_check),
            ('ориентиры без точной сессии', refs_loose_age),
            ('нет строки месяца — берётся последняя', fallback_last_row),
            ('digest живого ряда не обязателен', signal_digest_optional)]


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

    import daily as DLm

    def o3e_cut_silent():
        """ТРИДЦАТЬ ПЕРВЫЙ КРУГ, №2 + ТРИДЦАТЬ ПЯТЫЙ, №3: тревога перестаёт быть
        долговечной ДО состояния — колбэк не передаётся, и файл создаёт только внешний путь
        уже ПОСЛЕ сохранения книги и записи итога §7. Прежняя редакция мутации патчила
        session.post_o3e_alarm и потому не менялa НИЧЕГО наблюдаемого: внешний код всё
        равно создавал файл к концу прогона, и мутация честно осталась НЕ ПОЙМАНОЙ."""
        orig = DLm.run_session

        def patched(*a, **k):
            k.pop('o3e_alarm_fn', None)          # колбэк не доходит до сессии
            return orig(*a, **k)
        return orig, patched, DLm, 'run_session'

    def o3e_pre_cut_silent():
        """ТРИДЦАТЬ ВТОРОЙ КРУГ, №2: предторговый срез step_e снова не выставляет признак
        среза — тревоги не будет, причина не разберётся, полоса вернёт книгу к 2x."""
        orig = DLm.step_e
        def patched(*a, **k):
            d = orig(*a, **k)
            d.o3e_cut = None
            return d
        return orig, patched, DLm, 'step_e'

    def pair_margin_off():
        """ТРИДЦАТЬ ШЕСТОЙ КРУГ, №12: запас времени перед ПЕРВОЙ ногой среза снят — первая
        продажа может исполниться у самого края окна, а вторая уже упрётся в WindowClosed
        (непарная дельта порядка половины NLV). Мутация бьёт в сами ворота."""
        orig = DLm._window_gate
        return orig, (lambda deadline, what='', margin_min=False:
                      orig(deadline, what=what, margin_min=False)), DLm, '_window_gate'

    def decision_stale_after_cut():
        """ТРИДЦАТЬ ШЕСТОЙ КРУГ, №12: Decision после среза снова описывает ДОсрезную книгу —
        §7 получает старое плечо, заём считается по уже проданной позиции, а операторский
        лог скрывает крупнейшие исполнения дня.

        МУТИРУЕТСЯ ВЕСЬ ОБЪЯВЛЕННЫЙ НАБОР (тридцать седьмой круг, №18). Прежде подменялась
        одна лишь exposure, а утверждение сверяло её ТОЖДЕСТВОМ и потому не наблюдало
        ничего; про daily_costs и cushion парной мутации не было вовсе, хотя именно они
        объявлены исправленными."""
        orig = DLm.run_session

        def patched(*a, **k):
            dec, orders, diff = orig(*a, **k)
            _cut = getattr(dec, 'o3e_cut', None)
            if _cut:
                _c0, n0e, n0b, _ne, _nb = _cut
                _pe = getattr(a[1], 'px_eq_prev', 0.0) if len(a) > 1 else 0.0
                _pb = getattr(a[1], 'px_bd_prev', 0.0) if len(a) > 1 else 0.0
                dec.exposure = {'А': n0e * _pe, 'Б': n0b * _pb}   # книга ДО среза
                _P0 = n0e * _pe + n0b * _pb
                _cap = float(getattr(dec, 'capital_after_costs', 0.0) or 0.0)
                if getattr(dec, 'daily_costs', None):
                    dec.daily_costs = DLm.costs_e(_P0, _cap, n0e, _pe)   # расходы ДО среза
                if _c0 is not None:
                    dec.cushion = float(_c0)                             # запас ДО среза
            return dec, orders, diff
        return orig, patched, DLm, 'run_session'

    def o3e_delayed_lost():
        """ТРИДЦАТЬ ЧЕТВЁРТЫЙ КРУГ, №13: строки среза пишутся, но признак ЗАДЕРЖАННОГО
        ориентира теряется (delayed_out не передаётся). В сессии без первоначального
        ребаланса строки аварийного среза с данными типа 3 будут признаны полноценным
        измерением, и пятнадцать минут движения рынка попадут в «издержки» §7."""
        orig = DLm.o3e_journal

        def patched(journal_path, dec, nav, cut_orders, cut_placed):
            rows, _ = orig(journal_path, dec, nav, cut_orders, cut_placed)
            return rows, []
        return orig, patched, DLm, 'o3e_journal'

    def o3e_journal_off():
        """ТРИДЦАТЬ ПЕРВЫЙ КРУГ, №11: исполнения аварийного среза снова не попадают в §7 —
        крупнейший оборот сессии исключён из выборки издержек, а дата считается полной."""
        # МУТАЦИЯ ЛОМАЕТ ПРИЗНАК, А НЕ СИГНАТУРУ (тридцать четвёртый круг, №13): прежняя
        # редакция возвращала [] вместо пары (rows, delayed) и ловилась ошибкой распаковки,
        # то есть доказывала совместимость вызова, а не запись строк среза в §7.
        orig = DLm.o3e_journal
        return orig, (lambda journal_path, dec, nav, cut_orders, cut_placed: ([], [])), \
            DLm, 'o3e_journal'

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
        только при известно-низком запасе, неизвестный принимается молча.

        МУТАЦИЯ ПЕРЕНАЦЕЛЕНА (тридцать седьмой круг, разбор непойманных). Она била в
        SS.post_o3e_alarm — ВНЕШНЮЮ функцию, а сама защита с 33-35 кругов переехала ВНУТРЬ
        сессии (o3e_alarm_fn пишет файл ДО сохранения состояния, а внешняя ветка при уже
        написанном файле выходит рано). Подмена внешнего условия перестала что-либо менять,
        и полный прогон честно показал «НЕ ПОЙМАНА»: защита была наблюдаема не там, где
        живёт. Бьём в обе точки сразу — иначе мутация снова окажется пустой."""
        import daily as DLm
        orig = SS._alarm_o3e
        _ext = SS.post_o3e_alarm

        def _patched(day, reason, src, done=True):
            if 'НЕИЗВЕСТЕН' in str(reason) or 'неизвест' in str(reason).lower():
                return                                   # неизвестный запас глотается
            return orig(day, reason, src, done)

        SS.post_o3e_alarm = (lambda pc, ba, cut=None:
                             pc is not None and pc < DLm.O3E_MIN)

        def _restore(_o=orig, _e=_ext):
            SS.post_o3e_alarm = _e
            return _o
        return _restore(), _patched, SS, '_alarm_o3e'

    def post_o3e_removed():
        """Пост-трейд проверка О-3-Е удалена целиком (как было до восемнадцатого круга,
        №1): запас после исполнений не смотрит никто. Перенацелена вместе с предыдущей:
        внешняя функция больше не единственная точка решения."""
        orig = SS._alarm_o3e
        _ext = SS.post_o3e_alarm
        SS.post_o3e_alarm = (lambda pc, ba, cut=None: False)

        def _noop(day, reason, src, done=True):
            return

        def _restore(_o=orig, _e=_ext):
            SS.post_o3e_alarm = _e
            return _o
        return _restore(), _noop, SS, '_alarm_o3e'

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
        # МОДЕЛИРУЕМ ДЕФЕКТ, НЕ ТРОГАЯ МАШИННОЕ СОСТОЯНИЕ (сорок четвёртый круг, угол «от
        # семьи»). Смысл мутации — «каталог состояния живёт своей жизнью, а не следует за
        # каталогом замка», и для этого достаточно ЛЮБОГО постороннего каталога. Прежде
        # запасным путём стоял expanduser('~/.addfut'), а блок изоляции стенда ADDFUT_DIR
        # именно УДАЛЯЕТ (не подменяет) — значит под мутацией стенды писали журнал, книгу и
        # тревоги в ЖИВОЕ состояние счёта. Правило 5 нарушалось механизмом, а не в теории.
        import tempfile as _tf
        _alien = _tf.mkdtemp(prefix='addfut-mut-statedir-')
        orig = SS.state_dir
        return (orig,
                (lambda: _P(_os.environ.get('ADDFUT_DIR', _alien))),
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

    def handover_itog_only_when_empty():
        """Итог сессии перехода пишется только в ПУСТОЙ журнал — как было до 44-го
        круга, №7: возврат Е->Ф оставляет книгу с сегодняшней датой при итоге старой
        эпохи, и первое же замыкание отвергается якорем WORM (ALARM-backup навсегда,
        ролл заперт)."""
        import sys as _s7
        from pathlib import Path as _P7
        _root7 = str(_P7(__file__).resolve().parent.parent)
        if _root7 not in _s7.path:
            _s7.path.insert(0, _root7)
        import transition as _TR7
        orig = _TR7.open_session_in_journal

        def patched(jp, day, sess_no, from_route, to_route, was_used):
            if was_used:
                return
            return orig(jp, day, sess_no, from_route, to_route, was_used)
        return orig, patched, _TR7, "open_session_in_journal"

    def itog_rule_always_clean():
        """Правило «журнал закрыт итогом ЭТОЙ сессии» ничего не находит — как было до
        44-го круга, №6: обрыв между ST.save и J.append(ИТОГ) читался как штатный повтор
        дня, автопилот ставил traded-*, и недостача всплывала лишь на замыкании, когда
        день уже объявлен отторгованным. Мутируется САМО ПРАВИЛО (одна точка на два
        места — вход и якорь WORM), а не его вызов."""
        import journal as _JJ
        orig = _JJ.session_incomplete
        return orig, (lambda rows, last_session: ''), _JJ, 'session_incomplete'

    def alarm_general_overwrites():
        """Общая тревога снова ЗАТИРАЕТ частную — как было до инцидента 19.08.2026:
        ветка run_close «копия не снята» писала в тот же файл через alarm_write, стирая
        причину отказа снимка и разбор diagnose.py."""
        return _mutate_autopilot('alarm_keep "$ST/ALARM-backup-$day.txt"',
                                 'alarm_write "$ST/ALARM-backup-$day.txt"',
                                 'addfut-mut-sh-', 'общая тревога затирает причину')

    def lock_on_file_again():
        """Замок снова берётся на ФАЙЛ, а не на каталог — как было до 44-го круга, №9:
        подмена addfut-book.lock разводит двух держателей по разным inode, и оба идут
        подавать заявки по одной книге.

        Мутируется ИСТОЧНИК обоих дочерних процессов стенда: держатель и претендент обязаны
        исполнять один и тот же код, иначе «поймана» получилось бы из рассогласования
        стенда (родитель с новым замком, ребёнок со старым), а не из проверяемого свойства.
        Копия state.py — настоящая, с возвращённым дефектом в _lock_fd."""
        import invariants as _Im9
        import shutil as _sh9
        import tempfile as _tf9
        from pathlib import Path as _P9
        _src = _P9(_Im9.LOCK_SRC) / 'state.py'
        _txt = _src.read_text(encoding='utf-8')
        _mut = _txt.replace('    return os.open(str(d), os.O_RDONLY)',
                            '    return os.open(str(d / LOCK_NAME),\n'
                            '                   os.O_RDWR | os.O_CREAT, 0o644)')
        assert _mut != _txt, 'мутация замка не нашла своего места — стенд доказывал бы пустоту'
        _dir = _P9(_tf9.mkdtemp(prefix='addfut-mut-lock-'))
        (_dir / 'state.py').write_text(_mut, encoding='utf-8')
        orig = _Im9.LOCK_SRC
        return orig, str(_dir), _Im9, 'LOCK_SRC'

    def backup_push_always_zero():
        """Выгрузка копий снова всегда возвращает 0 — как было до разбора находки №11:
        последней командой скрипта был echo, счётчик неудач обнулялся каждое замыкание, и
        порог «три отказа подряд = тревога» был недостижим. Механизм существовал на бумаге,
        а в журнале уже лежало 16 пропущенных выгрузок."""
        import invariants as _I
        import tempfile as _tf
        from pathlib import Path as _P
        _orig = _I.BACKUP_PUSH_SH
        _src = _P(_orig).read_text(encoding='utf-8')
        _mark = 'exit "${_rc:-0}"'
        assert _src.count(_mark) == 1, 'мутация выгрузки не нашла своего места'
        _dst = _P(_tf.mkdtemp(prefix='addfut-mut-bp-')) / 'backup_push.sh'
        _dst.write_text(_src.replace(_mark, 'exit 0'), encoding='utf-8')
        return _orig, _dst, _I, 'BACKUP_PUSH_SH'

    def rotation_status_becomes_snapshot_status():
        """Статус уборщика архивов снова становится статусом снимка — как было до разбора
        находки №22: под pipefail отказ ротации читается вызывающим как «копия не снята»,
        замыкание дня не отмечается, и каждый следующий тик повторяет его заново.

        Мутация подменяет условие на заведомо ложное: предупреждение исчезает, а конвейер
        ротации снова оказывается последней исполняемой строкой функции — ровно прежнее
        поведение."""
        return _mutate_autopilot(
            '    if ! ls -1 "$bdir"/addfut-*.tgz 2>/dev/null | sort -r | tail -n +61 | xargs -r rm -f; then',
            '    if false; then',
            'addfut-mut-rot-', 'статус уборщика становится статусом снимка')

    def margin_age_warns_too_late():
        """Предупреждение о возрасте замера снова появляется только ПОСЛЕ предела — как
        было до разбора находки №14: до 23.09 не говорит ничего, а 23.09 выпуск встаёт.
        Заблаговременность и есть вся ценность этой проверки."""
        return _mutate_autopilot(
            '    if left > 10:',
            '    if left >= 0:',
            'addfut-mut-mgage-', 'предупреждение о замере запаздывает')

    def marker_cleanup_skips_late_days():
        """Уборщик отметок снова разбирает дату срезом по последнему «-2» — как было до
        разбора находки №12: КАЖДАЯ отметка с днём 20-29 пропускается, треть входа, и
        уборщик при этом не сообщает ничего."""
        return _mutate_autopilot(
            '            _base=${_mk##*/}; _d=${_base#*-}            # хвост ГГГГ-ММ-ДД из имени',
            '            _d=${_mk##*-2}; _d=2$_d                     # хвост ГГГГ-ММ-ДД из имени',
            'addfut-mut-mark-', 'уборщик отметок теряет дни 20-29')

    def plan_orders_leaks_tmpdir():
        """Оценка плана заявок снова оставляет временный каталог — как было до разбора
        находки №25: боевой предполёт течёт каталогами, /tmp растёт, автопилот однажды
        встаёт на переполнении."""
        import os as _os
        import tempfile as _tf
        import transition as T
        _orig = T.plan_orders

        def _mut(plan, legs, lim=None):
            _d = _tf.mkdtemp(prefix='addfut-plancount-')   # намеренно без уборки
            open(_os.path.join(_d, 'st.json'), 'w').write('{}')
            return _orig(plan, legs, lim)
        return _orig, _mut, T, 'plan_orders'

    def empty_route_is_a_route():
        """Пустой route.txt снова считается маршрутом «» — как было до разбора находки №20:
        отказ «маршрут неизвестен» не срабатывает, книга ищется по пути book-.json, и
        оператору сообщают об утрате КНИГИ вместо утраты МАРШРУТА."""
        import state as ST
        _orig = ST.active_route

        def _mut():
            from pathlib import Path as _P
            rt = _P(ST.lock_dir()) / 'route.txt'
            if rt.exists():
                return rt.read_text(encoding='utf-8').strip()
            return _orig()
        return _orig, _mut, ST, 'active_route'

    def journal_append_not_atomic():
        """Журнал §7 снова дописывается обычным append — как было до разбора находок №9
        и №10: утрата терминатора последней строки затирает row_hash предыдущей и рвёт
        цепочку необратимо, а обрыв записи оставляет усечённую строку."""
        import csv as _csv
        import journal as J

        def _mut(path, row):
            from pathlib import Path as _P
            import fcntl as _fc, os as _os
            path = _P(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            lock = path.with_suffix(path.suffix + '.lock')
            with open(lock, 'w') as lk:
                _fc.flock(lk, _fc.LOCK_EX)
                try:
                    prev = J._last_hash(path)
                    rec = {c: ('' if row.get(c) is None else str(row.get(c)))
                           for c in J.BASE}
                    rec['prev_hash'] = prev
                    rec['row_hash'] = J._digest(prev, rec)
                    try:
                        new = path.stat().st_size == 0
                    except OSError:
                        new = True
                    with open(path, 'a', newline='', encoding='utf-8') as f:
                        w = _csv.DictWriter(f, fieldnames=J.COLS, extrasaction='raise')
                        if new:
                            w.writeheader()
                        w.writerow(rec)
                        f.flush(); _os.fsync(f.fileno())
                    return rec['row_hash']
                finally:
                    _fc.flock(lk, _fc.LOCK_UN)
        return J.append, _mut, J, 'append'

    def j7_gate_silent():
        """Ворота целостности журнала §7 снова молчат — как было на пути «состояние принято
        по намерению» до разбора находки №8: утраченный журнал начинается заново с GENESIS,
        verify отдаёт 1, и подмена истории проходит все дальнейшие проверки."""
        import daily as D
        return D._j7_gate, (lambda *a, **k: None), D, '_j7_gate'

    def provisional_locks_emergency():
        """Сторож незамкнутой книги снова запирает АВАРИЙНЫЙ выход — как было до разбора
        сплошного аудита 27.08: с 08:45 до 02:00 следующего дня выхода из маршрута Е нет,
        то есть всю американскую сессию, когда стресс и случается."""
        import transition as T
        _orig = T._preflight_handover

        def _mut(from_route, to_route, *a, **k):
            k['emergency'] = False           # признак аварии теряется по дороге
            return _orig(from_route, to_route, *a, **k)
        return _orig, _mut, T, '_preflight_handover'

    def registry_exact_name_only():
        """Реестр перехода снова сверяется по ТОЧНОМУ имени — как было до разбора сплошного
        аудита 27.08: живые позиции приходят с серией (ESZ26), реестр направлений держит
        голые корни (ES), и любая живая позиция объявляется неизвестной. Переход заперт в
        обе стороны, включая аварийный выход при маржинальном стрессе."""
        import transition as T
        return T.reg_of, (lambda reg, i: reg.get(i)), T, 'reg_of'

    def registry_root_without_series():
        """Поиск по корню без проверки серии — первая, слишком широкая редакция правки:
        вместе с законным ESZ26 проходит мусорный ESZ26X. Ослабление впускает то, чего не
        впускала сверка по точному имени."""
        import transition as T
        return T.reg_of, (lambda reg, i: reg.get(i) or reg.get(T.fut_root(i))), T, 'reg_of'

    def partial_fill_reported_full():
        """Недобор исполнения снова сообщается как полное: адаптер отдаёт ЗАКАЗАННОЕ
        количество вместо фактического. Книга разойдётся со счётом молча — контур сочтёт
        ногу набранной, тогда как у брокера её половина."""
        import ib_broker as B
        _orig = B.IBBroker._rec

        def _mut(self, tr, instrument, qty, px_order=None):
            rec = _orig(self, tr, instrument, qty, px_order)
            rec['filled'] = float(qty)
            return rec
        return _orig, _mut, B.IBBroker, '_rec'

    def price_ok_by_emptiness():
        """Годность цены снова определяется НЕПУСТОТОЙ, как было до разбора сплошного
        аудита 27.08: значение -1, которым IBKR обозначает отсутствие стороны стакана,
        считается настоящей ценой, и mid выходит вдвое заниженным."""
        import ib_broker as B
        return B._px_ok, (lambda x: (float(x) if x else None)), B, '_px_ok'

    def qty_format_int_only():
        """Количество в отказе снова печатается как целое — как было до разбора сплошного
        аудита 27.08: на дробном количестве (а оно приходит из sell_units/buy_units всегда)
        форматирование падает ValueError, и доменный BrokerError не возникает вовсе."""
        import ib_broker as B
        return B._q, (lambda x: f'{x:+d}'), B, '_q'

    def inactive_cancel_trusted():
        """Снятие заявки Inactive снова принимается за факт брокера — как было до разбора
        сплошного аудита 27.08: ib_insync пишет 'Cancelled' сам, а адаптер возвращает
        «снята, сделок нет». Заявка при этом жива и может исполниться на открытии."""
        import ib_broker as B
        _orig = B.IBBroker.cancel_order
        _src = _orig

        def _mut(self, oid):
            try:
                return _orig(self, oid)
            except B.BrokerError as e:
                if 'была Inactive' in str(e):
                    return dict(terminal=True, cancelled=True, status='Cancelled', filled=0.0)
                raise
        return _src, _mut, B.IBBroker, 'cancel_order'

    def verdict_reads_whole_answer():
        """Вердикт вахты снова читается как «весь ответ начинается с LOW» — как было до
        45-го круга, №1: диагностические строки ib_insync встают перед маркером, разбор
        уходит в `*)`, и предписанный §8 срез в ту же сессию не запускается."""
        return _mutate_autopilot(
            "    printf '%s\\n' \"$1\" | awk 'match($0, /ADDFUT-VERDICT /)",
            "    printf '%s' \"$1\"; : 'awk отключён' # awk 'match($0, /ADDFUT-VERDICT /)",
            'addfut-mut-verd-', 'вердикт читается целиком')

    def env_guard_whitelist_open():
        """Белый список сторожа окружения снова принимает ЛЮБУЮ ADDFUT_* — как было до
        двадцать третьего круга, №22: мимо проходили ADDFUT_REGISTRY (другой набор conId),
        ADDFUT_SIGNALS_FALLBACK_OK (торговля по исследовательскому снимку), ADDFUT_LIVE_OK.
        Каждая меняет целую позицию или снимает временные ворота — молча."""
        return _mutate_autopilot(
            '            *) _bad="$_bad $_v" ;;',
            '            *) : ;;   # мутация: чужие переменные пропускаются',
            'addfut-mut-envwl-', 'белый список сторожа окружения открыт')

    def env_guard_alarm_undated():
        """Тревога сторожа окружения снова БЕЗ ДАТЫ в имени — как было до двадцать третьего
        круга, №7: следующий тик открывал тот же файл на запись, и остановка контура не
        оставляла долговечного следа для разбора."""
        return _mutate_autopilot(
            '        alarm_write "$ST/ALARM-env-$(chicago %F).txt" "посторонние переменные',
            '        alarm_write "$ST/ALARM-env.txt" "посторонние переменные',
            'addfut-mut-envdate-', 'тревога сторожа окружения без даты')

    def env_guard_denies_own_account():
        """Сторож окружения перестаёт различать СВОЁ: ADDFUT_ACCOUNT, который автопилот
        ставит себе сам из account.txt, объявляется посторонним. Автопилот не вошёл бы в
        торговлю НИ РАЗУ — ужесточение, закрывающее законный путь (угол «от
        противоположного знака»)."""
        return _mutate_autopilot(
            '            ADDFUT_ACCOUNT) : ;;                     # ставит сам автопилот из account.txt',
            '            ADDFUT_ACCOUNT) _bad="$_bad $_v" ;;   # мутация: своё объявлено чужим',
            'addfut-mut-envown-', 'сторож окружения не различает своё')

    def env_guard_always_denies():
        """Сторож окружения отказывает ВСЕГДА — ужесточение до абсурда. Мутация заведена
        не ради симметрии: без неё три РАЗРЕШАЮЩИХ исхода стенда не убивались ничем, то
        есть стенд не отличал бы сторожа от заглушки «нельзя никому», а такая заглушка
        останавливает контур целиком и навсегда."""
        return _mutate_autopilot(
            '    [ -z "${ADDFUT_DIR:-}${ADDFUT_LOCK_DIR:-}${ADDFUT_BOOK_PATH:-}${ADDFUT_SIGNALS:-}" ] && return 0',
            '    [ -z "${ADDFUT_DIR:-}${ADDFUT_LOCK_DIR:-}${ADDFUT_BOOK_PATH:-}${ADDFUT_SIGNALS:-}" ] && return 1',
            'addfut-mut-envall-', 'сторож окружения отказывает всем')

    def env_guard_second_watch_silent():
        """Второй сторож (переопределение путей при ПОДКЛЮЧЕНИИ файла) перестаёт оставлять
        след: возвращает отказ молча. Он недостижим в боевом пути — cron отдаёт переменные
        экспортированными, и первый сторож срабатывает раньше, — но охраняет подключаемый
        контекст, то есть все стенды слоя 1. Без этой мутации шестой исход стенда не
        убивался ничем."""
        return _mutate_autopilot(
            '    if [ ! -e "$ST/ALARM-env.txt" ]; then',
            '    if false; then   # мутация: второй сторож молчит',
            'addfut-mut-envsnd-', 'второй сторож окружения молчит')

    def hb_age_falls_back_to_mtime():
        """Возраст сердцебиения снова откатывается на mtime при негодном содержимом — как
        было до 44-го круга, №12: touch или восстановление каталога из копии делают
        зависшую отметку «свежей», сторож занятого замка молчит, контур слеп."""
        return _mutate_autopilot(
            '    if [ "$v" = 0 ]; then\n        echo "НЕЧИТАЕМО"\n        return 1\n    fi',
            '    if [ "$v" = 0 ]; then\n        echo $(( now - $(stat -c %Y "$f" 2>/dev/null || echo 0) ))\n        return 0\n    fi',
            'addfut-mut-hb-', 'возраст откатывается на mtime')

    def empty_clears_foreign_counter():
        """Ветка EMPTY снова обнуляет ЧУЖОЙ файл — исходный дефект правки №6: имени
        o3e-intraday-fail во всём скрипте больше нет, счётчик слепоты не обнуляется, и
        последовательность SKIP,SKIP,EMPTY,SKIP по-прежнему даёт три и останавливает
        контур ровно тем механизмом, ради снятия которого ветка заведена."""
        return _mutate_autopilot(
            'EMPTY\\ *|OK\\ *) rm -f "$ST/o3e-watch-fail-$day"',
            'EMPTY\\ *|OK\\ *) : > "$ST/o3e-intraday-fail-$day"',
            'addfut-mut-empt-', 'EMPTY чистит чужой счётчик')

    def empty_is_blindness_again():
        """Пустая книга Е снова считается слепотой: ветка EMPTY убрана из успешного
        разбора внутридневной вахты."""
        return _mutate_autopilot(
            '            EMPTY\\ *|OK\\ *) rm -f "$ST/o3e-watch-fail-$day"',
            '            OK\\ *) rm -f "$ST/o3e-watch-fail-$day"',
            'addfut-mut-emp2-', 'пустая книга снова слепота')

    def positions_before_verdict():
        """Позиции снова снимаются ДО разбора запаса и в общем try — как было в первой
        редакции правки №6: BrokerError из refresh() выбрасывает уже ИЗМЕРЕННЫЙ LOW,
        вердикт становится SKIP, и §8 срез в ту же сессию снова не запускается."""
        return _mutate_autopilot(
            '    c = _br.margin_cushion()\n    if c is None:',
            '    c = _br.margin_cushion()\n    _pos_early = _br.net_positions() or {}\n    if c is None:',
            'addfut-mut-pos-', 'позиции снимаются до вердикта')

    def gap_tolerance_ignores_min_prev():
        """Плоский допуск снова применяется при заданном min_prev — как было после правки
        №5 45-го круга: полоса единицы фонда детерминированно падает [STALE_BAR] на длинных
        европейских связках (29.12.2026 — разрыв шесть дней при допуске пять), gross()
        рвётся ПОСЛЕ первой исполненной пары, и переход уходит в MIXED."""
        # СУДЬЯ СОВПАДАЕТ С НАБЛЮДАТЕЛЕМ (разбор /code-review 45-го круга). Мутация
        # стояла в наборе СБОРЩИКА, который судит run_feed, а наблюдает правило стенд
        # 'правила45: допуск бара' из RUN_CASES: вердикт был «не поймал НИКТО» при
        # живом и верном стенде. Это тот же класс, что и мутация, пойманная поломкой
        # обвязки: приговор выносит не тот, кто смотрит.
        import feed as _FDg45
        orig = _FDg45._gap_tolerance_applies
        return (orig, (lambda expected_prev, min_prev: expected_prev is None),
                _FDg45, '_gap_tolerance_applies')

    def consume_partial_exact_zero():
        """СУДЬЯ ТОТ ЖЕ, ЧТО И НАБЛЮДАТЕЛЬ: правило наблюдает стенд «правила45: остаток ниже
        допуска не заявка» из RUN_CASES, поэтому и мутация живёт здесь, а не в наборе
        перехода — иначе вердикт «не поймал НИКТО» при верном стенде.

        Вычитание внутрилотового прогресса снова сравнивает с точным нулём — как было до
        разбора /code-review: план [0,1; 0,2; 0,3; 0,4] при полностью исполненном источнике
        оставляет 5,55e-16 юнита, цели маршрута Е от округления освобождены, и whatIf
        получает заявку на пыль: три POSTPONED и ABORT на ЗАКОННО завершённом переходе."""
        import sys as _s4, os as _o4
        _lv4 = _o4.path.join(_o4.path.dirname(_o4.path.abspath(__file__)), '..')
        if _lv4 not in _s4.path:
            _s4.path.insert(0, _lv4)
        import transition as _TR4
        orig = _TR4.consume_partial

        def patched(units, done_units):
            units = float(units)
            take = min(float(done_units or 0.0), units)
            if take > 0:
                units -= take
            return (0.0 if units <= 0 else units), max(0.0, float(done_units or 0.0) - take)
        return orig, patched, _TR4, 'consume_partial'

    def journal_header_unchecked():
        """Заголовок §7 больше не сверяется с COLS — как было до разбора /code-review:
        append дописывает строку под мусорной шапкой и начинает цепочку от GENESIS, а
        read/verify после этого отвергают журнал навсегда."""
        import journal as _Jh
        orig = _Jh._header_ok
        return orig, (lambda fieldnames: fieldnames is not None), _Jh, '_header_ok'

    def o3e_norm_by_unit():
        """Норматив О-3-Е снова читается как «лишь бы не ниже единицы» — как было до 45-го
        круга, №2: книга с ЖИВЫМ запасом 1,20 при нормативе 1,40 получает «да» во ВСЕХ
        ветках сразу (предпросмотр, вахта сессии, пост-трейдовый разбор, срез §8)."""
        import daily as _DLn
        orig = _DLn.o3e_ok
        return orig, (lambda cushion: cushion is not None and float(cushion) >= 1.0), \
            _DLn, 'o3e_ok'

    def o3e_signature_by_substring():
        """Маржинальный якорь диагноста снова голая подстрока «О-3-Е» — как было до разбора
        /code-review: тревога СЛЕПОТЫ вахты, «запас неизвестен» и здоровый замер после
        успешного среза читаются как пробой, и оператору называют противоположную причину."""
        import diagnose as _DGz
        orig = _DGz.SIGNS
        _mut = [((lambda body: 'О-3-Е' in str(body))
                 if _s is _DGz._zapas_nizhe_o3e else _s, _c, _t) for _s, _c, _t in orig]
        assert any(_s is not _o for (_s, _, _), (_o, _, _) in zip(_mut, orig)), \
            'мутация якоря О-3-Е не нашла своего места'
        return orig, _mut, _DGz, 'SIGNS'

    def code_errors_dressed_in_run():
        """Запрет переодевания ошибки кода снят В НАБОРЕ ЗАПУСКА (разбор /code-review 21.08).
        Такая же мутация есть у адаптера, но судит её run_adapter, где нет ни одного из
        новых мест ловли: пропуски в transition._preflight_handover и в daily наблюдают
        стенды RUN_CASES, значит и мутация обязана судиться ими."""
        import state as _STm45
        orig = _STm45.CODE_ERRORS
        return orig, (), _STm45, 'CODE_ERRORS'

    def diagnose_ignores_cause_codes():
        """Диагност снова угадывает причину по прозе, игнорируя код производителя — как
        было до ворот 3: перечень слов отстаёт от текстов, и оператор получает НЕ ТУ
        причину на самом дорогом событии дня (заглавные буквы, приклеенный хвост, чужой
        якорь «исход заявки»)."""
        import diagnose as _DGk
        orig = _DGk._codes
        return orig, (lambda body: []), _DGk, '_codes'

    def worm_account_fail_open():
        """Замер БЕЗ поля account снова заверяется молча — как было до разбора 21.08:
        условие `if _acc and _want` пропускало пару, принадлежность которой не установима,
        тогда как transition._live_margins ровно этот случай считает фатальным."""
        import worm_anchor as _WAa
        orig = _WAa._registry_margins_mismatch

        def patched(reg, mrg):
            _r = orig(reg, mrg)
            return '' if 'не называет счёта' in str(_r) else _r
        return orig, patched, _WAa, '_registry_margins_mismatch'

    def series_required_off():
        """Сторож серий публикации снова выключается — _series_required отвечает «не
        требуется» при любом реестре: книга Ф публикуется с пустыми ser_a/ser_b, ролл
        никогда не наступает, нога идёт в поставку. Точка одна — ворота 1 её и извлекли."""
        # Голый импорт, как у соседней мутации: r33build уже в sys.path со строки 17
        # модуля, а рукописная '..'-форма не совпадала строкой с канонической и плодила
        # дубликаты пути (разбор /code-review 22.08).
        import transition as _TR5
        orig = _TR5._series_required
        return orig, (lambda reg_keys: False), _TR5, '_series_required'

    def leg_b_never_live():
        """«Нога Б никогда не жива»: откат к d_fix СТАРОЙ книги Ф разрешается ДАЖЕ при
        живом ZN, и трежерис месяцами оцениваются по дюрации на момент ухода в Е — вклад
        ноги Б и плечо закрытия неверны (22-й круг, №9)."""
        import transition as _TRl
        orig = _TRl.нога_б_жива
        return orig, (lambda positions: False), _TRl, 'нога_б_жива'

    def leg_b_always_live():
        """Обратный конец: «нога Б жива всегда» — законный откат к старой книге запрещён
        даже при пустой ноге Б, и передача книги отказывает на исправном счёте."""
        import transition as _TRl2
        orig = _TRl2.нога_б_жива
        return orig, (lambda positions: True), _TRl2, 'нога_б_жива'

    def fut_name_always_true():
        """Опознание фьючерсного имени тождественно ИСТИННО. Обратный конец пары к
        fut_name_always_false: законная ДРОБНАЯ доля фонда (маршрут Е торгует дробями)
        объявляется дробным фьючерсом, и COMPLETE запрещается на исправном счёте.
        Патчится contracts.is_fut_name — единственный источник правила (седьмой прогон,
        №5): прежде правило жило внутри _execute_locked и обе константы оставляли ВСЮ
        батарею зелёной."""
        import contracts as _CTt
        orig = _CTt.is_fut_name
        return orig, (lambda instrument: True), _CTt, 'is_fut_name'

    def fut_name_always_false():
        """Опознание фьючерсного имени тождественно ЛОЖНО: дробный остаток фьючерса от
        частичного фила проходит финальную сверку и уходит в книгу, где нога считается
        ЦЕЛЫМИ контрактами (дефект двадцать девятого круга, №10), а голое имя в позициях
        перестаёт ловиться сторожем публикации hand_over_book."""
        import contracts as _CTf
        orig = _CTf.is_fut_name
        return orig, (lambda instrument: False), _CTf, 'is_fut_name'

    def registry_read_swallows_code_errors():
        """ЧИТАТЕЛЬ реестра снова глотает ошибки кода — TypeError становится «реестр
        нечитаем» вместо трассировки. Патчится _read_registry_keys — ОБЩАЯ точка предполёта
        и публикации: прежняя мутация била в обёртку публикации, которую предполёт не
        зовёт, и заявление «копия наблюдаема мутацией» было ложным (шестой прогон)."""
        import transition as _TR6
        orig = _TR6._read_registry_keys

        def patched():
            try:
                return orig()
            except Exception as _e:
                return None, _e
        return orig, patched, _TR6, '_read_registry_keys'

    def book_lock_ignores_book():
        """Замок книги снова берётся на каталог состояния независимо от того, где книга —
        как было до 45-го круга, №8: при ручном ADDFUT_BOOK_PATH торговля и переходный
        исполнитель держат РАЗНЫЕ flock над одним файлом."""
        import state as _STm45
        orig = _STm45.book_lock_dir
        return orig, (lambda path=None: _STm45.lock_dir()), _STm45, 'book_lock_dir'

    def journal_append_own_reader():
        """append снова берёт предыдущий хэш СВОИМ незащищённым читателем — как было до
        разбора /code-review: на файле с мусорной шапкой read() отказывает, а append
        дописывает строку под этой шапкой и начинает цепочку заново от GENESIS."""
        import csv as _csvm
        import journal as _Jm45
        orig = _Jm45._last_hash

        def patched(path):
            if not path.exists():
                return _Jm45.GENESIS
            rows = list(_csvm.DictReader(open(path, newline='', encoding='utf-8')))
            return rows[-1]['row_hash'] if rows else _Jm45.GENESIS
        return orig, patched, _Jm45, '_last_hash'

    def diagnose_reads_whole_body():
        """Соседство §8 снова проверяется по ВСЕМУ телу тревоги — как было до разбора
        /code-review: одна здоровая строка про запас О-3-Е отменяет верный диагноз отказа
        по капиталу, и оператор получает зеркально противоположную причину."""
        import diagnose as _DGm45
        # МУТИРУЕТСЯ ТАБЛИЦА, А НЕ ИМЯ ФУНКЦИИ (разбор /code-review 45-го круга). SIGNS
        # держит сам ОБЪЕКТ предиката, захваченный при импорте: подмена
        # diagnose._kapital_nizhe_poroga меняла имя, но не то, что реально вызывается, и
        # мутация била в пустоту при живом стенде. Место ловли — таблица.
        def _whole_body(body):
            b = str(body)
            if '§8' not in b:
                return False
            if 'О-3-Е' in b or 'запас' in b:
                return False
            return ('NLV' in b) or ('порог' in b)
        orig = _DGm45.SIGNS
        _mut = [((_whole_body if _s is _DGm45._kapital_nizhe_poroga else _s), _c, _t)
                for _s, _c, _t in orig]
        assert any(_s is _whole_body for _s, _, _ in _mut), \
            'мутация диагноста не нашла своего места в таблице'
        return orig, _mut, _DGm45, 'SIGNS'

    def worm_pair_always_consistent():
        """Сверка поколений реестра и замера всегда молчит — как было до 45-го круга, №10:
        якорь заверяет несовместимую пару, день объявляется закрытым, а расхождение
        всплывает позже, возможно при срочном переходе."""
        import worm_anchor as _WAm45
        orig = _WAm45._registry_margins_mismatch
        return orig, (lambda reg, mrg: ''), _WAm45, '_registry_margins_mismatch'

    def daily_forgets_code_errors():
        """Слой daily снова не знает про CODE_ERRORS — как было до разбора /code-review:
        опечатка в календаре приходит к О-5 доменным «поставочный риск неизвестен», и
        разбор начинается с календаря вместо трассировки."""
        import daily as _DLm45
        orig = _DLm45._code_errors
        return orig, (lambda: ()), _DLm45, '_code_errors'

    def worm_ever_attested_blind():
        """История якорей ничего не помнит — как было до 44-го круга, №13: удалённый замер
        маржи (и пин счёта) считались штатным отсутствием, и якорь заверял УТРАТУ как
        молодость контура. Мутируется признак, а не его применение: точка одна на тело
        якоря и на опись архива."""
        import worm_anchor as WA
        orig = WA._ever_attested
        return orig, (lambda: set()), WA, '_ever_attested'

    def worm_bdir_not_normalized():
        """Каталог копий НЕ приводится к Path — как было до инцидента 19.08.2026 (§12).
        Боевой вызов `worm_anchor.py --snap ДЕНЬ КАТАЛОГ` отдаёт строку из argv, `bdir / имя`
        внутри anchors_without_archive падает TypeError, внешний except объявляет проверку
        якорей невыполненной — и снимок отказывает на КАЖДОМ замыкании, начиная с первого
        якоря нового формата. Мутация одна на все три места приведения: точка нормализации
        одна ровно затем, чтобы её отсутствие было наблюдаемо целиком."""
        import worm_anchor as WA
        orig = WA._as_path
        return orig, (lambda p: p), WA, '_as_path'

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

    def o3e_cut_off():
        """ТРИДЦАТЫЙ КРУГ, №1: сокращение О-3-Е после исполнений не режет книгу — целью
        объявляется то, что уже есть. Книга ~2x уходит в ночь при запасе ниже 1,40."""
        orig = DL.o3e_reduce
        return (orig,
                (lambda capital, m, p_e, p_b, n_eq, n_bd, n0_eq, n0_bd, share:
                 (n_eq, n_bd, float(capital))),
                DL, 'o3e_reduce')

    def rejected_archive_stays():
        """ДВАДЦАТЫЙ КРУГ, №22: отвергнутый архив остаётся под рабочим именем, и им можно
        восстановиться."""
        import worm_anchor as WA
        return WA.reject_archive, (lambda dst: ''), WA, 'reject_archive'

    def rollgap_total_off():
        """СОРОК ЧЕТВЁРТЫЙ КРУГ, №5: доказуемо отложенный ролл снова сохраняет книгу с новой
        датой и новым номером сессии, НЕ записав итог. Автопилот по дате книги ставит
        traded-* и разрешает замыкание, а якорь WORM требует итог именно этой сессии —
        постоянный ALARM-backup, closed-* не ставится, следующий ролл блокируется."""
        import daily as _DLg
        orig = _DLg.write_rollgap_total
        return orig, (lambda *a, **k: None), _DLg, 'write_rollgap_total'

    return [('пин торгового счёта не требуется', pin_not_required),
            ('ворота торгового окна отключены', window_gate_off),
            ('сокращение О-3-Е не режет книгу', o3e_cut_off),
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
            ('состоявшийся срез не поднимает тревогу', o3e_cut_silent),
            ('предторговый срез не оставляет следа', o3e_pre_cut_silent),
            ('исполнения среза не идут в журнал §7', o3e_journal_off),
            ('запас времени на пару ног не требуется', pair_margin_off),
            ('Decision после среза описывает досрезную книгу', decision_stale_after_cut),
            ('признак задержанного ориентира среза теряется', o3e_delayed_lost),
            ('наблюдение пишет строки §7', dry_writes_journal),
            ('каталог тревог живёт своей жизнью', statedir_own_home),
            ('отложенный ролл не пишет итог сессии', rollgap_total_off),
            ('нет файла — «ФАЙЛА НЕТ» при успехе', worm_missing_ok),
            ('HEAD проверяется по имени', worm_git_name_only),
            ('каталог копий не приводится к Path', worm_bdir_not_normalized),
            ('история якорей ничего не помнит', worm_ever_attested_blind),
            ('возраст сердцебиения откатывается на mtime', hb_age_falls_back_to_mtime),
            ('выгрузка копий всегда возвращает 0', backup_push_always_zero),
            ('статус уборщика становится статусом снимка', rotation_status_becomes_snapshot_status),
            ('предупреждение о замере запаздывает', margin_age_warns_too_late),
            ('уборщик отметок теряет дни 20-29', marker_cleanup_skips_late_days),
            ('оценка плана заявок течёт каталогами', plan_orders_leaks_tmpdir),
            ('пустой route.txt считается маршрутом', empty_route_is_a_route),
            ('журнал §7 дописывается неатомарно', journal_append_not_atomic),
            ('ворота журнала §7 молчат', j7_gate_silent),
            ('незамкнутая книга запирает аварию', provisional_locks_emergency),
            ('реестр перехода сверяется по точному имени', registry_exact_name_only),
            ('поиск по корню без проверки серии', registry_root_without_series),
            ('недобор исполнения выдаётся за полное', partial_fill_reported_full),
            ('годность цены определяется непустотой', price_ok_by_emptiness),
            ('количество в отказе печатается как целое', qty_format_int_only),
            ('снятие заявки Inactive принято за факт', inactive_cancel_trusted),
            ('вердикт вахты читается целиком', verdict_reads_whole_answer),
            ('белый список сторожа окружения открыт', env_guard_whitelist_open),
            ('тревога сторожа окружения без даты', env_guard_alarm_undated),
            ('сторож окружения не различает своё', env_guard_denies_own_account),
            ('сторож окружения отказывает всем', env_guard_always_denies),
            ('второй сторож окружения молчит', env_guard_second_watch_silent),
            ('ветка EMPTY чистит чужой счётчик', empty_clears_foreign_counter),
            ('пустая книга Е снова слепота', empty_is_blindness_again),
            ('позиции снимаются до вердикта', positions_before_verdict),
            ('плоский допуск игнорирует min_prev', gap_tolerance_ignores_min_prev),
            ('остаток лота сравнивается с точным нулём', consume_partial_exact_zero),
            ('сторож серий публикации выключен', series_required_off),
            ('чтение реестра глотает ошибки кода', registry_read_swallows_code_errors),
            ('нога Б никогда не жива', leg_b_never_live),
            ('нога Б жива всегда', leg_b_always_live),
            ('фьючерсным считается любое имя', fut_name_always_true),
            ('фьючерсным не считается ничто', fut_name_always_false),
            ('диагност игнорирует коды причин', diagnose_ignores_cause_codes),
            ('замер без счёта заверяется молча', worm_account_fail_open),
            ('заголовок журнала не сверяется', journal_header_unchecked),
            ('норматив О-3-Е читается как единица', o3e_norm_by_unit),
            ('якорь О-3-Е снова голая подстрока', o3e_signature_by_substring),
            ('запрет переодевания снят (набор запуска)', code_errors_dressed_in_run),
            ('замок книги не зависит от книги', book_lock_ignores_book),
            ('append журнала со своим читателем', journal_append_own_reader),
            ('диагност читает тело целиком', diagnose_reads_whole_body),
            ('пара реестр/замер всегда согласна', worm_pair_always_consistent),
            ('слой daily не знает про CODE_ERRORS', daily_forgets_code_errors),
            ('замок берётся на файл, а не на каталог', lock_on_file_again),
            ('правило итога сессии ничего не находит', itog_rule_always_clean),
            ('итог перехода только в пустой журнал', handover_itog_only_when_empty),
            ('общая тревога затирает причину', alarm_general_overwrites),
            ('маршрут игнорируется', force_route_f)]


# ВОРОТА 2 (правило 8в): «СТЕНД, КОТОРОГО НЕ УБИВАЕТ НИ ОДНА МУТАЦИЯ, — НЕ СТЕНД».
# Обратная таблица уже была, но ТОЛЬКО для набора INVARIANTS — и потому проверка SAME_API
# про аварийный выход смогла стать неопровержимой незамеченной: её семья в отчёт не
# попадала. Копим убийц по ВСЕМ семьям одним помощником: данные уже вычисляются, их надо
# лишь не выбрасывать.
KILLERS = {}


def _note_killers(family, label, bad):
    """Запомнить, какие утверждения покраснели от мутации label в семье family."""
    _f = KILLERS.setdefault(family, {})
    for _b in (bad or ()):
        # имена приходят и с хвостом «[исключение: …]» — берём собственно утверждение
        _name = str(_b).split(' [')[0]
        _f.setdefault(_name, set()).add(label)
    return bad


# ЧЕСТНОЕ ИЗМЕРЕНИЕ ВОРОТ 2 (правило 8в): ADDFUT_MUT_FULL=1 снимает досрочный выход в
# судействе RUN — тогда таблица «кого убила каждая мутация» полна, а не обрезана первым
# несогласием. Обычный прогон остаётся быстрым: вердикт «поймана» от полноты не зависит.
import os as _os_mf

# РЕЖИМ ПРОГОНА — ОДНОЙ ТОЧКОЙ (пятый прогон /code-review): флаг читался тремя выражениями
# в двух полярностях, и правка одного рассинхронизировала бы подпись таблицы с фактом.
def _mut_full():
    return _os_mf.environ.get('ADDFUT_MUT_FULL') == '1'

def _mutate_autopilot(was, now, prefix, что):
    """МУТАЦИЯ БОЕВОГО ШЕЛЛА — ОДНИМ ПОМОЩНИКОМ (разбор /code-review 45-го круга).

    Блок «прочитать autopilot.sh, заменить кусок, положить копию в mkdtemp, перенаправить
    _I.AUTOPILOT_SH» был скопирован трижды, у каждой копии свои одноразовые псевдонимы
    импортов, и протокол копий уже разъехался. Помощник один: у правила «мутируется
    ПРОИЗВОДСТВЕННЫЙ текст, а не его пересказ» одна точка.

    Проверка `was` встречается РОВНО ОДИН РАЗ обязательна: замена, не нашедшая места, дала
    бы мутанта, тождественного оригиналу, и вердикт «поймана» означал бы «нечего ловить».
    """
    import invariants as _Imut
    import tempfile as _tfmut
    from pathlib import Path as _Pmut
    orig = _Imut.AUTOPILOT_SH
    _src = _Pmut(orig).read_text(encoding='utf-8')
    _n = _src.count(was)
    assert _n == 1, f'мутация «{что}» нашла своё место {_n} раз(а) вместо одного'
    _dst = _Pmut(_tfmut.mkdtemp(prefix=prefix)) / 'autopilot.sh'
    _dst.write_text(_src.replace(was, now), encoding='utf-8')
    return orig, _dst, _Imut, 'AUTOPILOT_SH'


def _transition_mutations():
    """Мутации ПЕРЕХОДНОГО ИСПОЛНИТЕЛЯ. Переход — единственное место, где книга существует
    разорванной, и §8б ограничивает разрыв одним процентом капитала. Ломается ровно то, что
    этот разрыв удерживает."""
    def pv_remainder_ignores_partial():
        """Остаток предпросмотра снова не вычитает внутрилотовый прогресс — как было до
        45-го круга, №3: частично исполненный переход просматривается как покупка ПОЛНОЙ
        цели поверх уже купленной части, отсюда ложные POSTPONED и MIXED на третьем.
        Мутации у этого правила не было вовсе, хотя комментарий обещал точку мутации."""
        import sys as _s3, os as _o3
        _lv3 = _o3.path.join(_o3.path.dirname(_o3.path.abspath(__file__)), '..')
        if _lv3 not in _s3.path:
            _s3.path.insert(0, _lv3)
        import transition as _TR3
        orig = _TR3.pv_remainder
        return orig, (lambda plan, done, partial=None: orig(plan, done)), _TR3, 'pv_remainder'

    def orders_counted_locally():
        """Дневная квота снова считается только по файлу прогресса — как было до
        44-го круга, №11: заявки утреннего ребаланса, ролла и предыдущего перехода
        того же дня невидимы, продажа проходит как локальная №390, а парная покупка
        отвергается счётом как №391 — уже ПОСЛЕ необратимой продажи."""
        import sys as _s11
        from pathlib import Path as _P11
        _root11 = str(_P11(__file__).resolve().parent.parent)
        if _root11 not in _s11.path:
            _s11.path.insert(0, _root11)
        import transition as _TR11
        orig = _TR11._orders_used_today
        return (orig, (lambda st: len(st.get("order_ids") or [])),
                _TR11, "_orders_used_today")

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

    def gen_map_subset():
        """ТРИДЦАТЬ ЧЕТВЁРТЫЙ КРУГ, №14: полнота карты поколения ослаблена до проверки
        только перечисленных ключей — серию с исправленным con_id снова можно убрать из
        con_ids, и старый замер ноги Б пройдёт ворота."""
        import transition as _Tg
        orig = _Tg._live_margins
        def patched():
            import json as _j, os as _o, csv as _c
            from pathlib import Path as _P
            # СТЕНД НЕ ТРОГАЕТ МАШИННОЕ СОСТОЯНИЕ (правило 5). Умолчание вело в БОЕВОЙ
            # live/margins_live.json: мутация переписала бы действующий замер маржи одной
            # строкой JSON. Не выстрелило только потому, что все стенды ставят ADDFUT_MARGINS
            # на временный путь, — но защита обязана быть механизмом, а не совпадением.
            _mp = _o.environ.get('ADDFUT_MARGINS')
            if not _mp:
                raise RuntimeError('мутация замера требует ADDFUT_MARGINS на временном пути: '
                                   'писать в боевой margins_live.json запрещено (правило 5)')
            _p = _P(_mp)
            _raw = _j.loads(_p.read_text(encoding='utf-8'))
            _meta = _raw.get('_meta') or {}
            _cids = dict(_meta.get('con_ids') or {})
            _meta['con_ids'] = {k: v for k, v in _cids.items()}
            _entries = {k: v for k, v in _raw.items() if k != '_meta'}
            _meta['con_ids'] = {k: _cids.get(k, '') for k in _cids}   # только свои ключи
            _raw['_meta'] = dict(_meta, con_ids={k: _cids[k] for k in _cids})
            # ослабление: дополняем карту недостающими ключами их же реестровыми значениями
            try:
                _rp = _o.environ.get('ADDFUT_REGISTRY')
                with open(_rp, encoding='utf-8') as _f:
                    _reg = {r['instrument']: str(r['con_id']) for r in _c.DictReader(_f)}
                for _k in _entries:
                    _raw['_meta']['con_ids'].setdefault(_k, _reg.get(_k, ''))
            except Exception:
                pass
            _p.write_text(_j.dumps(_raw), encoding='utf-8')
            return orig()
        return orig, patched, _Tg, '_live_margins'

    def attempt_trace_off():
        """ТРИДЦАТЬ ШЕСТОЙ КРУГ, №12: отметка о ПОПЫТКЕ подачи не пишется, и потерянное
        подтверждение снова выглядит доказанным чистым ABORT (дефект 35-го круга, №1).
        Мутация бьёт в саму запись, а не в соседние признаки."""
        import transition as _Ta
        orig = _Ta._run_lots

        def patched(broker, plan, st, state_path, *a, **k):
            class _NoAttempt(dict):
                def __setitem__(self, key, value):
                    if key == 'attempted':
                        return
                    dict.__setitem__(self, key, value)
            _st2 = _NoAttempt(st)
            try:
                return orig(broker, plan, _st2, state_path, *a, **k)
            finally:
                for _k, _v in _st2.items():
                    if _k != 'attempted':
                        st[_k] = _v
        return orig, patched, _Ta, '_run_lots'

    def mr_digest_off():
        """ТРИДЦАТЬ ПЕРВЫЙ КРУГ, №13: содержимое нормативного журнала МР снова не
        заверяется — пин сторожит только личность файла, и валидная правка «на месте»
        (дописанный OWNER_APPROVE) разрешает перевод счёта в другой маршрут."""
        import mr_engine as _Mm
        orig = _Mm._verify_journal_digest
        return orig, (lambda j, body: None), _Mm, '_verify_journal_digest'

    return [('остаток не вычитает внутрилотовый прогресс', pv_remainder_ignores_partial),
            ('квота дня считается по файлу прогресса', orders_counted_locally),
            ('дата перехода принимается на веру', asof_trusted),
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
            ('завершённые лоты исполняются повторно', replay_done),
            ('содержимое журнала МР не заверяется', mr_digest_off),
            ('след попытки подачи не пишется', attempt_trace_off),
            ('карта поколения сверяется только по своим ключам', gen_map_subset)]


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

    def roll_sub_lot_zero():
        """ТРИДЦАТЬ ТРЕТИЙ КРУГ, №11: ролловая доля меньше одного лота снова округляется в
        ноль — закрывающая сторона уходит в обычные сделки, и _roll_block достраивает её
        половиной номинала открывающей, удваивая результат MES-стороны."""
        import journal as _Jm
        orig = _Jm.rows_from_decision   # раннер §7 патчит модуль journal по имени атрибута

        def patched(dec, nav, orders, fills=None, delayed_out=None):
            rows = orig(dec, nav, orders, fills, delayed_out=delayed_out)
            out = []
            for r in rows:
                # МУТАЦИЯ ОБЯЗАНА МУТИРОВАТЬ (тридцать пятый круг, №10): условие
                # abs(int(float(qty))) == 1 ложно для дробной строки qty=-0.5 (int даёт 0),
                # то есть прежняя «парная мутация» не меняла выход ВООБЩЕ и доказывала
                # только саму себя. Воспроизводим ИМЕННО прежний дефект: дробная ролловая
                # доля округляется вниз до целых лотов, остаток уходит в обычные сделки.
                _q = float(r['qty'])
                if (str(r.get('note', '')).startswith('ролл')
                        and str(r['instrument']).startswith('ES')
                        and abs(_q) != int(abs(_q))):
                    _whole = int(abs(_q)) * (1 if _q > 0 else -1)
                    if _whole:
                        out.append(dict(r, qty=_whole))
                    out.append(dict(r, qty=_q - _whole, note=''))
                    continue
                out.append(r)
            return out
        return orig, patched, 'rows_from_decision'

    return [('пометки исключения не читаются', marks_unread),
            ('дата без ИТОГ считается полной', no_total_ok),
            ('пустая комиссия считается нулевой', commission_zero),
            ('порог двадцати по объединению классов', threshold_union),
            ('счётчик итога не сверяется', counter_unchecked),
            ('пустые цены отбрасываются молча', empty_dropped_silently),
            ('NaN проходит арифметику', nan_passes),
            ('ролловый номинал двусторонний', roll_two_sided),
            ('ролловая доля меньше лота округляется в ноль', roll_sub_lot_zero)]


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
        _note_killers('j7', label, bad)
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
        _note_killers('signal', label, bad)
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
        _note_killers('roll', label, bad)
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
        _note_killers('transition', label, bad)
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
                # ДОСРОЧНЫЙ ВЫХОД (рецензия 20.08): «поймана» доказывает ПЕРВОЕ несогласное
                # утверждение; вердикт «не поймал НИКТО» по-прежнему требует полного
                # прогона — при пустом bad выход не срабатывает по построению.
                _, bad = I.run_run(stop_on_first=not _mut_full())
            finally:
                setattr(holder, attr, orig)
            print(f'{label:<40}{"да" if bad else "НЕТ":>9}  {", ".join(sorted(bad))[:56]}')
            _note_killers('run', label, bad)
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
            _, bad = I.run_run(stop_on_first=not _mut_full())      # то же (рецензия 20.08)
        finally:
            setattr(holder, attr, orig)
        print(f'{label:<40}{"да" if bad else "НЕТ":>9}  {", ".join(sorted(bad))[:56]}')
        _note_killers('run', label, bad)
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
        # НОСИТЕЛЬ МОЖЕТ БЫТЬ НЕ feed (тридцатый круг, №3): сверка имени с поставкой живёт
        # в contracts, и патч по feed не тронул бы её вовсе — мутация била бы в пустоту, а
        # таблица печатала «поймана» за счёт постороннего утверждения. Кортеж из четырёх
        # элементов явно называет модуль, как в других группах.
        _got = make()
        orig, patched, attr = _got[0], _got[1], _got[-1]
        holder = _got[2] if len(_got) == 4 else FD
        setattr(holder, attr, patched)
        try:
            _, bad = I.run_feed()
        finally:
            setattr(holder, attr, orig)
        print(f'{label:<40}{"да" if bad else "НЕТ":>9}  {", ".join(sorted(bad))[:56]}')
        _note_killers('feed', label, bad)
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
        _note_killers('session', label, bad)
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
        # ДВА ВИДА КОРТЕЖА (как в остальных раннерах): часть мутаций называет свой модуль и
        # атрибут явно. Прежде раннер БЕЗУСЛОВНО патчил DL._resume_intent, и мутация другой
        # защиты уходила в пустоту — «НЕ ПОЙМАНА» без объяснения (урок №3 тридцатого круга).
        _got = make()
        if len(_got) == 4:
            orig, patched, _holder, _attr = _got
        else:
            orig, patched = _got
            _holder, _attr = DL, '_resume_intent'
        setattr(_holder, _attr, patched)
        try:
            _, bad = I.run_intent()
        finally:
            setattr(_holder, _attr, orig)
        print(f'{label:<40}{"да" if bad else "НЕТ":>9}  {", ".join(sorted(bad))[:60]}')
        _note_killers('intent', label, bad)
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
        # СИГНАТУРА СОВПАДАЕТ С ВЫЗОВОМ (сороковой круг, №2). Мутации принимали ТРИ
        # аргумента, а step() зовёт repack_excess с четвёртым (roll_a): подмена падала
        # TypeError, и мутация «ловилась» поломкой вызова, а не проверкой смысла капа.
        # Мутация, ловимая падением, ничего не доказывает о защите.
        orig = DLm.repack_excess
        def patched(before, after, unit_is_mes, roll_a=False):
            phys = DLm.orders_from_books(before, after)          # БЕЗ приведения к общей серии
            g_all = DLm.repack_grid(phys, unit_is_mes) if phys else 0
            net_g = abs(after.n_e - before.n_e) * (1 if unit_is_mes else 10)
            return max(0, g_all - net_g), g_all, net_g
        return orig, patched, 'repack_excess'

    def excess_cap_gate_off():
        """ДВАДЦАТЬ ДЕВЯТЫЙ КРУГ, №6: избыток объявлен нулевым — ворота капа снова считают
        по завышенному капиталу, и книга проходит 2,00 с уже известными издержками."""
        orig = DLm.repack_excess
        return orig, (lambda before, after, unit_is_mes, roll_a=False: (0, 0, 0)), 'repack_excess'

    def cut_cost_by_count():
        """ТРИДЦАТЬ ПЕРВЫЙ КРУГ, №4: издержки среза снова считаются по ЧИСЛУ срезанных
        единиц (за каждую списывается ещё одна комиссия), а не по изменению оборота."""
        import sim_v13 as Sm
        orig = DLm.cut_cost_delta
        def patched(n0, plan, final, u_e, u_b, roll_a=False, roll_b=False):
            (_, _), (pe0, pb0), (ne, nb) = n0, plan, final
            return Sm.COST * ((pe0 - ne) * u_e + (pb0 - nb) * u_b)
        return orig, patched, 'cut_cost_delta'

    miss = []
    for label, make in (('избыток перекладки считает оборот ролла', excess_counts_roll),
                        ('ворота капа без вычета избытка перекладки', excess_cap_gate_off),
                        ('издержки среза по числу единиц, а не по обороту', cut_cost_by_count)):
        orig, patched, attr = make()
        setattr(DLm, attr, patched)
        try:
            _, bad = I.run_pack()
        finally:
            setattr(DLm, attr, orig)
        print(f'{label:<40}{"да" if bad else "НЕТ":>9}  {", ".join(sorted(bad))[:56]}')
        _note_killers('pack', label, bad)
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
        _note_killers('refusal', label, bad)
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
        # КОНТРАКТ КОРТЕЖА ПРОВЕРЯЕТСЯ, А НЕ ПОДРАЗУМЕВАЕТСЯ (20.08.2026, ТРЕТИЙ случай за
        # сутки). Раннер молча трактовал ЛЮБУЮ длину, кроме 3, как «носитель — класс
        # адаптера»: мой четырёхэлементный возврат означал setattr на IBBroker вместо модуля,
        # мутация применялась В ПУСТОТУ, стенд оставался зелёным, и прогон честно писал «НЕ
        # ПОЙМАНА» — а я трижды искал причину в стенде. Молчаливое умолчание на неверной
        # форме — это ложное доказательство ровно того класса, что круг и ищет.
        if len(got) not in (2, 3):
            raise AssertionError(
                f'мутация {label!r}: кортеж длины {len(got)} — раннер понимает только '
                f'(orig, patched) и (orig, patched, holder). Четвёртый элемент (имя '
                f'атрибута) здесь НЕ читается: имя берётся из списка мутаций.')
        orig, patched = got[0], got[1]
        holder = got[2] if len(got) == 3 else B.IBBroker
        setattr(holder, attr, patched)
        try:
            _, bad = I.run_adapter()
        finally:
            setattr(holder, attr, orig)
        killers = ', '.join(sorted(bad))[:70]
        print(f'{label:<40}{"да" if bad else "НЕТ":>9}  {killers}')
        _note_killers('adapter', label, bad)
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
    # ВОРОТА 2: ОБРАТНЫЙ ВОПРОС ПО ОСТАЛЬНЫМ СЕМЬЯМ (правило 8в). «Поймана ли каждая
    # мутация» спрашивалось всегда; «убито ли каждое утверждение» — только у INVARIANTS.
    # Утверждение без единого убийцы либо тождество, либо замолчало: 22.08 так молча
    # ослепла проверка SAME_API про аварийный выход, и её слепота скрыла P0 в воротах
    # маржи. Досрочный выход (stop_on_first) может маскировать часть убийц, поэтому пока
    # это ПЕРЕЧЕНЬ КАНДИДАТОВ, а не отказ выпуска: цифру надо сначала измерить.
    # РЕЖИМ ТАБЛИЦЫ — В ЕЁ ЗАГОЛОВКЕ (разбор /code-review 22.08): обрезанная досрочным
    # выходом таблица завышала список «без убийцы» (28 против честных 10), и отличить её
    # от полной можно было только по памяти о переменной окружения. Потребитель обязан
    # видеть режим там же, где число.
    # Режим — ПО СЕМЬЕ (пятый прогон): досрочный выход есть только у run; таблица
    # адаптера полна всегда, и общий ярлык «ОБРЕЗАН» ложно дисконтировал её точную цифру.
    # Ярлык — ТРЕТЬИМ ЭЛЕМЕНТОМ кортежа семьи (шестой прогон: словарь, ключуемый именем
    # из соседнего цикла, давал бы KeyError на новой семье ПОСЛЕ многоминутного прогона).
    # ВСЕ СЕМЬИ, А НЕ ДВЕ (седьмой прогон /code-review, №12). Таблица печатала run и
    # adapter, а _note_killers записывает ОДИННАДЦАТЬ семей: 114 утверждений из 253
    # собирали данные об убийцах и выбрасывали их, притом что отчёт читался как «ворота
    # 8в-2 закрыты». Утверждение семьи transition или feed, ставшее тавтологией, оставалось
    # невидимым сколько угодно кругов — ровно тот класс, ради которого ворота и заведены.
    # Соответствие «семья -> реестр» строится ГРЕПОМ по вызовам _note_killers, а не
    # памятью; отсутствующий реестр называется явно, а не пропускается молча.
    _FAM_REG = (('run', 'RUN'), ('adapter', 'ADAPTER'), ('transition', 'TR'),
                ('signal', 'SIG'), ('roll', 'ROLL'), ('feed', 'FEED'), ('j7', 'J7'),
                ('session', 'SESSION_INVARIANTS'), ('intent', 'INTENT'),
                ('pack', 'PACK'), ('refusal', 'REF8'))
    _прочие = sorted(set(KILLERS) - {f for f, _ in _FAM_REG})
    if _прочие:
        print(f'\nСЕМЬИ БЕЗ РЕЕСТРА В ТАБЛИЦЕ (данные собраны, но не сверены): {_прочие}')
    for _fam, _regname in _FAM_REG:
        _reg = getattr(I, _regname, None)
        if _reg is None:
            print(f'\nСЕМЬЯ «{_fam}»: реестра {_regname} нет в invariants — НЕ СВЕРЕНА')
            continue
        # Досрочный выход есть только у run: остальные семьи гоняются целиком, и общий
        # ярлык «ОБРЕЗАН» ложно дисконтировал бы их точные цифры (урок пятого прогона).
        _mode = (('ПОЛНЫЙ прогон' if _mut_full() else
                  'ОБРЕЗАН досрочным выходом — список ЗАВЫШЕН, судить по полному')
                 if _fam == 'run' else 'ПОЛНЫЙ прогон (досрочного выхода нет)')
        _seen = KILLERS.get(_fam, {})
        _weak = [n for n, _f, _nd in _reg if not _seen.get(n)]
        print(f"\nУТВЕРЖДЕНИЯ СЕМЬИ «{_fam}» БЕЗ ЕДИНОГО УБИЙЦЫ ({_mode}): "
              f"{len(_weak)} из {len(_reg)}")
        for w in _weak:
            print(f'   {w}')
    sys.exit(1 if (missed or miss_a) else 0)
