# -*- coding: utf-8 -*-
"""МР v7.7 (ред. 30). ДВА ЗАМКА одновременно: (1) фиксированный файл замка в каталоге установки
и (2) дескриптор самого журнала (O_NOFOLLOW, сверка st_dev/st_ino). Подмена одного из объектов не даёт
второго владельца: подменивший lock-файл упирается в замок журнала, подменивший журнал — в замок файла
и в сверку личности. Смена журнала СЕРИАЛИЗУЕТСЯ по СТАРОМУ журналу из конфигурации: пока исполнитель
держит старый журнал, reprovision ждёт. Перед КАЖДОЙ записью и чтением сверяется, что удерживаемый
дескриптор всё ещё соответствует пути (dev+ino) — запись в отсоединённый inode невозможна; исполнитель
дополнительно перепроверяет эпоху перед каждым лотом. Механизм: BSD flock(2), advisory, ТОЛЬКО локальная
файловая система (NFS/CIFS не поддерживаются).
Платформа исполнения: Linux/Unix (fcntl). Двухфазный жизненный цикл по вердикту ред. 7.
ЕДИНСТВЕННЫЙ ИСТОЧНИК ИСТИНЫ — журнал событий mr_journal.csv (одна атомарная запись на событие):
  SWITCH_SIGNAL,<цель>   — РЕКОМЕНДАЦИЯ движка; фактический маршрут НЕ меняется;
  OWNER_APPROVE,<цель>|<sid> — одобрение заказчика на исполнение конкретного сигнала;
      без него исполнитель не открывает переход (ред. 31; решение заказчика 09.08.2026);
  TRANSITION_COMPLETE,<цель> — пишет исполнитель по завершении перехода; только это меняет маршрут;
  TRANSITION_ABORT,<цель>    — переход прерван, pending снимается.
Состояние (mr_state.csv) — производный кэш, пересобирается из журнала при каждом запуске;
рассинхронизация невозможна по построению. switches_3y = число TRANSITION_COMPLETE за
скользящие 3 года (события с датой позже asof игнорируются). Третье переключение за 3 года —
РЕШЕНИЕ REVIEW=13, не SWITCH. Повторный сигнал в ту же цель при незакрытом pending = HOLD;
встречный сигнал при pending = REVIEW. Валидация: NLV конечен и >0; URL с непустым адресом
(netloc); fix_date не позже asof; аварийный файл — ≥20 УНИКАЛЬНЫХ строго возрастающих дат."""
import csv, sys, os, math, argparse, fcntl, time
from contextlib import contextmanager
from datetime import date, timedelta
from urllib.parse import urlparse

STATE_EQ, STATE_BD, STATE_BOTH = 0.823, 0.722, 0.589
ONE_LEG = STATE_EQ + STATE_BD - 2*STATE_BOTH
GAP = 0.25
TER_PP = 0.07*(STATE_EQ+STATE_BD)
DRAG_PP = 0.25*STATE_EQ   # ред. 33: норматив повышен с 0,20% (решение заказчика 11.08.2026)
HOLD, INVALID, SWITCH_F, SWITCH_E, REDUCE, REVIEW = 0, 1, 10, 11, 12, 13

# §8 ред. 33: жёсткий порог размера счёта для маршрута Ф. Применяется ДО сравнения
# издержек: движок сравнивает маршруты только по деньгам и о гранулярности контрактов
# сведений не имеет, поэтому на малом счёте по издержкам побеждает Ф, который при этом
# неисполним. Решение заказчика 11.08.2026.
MIN_NLV_F = 3_000_000.0
NAMES = {0: 'HOLD', 1: 'INVALID', 10: 'SWITCH_F', 11: 'SWITCH_E', 12: 'REDUCE', 13: 'REVIEW'}

MAX_SWITCHES_3Y = 2                  # не более двух завершённых переключений за 3 года

def budget_exhausted(n_done):
    """Единственное правило бюджета: применяется и до захвата замка, и под ним."""
    return n_done + 1 > MAX_SWITCHES_3Y

STRATEGY_ID = 'ADD-FUT-v1_6'
ACCOUNT_ID = 'IBKR-PRIMARY'          # константа развёртывания (один счёт IBKR на стратегию)
import threading
_HELD = {}                           # путь замка -> id потока-владельца (реентерабельность только своему потоку)
_PINNED = {'journal': None, 'epoch': None, 'ident': None, 'uuid': None}
import json, uuid
class JournalCorrupt(Exception):
    pass

# EMERGENCY_OVERRIDE внесён девятой рецензией (№26): исполнитель писал его в журнал, а
# derive_state объявлял ту же строку порчей — аварийный вызов сам блокировал себя и
# оставлял журнал, который все последующие запуски считали повреждённым.
KNOWN_EVENTS = ('SWITCH_SIGNAL', 'OWNER_APPROVE', 'TRANSITION_OPEN', 'TRANSITION_COMPLETE',
                'TRANSITION_ABORT', 'TRANSITION_MIXED', 'TRANSITION_RESOLVED',
                'GRANULARITY_EXCEPTION', 'REDUCE_SIGNAL', 'REVIEW_SIGNAL',
                'EMERGENCY_OVERRIDE')

_MODDIR = os.path.dirname(os.path.abspath(__file__))
JOURNAL_SCHEMA = 'asof,event,detail'
INSTALL_DIR = os.path.join(os.path.expanduser('~'), '.add-fut')   # ФИКСИРОВАННЫЙ путь установки:
_STATE = {'dir': INSTALL_DIR}                                     # копии кода делят один замок и конфигурацию

def set_state_dir_for_tests(path):
    """ТОЛЬКО ДЛЯ ТЕСТОВ (изоляция параллельных прогонов). В бою не вызывается: путь установки фиксирован."""
    if _HELD:
        raise JournalCorrupt('смена служебного каталога при удерживаемом замке запрещена')
    _STATE['dir'] = os.path.abspath(path)
    _PINNED['journal'] = None
    os.makedirs(_STATE['dir'], exist_ok=True)
    return _STATE['dir']

def lock_path():
    os.makedirs(_STATE['dir'], exist_ok=True)
    return os.path.join(_STATE['dir'], f'lock.{STRATEGY_ID}.{ACCOUNT_ID}')

def deploy_config_path(journal_real=None):
    os.makedirs(_STATE['dir'], exist_ok=True)
    return os.path.join(_STATE['dir'], f'deploy.{STRATEGY_ID}.{ACCOUNT_ID}')

def _atomic(path, text):
    tmp = f'{path}.tmp.{os.getpid()}.{os.urandom(4).hex()}'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)

def _fsync_file(f):
    f.flush(); os.fsync(f.fileno())

def _fsync_dir(path):
    fd = os.open(os.path.dirname(path) or '.', os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)

def _read_deploy():
    p = deploy_config_path()
    if not os.path.exists(p):
        return None
    try:
        d = json.loads(open(p, encoding='utf-8').read())
        return {'journal': d['journal'], 'dev': int(d['dev']), 'ino': int(d['ino']),
                'uuid': d['uuid'], 'epoch': int(d['epoch'])}
    except Exception:
        raise JournalCorrupt('конфигурация развёртывания повреждена')

def _write_deploy(cfg):
    p = deploy_config_path()
    tmp = p + f'.tmp.{os.getpid()}'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(json.dumps(cfg)); _fsync_file(f)
    os.replace(tmp, p)
    _fsync_dir(p)

def _validate_journal_file(path, want_stat=False):
    """Схема и личность читаются с ОДНОГО дескриптора — окна между проверками нет."""
    if not os.path.isabs(path):
        raise JournalCorrupt(f'путь журнала должен быть абсолютным: {path!r}')
    if os.path.islink(path):
        raise JournalCorrupt('журнал не может быть symlink')
    try:
        _fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        f = os.fdopen(_fd, 'r', encoding='utf-8')
    except (FileNotFoundError, IsADirectoryError, OSError):
        raise JournalCorrupt(f'журнал не существует, недоступен либо является symlink: {path!r}')
    try:
        st = os.fstat(f.fileno())
        if not (st.st_mode & 0o100000):
            raise JournalCorrupt('журнал не является обычным файлом')
        head = f.readline().strip()
        if head != JOURNAL_SCHEMA:
            raise JournalCorrupt(f'схема журнала {head!r} != {JOURNAL_SCHEMA!r}')
        real = os.path.realpath(path)
        _sr = os.stat(real)
        if (_sr.st_dev, _sr.st_ino) != (st.st_dev, st.st_ino):
            raise JournalCorrupt('журнал подменён во время проверки — повторите провизию')
    finally:
        f.close()
    return (real, st) if want_stat else real

_JFD = {}                                   # realpath -> [file, глубина]: удерживаемый дескриптор журнала
_LOCKF = {'file': None, 'depth': 0, 'owner': None}   # файловый слой замка: реентерабелен внутри потока

def _clear_pin():
    _PINNED['journal'] = None; _PINNED['epoch'] = None
    _PINNED['ident'] = None; _PINNED['uuid'] = None

def _open_journal_verified(path, check_ident=True):
    """Открыть журнал БЕЗ следования symlink и сверить личность по ЭТОМУ дескриптору."""
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)          # O_NOFOLLOW: подстановка symlink невозможна
    try:
        st = os.fstat(fd)
        if check_ident and _PINNED.get('journal') == os.path.realpath(path) \
                and _PINNED.get('ident') is not None and (st.st_dev, st.st_ino) != _PINNED['ident']:
            raise JournalCorrupt('журнал подменён (другой st_dev/st_ino) — процесс инвалидирован, перезапуск')
        f = os.fdopen(fd, 'r+', encoding='utf-8')
    except Exception:
        os.close(fd); raise
    return f

def _assert_fd_is_path(f, path):
    """Удерживаемый дескриптор обязан всё ещё соответствовать пути: защита от записи в отсоединённый inode."""
    st_fd = os.fstat(f.fileno())
    try:
        st_p = os.stat(path)
    except FileNotFoundError:
        raise JournalCorrupt('журнал по пути исчез — запись в отсоединённый inode запрещена')
    if (st_fd.st_dev, st_fd.st_ino) != (st_p.st_dev, st_p.st_ino):
        raise JournalCorrupt('журнал по пути заменён — удерживаемый дескриптор отсоединён, операция запрещена')

def held_journal_file(journal):
    """Дескриптор владельца замка: чтение и дозапись идут через него же."""
    try: ent = _JFD.get(os.path.realpath(journal))
    except OSError: ent = None
    return ent[0] if ent else None

def _release_lockf(owned):
    _LOCKF['depth'] -= 1
    if _LOCKF['depth'] <= 0 and _LOCKF['file'] is not None:
        try: fcntl.flock(_LOCKF['file'], fcntl.LOCK_UN)
        finally:
            _LOCKF['file'].close(); _LOCKF.update(file=None, depth=0, owner=None)

@contextmanager
def _strategy_lock_raw(blocking=True, journal=None):
    """Мьютекс = flock на дескрипторе САМОГО журнала: один журнал — один владелец,
    независимо от HOME, каталога установки, копии кода и namespace (механизм POSIX)."""
    jp = os.path.realpath(journal if journal else _PINNED['journal'])
    ent = _JFD.get(jp)
    if ent and _HELD.get(jp) == (os.getpid(), threading.get_ident()):
        ent[1] += 1
        try: yield ent[0]
        finally: ent[1] -= 1
        return
    _same = (_PINNED.get('journal') == jp and _PINNED.get('ident') is not None)
    _me = (os.getpid(), threading.get_ident())
    if _LOCKF['owner'] == _me:                            # ЗАМОК 1 уже удерживается этим потоком
        lockf = _LOCKF['file']; _LOCKF['depth'] += 1; _own_lockf = False
    else:
        lockf = open(lock_path(), 'a+')                   # ЗАМОК 1: фиксированный файл установки
        try:
            fcntl.flock(lockf, fcntl.LOCK_EX if blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB))
        except OSError:
            lockf.close(); raise
        _LOCKF.update(file=lockf, depth=1, owner=_me); _own_lockf = True
    try:
        f = _open_journal_verified(jp, check_ident=_same)  # ЗАМОК 2: дескриптор самого журнала
        fcntl.flock(f, fcntl.LOCK_EX if blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB))
    except BaseException:
        _release_lockf(_own_lockf); raise
    try:
        _assert_fd_is_path(f, jp)
    except BaseException:
        fcntl.flock(f, fcntl.LOCK_UN); f.close()
        _release_lockf(_own_lockf)
        raise
    _JFD[jp] = [f, 1]; _HELD[jp] = (os.getpid(), threading.get_ident())
    try:
        yield f
    finally:
        _HELD.pop(jp, None); _JFD.pop(jp, None)
        fcntl.flock(f, fcntl.LOCK_UN); f.close()
        _release_lockf(_own_lockf)


def provision_journal_pin(journal_abspath, force=False):
    """ЯВНАЯ провизия развёртывания. Ждёт освобождения замка стратегии (сериализация со
    старым исполнителем), затем поднимает МОНОТОННУЮ эпоху — все старые процессы инвалидируются."""
    if not os.path.isabs(journal_abspath) or not os.path.exists(os.path.realpath(journal_abspath)):
        raise JournalCorrupt(f'журнал должен быть существующим абсолютным файлом: {journal_abspath!r}')
    _prev = _read_deploy()
    _old = _prev['journal'] if _prev else None
    if _old and os.path.realpath(journal_abspath) != _old and os.path.exists(_old):
        with _strategy_lock_raw(blocking=True, journal=_old):      # ждём владельца СТАРОГО журнала
            return _provision_locked(journal_abspath, force)
    return _provision_locked(journal_abspath, force)

def _provision_locked(journal_abspath, force):
    with _strategy_lock_raw(blocking=True, journal=os.path.realpath(journal_abspath)) as _lockedf:
        j = os.path.realpath(journal_abspath)
        _lockedf.seek(0)                                   # валидируем ИМЕННО заблокированный дескриптор
        if _lockedf.readline().strip() != JOURNAL_SCHEMA:
            raise JournalCorrupt(f'схема журнала != {JOURNAL_SCHEMA!r}')
        st = os.fstat(_lockedf.fileno())
        _assert_fd_is_path(_lockedf, j)
        cur = _read_deploy()
        if cur is not None:
            same = (cur['journal'] == j and cur['dev'] == st.st_dev and cur['ino'] == st.st_ino)
            if same and not force:
                return j
            if not force:
                raise JournalCorrupt(f'развёртывание уже указывает на {cur["journal"]} — смена только с force=True')
            epoch = cur['epoch'] + 1
        else:
            epoch = 1
        _write_deploy({'journal': j, 'dev': st.st_dev, 'ino': st.st_ino,
                       'uuid': uuid.uuid4().hex, 'epoch': epoch})
        st2 = os.stat(j)
        if (st2.st_dev, st2.st_ino) != (st.st_dev, st.st_ino):
            raise JournalCorrupt('журнал подменён во время провизии — повторите команду')
    _clear_pin()
    return j

def configure(journal_abspath):
    """Пиновка НЕИЗМЕНЯЕМА в процессе; требует существующей конфигурации развёртывания."""
    j = _validate_journal_file(journal_abspath)
    if _PINNED['journal'] is not None:
        if _PINNED['journal'] != j:
            raise JournalCorrupt(f'перепиновка запрещена: уже пинован {_PINNED["journal"]}')
        return j
    cfg = _read_deploy()
    if cfg is None:
        raise JournalCorrupt('конфигурация развёртывания отсутствует — выполните '
                             'python3 mr_engine.py --provision-journal АБСОЛЮТНЫЙ_ПУТЬ')
    if cfg['journal'] != j:
        raise JournalCorrupt(f'конфигурация указывает {cfg["journal"]} — alias/чужой путь отклонён')
    st = os.stat(j)
    if (cfg['dev'], cfg['ino']) != (st.st_dev, st.st_ino):
        raise JournalCorrupt('журнал по этому пути имеет другую личность (st_dev/st_ino) — требуется провизия')
    _PINNED['journal'] = j
    _PINNED['epoch'] = cfg['epoch']
    _PINNED['ident'] = (st.st_dev, st.st_ino)
    _PINNED['uuid'] = cfg['uuid']
    return j

def test_configure(journal_abspath):
    """ТОЛЬКО ДЛЯ ТЕСТОВ: переприсвоение пина процесса на журнал (с провизией при необходимости)."""
    _clear_pin()
    j = _validate_journal_file(journal_abspath)
    cur = _read_deploy()
    st = os.stat(j)
    if cur is None or cur['journal'] != j or (cur['dev'], cur['ino']) != (st.st_dev, st.st_ino):
        provision_journal_pin(j, force=cur is not None)
    return configure(j)

def canonical_journal(journal):
    if _PINNED['journal'] is None:
        raise JournalCorrupt('путь журнала не сконфигурирован — вызовите mr_engine.configure()')
    if os.path.islink(journal):
        raise JournalCorrupt('журнал не может быть symlink')
    if os.path.realpath(journal) != _PINNED['journal']:
        raise JournalCorrupt(f'путь журнала не совпадает с пинованным ({_PINNED["journal"]}) — alias отклонён')
    st = os.stat(_PINNED['journal'])
    if (st.st_dev, st.st_ino) != _PINNED['ident']:
        raise JournalCorrupt('журнал подменён (другой st_dev/st_ino) — процесс инвалидирован, перезапуск')
    cfg = _read_deploy()
    if cfg is None or cfg['journal'] != _PINNED['journal'] or cfg['epoch'] != _PINNED['epoch'] \
            or cfg['uuid'] != _PINNED['uuid']:
        raise JournalCorrupt('процесс инвалидирован: конфигурация развёртывания изменена (reprovision) — перезапуск')
    return _PINNED['journal']

def strategy_lock_path(journal=None):
    """Объект блокировки — фиксированный файл strategy+account, не зависящий от журнала."""
    return lock_path()

@contextmanager
def _locked(path):
    canonical_journal(path)                 # личность журнала и эпоха проверяются до захвата
    with _strategy_lock_raw(blocking=True, journal=path):
        yield

@contextmanager
def hold_strategy_lock(journal):
    """Эксклюзивный НЕБЛОКИРУЮЩИЙ захват на весь торговый цикл исполнителя."""
    canonical_journal(journal)
    try:
        with _strategy_lock_raw(blocking=False, journal=journal):
            yield
    except OSError:
        raise RuntimeError('журнал уже заблокирован другим процессом')

def journal_rows(journal, asof=None):
    journal = canonical_journal(journal)
    try:
        _hf = held_journal_file(journal)
        if _hf is not None:
            _assert_fd_is_path(_hf, os.path.realpath(journal))
            _hf.seek(0); _body = _hf.read()
            _assert_fd_is_path(_hf, os.path.realpath(journal))   # чтение устаревшего inode = инцидент
        else:
            _vf = _open_journal_verified(journal)
            try: _body = _vf.read()
            finally: _vf.close()
        import io as _io2
        rd = csv.DictReader(_io2.StringIO(_body), restkey='_extra')
        if rd.fieldnames is not None and rd.fieldnames != ['asof', 'event', 'detail']:
            raise JournalCorrupt(f'журнал: схема заголовка {rd.fieldnames!r} != [asof, event, detail]')
        rows = list(rd)
    except FileNotFoundError:
        return []
    out = []
    for i, r in enumerate(rows):
        if r.get('_extra'):
            raise JournalCorrupt(f'журнал: строка {i+2} содержит лишние поля')
        if r.get('asof') is None or r.get('event') is None or r.get('detail') is None:
            raise JournalCorrupt(f'журнал: строка {i+2} не соответствует схеме')
        try: d = date.fromisoformat(r['asof'])
        except Exception:
            raise JournalCorrupt(f'журнал: строка {i+2} с нечитаемой датой {r["asof"]!r}')
        if r['event'] not in KNOWN_EVENTS:
            raise JournalCorrupt(f'журнал: строка {i+2} с неизвестным событием {r["event"]!r}')
        np = len([x for x in r['detail'].split('|')])
        need = {'SWITCH_SIGNAL': 2, 'OWNER_APPROVE': 2, 'TRANSITION_OPEN': 3, 'TRANSITION_COMPLETE': 3,
                'TRANSITION_ABORT': 3, 'TRANSITION_MIXED': 3, 'TRANSITION_RESOLVED': 3,
                'GRANULARITY_EXCEPTION': 2}.get(r['event'])
        if need is not None and np != need:
            raise JournalCorrupt(f'журнал: строка {i+2} — {r["event"]} требует {need} компонент detail, найдено {np}')
        if asof is None or d <= asof: out.append((d, r['event'], r['detail']))
    return out

def derive_state(journal, asof):
    journal = canonical_journal(journal)
    """Возвращает (route, pending, mixed, anomalies). COMPLETE действует ТОЛЬКО при
    совпадении цели с ожидающим сигналом; повтор той же записи сразу после — идемпотентен;
    всё прочее — аномалия (маршрут не меняется, фиксируется)."""
    route, pending, sid, open_tid, mixed_key, mixed = 'F', None, '', '', '', False
    anomalies = []; last_eff = None
    for d, ev, det in journal_rows(journal, asof):
        parts = [x.strip() for x in det.split('|')]
        tgt = parts[0]
        ev_sid = parts[1] if len(parts) > 1 else ''
        ev_tid = parts[2] if len(parts) > 2 else ''
        if ev == 'SWITCH_SIGNAL':
            pending = tgt; sid = ev_sid; open_tid = ''; last_eff = None
        elif ev == 'TRANSITION_OPEN':
            if pending == tgt and ev_sid == sid and ev_tid and not open_tid:
                open_tid = ev_tid
            elif open_tid and ev_tid == open_tid and pending == tgt and ev_sid == sid:
                continue
            else:
                anomalies.append(f'{d}: OPEN {det!r} — повторный захват либо нет сигнала')
        elif ev == 'TRANSITION_COMPLETE':
            if mixed:
                anomalies.append(f'{d}: COMPLETE {det!r} при открытом MIXED — запрещено')
            elif pending == tgt and tgt in ('F', 'E') and ev_sid == sid and open_tid and ev_tid == open_tid:
                route = tgt; pending = None; sid = ''; open_tid = ''; last_eff = ('C', det.strip())
            elif last_eff == ('C', det.strip()):
                continue
            else:
                anomalies.append(f'{d}: COMPLETE {det!r} без открытого перехода/совпадения sid+tid')
        elif ev == 'TRANSITION_ABORT':
            if pending == tgt and ev_sid == sid and ((not open_tid) or ev_tid == open_tid):
                pending = None; sid = ''; open_tid = ''; last_eff = ('A', det.strip())
            elif last_eff == ('A', det.strip()): continue
            else: anomalies.append(f'{d}: ABORT {det!r} без соответствия')
        elif ev == 'TRANSITION_MIXED':
            if ev_sid == sid and open_tid and ev_tid == open_tid:
                mixed = True; mixed_key = f'{ev_sid}|{ev_tid}'
            else:
                anomalies.append(f'{d}: MIXED {det!r} без открытого перехода (OPEN обязателен)')
        elif ev == 'TRANSITION_RESOLVED':
            if not mixed:
                anomalies.append(f'{d}: RESOLVED {det!r} без открытого MIXED')
            elif f'{ev_sid}|{ev_tid}' != mixed_key:
                anomalies.append(f'{d}: RESOLVED {det!r} с чужой привязкой sid|tid')
            elif tgt not in ('F', 'E'):
                anomalies.append(f'{d}: RESOLVED {det!r} с недопустимой целью — MIXED сохраняется')
            else:
                route = tgt
                mixed = False; mixed_key = ''; pending = None; sid = ''; open_tid = ''
    return route, pending, mixed, anomalies, sid, open_tid, mixed_key

def switches_3y(journal, asof):
    journal = canonical_journal(journal)
    pending = None; sid = ''; open_tid = ''; last_eff = None; route = 'F'
    mixed = False; mixed_key = ''; n = 0
    for d, ev, det in journal_rows(journal, asof):
        parts = [x.strip() for x in det.split('|')]
        tgt = parts[0]
        ev_sid = parts[1] if len(parts) > 1 else ''
        ev_tid = parts[2] if len(parts) > 2 else ''
        if ev == 'SWITCH_SIGNAL': pending = tgt; sid = ev_sid; open_tid = ''; last_eff = None
        elif ev == 'TRANSITION_OPEN' and pending == tgt and ev_sid == sid: open_tid = ev_tid
        elif ev == 'TRANSITION_COMPLETE':
            if pending == tgt and tgt in ('F', 'E') and ev_sid == sid and open_tid and ev_tid == open_tid \
                    and det.strip() != last_eff:
                pending = None; sid = ''; open_tid = ''; last_eff = det.strip(); route = tgt
                if d > asof - timedelta(days=365*3): n += 1
        elif ev == 'TRANSITION_ABORT' and pending == tgt and ev_sid == sid:
            pending = None; sid = ''; open_tid = ''
        elif ev == 'TRANSITION_MIXED' and ev_sid == sid and open_tid and ev_tid == open_tid:
            mixed = True; mixed_key = f'{ev_sid}|{ev_tid}'
        elif ev == 'TRANSITION_RESOLVED' and mixed and f'{ev_sid}|{ev_tid}' == mixed_key:
            mixed = False
            if tgt in ('F', 'E') and tgt != route:
                route = tgt
                if d > asof - timedelta(days=365*3): n += 1
    return n

def _append_raw(journal, asof, event, detail):
    import io
    j = canonical_journal(journal)
    buf = io.StringIO()
    csv.writer(buf, quoting=csv.QUOTE_MINIMAL).writerow([str(asof), event, detail])
    _hf = held_journal_file(j)                    # дозапись через ТОТ ЖЕ проверенный дескриптор
    if _hf is not None:
        _assert_fd_is_path(_hf, j)                # до записи
        _hf.seek(0, os.SEEK_END); _hf.write(buf.getvalue()); _fsync_file(_hf)
        _assert_fd_is_path(_hf, j)                # ПОСЛЕ записи: подмена в окне = инцидент, не тихая потеря
    else:
        f = _open_journal_verified(j)
        try:
            f.seek(0, os.SEEK_END); f.write(buf.getvalue()); _fsync_file(f)
        finally:
            f.close()

def append_event(journal, asof, event, detail):
    # Имя события проверяется ДО записи: незнакомое имя не должно попадать в нормативный
    # журнал и превращать его в «повреждённый» для всех последующих запусков (№26).
    if event not in KNOWN_EVENTS:
        raise ValueError(f'событие {event!r} не входит в KNOWN_EVENTS — запись отклонена')
    # межпроцессная блокировка вокруг read-modify-write
    try: body = open(journal, encoding='utf-8').read()
    except FileNotFoundError: body = 'asof,event,detail\n'
    if not body.startswith('asof'): body = 'asof,event,detail\n' + body
    with _locked(journal):
        _append_raw(journal, asof, event, detail)

def write_state_cache(state_path, journal, asof):
    route, pending, mixed, anom, sid, open_tid, mkey = derive_state(journal, asof)
    _atomic(state_path, 'route,pending_route,mixed,anomalies,asof,switches_3y\n'
            f'{route},{pending or ""},{int(mixed)},{len(anom)},{asof},{switches_3y(journal, asof)}\n')

def load_tariff(path, asof, prearranged=False):
    rows = [r for r in csv.DictReader(open(path, encoding='utf-8'))
            if date.fromisoformat(r['as_of']) <= asof]
    if not rows: raise ValueError('тариф: нет строк на дату')
    last = max(r['as_of'] for r in rows)
    tiers = sorted((float(r['tier_limit']), float(r['spread_pct'])) for r in rows if r['as_of'] == last)
    prev = 0.0
    for lim, sp in tiers:
        if not (math.isfinite(lim) and math.isfinite(sp)) or lim <= prev or not (0.0 <= sp < 0.10):
            raise ValueError('тариф: неконечные/невозрастающие/вне диапазона значения')
        prev = lim if math.isfinite(lim) else prev
    if not prearranged:
        tiers = [(l, s + (0.01 if l > 50_000_000 else 0.0)) for l, s in tiers]
    return last, tiers

def loan_amt(debit, tiers):
    rest, amt, prev = debit, 0.0, 0.0
    for lim, sp in tiers:
        take = min(rest, lim-prev); prev = lim
        if take <= 0: continue
        amt += take*sp; rest -= take
        if rest <= 0: break
    return amt + debit*0.0010

def eq_E(nlv, tiers, drag_pp=DRAG_PP):
    lp = (loan_amt(1.05*nlv, tiers)/nlv*STATE_BOTH + loan_amt(0.05*nlv, tiers)/nlv*ONE_LEG)*100.0
    return lp + TER_PP + drag_pp

def eq_F(s_eq, s_zn, basis):
    return (s_eq+basis)*STATE_EQ + s_zn*STATE_BD + 0.062

def _num(x):
    v = float(x)
    if not math.isfinite(v): raise ValueError('не конечное')
    return v

def validate(rows, asof):
    errs = []; seen = set(); prev_end = None
    for r in rows:
        q = r['quarter_id']
        if q in seen: errs.append(f'{q}: дубликат')
        seen.add(q)
        try:
            ws = date.fromisoformat(r['window_start']); we = date.fromisoformat(r['window_end'])
            r['_ws'], r['_we'] = ws, we
            if ws >= we: errs.append(f'{q}: start>=end')
            if not (80 <= (we-ws).days <= 100): errs.append(f'{q}: не квартальная длина')
            if prev_end is not None and ws != prev_end: errs.append(f'{q}: разрыв непрерывности')
            prev_end = we
        except Exception:
            errs.append(f'{q}: нечитаемые даты'); prev_end = None; continue
        if r['status'] == 'OK':
            if we > asof: errs.append(f'{q}: OK при незавершённом окне')
            u = urlparse(r['source_url'].strip())
            if u.scheme not in ('http', 'https') or not u.netloc:
                errs.append(f'{q}: источник не URL с адресом')
            try:
                fd = date.fromisoformat(r['fix_date'].strip())
                if fd > asof: errs.append(f'{q}: fix_date в будущем')
            except Exception: errs.append(f'{q}: fix_date не дата')
            if r['quote_base'] not in ('EFFR', 'SOFR3M'): errs.append(f'{q}: база')
            for f in ('s_eq_pp', 's_zn_pp'):
                try: _num(r[f])
                except Exception: errs.append(f'{q}: {f} обязателен и конечен')
            if r['quote_base'] == 'SOFR3M':
                try: _num(r['basis_to_effr_pp'])
                except Exception: errs.append(f'{q}: basis обязателен')
    return errs

def emerg_20d(path, asof):
    rows = [r for r in csv.DictReader(open(path, encoding='utf-8'))
            if date.fromisoformat(r['date']) <= asof]
    ds = [date.fromisoformat(r['date']) for r in rows]
    if len(set(ds)) < 20 or ds != sorted(ds) or len(ds) != len(set(ds)):
        raise ValueError('аварийный файл: нужно ≥20 УНИКАЛЬНЫХ возрастающих дат')
    last = rows[-20:]
    if date.fromisoformat(last[-1]['date']) < asof - timedelta(days=7):
        raise ValueError('аварийный файл устарел')
    m = sum(_num(r['active_spread_pp']) for r in last)/20.0
    g = sum(_num(r['alt_gap_pp']) for r in last)/20.0
    avail = all(r['alt_available'].strip() == '1' for r in last[-5:])
    return m, g, avail

def run(inputs, tariff, state_path, journal, asof, nlv, emerg=None, prearranged=False, write=True):
    asof = date.fromisoformat(asof)
    try: nlv = _num(nlv)
    except Exception: print('DECISION=INVALID (NLV не конечное число)'); return INVALID
    if nlv <= 0: print('DECISION=INVALID (NLV <= 0)'); return INVALID
    try:
        route, pending, mixed, anomalies, pending_sid, open_tid, mkey = derive_state(journal, asof)
        n_sw = switches_3y(journal, asof)
    except JournalCorrupt as ex:
        print(f'DECISION=INVALID (журнал повреждён: {ex})'); return INVALID
    try:
        t_date, tiers = load_tariff(tariff, asof, prearranged)
        e_cost = eq_E(nlv, tiers)
    except ValueError as ex:
        print(f'DECISION=INVALID ({ex})'); return INVALID
    f_blocked = nlv < MIN_NLV_F
    if f_blocked:
        print(f'  ПОРОГ §8: NLV {nlv:,.0f} ниже {MIN_NLV_F:,.0f} — маршрут Ф недоступен')
        if route == 'F':
            print('DECISION=REVIEW (действующий маршрут Ф при NLV ниже порога §8 — '
                  'требуется решение заказчика: пополнение счёта либо перевод в Е)')
            return REVIEW
    if anomalies:
        for a in anomalies: print('  АНОМАЛИЯ ЖУРНАЛА: ' + a)
        print('DECISION=REVIEW (журнал содержит несверенные события — переключения заблокированы)')
        return REVIEW
    if mixed:
        print('DECISION=REVIEW (книга в состоянии MIXED — до ручного TRANSITION_RESOLVED переключения заблокированы)')
        return REVIEW
    print(f'МР v7.7 [{asof}] маршрут {route}, pending {pending or "—"}, переключений/3г {n_sw}, '
          f'NLV {nlv/1e6:.2f} млн, тариф {t_date}, Е = {e_cost:.4f}')
    rows = list(csv.DictReader(open(inputs, encoding='utf-8')))
    errs = validate(rows, asof)
    for e in errs: print('  ОШИБКА: ' + e)
    decision = HOLD; note = 'условий переключения нет'; target = None
    if errs:
        decision, note = INVALID, 'входы невалидны'
    else:
        done = sorted([r for r in rows if '_we' in r and r['_we'] <= asof], key=lambda r: r['_we'])
        if len(done) < 2: note = f'завершённых кварталов {len(done)} < 2'
        elif done[-2]['status'] != 'OK' or done[-1]['status'] != 'OK':
            note = 'последние завершённые не подтверждены'
        elif done[-2]['_we'] != done[-1]['_ws']: note = 'последние завершённые не смежны'
        else:
            def gap(r):
                basis = _num(r['basis_to_effr_pp']) if r['quote_base'] == 'SOFR3M' else \
                        (_num(r['basis_to_effr_pp']) if r['basis_to_effr_pp'].strip() not in ('', 'NA') else 0.0)
                return eq_F(_num(r['s_eq_pp']), _num(r['s_zn_pp']), basis) - e_cost
            ga, gb = gap(done[-2]), gap(done[-1])
            print(f'  последние два: {done[-2]["quarter_id"]} {ga:+.3f} / {done[-1]["quarter_id"]} {gb:+.3f}')
            if abs(ga) >= GAP and abs(gb) >= GAP and (ga > 0) == (gb > 0):
                target = 'E' if gb > 0 else 'F'
                if target == route: note = f'цель {target} уже действует'
                elif pending == target: note = 'переход уже инициирован, ожидает исполнения'
                elif pending is not None:
                    decision, note = REVIEW, f'встречный сигнал при незакрытом pending {pending}'
                elif budget_exhausted(n_sw):
                    decision, note = REVIEW, f'бюджет переключений исчерпан ({n_sw}/{MAX_SWITCHES_3Y} за 3 года)'
                elif target == 'F' and f_blocked:
                    decision, note = REVIEW, f'сигнал в Ф при NLV ниже порога §8 ({MIN_NLV_F:,.0f})'
                else:
                    decision = SWITCH_E if target == 'E' else SWITCH_F
                    note = 'два последних смежных однонаправленно ≥0,25'
            else: note = 'мёртвая зона либо разнонаправленно'
    if emerg and decision == HOLD:
        m, g, avail = emerg_20d(emerg, asof)
        print(f'  20-дн: спред {m:.2f}, разрыв {g:+.2f}, альтернатива {"да" if avail else "НЕТ"}')
        if m > 1.5:
            if not avail: decision, note = REDUCE, '20-дн >1,5, альтернатива недоступна'
            elif abs(g) >= GAP:
                t2 = 'E' if g > 0 else 'F'
                if t2 == 'F' and f_blocked:
                    decision, note = REVIEW, 'аварийный сигнал в Ф при NLV ниже порога §8'
                elif t2 != route and pending is None and not budget_exhausted(n_sw):
                    decision, target, note = (SWITCH_E if t2 == 'E' else SWITCH_F), t2, 'аварийное по 20-дн'
                else: decision, note = REVIEW, 'авария при pending/лимите/действующей цели'
            else: decision, note = REVIEW, '20-дн >1,5, разрыв <0,25'
    if not (write and decision in (SWITCH_F, SWITCH_E)):
        print(f'DECISION={NAMES[decision]} ({note})')
    if write and decision in (SWITCH_F, SWITCH_E):
        with _locked(journal):
            r2, pend2, mx2, an2, _, _, _ = derive_state(journal, asof)
            n2 = switches_3y(journal, asof)
            if an2 or mx2:                                    # 1) аномалия/MIXED
                print('DECISION=REVIEW (журнал изменился параллельно: аномалия/MIXED)')
                write_state_cache(state_path, journal, asof)
                return REVIEW
            if r2 == target:                                  # 2) цель уже действует
                print('DECISION=HOLD (цель уже действует после параллельного COMPLETE)')
                write_state_cache(state_path, journal, asof)
                return HOLD
            if pend2 == target:                               # 3) тот же переход уже инициирован
                print('DECISION=HOLD (переход уже инициирован параллельным запуском)')
                write_state_cache(state_path, journal, asof)
                return HOLD
            if pend2 is not None:                             # 4) встречный pending — как до замка
                print(f'DECISION=REVIEW (встречный сигнал при незакрытом pending {pend2})')
                write_state_cache(state_path, journal, asof)
                return REVIEW
            if budget_exhausted(n2):                          # 5) бюджет
                print(f'DECISION=REVIEW (под замком: бюджет исчерпан параллельно — {n2}/{MAX_SWITCHES_3Y} за 3 года)')
                write_state_cache(state_path, journal, asof)
                return REVIEW
            existing = {det.split('|')[1].strip() for _, ev, det in journal_rows(journal, asof)
                        if ev == 'SWITCH_SIGNAL' and '|' in det}
            k = len(existing) + 1
            sid = f'{asof}#{k}'
            while sid in existing:
                k += 1; sid = f'{asof}#{k}'
            _append_raw(journal, asof, 'SWITCH_SIGNAL', f'{target}|{sid}')
            print(f'DECISION={NAMES[decision]} (сигнал записан под замком)')
        write_state_cache(state_path, journal, asof)
        print(f'  журнал: SWITCH_SIGNAL {target}|{sid}; фактический маршрут НЕ изменён, pending={target}')
    elif write and decision in (REDUCE, REVIEW):
        append_event(journal, asof, 'REDUCE_SIGNAL' if decision == REDUCE else 'REVIEW_SIGNAL', note)
        write_state_cache(state_path, journal, asof)
        print(f'  журнал: {"REDUCE" if decision == REDUCE else "REVIEW"}_SIGNAL — решение аудируется')
    elif write:
        write_state_cache(state_path, journal, asof)
    return decision

def confirm_transition(journal, state_path, asof, target, kind='complete', tid='', sid=''):
    """Вызывается исполнителем. kind: open | complete | abort | mixed | resolved.
    ВЕСЬ цикл «прочитал-проверил-записал» — под одним межпроцессным замком журнала:
    параллельные подтверждения сериализуются, двойной захват OPEN невозможен.
    Повтор OPEN с тем же sid|tid идемпотентен (True без повторной записи).
    MIXED/ABORT/RESOLVED требуют точного совпадения tid с открытым переходом."""
    journal = canonical_journal(journal)
    d = date.fromisoformat(asof) if isinstance(asof, str) else asof
    with _locked(journal):
        route, pending, mixed, _, pending_sid, open_tid, mkey = derive_state(journal, d)
        if kind == 'open':
            if open_tid and tid == open_tid and pending == target and sid == pending_sid:
                return True                                    # идемпотентный повтор захвата
            if open_tid:
                print(f'open отклонён: переход уже захвачен tid={open_tid!r}')
                return False
            if pending != target or target not in ('F', 'E') or sid != pending_sid or not sid or not tid:
                print(f'open отклонён: pending={pending!r}/{pending_sid!r}, цель={target!r}/sid={sid!r}')
                return False
        if kind in ('complete', 'abort', 'mixed'):
            if pending != target or target not in ('F', 'E') or sid != pending_sid or not sid:
                print(f'confirm отклонён: pending={pending!r}/{pending_sid!r}, цель={target!r}/sid={sid!r}')
                return False
            if not open_tid or tid != open_tid:
                print(f'confirm отклонён: tid {tid!r} не совпадает с открытым переходом {open_tid!r}')
                return False
            if kind == 'complete' and mixed:
                print('confirm отклонён: COMPLETE при открытом MIXED запрещён')
                return False
        if kind == 'resolved':
            if not mixed or target not in ('F', 'E') or f'{sid}|{tid}' != mkey:
                print('confirm отклонён: RESOLVED требует открытый MIXED и точную привязку sid|tid')
                return False
        ev = {'open': 'TRANSITION_OPEN', 'complete': 'TRANSITION_COMPLETE', 'abort': 'TRANSITION_ABORT',
              'mixed': 'TRANSITION_MIXED', 'resolved': 'TRANSITION_RESOLVED'}[kind]
        det = f'{target}|{sid}|{tid}' if (sid or tid) else target
        _append_raw(journal, asof, ev, det)
    write_state_cache(state_path, journal, d)
    return True

def grant_granularity(journal, state_path, asof, sid, limit_usd):
    """Разовое разрешение заказчика на превышение owner-cap для конкретного сигнала:
    журнальное событие с привязкой sid и АБСОЛЮТНЫМ лимитом в долларах."""
    append_event(journal, asof, 'GRANULARITY_EXCEPTION', f'{sid}|{limit_usd}')
    d = date.fromisoformat(asof) if isinstance(asof, str) else asof
    write_state_cache(state_path, journal, d)

def approve_switch(journal, state_path, asof, target, sid):
    """Одобрение заказчика на исполнение конкретного сигнала (ред. 31).
    Привязано к цели и sid: одобрение одного сигнала не переносится на другой."""
    if target not in ('F', 'E'):
        raise ValueError('OWNER_APPROVE: недопустимая цель')
    if not str(sid).strip():
        raise ValueError('OWNER_APPROVE: пустой sid')
    append_event(journal, asof, 'OWNER_APPROVE', f'{target}|{sid}')
    d = date.fromisoformat(asof) if isinstance(asof, str) else asof
    write_state_cache(state_path, journal, d)

def find_approval(journal, asof, sid, target):
    """Есть ли одобрение заказчика на ЭТОТ сигнал и ЭТУ цель. Читается ТОЛЬКО из журнала."""
    for d, ev, det in journal_rows(journal, date.fromisoformat(asof) if isinstance(asof, str) else asof):
        if ev == 'OWNER_APPROVE':
            parts = [x.strip() for x in det.split('|')]
            if len(parts) >= 2 and parts[0] == target and parts[1] == sid:
                return True
    return False

def find_grant(journal, asof, sid):
    for d, ev, det in journal_rows(journal, date.fromisoformat(asof) if isinstance(asof, str) else asof):
        if ev == 'GRANULARITY_EXCEPTION':
            parts = [x.strip() for x in det.split('|')]
            if len(parts) >= 2 and parts[0] == sid:
                v = float(parts[1])
                if not math.isfinite(v) or v <= 0:
                    raise JournalCorrupt(f'GRANULARITY_EXCEPTION с неконечным/недопустимым лимитом {parts[1]!r}')
                return v
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--inputs', default='mr_inputs_retro.csv')
    p.add_argument('--tariff', default='mr_tariff.csv')
    p.add_argument('--state', default='mr_state.csv')
    p.add_argument('--journal', default='mr_journal.csv')
    p.add_argument('--asof')
    p.add_argument('--nlv')
    p.add_argument('--emerg', default=None)
    p.add_argument('--prearranged-above-50m', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--provision-journal', metavar='ПУТЬ', default=None)
    p.add_argument('--force-reprovision', action='store_true')
    p.add_argument('--approve-switch', action='store_true',
                   help='ред. 31: записать OWNER_APPROVE на ОЖИДАЮЩИЙ сигнал (акт заказчика)')
    a = p.parse_args()
    if a.provision_journal:
        j = provision_journal_pin(os.path.abspath(a.provision_journal), force=a.force_reprovision)
        print(f'маркер развёртывания записан: {j}'); sys.exit(0)
    if a.approve_switch:
        if not a.asof:
            p.error('--approve-switch требует --asof')
        a.journal = os.path.abspath(a.journal)
        configure(a.journal)
        d = date.fromisoformat(a.asof)
        route, pending, mixed, anom, sid, _otid, _mk = derive_state(a.journal, d)
        if mixed:
            print('ОТКАЗ: книга в состоянии MIXED — сначала TRANSITION_RESOLVED'); sys.exit(1)
        if not pending or not sid:
            print('ОТКАЗ: нет ожидающего сигнала для одобрения'); sys.exit(1)
        if find_approval(a.journal, d, sid, pending):
            print(f'уже одобрено: OWNER_APPROVE {pending}|{sid}'); sys.exit(0)
        approve_switch(a.journal, a.state, a.asof, pending, sid)
        print(f'записано OWNER_APPROVE {pending}|{sid} — переход в маршрут {pending} разрешён заказчиком')
        sys.exit(0)
    if not a.asof or not a.nlv:
        p.error('требуются --asof и --nlv (либо --provision-journal ПУТЬ)')
    a.journal = os.path.abspath(a.journal)
    configure(a.journal)
    sys.exit(run(a.inputs, a.tariff, a.state, a.journal, a.asof, a.nlv, a.emerg,
                 a.prearranged_above_50m, write=not a.dry_run))

if __name__ == '__main__':
    main()
