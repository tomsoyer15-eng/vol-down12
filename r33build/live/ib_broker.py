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


# ОШИБКИ КОДА — ИЗ ОДНОЙ ТОЧКИ (state.CODE_ERRORS, сведено рецензией 19.08): копия в
# каждом модуле разошлась бы на первом же добавленном классе, и §12-инцидент воскрес бы
# в незатронутой копии.
import state as _STce
CODE_ERRORS = _STce.CODE_ERRORS


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

    def unit_ref(self, instrument, cls, at_close=False):
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
        import contracts as _CTu
        name = str(instrument)
        root = ''.join(ch for ch in name if not ch.isdigit()).rstrip('UZHM') or name
        today = _FDu.exchange_today()
        # ОДИН МОМЕНТ — ЭТО ОДНА ДАТА, А НЕ «ТИП ЦЕНЫ» (сорок второй круг, №5). at_close
        # переключал каждый инструмент на его ПОСЛЕДНЕЕ закрытие, а даты выбрасывались, и
        # closes(expected_prev=None) допускает бар возрастом до пяти дней: в одном gross
        # могли смешаться пятничный CSPX, понедельничный ES и другая дата TNX для ZN. Все
        # числа конечны, полосы пройдены, а сравнение с CLOSE_CAP=2,00 относилось бы к книге,
        # которой не существовало ни в один момент времени. Требуем ИМЕННО закрытие
        # предыдущей биржевой сессии — тот же ключ, которым живёт вся арифметика §1.
        # КАЛЕНДАРЬ ПРИНАДЛЕЖИТ ПЛОЩАДКЕ ИНСТРУМЕНТА, А НЕ ДВИЖКУ (СОРОК ЧЕТВЁРТЫЙ КРУГ,
        # №8). Здесь безусловно брался календарь CME и применялся В ТОМ ЧИСЛЕ к CSPX/CBU0.
        # После американского праздника Европа торгует, и её свежий бар НОВЕЕ, чем «предыдущая
        # сессия CME»: closes(expected_prev=...) требует точного совпадения и поднимает
        # [STALE_BAR], gross() падает. Предполёт с at_close=False проходит (там полоса
        # строится по котировке момента), первая пара уже исполнена — и переход уходит в
        # MIXED с непарной позицией. Фонды живут по LSE/SIX, и календарь у них свой; тот же
        # eu_holidays уже держит весь маршрут Е в feed.py — правило одно, источник один.
        _is_fund = str(cls) == 'ETF' or name in _CTu.ETF_EXPECT
        _prev = self._venue_prev(_is_fund, today) if at_close else None
        # ФОНД ОПОЗНАЁТСЯ ПО ИМЕНИ, А НЕ ПО КЛАССУ ОТВЕТА БИРЖИ (тридцать седьмой круг, №2).
        # Здесь стояло только `cls == 'ETF'`, а живой реестр отдаёт для CSPX/CBU0 secType
        # 'STK' (грабли §7: у биржи фонды — акции). gross() передаёт класс ИЗ реестра,
        # поэтому для обеих целей маршрута Е полоса возвращалась None, gross падал
        # «долларовая единица неизвестна» — и первый же живой Ф->Е после уже исполненной
        # пары уходил в MIXED: штатный COMPLETE был технически недостижим. Ложное
        # доказательство рядом: стенд unit_ref подавал искусственный 'ETF' и конфликта не
        # видел. Имя — то же независимое ожидание, что и в contracts.ETF_EXPECT.
        # ОДИН МОМЕНТ НА ВСЮ КНИГУ (сорок первый круг, №3). Прежде ETF/ES брались из снимка
        # (last/mid, пусть и задержанного), а ZN — из доходности ПРЕДЫДУЩЕЙ сессии: gross
        # смешивал разные моменты, и падение текущей котировки могло занизить плечо и
        # пропустить CLOSE_CAP=2,00. Конвенция §1 — закрытия предыдущей сессии, и плечо,
        # сверяемое с капом, обязано считаться ими же. at_close=True просит именно их;
        # проверка ПЛАНА перехода (check_plan_prices) по-прежнему смотрит на момент, там
        # полоса ловит порядок величины, а не сверяет с решением.
        if _is_fund:
            px = None if at_close else self._px_now(name)
            if px is None:
                px, _d, _, _ = _FDu.closes(self.ib, _FDu.contract_of(self.ib, name), today,
                                           expected_prev=_prev if at_close else None)
                self._last_close_date = _d
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
            px = None if at_close else self._px_now(name)
            if px is None:
                px, _d, _, _ = _FDu.closes(self.ib, _FDu.contract_of(self.ib, name), today,
                                           expected_prev=_prev if at_close else None)
                self._last_close_date = _d
            mult = _Su.ES_MULT / 10.0 if root == 'MES' else _Su.ES_MULT
            # ЦЕНА НОГИ А — САМ SPY, А НЕ ES/10 (СОРОК ЧЕТВЁРТЫЙ КРУГ, №1, P0).
            # Норматив: модельная единица ноги А = ES_MULT x SPY. feed.build_market так и
            # считает, а ES/10 держит ТОЛЬКО как сверку базиса (|ES/10 / SPY - 1| <= 2%).
            # Здесь же единица строилась из ES, и gross() брал СЕРЕДИНУ этой полосы как
            # цену — то есть ворота капа на закрытии считали ногу А по фьючерсу, а решение
            # дня по SPY. Базис (ставка минус дивиденды) сдвигает их друг относительно
            # друга, а между CLOSE_CAP=2,00 и INTRA_CAP=2,02 запаса на 2% нет вовсе:
            # при отрицательном базисе плечо занижается и книга выше капа проходит ворота.
            # Тот же класс уже чинили для ноги Б (37-й круг, №3: «середина полосы — не
            # цена»), но починили ПРИМЕР, а не правило — нога А осталась как была.
            # На пути капа (at_close) берём SPY закрытием ТОЙ ЖЕ предыдущей сессии.
            if at_close:
                from ib_insync import Stock as _Stk
                _spyc = _Stk('SPY', 'SMART', 'USD', primaryExchange='ARCA')
                self.ib.qualifyContracts(_spyc)
                _spy, _dspy, _, _ = _FDu.closes(self.ib, _spyc, today, expected_prev=_prev)
                _spy = float(_spy)
                if not _spy or _spy != _spy:
                    raise BrokerError(f'{name}: закрытие SPY недостоверно — модельная '
                                      f'единица ноги А неизвестна, плечо непроверяемо')
                _basis = _FDu.es_to_unit(float(px)) / _spy - 1.0
                if abs(_basis) > _FDu.BASIS_MAX:
                    raise BrokerError(f'{name}: базис ES/10 к SPY {_basis:+.2%} превышает '
                                      f'{_FDu.BASIS_MAX:.0%} — один из источников '
                                      f'недостоверен, плечо непроверяемо')
                u = mult * _spy
            else:
                u = mult * _FDu.es_to_unit(float(px))
            return (u * (1.0 - self.UNIT_BAND_EQ), u * (1.0 + self.UNIT_BAND_EQ))
        if root == 'ZN':
            # ДАТА ТРЕБУЕТСЯ И ДЛЯ ZN (сорок третий круг, №4). Для ETF/ES я передал
            # expected_prev, а обе ветки доходности оставил без него — и feed.closes снова
            # допускает бар возрастом до пяти календарных дней: пятничный TNX во вторник
            # проходит, старый dref размеряет ZN, а полоса, построенная по столь же старому
            # dref, этого не ловит. CLOSE_CAP=2,00 пропустил бы фактически более крупную
            # книгу. Семья «один момент» была исправлена на две трети.
            y, _ = _FDu.yield_pct(self.ib, today, expected_prev=_prev if at_close else None)
            dref = _FDu.dref_from_yield(float(y) / 100.0)
            base = _Su.ZN_MODEL_PX_EQ * _Su.CTD_RATIO
            # d_fix книги неизвестен: границы по крайним дюрациям норматива.
            return (base * _FDu.DUR_MIN / dref * (1.0 - self.UNIT_BAND),
                    base * _FDu.DUR_MAX / dref * (1.0 + self.UNIT_BAND))
        return None

    # ------------------------------------------------------------------ переход Ф<->Е
    # ИНТЕРФЕЙС ПЕРЕХОДНОГО БРОКЕРА (тридцать шестой круг, №6). Исполнитель перехода зовёт
    # preview / sell_units / buy_units / minutes_since / gross, и НИ ОДНОГО из них у живого
    # адаптера не было: первый штатный переход остановился бы на broker.preview() ещё до
    # OPEN. Это не «нет живого подтверждения» — успешный путь был технически неисполним, а
    # SAME_API сверял только пять ежедневных методов и потому молчал.
    #
    # ЧЕСТНАЯ ГРАНИЦА: методы построены на УЖЕ проверенных примитивах (place с барьером
    # permId, согласованный снимок позиций, сводка счёта), но вживую переход не исполнялся
    # ни разу — это остаётся признанным пределом, а не закрытым долгом.

    def sell_units(self, instrument, units):
        """Продажа единиц. Возвращает (номер заявки, исполнено) — как ждёт исполнитель.

        ДРОБИ НЕ УСЕКАЮТСЯ (тридцать седьмой круг, №4). Здесь стояло `int(units)` — тот же
        дефект, который в основном place() был исправлен раньше и о котором в place()
        написан отдельный абзац: новая обёртка прошла мимо него. При выходе из книги
        CBU0 = 2 000 000,5 весь переход исполнился бы, а последний лот 0,5 стал бы нулевой
        заявкой и дал MIXED на ровном месте. Целочисленность фьючерсов обеспечивает
        планировщик (plan_lots), а не адаптер: усекать здесь нечего.
        """
        rec = self.place(instrument, -abs(float(units)), self._px_hint(instrument))
        return rec.get('order_id'), -float(rec.get('filled') or 0.0)

    def buy_units(self, instrument, units):
        """Покупка единиц. Возвращает (номер заявки, исполнено). Дроби — см. sell_units."""
        rec = self.place(instrument, abs(float(units)), self._px_hint(instrument))
        return rec.get('order_id'), float(rec.get('filled') or 0.0)

    def _px_hint(self, instrument):
        """Ориентир цены для журнала §7; сбой снимка не смеет ронять перевод денег."""
        try:
            px, _ = self._quote_ref(instrument)
            return px
        except Exception:
            return None

    def _px_now(self, instrument):
        """Котировка МОМЕНТА или None. Отдельно от _px_hint: полоса единицы обязана
        отличать «цена есть» от «цены нет», а не подставлять молча вчерашнее закрытие."""
        try:
            px, _ = self._quote_ref(instrument)
            return float(px) if px is not None else None
        except Exception:
            return None

    def _venue_prev(self, is_fund, today):
        """Предыдущая сессия ПЛОЩАДКИ инструмента — одной функцией, чтобы правило было
        наблюдаемо одной мутацией (сорок четвёртый круг, №8).

        Фонды CSPX/CBU0 живут по LSE/SIX, фьючерсы — по CME. Прежде календарь брался один,
        CME, и навязывался фондам: после американского праздника европейский бар НОВЕЕ
        «предыдущей сессии CME», closes(expected_prev=...) требует точного совпадения и
        поднимает [STALE_BAR] — gross() падает уже ПОСЛЕ первой исполненной пары, то есть
        переход уходит в MIXED. Тот же eu_holidays держит весь маршрут Е в feed.py."""
        import feed as _FDv
        if is_fund:
            try:
                _hol = _FDv.eu_holidays(today.year, today.year + 1)
            except _FDv.FeedError:
                # следующий год ещё не продлён — об этом кричит calendar_horizon, не здесь
                _hol = _FDv.eu_holidays(today.year)
            return _FDv.prev_session(today, _hol)
        import daily as _DLv
        return _FDv.prev_session(today, holidays=_DLv.holidays_for(today.year))

    def mark_pair(self, key):
        """ПУСК ЧАСОВ ПАРЫ (тридцать седьмой круг, №6). minutes_since() заводил отсчёт
        ПЕРВЫМ ОБРАЩЕНИЕМ, а первое обращение исполнитель делал уже ПОСЛЕ продажи, покупки,
        компенсации и gross(): в этот момент метод возвращал почти ноль, и лимит 15 минут не
        мог сработать никогда — одноитерационная пара висела сколько угодно. Часы обязаны
        пускаться ДО первой заявки пары, явным вызовом, который видно и можно мутировать."""
        # ЧАСЫ МОНОТОННЫЕ, А НЕ НАСТЕННЫЕ (СОРОК ЧЕТВЁРТЫЙ КРУГ, №10). time.time() идёт от
        # системных часов: шаг NTP или ручной перевод назад делает возраст пары
        # ОТРИЦАТЕЛЬНЫМ, и предел 15 минут отключается ровно тогда, когда одна нога уже
        # продана; перевод вперёд даёт ложный тайм-аут и обрывает законную пару.
        # monotonic от часов не зависит по определению.
        # И ПУСК ЗНАЧИТ ПУСК (тот же №10): здесь стоял setdefault, то есть повторное
        # использование ключа в том же объекте брокера НАСЛЕДОВАЛО часы прошлой пары —
        # тайм-аут мог сработать сразу после первой заявки новой. Пара начинается здесь,
        # значит и отсчёт начинается здесь: присваиваем, а не подставляем по умолчанию.
        import time as _t
        _m = getattr(self, '_since', None)
        if _m is None:
            _m = self._since = {}
        _m[str(key)] = _t.monotonic()
        return True

    def minutes_since(self, key):
        """Минуты с ПУСКА часов этой пары — таймаут ожидания встречной ноги.

        Часы пускает mark_pair() до первой заявки. Если ключ неизвестен (пуск пропущен),
        это не «ноль минут», а неизмеримость: тайм-аут — денежная защита, и её молчаливое
        отключение опаснее лишнего отказа."""
        import time as _t
        _m = getattr(self, '_since', None)
        if _m is None or str(key) not in _m:
            raise BrokerError(f'часы пары {key} не пущены (mark_pair) — тайм-аут 15 минут '
                              f'неизмерим, исполнение вслепую запрещено')
        return (_t.monotonic() - _m[str(key)]) / 60.0

    def gross(self, d_fix=None):
        """Плечо ФАКТИЧЕСКОЙ книги: суммарная модельная экспозиция к NLV.

        ПОЗИЦИИ — ЖИВЫЕ, ЦЕНЫ — ЗАКРЫТИЯ ПРЕДЫДУЩЕЙ СЕССИИ (уточнено в тридцать восьмом
        круге, №6: прежний текст обещал «котировки момента» и этим врал). Так и должно быть:
        модельная арифметика §1 вся построена на закрытиях предыдущей сессии, и плечо,
        сверяемое с капом, обязано считаться теми же ценами, что и решение. Живое здесь —
        то, что и должно быть живым: состав книги у брокера.

        СЕРЕДИНА ПОЛОСЫ — НЕ ЦЕНА (тридцать седьмой круг, №3). Прежде экспозиция считалась
        по середине проверочной полосы предыдущего закрытия. Для ZN полоса нарочно широка
        (строится по крайним дюрациям D=3..12, потому что d_fix принадлежит книге), и её
        середина не имеет отношения к действительности: при d_fix=12 и dref=8 фактическая
        модельная единица около $147 800, а середина полосы — около $100 700. Плечо
        занижалось примерно на треть и проходило CLOSE_CAP=2,00 при фактическом превышении.
        При INTRA_CAP=2,02 и CLOSE_CAP=2,00 запаса на такую ошибку нет вовсе.

        Поэтому: единица ноги Б считается ЧЕСТНОЙ формулой норматива от d_fix книги и живой
        доходности, а независимая полоса остаётся тем, чем задумывалась, — воротами порядка
        величины: посчитанная единица обязана в неё попасть. Это не «план доказывает план»:
        d_fix — константа книги, а не плана перехода; позиции, котировки и доходность
        по-прежнему берутся у брокера и биржи. Без d_fix ноги Б в книге плечо непроверяемо,
        и это отказ, а не тихая подстановка середины.
        """
        import feed as _FDg
        import sim_v13 as _Sg
        pos = self.net_positions() or {}
        nlv = float(self.net_liquidation())
        if not nlv or nlv != nlv:
            raise BrokerError('NLV недостоверен — плечо на закрытии непроверяемо')
        _exp = 0.0
        for _inst, _q in pos.items():
            _q = float(_q or 0.0)
            if not _q:
                continue
            if str(_inst).startswith('НЕИЗВЕСТНЫЙ'):
                raise BrokerError(f'{_inst}: посторонняя позиция — плечо непроверяемо')
            # at_close=True: плечо считается конвенцией §1 — закрытиями предыдущей сессии
            _band = self.unit_ref(_inst, self._meta.get(_inst, {}).get('sec_type', ''),
                                  at_close=True)
            if not _band:
                raise BrokerError(f'{_inst}: долларовая единица неизвестна — плечо '
                                  f'непроверяемо')
            # КОНЕЧНОСТЬ ПОЗИЦИИ (тридцать восьмой круг, №6): при q=NaN экспозиция и плечо
            # выходят NaN, а ОБА сравнения «g > INTRA_CAP» и «g > CLOSE_CAP» с NaN ЛОЖНЫ —
            # переход дошёл бы до COMPLETE с непроверенным плечом. Тот же класс, что
            # NaN-cushion семнадцатого круга: неизвестность обязана быть отказом.
            if _q != _q or _q in (float('inf'), float('-inf')):
                raise BrokerError(f'{_inst}: позиция {_q!r} не конечна — плечо непроверяемо')
            _root = ''.join(c for c in str(_inst) if not c.isdigit()).rstrip('UZHM')
            if _root == 'ZN':
                if d_fix is None or float(d_fix) != float(d_fix) or not float(d_fix):
                    raise BrokerError(f'{_inst}: d_fix книги не передан или не конечен '
                                      f'({d_fix!r}) — модельная единица ноги Б неизвестна, '
                                      f'плечо непроверяемо')
                # ДОХОДНОСТЬ — ЗАКРЫТИЕ ПРЕДЫДУЩЕЙ СЕССИИ, И ТАК И НАДО НАЗЫВАТЬ (№6).
                # feed.yield_pct намеренно отбрасывает сегодняшний незавершённый бар, а
                # docstring обещал «котировки момента». Это не дефект расчёта: вся модельная
                # арифметика §1 строится на закрытиях предыдущей сессии, и dref обязан быть
                # тем же, что у решения. Дефект был в ОБЕЩАНИИ. При CLOSE_CAP=2,00
                # внутридневной сдвиг доходности в плечо не входит — это признанный предел,
                # общий с ежедневным контуром, а не свойство одного gross().
                # ОДНО ЧТЕНИЕ ДОХОДНОСТИ НА ВЕСЬ РАСЧЁТ (№4): gross читал её ОТДЕЛЬНО от
                # полосы, то есть полоса и единица могли опираться на разные бары. Читаем
                # с той же ожидаемой датой, что и полоса, — и один раз на вызов.
                import daily as _DLg2
                _prev_g = _FDg.prev_session(_FDg.exchange_today(),
                                            holidays=_DLg2.holidays_for(
                                                _FDg.exchange_today().year))
                _y, _ = _FDg.yield_pct(self.ib, _FDg.exchange_today(),
                                       expected_prev=_prev_g)
                _dref = _FDg.dref_from_yield(float(_y) / 100.0)
                if not _dref or _dref != _dref:
                    raise BrokerError(f'{_inst}: dref {_dref!r} не конечен — плечо '
                                      f'непроверяемо')
                _u = _Sg.ZN_MODEL_PX_EQ * _Sg.CTD_RATIO * float(d_fix) / _dref
            else:
                # для ES/MES/фондов полоса построена вокруг самой котировки момента
                _u = (float(_band[0]) + float(_band[1])) / 2.0
            if _u != _u or not (_band[0] <= _u <= _band[1]):
                raise BrokerError(f'{_inst}: модельная единица ${_u:,.0f} вне независимой '
                                  f'полосы ${_band[0]:,.0f}..${_band[1]:,.0f} — плечо '
                                  f'непроверяемо')
            _exp += abs(_q) * _u
        _g = _exp / nlv
        # РЕЗУЛЬТАТ ОБЯЗАН БЫТЬ ЧИСЛОМ (№6): ворота сравнением с NaN пропускают что угодно.
        if _g != _g or _g in (float('inf'), float('-inf')):
            raise BrokerError(f'плечо {_g!r} не конечно — ворота капа его не проверят')
        return _g

    def preview(self, orders=None, emergency=False):
        """Предпросмотр маржи ПЕРЕД переводом денег.

        ЗДОРОВЬЕ ИСХОДНОЙ КНИГИ НИЧЕГО НЕ ГОВОРИТ О ЦЕЛЕВОЙ (тридцать седьмой круг, №7).
        Комментарий обещал whatIf, а метод whatIfOrder не вызывал и плана вообще не знал:
        он спрашивал margin_cushion() ТЕКУЩЕЙ книги. Запас 2,0× на фьючерсах не доказывает,
        что счёт выдержит целевую позицию в фондах с займом, а при внутридневном повышении
        house-margin устаревший файловый предполёт мог пропустить цель, и этот «предпросмотр»
        давал ложное разрешение ровно там, где нужен запрет.

        ЧТО ЭТА ПРОВЕРКА НЕ ДОКАЗЫВАЕТ (сорок первый круг, №1) — назвать обязательно.
        whatIfOrder существует только для ОДНОЙ заявки: совместной маржи целевого портфеля у
        API спросить нечем. Сумма отдельных приращений не является даже нижней оценкой —
        нелинейные offsets и concentration add-ons способны сделать совместное требование
        ВЫШЕ каждой из наших величин. Пример: две дельты по $100k при NLV $1 млн выглядят
        запасом 5×, а совместная цель с FullInit $800k даёт фактические 1,25×.
        Поэтому preview — НЕОБХОДИМОЕ условие, а не достаточное: он отсекает случаи, где уже
        нижняя оценка не проходит. Достаточность держат другие, независимые барьеры:
        файловый предполёт по ИЗМЕРЕННЫМ маржам контрактов (preflight_margin_orders),
        внутрипарные ворота gross <= INTRA_CAP после КАЖДОЙ пары и вахта О-3-Е, сокращающая
        книгу в ту же сессию. Признанный предел, а не закрытый долг.

        Теперь при переданном плане (список пар инструмент-количество ЦЕЛИ) спрашивается сам
        брокер: whatIfOrder на каждую целевую заявку. Истина — только если брокер вернул
        начальную маржу по ВСЕМ заявкам и их сумма оставляет запас (NLV/маржа >= 1,40 по
        О-3-Е). Пустой или неполный ответ — ЛОЖЬ, то есть «отложить»: бумажный whatIf
        капризен (утром отдаёт, днём пусто, §12), и отсрочка при непонятном ответе дешевле
        перевода вслепую. Без плана метод остаётся прежней проверкой текущей книги и честно
        объявлен нижней границей, а не разрешением.
        """
        self._preview_why = ''                 # причина отказа этого вызова (44-й круг, №14)

        def _no(why):
            # ПРИЧИНА — СТРУКТУРНО, А НЕ ПО ДИСЦИПЛИНЕ (найдено рецензией 19.08, угол
            # «упрощение»). Первая редакция №14 полагалась на то, что КАЖДЫЙ выход не
            # забудет записать _preview_why, и уже в ней пять выходов из десяти молчали —
            # включая ПУСТОЙ ОТВЕТ whatIf, то есть ровно семью граблей 18.08 (пресет счёта
            # и TIF). Оператор снова читал бы «маржа цели не проходит О-3-Е» там, где шлюз
            # просто не ответил. Один выход — одна функция: забыть причину больше нельзя.
            self._preview_why = why
            return False

        try:
            _c = self.margin_cushion()
        except BrokerError as _exc0:
            return _no(f'запас счёта неизвестен ({_exc0})')
        # РАННИЙ БАРЬЕР НЕ СМЕЕТ ЗАПИРАТЬ РАЗГРУЗКУ (сорок третий круг, №3). Здесь стояло
        # безусловное `cushion < 1.0 -> False` ДО чтения плана: значит ветка «все приращения
        # неположительны — разрешить разгрузку», введённая в 42-м круге, была НЕДОСТИЖИМА
        # именно при фактическом маржинальном дефиците, ради которого и заводилась. Три
        # отказа — POSTPONED и ABORT, маршрут Е остаётся без аварийного выхода при cushion
        # ниже 1,0. Я чинил эту дверь дважды и оба раза оставлял её запертой этажом выше.
        # Барьер сохраняется для планов БЕЗ разгрузки и для вызова без плана; при плане
        # решение принимается ниже, по знаку приращений.
        if _c is None:
            return _no('запас счёта не число (None) — сводка не отдана')
        if float(_c) < 1.0 and not orders:
            return _no(f'запас {float(_c):.2f}x ниже 1,0 и плана нет')
        if not orders:
            return True
        try:
            import contracts as _CTp
            from ib_insync import MarketOrder
            _need = 0.0
            _ims = []                      # приращения по каждой заявке — для худшей оценки
            for _inst, _qty in orders:
                _qty = float(_qty)
                if not _qty:
                    continue
                # ЦЕЛОЕ ЧИСЛО КОНТРАКТОВ ДЛЯ ФЬЮЧЕРСА (тридцать восьмой круг, №5). План
                # приходит сырым отношением units*unit_usd/dprice и даёт, например,
                # 109,295 MES. Дробная what-if заявка на фьючерс у IBKR недопустима: ответ
                # пустой или ошибочный, preview честно возвращает «отложить», три раза
                # подряд — и ЗАКОННЫЙ переход уходит в ABORT. Округляем ровно так же, как
                # торгует исполнитель; дробность оставлена только фондам.
                if str(_inst) not in _CTp.ETF_EXPECT:
                    _qty = float(int(round(_qty)))
                    if not _qty:
                        continue
                _o = MarketOrder('BUY' if _qty > 0 else 'SELL', abs(_qty))
                _o.whatIf = True
                # ЗАЯВКА ПРИВЯЗЫВАЕТСЯ К ПИНОВАННОМУ СЧЁТУ (№4): без account ответ
                # относится к произвольному счёту под тем же логином — предпросмотр
                # доказывал бы маржу НЕ ТОГО портфеля.
                if self.account:
                    _o.account = self.account
                # TIF ЯВНО — ИНАЧЕ whatIfOrder ОТДАЁТ ПУСТОЙ СПИСОК (найдено 18.08). Пресет
                # счёта переопределяет TIF и ломает ответ; без этой строки preview на живом
                # шлюзе ВСЕГДА возвращал бы «отложить», то есть переход после трёх попыток
                # уходил бы в ABORT — и вся работа кругов 37-43 над предпросмотром была бы
                # бесполезна в бою. Стаб этого не воспроизводил: он отвечает всегда.
                # ...И ИМЕННО ТОТ TIF, С КОТОРЫМ ЗАЯВКА ПОЙДЁТ В РЫНОК. place() ставит
                # GTC+outsideRth (см. ниже, там же и причина), а предпросмотр спрашивал про
                # DAY — то есть доказывал маржу НЕ ТОЙ заявки. На шлюзе 18.08 обе формы дали
                # одинаковую начальную маржу (34 777,13), но совпадение чисел не есть
                # правило: предпросмотр обязан описывать подаваемую заявку, а не похожую.
                _o.tif = 'GTC'
                _o.outsideRth = True
                _st = self.ib.whatIfOrder(self._contract(str(_inst)), _o)
                _im = getattr(_st, 'initMarginChange', None) if _st is not None else None
                if _im in (None, ''):
                    # СЕМЬЯ 18.08: пустой ответ шлюза — не «маржа не прошла», а МОЛЧАНИЕ
                    # (пресет счёта, TIF, отсутствие подписки). Называем это прямо.
                    return _no(f'{_inst}: шлюз не вернул начальную маржу (пустой ответ '
                               f'whatIf) — предпросмотр недоказуем')
                _im = float(_im)
                if _im != _im or _im in (float('inf'), float('-inf')):
                    return _no(f'{_inst}: приращение маржи {_im!r} не конечно')
                _ims.append(_im)
                _need += _im
            # ОТРИЦАТЕЛЬНАЯ СУММА — НЕ ДОКАЗАТЕЛЬСТВО (тридцать восьмой круг, №4). Здесь
            # стояло `if _need <= 0: return True`. Каждая целевая заявка просматривается
            # ПООДИНОЧКЕ и против ВСЁ ЕЩЁ СУЩЕСТВУЮЩЕЙ исходной книги: неттинг с активами,
            # которые будут проданы, даёт отрицательные приращения, исчезающие после
            # продажи. Складывать их как «полную маржу цели» нельзя, и объявлять
            # отрицательную сумму «маржи не потребуется» — тоже.
            # НО И ЖЁСТКИЙ ОТКАЗ ЗДЕСЬ НЕВЕРЕН (найдено САМОРЕЦЕНЗИЕЙ, правило 8а). Первая
            # редакция этой правки возвращала False — а отрицательная сумма это ровно
            # подпись АВАРИЙНОГО ВЫХОДА Е→Ф: уход из книги с займом во фьючерсы маржу
            # ОСВОБОЖДАЕТ. Три отказа подряд превратили бы спасительный путь в инцидент,
            # то есть защита закрыла бы дверь, ради которой построена. emergency preview не
            # обходит (обход касается только порога §8), проверено грепом.
            # Компромисс назван вслух: когда приращения не доказывают маржу цели, требуем
            # доказуемого — что счёт СЕЙЧАС здоров по тому же нормативу О-3-Е (1,40, а не
            # 1,0 как раньше). Это нижняя граница, а не доказательство целевой книги.
            import daily as _DLp
            _nlv = float(self.net_liquidation())
            if not _nlv or _nlv != _nlv:
                return _no(f'NLV счёта {_nlv!r} не конечен — запас непроверяем')
            # РАННЕГО ВЫХОДА БОЛЬШЕ НЕТ (сороковой круг, №1). Здесь стояло
            # `if _need <= 0: return cushion >= O3E_MIN` — ДО вычисления худшей оценки. При
            # приращениях [-900k, +800k] сумма отрицательна, текущий запас здоров, и переход
            # разрешался, хотя одна положительная часть требует $800k и при NLV $1 млн даёт
            # запас лишь 1,25×. Обещанная «худшая из трёх оценок» выполнялась только при
            # положительной сумме — то есть обещание было шире кода, снова.
            # Отрицательная сумма по-прежнему НЕ запрещает переход (иначе закрылся бы
            # аварийный выход Е→Ф, освобождающий маржу), но и не даёт пройти мимо оценки:
            # она просто одна из трёх, и решает ХУДШАЯ.
            # СУММА ПРИРАЩЕНИЙ — НИЖНЯЯ ОЦЕНКА, А НЕ МАРЖА ЦЕЛИ (тридцать девятый круг, №2).
            # Каждый whatIfOrder считается ПРОТИВ ВСЁ ЕЩЁ СУЩЕСТВУЮЩЕЙ исходной позиции:
            # неттинг с активами, которые затем будут проданы, занижает приращения, а сумма
            # приращений не равна FullInitMarginReq целевого портфеля. Значит порога О-3-Е
            # по такой сумме НЕДОСТАТОЧНО: смесь отрицательных и положительных с
            # положительным итогом всё ещё даёт ложное разрешение.
            # Что можно утверждать честно: требование цели НЕ МЕНЬШЕ суммы положительных
            # приращений, и оно не меньше уже действующего требования счёта (источник ещё не
            # продан, но целевая книга того же порядка). Берём ХУДШУЮ из двух оценок и
            # добавляем запас за неизмеримый неттинг: без полного FullInitMarginReq цели это
            # предпросмотр, а не доказательство, и он обязан ошибаться в сторону отказа.
            # Множителя «на всякий случай» здесь НЕТ намеренно: подгонка параметров правилом
            # проекта запрещена, а консерватизм даёт сам выбор ХУДШЕЙ из оценок.
            _pos_need = sum(x for x in _ims if x > 0)
            # ДЕЙСТВУЮЩЕЕ ТРЕБОВАНИЕ БОЛЬШЕ НЕ ВХОДИТ В ОЦЕНКУ ЦЕЛИ (сорок первый круг, №2).
            # Оно относится к ИСХОДНОЙ книге, которая после перехода будет продана, и нижней
            # границей маржи ЦЕЛИ не является. Хуже: при маржинальном стрессе (cushion 1,20×,
            # FullInitMarginReq близко к NLV) оно безусловно выигрывало max(), и preview
            # возвращал False — то есть запирал АВАРИЙНЫЙ выход Е→Ф ровно тогда, когда он
            # нужен, а три отказа подряд давали инцидент без выхода. emergency preview не
            # обходит. Оценка цели строится только из приращений цели.
            _worst = max(_need, _pos_need)
            if _worst != _worst:
                return _no('худшая оценка маржи цели не конечна')
            # Разгрузка разрешается ДАЖЕ при cushion ниже 1,0: план не требует маржи, он её
            # освобождает, и запрет здесь равен запрету на спасение книги.
            if _worst <= 0:
                # ВСЕ ПРИРАЩЕНИЯ ОТРИЦАТЕЛЬНЫ — ЭТО ПОДПИСЬ ОСВОБОЖДЕНИЯ МАРЖИ (сорок второй
                # круг, №4). В 41-м круге я убрал из оценки действующее FullInitMarginReq и
                # счёл выход Е→Ф открытым. Он остался заперт: при _worst<=0 решение падало на
                # ТЕКУЩИЙ запас, а при маржинальном стрессе он как раз ниже 1,40 — то есть
                # трижды POSTPONED и ABORT ровно тогда, когда выход и нужен. Логика была
                # вывернута: план, который маржу ОСВОБОЖДАЕТ, запрещался из-за того, что её
                # сейчас мало.
                # НЕПОЛОЖИТЕЛЬНЫЕ ПРИРАЩЕНИЯ САМИ ПО СЕБЕ НИЧЕГО НЕ ДОКАЗЫВАЮТ (СОРОК
                # ЧЕТВЁРТЫЙ КРУГ, №2, P0). Здесь стояло: «целевая книга требует не больше
                # текущей — каждое приращение неположительно», и этого объявлялось
                # достаточно. Утверждение ложно: каждый whatIfOrder считается ПРОТИВ ВСЁ ЕЩЁ
                # СУЩЕСТВУЮЩЕЙ исходной книги, и отрицательное приращение может быть просто
                # неттингом с активами, которые переход затем ПРОДАСТ. После продажи офсет
                # исчезает, а целевая книга требует положительной маржи. Опаснее всего это
                # при cushion ниже 1,0: ветка обходит последнюю ЖИВУЮ проверку и оставляет
                # решение файловому замеру возрастом до 35 дней.
                # ЧТО МОЖНО УТВЕРЖДАТЬ ЧЕСТНО — величину, НЕ ЗАВИСЯЩУЮ ОТ НЕТТИНГА: по
                # ИЗМЕРЕННЫМ маржам контрактов целевые заявки требуют не больше, чем занято
                # книгой сейчас. Это ровно смысл слова «разгрузка», и оно проверяется тем же
                # замером, которым потом считает preflight_margin_orders.
                # ДВЕРЬ АВАРИЙНОГО ВЫХОДА ОСТАЁТСЯ ОТКРЫТОЙ: настоящий выход Е->Ф покупает
                # фьючерсы, чья измеренная маржа меньше требования исходной книги с займом,
                # и проверку проходит. Закрывается только ложный случай — когда «разгрузка»
                # existует лишь в приращениях, а целевая книга на деле дороже.
                if _ims and all(x <= 0 for x in _ims) and self._release_by_measure(orders):
                    return True
                if float(_c) >= _DLp.O3E_MIN:
                    return True
                return _no(f'разгрузка замером НЕ подтверждена, а текущий запас '
                           f'{float(_c):.2f}x ниже норматива О-3-Е {_DLp.O3E_MIN}')
            if float(_c) < 1.0:
                # план ТРЕБУЕТ маржи, а её сейчас нет вовсе — это отказ по существу
                return _no(f'план требует маржи, а запас счёта {float(_c):.2f}x ниже 1,0')
            if _nlv / _worst >= _DLp.O3E_MIN:
                return True
            return _no(f'худшая оценка маржи цели ${_worst:,.0f} при NLV ${_nlv:,.0f} даёт '
                       f'запас {_nlv / _worst:.2f}x ниже норматива О-3-Е {_DLp.O3E_MIN}')
        except CODE_ERRORS:
            # ОШИБКА КОДА НЕ ПЕРЕОДЕВАЕТСЯ В «МАРЖА НЕ ПРОШЛА» (44-й круг, №14): иначе
            # опечатка в предпросмотре три раза подряд читается как отказ по марже и уводит
            # ЗАКОННЫЙ переход в ABORT с ложным объяснением.
            raise
        except Exception as _exv:
            # ПРИЧИНА СОХРАНЯЕТСЯ (тот же №14). Голый False объявлял любую беду — контракт,
            # счёт, реестр, обрыв API — «маржа отвергнута», и оператор искал деньги там, где
            # сломан справочник. Решение прежнее (отложить), объяснение — настоящее.
            return _no(f'{type(_exv).__name__}: {_exv}')

    def _release_by_measure(self, orders):
        """Подтверждение разгрузки ПО ИЗМЕРЕННЫМ маржам, а не по приращениям whatIf.

        Сорок четвёртый круг, №2. Приращения считаются против ещё не проданной исходной
        книги и потому заражены неттингом; измеренная маржа контракта от состава книги не
        зависит. Требуем: сумма измеренной маржи ЦЕЛЕВЫХ заявок <= измеренной маржи книги
        СЕЙЧАС. Неизвестность — отказ (О-5): если замер не покрывает инструмент цели или
        книги, разгрузка не подтверждена, и решение падает обратно на порог О-3-Е.
        """
        try:
            import transition as _Tr
            _lm = _Tr._live_margins()
        except Exception:
            return False
        if not _lm:
            return False

        def _root(name):
            n = str(name)
            return ''.join(c for c in n if not c.isdigit()).rstrip('UZHM') or n

        need = 0.0
        for _inst, _qty in (orders or ()):
            _q = abs(float(_qty))
            if not _q:
                continue
            _m = _lm.get(_root(_inst))
            if _m is None or float(_m) != float(_m) or float(_m) <= 0:
                return False           # цель не покрыта замером — не доказано
            need += float(_m) * _q
        if need <= 0:
            return False
        # «СЕЙЧАС ЗАНЯТО» БЕРЁТСЯ У БРОКЕРА, А НЕ ИЗ ФАЙЛА (найдено углом «от
        # противоположного знака» сразу после первой редакции этой правки). Считать have по
        # тому же замеру нельзя: при аварийном выходе Е->Ф текущая книга состоит из ФОНДОВ,
        # а замер покрывает только фьючерсы — helper вернул бы False, и правка снова заперла
        # бы дверь спасения. Ровно ту, которую я уже запирал дважды (41-й и 42-й круги).
        # Действующее требование счёта измерено брокером по ФАКТИЧЕСКОЙ книге и от неттинга
        # целевых заявок не зависит: это законная правая часть сравнения.
        try:
            self._summary_barrier()
            _vals = (self.ib.accountSummary(self.account) if self.account
                     else self.ib.accountSummary())
        except Exception:
            return False
        have = None
        for _v in _vals:
            if getattr(_v, 'tag', '') == 'FullInitMarginReq' and getattr(_v, 'currency', '') == 'USD':
                have = float(_v.value)
        if have is None:
            for _v in _vals:
                if getattr(_v, 'tag', '') == 'MaintMarginReq' and getattr(_v, 'currency', '') == 'USD':
                    have = float(_v.value)
        if have is None or have != have or have <= 0 or have in (float('inf'), float('-inf')):
            return False               # требование счёта неизвестно — не доказано (О-5)
        return need <= have

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
    # ИНТЕРФЕЙС ПЕРЕХОДА СВЕРЯЕТСЯ ТОЖЕ (тридцать шестой круг, №6). SAME_API проверял ровно
    # пять ежедневных методов, поэтому отсутствие preview/sell_units/buy_units/minutes_since/
    # gross у живого адаптера НЕ ЛОВИЛОСЬ ничем: успешный путь перехода был технически
    # неисполним, а батарея оставалась зелёной. Сверяем ПРИСУТСТВИЕ и форму вызова с тем,
    # что зовёт исполнитель (transition.py) — расхождение обязано быть громким.
    # ПРИСУТСТВИЕ И ЧИСЛО АРГУМЕНТОВ — НЕ ПОВЕДЕНИЕ (тридцать седьмой круг, №16). Сверка
    # ниже осталась, но сама по себе она пропустила ВСЕ дефекты этого круга по переходному
    # интерфейсу: класс 'STK' против ожидания 'ETF', середина полосы вместо цены, усечение
    # дробей, часы пары, предпросмотр без плана. Ни один метод не ВЫЗЫВАЛСЯ, а у макета их
    # не было вовсе — сравнивать было не с чем. Ниже добавлен прогон обоих.
    _tr_need = {'preview': 3, 'sell_units': 3, 'buy_units': 3, 'minutes_since': 2,
                'mark_pair': 2, 'gross': 2, 'unit_ref': 4}
    for _m, _n in sorted(_tr_need.items()):
        _f = getattr(IBBroker, _m, None)
        _g = getattr(FB.FakeBroker, _m, None)
        if _f is None:
            bad.append(f'{_m}: живой адаптер НЕ несёт метод, который зовёт переходный '
                       f'исполнитель — успешный путь перехода неисполним')
            continue
        if _g is None:
            bad.append(f'{_m}: макет НЕ несёт переходный метод — поведение живого сверять '
                       f'не с чем, расхождение снова станет невидимым')
            continue
        _p = list(inspect.signature(_f).parameters)
        _pm = list(inspect.signature(_g).parameters)
        if len(_p) != _n:
            bad.append(f'{_m}: аргументы живого {_p} не совпадают с ожиданием исполнителя '
                       f'({_n} с учётом self)')
        if len(_pm) != _n:
            bad.append(f'{_m}: аргументы макета {_pm} не совпадают с ожиданием исполнителя '
                       f'({_n} с учётом self)')

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

    # --- ПЕРЕХОДНЫЕ МЕТОДЫ ВЫЗЫВАЮТСЯ, А НЕ ОСМАТРИВАЮТСЯ (тридцать седьмой круг, №16) ---
    _tib = ib_stub.StubIB(rows)
    _tib.quote_px = 100.0          # штатный рынок: без котировки проверялось бы её отсутствие
    _tl = IBBroker(_tib, registry=reg, settle_s=0.0, timeout_s=1.0)
    _tm = FB.FakeBroker(prices={inst: 100.0})

    # (а) ДРОБНАЯ ДОЛЯ ДОХОДИТ ДО ЗАЯВКИ. int(units) превращал последний лот 0,5 в нулевую
    # заявку и давал MIXED на выходе из маршрута Е.
    # ПРОВЕРЯЕТСЯ НА ФОНДЕ, А НЕ НА ФЬЮЧЕРСЕ (тридцать восьмой круг, №7): дробность —
    # свойство маршрута Е, и брать rows[0] (ESU26) значило проверять её там, где её не
    # бывает. Макету цена фонда даётся явно, иначе place() откажет «нет цены».
    import contracts as _CTs
    _frac_inst = 'CBU0'
    _tm.prices[_frac_inst] = 5.0
    for _nm, _fn in (('sell_units', 'sell_units'), ('buy_units', 'buy_units')):
        try:
            _oid, _f = getattr(_tl, _fn)(_frac_inst, 0.5)
            if abs(abs(float(_f)) - 0.5) > 1e-9:
                bad.append(f'{_nm}: дробная доля 0,5 фонда исполнена как {_f!r} — живой '
                           f'адаптер усекает дроби, последний лот маршрута Е станет '
                           f'нулевой заявкой')
        except Exception as _ex:
            bad.append(f'{_nm}: дробная доля 0,5 фонда отвергнута живым ({_ex}) — выход из '
                       f'маршрута Е неисполним')
        try:
            _oidm, _fm = getattr(_tm, _fn)(_frac_inst, 0.5)
            if abs(abs(float(_fm)) - 0.5) > 1e-9:
                bad.append(f'{_nm}: макет исполнил дробную долю фонда как {_fm!r}')
        except Exception as _ex:
            bad.append(f'{_nm}: макет отверг дробную долю фонда ({_ex})')

    # (б) ЧАСЫ ПАРЫ. Без пуска minutes_since обязан ОТКАЗАТЬ, а не вернуть ноль: нулём он
    # молча отключал лимит 15 минут (№6).
    for _who, _b in (('живой', _tl), ('макет', _tm)):
        try:
            _b.minutes_since('нет-такой-пары')
            bad.append(f'minutes_since ({_who}): непущенные часы вернули число вместо '
                       f'отказа — тайм-аут 15 минут молча отключается')
        except Exception:
            pass
        _b.mark_pair('пара:0')
        try:
            if float(_b.minutes_since('пара:0')) < 0:
                bad.append(f'minutes_since ({_who}): отрицательное время после пуска')
        except Exception as _ex:
            bad.append(f'minutes_since ({_who}): после mark_pair отказ ({_ex})')

    # (в) ПОЛОСА ЕДИНИЦЫ ФОНДА ПРИ КЛАССЕ 'STK'. Биржа отдаёт фонды акциями (грабли §7), и
    # именно этот класс gross() берёт из живого реестра: ожидание 'ETF' возвращало None и
    # роняло плечо в MIXED (№2). Проверяем ровно ту пару, что бывает вживую — у ОБОИХ
    # брокеров (тридцать восьмой круг, №7: у макета unit_ref не вызывался вовсе).
    for _fund in sorted(_CTs.ETF_EXPECT):
        for _who, _b in (('живой', _tl), ('макет', _tm)):
            _tm.prices.setdefault(_fund, 5.0)
            try:
                _b_stk = _b.unit_ref(_fund, 'STK')
            except Exception:
                _b_stk = None
            if not _b_stk:
                bad.append(f'unit_ref({_fund}, "STK") у {_who}: полосы нет — живой реестр '
                           f'отдаёт фонды классом STK, и gross() на маршруте Е станет '
                           f'неизмерим')

    # (г) gross() И preview() ВЫЗЫВАЮТСЯ, А НЕ ЧИСЛЯТСЯ (тридцать восьмой круг, №7). В 37-м
    # круге я объявил SAME_API исправленным «вызывает оба брокера», а на деле вызывались
    # только sell/buy_units, minutes_since и unit_ref живого. Поэтому регрессия к середине
    # полосы ZN или к старому preview осталась бы зелёной, а ib_stub.whatIfOrder — мёртвым
    # кодом. Это ровно тот класс, который круг и ищет: комментарий обещает больше, чем код.
    _tib.quote_px = 100.0
    _tl_pos = IBBroker(_tib, registry=reg, settle_s=0.0, timeout_s=1.0)
    for _who, _b, _dfix in (('живой', _tl_pos, 8.0), ('макет', _tm, 8.0)):
        try:
            _g = _b.gross(_dfix)
            if _g != _g or _g < 0:
                bad.append(f'gross ({_who}): вернул {_g!r} — ворота капа такое не проверят')
        except Exception as _exg:
            bad.append(f'gross ({_who}): вызов не проходит ({_exg}) — финальная сверка '
                       f'перехода неисполнима')
    # gross ОБЯЗАН ОТКАЗАТЬ БЕЗ d_fix, когда в книге есть нога Б: молчаливая подстановка
    # середины полосы занижала плечо примерно на треть (тридцать седьмой круг, №3).
    _tib_zn = ib_stub.StubIB(rows); _tib_zn.quote_px = 100.0
    _tib_zn._pos = {900003: 10.0}; _tib_zn._shown = dict(_tib_zn._pos)
    # ДОХОДНОСТЬ ОБЯЗАНА БЫТЬ В ФИКСТУРЕ, ИНАЧЕ ПРОВЕРКА МОЛЧА ПРОПУСКАЕТСЯ. Без баров TNX
    # yield_pct поднимает FeedError, тот попадал в `except Exception: pass` — и сверка
    # значения gross не выполнялась ВООБЩЕ, оставаясь зелёной. Ровно тот класс, ради
    # которого круг и заведён: проверка на месте, но недостижима.
    import datetime as _dtz
    import daily as _DLz
    import feed as _FDz
    _tz = _FDz.exchange_today()
    _pz = _FDz.prev_session(_tz, holidays=_DLz.holidays_for(_tz.year)).date()
    _tib_zn.set_bars({990001: [(str(_pz - _dtz.timedelta(days=1)), 46.9),
                               (str(_pz), 46.84)]})
    _bz = IBBroker(_tib_zn, registry=reg, settle_s=0.0, timeout_s=1.0)
    try:
        _bz.gross(None)
        bad.append('gross: без d_fix посчитал плечо ноги Б — единица ZN подставлена молча')
    except BrokerError:
        pass
    except Exception:
        pass
    # ЗНАЧЕНИЕ, А НЕ ФАКТ ВЫЗОВА (тридцать восьмой круг, №7, вторая часть). Проверять, что
    # gross «отработал и вернул конечное число», недостаточно: и середина полосы вернула бы
    # конечное число, и обе величины лежат ВНУТРИ полосы, поэтому внутренняя сверка их не
    # различает. Пересчитываем экспозицию НЕЗАВИСИМО по формуле норматива, взяв d_fix=12 —
    # там, где формула и середина расходятся более чем в полтора раза.
    # Дискриминатор выбран так, чтобы не тянуть за собой пересчёт всей формулы: модельная
    # единица ноги Б ПРОПОРЦИОНАЛЬНА d_fix, а середина полосы от него не зависит вовсе.
    # Значит удвоение d_fix обязано удвоить плечо; у подменённой реализации отношение 1.
    try:
        _g6, _g12 = float(_bz.gross(6.0)), float(_bz.gross(12.0))
        if not _g6 or abs(_g12 / _g6 - 2.0) > 1e-9:
            bad.append(f'gross: удвоение d_fix дало отношение {(_g12 / _g6) if _g6 else 0:.4f} '
                       f'вместо 2 — единица ноги Б считается НЕ по d_fix книги (середина '
                       f'полосы от d_fix не зависит и занижает плечо примерно на треть)')
    except BrokerError as _exv:
        bad.append(f'gross(d_fix): вызов отвергнут ({_exv}) — плечо ноги Б неизмеримо')
    except Exception as _exv2:
        bad.append(f'gross(d_fix): проверка не выполнена ({_exv2}) — молча пропускать её '
                   f'нельзя, иначе регрессия к середине полосы останется зелёной')
    # УСПЕШНЫЙ ПУТЬ preview ИСПОЛНЯЕТСЯ, И ЗНАЧЕНИЯ СРАВНИВАЮТСЯ (тридцать девятый круг,
    # №12). Прежде здесь стоял брокер С ПУСТОЙ ПОЗИЦИЕЙ: margin_cushion() отдавал None, оба
    # вызова заканчивались ДО whatIfOrder, а проверялось лишь isinstance(..., bool) — живой
    # мог вернуть False, макет True, и SAME_API оставался зелёным. То есть ни успешный
    # положительный whatIf, ни привязка account, ни округление фьючерса, ни отказ по
    # недостатку запаса не наблюдались ничем. Даём живому позицию, чтобы запас считался, и
    # сверяем ЗНАЧЕНИЯ обоих брокеров на одном и том же плане.
    _tib_pv = ib_stub.StubIB(rows, nlv=1_000_000.0)
    _tib_pv.quote_px = 100.0
    _tib_pv._pos = {900004: 100.0}; _tib_pv._shown = dict(_tib_pv._pos)
    _tl_pv = IBBroker(_tib_pv, registry=reg, settle_s=0.0, timeout_s=1.0)
    _tm.prices.setdefault('CSPX', 700.0)
    _tm.preview_ok = True
    for _plan_pv, _want, _label in ((None, True, 'без плана'),
                                    ([('CSPX', 1.0)], True, 'просторный план'),
                                    ([('MESU26', 109.295)], True, 'дробный фьючерс')):
        try:
            _pl = _tl_pv.preview(_plan_pv)
        except Exception as _exp:
            bad.append(f'preview (живой, {_label}): вызов не проходит ({_exp}) — переход '
                       f'остановится на предпросмотре до OPEN')
            continue
        if _pl is not _want:
            bad.append(f'preview (живой, {_label}): {_pl!r} вместо {_want!r} — успешный путь '
                       f'предпросмотра не исполняется')
        try:
            _pm = _tm.preview(_plan_pv)
        except Exception as _exp:
            bad.append(f'preview (макет, {_label}): вызов не проходит ({_exp})')
            continue
        if not isinstance(_pm, bool) or not isinstance(_pl, bool):
            bad.append(f'preview ({_label}): не булево — живой {_pl!r}, макет {_pm!r}')
        elif _pl != _pm:
            bad.append(f'preview ({_label}): живой {_pl!r} против макета {_pm!r} — брокеры '
                       f'расходятся на одном и том же плане')
    # ОТКАЗ ПО СУЩЕСТВУ (не по молчанию whatIf): маржа тесная — переход обязан отложиться.
    _tib_pv.whatif = 'тесно'
    if _tl_pv.preview([('CSPX', 1.0)]):
        bad.append('preview: тесная маржа цели принята за разрешение — перевод денег при '
                   'запасе ниже норматива О-3-Е')
    # АВАРИЙНЫЙ ВЫХОД Е→Ф ПРИ ПРОБИТОМ О-3-Е (сорок второй круг, №4). Ветка, ради которой
    # существует весь путь спасения, не исполнялась НИКЕМ: стаб умел только положительную
    # маржу, и «все приращения отрицательны при запасе 1,20» не достигалось ни здесь, ни
    # мутациями. Разгрузку маржи запрещать нельзя — иначе трижды POSTPONED и ABORT ровно
    # тогда, когда книгу надо спасать.
    _tib_em = ib_stub.StubIB(rows, nlv=1_000_000.0)
    _tib_em.quote_px = 100.0
    _tib_em.behaviour = 'thin_cushion'          # запас 1,20 < 1,40
    _tib_em._pos = {900004: 100.0}; _tib_em._shown = dict(_tib_em._pos)
    _tib_em.set_bars({990001: [(str(_pz - _dtz.timedelta(days=1)), 46.9), (str(_pz), 46.84)]})
    _tl_em = IBBroker(_tib_em, registry=reg, settle_s=0.0, timeout_s=1.0)
    _tib_em.whatif = 'освобождает'
    # ЗАМЕР И ПИН — СВОИ, ИЗОЛИРОВАННЫЕ (инцидент 19.08.2026, §12). С 44-го круга (№2)
    # разгрузка подтверждается ИЗМЕРЕННОЙ маржой, то есть путь ведёт в _live_margins, а тот
    # требует пинованного счёта, действующего реестра и полного покрытия его FUT-серий.
    # В батарее ADDFUT_LOCK_DIR уводится на временный каталог (правило 5), пина там нет —
    # и проверка отказывала НЕ по существу, а по отсутствию машинного состояния: батарея
    # краснела, а SAME_API поодиночке был зелен. Стенд обязан приносить своё состояние.
    _keep_em = {_k: os.environ.get(_k) for _k in
                ('ADDFUT_MARGINS', 'ADDFUT_ACCOUNT', 'ADDFUT_REGISTRY')}
    os.environ['ADDFUT_MARGINS'] = str(ib_stub.fixture_margins(
        os.path.dirname(str(reg)), account=_tib_em.managedAccounts()[0]))
    os.environ['ADDFUT_ACCOUNT'] = _tib_em.managedAccounts()[0]
    os.environ['ADDFUT_REGISTRY'] = str(reg)
    try:
        _em_ok = _tl_em.preview([('ZNU26', 10)], emergency=True)
    finally:
        for _k, _v in _keep_em.items():
            if _v is None:
                os.environ.pop(_k, None)
            else:
                os.environ[_k] = _v
    if not _em_ok:
        bad.append('preview: план, ОСВОБОЖДАЮЩИЙ маржу, отвергнут при запасе ниже О-3-Е — '
                   'аварийный выход Е→Ф заперт ровно тогда, когда он нужен')
    _tib_pv.whatif = ''
    # ПУСТОЙ ОТВЕТ whatIf — ОТСРОЧКА, А НЕ РАЗРЕШЕНИЕ (№4): проверяем именно живой путь.
    _tib_q = ib_stub.StubIB(rows); _tib_q.quote_px = 100.0
    _tib_q._pos = {900004: 100.0}; _tib_q._shown = dict(_tib_q._pos)
    _tib_q.whatif = 'нет'
    if IBBroker(_tib_q, registry=reg, settle_s=0.0, timeout_s=1.0).preview([('CSPX', 1.0)]):
        bad.append('preview: молчащий whatIf принят за разрешение — перевод денег вслепую')

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
