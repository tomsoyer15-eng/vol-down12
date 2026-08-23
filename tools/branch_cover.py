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
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / 'r33build' / 'live'
TOOL = 'ADDFUT-BRANCH-COVER'


def changed_lines(base):
    """Изменённые строки по git diff: {абсолютный путь: {номера строк}}."""
    out = subprocess.run(['git', 'diff', '-U0', base, '--', 'r33build', 'tools'],
                         cwd=str(ROOT), capture_output=True, text=True).stdout
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
    try:
        _code = compile(Path(path).read_text(encoding='utf-8'), str(path), 'exec')
    except (OSError, SyntaxError):
        return set()
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
            _has_lazy = any(isinstance(_x, _lazy_t) for _x in ast.walk(_n))
            _spans.append((set(range(_n.lineno, _n.end_lineno + 1)), _has_lazy))
        if isinstance(_n, ast.ExceptHandler) and _is_catchall(_n.type):
            _catch.add(_n.lineno)
    return _spans, _catch


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


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else 'origin/master'
    diff = changed_lines(base)
    # МУТАЦИОННЫЙ СТЕНД ИЗМЕРЯЕТСЯ НЕ ЗДЕСЬ. mutation.py исполняет не батарея, а свой
    # прогон; требовать от батареи покрытия его тела значит выдавать сорок ложных обвинений
    # и научить читателя пропускать список. Его собственная непокрытость видна иначе — по
    # вердикту «мутаций, которых не поймал никто».
    # Вне области: mutation.py исполняет собственный прогон, а не батарея; сам инструмент
    # меряет себя частично (report-ветки идут после снятия монитора) — self-шум.
    # Вне области: mutation.py и contour_test.py исполняют собственные прогоны;
    # branch_cover.py меряет сам себя лишь частично; review_new_code.py меняется каждый
    # круг ПО ПОСТРОЕНИЮ (KRUG_N) и батареей не импортируется — без льготы подготовка
    # следующего круга давала бы гарантированное ложное красное.
    _out_of_scope = ('mutation.py', 'branch_cover.py', 'review_new_code.py',
                     'contour_test.py')
    diff = {f: l for f, l in diff.items()
            if not f.endswith(_out_of_scope)}
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
    for f, lines in sorted(diff.items()):
        _ex = set(seen.get(f, set()))
        _spans, _catch = file_facts(f)
        # До неподвижной точки: два многострочных предложения могут делить строку через
        # точку с запятой, и однопроходное слияние зависело бы от порядка.
        _grew = True
        while _grew:
            _grew = False
            for _span, _lazy in _spans:
                if _ex & _span:
                    _add = {min(_span)} if _lazy else _span
                    if not _add <= _ex:
                        _ex |= _add
                        _grew = True
        _test_infra = f.endswith(('invariants.py', 'ib_stub.py', 'fake_broker.py'))
        for ln in sorted(lines):
            _name = f'{Path(f).relative_to(ROOT)}:{ln}'
            if ln not in _ex:
                # ЯВНЫЙ if/else (разбор /code-review 22.08): идиома `X and A or B` при
                # ПУСТОМ списке A всегда выбирала B — канал «справочно» был мёртв навсегда,
                # а строки обвязки падали бы в боевой отказ. Тот самый «тождественно
                # ложный по построению» класс — в инструменте, построенном его ловить.
                (infra if _test_infra else bad_lines).append(_name)
            elif (f, ln) in one_sided and ln not in _catch:
                # Однобокость обвязки — не отказ (отказные ветки зондов исполняются только
                # при красной батарее), но и не молчание: справочный список, как у строк.
                (infra_branch if _test_infra else bad_branch).append(_name)
    _n = sum(len(v) for v in diff.values())
    # Обвязка вычитается ИЗ ИСПОЛНЕННОГО тоже: её неисполненные строки уходят в справочный
    # ярус, и без вычитания заголовок завышал цифру, которая цитируется в BRIEF (пятый
    # прогон: тот же класс, что «ложное 279/279»).
    print(f'изменённых строк питона: {_n}; батарея исполнила: '
          f'{_n - len(bad_lines) - len(infra)}'
          + (f' (+{len(infra)} строк обвязки не исполнялось — справочно)' if infra else ''))
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
            print(f'\nТЕСТОВАЯ ОБВЯЗКА, {_tag} (справочно, {len(_lst)}):')
            for b in _lst:
                print(f'   {b}')
    return 1 if (bad_lines or bad_branch) else 0


if __name__ == '__main__':
    raise SystemExit(main())
