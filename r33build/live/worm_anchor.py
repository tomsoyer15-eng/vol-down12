#!/usr/bin/env python3
"""WORM-якорь вне машины + согласованная резервная копия (18-й круг, №18/№19).

Первый вариант делал четыре ложных заверения (найдено рецензией): digest книги читался из
сырого JSON без state.load; хэшировались фиксированные пути вместо действующих
ADDFUT_REGISTRY/ADDFUT_MARGINS; git-отказ глотался «|| true»; файл писался неатомарно.
И tar снимался БЕЗ замка книги — переход мог заменить книгу и маршрут посреди архивации,
давая «успешную» копию из трёх разных поколений.

Теперь: --snap ДЕНЬ КАТАЛОГ делает ОДНИМ захватом hold_book_lock согласованный tar
состояния и WORM-якорь того же поколения; книга читается state.load (digest сверяется),
пути реестра и замера — те, которыми торгует контур; якорь пишется атомарно с fsync;
git add+commit выполняются с проверкой кода И присутствия файла в HEAD. Любой отказ —
ненулевой выход: автопилот ставит ALARM. Якоря только дописываются (write once)."""
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
ANCHORS = ROOT / 'anchors'
sys.path.insert(0, str(HERE))


def _sha(p):
    try:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    except OSError:
        return 'ФАЙЛА НЕТ'


def _atomic_write(path, body):
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix='.worm-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _anchor_body(day):
    import daily
    import journal as J
    import state as STm
    st = Path.home() / '.addfut'
    rt = st / 'route.txt'
    route = rt.read_text().strip() if rt.exists() else 'F'
    cls = daily.BookE if route == 'E' else daily.Book
    # КНИГА — ЧЕРЕЗ state.load (№18): digest сверяется загрузчиком, а не переписывается
    # из сырого JSON, где порчу payload никто бы не заметил.
    book, sess, saved_route = STm.load(STm.book_path(route), cls)
    if book is None:
        raise RuntimeError('книга отсутствует — якорь не о чем писать')
    jp = st / f'journal-{route}.csv'
    n = J.verify(jp)
    rows = J.read(jp)
    tail = rows[-1]['row_hash'] if rows else 'пусто'
    # ПУТИ — ДЕЙСТВУЮЩИЕ (№18): контур торгует по ADDFUT_REGISTRY/ADDFUT_MARGINS, и якорь
    # обязан аттестовать именно их, а не файлы рядом с кодом.
    reg = Path(os.environ.get('ADDFUT_REGISTRY') or (HERE / 'instruments_live.csv'))
    mrg = Path(os.environ.get('ADDFUT_MARGINS') or (HERE / 'margins_live.json'))
    raw_digest = json.loads(STm.book_path(route).read_text(encoding='utf-8')).get('digest')
    return (f'WORM-якорь ADD-FUT v1.6.0 ред. 33 — {day} (маршрут {route}, сессия {sess})\n'
            f'книга: {book.last_session}, замкнута={"нет" if book.close_provisional else "да"}, '
            f'digest {raw_digest} (payload сверен state.load)\n'
            f'журнал §7: строк {n}, корень цепочки {tail}\n'
            f'sha256 журнала-файла: {_sha(jp)}\n'
            f'sha256 реестра ({reg.name}): {_sha(reg)}\n'
            f'sha256 замера маржи ({mrg.name}): {_sha(mrg)}\n'
            f'sha256 живого ряда сигналов: {_sha(st / "signals_live.csv")}\n')


def _write_anchor(day, body):
    ANCHORS.mkdir(exist_ok=True)
    out = ANCHORS / f'worm-{day}.txt'
    if out.exists():
        k = 2
        while (ANCHORS / f'worm-{day}-{k}.txt').exists():
            k += 1
        out = ANCHORS / f'worm-{day}-{k}.txt'
    _atomic_write(out, body)
    return out


def _git_commit_verified(out):
    """add+commit якоря с ПРОВЕРКОЙ (№18): '|| true' прятал отказ, и «WORM создан»
    относился к файлу, не существующему нигде, кроме локального диска."""
    rel = out.relative_to(ROOT)
    r1 = subprocess.run(['git', '-C', str(ROOT), 'add', str(rel)],
                        capture_output=True, text=True)
    if r1.returncode != 0:
        raise RuntimeError(f'git add отказал: {r1.stderr.strip()[:120]}')
    r2 = subprocess.run(['git', '-C', str(ROOT), 'commit', '-q', '-m',
                         f'WORM-якорь {out.stem}'], capture_output=True, text=True)
    if r2.returncode != 0:
        raise RuntimeError(f'git commit отказал: {(r2.stderr or r2.stdout).strip()[:120]}')
    r3 = subprocess.run(['git', '-C', str(ROOT), 'ls-tree', '--name-only', 'HEAD',
                         str(rel)], capture_output=True, text=True)
    if r3.returncode != 0 or not r3.stdout.strip():
        raise RuntimeError('якорь не виден в HEAD после коммита — заверение ложно')


def _tar_state(day, bdir):
    st = Path.home() / '.addfut'
    bdir = Path(bdir)
    bdir.mkdir(parents=True, exist_ok=True)
    import datetime as _dt
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime('%H%M%S')
    tmp = bdir / f'.addfut-{day}-{stamp}.tmp'
    dst = bdir / f'addfut-{day}-{stamp}.tgz'
    skip = {'ibgw.env'}                      # секреты не копируются
    with tarfile.open(tmp, 'w:gz') as tf:
        for p in sorted(st.rglob('*')):
            rel = p.relative_to(st)
            if rel.parts[0] in skip or rel.parts[0] == 'zoneinfo' \
               or p.suffix == '.lock':
                continue
            tf.add(p, arcname=str(Path('.addfut') / rel), recursive=False)
    with open(tmp, 'rb') as f:
        os.fsync(f.fileno())
    os.replace(tmp, dst)
    return dst


def snap(day, bdir):
    import state as STm
    # ОДИН ЗАХВАТ ЗАМКА КНИГИ (№19): архив и якорь снимаются с ОДНОГО поколения состояния —
    # переход не может заменить книгу и маршрут посреди копии.
    with STm.hold_book_lock():
        body = _anchor_body(day)
        dst = _tar_state(day, bdir)
    out = _write_anchor(day, body)
    _git_commit_verified(out)
    print(f'копия: {dst.name}; якорь: {out.name} (в HEAD)')
    return 0


def main(day):
    body = _anchor_body(day)
    out = _write_anchor(day, body)
    _git_commit_verified(out)
    print(f'якорь: {out.name} (в HEAD)')
    return 0


if __name__ == '__main__':
    try:
        if len(sys.argv) >= 4 and sys.argv[1] == '--snap':
            sys.exit(snap(sys.argv[2], sys.argv[3]))
        sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else 'без-даты'))
    except Exception as ex:
        print(f'ОТКАЗ: {type(ex).__name__}: {ex}')
        sys.exit(1)
