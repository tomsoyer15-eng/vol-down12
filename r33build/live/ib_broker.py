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
REC_KEYS = ('order_id', 'instrument', 'qty', 'filled', 'px_order', 'px_fill',
            'px_order_live', 'commission')      # №23: ориентир — котировка момента?

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
    # ПОЛОСЫ unit_ref — ПО КЛАССАМ, А НЕ ОДНА ШИРОКАЯ НА ВСЁ (тридцать первый круг, №5).
    # Единая полоса 15% выбиралась по САМОМУ неопределённому случаю — ноге Б, где модельная
    # единица несёт неизвестное исполнителю отношение d_fix/dref. Но для фондов и ноги А
    # единица наблюдаема прямо: у ETF это биржевая цена доли, у ES/MES — ES_MULT x SPY, а
    # расхождение котировки ES с SPY ограничено фьючерсным базисом (feed.BASIS_MAX = 2%).
    # Держать там же 15% значило пропускать ошибку плана в 10-15%: завышенный dprice даёт
    # недокупленную целевую ногу, заниженный unit_usd — фактическую непарную дельту выше
    # лимита 1%. Сужаем там, где есть независимое измерение; для ZN широта остаётся
    # ПРИЗНАННЫМ ПРЕДЕЛОМ (границы по крайним дюрациям норматива), а не выбором.
    UNIT_BAND = 0.15          # нога Б: предел — d_fix книги исполнителю неизвестен
    UNIT_BAND_ETF = 0.03      # фонд: единица = биржевая цена доли, сверять нечего сверх неё
    UNIT_BAND_EQ = 0.05       # нога А: ES_MULT x SPY против ES/10, базис ограничен 2%

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
        # ПИН ПОКОЛЕНИЯ — ОБЩИЙ С feed (двадцать четвёртый круг, №1). Адаптер читал CSV
        # САМ, минуя feed.registry() и его _REG_PIN: если first_connect заменит файл между
        # созданием адаптера и первым чтением рынка, адаптер останется на поколении A, а
        # решение и ориентиры придут из B — размер посчитан по исправленному con_id, а
        # заявка уйдёт по старому. Регистрируемся в том же пине, что и остальные читатели.
        try:
            import feed as _FDp
            _os_env = os.environ.get('ADDFUT_REGISTRY')
            os.environ['ADDFUT_REGISTRY'] = str(reg)
            try:
                _FDp.registry()                      # поднимет FeedError при смене поколения
            finally:
                if _os_env is None:
                    os.environ.pop('ADDFUT_REGISTRY', None)
                else:
                    os.environ['ADDFUT_REGISTRY'] = _os_env
        except ImportError:
            pass
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
            # КЛЮЧ УНИКАЛЕН ТОЛЬКО ПАРОЙ clientId:orderId (восемнадцатый круг, №2):
            # голый orderId схлопывал заявки разных клиентов в set, и отмена била по
            # первой найденной. Ключ непрозрачен для вызывающих — они возвращают его
            # в cancel_order как есть.
            cid = getattr(t.order, 'clientId', 0) or 0
            out.add(f'{cid}:{t.order.orderId}')
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

    def _summary_barrier(self):
        """СВЕЖИЙ request/end-барьер сводки счёта (девятнадцатый круг, №4).

        ib_insync.accountSummary() шлёт запрос только при ПУСТОМ кэше, дальше отдаёт
        значения подписки — IB обновляет её не чаще ~3 минут, и чтение сразу после
        исполнения видело ДОторговые NLV и MaintMarginReq: обе ноги размерялись по старому
        капиталу, а пост-трейд О-3-Е проверялся старым требованием. reqAccountSummary()
        каждый раз открывает НОВЫЙ reqId и блокируется до accountSummaryEnd — сервер
        отдаёт текущие значения на момент запроса. ПРИЗНАННЫЙ ПРЕДЕЛ: между End-барьером
        и использованием значение может измениться; точнее IB не отдаёт (§12)."""
        try:
            self.ib.reqAccountSummary()
        except Exception as ex:
            raise BrokerError(f'сводка счёта не обновлена ({ex}) — NLV и запас О-3-Е '
                              f'недостоверны, торговля запрещена')

    def margin_cushion(self):
        """Живой запас О-3-Е: EquityWithLoan / MaintMarginReq от брокера (десятый круг,
        №2). Без него step_e пользовался расчётной прокси, которая при капе 2,00 не
        опускается ниже порога 1,40 — аварийное сокращение маршрута Е было недостижимо в
        бою по построению."""
        self._summary_barrier()               # свежий срез, а не кэш подписки (№4)
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
        # БЕСКОНЕЧНОСТЬ — НЕ ЧИСЛО (двадцать шестой круг, №16): проверялся только NaN, а
        # EquityWithLoanValue=inf давало cushion=inf и МОЛЧА выключало О-3-Е.
        if ewl is not None and (ewl == float('inf') or ewl == float('-inf')):
            raise BrokerError(f'EquityWithLoanValue={ewl!r} — не конечное число, '
                              f'запас О-3-Е непроверяем')
        if ewl is None or ewl != ewl:
            raise BrokerError('брокер не вернул числовой EquityWithLoanValue — запас '
                              'О-3-Е неизвестен')
        if maint is not None and maint != maint:
            raise BrokerError('MaintMarginReq = NaN — запас О-3-Е неизвестен')
        # ТРЕБОВАНИЕ ОБЯЗАНО БЫТЬ КОНЕЧНЫМ И ПОЛОЖИТЕЛЬНЫМ (тридцать второй круг, №6).
        # У EquityWithLoanValue конечность проверялась (26-й круг, №16), а у maint — только
        # NaN: maint=inf давало cushion РОВНО 0, отрицательное — отрицательный cushion, и
        # оба МОЛЧА запускали аварийное сокращение книги вместо отказа по недостоверным
        # данным. С тридцать первого круга это уже не только тревога, но и продажа
        # (внутридневная вахта), поэтому цена повреждённой сводки IBKR — ненужная
        # ликвидация половины позиции.
        if maint is not None and (maint in (float('inf'), float('-inf')) or maint < 0):
            raise BrokerError(f'MaintMarginReq={maint!r} — не конечное положительное '
                              f'число, запас О-3-Е непроверяем (сокращение по такому '
                              f'числу было бы ликвидацией по ошибке сводки)')
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
        # сессия висела до тайм-аута. И не кэш подписки (девятнадцатый круг, №4):
        # accountSummary() после первого вызова ходит только в кэш — барьер даёт
        # reqAccountSummary() с новым reqId и ожиданием accountSummaryEnd.
        self._summary_barrier()
        vals = (self.ib.accountSummary(self.account) if self.account
                else self.ib.accountSummary())
        for v in vals:
            if v.tag == 'NetLiquidation' and v.currency == 'USD':
                x = float(v.value)
                # КОНЕЧНОСТЬ, А НЕ ТОЛЬКО NaN И ЗНАК (двадцать шестой круг, №16):
                # NetLiquidation=inf проходил `x == x` и `x > 0`, а в переходе сравнение
                # `inf > inf` ложно — значит ЛЮБОЙ конечный capital считался сверенным
                # с брокером, и все ворота считались от выдуманного числа.
                if not (x == x) or x in (float('inf'), float('-inf')) or x <= 0:
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
        # СЕРИЯ ИМЕНИ — И НА ГРАНИЦЕ ЗАЯВКИ (тридцать первый круг, №8). series_mismatch
        # завели в тридцатом круге, но подключили только к feed.contract_of: согласованно
        # подменённая строка (ESU26 с полями ESZ26) проходила ровно там, где подаётся
        # заявка, — реестр и биржа подтверждают друг друга, а смысл ИМЕНИ не сверяется ни с
        # чем. Заявка ушла бы в декабрьскую поставку под именем сентябрьской, а календарь
        # роллов и зона поставки считаются ПО ИМЕНИ. Исправление одной точки вызова из
        # двух — тот самый класс дефекта, ради которого круг и гоняется.
        bad = CT.identity_bad(self.ib, instrument, c, self._meta.get(instrument, {}))
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
        perm = getattr(tr.order, 'permId', 0)
        # ЦЕНА ИСПОЛНЕНИЯ — ИЗ ОТЧЁТОВ О СДЕЛКАХ (девятнадцатый круг, №16): статусное
        # avgFillPrice обновляется отдельным сообщением и за exec-барьером не обязано
        # поспевать; отчёты уже за барьером — цена берётся из них, взвешенно по объёму.
        _num = _den = 0.0
        for f in self.ib.fills():
            e = f.execution
            if (getattr(e, 'permId', 0) == perm and f.contract.conId == tr.contract.conId
                    and (not self.account or e.acctNumber == self.account)):
                _num += float(getattr(e, 'price', 0.0)) * abs(float(e.shares))
                _den += abs(float(e.shares))
        px_fill = (_num / _den) if _den and _num else \
            (float(tr.orderStatus.avgFillPrice or 0) or None)
        # Комиссии — ПО permId, как и исполнения (№27): orderId переиспользуется между
        # clientId, и чужая комиссия попадала бы в нашу строку журнала.
        # ТОЛЬКО ПРИШЕДШИЕ commissionReport (девятнадцатый круг, №16): exec-барьер — не
        # барьер комиссий; отчёт без execId ещё не пришёл, и нулём его считать нельзя —
        # поле остаётся ПУСТЫМ, и §7 честно не досчитывает строку, а не занижает издержки.
        comm = 0.0
        comm_ok = True
        for f in self.ib.fills():
            if (getattr(f.execution, 'permId', 0) == perm
                    and f.contract.conId == tr.contract.conId):
                cr = getattr(f, 'commissionReport', None)
                if cr is None or not getattr(cr, 'execId', ''):
                    comm_ok = False
                    continue
                comm += float(cr.commission or 0)
        rec = dict(order_id=tr.order.orderId, instrument=instrument, qty=qty, filled=filled,
                   px_order=px_order, px_fill=px_fill,
                   px_order_live=bool(getattr(self, '_px_live', False)),   # №23
                   commission=(comm if comm_ok else ''),
                   status=tr.orderStatus.status)
        if abs(filled - qty) > 1e-9:
            rec['incident'] = ('недобор' if abs(filled) < abs(qty)
                               else 'исполнение больше заявки')
        return rec

    def unit_ref(self, instrument, cls):
        """ПОЛОСА ДОЛЛАРОВОЙ ЕДИНИЦЫ по рыночным данным (двадцать девятый круг, №3).

        Исполнитель перехода получал unit_usd и dprice от вызывающего и проверял их лишь
        на конечность: все дальнейшие ворота считались по тем же числам, то есть план
        сверялся с планом. Здесь строится НЕЗАВИСИМАЯ полоса из закрытий и доходности.

        Полоса нарочно широка. Модельная единица ноги Б — ZN_MODEL_PX_EQ x CTD_RATIO x
        d_fix/dref, а d_fix принадлежит книге и исполнителю неизвестен; поэтому границы
        берутся по крайним допустимым дюрациям. Задача полосы — поймать ПОРЯДОК величины
        (десятикратную ошибку из денежного пути рецензии), а не базисные пункты.
        Возвращает (низ, верх) в долларах за ОДНУ единицу или None для чужого класса.
        """
        import feed as _FDu
        import sim_v13 as _Su
        name = str(instrument)
        root = ''.join(ch for ch in name if not ch.isdigit()).rstrip('UZHM') or name
        today = _FDu.exchange_today()
        if str(cls) == 'ETF':
            px, _, _, _ = _FDu.closes(self.ib, _FDu.contract_of(self.ib, name), today)
            px = float(px)
            return (px * (1.0 - self.UNIT_BAND_ETF), px * (1.0 + self.UNIT_BAND_ETF))
        if root in ('ES', 'MES'):
            # Модельная единица ноги А — ES_MULT x SPY; котировка ES отличается от SPY
            # фьючерсным базисом, и es_to_unit приводит её к десятой доле индекса.
            # MES КОТИРУЕТСЯ ТЕМ ЖЕ УРОВНЕМ ИНДЕКСА, ЧТО И ES (тридцать первый круг, №6).
            # Здесь стояло es_to_unit(px * 10) для MES — как будто мини-контракт котируется
            # десятой долей индекса. Он котируется тем же числом (~6000), а от ES отличается
            # множителем ($5 против $50), и он уже учтён в mult. Полоса MES выходила ровно
            # в ДЕСЯТЬ раз выше истины ($300 000 против $30 000): правильный план Е->Ф
            # отвергался как «вне рыночной полосы», а подогнанный под ошибочную полосу
            # купил бы десятую часть ноги А. Живая реализация не исполнялась ни одним
            # стендом — все переходные стенды подменяют unit_ref своей таблицей.
            px, _, _, _ = _FDu.closes(self.ib, _FDu.contract_of(self.ib, name), today)
            mult = _Su.ES_MULT / 10.0 if root == 'MES' else _Su.ES_MULT
            u = mult * _FDu.es_to_unit(float(px))
            return (u * (1.0 - self.UNIT_BAND_EQ), u * (1.0 + self.UNIT_BAND_EQ))
        if root == 'ZN':
            y, _ = _FDu.yield_pct(self.ib, today)
            dref = _FDu.dref_from_yield(float(y) / 100.0)
            base = _Su.ZN_MODEL_PX_EQ * _Su.CTD_RATIO
            # d_fix книги неизвестен: границы по крайним дюрациям норматива.
            return (base * _FDu.DUR_MIN / dref * (1.0 - self.UNIT_BAND),
                    base * _FDu.DUR_MAX / dref * (1.0 + self.UNIT_BAND))
        return None

    def _quote_ref(self, instrument):
        """КОТИРОВОЧНЫЙ ОРИЕНТИР В МОМЕНТ ЗАЯВКИ (двадцать второй круг, №20).

        Прежний px_order — ВЧЕРАШНЕЕ закрытие: §7 считал «издержками» разницу fill−close,
        то есть ночной гэп и всё движение до 08:45 записывались в торговые издержки, и
        сверка 5 б.п. мерила не исполнение, а погоду. Ориентир обязан быть ценой,
        существовавшей В МОМЕНТ подачи: снимок котировки (задержанной — у неё 15 минут
        возраста против суток у закрытия). Сбой снимка НЕ блокирует заявку — ориентир
        честно остаётся prev-close, и строка §7 помечается (это уже умеет _excluded_dates
        по пустой цене не ловить, поэтому помечаем notе-ой на уровне вызывающего).
        Возвращает (цена | None).
        """
        try:
            t = self.ib.reqMktData(self._contract(instrument), '', True, False)
            self.ib.sleep(2.0)
            # ПОРЯДОК ИСТОЧНИКОВ ЧЕСТНЫЙ (двадцать третий круг, №23): t.close у снимка —
            # это ОБЫЧНО ПРЕДЫДУЩЕЕ ЗАКРЫТИЕ, то есть ровно то, от чего мы уходим. Он
            # проверялся ВТОРЫМ, раньше mid, и §7 снова считал ночной гэп издержками.
            # Теперь: last -> mid(bid,ask) -> и только потом close, причём close помечается
            # как НЕ котировка момента.
            # ЗАДЕРЖАННЫЕ ДАННЫЕ НЕ ЕСТЬ КОТИРОВКА МОМЕНТА (двадцать пятый круг, №15).
            # Тип 3 у IBKR задержан примерно на 15 минут: за это время ES/ETF легко проходят
            # больше модельных 5 б.п., и §7 мерил бы движение рынка, а не качество
            # исполнения. Пометка live ставится ТОЛЬКО при подписке реального времени;
            # задержанные данные дают ориентир, но строка из выборки издержек исключается.
            # ФЛАГ ПО ФАКТУ, А НЕ ПО НАСТРОЙКЕ (тридцатый круг, №16). Прежде признак
            # «котировка реального времени» выводился ИСКЛЮЧИТЕЛЬНО из того, что выставлен
            # ADDFUT_REALTIME_MD=1. Но IB отдаёт delayed/frozen fallback, когда подписки на
            # КОНКРЕТНЫЙ инструмент нет: тикер придёт задержанный, а строка §7 будет
            # помечена live, и 15-минутное движение рынка «докажет» пересмотр 5 б.п.
            # Спрашиваем сам тикер: marketDataType 1 — реальное время, 2 — frozen,
            # 3 — delayed, 4 — delayed frozen. Настройка остаётся необходимым условием,
            # но достаточным — только подтверждение от биржи.
            # ОТСУТСТВИЕ ФАКТА — НЕ ФАКТ (тридцать второй круг, №16). Тридцатый круг снял
            # признак с настройки и повесил на тикер, но оставил `_mdt in (None, 1)`:
            # None — это «биржа ещё ничего не подтвердила» (callback не пришёл), и при
            # delayed-fallback без своевременного подтверждения пятнадцатиминутная
            # котировка попадала в §7 как live и «доказывала» пересмотр 5 б.п. Подтверждение
            # обязано быть ЯВНЫМ: только marketDataType == 1.
            _mdt = getattr(t, 'marketDataType', None)
            _rt = bool(getattr(self, 'realtime_md', False)) and (_mdt == 1)
            _mid = (t.bid + t.ask) / 2 if (t.bid and t.ask) else None
            for v, live in ((t.last, _rt), (_mid, _rt), (t.close, False)):
                v = float(v) if v is not None else float('nan')
                if v == v and v > 0:
                    return v, live
        except AttributeError as ex:
            # ПОЛОМКА ИНТЕРФЕЙСА — НЕ «КОТИРОВКИ НЕТ» (двадцать пятый круг, №16): молчаливое
            # проглатывание AttributeError превращало отсутствующий метод брокера в штатный
            # запасной путь, и расхождение макета с живым адаптером выглядело нормой.
            raise BrokerError(f'снимок котировки {instrument}: интерфейс брокера неполон '
                              f'({ex}) — ориентир недостоверен')
        except Exception:
            pass
        return None, False

    def place(self, instrument, qty, px_order=None):
        from ib_insync import MarketOrder
        if not qty:
            raise BrokerError(f'{instrument}: нулевая заявка')
        # ориентир момента заявки; вчерашнее закрытие — только запасной путь (№20).
        # ПОМЕТКА ЧЕСТНОСТИ (№23): если ориентир НЕ котировка момента (снимок не удался
        # либо отдал только close), запись несёт px_order_live=False, и §7 обязан
        # исключить строку из выборки издержек, а не выдавать гэп за проскальзывание.
        _q_ref, _q_live = self._quote_ref(instrument)
        if _q_ref is not None:
            px_order = _q_ref
        self._px_live = bool(_q_ref is not None and _q_live)
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
        # Ключ — 'clientId:orderId' из open_orders; голое число (старые вызыватели и
        # стенды) сравнивается только по orderId нашего clientId по-прежнему безопасно:
        # отмена дальше фильтрует счёт и метку стратегии.
        want_cid = want_oid = None
        if isinstance(oid, str) and ':' in oid:
            _c, _o = oid.split(':', 1)
            want_cid, want_oid = int(_c), int(_o)
        else:
            want_oid = int(oid)
        for t in self.ib.openTrades():
            _tc = getattr(t.order, 'clientId', 0) or 0
            if t.order.orderId == want_oid and (want_cid is None or _tc == want_cid):
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
        # ЗАЯВКИ НЕТ В openTrades — ЭТО НЕ «ОТМЕНЕНА» (двадцать третий круг, №13). Она
        # могла ИСПОЛНИТЬСЯ между open_orders() и cancel_order(); прежний ответ
        # (terminal=True, cancelled=True, filled=0) означал «ничего не произошло», и при
        # устойчиво старом снимке позиций переход писал ABORT вместо MIXED, а ежедневное
        # восстановление объявляло книгу исходной. Исход выясняется по ОТЧЁТАМ ИСПОЛНЕНИЯ
        # (правило 7 проекта: статус заявки ≠ факт), и только пустой отчёт даёт «отсутствует».
        _done = 0.0
        try:
            # БАРЬЕР ОТЧЁТОВ ПЕРЕД ЧТЕНИЕМ (двадцать седьмой круг, №14): fills() читает КЭШ,
            # и сразу после исчезновения заявки отчёт мог ещё не дойти — «filled=0» означало
            # бы «не исполнялась», хотя исполнение состоялось.
            self._exec_barrier()
            for f in self.ib.fills():
                _ex = getattr(f, 'execution', None)
                if _ex is None:
                    continue
                if int(getattr(_ex, 'orderId', -1)) != want_oid:
                    continue
                if want_cid is not None and int(getattr(_ex, 'clientId', -1)) != want_cid:
                    continue
                _acct = getattr(_ex, 'acctNumber', '') or ''
                if self.account and _acct and _acct != self.account:
                    continue
                _done += abs(float(getattr(_ex, 'shares', 0) or 0))
        except Exception as ex:
            raise BrokerError(f'заявка {oid} исчезла из openTrades, а отчёты исполнения '
                              f'недоступны ({ex}) — исход неизвестен, объявлять отменённой '
                              f'нельзя (правило 7)')
        if _done:
            return dict(terminal=True, cancelled=False, status='исполнена до отмены',
                        filled=_done)
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
    # СТАБ ОТДАЁТ КОТИРОВКУ, КАК ЖИВОЙ ШЛЮЗ (двадцать пятый круг, №16): без баров снимок
    # не удавался, живой адаптер писал px_order_live=False против True у макета, и сверка,
    # смотревшая только на набор ключей, объявляла их одинаковыми. Фикстура обязана
    # описывать штатное состояние рынка, а не его отсутствие.
    _sib = ib_stub.StubIB(rows)
    _sib.quote_px = 100.0
    live = IBBroker(_sib, registry=reg, settle_s=0.0, timeout_s=1.0)
    mock = FB.FakeBroker(prices={inst: 100.0})
    rl, rm = live.place(inst, 2), mock.place(inst, 2)
    if shape(rl) != shape(rm):
        bad.append(f'place: живой {shape(rl)} против макета {shape(rm)}')
    elif isinstance(rl, dict) and isinstance(rm, dict):
        # ЗНАЧЕНИЯ, А НЕ ТОЛЬКО НАБОР КЛЮЧЕЙ (двадцать пятый круг, №16): на qty/filled
        # стоит сверка с намерением, на px_order_live — попадание строки в выборку §7.
        for _k in ('qty', 'filled', 'px_order_live'):
            _vl, _vm = rl.get(_k), rm.get(_k)
            _same = (bool(_vl) == bool(_vm) if _k == 'px_order_live'
                     else abs(float(_vl or 0) - float(_vm or 0)) < 1e-9)
            if not _same:
                bad.append(f'place: поле {_k} — живой {_vl!r}, макет {_vm!r}')
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
        # ЗНАЧЕНИЯ ОТМЕНЫ (двадцать шестой круг, №17). В прошлый раз правка легла ТОЛЬКО на
        # place, и это осталось незамеченным. Именно cancelled/filled решают, писать ABORT
        # или MIXED и повторять ли заявку: подмена filled=5,cancelled=False на
        # filled=0,cancelled=True оставляла сверку зелёной, а поздний fill приходил уже
        # после снятого pending.
        if isinstance(cl, dict) and isinstance(cm, dict):
            for _k in ('terminal', 'cancelled', 'foreign'):
                if bool(cl.get(_k)) != bool(cm.get(_k)):
                    bad.append(f'cancel_order ({label}): поле {_k} — живой {cl.get(_k)!r}, '
                               f'макет {cm.get(_k)!r}')
            if abs(float(cl.get('filled') or 0) - float(cm.get('filled') or 0)) > 1e-9:
                bad.append(f'cancel_order ({label}): filled — живой {cl.get("filled")!r}, '
                           f'макет {cm.get("filled")!r}: на нём стоит ABORT против MIXED')
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
