#!/usr/bin/env python3
"""Устойчивое состояние торговой сессии, блокировка и сверка с книгой брокера.

ЗАЧЕМ. До этого модуля контур считал решение и отдавал заявки, но нигде не хранил, что он
уже сделал. Из этого следовали три дыры, названные внешней рецензией условием того, чтобы
бумажный этап §8 вообще что-то доказывал:

  (1) перезапуск терял книгу — при следующем запуске контур считал позицию нулевой;
  (2) ежедневный контур и переходный исполнитель могли работать одновременно и подать
      встречные заявки по одной и той же книге;
  (3) фактическая позиция у брокера никогда не сверялась с нашей: расхождение после
      частичного исполнения, отказа или ПОЗДНЕГО исполнения отменённой заявки осталось бы
      незамеченным до ближайшего инцидента О-5.

ПРИНЦИПЫ. Состояние пишется атомарно (временный файл + os.replace + fsync каталога) и несёт
номер сессии и хэш содержимого: оборванная запись не может дать полуфайл. Блокировка —
одна на всю программу, общая с переходным исполнителем, поэтому две программы не работают
по книге одновременно. Сверка выполняется ДО расчёта: расхождение с брокером не
исправляется молча, а останавливает сессию.
"""
import fcntl, hashlib, json, os, tempfile
from contextlib import contextmanager
from pathlib import Path

STATE_VERSION = 1
LOCK_NAME = 'addfut-book.lock'      # ОДНО имя для daily и transition

def active_route():
    """ДЕЙСТВУЮЩИЙ маршрут по ДВУМ источникам (двадцать седьмой круг, №1).

    route.txt читает автопилот, нормативный журнал МР ведёт переходный исполнитель. Оба
    обязаны говорить одно и то же: расхождение означает оборванный переход, а потеря
    route.txt — не повод молча выбрать Ф (именно так ошибочный ручной запуск мог открыть
    вторую книгу в обход одобрения заказчика).
    """
    import os as _o
    rt = lock_dir() / 'route.txt'
    _file = rt.read_text(encoding='utf-8').strip() if rt.exists() else None
    _jrn = None
    try:
        import sys as _s
        _root = Path(__file__).resolve().parent.parent
        if str(_root) not in _s.path:
            _s.path.insert(0, str(_root))
        import mr_engine as _M
        import datetime as _dt
        _jp = _o.environ.get('ADDFUT_MR_JOURNAL') or (_root / 'mr_journal.csv')
        # ЖУРНАЛ МР КАК ВТОРОЙ ИСТОЧНИК — ОТКРЫТЫЙ ДОЛГ (двадцать восьмой круг, №1).
        # Прямой вызов derive_state требует mr_engine.configure(), а тот пинует путь и
        # отвергает чужой — то есть конфликтует с конфигурацией исполнителя. Пока
        # маршрут подтверждается ТОЛЬКО route.txt: это fail-open, и он ЗАЯВЛЕН как
        # незакрытый, а не выдан за сверку двух источников.
        _jrn = None
    except Exception:
        _jrn = None                 # см. пометку долга выше
    if _file is None and _jrn is None:
        raise RuntimeError(f'{rt}: файла маршрута нет и журнал МР недоступен — действующий '
                           f'маршрут неизвестен, торговля запрещена (О-5)')
    if _file is None:
        return _jrn
    if _jrn is not None and _jrn != _file:
        raise RuntimeError(f'route.txt говорит {_file}, журнал МР — {_jrn}: переход '
                           f'оборван или состояние подменено, торговля запрещена (О-5)')
    return _file


def lock_dir(hint=None):
    """ЕДИНСТВЕННЫЙ каталог блокировки. Прежде daily брал его из своего dirpath, а
    transition — из каталога своего state_path; при разных путях «общая» блокировка
    оказывалась двумя разными файлами и ничего не защищала. Приоритет: переменная
    окружения ADDFUT_LOCK_DIR, затем ЯВНЫЙ hint, иначе машинный каталог.

    hint ОБЯЗАН уважаться: восьмой круг показал, что он молча игнорировался — процесс,
    взявший hold_book_lock(tmp), на деле запирал ~/.addfut, а сосед с ADDFUT_LOCK_DIR=tmp
    запирал tmp: два файла, никакой взаимности. Межпроцессный стенд поймал это в первом же
    прогоне: оба «взяли» один и тот же замок."""
    env = os.environ.get('ADDFUT_LOCK_DIR')
    if env:
        return Path(env)
    if hint:
        return Path(hint)
    # НЕ каталог кода. Блокировка защищает СЧЁТ, а не файлы: две копии пакета на одной
    # машине брали бы разные каталоги и не видели друг друга. Место фиксировано и одно
    # для всех копий; переопределяется только явной переменной окружения.
    return Path(os.path.expanduser('~/.addfut'))


@contextmanager
def hold_book_lock(dirpath=None, timeout_s=30):
    """Исключительная блокировка книги. Тот же файл берёт переходный исполнитель, поэтому
    ежедневный контур и перевод между маршрутами не могут идти одновременно."""
    d = lock_dir(dirpath); d.mkdir(parents=True, exist_ok=True)
    lk = open(d / LOCK_NAME, 'w')
    try:
        import time
        t0 = time.time()
        while True:
            try:
                fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() - t0 > timeout_s:
                    raise RuntimeError(
                        'книга занята другой программой (ежедневный контур или переходный '
                        'исполнитель); одновременная работа запрещена')
                time.sleep(0.2)
        lk.write(f'{os.getpid()}\n'); lk.flush()
        yield
    finally:
        fcntl.flock(lk, fcntl.LOCK_UN); lk.close()


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                     ensure_ascii=False).encode('utf-8')).hexdigest()


def save(path, book, route, session_no, note=''):
    """Атомарная запись состояния. Оборванная запись не оставляет полуфайла."""
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(version=STATE_VERSION, route=route, session_no=int(session_no),
                   note=note, book={k: v for k, v in vars(book).items()})
    rec = dict(payload=payload, digest=_digest(payload))
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(rec, f, ensure_ascii=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
        dfd = os.open(str(path.parent), os.O_DIRECTORY)
        try:
            os.fsync(dfd)                 # запись каталога, иначе replace может пережить сбой
        finally:
            os.close(dfd)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return rec['digest']


def load(path, cls):
    """Чтение состояния. Повреждённый файл поднимает исключение, а не даёт пустую книгу:
    молча начать с нулевой позиции при существующей книге у брокера — худшее из возможных."""
    path = Path(path)
    if not path.exists():
        return None, 0, None
    rec = json.loads(path.read_text(encoding='utf-8'))
    if _digest(rec['payload']) != rec['digest']:
        raise ValueError(f'{path}: состояние повреждено (хэш не сходится)')
    p = rec['payload']
    if p.get('version') != STATE_VERSION:
        raise ValueError(f"{path}: версия состояния {p.get('version')}, ожидается {STATE_VERSION}")
    # МАРШРУТ ПРОВЕРЯЕТСЯ ДО КОНСТРУИРОВАНИЯ. Поля Book и BookE несовместимы, и попытка
    # собрать книгу другого маршрута падала TypeError раньше, чем становилась понятна
    # причина: состояние просто относится к другому маршруту.
    want = getattr(cls, '__name__', '')
    is_e = (want == 'BookE')
    if (p['route'] == 'E') != is_e:
        raise ValueError(f"{path}: состояние записано для маршрута {p['route']}, "
                         f"запрошен {'E' if is_e else 'F'} — книгу между маршрутами "
                         f"переводит transition.py, а не чтение состояния")
    return cls(**p['book']), p['session_no'], p['route']


# ---------------------------------------------------------------- намерение сессии
# СВЯЗКА «СДЕЛКА + СОСТОЯНИЕ» НЕ АТОМАРНА, И ЭТОГО НЕ ИСПРАВИТЬ. Заявка исполняется у
# брокера, состояние пишется у нас; между этими событиями процесс может умереть, и тогда
# книга у брокера новая, а на диске старая. Атомарность файла (os.replace + fsync) защищает
# от полузаписи, но не от разрыва между двумя системами.
#
# ЧТО МОЖНО СДЕЛАТЬ: записать НАМЕРЕНИЕ до первой заявки. Тогда после сбоя видно не только
# что книги разошлись, но и КАКАЯ книга намечалась — а значит различимы три случая:
# сделка не начиналась (у брокера прежняя книга — намерение снимается), сделка прошла
# целиком (у брокера намеченная книга — она принимается, состояние дописывается), и всё
# остальное (ручной разбор О-5). Без намерения все три выглядят одинаково.
INTENT_SUFFIX = '.intent.json'


def book_path(route, override=None):
    """ЕДИНСТВЕННОЕ место книги, общее для ежедневного контура и переходного исполнителя.

    Прежде контур читал ~/.addfut/book-F.json, а переход после исполнения писал
    ~/.addfut/book.json: без вручную заданной переменной окружения источник истины
    раздваивался ровно в момент, когда брокер уже переведён на другой маршрут.
    """
    env = override or os.environ.get('ADDFUT_BOOK_PATH')
    if env:
        return Path(env)
    return lock_dir() / f'book-{route}.json'


def intent_path(book_path):
    return Path(str(book_path) + INTENT_SUFFIX)


def save_intent(book_path, route, session_no, book_before, book_after, orders,
                session_date=''):
    p = intent_path(book_path)
    payload = dict(version=STATE_VERSION, route=route, session_no=int(session_no),
                   session_date=str(session_date),
                   book_before={k: v for k, v in vars(book_before).items()},
                   book_after={k: v for k, v in vars(book_after).items()},
                   orders=[[i, q] for i, q in orders])
    rec = dict(payload=payload, digest=_digest(payload))
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(rec, f, ensure_ascii=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, p)
        dfd = os.open(str(p.parent), os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return p


def load_intent(book_path):
    p = intent_path(book_path)
    if not p.exists():
        return None
    rec = json.loads(p.read_text(encoding='utf-8'))
    if _digest(rec['payload']) != rec['digest']:
        raise ValueError(f'{p}: намерение повреждено (хэш не сходится) — ручной разбор')
    return rec['payload']


def clear_intent(book_path, archive=True):
    """Снять намерение. ДОКАЗАТЕЛЬСТВО НЕ УНИЧТОЖАЕТСЯ (двадцатый круг, №16).

    Прежде намерение просто удалялось. Разбор оборвавшегося запуска (daily._resume_intent)
    при уже дописанной книге снимает его СРАЗУ, до сверки позиций и заявок с брокером, —
    и если сверка потом находит ПОЗДНЕЕ ИСПОЛНЕНИЕ, файл с book_before, book_after и
    списком заявок уже уничтожен. Именно он нужен, чтобы понять, какую компенсацию
    подавать по О-5: журнал §7 и permId-отчёты говорят, что СЛУЧИЛОСЬ, но не то, что
    НАМЕЧАЛОСЬ. Теперь файл переименовывается в intent-done-*, а не пропадает.
    """
    p = intent_path(book_path)
    if p.exists():
        if archive:
            # УНИКАЛЬНОЕ ИМЯ (двадцать первый круг, №17): секундной метки мало — два
            # снятия в одну секунду затирали прежнее доказательство через os.replace.
            import time as _t
            base = f'{p.stem}-done-{int(_t.time())}'
            dst = p.with_name(f'{base}{p.suffix}')
            _k = 0
            while dst.exists():
                _k += 1
                dst = p.with_name(f'{base}-{_k}{p.suffix}')
            os.replace(str(p), str(dst))
        else:
            p.unlink()
        dfd = os.open(str(p.parent), os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


def expected_positions(book, route):
    """Что ДОЛЖНО быть у брокера по нашему состоянию, в терминах инструментов."""
    if route == 'E':
        return {k: v for k, v in (('CSPX', book.n_eq), ('CBU0', book.n_bd)) if v}
    out = {}
    if book.n_b:
        out[f'ZN{book.ser_b}'] = book.n_b
    if book.n_e:
        if not book.unit_is_mes:
            out[f'ES{book.ser_a}'] = book.n_e
        else:
            es = book.es_held if book.es_held is not None else book.n_e // 10
            mes = book.n_e - 10 * es
            if es:
                out[f'ES{book.ser_a}'] = es
            if mes:
                out[f'MES{book.ser_a}'] = mes
    return out


def check_one_series(pos):
    """У каждой ноги ровно одна серия (двадцатый круг, №10). Отдельной функцией — чтобы у
    защиты была парная мутация, бьющая ровно в неё."""
    _ser = {'А': set(), 'Б': set()}
    for k in pos:
        if k.startswith('MES'):
            _ser['А'].add(k[3:])
        elif k.startswith('ES'):
            _ser['А'].add(k[2:])
        elif k.startswith('ZN'):
            _ser['Б'].add(k[2:])
    for _leg, _ss in _ser.items():
        if len(_ss) > 1:
            raise ValueError(f'нога {_leg}: у брокера несколько серий {sorted(_ss)} — книга '
                             f'не строится, суммировать их в одну серию значит потерять '
                             f'поставочный контракт; ручной разбор (О-5)')


def book_from_broker(cls, positions, route, *, ser_a=None, ser_b=None, unit_is_mes=True,
                     d_fix=0.0, st_eq=None, st_bd=None, roll_pending=False):
    """Собрать состояние из ФАКТИЧЕСКОЙ книги брокера.

    Нужна ровно в одном месте: после перевода между маршрутами. Прежде переходный
    исполнитель записывал журнал и своё состояние, но не состояние ежедневного контура —
    и штатного продолжения торговли после перехода не существовало вовсе: запуск на старом
    маршруте останавливался на расхождении, на новом — падал при чтении чужого формата.

    Книга берётся у брокера, а не вычисляется, потому что после перехода истина именно там.
    """
    pos = {k: float(v) for k, v in (positions or {}).items() if float(v) != 0.0}
    # ПРИЗНАК ОТЛОЖЕННОГО РОЛЛА — ПЕР-НОЖНЫЙ (девятнадцатый круг, №1): строка 'А'/'Б'/'АБ'
    # переносится КАК ЕСТЬ; приведение к bool стирало ноги, и повтор роллил обе.
    _pend = roll_pending if isinstance(roll_pending, str) else bool(roll_pending)
    # ПОСТОРОННЯЯ ПОЗИЦИЯ НЕ ВЫБРАСЫВАЕТСЯ (девятнадцатый круг, №12): книга, построенная
    # «только из знакомых инструментов», молча теряла чужую позицию, появившуюся во время
    # перехода, — она оставалась неуправляемой при формально успешной передаче.
    known = (('CSPX', 'CBU0') if route == 'E' else ('MES', 'ES', 'ZN'))
    alien = [k for k in pos
             if not any(str(k).startswith(w) for w in known)] if route != 'E' else \
            [k for k in pos if k not in known]
    if alien:
        raise ValueError(f'посторонние позиции {alien} при построении книги маршрута '
                         f'{route} — книга не строится, ручной разбор (О-5)')
    if route == 'E':
        return cls(n_eq=pos.get('CSPX', 0), n_bd=pos.get('CBU0', 0),
                   prev_st_eq=st_eq, prev_st_bd=st_bd, roll_pending=_pend)
    es = mes = zn = 0
    # НЕСКОЛЬКО СЕРИЙ ОДНОЙ НОГИ — ОТКАЗ, А НЕ СУММА (двадцатый круг, №10). Прежде
    # {'ZNU26': 1, 'ZNZ26': 100} превращалось в n_b=101 ОДНОЙ произвольно выбранной серии
    # (какая первой попалась в словаре), и старый ПОСТАВОЧНЫЙ контракт исчезал из
    # машинного состояния, оставаясь у брокера: переход при этом мог получить COMPLETE.
    # То же для ноги А, где ES и MES обязаны быть одной серии. Смешанное состояние
    # законно лишь мгновение внутри ролла, а сюда книга попадает ПОСЛЕ перехода — значит
    # это разбор вручную (О-5), а не арифметика.
    check_one_series(pos)
    for k, v in pos.items():
        if k.startswith('MES'):
            mes += int(v); ser_a = ser_a or k[3:]
        elif k.startswith('ES'):
            es += int(v); ser_a = ser_a or k[2:]
        elif k.startswith('ZN'):
            zn += int(v); ser_b = ser_b or k[2:]
    # НЕЗАВЕРШЁННЫЙ РОЛЛ ПЕРЕЖИВАЕТ СМЕНУ МАРШРУТА. Позиции у брокера говорят, ЧТО есть, но
    # не говорят, что осталось СДЕЛАТЬ. Прежде признак отложенного ролла терялся при
    # перестроении книги: перенос не состоялся бы до следующего квартального ролла, и
    # поймал бы это лишь запрет входа в месяц поставки — то есть поздно и уже отказом.
    return cls(n_e=(es * 10 + mes) if unit_is_mes else es, n_b=zn, unit_is_mes=unit_is_mes,
               d_fix=d_fix, ser_a=ser_a, ser_b=ser_b, es_held=es if unit_is_mes else None,
               prev_st_eq=st_eq, prev_st_bd=st_bd, roll_pending=_pend)


def reconcile(book, route, broker_positions, open_orders=None):
    """Сверка состояния с фактической книгой брокера. Возвращает список расхождений.

    Сюда попадает и ПОЗДНЕЕ ИСПОЛНЕНИЕ отменённой заявки: оно проявляется как лишняя
    позиция у брокера и обнаруживается в начале следующей сессии, а не по факту убытка.
    """
    exp = expected_positions(book, route)
    # БЕЗ int(): доли ETF бывают дробными, и приведение к целому МОЛЧА теряло остаток —
    # расхождение на доли акции не замечалось вовсе.
    act = {k: float(v) for k, v in (broker_positions or {}).items() if float(v) != 0.0}
    out = []
    for k in sorted(set(exp) | set(act)):
        e, a = float(exp.get(k, 0)), float(act.get(k, 0))
        if a != a or e != e:          # NaN: сравнение всегда ложно, значит «совпадение»
            out.append(f'{k}: нечисловое количество (у нас {e}, у брокера {a}) — сверка '
                       f'невозможна, сессия останавливается')
            continue
        if abs(a - e) > 1e-9:
            out.append(f'{k}: у нас {e:+.4f}, у брокера {a:+.4f} (расхождение {a - e:+.4f})')
    # ОТКРЫТЫЕ ЗАЯВКИ — ТОЖЕ РАСХОЖДЕНИЕ. После разрыва связи заявка остаётся живой, позиции
    # ещё не изменились, сверка по позициям проходит — и контур подаёт ДУБЛИКАТ. Позже
    # исполнятся обе.
    if open_orders:
        out.append(f'у брокера {len(open_orders)} незакрытых заявок ({list(open_orders)[:5]}) '
                   f'— возможен дубликат, сессия останавливается до их снятия')
    return out
