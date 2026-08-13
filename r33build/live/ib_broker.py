#!/usr/bin/env python3
"""Адаптер Interactive Brokers под интерфейс, который контур уже умеет вызывать.

ЗАЧЕМ ОТДЕЛЬНЫМ ФАЙЛОМ. daily.py обращается к брокеру ровно четырьмя методами
(net_positions, net_liquidation, place, cancel_order) и ничего не знает про ib_insync. Вся
проверенная логика — решение, заявки как разность книг, восстановление по фактической
позиции — остаётся нетронутой, а живой брокер подставляется вместо fake_broker.
Совпадение интерфейсов проверяется здесь же (SAME_API), а не предполагается.

ПОЧЕМУ КОНТРАКТЫ БЕРУТСЯ ПО con_id. Имя серии («ESU26») однозначно только внутри нашего
кода: у биржи тот же контракт называется ESU6, и разбор строки при смене года давал бы
молча другой инструмент. Реестр instruments_live.csv снимается first_connect.py и
привязывает наше имя к con_id, то есть к конкретной поставке.

СТАТУС ЗАЯВКИ НЕ УГАДЫВАЕТСЯ. place возвращает управление только когда заявка получила
терминальный статус; всё остальное — исключение. Контур на исключение отвечает
восстановлением по ФАКТИЧЕСКОЙ книге брокера, а не повтором.
"""
import os
import time
import zoneinfo
from pathlib import Path

import tz                      # подключает устаревшие зоны IBKR (см. tz.py)


TERMINAL_OK = ('Filled',)
TERMINAL_BAD = ('Cancelled', 'ApiCancelled', 'Inactive')
SETTLE_S = 8.0        # сколько ждать разноски сделок и позиций после терминального статуса

# ВИД ЗАПИСИ ОБ ИСПОЛНЕНИИ. Контур читает из неё поле filled и сравнивает с заявленным.
# Прежняя проверка совпадения интерфейса сверяла только ИМЕНА ПАРАМЕТРОВ и пропустила
# расхождение возвращаемого значения: адаптер отдавал число, контур звал у него .get и
# падал на первой же живой заявке. Набор полей теперь объявлен и сверяется с макетом.
REC_KEYS = ('order_id', 'instrument', 'qty', 'filled', 'px_order', 'px_fill', 'commission')

# СТАТУС ЗАЯВКИ НЕ РАВЕН ФАКТУ ИСПОЛНЕНИЯ. Проверено на бумажном счёте 12.08.2026: заявка
# на 2 ES получила предупреждение 10349 («TIF выставлен в DAY пресетом счёта»), была
# помечена Cancelled — И ВСЁ РАВНО ИСПОЛНИЛАСЬ. Контур повтора не делает и пошёл
# восстанавливать книгу, но позиции у брокера ещё не разнеслись, сверка увидела ноль и
# объявила книгу восстановленной. На счёте остались два лишних контракта при полном
# молчании со стороны системы. Поэтому исполнение определяется ОТЧЁТАМИ О СДЕЛКАХ, а
# статус — только поводом их перечитать.


class BrokerError(RuntimeError):
    """Заявка не достигла терминального статуса или отвергнута. Повтор ЗАПРЕЩЁН."""


class IBBroker:
    def __init__(self, ib, registry=None, account=None, timeout_s=120, settle_s=SETTLE_S):
        miss = tz.missing()
        if miss:
            raise BrokerError(f'нет зон {miss}: отчёт об исполнении не разберётся, '
                              f'подтверждение сделки будет потеряно')
        self.ib = ib
        import os as _os
        pinned = account or _os.environ.get('ADDFUT_ACCOUNT')
        accts = ib.managedAccounts() or []
        if pinned:
            if accts and pinned not in accts:
                raise BrokerError(f'счёт {pinned} не среди managed {accts} — не тот шлюз')
            self.account = pinned
        elif len(accts) == 1:
            self.account = accts[0]
        else:
            # «Первый из списка» при нескольких счетах отправлял бы заявки на ЧУЖОЙ (№5).
            raise BrokerError(f'несколько managed accounts {accts}: пинуйте ADDFUT_ACCOUNT')
        self.timeout_s = timeout_s
        self.settle_s = float(settle_s)
        self.log = []
        self._con = {}
        self._qcache = {}
        self._stable = {}
        reg = Path(registry or os.environ.get('ADDFUT_REGISTRY')
                   or Path(__file__).resolve().parent / 'instruments_live.csv')
        if not reg.exists():
            raise BrokerError(f'нет реестра инструментов {reg}: сначала first_connect.py')
        import csv
        self._meta = {}
        with open(reg, encoding='utf-8') as f:
            for r in csv.DictReader(f):
                cid = int(r['con_id'])
                # ДУБЛИКАТ con_id — ОТКАЗ, А НЕ ПОСЛЕДНЕЕ ИМЯ ПОБЕЖДАЕТ. Обратное отображение
                # con_id -> имя строится для сверки позиций; при двух именах на один контракт
                # позиция молча меняла серию, а заявки шли те же — книга меняла смысл.
                dup = [k for k, v in self._con.items() if v == cid]
                if dup:
                    raise BrokerError(f'{reg}: con_id {cid} указан и для {dup[0]}, и для '
                                      f'{r["instrument"]} — реестр неоднозначен')
                self._con[r['instrument']] = cid
                self._meta[r['instrument']] = r

    # --- чтение ---------------------------------------------------------------
    def _exec_barrier(self):
        """Дождаться ВСЕХ отчётов об исполнении, известных брокеру на этот момент.

        reqExecutions — настоящий запрос-ответ: он завершается по execDetailsEnd, то есть
        служит БАРЬЕРОМ, а не паузой. Прежде исполнение определялось по тому, что успело
        разнестись за фиксированные восемь секунд: окно ошибки сдвигалось, но не
        закрывалось, и позднее исполнение снова расходилось с записанным состоянием.
        """
        try:
            self.ib.reqExecutions()
        except Exception as ex:
            # Глотать сбой барьера значит объявлять восстановление по заведомо неполным
            # отчётам (№15): исход неизвестен — так и говорим.
            raise BrokerError(f'барьер отчётов об исполнении недоступен: {ex}')

    def _snapshot(self):
        """Снимок позиций ПО ЗАВЕРШЁННОЙ ВЫГРУЗКЕ и ТОЛЬКО ПО СВОЕМУ СЧЁТУ.

        reqPositions отдаёт позиции ВСЕХ доступных счетов. Прежняя редакция поле счёта
        игнорировала, а словарь по con_id ещё и затирал совпадения последней строкой:
        сверялся чужой счёт, а торговался свой. Денежный исход прямой — на целевом счёте
        ноль ES, на соседнем 26, сверка «проходит», и книга не строится вовсе.
        Одинаковые con_id внутри одного счёта СКЛАДЫВАЮТСЯ, а не перетираются.
        """
        self._exec_barrier()
        pos = self.ib.reqPositions()
        if not pos:
            pos = self.ib.positions()
        out = {}
        for p in (pos or []):
            if not p.position:
                continue
            acct = getattr(p, 'account', None)
            if self.account and acct is not None and acct != self.account:
                continue
            out[p.contract.conId] = out.get(p.contract.conId, 0.0) + float(p.position)
        return {k: v for k, v in out.items() if v}

    def refresh(self, wait_s=None, tries=4):
        """Позиции читаются до СОВПАДЕНИЯ ДВУХ ПОДРЯД СНИМКОВ, а не один раз после паузы.

        Одна пауза фиксированной длины не барьер: отчёты об исполнении приходят
        асинхронно, и снимок «после SETTLE_S» с тем же успехом попадает в середину
        разноски — окно ошибки сдвигается, но не закрывается. Именно так восстановление
        объявлялось успешным, пока на счёте оставались лишние контракты. Устойчивость двух
        подряд чтений не доказывает, что разноска завершена, но исключает наблюдённый
        случай «позиции отстают на один запрос»; при несходимости — отказ, а не догадка.
        """
        w = self.settle_s if wait_s is None else wait_s
        prev = self._snapshot()
        for _ in range(tries):
            self.ib.sleep(w)
            cur = self._snapshot()
            if cur == prev:
                self._stable = cur
                return cur
            prev = cur
        raise BrokerError(f'позиции у брокера не устоялись за {tries} чтений: {prev} — '
                          f'сверка невозможна, сессия останавливается')

    def net_positions(self):
        """Фактическая книга в НАШИХ именах. Позиции по con_id, не по строке имени."""
        stable = self.refresh()
        back = {v: k for k, v in self._con.items()}
        out = {}
        for cid, q in stable.items():
            name = back.get(cid)
            if name is None:                 # чужой инструмент на счёте — не молчим
                name = f'НЕИЗВЕСТНЫЙ:{cid}'
            out[name] = out.get(name, 0) + float(q)
        return {k: v for k, v in out.items() if v}

    def open_orders(self):
        """Живые заявки НАШЕГО СЧЁТА. reqAllOpenOrders нужен, чтобы видеть заявки другого
        clientId и ручного терминала (иначе сверка пропускала их и разрешала ДУБЛИКАТ), но
        он же открывает видимость ЧУЖИХ managed accounts — их заявки не наши: ни снимать,
        ни блокироваться ими нельзя, и они отфильтровываются по полю счёта.

        СБОЙ ЗАПРОСА — ОТКАЗ, А НЕ ПУСТОЙ СПИСОК (шестнадцатый круг, №2): проглоченная
        ошибка оставляла локальный кэш openTrades, слепой к заявкам другого clientId;
        сверка «не видела» живую ADDFUT-заявку и разрешала дубль — обе позже исполнялись."""
        try:
            self.ib.reqAllOpenOrders()
            self.ib.sleep(1.0)
        except Exception as ex:
            raise BrokerError(f'запрос открытых заявок не выполнен ({ex}) — сверка '
                              f'дубликатов невозможна, торговля запрещена')
        out = set()
        for t in self.ib.openTrades():
            acct = getattr(t.order, 'account', '') or ''
            if self.account and acct and acct != self.account:
                continue
            out.add(t.order.orderId)
        return sorted(out)

    def todays_executions(self):
        """permId сегодняшних исполнений НАШЕЙ метки на НАШЕМ счёте (семнадцатый круг, №3):
        второй независимый источник для разбора намерения — снимок позиций один не
        доказывает «заявок не было» (fill_after_end, stale_twice)."""
        self._exec_barrier()
        out = []
        for f in (self.ib.fills() or []):
            ex = getattr(f, 'execution', None)
            if ex is None:
                continue
            acct = getattr(ex, 'acctNumber', '') or ''
            if self.account and acct and acct != self.account:
                continue
            ref = getattr(ex, 'orderRef', '') or ''
            if ref != 'ADDFUT':
                continue
            out.append(getattr(ex, 'permId', 0))
        return out

    def margin_cushion(self):
        """Живой запас О-3-Е: EquityWithLoan / MaintMarginReq от брокера (десятый круг,
        №2). Без него step_e пользовался расчётной прокси, которая при капе 2,00 не
        опускается ниже порога 1,40 — аварийное сокращение маршрута Е было недостижимо в
        бою по построению."""
        vals = (self.ib.accountSummary(self.account) if self.account
                else self.ib.accountSummary())
        ewl = maint = None
        for v in vals:
            if v.tag == 'EquityWithLoanValue' and v.currency == 'USD':
                ewl = float(v.value)
            if v.tag == 'MaintMarginReq' and v.currency == 'USD':
                maint = float(v.value)
        # NaN НЕ ЧИСЛО (семнадцатый круг, №7): NaN/maint давал cushion=NaN, сравнение
        # «NaN < 1.40» ложно — аварийное сокращение молча отключалось.
        if ewl is None or ewl != ewl:
            raise BrokerError('брокер не вернул числовой EquityWithLoanValue — запас '
                              'О-3-Е неизвестен')
        if maint is not None and maint != maint:
            raise BrokerError('MaintMarginReq = NaN — запас О-3-Е неизвестен')
        if not maint:
            return None       # требования нет; «нет позиций или неполный ответ» решает вызывающий
        return ewl / maint

    def net_liquidation(self):
        """NLV с ЯВНЫМ ЗАПРОСОМ ОБНОВЛЕНИЯ. Прежде читалось текущее содержимое кэша
        ib_insync без запроса, отметки времени и проверки связи: после переподключения или
        задержки цель ОБЕИХ ног считалась бы по старому капиталу, а у позиции возле капа
        это меняет и размеры заявок, и само решение о допустимости плеча."""
        if not self.ib.isConnected():
            raise BrokerError('связь с брокером потеряна — NLV недостоверен')
        # ОДНОРАЗОВЫЙ ЗАПРОС СВОДКИ, а не подписка на обновления счёта: reqAccountUpdates в
        # ib_insync ждёт полной выгрузки и на бумажном шлюзе НЕ ВОЗВРАЩАЕТСЯ вовсе —
        # сессия висела до тайм-аута. accountSummary отвечает за доли секунды и, в отличие
        # от чтения кэша, действительно ходит к брокеру.
        vals = (self.ib.accountSummary(self.account) if self.account
                else self.ib.accountSummary())
        for v in vals:
            if v.tag == 'NetLiquidation' and v.currency == 'USD':
                x = float(v.value)
                if not (x == x) or x <= 0:
                    raise BrokerError(f'NetLiquidation недостоверен: {v.value}')
                return x
        raise BrokerError('брокер не вернул NetLiquidation в USD')

    # --- запись ---------------------------------------------------------------
    def _contract(self, instrument):
        """Контракт по con_id С ПРОВЕРКОЙ ЛИЧНОСТИ.

        qualifyContracts возвращает описание, но ничего не сверяет: устаревший, ошибочный
        или переиспользованный con_id направил бы заявку в ДРУГУЮ серию, а внутренняя
        сверка продолжала бы звать её ожидаемым именем. Поля expiry и multiplier реестр уже
        хранит — теперь они не украшение, а условие подачи.
        """
        from ib_insync import Contract
        cid = self._con.get(instrument)
        if cid is None:
            raise BrokerError(f'{instrument}: нет в реестре instruments_live.csv')
        c = self._qcache.get(instrument)
        if c is not None:
            return c
        c = Contract(conId=cid)
        self.ib.qualifyContracts(c)
        if not c.conId:
            raise BrokerError(f'{instrument}: con_id {cid} не подтверждён биржей')
        import contracts as CT
        bad = CT.mismatches(c, self._meta.get(instrument, {})) \
            + CT.verify_isin(self.ib, c, self._meta.get(instrument, {}))
        if bad:
            raise BrokerError(f'{instrument}: con_id {cid} описывает другой контракт — '
                              f'{"; ".join(bad)}; обновить реестр first_connect.py')
        self._qcache[instrument] = c
        return c

    def _executed(self, tr):
        """Сколько РЕАЛЬНО исполнено по ЭТОЙ заявке — по отчётам о сделках, не по статусу.

        Сопоставление по одному orderId НЕДОСТАТОЧНО: номер уникален лишь внутри одного
        clientId и переиспользуется после перезапуска нумерации, поэтому чужое или старое
        исполнение могло быть приписано текущей заявке — и книга сохранилась бы неверной.
        Ключ — permId (глобальный и устойчивый), а совпадение контракта и счёта проверяется
        дополнительно. Пока permId не присвоен, исполнения не засчитываются вовсе.
        """
        perm = getattr(tr.order, 'permId', 0)
        cid = tr.contract.conId
        n = 0.0
        if perm:
            for f in self.ib.fills():
                e = f.execution
                if (getattr(e, 'permId', 0) == perm and f.contract.conId == cid
                        and (not self.account or e.acctNumber == self.account)):
                    n += float(e.shares) * (1 if e.side == 'BOT' else -1)
            return n
        # ЗАПАСНОГО ПУТИ НЕТ. Прежде при отсутствии permId, отсутствии отчёта, несовпадении
        # контракта или счёта исполнение всё равно принималось из orderStatus.filled — то
        # есть проверка личности исполнения могла НЕ ПРОЙТИ, а результат засчитывался. Ровно
        # этот счётчик и подвёл на живом счёте: статус «отменена», filled = 0, сделка есть.
        # Отсутствие доказательства — не доказательство отсутствия: исход НЕИЗВЕСТЕН.
        raise BrokerError(f'{tr.contract.localSymbol or tr.contract.conId}: исполнение не '
                          f'подтверждено отчётами (permId={perm}); исход НЕИЗВЕСТЕН, '
                          f'повтор запрещён')

    def _rec(self, tr, instrument, qty, px_order):
        """Запись об исполнении в том же виде, что отдаёт макет."""
        filled = self._executed(tr)
        px_fill = float(tr.orderStatus.avgFillPrice or 0) or None
        # Комиссии — ПО permId, как и исполнения (№27): orderId переиспользуется между
        # clientId, и чужая комиссия попадала бы в нашу строку журнала.
        perm = getattr(tr.order, 'permId', 0)
        comm = sum(float(f.commissionReport.commission or 0) for f in self.ib.fills()
                   if getattr(f.execution, 'permId', 0) == perm
                   and f.contract.conId == tr.contract.conId)
        rec = dict(order_id=tr.order.orderId, instrument=instrument, qty=qty, filled=filled,
                   px_order=px_order, px_fill=px_fill, commission=comm,
                   status=tr.orderStatus.status)
        if abs(filled - qty) > 1e-9:
            rec['incident'] = ('недобор' if abs(filled) < abs(qty)
                               else 'исполнение больше заявки')
        return rec

    def place(self, instrument, qty, px_order=None):
        from ib_insync import MarketOrder
        if not qty:
            raise BrokerError(f'{instrument}: нулевая заявка')
        # ДРОБНЫЕ ДОЛИ ФОНДОВ НЕ УСЕКАЮТСЯ. abs(int(qty)) превращал 100,5 в 100, а 0,5 — в
        # НУЛЕВУЮ заявку, уже прошедшую проверку «if not qty»: весь код выше специально
        # хранит дроби, а адаптер уничтожал их на последнем шаге (маршрут Е).
        q = abs(float(qty))
        if abs(q - round(q)) < 1e-9:
            q = int(round(q))
        o = MarketOrder('BUY' if qty > 0 else 'SELL', q)
        o.account = self.account
        o.orderRef = 'ADDFUT'          # принадлежность СТРАТЕГИИ, а не инструменту (№12)
        # TIF И ВНЕ ОСНОВНОЙ СЕССИИ — ЯВНО. Без этого пресет счёта ставит DAY, и рыночная
        # заявка вне RTH отменяется предупреждением 10349, притом иногда всё же исполняясь.
        o.tif = 'GTC'
        o.outsideRth = True
        tr = self.ib.placeOrder(self._contract(instrument), o)
        self.log.append((instrument, qty))
        t0 = time.time()
        while time.time() - t0 < self.timeout_s:
            self.ib.waitOnUpdate(timeout=1.0)
            st = tr.orderStatus.status
            if st in TERMINAL_OK:
                self._exec_barrier()      # барьер и на успешном пути: отчёт может отставать
                self.ib.sleep(1.0)
                rec = self._rec(tr, instrument, qty, px_order)
                if not rec['filled']:
                    # ПРОТИВОРЕЧИЕ: статус «исполнена», отчётов нет. Молча вернуть нулевое
                    # исполнение значит выдать неизвестность за факт.
                    raise BrokerError(f'{instrument} {qty:+d}: статус {st}, но отчётов об '
                                      f'исполнении нет — исход НЕИЗВЕСТЕН, повтор запрещён')
                return rec
            if st in TERMINAL_BAD:
                self._exec_barrier()                  # барьер, а не пауза
                self.ib.sleep(self.settle_s)
                if self._executed(tr):
                    # Отменена ПО СТАТУСУ, но исполнена по факту. Это не ошибка вызывающего
                    # и не повод повторять: книга изменилась, и вернуть надо именно факт.
                    return self._rec(tr, instrument, qty, px_order)
                raise BrokerError(f'{instrument} {qty:+d}: статус {st}, сделок нет — '
                                  f'заявка не исполнена')
        self._exec_barrier()
        self.ib.sleep(self.settle_s)
        done = self._executed(tr)
        raise BrokerError(f'{instrument} {qty:+d}: за {self.timeout_s} с статус остался '
                          f'{tr.orderStatus.status}, исполнено по отчётам {done:+.0f} — '
                          f'исход НЕИЗВЕСТЕН, повтор запрещён')

    def cancel_order(self, oid):
        """Снять заявку и вернуть СТРУКТУРНЫЙ ФАКТ, а не строку и не «да».

        Два разных исхода нельзя сводить к одному булеву значению: заявка снята — и заявка
        УСПЕЛА ИСПОЛНИТЬСЯ до снятия. Прежний живой адаптер отдавал строку там, где контур
        требовал ровно True, поэтому КАЖДОЕ восстановление на IBKR объявляло заявки
        неснятыми; а «очевидная починка» return True назвала бы исполненную заявку успешно
        отменённой — дефект опаснее исходного.
        """
        for t in self.ib.openTrades():
            if t.order.orderId == oid:
                # СЧЁТ ПРОВЕРЯЕТСЯ И ЗДЕСЬ (тринадцатый круг, №6): orderId не глобален между
                # clientId и счетами; защита не должна распадаться между open_orders и отменой.
                acct = getattr(t.order, 'account', '') or ''
                if self.account and acct and acct != self.account:
                    continue
                # СНИМАЮТСЯ ТОЛЬКО ЗАЯВКИ ЭТОЙ СТРАТЕГИИ. Восстановление после аварии не
                # вправе отменить ручную защитную заявку или заявку другой стратегии на том
                # же счёте: чужой инструмент возвращается НЕтерминальным ответом, попадает в
                # список неснятых, и сессия останавливается с именем заявки — решает человек.
                ref = getattr(t.order, 'orderRef', '') or ''
                # ПРИНАДЛЕЖНОСТЬ — ТОЛЬКО ПО МЕТКЕ СТРАТЕГИИ (десятый круг, №3): прежняя
                # связка «или инструмент из реестра» снимала ЧУЖУЮ защитную заявку на нашем
                # же ES/ZN. Нет метки ADDFUT — заявка не наша, снимать её нельзя.
                if ref != 'ADDFUT':
                    return dict(terminal=False, cancelled=False, status='чужая стратегия',
                                filled=0.0, foreign=True)
                self.ib.cancelOrder(t.order)
                t0 = time.time()
                while time.time() - t0 < 30:
                    self.ib.waitOnUpdate(timeout=1.0)
                    if t.orderStatus.status in TERMINAL_BAD + TERMINAL_OK:
                        self.ib.sleep(1.0)
                        done = self._executed(t)
                        return dict(terminal=True, cancelled=bool(not done),
                                    status=t.orderStatus.status, filled=done)
                raise BrokerError(f'заявка {oid}: снятие не подтверждено за 30 с')
        return dict(terminal=True, cancelled=True, status='отсутствует', filled=0.0)


def SAME_API():
    """Совпадение с макетом ПРОВЕРЯЕТСЯ ПРОГОНОМ ОБОИХ, а не сверкой макета с самим собой.

    Прежняя редакция сверяла имена параметров и поля записи, вызвав ТОЛЬКО FakeBroker.place;
    живой адаптер не исполнялся вовсе, поэтому уже существовавшее расхождение
    cancel_order (макет -> True, живой -> строка) объявлялось совместимым — и на IBKR каждое
    восстановление получало бы список неснятых заявок. Теперь оба брокера проходят
    одинаковые сценарии на подстановке ib_stub, и сравниваются ФАКТИЧЕСКИЕ ответы.
    """
    import csv
    import inspect
    import fake_broker as FB
    import ib_stub

    bad = []
    need = ('net_positions', 'open_orders', 'net_liquidation', 'place', 'cancel_order')
    for m in need:
        x = inspect.signature(getattr(FB.FakeBroker, m))
        y = inspect.signature(getattr(IBBroker, m))
        if list(x.parameters) != list(y.parameters):
            bad.append(f'{m}: макет {list(x.parameters)} против живого {list(y.parameters)}')

    # ФИКСТУРА, А НЕ ЖИВОЙ РЕЕСТР: проверка обязана работать в распакованном пакете, где
    # instruments_live.csv отсутствует по существу — он принадлежит счёту, а не программе.
    import tempfile
    d = tempfile.mkdtemp(prefix='addfut-fix-')
    reg = ib_stub.fixture_registry(d)
    rows = list(ib_stub.FIXTURE_ROWS)
    inst = rows[0]['instrument']

    def shape(v):
        """Вид ответа: тип и набор полей. Значения различаются законно, ВИД — нет."""
        if isinstance(v, dict):
            return ('dict', tuple(sorted(k for k in v if k not in ('status', 'incident'))))
        return (type(v).__name__,)

    # --- подача ---
    live = IBBroker(ib_stub.StubIB(rows), registry=reg, settle_s=0.0, timeout_s=1.0)
    mock = FB.FakeBroker(prices={inst: 100.0})
    rl, rm = live.place(inst, 2), mock.place(inst, 2)
    if shape(rl) != shape(rm):
        bad.append(f'place: живой {shape(rl)} против макета {shape(rm)}')
    if isinstance(rl, dict):
        for k in REC_KEYS:
            if k not in rl:
                bad.append(f'place: живой не отдаёт поле {k}')
    else:
        bad.append(f'place: живой отдаёт {type(rl).__name__}, а не запись об исполнении')

    # --- отмена: и снятой заявки, и УСПЕВШЕЙ ИСПОЛНИТЬСЯ ---
    for label, mk_live, mk_mock in (
            ('снятая', lambda: _stub_open(rows, inst), lambda: _mock_open(FB, inst)),
            ('исполненная', lambda: _stub_filled(rows, inst), lambda: _mock_filled(FB, inst))):
        (lb, loid), (mb, moid) = mk_live(), mk_mock()
        cl, cm = lb.cancel_order(loid), mb.cancel_order(moid)
        if shape(cl) != shape(cm):
            bad.append(f'cancel_order ({label}): живой {shape(cl)} против макета {shape(cm)}')
        for r, who in ((cl, 'живой'), (cm, 'макет')):
            if not isinstance(r, dict) or 'terminal' not in r or 'filled' not in r:
                bad.append(f'cancel_order ({label}): {who} не отдаёт terminal/filled')
    return bad


def _stub_open(rows, inst):
    import ib_stub
    import tempfile
    ib = ib_stub.StubIB(rows, behaviour='disconnect')
    b = IBBroker(ib, registry=ib_stub.fixture_registry(tempfile.mkdtemp()),
                 settle_s=0.0, timeout_s=1.0)
    try:
        b.place(inst, 1)
    except Exception:
        pass
    if not ib._trades:            # place подменён и до брокера не дошёл — заявка не нужна
        ib.placeOrder(ib._contract_of(int(rows[0]['con_id'])), _bare_order())
    return b, ib._trades[-1].order.orderId


def _bare_order():
    from ib_insync import MarketOrder
    return MarketOrder('BUY', 1)


def _stub_filled(rows, inst):
    import ib_stub
    import tempfile
    ib = ib_stub.StubIB(rows)
    b = IBBroker(ib, registry=ib_stub.fixture_registry(tempfile.mkdtemp()),
                 settle_s=0.0, timeout_s=1.0)
    try:
        r = b.place(inst, 1)
    except Exception:
        r = None
    if not isinstance(r, dict):
        ib.placeOrder(ib._contract_of(int(rows[0]['con_id'])), _bare_order())
        return b, ib._trades[-1].order.orderId
    return b, r['order_id']


def _mock_open(FB, inst):
    b = FB.FakeBroker(prices={inst: 100.0})
    b._orders[901] = dict(status='open', instrument=inst, qty=1)
    return b, 901


def _mock_filled(FB, inst):
    b = FB.FakeBroker(prices={inst: 100.0})
    return b, b.place(inst, 1)['order_id']


if __name__ == '__main__':
    bad = SAME_API()
    print('интерфейс совпадает с макетом' if not bad else 'РАСХОЖДЕНИЕ ИНТЕРФЕЙСА:')
    for x in bad:
        print('  ', x)
    raise SystemExit(1 if bad else 0)
