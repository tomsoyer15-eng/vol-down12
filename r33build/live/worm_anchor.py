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


def _sha(p, required=False):
    """required=True (девятнадцатый круг, №18): отсутствие ОБЯЗАТЕЛЬНОГО файла — отказ
    снимка, а не строка «ФАЙЛА НЕТ» при успешном завершении: якорь не смеет заверять
    пустоту как состояние."""
    try:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    except OSError:
        if required:
            raise RuntimeError(f'{p}: обязательный файл отсутствует — якорь не заверяет '
                               f'пустоту, снимок не создаётся')
        return 'ФАЙЛА НЕТ'


def _state_root():
    """Каталог состояния — ДЕЙСТВУЮЩИЙ (девятнадцатый круг, №18): тот же lock_dir, что у
    книги и замка (уважает ADDFUT_LOCK_DIR), а не жёсткий ~/.addfut."""
    import state as STm
    return Path(STm.lock_dir())


def _signals_path():
    """Живой ряд сигналов — тем же правилом, что у сборщика (ADDFUT_SIGNALS)."""
    return Path(os.environ.get('ADDFUT_SIGNALS') or (_state_root() / 'signals_live.csv'))


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
    st = _state_root()               # действующий каталог, не жёсткий ~/.addfut (№18)
    # МАРШРУТ БЕРЁТСЯ ЧЕРЕЗ ОБЩУЮ ФУНКЦИЮ, А НЕ УГАДЫВАЕТСЯ (двадцать восьмой круг, №16).
    # Прежде при отсутствии route.txt подставлялся 'F': после перехода старая книга Ф обычно
    # остаётся на диске, поэтому якорь ЗАВЕРЯЛ НЕАКТИВНУЮ книгу как действующую, когда
    # фактическая позиция уже в Е. Тот же источник истины, что у сессии; неизвестный
    # маршрут — отказ снимать якорь, а не догадка.
    rt = st / 'route.txt'
    import state as _STw
    route = _STw.active_route()
    cls = daily.BookE if route == 'E' else daily.Book
    # КНИГА — ЧЕРЕЗ state.load (№18): digest сверяется загрузчиком, а не переписывается
    # из сырого JSON, где порчу payload никто бы не заметил.
    book, sess, saved_route = STm.load(STm.book_path(route), cls)
    if book is None:
        raise RuntimeError('книга отсутствует — якорь не о чем писать')
    jp = st / f'journal-{route}.csv'
    if not jp.exists():
        # журнал §7 обязателен (девятнадцатый круг, №18): J.verify пустого пути отдаёт 0
        # без ошибки, и якорь заверял бы «журнал цел» про несуществующий файл.
        raise RuntimeError(f'{jp}: журнала §7 нет — якорь не заверяет пустоту')
    # ОДИН СНИМОК НА ЧИСЛО И НА КОРЕНЬ ЦЕПОЧКИ (двадцать пятый круг, №17). Исправление
    # journal.verify до одного чтения здесь отменялось следующим же вызовом: n брался из
    # verify(), а tail — отдельным read(). При конкурентном append число «проверенных»
    # строк и хвост цепочки относились к РАЗНЫМ поколениям, и в режиме main() такой
    # смешанный текст сразу коммитился как WORM-якорь.
    rows = J.read(jp)
    n = J.verify_rows(rows, jp) if hasattr(J, 'verify_rows') else J.verify(jp)
    if len(rows) != n:
        raise RuntimeError(f'{jp}: журнал изменился во время снятия якоря '
                           f'(проверено {n} строк, прочитано {len(rows)}) — '
                           f'якорь относился бы к двум поколениям')
    # ЯКОРЬ НЕ ЗАВЕРЯЕТ НОВЫЙ GENESIS (двадцать второй круг, №16): header-only журнал
    # проходит verify()==0 без ошибки, и якорь подписывал «журнал цел, строк 0» при книге,
    # которая уже торговала, — подменённый файл получал WORM-заверение.
    if not rows and int(sess or 0) > 0:
        raise RuntimeError(f'{jp}: журнал пуст, а книга несёт сессию №{sess} — журнал '
                           f'утрачен или подменён, якорь пустоту не заверяет')
    tail = rows[-1]['row_hash'] if rows else 'пусто'
    # ПУТИ — ДЕЙСТВУЮЩИЕ (№18): контур торгует по ADDFUT_REGISTRY/ADDFUT_MARGINS, и якорь
    # обязан аттестовать именно их, а не файлы рядом с кодом.
    reg = Path(os.environ.get('ADDFUT_REGISTRY') or (HERE / 'instruments_live.csv'))
    mrg = Path(os.environ.get('ADDFUT_MARGINS') or (HERE / 'margins_live.json'))
    raw_digest = json.loads(STm.book_path(route).read_text(encoding='utf-8')).get('digest')
    # ОБЯЗАТЕЛЬНЫ: журнал, реестр, живой ряд сигналов (№18). Замер маржи может законно
    # отсутствовать до первого перехода — его пустота называется честно строкой.
    return (f'WORM-якорь ADD-FUT v1.6.0 ред. 33 — {day} (маршрут {route}, сессия {sess})\n'
            f'книга: {book.last_session}, замкнута={"нет" if book.close_provisional else "да"}, '
            f'digest {raw_digest} (payload сверен state.load)\n'
            f'журнал §7: строк {n}, корень цепочки {tail}\n'
            f'sha256 журнала-файла: {_sha(jp, required=True)}\n'
            f'sha256 реестра ({reg.name}): {_sha(reg, required=True)}\n'
            f'sha256 замера маржи ({mrg.name}): {_sha(mrg)}\n'
            f'sha256 живого ряда сигналов: {_sha(_signals_path(), required=True)}\n'
            # ДВАДЦАТЬ ПЕРВЫЙ КРУГ, №14: сайдкар уровней и пин счёта попадают в tar, но в
            # теле якоря их не было — архив с подменённым пином или другим поколением
            # уровней имел бы полностью «правильный» WORM-текст. Уровни обязательны:
            # без них сигнал недоказуем. Пин может отсутствовать до первого запуска.
            f'sha256 сайдкара уровней: {_sha(st / "signals_levels.csv", required=True)}\n'
            f'sha256 пина счёта: {_sha(st / "account.txt")}\n'
            # ЖУРНАЛ МР — ТОЖЕ В ЯКОРЕ (тридцать первый круг, №13). Он единственный
            # источник действующего маршрута и одобрений перехода, а внешнего следа не имел
            # вовсе: правка «на месте» (inode тот же, схема цела) была видна только тому,
            # кто её ищет. Пин защищает личность файла, digest конфигурации — содержимое,
            # якорь — историю: три разных слоя, и только последний переживает машину.
            # Журнала МР может не быть до первой записи — пустота называется честно.
            f'sha256 журнала МР ({_mr_journal().name}): {_sha(_mr_journal())}\n'
            f'sha256 конфигурации развёртывания МР: {_sha(_mr_deploy())}\n')


def _mr_journal():
    """Действующий путь нормативного журнала МР — тот же, что читает контур (state.py)."""
    return Path(os.environ.get('ADDFUT_MR_JOURNAL')
                or (Path(__file__).resolve().parent.parent / 'mr_journal.csv'))


def _mr_deploy():
    """Конфигурация развёртывания МР: в ней живёт digest журнала (31-й круг, №13)."""
    import sys as _s
    _root = str(Path(__file__).resolve().parent.parent)
    if _root not in _s.path:
        _s.path.insert(0, _root)
    try:
        import mr_engine as _M
        return Path(_M.deploy_config_path())
    except Exception:
        return Path('/nonexistent/mr-deploy.json')


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


def _git_commit_verified(out, body=None):
    """add+commit якоря с ПРОВЕРКОЙ (№18): '|| true' прятал отказ, и «WORM создан»
    относился к файлу, не существующему нигде, кроме локального диска.

    СРАВНИВАЕТСЯ СОДЕРЖИМОЕ, А НЕ ИМЯ (девятнадцатый круг, №19): ls-tree --name-only
    подтверждал лишь присутствие пути — pre-commit hook или конкурентный процесс могли
    подменить blob, и «якорь в HEAD» относился к другому тексту. Сверяется git-объект
    HEAD с хэшем ФАЙЛА на диске (тем самым, чей текст проверен)."""
    rel = out.relative_to(ROOT)
    r1 = subprocess.run(['git', '-C', str(ROOT), 'add', str(rel)],
                        capture_output=True, text=True)
    if r1.returncode != 0:
        raise RuntimeError(f'git add отказал: {r1.stderr.strip()[:120]}')
    # КОММИТ ТОЛЬКО ЯКОРЯ (двадцать четвёртый круг, №26). Обычный `git commit` захватывал
    # ВСЁ, что уже лежало в index: незавершённая или непроверенная правка торгового кода
    # молча уезжала вместе с WORM-якорем и выглядела частью ЗАВЕРЕННОЙ истории, хотя
    # проверяется потом только blob якоря. `--only <путь>` коммитит ровно один файл и не
    # трогает остальной индекс.
    r2 = subprocess.run(['git', '-C', str(ROOT), 'commit', '-q', '--only', str(rel), '-m',
                         f'WORM-якорь {out.stem}'], capture_output=True, text=True)
    if r2.returncode != 0:
        raise RuntimeError(f'git commit отказал: {(r2.stderr or r2.stdout).strip()[:120]}')
    r3 = subprocess.run(['git', '-C', str(ROOT), 'ls-tree', 'HEAD', '--', str(rel)],
                        capture_output=True, text=True)
    if r3.returncode != 0 or not r3.stdout.strip():
        raise RuntimeError('якорь не виден в HEAD после коммита — заверение ложно')
    try:
        blob_head = r3.stdout.split()[2]
    except IndexError:
        raise RuntimeError(f'ls-tree вернул неразбираемое: {r3.stdout[:80]!r}')
    # СВЕРКА С ПРОВЕРЕННЫМ ТЕЛОМ, А НЕ С ФАЙЛОМ ПОСЛЕ HOOK (двадцать восьмой круг, №17).
    # hash-object считал хэш файла НА ДИСКЕ уже после коммита: pre-commit hook или чужой
    # процесс могли переписать и файл, и blob согласованно — сверка совпадала бы, заверяя
    # чужой текст. Хэшируем ТО ТЕЛО, которое проверено и записано, не перечитывая диск.
    import subprocess as _sp2
    # ХЭШИРУЕТСЯ ПРОВЕРЕННОЕ ТЕЛО (двадцать девятый круг, №16). Прежний вариант читал файл
    # с диска — пусть и через stdin, но ПОСЛЕ коммита: pre-commit hook или чужой процесс
    # могли согласованно переписать и файл, и blob, и сверка совпала бы, заверяя чужой
    # текст. Сравнивать надо с тем, что мы ПРОВЕРИЛИ, а не с тем, что сейчас лежит рядом.
    _payload = (body.encode('utf-8') if isinstance(body, str)
                else (body if body is not None else out.read_bytes()))
    r4 = _sp2.run(['git', '-C', str(ROOT), 'hash-object', '--stdin'],
                  input=_payload, capture_output=True)
    _blob_body = r4.stdout.decode().strip() if r4.returncode == 0 else ''
    if not _blob_body:
        raise RuntimeError(f'git hash-object отказал: {r4.stderr.decode()[:120]}')
    if blob_head != _blob_body:
        raise RuntimeError('содержимое якоря в HEAD НЕ СОВПАДАЕТ с записанным файлом '
                           '(подмена между add и commit) — заверение ложно')


def _tar_state(day, bdir):
    st = _state_root()               # действующий каталог (девятнадцатый круг, №18)
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


def reject_archive(dst):
    """Пометить отвергнутый архив (двадцатый круг, №22). Отдельной функцией — под парную
    мутацию. Возвращает '' при успехе, иначе текст предупреждения."""
    try:
        dst.rename(dst.with_suffix(dst.suffix + '.rejected'))
        return ''
    except OSError as ex:
        return (f' | ВНИМАНИЕ: отклонённый архив {dst.name} НЕ переименован ({ex}) — '
                f'убрать вручную, иначе им можно восстановиться')


def snap(day, bdir):
    import state as STm
    # ОДИН ЗАХВАТ ЗАМКА КНИГИ (№19): архив и якорь снимаются с ОДНОГО поколения состояния —
    # переход не может заменить книгу и маршрут посреди копии.
    with STm.hold_book_lock():
        body = _anchor_body(day)
        dst = _tar_state(day, bdir)
        # ОДНО ПОКОЛЕНИЕ ДОКАЗЫВАЕТСЯ ПОВТОРНЫМ ХЭШЕМ (девятнадцатый круг, №18): замок
        # книги не держат ни signal_update, ни first_connect — их файлы могли смениться
        # между вычислением тела якоря и попаданием в tar. Совпадение тел до и после
        # архивации гарантирует, что якорь и архив описывают одни и те же байты;
        # расхождение — отказ (следующее замыкание повторит попытку).
        # ИСКЛЮЧЕНИЕ ВТОРОГО ЧТЕНИЯ — ТОЖЕ ОТКАЗ (двадцать пятый круг, №18). Прежде второй
        # _anchor_body стоял ВНЕ защиты: если он не возвращал другой текст, а ПАДАЛ —
        # например, видел оборванный или повреждённый журнал после первого чтения, —
        # архив оставался под рабочим именем addfut-*.tgz, и следующий backup_push мог
        # выгрузить копию, которую сам сниматель не смог аттестовать.
        try:
            body2 = _anchor_body(day)
        except Exception as _ex2:
            _mark2 = reject_archive(dst)
            raise RuntimeError(f'повторное вычисление тела якоря не удалось ({_ex2}) — '
                               f'архив не аттестован; помечен .rejected{_mark2}')
        if body2 != body:
            # ОТВЕРГНУТЫЙ АРХИВ НЕ ОСТАЁТСЯ ПОД РАБОЧИМ ИМЕНЕМ (двадцатый круг, №22).
            # _tar_state публикует addfut-*.tgz ДО повторного вычисления тела; прежде при
            # расхождении поколений исключение поднималось, а файл оставался лежать — и
            # следующий backup_push или ручное восстановление могли взять архив, про
            # который сам код установил, что книга, маршрут, сигнал и реестр сняты из
            # РАЗНЫХ поколений. ALARM его обратно во временный не превращал.
            # Файл не удаляется, а помечается: доказательство инцидента сохраняется, но
            # под именем, которое восстановление не подхватывает.
            _mark = reject_archive(dst)
            raise RuntimeError('файлы состояния изменились во время снимка — якорь и '
                               'архив были бы разных поколений; снимок отклонён, архив '
                               'помечен .rejected' + _mark)
    # ЛЮБОЙ ПРОВАЛ ПОСЛЕ ПУБЛИКАЦИИ АРХИВА ПОМЕЧАЕТ ЕГО (двадцать первый круг, №15).
    # Прежде .rejected ставился ТОЛЬКО при расхождении поколений; если падали запись
    # якоря или git-заверение, addfut-*.tgz оставался под рабочей маской без
    # подтверждённого якоря — и следующий backup_push подхватывал его как обычную копию,
    # то есть в зеркале лежала бы копия, которую никто не заверил.
    # ЯКОРЬ ПРИВЯЗЫВАЕТСЯ К АРХИВУ (двадцать первый круг, №14): без sha256 самого
    # addfut-*.tgz по якорю нельзя проверить КОНКРЕТНУЮ копию после выгрузки в зеркало
    # или восстановления — заверялось состояние, но не то, что из него сделано.
    body_full = body + (f'sha256 архива ({dst.name}): '
                        f'{_sha(dst, required=True)}\n')
    try:
        out = _write_anchor(day, body_full)
        # СВЕРЯЕТСЯ ТО ТЕЛО, КОТОРОЕ ЗАПИСАНО (тридцатый круг, №11). Здесь стояло `body` —
        # тело БЕЗ строки sha256 архива, тогда как в файл уходит body_full. Хэши не могли
        # совпасть НИКОГДА: каждое замыкание помечало бы архив .rejected, поднимало ALARM,
        # не ставило closed-* и останавливало автопилот до ручного разбора. Дефект внесён
        # моей же правкой двадцать девятого круга (№16 двадцать восьмого), когда сверку
        # перевели с файла на диске на проверенное тело — и передали не то тело.
        _git_commit_verified(out, body_full)
    except BaseException as ex:
        _m = reject_archive(dst)
        raise RuntimeError(f'якорь не подтверждён ({ex}) — архив {dst.name} помечен '
                           f'.rejected, заверенной копии за {day} НЕТ{_m}')
    print(f'копия: {dst.name}; якорь: {out.name} (в HEAD)')
    return 0


def main(day):
    """Официальный путь создания якоря (двадцать шестой круг, №22).

    Согласованный захват и двойное чтение были ТОЛЬКО в snap(): обычный запуск
    `worm_anchor.py <день>` читал маршрут, книгу, журнал и внешние файлы в разные моменты
    БЕЗ книжного замка и сразу коммитил текст. Переход, случившийся между этими чтениями,
    давал валидный git-якорь из НЕСКОЛЬКИХ поколений состояния — ложное доказательство,
    которое ничем не помечалось. Здесь тот же замок и та же сверка двух чтений: якорь либо
    описывает одно поколение, либо не создаётся вовсе.
    """
    import state as STm
    with STm.hold_book_lock():
        body = _anchor_body(day)
        body2 = _anchor_body(day)
        if body2 != body:
            raise RuntimeError('файлы состояния изменились во время снятия якоря — '
                               'текст описывал бы два поколения; якорь не создан')
        out = _write_anchor(day, body)
        _git_commit_verified(out, body)
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
