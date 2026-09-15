# -*- coding: utf-8 -*-
"""ИСПОЛНЕНИЕ перехода Ф->Е штатным transition.execute.

СОБРАНО 11.09.2026 ПОСЛЕ ТРЁХ МОИХ ОШИБОК В ВЫЗОВЕ — чтобы не вспоминать их заново:
 1. mr_engine.configure(путь) обязателен ДО любого обращения к журналу, иначе отказ
    «путь журнала не сконфигурирован» ещё до первой заявки;
 2. registry — УПАКОВАННЫЙ шаблон ролей r33build/instruments.csv (он и закреплён пином
    SHA-256). Живой реестр с conId адаптер читает сам через feed.registry(); подстановка
    живого файла сюда даёт отказ «подмена файла»;
 3. state_path — СВОЙ файл прогресса перехода, которого ещё нет. Подстановка книги даёт
    отказ по digest: по этому файлу считается, какие лоты завершены.
Дата берётся из системной, файл прогресса именуется по ней. Окно спрашивается у
feed.common_window — не у trade_till('E'), тот молчит в выходные и занижает край.
"""
import json, pathlib, sys
sys.path.insert(0, '/home/alex/claude-projects/vol-down12/r33build/live')
sys.path.insert(0, '/home/alex/claude-projects/vol-down12/r33build')
import datetime as dt
import json
from ib_insync import IB, Stock, Future
import ib_broker as IBB
import transition as T
import feed as FD
import mr_engine as M

# ПУТЬ ЖУРНАЛА ПИНУЕТСЯ В ПРОЦЕССЕ ДО ЛЮБОГО ОБРАЩЕНИЯ: transition читает журнал
# через mr_engine, а тот требует явной конфигурации — иначе отказ ДО первой заявки.
M.configure(str(pathlib.Path('/home/alex/claude-projects/vol-down12/r33build/mr_journal.csv')))

КОРЕНЬ = pathlib.Path('/home/alex/claude-projects/vol-down12/r33build')
счёт = pathlib.Path('~/.addfut/account.txt').expanduser().read_text().strip()

ib = IB(); ib.connect('127.0.0.1', 4002, clientId=74, timeout=30); ib.reqMarketDataType(3)
try:
    св = {t.tag: t.value for t in ib.accountSummary(счёт)}
    капитал = float(св['NetLiquidation'])
    # ВОЗОБНОВЛЕНИЕ ИДЁТ С КАПИТАЛОМ ИСХОДНОГО ЗАПУСКА, А НЕ С ТЕКУЩИМ (15.09.2026).
    # Идентификатор перехода считается от связки «сигнал+маршруты+КАПИТАЛ+план», а капитал
    # меняется непрерывно: после обрыва текущий NLV даёт ДРУГОЙ идентификатор, и исполнитель
    # честно отказывается трогать чужой открытый переход. Величина передаётся явно ключом
    # --kapital, чтобы она была ВИДНА в команде, а не подставлялась молча.
    for _i, _a in enumerate(sys.argv):
        if _a == '--kapital':
            капитал = float(sys.argv[_i + 1])
            print(f'капитал исходного запуска задан явно: {капитал:,.2f}')
    поз = {p.contract.localSymbol: p.position for p in ib.positions(счёт)}

    def закрытие(c):
        b = ib.reqHistoricalData(c, endDateTime='', durationStr='3 D',
                                 barSizeSetting='1 day', whatToShow='TRADES', useRTH=True)
        return b[-1].close
    es = Future(conId=515416632, exchange='CME'); ib.qualifyContracts(es)
    cspx = Stock(conId=76023663, exchange='SMART'); ib.qualifyContracts(cspx)
    p_es, p_cspx = закрытие(es), закрытие(cspx)
    # ПРИ ВОЗОБНОВЛЕНИИ НОГИ СТРОЯТСЯ ИЗ СНИМКА ПРОГРЕССА И ЦЕН ИСХОДНОГО ЗАПУСКА:
    # текущие позиции уже промежуточные (часть продана), и план из них дал бы ДРУГОЙ
    # идентификатор перехода — а по нему исполнитель и отличает свой переход от чужого.
    if '--resume' in sys.argv:
        _пр = json.loads(pathlib.Path(
            f'~/.addfut/perehod-{dt.date.today().isoformat()}.json').expanduser().read_text())
        _сн = _пр['snapshot']
        p_es = float(_пр['executed_usd']) / 50.0        # единица ES записана исполнением
        p_cspx = float(sys.argv[sys.argv.index('--cspx') + 1])
        поз = {'ESZ6': _сн['ESZ26'], 'MESZ6': _сн['MESZ26']}
        print(f'возобновление: снимок {_сн}, ES {p_es}, CSPX {p_cspx}')
    ноги = {'А': dict(src=[('ESZ26', int(поз.get('ESZ6', 0)), p_es*50),
                           ('MESZ26', int(поз.get('MESZ6', 0)), p_es*5)],
                      dst=('CSPX', p_cspx, 'ETF'))}
    сегодня = dt.date.today()
    a, b = FD.common_window(сегодня)
    сейчас = dt.datetime.now(a.tzinfo)
    в_окне = bool(a <= сейчас <= b)
    print(f'капитал {капитал:,.2f}; окно открыто: {в_окне}')
    if not в_окне:
        raise SystemExit('окно закрыто — исполнение отменено')

    бр = IBB.IBBroker(ib, account=счёт)
    итог = T.execute(
        бр,
        # ФАЙЛ ПРОГРЕССА ПЕРЕХОДА — СВОЙ И НОВЫЙ, а не книга: исполнитель создаёт его
        # сам и считает по нему, какие лоты завершены (стенды передают заведомо
        # несуществующий путь). Подстановка книги дала отказ по digest — мой промах.
        str(pathlib.Path(f'~/.addfut/perehod-{сегодня.isoformat()}.json').expanduser()),
        капитал, ноги,
        signal_id='2026-09-04#1', from_route='F', to_route='E',
        in_common_window=в_окне,
        # ВОЗОБНОВЛЕНИЕ ПОСЛЕ ОБРЫВА (15.09.2026): прогресс уже записан, и повтор
        # запрещён — какие лоты завершены, считается по файлу прогресса, а не по
        # памяти. Флагом управляет вызывающий: --resume.
        resume=('--resume' in sys.argv),
        journal=str(КОРЕНЬ / 'mr_journal.csv'),
        mr_state=str(КОРЕНЬ / 'mr_state.csv'),
        asof=сегодня.isoformat(),
        # РЕЕСТР ЗДЕСЬ — УПАКОВАННЫЙ ШАБЛОН РОЛЕЙ (ES/MES/ZN/CSPX/CBU0 с их
        # sec_type и биржей), он и закреплён пином SHA-256. Живой реестр с conId
        # читает сам адаптер через feed.registry(); подставить его сюда — отказ
        # «подмена файла», что и случилось первой попыткой.
        registry=str(КОРЕНЬ / 'instruments.csv'),
    )
    print('ИТОГ ИСПОЛНИТЕЛЯ:', итог)
finally:
    ib.disconnect()
