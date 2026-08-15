#!/usr/bin/env python3
"""Сценарные проверки боевого контура на фиктивном брокере.

Проверяется не «работает ли в норме», а поведение на КАЖДОМ неприятном пути: отказ
брокера, недобор, исполнение больше заявки, потеря связи, отказы §8 по размеру счёта и
гранулярности. Плюс журнал §7: что он пишет и когда отказывается делать вывод.
"""
import sys, tempfile
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'live'))
import sim_v13 as S
import os as _os_iso
import tempfile as _tf_iso
# ИЗОЛЯЦИЯ ОТ МАШИННОГО КАТАЛОГА: самостоятельный запуск писал lock-файл в настоящий
# ~/.addfut — тот же класс утечки, что затёр живую книгу из selfcheck.
_os_iso.environ.setdefault('ADDFUT_LOCK_DIR', _tf_iso.mkdtemp(prefix='addfut-ct-'))
import daily as DL, journal as J
from fake_broker import FakeBroker, BrokerError

OK = []


def chk(name, cond, detail=''):
    OK.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}{(': ' + detail) if detail else ''}")


d = S.build_modern()
days, re, rb, rfv, spy, dref = d[:6]
m = DL.Market(date=days[-1], px_eq_prev=spy[-2], dref_prev=dref[-2], dref_today=dref[-1],
              px_eq_today=spy[-1], roll_today=False, st_eq=True, st_bd=True)
book0 = DL.Book(d_fix=dref[-2])

# --- 1. штатное решение и раскладка в книгу брокера ---
dec = DL.step(book0, m, 10_000_000.0)
orders = DL.book_to_orders(dec, book0)
chk('штатная сессия: заявки сформированы', dec.trade and orders,
    ', '.join(f'{i} {q:+d}' for i, q in orders))
# Заявки теперь адресованы сериям (ESZ26, MESZ26), поэтому корень берётся из имени.
es = sum(q for i, q in orders if i.startswith('ES'))
mes = sum(q for i, q in orders if i.startswith('MES'))
chk('раскладка MES-сетки в книгу совпадает с map_mes',
    es * 10 + mes == dec.book_after.n_e, f'{es}×ES + {mes}×MES = {es*10+mes}')
chk('заявки первой сессии тоже адресованы серии, а не корню',
    all(i not in ('ES', 'MES', 'ZN') for i, _ in orders),
    ', '.join(f'{i}{q:+d}' for i, q in orders))

# --- 2. отказы §8 ---
chk('отказ ниже порога 3 млн', bool(DL.step(book0, m, 2_999_999.0).refusals))
chk('отказ по гранулярности на малом счёте',
    any('полос' in r for r in DL.step(book0, m, 400_000.0).refusals))
chk('на пороге ровно 3 млн заявка допускается', not DL.step(book0, m, 3_000_000.0).refusals)

# --- 3. поведение брокера ---
px = {'ES': spy[-1] * 10, 'MES': spy[-1] * 10, 'ZN': 112.0}
for beh, expect in (('normal', 'исполнено'), ('partial', 'недобор'),
                    ('overfill', 'исполнение больше заявки'), ('reject', 'отказ'),
                    ('disconnect', 'связь потеряна')):
    b = FakeBroker(prices=px, behaviour=beh)
    try:
        rec = b.place('ZN', 101)
        got = rec.get('incident', 'исполнено')
    except BrokerError as ex:
        got = 'отказ' if beh == 'reject' else 'связь потеряна'
    chk(f'брокер: {beh}', got == expect, got)

b = FakeBroker(prices=px, behaviour='disconnect')
try:
    b.place('ZN', 101)
except BrokerError:
    pass
# Ответ на снятие — СТРУКТУРНЫЙ ФАКТ, а не «да»: заявка могла успеть исполниться. Прежняя
# проверка требовала ровно True и потому молчала о том, что живой адаптер отдаёт другое.
_c = b.cancel_order(b.open_orders()[0]) if b.open_orders() else None
chk('после потери связи заявка остаётся открытой и снимается',
    isinstance(_c, dict) and _c.get('terminal') and _c.get('cancelled') and not _c.get('filled'))
_b2 = FakeBroker(prices=px)
_r2 = _b2.place('ZN', 5)
_c2 = _b2.cancel_order(_r2['order_id'])
chk('снятие ИСПОЛНИВШЕЙСЯ заявки не выдаётся за отмену',
    isinstance(_c2, dict) and _c2.get('terminal') and not _c2.get('cancelled')
    and _c2.get('filled') == 5)

# --- 3а. РОЛЛ: перенос серии, а не нетто-заявка ---
roll_m = DL.Market(date=pd.Timestamp('2026-08-26'), px_eq_prev=spy[-2], dref_prev=dref[-2],
                   dref_today=dref[-1], px_eq_today=spy[-1], roll_today=True,
                   st_eq=True, st_bd=True)
held = DL.Book(d_fix=dref[-2], n_e=260, n_b=101, unit_is_mes=True,
               prev_st_eq=True, prev_st_bd=True, ser_a='U26', ser_b='U26')
rd = DL.step(held, roll_m, 10_000_000.0, check_guards=False)
ro = DL.book_to_orders(rd, held)
chk('ролл: пара заявок на каждую ногу с позицией', len(rd.roll_pairs) == 2,
    '; '.join(f"{p['leg']}: {p['close'][0]}{p['close'][1]:+d} -> {p['open'][0]}{p['open'][1]:+d}"
              for p in rd.roll_pairs))
chk('ролл: серии сдвинулись на следующую поставку',
    rd.book_after.ser_b == 'Z26' and rd.book_after.ser_a == 'Z26',
    f'{rd.book_after.ser_a} / {rd.book_after.ser_b}')
chk('ролл: заявки адресованы КОНКРЕТНЫМ сериям, а не корню',
    all(o[0] not in ('ES', 'MES', 'ZN') for o in ro), ', '.join(f'{i}{q:+d}' for i, q in ro))
zn = [q for i, q in ro if i.startswith('ZN')]
chk('ролл: старая серия ZN закрывается целиком, новая открывается',
    sorted(zn) == sorted([-101, 101]), str(zn))

# ролл при НЕИЗМЕННОМ числе контрактов всё равно даёт заявки
chk('ролл: заявки возникают даже когда число контрактов не изменилось',
    rd.book_after.n_b == held.n_b and len(zn) == 2, f'{held.n_b} -> {rd.book_after.n_b}')

# ролл НЕ откатывается назавтра, признак trade его видит, пропуск ролла ловится
nxt = DL.step(rd.book_after, DL.Market(date=pd.Timestamp('2026-08-27'), px_eq_prev=spy[-2],
              dref_prev=dref[-2], dref_today=dref[-1], px_eq_today=spy[-1], roll_today=False,
              st_eq=True, st_bd=True), 10_000_000.0, check_guards=False)
chk('ролл: назавтра книга НЕ возвращается в старую серию',
    not nxt.roll_pairs and nxt.book_after.ser_b == 'Z26', f'серия {nxt.book_after.ser_b}')
chk('ролл: чистый перенос виден признаку trade',
    DL.Decision(date=pd.Timestamp('2026-08-26'), roll_pairs=[{'leg': 'Б'}]).trade)
miss = DL.step(DL.Book(d_fix=dref[-2], n_e=260, n_b=101, unit_is_mes=True, prev_st_eq=True,
               prev_st_bd=True, ser_a='U26', ser_b='U26'),
               DL.Market(date=pd.Timestamp('2026-09-10'), px_eq_prev=spy[-2], dref_prev=dref[-2],
               dref_today=dref[-1], px_eq_today=spy[-1], roll_today=False, st_eq=True, st_bd=True),
               10_000_000.0, check_guards=False)
# Норма ужесточена десятым-одиннадцатым кругами: пропущенный ролл не «обнаруживается
# отказом в поставочной зоне», а НАВЁРСТЫВАЕТСЯ немедленно по сроку серии.
chk('ролл: пропущенная сессия ролла НАВЁРСТЫВАЕТСЯ',
    miss.book_after.ser_a == 'Z26' and any('пропущен' in r for r in miss.reasons)
    and not miss.refusals, f'серия {miss.book_after.ser_a}')

# отказ §8 обязан пропускать снижение риска и блокировать наращивание
big = DL.Book(d_fix=dref[-2], n_e=260, n_b=101, unit_is_mes=True, prev_st_eq=True,
              prev_st_bd=True, ser_a='U26', ser_b='U26')
off = DL.step(big, DL.Market(date=days[-1], px_eq_prev=spy[-2], dref_prev=dref[-2],
              dref_today=dref[-1], px_eq_today=spy[-1], roll_today=False, st_eq=False,
              st_bd=False), 2_000_000.0)
chk('отказ §8 пропускает СОКРАЩЕНИЕ риска, а не замораживает книгу',
    bool(off.refusals) and off.book_after.n_e == 0 and off.book_after.n_b == 0,
    f'книга {off.book_after.n_e}/{off.book_after.n_b}')
up = DL.step(DL.Book(d_fix=dref[-2], ser_a='U26', ser_b='U26', prev_st_eq=False, prev_st_bd=False),
             DL.Market(date=days[-1], px_eq_prev=spy[-2], dref_prev=dref[-2], dref_today=dref[-1],
             px_eq_today=spy[-1], roll_today=False, st_eq=True, st_bd=True), 2_000_000.0)
chk('отказ §8 НЕ пропускает наращивание риска',
    bool(up.refusals) and up.book_after.n_e == 0 and up.book_after.n_b == 0)

# опасный путь: старая закрыта, новая не открылась
bb = FakeBroker(prices={'ZNU26': 112.0, 'ZNZ26': 112.5}, behaviour='normal')
bb.place('ZNU26', -101)
bb.behaviour = 'reject'
try:
    bb.place('ZNZ26', 101); gap = False
except BrokerError:
    gap = True
chk('ролл: отказ на открытии новой серии виден как разрыв', gap and bb.net_positions().get('ZNZ26', 0) == 0,
    f"позиции после сбоя: {bb.net_positions()}")

# --- 4. журнал §7 ---
with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp) / 'journal.csv'
    bb = FakeBroker(prices={'ZNZ26': 112.0, 'ESZ26': 6000.0})
    # КАЖДАЯ СЕССИЯ ЗАКРЫВАЕТСЯ СТРОКОЙ ИТОГ (двадцать первый круг, №18). Прежде фикстура
    # писала 29 исполнений без единого итога — с правкой двадцатого круга (№20) такие даты
    # выходят из выборки §7 как неполные, reconcile возвращает ранний ответ без ключей
    # trade/roll, и тест падал KeyError. Настоящий журнал так не выглядит: итог обязателен.
    def _itog(dte, n, nn):
        J.append(p, dict(date=dte, leg='', instrument='ИТОГ', qty=0, px_order='-',
                         px_fill='', commission='', reason='', nav='10000000',
                         leverage='2.0000', note=f'итог сессии {n}: строк {nn}'))

    for k in range(25):                       # 25 РАЗНЫХ сессий
        rec = bb.place('ZNZ26', 3 if k % 2 == 0 else -3)
        _d = f'2026-{8 + k//28:02d}-{k%28+1:02d}'
        J.append(p, dict(date=_d, leg='Б', instrument='ZNZ26',
                         qty=rec['filled'], px_order=rec['px_order'], px_fill=rec['px_fill'],
                         commission=rec['commission'], reason='нога Б вне полосы',
                         nav='10000000', leverage='2.0000'))
        _itog(_d, k + 1, 1)
    for k in range(4):                        # роллы — отдельной сверкой
        rec = bb.place('ZNZ26', 101 if k % 2 == 0 else -101)
        _d = f'2026-11-{20+k:02d}'
        J.append(p, dict(date=_d, leg='Б', instrument='ZNZ26',
                         qty=rec['filled'], px_order=rec['px_order'], px_fill=rec['px_fill'],
                         commission=rec['commission'], reason='ролл серии', note='ролл',
                         nav='10000000', leverage='2.0000'))
        _itog(_d, 25 + k + 1, 1)
    chk('журнал: цепочка хэшей цела', J.verify(p) == 58, f'{J.verify(p)} строк')

    r = J.reconcile(p)
    chk('журнал: наблюдение считается сессиями, а не строками',
        r['n_sessions'] == 29 and r['n_rows'] == 29, f"сессий {r['n_sessions']}, строк {r['n_rows']}")
    chk('журнал: РОЛЛ сверяется отдельно от сделок',
        r['roll']['n'] == 4 and r['trade']['n'] == 25,
        f"сделка {r['trade']['bp']:.2f} б.п. против {J.MODEL_COST_BP}; "
        f"ролл {r['roll']['bp']:.2f} б.п. против {J.MODEL_ROLL_BP}")
    chk('журнал: издержки взвешены по номиналу, а не по строкам',
        r['trade']['notional'] > 0 and 'by_instrument' in r,
        f"номинал {r['trade']['notional']:,.0f} $")

    # подделка старой строки должна ломать цепочку
    lines = p.read_text(encoding='utf-8').splitlines()
    lines[3] = lines[3].replace(',нога Б вне полосы,', ',подделка,')
    p.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    try:
        J.verify(p); broke = False
    except ValueError:
        broke = True
    chk('журнал: правка задним числом обнаруживается', broke)

    p2 = Path(tmp) / 'short.csv'
    J.append(p2, dict(date='2026-08-01', leg='Б', instrument='ZNZ26', qty=1,
                      px_order=112.0, px_fill=112.01, commission=2.0, reason='x',
                      nav='1', leverage='2'))
    chk('журнал: при недостатке наблюдений вывод не делается',
        'trade' not in J.reconcile(p2), J.reconcile(p2)['verdict'])

    p3 = Path(tmp) / 'bad.csv'
    p3.write_text('date,leg\n2026-08-01,Б\n', encoding='utf-8')
    try:
        J.read(p3); bad = False
    except ValueError:
        bad = True
    chk('журнал: повреждённая строка не пропускается молча', bad)

# --- 5. маршрут Е: собственный контур ---
be = DL.BookE(prev_st_eq=False, prev_st_bd=False)
me = DL.MarketE(date=days[-1], px_eq_prev=700.0, px_bd_prev=5.0, px_eq_today=700.0,
                px_bd_today=5.0, st_eq=True, st_bd=True)
de = DL.step_e(be, me, 10_000_000.0)
chk('маршрут Е: контур строит книгу в долях CSPX/CBU0, а не во фьючерсах',
    set(de.orders) == {'CSPX', 'CBU0'}, ', '.join(f'{k} {v:+d}' for k, v in de.orders.items()))
chk('маршрут Е: считается дебет, TER, drag и заём',
    all(k in de.daily_costs for k in ('ter', 'drag', 'loan', 'debit')),
    f"дебет {de.daily_costs['debit']:,.0f} $")
norm = DL.step_e(be, me, 10_000_000.0, maint=0.25)
chk('маршрут Е: при нормативных 25% О-3-Е сработать не может (кап держит запас 2,00×)',
    not any('О-3-Е' in r for r in norm.reasons) and abs(norm.cushion - 2.0) < 0.05,
    f'запас {norm.cushion:.2f}×')
chk('маршрут Е: расчётный запас при house 35% остаётся выше порога (кап не пускает ниже)',
    abs(DL.step_e(be, me, 10_000_000.0, maint=0.35).cushion - 1.43) < 0.03)
live = DL.step_e(be, me, 10_000_000.0, live_cushion=1.31)
chk('маршрут Е: О-3-Е срабатывает по ЖИВОМУ значению брокера и режет до L=1',
    any('О-3-Е' in r for r in live.reasons) and live.leverage <= 1.05,
    f'плечо после сокращения {live.leverage:.2f}')

# --- 6. состояние сессии: восстановление, блокировка, сверка, позднее исполнение ---
import state as ST
with tempfile.TemporaryDirectory() as tmp:
    sp = Path(tmp) / 'book.json'
    b0 = DL.Book(d_fix=8.0, n_e=260, n_b=101, unit_is_mes=True, prev_st_eq=True,
                 prev_st_bd=True, ser_a='U26', ser_b='U26', es_held=26)
    ST.save(sp, b0, 'F', 7, note='проверка')
    b1, sess, rt = ST.load(sp, DL.Book)
    chk('состояние: книга переживает перезапуск без потерь',
        b1 == b0 and sess == 7 and rt == 'F')

    raw = sp.read_text(encoding='utf-8').replace('"n_b": 101', '"n_b": 99')
    sp.write_text(raw, encoding='utf-8')
    try:
        ST.load(sp, DL.Book); tamper = False
    except ValueError:
        tamper = True
    chk('состояние: правка файла обнаруживается по хэшу', tamper)
    ST.save(sp, b0, 'F', 7)

    exp = ST.expected_positions(b0, 'F')
    chk('сверка: ожидаемая книга разложена по конкретным сериям',
        exp == {'ZNU26': 101, 'ESU26': 26}, str(exp))
    chk('сверка: совпадение с брокером не даёт расхождений',
        ST.reconcile(b0, 'F', dict(exp)) == [])
    late = dict(exp); late['ZNU26'] = 111        # позднее исполнение отменённой заявки
    diffs = ST.reconcile(b0, 'F', late)
    chk('сверка: ПОЗДНЕЕ исполнение после отмены обнаруживается',
        len(diffs) == 1 and '+10' in diffs[0], diffs[0] if diffs else '—')

    br = FakeBroker(prices={'ZNU26': 112.0, 'ESU26': 6000.0}, positions=late)
    mkt = DL.Market(date=days[-1], px_eq_prev=spy[-2], dref_prev=dref[-2], dref_today=dref[-1],
                    px_eq_today=spy[-1], roll_today=False, st_eq=True, st_bd=True)
    try:
        DL.run_session(br, mkt, dirpath=tmp, route='F', capital=10_000_000.0,
                       book_path=str(sp)); stopped = False
    except RuntimeError as ex:
        stopped = 'не совпадает' in str(ex)
    chk('сессия: при расхождении с брокером расчёт НЕ выполняется', stopped)

    br2 = FakeBroker(prices={'ZNU26': 112.0, 'ESU26': 6000.0}, positions=dict(exp))
    dec, orders, diff = DL.run_session(br2, mkt, dirpath=tmp, route='F',
                                       capital=10_000_000.0, dry_run=True,
                                       book_path=str(sp))
    chk('сессия: режим наблюдения без позиции не подаёт заявок и не меняет состояние',
        not br2.log and ST.load(sp, DL.Book)[1] == 7, f'заявок {len(br2.log)}')

    import threading, time as _t
    held = []
    def _hold():
        with ST.hold_book_lock(tmp):
            held.append(1); _t.sleep(1.2)
    th = threading.Thread(target=_hold); th.start(); _t.sleep(0.3)
    try:
        with ST.hold_book_lock(tmp, timeout_s=0.4):
            clash = False
    except RuntimeError:
        clash = True
    th.join()
    chk('блокировка: вторая программа не входит в книгу одновременно', clash)

# --- 7. разрыв ролла: откат вместо непокрытой книги ---
ro_orders = [('ZNU26', -101), ('ZNZ26', 101)]
_bk_old = DL.Book(d_fix=8.0, n_e=0, n_b=101, unit_is_mes=True, prev_st_eq=True,
                  prev_st_bd=True, ser_a='U26', ser_b='U26', es_held=0)
d_ok = FakeBroker(prices={'ZNU26': 112.0, 'ZNZ26': 112.5}, positions={'ZNU26': 101})
DL.execute_roll(d_ok, ro_orders, book_before=_bk_old)
chk('ролл: при штатном исполнении книга переходит в новую серию',
    d_ok.net_positions().get('ZNU26', 0) == 0 and d_ok.net_positions().get('ZNZ26', 0) == 101,
    str(d_ok.net_positions()))

class _Refuse(FakeBroker):
    def place(self, instrument, qty, px_order=None):
        if instrument == 'ZNZ26' and qty > 0:
            raise BrokerError('открытие новой серии отклонено')
        return FakeBroker.place(self, instrument, qty, px_order)

d_bad = _Refuse(prices={'ZNU26': 112.0, 'ZNZ26': 112.5}, positions={'ZNU26': 101})
try:
    DL.execute_roll(d_bad, ro_orders, book_before=_bk_old); gap = 'исполнено'
except DL.RollGap as ex:
    # Шестнадцатый круг, №5: исключение из place() — исход НЕИЗВЕСТЕН адаптеру, поэтому
    # честный вердикт — «по снимку соответствует... недоказуемо» и О-5 БЕЗ автопереноса;
    # прежняя «приведена к исходной» для этого пути была заявлением сверх доказуемого.
    gap = ('откачен' if ('соответствует исходной' in str(ex)
                         and 'недоказуемо' in str(ex)
                         and 'переносится' not in str(ex)) else 'НЕ ОТКАЧЕН')
    _msg = str(ex)
chk('ролл: отказ на открытии откатывает закрытие, книга не остаётся пустой',
    gap == 'откачен' and d_bad.net_positions().get('ZNU26', 0) == 101,
    f"{gap}, ZNU26 = {d_bad.net_positions().get('ZNU26', 0)}")

# --- 8. дефекты, найденные рецензией нового кода ---
b_mes = DL.Book(d_fix=8.0, n_e=10, n_b=0, unit_is_mes=True, prev_st_eq=True, prev_st_bd=True,
                ser_a='U26', ser_b='U26', es_held=0)          # у брокера 10 MES, целых ES нет
d_mes = DL.step(b_mes, DL.Market(date=pd.Timestamp('2026-08-26'), px_eq_prev=600.0,
                dref_prev=8.0, dref_today=8.0, px_eq_today=600.0, roll_today=True,
                st_eq=True, st_bd=True), 10_000_000.0, check_guards=False)
old = [x for x in DL.book_to_orders(d_mes, b_mes) if 'U26' in x[0]]
chk('ролл закрывает ФАКТИЧЕСКУЮ упаковку, а не каноническую',
    old == [('MESU26', -10)], str(old))

b_out = DL.Book(d_fix=8.0, n_e=0, n_b=101, unit_is_mes=True, prev_st_eq=False,
                prev_st_bd=True, ser_a='U26', ser_b='U26', es_held=0)
d_out = DL.step(b_out, DL.Market(date=pd.Timestamp('2026-08-26'), px_eq_prev=600.0,
                dref_prev=8.0, dref_today=8.0, px_eq_today=600.0, roll_today=True,
                st_eq=False, st_bd=False), 10_000_000.0, check_guards=False)
chk('нулевых заявок не возникает при ролле с выходом из ноги',
    all(q for _, q in DL.book_to_orders(d_out, b_out)))

for dte, rp, exp in ((pd.Timestamp('2026-08-26'), False, 'Z26'),
                     (pd.Timestamp('2026-08-27'), True, 'Z26'),
                     (pd.Timestamp('2026-08-10'), False, 'U26')):
    dd = DL.step(DL.Book(d_fix=8.0), DL.Market(date=dte, px_eq_prev=600.0, dref_prev=8.0,
                 dref_today=8.0, px_eq_today=600.0, roll_today=(dte.day == 26),
                 st_eq=True, st_bd=True, roll_passed=rp), 10_000_000.0, check_guards=False)
    chk(f'первый вход {dte.date()} даёт серию {exp}', dd.book_after.ser_b == exp,
        dd.book_after.ser_b)

chk('признак trade не завязан на отказы (спасительная заявка уходит)',
    DL.Decision(date=days[-1], orders={'Б': -50}, refusals=['порог §8']).trade)

with tempfile.TemporaryDirectory() as tmp:
    exp0 = {'ZNU26': 101, 'ESU26': 26}
    sp2 = Path(tmp) / 'book.json'
    ST.save(sp2,
            DL.Book(d_fix=8.0, n_e=260, n_b=101, unit_is_mes=True, prev_st_eq=True,
                    prev_st_bd=True, ser_a='U26', ser_b='U26', es_held=26), 'F', 1)
    class _Partial(FakeBroker):
        def place(self, instrument, qty, px_order=None):
            return FakeBroker.place(self, instrument, int(qty * 0.5) or 1, px_order)
    bp = _Partial(prices={'ZNU26': 112.0, 'ESU26': 6000.0}, positions=dict(exp0))
    mk = DL.Market(date=days[-1], px_eq_prev=spy[-2], dref_prev=dref[-2], dref_today=dref[-1],
                   px_eq_today=spy[-1], roll_today=False, st_eq=False, st_bd=False)
    try:
        DL.run_session(bp, mk, dirpath=tmp, route='F', capital=10_000_000.0,
                       book_path=str(sp2)); caught = False
    except RuntimeError as ex:
        caught = any(t in str(ex) for t in ('разошлось с намерением', 'не совпадает',
                                            'исполнено', 'ВОССТАНОВИТЬ'))
    saved, _, _ = ST.load(Path(tmp) / 'book.json', DL.Book)
    chk('частичное исполнение не сохраняется как успех',
        caught and saved.n_b == 101, f'поймано={caught}, книга в состоянии {saved.n_b}')

# частичное закрытие не должно вести к открытию новой серии
class _HalfClose(FakeBroker):
    def place(self, instrument, qty, px_order=None):
        if qty < 0:
            return FakeBroker.place(self, instrument, qty // 2, px_order)
        return FakeBroker.place(self, instrument, qty, px_order)
hb = _HalfClose(prices={'ZNU26': 112.0, 'ZNZ26': 112.5}, positions={'ZNU26': 101})
try:
    DL.execute_roll(hb, ro_orders, book_before=_bk_old); half = 'открыл новую'
except DL.RollGap as ex:
    half = 'остановился' if 'исполнено' in str(ex) else str(ex)[:40]
chk('ролл: частичное закрытие НЕ ведёт к открытию новой серии',
    half == 'остановился' and hb.net_positions().get('ZNZ26', 0) == 0, half)

# О-3-Е не наращивает позицию
be_empty = DL.BookE(prev_st_eq=False, prev_st_bd=False)
o3 = DL.step_e(be_empty, me, 10_000_000.0, live_cushion=1.2)
chk('О-3-Е не открывает новую позицию при аварийно низком запасе',
    o3.book_after.n_eq == 0 and o3.book_after.n_bd == 0,
    f'{o3.book_after.n_eq}/{o3.book_after.n_bd}')

# --- 9. передача книги между маршрутами ---
bF = ST.book_from_broker(DL.Book, {'ESU26': 26, 'MESU26': 7, 'ZNU26': 101}, 'F')
chk('передача: книга маршрута Ф собирается из фактических позиций брокера',
    bF.n_e == 267 and bF.n_b == 101 and bF.es_held == 26 and bF.ser_a == 'U26',
    f'{bF.n_e}/{bF.n_b}, es_held={bF.es_held}, серии {bF.ser_a}/{bF.ser_b}')
bE = ST.book_from_broker(DL.BookE, {'CSPX': 14255, 'CBU0': 2000000.5}, 'E')
chk('передача: дробная доля ETF не теряется', bE.n_bd == 2000000.5, str(bE.n_bd))
with tempfile.TemporaryDirectory() as tmp:
    sp = Path(tmp) / 'b.json'
    ST.save(sp, bF, 'F', 1)
    try:
        ST.load(sp, DL.BookE); wrong = False
    except ValueError as ex:
        wrong = 'маршрута' in str(ex)
    chk('передача: чтение книги чужого маршрута даёт понятный отказ, а не TypeError', wrong)

# восстановление ведётся по ФАКТИЧЕСКИМ позициям, а не по нашим шагам
class _LostAck(FakeBroker):
    """Заявка исполняется, но подтверждение теряется — статус неизвестен."""
    def place(self, instrument, qty, px_order=None):
        rec = FakeBroker.place(self, instrument, qty, px_order)
        if instrument == 'ZNZ26' and qty > 0:
            raise BrokerError('подтверждение потеряно, заявка могла исполниться')
        return rec

la = _LostAck(prices={'ZNU26': 112.0, 'ZNZ26': 112.5}, positions={'ZNU26': 101})
try:
    DL.execute_roll(la, ro_orders, book_before=_bk_old); lost = 'прошло'
except DL.RollGap as ex:
    # Потерянное подтверждение — эталонный НЕИЗВЕСТНЫЙ исход (шестнадцатый круг, №5):
    # позиции обязаны быть приведены по снимку, но вердикт — «недоказуемо», О-5, и
    # никакого «переносится»: поздний отчёт ещё может изменить книгу.
    lost = ('восстановлено' if ('соответствует исходной' in str(ex)
                                and 'недоказуемо' in str(ex)
                                and 'переносится' not in str(ex)) else str(ex)[:50])
chk('ролл: потерянное подтверждение НЕ приводит к повтору и книга восстановлена',
    lost == 'восстановлено' and la.net_positions().get('ZNU26', 0) == 101
    and la.net_positions().get('ZNZ26', 0) == 0,
    f"{lost}; книга {dict((k, v) for k, v in la.net_positions().items() if v)}")

print('\nИТОГ:', 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ' if all(OK) else 'ЕСТЬ ОТКАЗЫ')
sys.exit(0 if all(OK) else 1)
