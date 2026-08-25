#!/usr/bin/env python3
"""ВОРОТА 1 (правило 8в CLAUDE.md): КАЖДАЯ ИЗМЕНЁННАЯ ВЕТКА ИСПОЛНЕНА В ОБЕ СТОРОНЫ.

Измерено 22.08.2026: три круга правок подряд дали один дефект на 33–91 изменённую строку,
и четыре дефекта из пятнадцати последнего круга — это код, не исполнявшийся НИ РАЗУ до
объявления готовым (имя `emergency`, которого нет в области видимости; флаг `done_all`,
тождественно ложный по построению; плечо условия, потерявшее свой токен; ветка счёта,
которую батарея не брала ни разу). Ни саморецензия, ни пять углов этого не находят: они
читают текст, а текст выглядит правдоподобно. Исполнение — не выглядит.

Инструмент запускает батарею под sys.monitoring (PEP 669, БЕЗ сторонних пакетов: pip в
этой среде нет) и собирает ДВА множества:
  * какие строки исполнялись;
  * у каких переходов (BRANCH) виден только ОДИН исход.
Затем сверяет с `git diff` и называет изменённые строки, которых прогон не достиг, и
изменённые условия, взятые лишь в одну сторону.

Вызов:  python tools/branch_cover.py [БАЗА]     (по умолчанию БАЗА = origin/master)
Код возврата 1 — есть непокрытое; печатается список.
"""
import os
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / 'r33build' / 'live'
TOOL = 'ADDFUT-BRANCH-COVER'


def changed_lines(base):
    """Изменённые строки по git diff: {абсолютный путь: {номера строк}}."""
    # ОТКАЗ git — ГРОМКИЙ (восьмой прогон /code-review). Прежде код возврата и stderr
    # отбрасывались, и `git diff -U0 origin/опечатка` давал ПУСТОЙ stdout: ворота печатали
    # «проверять нечего» и выходили с нулём. Опечатки в базе, отсутствия remote и свежего
    # клона без fetch хватало, чтобы весь этот механизм тихо не проверил ничего.
    _r = subprocess.run(['git', 'diff', '-U0', base, '--', 'r33build', 'tools'],
                        cwd=str(ROOT), capture_output=True, text=True)
    if _r.returncode != 0:
        raise SystemExit(f'ВОРОТА НЕ СРАБОТАЛИ: git diff против базы {base!r} отказал '
                         f'(код {_r.returncode}): {(_r.stderr or "").strip()[:200]}')
    out = _r.stdout
    res, cur = {}, None
    for l in out.split('\n'):
        if l.startswith('+++ b/'):
            cur = str(ROOT / l[6:].strip())
            res.setdefault(cur, set())
        elif l.startswith('@@') and cur:
            # @@ -a,b +c,d @@
            _plus = l.split('+', 1)[1].split(' ')[0]
            _start, _, _cnt = _plus.partition(',')
            for _i in range(int(_start), int(_start) + int(_cnt or 1)):
                res[cur].add(_i)
    return {k: v for k, v in res.items() if k.endswith('.py') and v}


def executable(path):
    """Строки файла, которые ВООБЩЕ могут исполниться.

    `git diff -U0` считает изменённой всякую строку, включая комментарии и пустые, а этот
    проект пишет комментариев больше, чем кода. Без такого отсева ворота обвиняли бы
    восемнадцать строк пояснения в том, что они «не исполнялись», — и через два прогона их
    научились бы пропускать. Источник истины — сам компилятор: co_lines() перечисляет
    ровно то, что имеет исполняемое представление.
    """
    # ОТКАЗ РАЗБОРА — ГРОМКИЙ (девятый прогон /code-review). Прежде SyntaxError и OSError
    # глотались, функция отдавала пустое множество, и файл ЦЕЛИКОМ исчезал из diff: main
    # печатал «проверять нечего» и выходил с нулём. Это тот же fail-open, который этот же
    # круг только что закрыл для отказа git, — незакомпилировавшийся файл обязан быть
    # отказом, а не невидимкой.
    try:
        _code = compile(Path(path).read_text(encoding='utf-8'), str(path), 'exec')
    except (OSError, SyntaxError, UnicodeDecodeError) as _e:
        raise SystemExit(f'ВОРОТА НЕ СРАБОТАЛИ: {path} не разбирается '
                         f'({type(_e).__name__}: {str(_e)[:120]}) — измерить его нечем, '
                         f'и молча выбросить из diff значило бы выдать пустоту за чистоту')
    _out, _todo = set(), [_code]
    while _todo:
        _c = _todo.pop()
        for _s, _e, _l in _c.co_lines():
            if _l:
                _out.add(_l)
        _todo.extend(_x for _x in _c.co_consts if hasattr(_x, 'co_lines'))
    return _out


def _is_catchall(t):
    """Перехват-всех: bare, Exception, BaseException — и кортежи, их содержащие.

    Первая редакция знала только Exception; в transition живут шесть `except BaseException`
    — их однобокость структурна так же, и без льготы каждая стала бы ВЕЧНЫМ ложным красным.
    """
    import ast
    if t is None:
        return True
    if isinstance(t, ast.Name) and t.id in ('Exception', 'BaseException'):
        return True
    if isinstance(t, ast.Tuple):
        return any(_is_catchall(e) for e in t.elts)
    return False


def file_facts(path):
    """Один AST-разбор файла: (спаны предложений с признаком ленивости, перехваты-всех).

    ДВА УРОКА ПЯТОГО ПРОГОНА /code-review (23.08) — затопление возвращалось уровнем ниже:
    (1) СОСТАВНОЕ определяется НАЛИЧИЕМ ВЛОЖЕННЫХ ПРЕДЛОЖЕНИЙ, а не полем body: у ast.Match
        поля body нет (cases), и целый match-блок проходил как «простое» — исполнение
        строки subject зачитывало все ветви.
    (2) Внутри ПРОСТОГО предложения живут ЛЕНИВЫЕ куски: ветви тернарника, правые операнды
        and/or, тела лямбд и генераторов исполняются не всегда, и полное зачитывание спана
        объявляло исполненной мёртвую ветвь — включая ту самую ветку done_all, ради которой
        ворота строились. У ленивого предложения зачитывается только ЗАГОЛОВОЧНАЯ строка
        («предложение исполнялось»), остальные строки отвечают за себя; их мёртвые ветви
        ловит канал однобокости по BRANCH-событиям.
    """
    import ast
    try:
        _tree = ast.parse(Path(path).read_text(encoding='utf-8'))
    except (OSError, SyntaxError):
        return [], set()
    _lazy_t = (ast.IfExp, ast.BoolOp, ast.Lambda, ast.GeneratorExp,
               ast.ListComp, ast.SetComp, ast.DictComp)
    _spans, _catch = [], set()
    for _n in ast.walk(_tree):
        # Составное = несёт вложенные предложения В ЛЮБОМ поле-списке (body, orelse,
        # finalbody, cases->..., handlers): проверка по полю body пропускала ast.Match.
        _compound = any(isinstance(_v, list) and _v and isinstance(_v[0], (ast.stmt,
                        ast.ExceptHandler, ast.match_case))
                        for _f, _v in ast.iter_fields(_n))
        if isinstance(_n, ast.stmt) and not _compound \
                and getattr(_n, 'end_lineno', None) and _n.end_lineno > _n.lineno:
            # Шестой прогон, доказано зондами: сообщение assert вычисляется ТОЛЬКО при
            # провале, третий операнд цепного сравнения — только при истинности первых.
            _has_lazy = (any(isinstance(_x, _lazy_t) for _x in ast.walk(_n))
                         or (isinstance(_n, ast.Assert) and _n.msg is not None)
                         or any(isinstance(_x, ast.Compare) and len(_x.ops) > 1
                                for _x in ast.walk(_n)))
            _spans.append((set(range(_n.lineno, _n.end_lineno + 1)), _has_lazy))
        if isinstance(_n, ast.ExceptHandler) and _is_catchall(_n.type):
            _catch.add(_n.lineno)
    return _spans, _catch


def credit_spans(executed, spans):
    """Зачёт спанов: НЕленивому — все строки, ленивому — только заголовок (мёртвые ветви
    внутри ловит канал однобокости). До неподвижной точки: предложения могут делить строку
    через точку с запятой. Вынесено из main() шестым прогоном: политика потребления флага
    не исполнялась ни одним стендом, и её инверсия воскрешала бы затопление под зелёной
    самопроверкой, которая видит только производство флага.
    """
    _ex = set(executed)
    _grew = True
    while _grew:
        _grew = False
        for _span, _lazy in spans:
            if _ex & _span:
                _add = {min(_span)} if _lazy else _span
                if not _add <= _ex:
                    _ex |= _add
                    _grew = True
    return _ex


def measure(target):
    """Прогнать target() под наблюдением. Возвращает (исполненные, односторонние)."""
    mon = sys.monitoring
    mid = mon.DEBUGGER_ID
    seen = {}          # файл -> множество исполненных строк
    dests = {}         # (файл, строка) -> множество адресатов перехода
    watch = str(ROOT)

    def _line(code, lineno):
        if code.co_filename.startswith(watch):
            seen.setdefault(code.co_filename, set()).add(lineno)
        return mon.DISABLE if not code.co_filename.startswith(watch) else None

    def _branch(code, off, dst):
        if not code.co_filename.startswith(watch):
            return mon.DISABLE
        _ln = None
        for _s, _e, _l in code.co_lines():
            if _s <= off < _e:
                _ln = _l
                break
        if _ln is not None:
            dests.setdefault((code.co_filename, _ln), set()).add(dst)
        return None

    mon.use_tool_id(mid, TOOL)
    try:
        mon.register_callback(mid, mon.events.LINE, _line)
        mon.register_callback(mid, mon.events.BRANCH, _branch)
        mon.set_events(mid, mon.events.LINE | mon.events.BRANCH)
        target()
    finally:
        mon.set_events(mid, 0)
        mon.free_tool_id(mid)
    one_sided = {k for k, v in dests.items() if len(v) < 2}
    return seen, one_sided


# БОЕВОЙ ЯРУС — ЭТО КОНТУР, А НЕ ВЕСЬ ПАКЕТ (девятый прогон /code-review). Правило
# «всё внутри r33build судится батареей» столкнулось сразу с двумя вещами: замороженные
# движки sim_v13/sim_v164 правилом 2 трогать нельзя, а батарея их не грузит — то есть их
# правка делала бы прогон вечно красным без лечения; и в r33build/code/ лежат два десятка
# исследовательских скриптов, которых не запускает никто. Контур — это live/ и
# transition.py: их батарея достигает, и с них спрос.
CONTOUR = (ROOT / 'r33build' / 'live', ROOT / 'r33build' / 'transition.py')
МЕТКА = 'ADDFUT_ПОКРЫТИЕ'        # файл САМ объявляет, каким прогоном он покрыт


def _own_run(path):
    """Объявленный САМИМ ФАЙЛОМ прогон, который его покрывает, или None.

    Механизм вместо списка имён (девятый прогон): прежде branch_cover держал у себя
    словарь basename'ов, и это был ровно тот «список имён», который он же и объявлял
    дефектом — новый файл в него не попадал, а опечатка в ключе освобождала боевой модуль
    от ворот молча. Теперь объявление живёт РЯДОМ С КОДОМ, и его видно при чтении файла.
    """
    try:
        _t = ast.parse(Path(path).read_text('utf-8'))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return None
    for _n in _t.body:
        if isinstance(_n, ast.Assign) and any(
                isinstance(_t2, ast.Name) and _t2.id == МЕТКА for _t2 in _n.targets):
            if isinstance(_n.value, ast.Constant) and isinstance(_n.value.value, str):
                return _n.value.value
    return None


def _tier_of(path, seen):
    """Ярус файла: ('боевой'|'справочно', причина). Причина непустая только у справочного.

    Единственное ИМЕНОВАННОЕ исключение — сам инструмент: его отчётные ветки исполняются
    ПОСЛЕ снятия монитора и потому ненаблюдаемы по построению, а не по недосмотру. Его
    наблюдает отдельный случай батареи («ворота покрытия наблюдают сами себя») с парными
    мутациями — то есть исключение здесь не означает бесконтрольности.
    Всё остальное решает факт загрузки: файл, который батарея не открывала ни разу,
    измерить нечем (типичный случай — tools/, откуда батарея не импортирует ничего).
    """
    _p = Path(path)
    if not any(_p == _c or _c in _p.parents for _c in CONTOUR):
        return 'справочно', 'вне контура: батарея этот файл не запускает (движки, code/, tools/)'
    _own = _own_run(path)
    if _own:
        return 'справочно', f'объявленное покрытие: {_own}'
    # НЕЗАГРУЖЕННЫЙ БОЕВОЙ ФАЙЛ — НЕ ПОСЛАБЛЕНИЕ, А ХУДШИЙ СЛУЧАЙ (восьмой прогон).
    # Седьмой круг сделал «файл не загружался батареей» справочным — и тем укрыл ровно те
    # боевые модули, у которых покрытия НЕТ ВОВСЕ (selfcheck_v192.py, margin_check.py,
    # replay_check.py, любой новый модуль live/). Их изменённые строки обязаны выйти в
    # «не исполнялись ни разу» целиком, а не в справку. Прежний список имён хотя бы
    # называл исключения поимённо; правило «не грузится — значит простительно» не называет.
    return 'боевой', ''


# ФАЙЛЫ САМОЙ БАТАРЕИ. Для них ОДНОСТОРОННОСТЬ не отказ: отказная ветка зонда берётся
# только при красной батарее, и требовать оба конца значило бы держать прогон вечно
# красным. НЕИСПОЛНЕННАЯ строка отказом остаётся и здесь — именно её пропуск дал седьмому
# прогону мёртвый блок восстановления каталога МР, напечатанный справочно и не уронивший
# ничего. Список узкий и назван: он управляет ровно одним каналом из двух.
_STAND_NAMES = {'invariants.py', 'ib_stub.py', 'fake_broker.py', 'contour_test.py'}


def _one_sided_is_fatal(path):
    return Path(path).name not in _STAND_NAMES


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else 'origin/master'
    diff = changed_lines(base)
    # МУТАЦИОННЫЙ СТЕНД ИЗМЕРЯЕТСЯ НЕ ЗДЕСЬ. mutation.py исполняет не батарея, а свой
    # прогон; требовать от батареи покрытия его тела значит выдавать сорок ложных обвинений
    # и научить читателя пропускать список. Его собственная непокрытость видна иначе — по
    # вердикту «мутаций, которых не поймал никто».
    # Вне области: mutation.py исполняет собственный прогон, а не батарея; сам инструмент
    # меряет себя частично (report-ветки идут после снятия монитора) — self-шум.
    # Вне замера БАТАРЕЕЙ: mutation.py исполняет собственный прогон, branch_cover.py
    # меряет себя частично. Остальная обвязка и меняющийся по построению review_new_code
    # (KRUG_N каждый круг) — СПРАВОЧНЫЙ ярус, не невидимость: шестой прогон показал, что
    # полное исключение прятало бы недостижимый зонд бесследно. Имена сравниваются ТОЧНО
    # (Path.name): endswith ловил бы будущий permutation.py как чужую запись.
    # ЯРУС ОПРЕДЕЛЯЕТСЯ ИЗМЕРЕНИЕМ, А НЕ СПИСКОМ ИМЁН (седьмой прогон, №6 и №10).
    # Список имён давал три беды сразу. (1) invariants.py числился обвязкой, а обвязка в
    # код возврата не входит — значит неисполненная строка стенда НЕ МОГЛА уронить прогон
    # никогда; этот же круг напечатал в справочном ярусе мёртвый блок восстановления
    # каталога МР и вышел с нулём. (2) branch_cover.py исключался целиком, поэтому весь
    # новый код самих ворот оставался неизмеренным, а заголовок занижал знаменатель.
    # (3) diff смотрит и tools/, но батарея оттуда не импортирует НИЧЕГО — любая правка
    # tools/release.py давала красный без лечения, кроме дописывания ещё одного имени.
    # Новое правило одно: справочный ярус — ТОЛЬКО там, где измерение невозможно, и
    # причина ПЕЧАТАЕТСЯ. Всё, что батарея загрузила, судится боевым порядком.
    diff = {f: (l & executable(f)) for f, l in diff.items()}
    diff = {f: l for f, l in diff.items() if l}
    if not diff:
        print(f'изменённых строк питона против {base} нет — проверять нечего')
        return 0
    sys.path.insert(0, str(LIVE))
    sys.path.insert(0, str(ROOT / 'r33build'))

    # ГОНЯЕМ ТЕ ЖЕ СЕМЬИ, ЧТО И БАТАРЕЯ. Имена взяты грепом `^def run_` из invariants.py,
    # а не по памяти: пропущенная семья превратила бы «не исполнялось» в ложное обвинение.
    _FAMS = ('run_adapter', 'run_intent', 'run_feed', 'run_run', 'run_transition',
             'run_roll', 'run_refusal', 'run_pack', 'run_signal', 'run_j7', 'run_sessions')

    def _run():
        # Импорт — ВНУТРИ окна наблюдения: код уровня модуля (декораторы, регистрации,
        # санация окружения) исполняется при импорте, и вне окна он ложно числился бы
        # «не исполнялось» — первое измерение дало так десяток ложных обвинений.
        import invariants as I
        for _f in _FAMS:
            getattr(I, _f)()

    seen, one_sided = measure(_run)
    bad_lines, bad_branch, infra, infra_branch = [], [], [], []
    _reasons = {}
    for f, lines in sorted(diff.items()):
        _ex = set(seen.get(f, set()))
        _spans, _catch = file_facts(f)
        _ex = credit_spans(_ex, _spans)
        _tier, _why_tier = _tier_of(f, seen)
        _test_infra = (_tier == 'справочно')
        if _test_infra:
            _reasons[str(Path(f).relative_to(ROOT))] = _why_tier
        for ln in sorted(lines):
            _name = f'{Path(f).relative_to(ROOT)}:{ln}'
            if ln not in _ex:
                # ЯВНЫЙ if/else (разбор /code-review 22.08): идиома `X and A or B` при
                # ПУСТОМ списке A всегда выбирала B — канал «справочно» был мёртв навсегда,
                # а строки обвязки падали бы в боевой отказ. Тот самый «тождественно
                # ложный по построению» класс — в инструменте, построенном его ловить.
                (infra if _test_infra else bad_lines).append(_name)
            elif (f, ln) in one_sided and ln not in _catch:
                (bad_branch if (not _test_infra and _one_sided_is_fatal(f))
                 else infra_branch).append(_name)
                if _test_infra or not _one_sided_is_fatal(f):
                    _reasons.setdefault(
                        str(Path(f).relative_to(ROOT)),
                        'файл батареи: отказная ветка зонда берётся только при красной батарее')
    _n = sum(len(v) for v in diff.values())
    # ЗНАМЕНАТЕЛЬ — ПОЛНЫЙ, С РАЗБИВКОЙ ПО ЯРУСАМ (седьмой прогон, №6). Прежний заголовок
    # печатал только то, что осталось после вычитания исключённых файлов, и цифра,
    # цитируемая в BRIEF как покрытие круга, была занижена: код самих ворот в неё не
    # входил вовсе. Теперь видно всё три числа сразу — сколько изменено, сколько судится
    # боевым порядком, сколько измерить нечем и почему.
    _n_ref = sum(len(v) for f, v in diff.items() if _tier_of(f, seen)[0] == 'справочно')
    _n_prod = _n - _n_ref
    print(f'изменённых строк питона: {_n} (боевых {_n_prod}, справочных {_n_ref}); '
          f'батарея исполнила боевых: {_n_prod - len(bad_lines)}')
    if bad_lines:
        print(f'\nНЕ ИСПОЛНЯЛИСЬ НИ РАЗУ ({len(bad_lines)}):')
        for b in bad_lines:
            print(f'   {b}')
    if bad_branch:
        print(f'\nВЗЯТЫ ТОЛЬКО В ОДНУ СТОРОНУ ({len(bad_branch)}):')
        for b in bad_branch:
            print(f'   {b}')
    for _tag, _lst in (('НЕ ИСПОЛНЯЛАСЬ', infra), ('ОДНОБОКА', infra_branch)):
        if _lst:
            print(f'\nСПРАВОЧНО ({_tag}, {len(_lst)}) — измерить нечем:')
            for b in _lst:
                print(f'   {b}')
    if _reasons:
        print('\nПОЧЕМУ СПРАВОЧНО:')
        for _f, _r in sorted(_reasons.items()):
            print(f'   {_f}: {_r}')
    return 1 if (bad_lines or bad_branch) else 0


if __name__ == '__main__':
    raise SystemExit(main())
