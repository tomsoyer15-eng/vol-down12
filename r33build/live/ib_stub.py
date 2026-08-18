#!/usr/bin/env python3
"""Подставной интерфейс ib_insync: живой адаптер под теми же проверками, что расчётчик.

ЗАЧЕМ. Шестая рецензия указала на измеренную дыру: перебор состояний и мутации гоняли
только daily.py через FakeBroker, а live/ib_broker.py — код, который РЕАЛЬНО ходит на биржу
— не проверялся ничем. Поэтому несовместимость контракта отмены, усечение дробных долей,
сопоставление отчётов об исполнении, устаревший NLV и подмена контракта имели гарантированно
нулевую наблюдаемость: заявление «все мутации ловятся» относилось к чистому расчётчику и
ничего не говорило о денежно опасной границе с IBKR.

ЧТО ИМЕННО ВОСПРОИЗВОДИТСЯ. Не «брокер вообще», а конкретные наблюдённые повадки IBKR:
  'cancelled_but_filled' — статус Cancelled при СОСТОЯВШЕМСЯ исполнении (наблюдено 12.08.2026);
  'stale_positions'      — позиции отстают на один запрос (из-за этого сверка при
                           восстановлении читала ноль и объявляла книгу восстановленной);
  'foreign_fill'         — в отчётах лежит чужое исполнение с тем же orderId, но другим
                           permId: проверка, что сопоставление идёт не по номеру заявки;
  'late_fills'           — отчёт о сделке приходит ТОЛЬКО по reqExecutions: проверка,
                           что исход определяется барьером, а не длиной паузы;
  'other_account'        — на другом managed account лежит позиция по тому же контракту;
  'stale_twice'          — позиции устарели УСТОЙЧИВО: два подряд снимка совпадают и оба лгут.
                           Совпадение снимков не барьер, и подстановка обязана это показывать,
                           иначе проверка воспроизводит алгоритм, а не поведение биржи;
  'fill_after_end'       — исполнение приходит ПОСЛЕ конца выгрузки (execDetailsEnd): барьер
                           завершился, а сделка ещё не была известна брокеру;
  'partial', 'reject', 'disconnect', 'stale_nlv', 'wrong_contract'.

Подстановка НЕ моделирует биржу и не претендует на это. Она задаёт ровно те исходы, на
которых адаптер обязан вести себя определённым образом, и позволяет их перебирать.
"""
import csv
import itertools
from pathlib import Path
from typing import NamedTuple

# ФИКСТУРА РЕЕСТРА. Проверки адаптера НЕ ДОЛЖНЫ зависеть от instruments_live.csv: тот файл
# принадлежит конкретному счёту, меняется каждый квартал вместе с con_id и в пакет не входит.
# Первая же сборка это и показала — распакованный архив провалил проверки, работавшие в
# рабочем каталоге. Здесь набор фиксирован и самодостаточен.
# primary_exchange — ВО ВСЕХ строках (двадцать второй круг, №24): FIXTURE_COLS строится по
# первой строке, и без этого поля ветка mismatches() для листинговой линии LSEETF/EBS в
# прогонах живого адаптера не исполнялась НИКОГДА — регрессия отправила бы заявку на
# рынок с другим календарём, и GTC осталась бы на ночь.
FIXTURE_ROWS = [
    dict(instrument='ESU26', sec_type='FUT', pair_group='EQ', exchange='CME', currency='USD',
         con_id='900001', local_symbol='ESU6', expiry='20260918', multiplier='50',
         primary_exchange='CME', isin=''),
    dict(instrument='MESU26', sec_type='FUT', pair_group='EQ', exchange='CME', currency='USD',
         con_id='900002', local_symbol='MESU6', expiry='20260918', multiplier='5',
         primary_exchange='CME', isin=''),
    dict(instrument='ZNU26', sec_type='FUT', pair_group='BOND', exchange='CBOT', currency='USD',
         con_id='900003', local_symbol='ZNU6', expiry='20260921', multiplier='1000',
         primary_exchange='CBOT', isin=''),
    # ОБЕ серии, как в боевом реестре (first_connect пишет U и Z): ролловые кейсы требуют
    # ориентиров дальней серии — фикстура без Z26 валила их гвардом «нет ориентира».
    dict(instrument='ESZ26', sec_type='FUT', pair_group='EQ', exchange='CME', currency='USD',
         con_id='900006', local_symbol='ESZ6', expiry='20261218', multiplier='50',
         primary_exchange='CME', isin=''),
    dict(instrument='MESZ26', sec_type='FUT', pair_group='EQ', exchange='CME', currency='USD',
         con_id='900007', local_symbol='MESZ6', expiry='20261218', multiplier='5',
         primary_exchange='CME', isin=''),
    dict(instrument='ZNZ26', sec_type='FUT', pair_group='BOND', exchange='CBOT', currency='USD',
         con_id='900008', local_symbol='ZNZ6', expiry='20261221', multiplier='1000',
         primary_exchange='CBOT', isin=''),
    # У IBKR фонды — 'STK': литерал 'ETF' в фикстуре скрывал от стендов дефект №14.
    # ISIN у STK обязателен (пятнадцатый круг, №6) — как в боевом реестре first_connect;
    # фьючерсам ISIN не положен, пустое поле для них штатно.
    dict(instrument='CSPX', sec_type='STK', pair_group='EQ', exchange='SMART', currency='USD',
         con_id='900004', local_symbol='CSPX', expiry='', multiplier='',
         primary_exchange='LSEETF', isin='IE00B5BMR087'),
    dict(instrument='CBU0', sec_type='STK', pair_group='BOND', exchange='SMART', currency='USD',
         con_id='900005', local_symbol='CSBGU0', expiry='', multiplier='',
         primary_exchange='EBS', isin='IE00B3VWN518'),
]
FIXTURE_COLS = list(FIXTURE_ROWS[0])


def fixture_registry(dirpath, rows=None):
    """Записать фикстуру реестра во временный каталог и вернуть путь.

    rows — необязательная ЗАМЕНА строк (тридцать первый круг, №8): согласованную подмену
    серии (имя ESU26 при полях ESZ26) нельзя изобразить поведением стаба, потому что
    подмена обязана быть согласованной И в реестре, И в ответе биржи; иначе её ловит
    mismatches, и стенд доказывал бы соседнюю защиту."""
    p = Path(dirpath) / 'instruments_fixture.csv'
    with open(p, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=FIXTURE_COLS)
        w.writeheader()
        for r in (rows if rows is not None else FIXTURE_ROWS):
            w.writerow(r)
    return p


class _OrderStatus:
    def __init__(self):
        self.status = 'PendingSubmit'; self.filled = 0.0; self.avgFillPrice = 0.0


class _Trade:
    def __init__(self, contract, order):
        self.contract = contract; self.order = order
        self.orderStatus = _OrderStatus(); self.log = []


class _Exec:
    def __init__(self, orderId, permId, shares, side, acct, orderRef='ADDFUT'):
        self.orderId = orderId; self.permId = permId; self.shares = shares
        self.side = side; self.acctNumber = acct; self.price = 100.0
        # Метка стратегии на ИСПОЛНЕНИИ — как у настоящего шлюза (семнадцатый круг, №3):
        # барьер todays_executions фильтрует отчёты по ней.
        self.orderRef = orderRef


class _Commission:
    """execId — признак ПРИШЕДШЕГО отчёта (девятнадцатый круг, №16): у настоящего шлюза
    commissionReport с пустым execId означает «ещё не пришёл», и нулём его считать нельзя."""
    def __init__(self, c, exec_id='X1'):
        self.commission = c; self.execId = exec_id


class _Fill:
    def __init__(self, contract, execution, commission=2.0):
        self.contract = contract; self.execution = execution
        self.commissionReport = (commission if isinstance(commission, _Commission)
                                 else _Commission(commission))


class _Bar(NamedTuple):
    """Именованный кортеж, а не свободный объект: util.df собирает кадр из записей."""
    date: object
    open: float
    high: float
    low: float
    close: float
    volume: float
    average: float
    barCount: int


class _WhatIf:
    """Ответ whatIfOrder. Пустая строка — именно то, что отдаёт бумажный шлюз, когда
    предпросмотр недоступен: адаптер обязан читать это как «отложить», а не как ноль."""

    def __init__(self, init_margin):
        self.initMarginChange = '' if init_margin is None else f'{float(init_margin):.2f}'
        self.maintMarginChange = self.initMarginChange
        self.equityWithLoanChange = ''


class _Pos:
    def __init__(self, contract, position, account='DU000001'):
        self.contract = contract; self.position = position; self.account = account


class _Val:
    def __init__(self, tag, value, currency='USD'):
        self.tag = tag; self.value = value; self.currency = currency


class StubIB:
    """Минимальный ib_insync, достаточный для IBBroker и только для него."""

    def __init__(self, rows, behaviour='normal', positions=None, nlv=1_000_000.0,
                 account='DU000001'):
        self.rows = {int(r['con_id']): r for r in rows}
        self.behaviour = behaviour
        self._pos = dict(positions or {})          # con_id -> количество
        self._nlv = float(nlv)
        self._acct = account
        self._trades = []
        self._fills = []
        self._ids = itertools.count(1)
        self._perm = itertools.count(1000)
        self._pos_reads = 0
        self._shown = dict(self._pos)              # что «видно» снаружи

    # --- инфраструктура ---
    def managedAccounts(self): return [self._acct]
    def isConnected(self): return self.behaviour != 'disconnect_link'
    def sleep(self, s): pass
    def waitOnUpdate(self, timeout=None): pass
    def reqAllOpenOrders(self):
        # 'orders_req_fails': соединение живо, но КОНКРЕТНЫЙ запрос заявок падает —
        # проверка API этот случай не исключает (шестнадцатый круг, №2).
        if self.behaviour == 'orders_req_fails':
            raise RuntimeError('запрос открытых заявок оборвался')
    def reqAccountUpdates(self, acct=None): pass
    def orders(self): return [t.order for t in self._trades]

    def reqExecutions(self, execFilter=None):
        # 'fill_after_end': ПЕРВЫЙ барьер завершается БЕЗ отчёта — сделка ещё не известна
        # брокеру. Отчёт появляется только на следующем запросе, то есть уже после того,
        # как контур счёл исход определённым.
        if self.behaviour == 'fill_after_end':
            self._exec_calls = getattr(self, '_exec_calls', 0) + 1
            if self._exec_calls <= 1:
                return list(self._fills)
        """Барьер отчётов об исполнении: возвращает всё, что известно брокеру.

        При 'late_fills' отчёт о СОСТОЯВШЕЙСЯ сделке доходит ТОЛЬКО по этому запросу — так
        проверяется, что исход заявки определяется барьером, а не тем, что успело
        разнестись за паузу. Без запроса сделки как будто нет, хотя позиция уже изменилась.
        """
        self._fills.extend(getattr(self, '_pending', []))
        self._pending = []
        return list(self._fills)

    STALE_READS = {'stale_positions': 1, 'stale_twice': 3}

    def reqPositions(self, *a, **k):
        # ПОЗИЦИИ ОТСТАЮТ. 'stale_positions' — на один запрос; 'stale_twice' — на три, то
        # есть ДВА ПОДРЯД снимка совпадают и оба устарели. Второй случай показывает предел
        # возможного: на уровне адаптера устойчиво устаревшая картина неотличима от верной,
        # и доказывать надо не её распознавание, а то, что расхождение поймает следующая
        # сессия и остановится.
        self._pos_reads += 1
        if self._pos_reads > self.STALE_READS.get(self.behaviour, 0):
            self._shown = dict(self._pos)
        return self.positions()

    def positions(self, account=None):
        """Позиции СВОЕГО счёта плюс, при 'other_account', чужого. Прежняя подстановка
        моделировала ровно один счёт и потому по построению не могла показать смешение."""
        out = [_Pos(self._contract_of(cid), q, self._acct)
               for cid, q in self._shown.items() if q]
        if self.behaviour == 'other_account':
            cid = next(iter(self.rows))
            out.append(_Pos(self._contract_of(cid), 26.0, 'DU999999'))
        return out

    def accountValues(self, account=None):
        nlv = self._nlv * (0.5 if self.behaviour == 'stale_nlv' else 1.0)
        return [_Val('NetLiquidation', f'{nlv:.2f}')]

    def reqAccountSummary(self):
        """Свежий request/end-барьер сводки (девятнадцатый круг, №4): как у ib_insync,
        новый запрос отдаёт ТЕКУЩИЕ значения сервера. Без него 'stale_nlv' продолжает
        отдавать доторговый кэш подписки."""
        self._summary_fresh = True

    def accountSummary(self, account=None):
        """Сводка КАК У ib_insync (девятнадцатый круг, №4): после первого запроса отдаётся
        кэш подписки; при 'stale_nlv' он устарел (NLV вдвое меньше), и свежее значение даёт
        только reqAccountSummary(). Свежесть потребляется чтением: каждый следующий вызов
        без нового барьера снова читает кэш. Отдаются и теги запаса О-3-Е; при
        'thin_cushion' запас 1,20 — ниже порога 1,40."""
        _fresh = getattr(self, '_summary_fresh', False)
        self._summary_fresh = False
        if self.behaviour == 'stale_nlv' and _fresh:
            out = [_Val('NetLiquidation', f'{self._nlv:.2f}')]
        else:
            out = self.accountValues(account)
        # 'nan_cushion': шлюз временно отдаёт NaN в тегах запаса (семнадцатый круг, №7).
        if self.behaviour == 'nan_cushion':
            out.append(_Val('EquityWithLoanValue', 'nan'))
            out.append(_Val('MaintMarginReq', 'nan'))
            return out
        ewl = self._nlv
        # Требование при ЖИВЫХ позициях ненулевое, как у настоящего шлюза (семнадцатый
        # круг, №7): нулевой maint при существующих позициях — неполный ответ, и сессия Е
        # обязана отказывать, а не брать прокси; пустой счёт честно отдаёт ноль.
        # 'thin_after' — запас тонкий ТОЛЬКО когда позиции уже есть (пост-трейд путь,
        # девятнадцатый круг, №10 / восемнадцатый, №1); 'no_maint' — требование не
        # возвращается вовсе (неполный ответ после исполнения).
        if self.behaviour == 'inf_maint':
            # ТРИДЦАТЬ ТРЕТИЙ КРУГ, №13: сводка отдаёт бесконечное требование. Прежде
            # проверялся только NaN: inf даёт cushion РОВНО 0, и вахта продала бы половину
            # книги по ошибке сводки, а не по факту рынка.
            out.append(_Val('EquityWithLoanValue', f'{self._nlv:.2f}'))
            out.append(_Val('MaintMarginReq', 'inf'))
            return out
        if self.behaviour == 'neg_maint':
            # ТРИДЦАТЬ ТРЕТИЙ КРУГ, №13: отрицательное требование даёт отрицательный
            # cushion — тот же ложный приказ на ликвидацию.
            out.append(_Val('EquityWithLoanValue', f'{self._nlv:.2f}'))
            out.append(_Val('MaintMarginReq', '-1000.00'))
            return out
        if self.behaviour == 'thin_cushion':
            maint = self._nlv / 1.2
        elif self.behaviour == 'no_maint':
            maint = 0.0
        elif self.behaviour == 'thin_after':
            maint = self._nlv / 1.2 if any(self._pos.values()) else 0.0
        elif self.behaviour == 'thin_after_ok':
            # ТРИДЦАТЬ ПЕРВЫЙ КРУГ, №2: запас тонок при ПЕРВОМ пост-трейд замере и
            # ВОССТАНАВЛИВАЕТСЯ после сокращения — то есть штатный исход норматива §8.
            # Прежний 'thin_after' держал 1,20 навсегда, поэтому производственная ветка
            # «срез удался, запас выше порога» не исполнялась ни разу, а тревога в ней
            # не ставилась вовсе: аварийное сокращение проходило молча, и следующая
            # сессия полосой наращивала книгу обратно к 2x.
            if any(self._pos.values()):
                self._thin_calls = getattr(self, '_thin_calls', 0) + 1
                maint = self._nlv / (1.2 if self._thin_calls == 1 else 2.5)
            else:
                maint = 0.0
        elif any(self._pos.values()):
            maint = self._nlv / 2.0
        else:
            maint = 0.0
        out.append(_Val('EquityWithLoanValue', f'{ewl:.2f}'))
        out.append(_Val('MaintMarginReq', f'{maint:.2f}'))
        # ДЕЙСТВУЮЩЕЕ НАЧАЛЬНОЕ ТРЕБОВАНИЕ (сороковой круг, №1): без него оценка _cur_req в
        # preview() не наблюдалась НИ ОДНИМ стендом — сверка «худшая из трёх» проверяла две.
        out.append(_Val('FullInitMarginReq', f'{maint * 1.4:.2f}'))
        return out

    def fills(self): return list(self._fills)
    def openTrades(self):
        return [t for t in self._trades if t.orderStatus.status in ('PendingSubmit', 'Submitted')]

    # --- контракты ---
    def _contract_of(self, cid):
        from ib_insync import Contract
        r = self.rows.get(cid, {})
        return Contract(conId=cid, secType=r.get('sec_type', 'FUT'),
                        symbol=r.get('instrument', ''), localSymbol=r.get('local_symbol', ''),
                        exchange=r.get('exchange', ''), currency=r.get('currency', 'USD'),
                        lastTradeDateOrContractMonth=str(r.get('expiry', '')),
                        multiplier=str(r.get('multiplier', '')))

    def reqContractDetails(self, c):
        """Личность по бирже: ISIN отдаётся в secIdList — как настоящий шлюз. Строка без
        ISIN (фьючерс) даёт пустой список тегов; verify_isin до этого вызова для FUT и не
        доходит, но полнота ответа — забота стаба, не проверяющего."""
        r = self.rows.get(getattr(c, 'conId', 0))
        if r is None:
            return []

        class _Tag:
            tag = 'ISIN'
            value = (r.get('isin') or '').strip()

        class _Det:
            secIdList = [_Tag()] if _Tag.value else []
        return [_Det()]

    # ДОХОДНОСТЬ ДОЛЖНА РАЗРЕШАТЬСЯ И БЕЗ ПОДМЕНЫ ib_insync.Index (тридцать восьмой круг,
    # №7). Прогонные стенды патчат Index своим классом, а SAME_API — нет, и любая проверка,
    # где gross() трогает ногу Б, молча падала в FeedError «TNX: история пуста» и
    # пропускалась целиком. Проверка, которую можно бесшумно пропустить, — не проверка.
    AUX = {'SPY': 900010, 'IEF': 900011, 'TNX': 990001}   # сигналы, цена ноги А, доходность
    AUX_TYPE = {'TNX': 'IND'}                # TNX — индекс, а не акция

    def reqMktData(self, contract, tick_list='', snapshot=True, regulatory=False):
        """Снимок котировки (двадцать второй круг, №20): px_order берётся из момента
        заявки. По умолчанию котировки НЕТ (nan) — адаптер честно падает на запасной
        prev-close, и прежние стенды видят прежние ориентиры. Повадка задаётся полем
        quote_px: стенд котировочного ориентира выставляет цену явно."""
        class _T:
            pass
        t = _T()
        # ПО УМОЛЧАНИЮ КОТИРОВКА ЕСТЬ, КАК НА БИРЖЕ (двадцать третий круг, №23): пустой
        # снимок — исключение, а не норма, и фикстура, где его нет никогда, заставляла бы
        # §7 всегда исключать строки из выборки. Берём последний бар инструмента; стенд
        # запасного пути ставит quote_px=None явно.
        q = getattr(self, 'quote_px', 'нет')
        if q == 'нет':
            # _bars МОЖЕТ НЕ СУЩЕСТВОВАТЬ (двадцать пятый круг, №16): set_bars зовут не
            # все стенды, и обращение к атрибуту роняло _quote_ref в AttributeError —
            # живой адаптер молча отдавал px_order_live=False там, где макет отдавал True,
            # а SAME_API сравнивал только набор ключей и объявлял их одинаковыми.
            _b = (getattr(self, '_bars', None) or {}).get(getattr(contract, 'conId', 0)) or []
            q = float(_b[-1][1]) if _b else None
        t.last = q if q is not None else float('nan')
        t.close = float('nan')
        t.bid = float('nan')
        t.ask = float('nan')
        # ТИП ДАННЫХ ОТДАЁТСЯ, КАК У ЖИВОГО ТИКЕРА (тридцать третий круг, №12). Стаб не
        # выставлял marketDataType вовсе, поэтому УСПЕШНАЯ ветка признака «котировка
        # реального времени» не исполнялась ни разу: возврат боевого условия к
        # `_mdt in (None, 1)` не изменил бы ни одного утверждения, а в бою delayed-fallback
        # снова помечался бы как live и доказывал издержки движением рынка.
        # 1 — реальное время, 3 — задержанные; повадка задаётся полем md_type.
        t.marketDataType = getattr(self, 'md_type', 3)
        return t

    def qualifyContracts(self, *cs):
        for c in cs:
            if not getattr(c, 'conId', 0) and getattr(c, 'symbol', '') in self.AUX:
                # Запрос по символу (Stock('SPY'...)): стендам нужен тот же путь, что бою.
                c.conId = self.AUX[c.symbol]
                self.rows.setdefault(c.conId, dict(
                    instrument=c.symbol, sec_type=self.AUX_TYPE.get(c.symbol, 'STK'),
                    exchange=('CBOE' if self.AUX_TYPE.get(c.symbol) == 'IND' else 'SMART'),
                    currency='USD',
                    con_id=str(c.conId), local_symbol=c.symbol, expiry='', multiplier=''))
            r = self.rows.get(c.conId)
            if r is None:
                c.conId = 0; continue
            c.secType = r.get('sec_type', 'FUT')
            c.symbol = r.get('instrument', '')
            c.localSymbol = r.get('local_symbol', '')
            c.exchange = r.get('exchange', '')
            c.currency = r.get('currency', 'USD')
            c.lastTradeDateOrContractMonth = str(r.get('expiry', ''))
            c.multiplier = str(r.get('multiplier', ''))
            # ЛИСТИНГОВАЯ ЛИНИЯ (двадцать второй круг, №24): фикстура теперь несёт
            # primary_exchange, значит стаб обязан отдавать его как биржа — иначе честная
            # сверка mismatches валила бы все адаптерные стенды «площадка не подтверждена».
            c.primaryExchange = r.get('primary_exchange', '') or ''
            if self.behaviour == 'etf_line_swap':
                # ТРИДЦАТЬ ТРЕТИЙ КРУГ, №4: СОГЛАСОВАННАЯ подмена фонда — другая
                # листинговая линия с её настоящими полями. Строка реестра и ответ биржи
                # сходятся между собой; лжёт только ИМЯ, и уличить его может лишь
                # независимое пинованное ожидание.
                if r.get('sec_type') == 'STK':
                    c.primaryExchange = 'AEB'
            if self.behaviour == 'wrong_contract':
                # con_id указывает на ДРУГУЮ поставку: адаптер обязан отказать, а не подать.
                c.lastTradeDateOrContractMonth = '20991231'
                c.localSymbol = 'ЧУЖОЙ'
        return list(cs)

    # --- история ---
    def set_bars(self, bars, what=None):
        """bars: con_id -> [(дата, закрытие), ...]. Сборщик входов проверяет ДАТЫ баров,
        и без подстановки истории эта проверка не исполнялась ни разу.
        what — необязательный срез источника ('TRADES', 'ADJUSTED_LAST'): сверка свежего
        месяца (шестнадцатый круг, №3) читает ДВА среза; без отдельной подстановки TRADES
        отдаются те же бары — штатный случай совпадения."""
        if what is None:
            self._bars = dict(bars)
        else:
            if not hasattr(self, '_bars_what'):
                self._bars_what = {}
            self._bars_what[what] = dict(bars)

    def reqHistoricalData(self, contract, endDateTime='', durationStr='', barSizeSetting='',
                          whatToShow='', useRTH=True, **kw):
        import pandas as pd
        rows = getattr(self, '_bars_what', {}).get(whatToShow, {}).get(contract.conId)
        if rows is None:
            rows = getattr(self, '_bars', {}).get(contract.conId, [])
        out = []
        for d, c in rows:
            v = float(c)
            out.append(_Bar(pd.Timestamp(d).date(), v, v, v, v, 0.0, v, 1))
        return out

    # --- предпросмотр маржи ---
    def whatIfOrder(self, contract, order):
        """ПРЕДПРОСМОТР ЦЕЛЕВОЙ ЗАЯВКИ (тридцать седьмой круг, №7 и его проверка).

        Стаба этого метода не было, и сверка интерфейса адаптера честно уронила батарею:
        новый путь preview(orders) был бы недостижим ни одним стендом — ровно тот класс,
        из-за которого переходный интерфейс вообще появился на разборе (§12, 36-й круг, №6).

        Поведение задаётся полем whatif:
          'нет'    — пустой ответ (бумажный whatIf днём молчит, §12) -> «отложить»;
          'тесно'  — маржа заведомо не оставляет запаса О-3-Е;
          иначе    — начальная маржа 10% от номинала, запас достаточный.
        """
        _mode = getattr(self, 'whatif', '')
        if _mode == 'нет':
            return _WhatIf(None)
        # БИРЖЕВАЯ ГРАНИЦА ВОСПРОИЗВОДИТСЯ (тридцать восьмой круг, №5): стаб принимал
        # дробное количество фьючерса и потому не показывал, что законный переход уходит в
        # ABORT из-за нецелой what-if заявки. Дробить можно только фонды.
        _q0 = abs(float(getattr(order, 'totalQuantity', 0) or 0))
        if getattr(contract, 'secType', '') == 'FUT' and abs(_q0 - round(_q0)) > 1e-9:
            return _WhatIf(None)
        # ОТРИЦАТЕЛЬНЫЕ ПРИРАЩЕНИЯ (сорок второй круг, №4). Стаб умел только положительную
        # маржу, «тесно» и пустой ответ — поэтому аварийный выход Е→Ф, где целевые заявки
        # маржу ОСВОБОЖДАЮТ, не достигался ни SAME_API, ни мутациями: ветка, ради которой
        # существует весь путь спасения, не исполнялась никем.
        if _mode == 'освобождает':
            _q0n = abs(float(getattr(order, 'totalQuantity', 0) or 0))
            _pxn = float(getattr(self, 'quote_px', 100.0) or 100.0)
            return _WhatIf(-_q0n * _pxn * 0.10)
        if _mode == 'тесно':
            # запас NLV/маржа = 1,20 — ниже порога О-3-Е 1,40, то есть отказ по существу,
            # а не по отсутствию ответа: два разных исхода не должны совпадать.
            return _WhatIf(float(self._nlv) / 1.20)
        _q = abs(float(getattr(order, 'totalQuantity', 0) or 0))
        _px = float(getattr(self, 'quote_px', 100.0) or 100.0)
        _mult = float(getattr(contract, 'multiplier', '') or 1)
        return _WhatIf(_q * _px * _mult * 0.10)

    # --- заявки ---
    def placeOrder(self, contract, order):
        tr = _Trade(contract, order)
        order.orderId = next(self._ids)
        order.permId = next(self._perm)
        self._trades.append(tr)
        want = float(order.totalQuantity) * (1 if order.action == 'BUY' else -1)
        b = self.behaviour
        if b == 'reject':
            tr.orderStatus.status = 'Inactive'; return tr
        if b == 'disconnect':
            tr.orderStatus.status = 'Submitted'; return tr
        done = want
        if b == 'partial':
            done = want * 0.5
        if b == 'foreign_fill':
            # Чужое исполнение с ТЕМ ЖЕ номером заявки, но другим permId и контрактом.
            self._fills.append(_Fill(self._contract_of(next(iter(self.rows))),
                                     _Exec(order.orderId, 999999, 77.0, 'BOT', self._acct)))
        self._pos[contract.conId] = self._pos.get(contract.conId, 0) + done
        f = _Fill(contract, _Exec(order.orderId, order.permId, abs(done),
                                  'BOT' if done > 0 else 'SLD', self._acct,
                                  orderRef=getattr(order, 'orderRef', '') or ''),
                  commission=(_Commission(2.0, exec_id='')
                              if b == 'no_commission_report' else 2.0))
        if b in ('late_fills', 'late_cancelled', 'fill_after_end'):
            self._pending = getattr(self, '_pending', []) + [f]
        else:
            self._fills.append(f)
        # 'no_avg_price': статусная средняя цена отстаёт (нулевая), правда — в отчётах
        # (девятнадцатый круг, №16): цена исполнения обязана браться из exec-отчётов.
        tr.orderStatus.avgFillPrice = 0.0 if b == 'no_avg_price' else 100.0
        if b == 'late_cancelled':
            # ХУДШИЙ НАБЛЮДЁННЫЙ СЛУЧАЙ ЦЕЛИКОМ: статус «отменена», собственный счётчик
            # заявки НУЛЕВОЙ, позиция изменена, а единственное свидетельство — отчёт о
            # сделке, доходящий только по reqExecutions. Запасной путь «взять filled из
            # статуса» здесь бесполезен, и исход определяет ровно барьер выгрузки.
            tr.orderStatus.filled = 0.0
            tr.orderStatus.status = 'Cancelled'
        else:
            tr.orderStatus.filled = abs(done)
            tr.orderStatus.status = 'Cancelled' if b == 'cancelled_but_filled' else 'Filled'
        return tr

    def cancelOrder(self, order):
        for t in self._trades:
            if t.order.orderId == order.orderId and t.orderStatus.status in ('PendingSubmit',
                                                                            'Submitted'):
                t.orderStatus.status = 'Cancelled'
