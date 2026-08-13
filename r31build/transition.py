# -*- coding: utf-8 -*-
"""Переходный исполнитель v7.7 (ред. 30). Платформа Linux/Unix. Журнал — ПИНОВАННЫЙ
абсолютный путь (mr_engine.configure); alias-пути отклоняются. Замок — единый постоянный strategy+account
(mr_engine.hold_strategy_lock), реентерабельный только для потока-владельца, общий с движком МР.
Реестр инструментов пинован SHA-256 (одно чтение байтов). Ноги — пары src→dst с классами из реестра,
канонический порядок (-зерно цели, имя). Лимит непарной дельты СТРОГИЙ: требуемая сумма = зерно пары +
зазор округления; owner-cap 1,0% NLV (решение заказчика 08.08.2026); журнальные разрешения
GRANULARITY_EXCEPTION по sid. Ред. 31: открытие перехода требует OWNER_APPROVE заказчика
на тот же sid и ту же цель — сигнал МР является рекомендацией, а не командой.
Двухфазность: OPEN/COMPLETE/ABORT/MIXED только через журнал МР;
сверка книги с планом и реестром до первого ордера; восстановление по нетто-позициям и order-id.
"""
import json, os, hashlib, csv, fcntl

MAX_UNPAIRED_PCT = 0.010
INTRA_CAP = 2.02
CLOSE_CAP = 2.00
TIMEOUT_MIN = 15
TOL = 1e-6

class Incident(Exception):
    pass

STRATEGY_ID = 'ADD-FUT-v1_6'

REGISTRY_SHA256 = 'd5a2982c128f6869081a820d68bbaea4da7f6284ea1695b12db093beefeba2f7'

def load_registry(path='instruments.csv'):
    body = open(path, 'rb').read()                             # ЕДИНСТВЕННОЕ чтение: хэш и разбор одного body
    if hashlib.sha256(body).hexdigest() != REGISTRY_SHA256:
        raise Incident('реестр инструментов не совпадает с пинованным SHA-256 — подмена файла, исполнение запрещено')
    import io
    reg = {}
    for r in csv.DictReader(io.StringIO(body.decode('utf-8'))):
        reg[r['instrument']] = dict(sec_type=r['sec_type'], pair_group=r['pair_group'],
                                    exchange=r['exchange'], currency=r['currency'], con_id=r['con_id'])
    return reg


def _atomic(path, obj):
    tmp = f'{path}.tmp.{os.getpid()}.{os.urandom(4).hex()}'
    with open(tmp, 'w') as f:                       # долговечность состояния исполнителя
        json.dump(obj, f, ensure_ascii=False); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)
    fd = os.open(os.path.dirname(os.path.abspath(path)) or '.', os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)

def transition_id(signal_id, from_route, to_route, capital, plan):
    key = json.dumps([signal_id, from_route, to_route, round(capital, 2), plan], sort_keys=True)
    return hashlib.sha256(key.encode()).hexdigest()[:16]

def plan_lots(legs, capital):
    lots = []
    seen_src, seen_dst = set(), set()
    for name, spec in legs.items():
        di = spec['dst'][0]
        if di in seen_dst: raise Incident(f'{di}: дублированная цель в плане')
        seen_dst.add(di)
        dpv = float(spec['dst'][1])
        if not (dpv > 0 and dpv == dpv and dpv != float('inf')):
            raise Incident(f'{di}: цена цели должна быть конечной положительной')
        for instr, units, u in spec['src']:
            if instr in seen_src: raise Incident(f'{instr}: дублированный источник в плане')
            seen_src.add(instr)
            uv = float(u)
            if not (uv > 0 and uv == uv and uv != float('inf')):
                raise Incident(f'{instr}: долларовый эквивалент должен быть конечным положительным')
    for name, spec in sorted(legs.items(), key=lambda kv: (-float(kv[1]['dst'][1]), kv[0])):
        if len(spec['dst']) != 3 or spec['dst'][2] not in ('ETF', 'FUT'):
            raise Incident(f'{name}: dst требует класс инструмента ETF|FUT')
        dinstr, dprice = spec['dst'][0], spec['dst'][1]
        for instr, units, unit_usd in spec['src']:
            if int(units) != units or units <= 0:
                raise Incident(f'{instr}: единицы должны быть целыми положительными')
            q = max(1, int(round(dprice/float(unit_usd)))) if dprice > float(unit_usd)*1.5 else 1
            tail = int(units) % q
            per_lot = max(q, (int(units) // 4) // q * q)
            k, left = 0, int(units)
            while left > 0:
                take = min(per_lot, left)
                lots.append(dict(leg=name, src=instr, dst=dinstr, units=take,
                                 unit_usd=float(unit_usd), dprice=float(dprice), step=k))
                left -= take; k += 1
    return lots

MIN_AUTO_NLV = 9_856_000.0   # ниже — автоматический переход невозможен: один ZN не входит в owner-cap 1%

def unpaired_limit(legs, capital, grant_limit=None):
    """Owner-cap заказчика = 1,0% капитала. Контракт крупнее owner-cap НЕ расширяет лимит
    автоматически: требуется предразложение ES->MES (нормативно, §8б) либо явное разрешение
    заказчика на конкретный переход (allow_granularity_exception=True) — иначе Incident/REVIEW."""
    mx = max([u for spec in legs.values() for _, n, u in spec['src']]
             + [float(spec['dst'][1]) for spec in legs.values()])   # зерно ОБЕИХ сторон пары
    h = max(min(spec['dst'][1], float(u))/2.0 + 1.0
            for spec in legs.values() for _, n, u in spec['src'])   # зазор округления пары
    pct = MAX_UNPAIRED_PCT*capital
    if mx + h > pct:
        need = mx + h
        if grant_limit is None or grant_limit < need:
            raise Incident(f'требуемый лимит ${need:,.0f} (зерно ${mx:,.0f} + зазор округления ${h:,.0f}) '
                           f'превышает owner-cap ${pct:,.0f} — журнальное разрешение GRANULARITY_EXCEPTION '
                           f'на сумму не ниже требуемой либо NLV >= ${MIN_AUTO_NLV:,.0f}')
        return grant_limit
    return pct

def _int_fill(fill, what):
    if abs(fill - round(fill)) > 1e-9:
        raise Incident(f'{what}: дробное исполнение {fill} отклонено')
    return int(round(fill))

def execute(broker, state_path, capital, legs, signal_id='', from_route='F', to_route='E',
            in_common_window=True, resume=False, journal=None, mr_state=None, asof=None,
            registry='instruments.csv'):
    import mr_engine as _M
    import math as _math
    if not (isinstance(capital, (int, float)) and _math.isfinite(capital) and capital > 0):
        raise Incident('capital должен быть конечным положительным числом')
    if journal is None or mr_state is None or asof is None:
        raise Incident('journal/mr_state/asof обязательны — исполнение без журнала МР запрещено')
    import mr_engine as _M0
    try:
        journal = _M0.canonical_journal(journal)               # канонический путь; symlink = отказ
    except _M0.JournalCorrupt as ex:
        raise Incident(str(ex))
    reg = load_registry(registry)
    want_cls = 'ETF' if to_route == 'E' else 'FUT'
    src_cls = 'ETF' if from_route == 'E' else 'FUT'
    for name, spec in legs.items():
        di = spec['dst'][0]
        if di not in reg:
            raise Incident(f'{name}: инструмент цели {di} отсутствует в реестре instruments.csv')
        rc = reg[di]['sec_type']
        if len(spec['dst']) != 3 or spec['dst'][2] != rc:
            raise Incident(f'{name}: метка класса {spec["dst"][2] if len(spec["dst"]) > 2 else "?"} '
                           f'не совпадает с реестром ({di} = {rc})')
        if rc != want_cls:
            raise Incident(f'{name}: класс цели {rc} по реестру не соответствует маршруту {to_route} (ожидается {want_cls})')
        for instr, units, u in spec['src']:
            if instr not in reg:
                raise Incident(f'{name}: инструмент источника {instr} отсутствует в реестре')
            if reg[instr]['sec_type'] != src_cls:
                raise Incident(f'{name}: {instr} класса {reg[instr]["sec_type"]} не является источником маршрута {from_route}')
            if reg[instr]['pair_group'] != reg[di]['pair_group']:
                raise Incident(f'{name}: пара {instr}->{di} вне whitelist направлений (группы {reg[instr]["pair_group"]}/{reg[di]["pair_group"]})')
    _r_now = _M.derive_state(journal, __import__('datetime').date.fromisoformat(asof))[0]
    if _r_now != from_route:
        raise Incident(f'from_route={from_route} не совпадает с маршрутом журнала {_r_now}')
    if not in_common_window:
        raise Incident('вне пересечения фактических торговых сессий LSE/CME')
    plan = plan_lots(legs, capital)
    tid = transition_id(signal_id, from_route, to_route, capital, plan)
    try:
        _ctx = _M.hold_strategy_lock(journal)
        _ctx.__enter__()
    except RuntimeError:
        raise Incident('strategy-lock (стратегия+счёт) занят другим процессом — параллельное исполнение запрещено')
    try:
        return _execute_locked(broker, state_path, capital, legs, signal_id, from_route, to_route,
                               in_common_window, resume, journal, mr_state, asof, registry,
                               plan, tid, reg, want_cls, src_cls, _M)
    finally:
        _ctx.__exit__(None, None, None)


def _execute_locked(broker, state_path, capital, legs, signal_id, from_route, to_route,
                    in_common_window, resume, journal, mr_state, asof, registry,
                    plan, tid, reg, want_cls, src_cls, _M):
    try:                                                       # ред. 31: подпись заказчика ТОЛЬКО из журнала
        approved = _M.find_approval(journal, asof, signal_id, to_route)
    except _M.JournalCorrupt as ex:
        raise Incident(f'журнал повреждён при чтении одобрения: {ex}')
    if not approved:
        raise Incident(f'нет OWNER_APPROVE заказчика на сигнал {signal_id} в маршрут {to_route} — '
                       f'сигнал МР является рекомендацией, исполнение запрещено')
    try:
        grant_limit = _M.find_grant(journal, asof, signal_id)   # разрешение ТОЛЬКО из журнала
    except _M.JournalCorrupt as ex:
        raise Incident(f'журнал повреждён при чтении разрешения: {ex}')
    lim = unpaired_limit(legs, capital, grant_limit)
    for name, spec in legs.items():                            # хвост кванта цели допустим только в пределах лимита
        dp = spec['dst'][1]
        for instr, units, u in spec['src']:
            q = max(1, int(round(dp/float(u)))) if dp > float(u)*1.5 else 1
            if (int(units) % q)*float(u) > lim + TOL:
                raise Incident(f'{instr}: хвост {int(units) % q} единиц (${(int(units) % q)*float(u):,.0f}) '
                               f'превышает лимит — предварительная подгонка книги')


    def hook(kind):
        return _M.confirm_transition(journal, mr_state, asof, to_route, kind=kind,
                                     tid=tid, sid=signal_id) is True

    if os.path.exists(state_path):
        st = json.load(open(state_path))
        if st.get('tid') != tid:
            raise Incident('состояние не соответствует переходу (transition_id) — ручная сверка')
        if not resume and (st.get('opened') or st.get('done') or st.get('executed_usd', 0.0) > TOL or st.get('order_ids')):
            raise Incident('переход уже открыт/имеет прогресс — повторный запуск только с resume=True')
        if not resume:                                         # свежий заход без прогресса: снапшот пересоздаётся
            st = dict(tid=tid, postponed=st.get('postponed', 0), done=[], executed_usd=0.0,
                      order_ids=[], snapshot=broker.net_positions(), log=[])
            _atomic(state_path, st)
    else:
        st = dict(tid=tid, postponed=0, done=[], executed_usd=0.0, order_ids=[],
                  snapshot=broker.net_positions(), log=[])
        _atomic(state_path, st)
    if not broker.preview():
        st['postponed'] += 1; _atomic(state_path, st)
        if st['postponed'] >= 3:
            if st['executed_usd'] > TOL:
                hook('mixed')
            else:
                hook('open'); hook('abort')                    # честная пара OPEN+ABORT, pending снимается строго
            raise Incident('margin preview отклонён три раза — инцидент')
        return dict(status='POSTPONED', postponed=st['postponed'])
    st['postponed'] = 0; _atomic(state_path, st)
    _r0, _p0, _mx0, _an0, _sid0, _otid0, _mk0 = _M.derive_state(journal, __import__('datetime').date.fromisoformat(asof))
    if _otid0 == tid and not resume and not st.get('opened'):
        raise Incident('переход с этим tid уже захвачен в журнале — только resume')
    if not resume:
        snap0 = broker.net_positions()
        planned_src = {instr for spec in legs.values() for instr, _, _ in spec['src']}
        for name, spec in legs.items():
            for instr, units, u in spec['src']:
                if snap0.get(instr, 0) != int(units):
                    raise Incident(f'{instr}: книга ({snap0.get(instr, 0)}) не соответствует плану ({int(units)}) — переход отклонён')
        for instr, qty in snap0.items():                       # ПОЛНАЯ сверка книги
            if qty == 0: continue
            if instr not in reg:
                raise Incident(f'{instr}: неизвестный реестру инструмент в книге ({qty}) — исполнение запрещено')
            if reg[instr]['sec_type'] == src_cls and instr not in planned_src:
                raise Incident(f'{instr}: позиция класса источника ({qty}) вне плана — книга не переводится целиком')
            if reg[instr]['sec_type'] == want_cls:
                raise Incident(f'{instr}: предсуществующая позиция класса цели ({qty}) до перехода — требуется разбор')
    if not hook('open'):                                      # проверяется ВСЕГДА, включая resume (идемпотентно)
        raise Incident('журнал отклонил открытие перехода (нет сигнала/sid/чужой tid) — исполнение запрещено')
    st['opened'] = True; _atomic(state_path, st)

    max_dp = max(spec['dst'][1] for spec in legs.values())

    def fail(msg, cancel=True):
        if cancel:
            for oid in broker.open_orders(): broker.cancel_order(oid)   # контракт адаптера: блокирующая до терминального статуса
        try:                                                            # обязательная сверка книги до записи исхода
            now = broker.net_positions(); snap = st['snapshot']
            moved = sum(abs(now.get(k, 0) - snap.get(k, 0)) for k in set(list(now) + list(snap)))
        except Exception:
            moved = 1
        kind = 'mixed' if (st['executed_usd'] > TOL or moved > 0) else 'abort'
        try:
            ok = hook(kind)
        except Exception as ex:
            _atomic(state_path, st)
            raise Incident(msg + f' | КРИТИЧНО: запись {kind.upper()} в журнал провалилась ({ex!r}) — немедленная ручная сверка книги и журнала')
        if not ok:
            _atomic(state_path, st)
            raise Incident(msg + f' | КРИТИЧНО: журнал отклонил {kind.upper()} — немедленная ручная сверка книги и журнала')
        _atomic(state_path, st)
        raise Incident(msg)

    if resume:
        for oid in broker.open_orders():                      # ВСЕ открытые: известные и чужие
            if broker.cancel_order(oid) is not True:          # контракт: строго True после терминального статуса
                raise Incident(f'отмена ордера {oid} не подтверждена терминальным статусом — ручная сверка')
            st['log'].append(('cancel_on_resume', oid, 0))
        now = broker.net_positions(); snap = st['snapshot']
        src_prog = {}; total_unp = 0.0
        for name, spec in legs.items():
            di, dp = spec['dst'][0], spec['dst'][1]
            sold_leg = 0.0
            for instr, units, u in spec['src']:
                ds = snap.get(instr, 0) - now.get(instr, 0)
                _int_fill(ds, instr)
                if ds < 0: raise Incident(f'{instr}: позиция выросла во время перехода — ручная сверка')
                sold_leg += ds*u; src_prog[instr] = ds
            db = now.get(di, 0) - snap.get(di, 0)
            _int_fill(db, di)
            diff = sold_leg - db*dp                       # компенсация СВОЕЙ ногой
            if diff > dp/2 + TOL:
                oid, f = broker.buy_units(di, int(round(diff/dp)))
                st['order_ids'].append(oid); diff -= _int_fill(f, di)*dp
                st['log'].append(('recover_buy', di, f))
            elif diff < -dp/2 - TOL:
                oid, f = broker.sell_units(di, int(round(-diff/dp)))
                st['order_ids'].append(oid); diff += _int_fill(f, di)*dp
                st['log'].append(('recover_sell', di, f))
            _atomic(state_path, st)
            if abs(diff) > dp + TOL:
                raise Incident(f'{name}: рассинхрон не компенсирован своей ногой — ручная сверка')
            total_unp += diff
        if abs(total_unp) > lim + TOL:
            hook('mixed'); _atomic(state_path, st)
            raise Incident('восстановление: |непарная| выше предела — состояние MIXED, ручная сверка')
        st['done'] = []
        acc = {}
        for lot in plan:
            got = src_prog.get(lot['src'], 0) - acc.get(lot['src'], 0)
            if got >= lot['units']:
                st['done'].append(f"{lot['src']}:{lot['step']}")
                acc[lot['src']] = acc.get(lot['src'], 0) + lot['units']
        st['executed_usd'] = max(st.get('executed_usd', 0.0),
            sum(src_prog.get(i, 0)*u for name, spec in legs.items() for i, n, u in spec['src']))
        _atomic(state_path, st)

    unp = {name: 0.0 for name in legs}
    dst_bought = {}
    try:
        _run_lots(broker, plan, st, state_path, lim, unp, dst_bought, fail, _M, journal)
    except Incident:
        raise
    except Exception as ex:
        fail(f'исключение адаптера: {ex!r}')

    for name, spec in legs.items():
        if abs(unp[name]) > spec['dst'][1] + TOL:
            fail(f'{name}: финальная |непарная| выше цены одной доли')
    # полная сверка фактической книги с планом перехода
    now = broker.net_positions(); snap = st['snapshot']
    for name, spec in legs.items():
        di, dp = spec['dst'][0], spec['dst'][1]
        planned_usd = sum(n*u for _, n, u in spec['src'])
        got_usd = (now.get(di, 0) - snap.get(di, 0))*dp
        got_units = now.get(di, 0) - snap.get(di, 0)
        if got_units < 0:
            fail(f'{di}: короткая целевая позиция после перехода')
        if abs(got_usd - planned_usd) > dp + TOL:
            fail(f'{di}: целевая позиция расходится с планом на ${abs(got_usd-planned_usd):,.0f}')
        for instr, units, u in spec['src']:
            if snap.get(instr, 0) - now.get(instr, 0) != units:
                fail(f'{instr}: закрыто не по плану')
    g = broker.gross()
    if g > CLOSE_CAP + 1e-9:
        fail(f'плечо на закрытии {g:.4f} > {CLOSE_CAP}', cancel=False)
    st['log'].append(('complete', tid, g)); _atomic(state_path, st)
    if not hook('complete'):
        hook('mixed')
        raise Incident('журнал отклонил COMPLETE — книга переведена, состояние MIXED, ручная сверка')
    return dict(status='COMPLETE', gross_close=g, lots=len(st['done']),
                unpaired_usd=sum(unp.values()), tid=tid)


def _run_lots(broker, plan, st, state_path, lim, unp, dst_bought, fail, _M=None, journal=None):
    for lot in plan:
        if _M is not None and journal is not None:
            _M.canonical_journal(journal)         # перед каждым лотом: эпоха и личность журнала
        key = f"{lot['src']}:{lot['step']}"
        if key in st['done']: continue
        remaining = lot['units']; noop = 0
        while remaining > 0:
            head = min(lot['dprice'], lot['unit_usd'])/2.0 + 1.0
            avail = max(lot['dprice'], lim - abs(sum(unp.values())) - head)
            k_dst = max(1, int(avail // lot['dprice']))          # выравнивание по зерну ЦЕЛИ с зазором
            desired = k_dst*lot['dprice'] - unp[lot['leg']]      # продажа целится в кванты МИНУС текущий остаток
            step = max(1, min(remaining, int(round(desired/lot['unit_usd'])) or 1,
                              int(avail // lot['unit_usd']) or 1))
            oid, f = broker.sell_units(lot['src'], step)
            st['order_ids'].append(oid)
            try:
                sold = _int_fill(f, lot['src'])
            except Incident:
                st['executed_usd'] += abs(f)*lot['unit_usd']
                fail(f'{lot["src"]}: дробное исполнение {f} — сверка с брокером')
            unp[lot['leg']] += sold*lot['unit_usd']; st['executed_usd'] += sold*lot['unit_usd']
            if sold > step: fail('исполнено больше заявки (продажа) — сверка с брокером')
            st['log'].append(('sell', lot['src'], sold)); _atomic(state_path, st)
            if sum(abs(v) for v in unp.values()) > lim + TOL:      # СТРОГО в пределах утверждённого лимита
                fail('|непарная дельта| выше утверждённого лимита')
            want = max(0, int(round(unp[lot['leg']]/lot['dprice'])))   # покупка кроет НАКОПЛЕННЫЙ остаток ноги
            oid2, f2 = broker.buy_units(lot['dst'], want)
            st['order_ids'].append(oid2)
            try:
                bought = _int_fill(f2, lot['dst'])
            except Incident:
                st['executed_usd'] += abs(f2)*lot['dprice']
                fail(f'{lot["dst"]}: дробное исполнение {f2} — сверка с брокером')
            st['executed_usd'] += bought*lot['dprice']
            dst_bought[lot['dst']] = dst_bought.get(lot['dst'], 0) + bought
            if bought > want:
                unp[lot['leg']] -= bought*lot['dprice']
                fail('исполнено больше заявки (покупка) — сверка с брокером')
            unp[lot['leg']] -= bought*lot['dprice']
            st['log'].append(('buy', lot['dst'], bought)); _atomic(state_path, st)
            if sold == 0 and bought == 0:
                noop += 1
                if noop >= 2: fail('нулевой прогресс два шага подряд')
                continue
            noop = 0
            u = unp[lot['leg']]
            if u > lot['dprice']/2 + TOL:       # недобор dst своей ноги — докупка
                oid3, f3 = broker.buy_units(lot['dst'], int(round(u/lot['dprice'])))
                st['order_ids'].append(oid3); unp[lot['leg']] -= _int_fill(f3, lot['dst'])*lot['dprice']
                st['log'].append(('compensate_buy', lot['dst'], f3)); _atomic(state_path, st)
            elif u < -lot['dprice']/2 - TOL:    # перебор — обратная продажа своей ноги (не глубже купленного)
                _want = min(int(round(-u/lot['dprice'])), dst_bought.get(lot['dst'], 0))
                oid3, f3 = broker.sell_units(lot['dst'], _want)
                st['order_ids'].append(oid3); _fs = _int_fill(f3, lot['dst']); unp[lot['leg']] += _fs*lot['dprice']
                dst_bought[lot['dst']] = dst_bought.get(lot['dst'], 0) - _fs
                st['log'].append(('compensate_sell', lot['dst'], f3)); _atomic(state_path, st)
            if abs(unp[lot['leg']]) > lot['dprice'] + TOL:
                fail('пара не выровнена компенсацией — ручная сверка')
            if broker.minutes_since(key) > TIMEOUT_MIN:
                fail('тайм-аут пары 15 минут')
            g = broker.gross()
            if g > INTRA_CAP + 1e-9:
                fail(f'внутрисессионный gross {g:.4f} > {INTRA_CAP}')
            remaining -= sold if sold > 0 else 0
        st['done'].append(key); _atomic(state_path, st)
