#!/usr/bin/env python3
"""Фиктивный брокер: контур проверяется без IBKR и без Java.

Реализует ТОТ ЖЕ контракт адаптера, что ждёт transition.py — net_positions, open_orders,
cancel_order — плюс подачу заявок и NLV. Умеет вести себя плохо ровно теми способами,
которыми ведёт себя настоящий брокер: частичное исполнение, отказ, задержка, исполнение
БОЛЬШЕ заявки, разрыв связи между продажей и покупкой.

Смысл не в том, чтобы «проверить, что работает», а в том, чтобы каждый неприятный путь
был пройден хотя бы раз до того, как его пройдут деньги.
"""
import itertools


class BrokerError(Exception):
    pass


class FakeBroker:
    def __init__(self, nlv=10_000_000.0, positions=None, prices=None,
                 behaviour='normal', slip_bp=0.5, commission_per_contract=2.0):
        self.nlv = float(nlv)
        self._pos = dict(positions or {})
        self.prices = dict(prices or {})
        self.behaviour = behaviour
        self.slip_bp = slip_bp
        self.commission = commission_per_contract
        self._orders = {}
        self._ids = itertools.count(1)
        self.log = []

    # --- контракт адаптера, тот же, что у transition.py ---
    def net_positions(self):
        return dict(self._pos)

    def open_orders(self):
        return [oid for oid, o in self._orders.items() if o['status'] == 'open']

    def cancel_order(self, oid):
        """Снять заявку и вернуть СТРУКТУРНЫЙ ФАКТ — тот же вид, что у живого адаптера.
        Булево «да» смешивало два разных исхода: заявка снята и заявка успела исполниться."""
        o = self._orders.get(oid)
        if o is None:
            return dict(terminal=True, cancelled=True, status='отсутствует', filled=0.0)
        if o['status'] == 'open':
            o['status'] = 'cancelled'
            return dict(terminal=True, cancelled=True, status='cancelled', filled=0.0)
        done = float(o.get('filled', 0) or 0)
        return dict(terminal=True, cancelled=not done, status=o['status'], filled=done)


    # --- подача заявки ---
    def place(self, instrument, qty, px_order=None):
        if qty == 0:
            raise BrokerError('нулевая заявка')
        px_order = self.prices.get(instrument) if px_order is None else px_order
        if px_order is None:
            raise BrokerError(f'нет цены для {instrument}')
        oid = next(self._ids)
        b = self.behaviour
        if b == 'reject':
            self._orders[oid] = dict(status='rejected', instrument=instrument, qty=qty)
            raise BrokerError(f'заявка отклонена брокером: {instrument} {qty:+d}')
        filled = qty
        if b == 'partial':
            filled = int(qty * 0.6) or (1 if qty > 0 else -1)
        elif b == 'overfill':
            filled = qty + (1 if qty > 0 else -1)      # исполнение БОЛЬШЕ заявки — инцидент
        elif b == 'disconnect':
            self._orders[oid] = dict(status='open', instrument=instrument, qty=qty)
            raise BrokerError('связь потеряна после подачи; статус заявки неизвестен')
        px_fill = px_order * (1 + self.slip_bp / 1e4 * (1 if filled > 0 else -1))
        self._pos[instrument] = self._pos.get(instrument, 0) + filled
        comm = abs(filled) * self.commission
        self.nlv -= comm
        self._orders[oid] = dict(status='filled', instrument=instrument, qty=qty, filled=filled)
        rec = dict(order_id=oid, instrument=instrument, qty=qty, filled=filled,
                   px_order=px_order, px_fill=px_fill, commission=comm)
        self.log.append(rec)
        if filled != qty:
            rec['incident'] = ('недобор' if abs(filled) < abs(qty) else 'исполнение больше заявки')
        return rec

    def net_liquidation(self):
        return self.nlv
