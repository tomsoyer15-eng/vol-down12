# -*- coding: utf-8 -*-
"""Самопроверка ADD-FUT v1.6.0 ред. 32. Запуск из корня замороженного пакета данных после
его 10 PASS; файлы ред. 32 скопированы в корень. Корень доверия — SHA-256 ZIP.
Маршрут Ф (sim_v164) не изменялся против замороженной ред. 4.1."""
import tempfile as _tf0
import os as _os1
_TD = _tf0.mkdtemp(prefix='addfut-selfcheck-')     # ВСЕ фикстуры — в уникальном каталоге прогона:
                                                   # параллельные запуски самопроверки не мешают друг другу

def _JP(_name):
    """Каждая фикстура — в своём подкаталоге: конфигурация развёртывания привязана к каталогу журнала."""
    _d = _os1.path.join(_TD, _os1.path.splitext(_name)[0])
    _os1.makedirs(_d, exist_ok=True)
    return _os1.path.join(_d, _name)
import sys, hashlib, os, csv
import pandas as pd, numpy as np
sys.path.insert(0, '.')
import sim_v13 as S
import shutil as _sh0
from sim_v164 import sim164, map_mes
from sim_etf import sim_etf
import mr_engine as M
M.set_state_dir_for_tests(os.path.join(_TD, 'install'))   # изоляция прогона: боевой ~/.add-fut не трогаем

# ИЗОЛЯЦИЯ ЖИВОГО КОНТУРА — И ДОКАЗАТЕЛЬСТВО ИЗОЛЯЦИИ. lock_dir() машинный (~/.addfut):
# он защищает СЧЁТ, а не копию кода, поэтому туда же смотрят book_path и hand_over_book.
# 12.08.2026 тестовый переход из самопроверки ЗАТЁР ЖИВУЮ КНИГУ торгового счёта записью
# «книга принята от перехода E->F» с фиктивными позициями. Каталог подменяется на прогонный,
# а в конце сверяется снимок настоящего ~/.addfut: провал здесь важнее всех остальных —
# самопроверка, портящая боевое состояние, хуже отсутствия самопроверки.
_REAL_ADDFUT = os.path.expanduser('~/.addfut')

def _machine_snapshot():
    """РЕКУРСИВНО и через lstat. Восьмая рецензия показала готовое ложное доказательство:
    снимок обходил только верхний уровень, а live/tz.py пишет в ~/.addfut/zoneinfo/ —
    самопроверка могла менять машинный каталог и печатать «не тронуто». Плюс верхнеуровневый
    isfile ходил ПО симлинку: подмена файла ссылкой с тем же содержимым была невидима.
    Фиксируются тип, права, цель ссылки и хэш; каталоги — как записи. Откат «записал и
    вернул» между снимками невидим ПО ПОСТРОЕНИЮ — потому первая линия защиты остаётся
    изоляция окружением, снимок — вторая."""
    # ДИСПЕТЧЕРСКИЕ файлы живого автопилота — ВНЕ снимка (после починки cron 13.08.2026
    # тик каждые 10 минут пишет сердцебиение и лог, и часовой прогон selfcheck валился о
    # ЖИВОЙ контур, а не о стенды). Снимок охраняет ТОРГОВОЕ состояние: книги, журналы,
    # сигналы, намерение, реестры, тревоги — они в снимке остаются строго.
    # + ФАЙЛЫ ОНЛАЙН-СТРАХОВКИ (19.08.2026): wip_push.sh каждые полчаса пушит коммиты и
    # копирует незакоммиченное на Drive; его журнал и замок живут в ~/.addfut той же
    # семьёй, что и журнал автопилота, и торгового состояния не несут. Без исключения
    # часовой selfcheck валился о СТРАХОВКУ, а не о стенды — ровно как 13.08 о живой тик.
    # + ВРЕМЕННЫЕ ФАЙЛЫ АТОМАРНОЙ ЗАПИСИ ОТМЕТОК (рецензия 20.08): hb_write пишет
    # tick-heartbeat.tmp / lock-nohb.tmp и переименовывает. Тик, попавший внутрь часового
    # прогона, ронял бы «стенды не тронули машинное состояние» и блокировал выпуск — тот же
    # ложный отказ, ради которого сюда внесли сам tick-heartbeat 13.08.
    _DISPATCH = {'autopilot.log', 'tick-heartbeat', 'autopilot.lock', 'push-fails',
                 'wip-push.log', 'wip-push.lock',
                 'tick-heartbeat.tmp', 'lock-nohb.tmp', 'lock-nohb'}
    out = {}
    if not os.path.isdir(_REAL_ADDFUT):
        return out
    for _root, _dirs, _files in os.walk(_REAL_ADDFUT):
        for _n in sorted(_dirs) + sorted(_files):
            _fp = os.path.join(_root, _n)
            _rel = os.path.relpath(_fp, _REAL_ADDFUT)
            # ОТМЕТКИ ДНЯ — ЭТО СОСТОЯНИЕ, А НЕ ШУМ (двадцать седьмой круг, №21). Прежде
            # traded-*, closed-* и sigup-* исключались из снимка, хотя именно они решают,
            # торговать ли день ЗАНОВО и обновлён ли сигнал месяца: стенд, создавший или
            # удаливший такую отметку, менял поведение живого контура НЕВИДИМО для
            # доказательства «машинное состояние не тронуто». Исключается только заведомо
            # изменчивое: журнал автопилота, пульс, замок, счётчик неудачных выгрузок.
            if _rel in _DISPATCH:
                continue
            _st = os.lstat(_fp)
            import stat as _stat
            if _stat.S_ISLNK(_st.st_mode):
                out[_rel] = ('link', oct(_st.st_mode), os.readlink(_fp))
            elif _stat.S_ISDIR(_st.st_mode):
                out[_rel] = ('dir', oct(_st.st_mode))
            else:
                out[_rel] = ('file', oct(_st.st_mode),
                             hashlib.sha256(open(_fp, 'rb').read()).hexdigest())
    return out

# tz.py дописывает недостающие зоны ПРИ ПЕРВОМ импорте — он обязан отработать ДО снимка,
# иначе на свежей машине легитимная запись зоны выглядела бы вмешательством стендов.
sys.path.insert(0, os.path.join('.', 'live'))
try:
    import tz as _tz0                                     # noqa: F401
except Exception:
    pass

_MACHINE0 = _machine_snapshot()
# ОКРУЖЕНИЕ ИЗОЛИРУЕТСЯ ЦЕЛИКОМ (двадцать третий круг, №11). Прежде задавался только
# ADDFUT_LOCK_DIR, а book_path() отдаёт приоритет УНАСЛЕДОВАННОМУ ADDFUT_BOOK_PATH, и
# session.state_dir() — ADDFUT_DIR. release.py окружение не чистит, поэтому стенды могли
# писать фикстуру ПО ЖИВОМУ пути, а lstat-снимок ~/.addfut этого даже не увидел бы: он
# сторожит каталог, а не внешнее переопределение. Правило 5 проекта требует обратного.
for _v in ('ADDFUT_BOOK_PATH', 'ADDFUT_DIR', 'ADDFUT_SIGNALS', 'ADDFUT_JOURNAL',
           'ADDFUT_ACCOUNT', 'ADDFUT_MARGINS', 'ADDFUT_REGISTRY', 'ADDFUT_LIVE_OK',
           'ADDFUT_SIGNALS_FALLBACK_OK', 'ADDFUT_SIGNAL_CONFIRM'):
    os.environ.pop(_v, None)
os.environ['ADDFUT_LOCK_DIR'] = os.path.join(_TD, 'addfut')

FAIL = []
def chk(name, ok, msg=''):
    print(('[PASS] ' if ok else '[FAIL] ') + name + (': ' + msg if msg else ''))
    if not ok: FAIL.append(name)

ALLOW = ['ADD-FUT-v1_6_0-r33.pdf', 'ADD-FUT-v1_6_0-r33.txt', 'sim_v164.py', 'sim_etf.py', 'mr_engine.py', 'transition.py',
         'mr_inputs_retro.csv', 'mr_tariff.csv', 'mr_state.csv', 'mr_journal.csv', 'selfcheck_v192.py',
         'nav_v164_modern.csv', 'nav_v164_splice.csv', 'nav_etf_modern.csv', 'instruments.csv', 'README-192.md',
         'live/daily.py', 'live/journal.py', 'live/fake_broker.py', 'live/replay_check.py', 'live/margin_check.py', 'live/contour_test.py', 'live/ibgw/config.ini', 'live/ibgw/start.sh', 'depr_real.py', 'live/state.py', 'live/invariants.py', 'live/mutation.py',
         'live/first_connect.py',
         # ЖИВАЯ ПАРА В ПАКЕТ НЕ ВХОДИТ — И ЭТО РЕШЕНИЕ, А НЕ УПУЩЕНИЕ (двадцать шестой
         # круг, №20; уточнено 16.08). Реестр и замер принадлежат СЧЁТУ: con_id меняются
         # каждый квартал, замер привязан к счёту и дате. Класть их в архив значит менять
         # пакет ради данных счёта и заверять корнем доверия то, что живёт своей жизнью.
         # Ложным было не отсутствие файлов, а ФОРМУЛИРОВКА проверки: она обещала
         # «согласованность поставленной пары», проверяя пару НА МАШИНЕ. Текст исправлен
         # ниже: на чистом архиве проверка честно объявляет себя неприменимой.
         # ЗАМОРОЖЕННОЕ ЯДРО. Импортируется всем пакетом, но хэшем не защищалось и в
         # архив не попадало: распакованный пакет вообще не запускался.
         'sim_v13.py', 'depr_v13.py',
         # ЖИВОЙ КОНТУР: адаптер брокера, сборщик входов, запуск сессии и зоны IBKR.
         'live/ib_broker.py', 'live/feed.py', 'live/session.py', 'live/tz.py',
         'live/ib_stub.py', 'live/contracts.py', 'live/signal_update.py',
         # Диспетчер бумажного этапа: девятая рецензия (№28) — работающая внешняя копия
         # не попадала под контроль целостности пакета вовсе.
         'live/autopilot.sh',
         # WORM-якорь и диагност тревог (решение заказчика 13.08.2026, после 17-го круга).
         'live/worm_anchor.py', 'live/diagnose.py'] + [
         # ИСХОДНЫЕ РЯДЫ. Без них §10 проверял только неизменность кода: пересчитать
         # результат по архиву было НЕЛЬЗЯ — данных в нём не было вовсе.
         f'data/{n}' for n in sorted(os.listdir('data')) if n.endswith('.csv')]

try:
    # строки-комментарии (git-база выпуска, девятнадцатый круг, №22) — не записи манифеста
    entries = [(h, fn.strip()) for h, fn in
               (line.strip().split(maxsplit=1) for line in open('MANIFEST-192.txt', encoding='utf-8')
                if line.strip() and not line.lstrip().startswith('#'))]
    names = [fn for _, fn in entries]
    chk(f'уровень 0а: манифест = зашитый список ({len(ALLOW)} записей)',
        len(entries) == len(ALLOW) and sorted(names) == sorted(ALLOW) and len(set(names)) == len(names),
        f'{len(entries)} записей')
    bad = [fn for h, fn in entries if fn != 'selfcheck_v192.py'
           and not (os.path.exists(fn) and hashlib.sha256(open(fn, 'rb').read()).hexdigest() == h)]
    chk('уровень 0б: SHA-256 файлов пакета', not bad, ', '.join(bad) if bad else 'кроме selfcheck (внешняя проверка)')
except Exception as ex:
    chk('уровень 0: манифест', False, str(ex))
if FAIL:
    print('ИТОГ: ПРОВАЛ НА УРОВНЕ 0'); sys.exit(1)

dm = S.build_modern(); ds = S.build_splice()

for nm, d, ref in [('соврем.', dm, 'data/nav_v13_modern.csv'), ('склейка', ds, 'data/nav_v13_splice.csv')]:
    r, nav, st = sim164(*d, cap=None, first_day='legacy', band=S.BAND)   # ред.33: регрессия к замороженной полосе 3%
    et = pd.read_csv(ref, parse_dates=[0], index_col=0)['nav']
    chk(f'Ф: регрессия к замороженному NAV ({nm}), 1e-12',
        et.index.equals(nav.index) and np.allclose(et.values, nav.values, rtol=1e-12, atol=0))
r, navm, st = sim164(*dm, cap=2.0, first_day='fixed'); c, dd = S.met(r)
chk('Ф соврем. (ред. 33, полоса 10%)', abs(c-9.78) <= 0.01 and st['cap_events'] == 140
    and abs(st['max_close_lev']-2.0391) <= 0.0005 and st['days_close_above'] == 127 and st['viol_after_costs'] == 0,
    f'{c:.2f}%/{dd:.2f}%')
for sp, cexp in [(0.81, 9.29), (1.00, 9.01), (1.5, 8.19)]:
    r2, _, _ = sim164(*dm, cap=2.0, first_day='fixed', spread_pp=sp); c2, _ = S.met(r2)
    chk(f'Ф чувствительность: спред {sp}', abs(c2-cexp) <= 0.01, f'{c2:.2f}%')
r, navs, st = sim164(*ds, cap=2.0, first_day='fixed'); c, dd = S.met(r)
chk('Ф склейка (ред. 33, полоса 10%)', abs(c-11.26) <= 0.01 and st['cap_events'] == 659
    and abs(st['max_close_lev']-2.0816) <= 0.0005 and st['days_close_above'] == 609 and st['viol_after_costs'] == 0,
    f'{c:.2f}%/{dd:.2f}%')
for f, nav in [('nav_v164_modern.csv', navm), ('nav_v164_splice.csv', navs)]:
    et = pd.read_csv(f, parse_dates=[0], index_col=0)['nav']
    chk(f'Ф NAV = эталон {f} (1e-12)', et.index.equals(nav.index) and np.allclose(et.values, nav.values, rtol=1e-12, atol=0))

r, nave, st = sim_etf(*dm); c, dd = S.met(r)
chk('Е 10 млн (физ., норматив; буфер 5%, полная лестница)', abs(c-9.83) <= 0.01 and abs(dd+21.92) <= 0.05, f'{c:.2f}%/{dd:.2f}%')
chk('Е: кап и инвариант', st['cap_events'] == 99 and st['days_close_above'] == 82
    and abs(st['max_close_lev']-2.0518) <= 0.0005 and st['viol_after_costs'] == 0,
    f"{st['cap_events']}/{st['days_close_above']}/{st['max_close_lev']:.4f}/0")
chk('Е: издержки, п.п./год', abs(st['loan_pp_yr']-0.511) <= 0.02 and abs(st['ter_pp_yr']-0.11) <= 0.01
    and abs(st['drag_pp_yr']-0.20) <= 0.01, f"займ {st['loan_pp_yr']:.3f} / TER {st['ter_pp_yr']:.2f} / drag {st['drag_pp_yr']:.2f}")
chk('Е: контрольная прокси-метрика маржи', abs(st['min_ratio']-1.950) <= 0.005, f"{st['min_ratio']:.3f}")
et = pd.read_csv('nav_etf_modern.csv', parse_dates=[0], index_col=0)['nav']
chk('Е NAV = эталон (1e-12)', et.index.equals(nave.index) and np.allclose(et.values, nave.values, rtol=1e-12, atol=0))
r2, nave2, _ = sim_etf(*dm)
chk('Е: детерминизм', bool(np.array_equal(nave.values, nave2.values)))
r1, _, st1 = sim_etf(*dm, cap0=1e6); c1, _ = S.met(r1)
chk('Е масштаб пилота 1 млн', abs(c1-9.70) <= 0.02 and abs(st1['loan_pp_yr']-0.598) <= 0.03,
    f"{c1:.2f}%, займ {st1['loan_pp_yr']:.3f}")

# --- боевой контур ред. 33: он теперь часть пакета, значит и часть проверок ---
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'live'))
import daily as _DL, journal as _J
import contracts as _CTsc      # корни — одна точка (contracts.FUT_ROOTS)
import mr_engine as _M2
import pandas as _pd, tempfile as _tf

_held = _DL.Book(d_fix=8.0, n_e=260, n_b=101, unit_is_mes=True,
                 prev_st_eq=True, prev_st_bd=True, ser_a='U26', ser_b='U26')
_m = _DL.Market(date=_pd.Timestamp('2026-08-26'), px_eq_prev=600.0, dref_prev=8.0,
                dref_today=8.0, px_eq_today=600.0, roll_today=True, st_eq=True, st_bd=True)
_rd = _DL.step(_held, _m, 10_000_000.0, check_guards=False)
_ro = _DL.book_to_orders(_rd, _held)
chk('контур: ролл даёт пару заявок в РАЗНЫХ сериях',
    len(_rd.roll_pairs) == 2 and all(i not in _CTsc.FUT_ROOTS for i, _ in _ro)
    and sorted(q for i, q in _ro if i.startswith('ZN')) == [-101, 101])
_nx = _DL.step(_rd.book_after, _DL.Market(date=_pd.Timestamp('2026-08-27'), px_eq_prev=600.0,
               dref_prev=8.0, dref_today=8.0, px_eq_today=600.0, roll_today=False,
               st_eq=True, st_bd=True), 10_000_000.0, check_guards=False)
chk('контур: назавтра после ролла книга НЕ откатывается в старую серию',
    not _nx.roll_pairs and _nx.book_after.ser_b == 'Z26')
chk('контур: чистый перенос серии виден признаку trade',
    _DL.Decision(date=_pd.Timestamp('2026-08-26'), roll_pairs=[{'leg': 'Б'}]).trade)
chk('§1: ноябрьский ролл присутствует в календаре (исправление ред. 33)',
    len([x for x in S.roll_days_calendar(ds[0]) if x.month == 11 and x.year == 2025]) == 1)
chk('контур: отказ §8 не меняет состояние книги',
    _DL.step(_DL.Book(d_fix=8.0), _m, 2_999_999.0).book_after.n_b == 0)
with _tf.TemporaryDirectory() as _t:
    _jp = os.path.join(_t, 'j.csv')
    for _k in range(3):
        _J.append(_jp, dict(date=f'2026-08-0{_k+1}', leg='Б', instrument='ZNZ26', qty=1,
                            px_order=112.0, px_fill=112.01, commission=2.0,
                            reason='проверка', nav='1', leverage='2'))
    _ok_chain = _J.verify(_jp) == 3
    _ln = open(_jp).read().splitlines(); _ln[2] = _ln[2].replace('проверка', 'подделка')
    open(_jp, 'w').write('\n'.join(_ln) + '\n')
    try:
        _J.verify(_jp); _broke = False
    except ValueError:
        _broke = True
chk('контур: журнал §7 обнаруживает правку задним числом', _ok_chain and _broke)
import state as _ST
_b0 = _DL.Book(d_fix=8.0, n_e=260, n_b=101, unit_is_mes=True, prev_st_eq=True,
               prev_st_bd=True, ser_a='U26', ser_b='U26', es_held=26)
with _tf.TemporaryDirectory() as _t2:
    _sp = os.path.join(_t2, 'book.json')
    _ST.save(_sp, _b0, 'F', 3)
    _b1, _s1, _r1 = _ST.load(_sp, _DL.Book)
    _exp = _ST.expected_positions(_b0, 'F')
    _late = dict(_exp); _late['ZNU26'] = _exp['ZNU26'] + 10
    import subprocess as _sp2
_inv = _sp2.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 'live', 'invariants.py')], capture_output=True, text=True)
_mut = _sp2.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 'live', 'mutation.py')], capture_output=True, text=True)
chk('контур: каждая мутация ловится, ни один инвариант не вакуумен', _mut.returncode == 0,
    ' | '.join(l for l in _mut.stdout.splitlines() if 'НЕ ПОЙМАЛ' in l or 'ТОЖДЕСТВА' in l) or
    'все мутации пойманы, вакуумных нет')
chk('контур: инварианты держатся на всём переборе состояний и сессий',
    _inv.returncode == 0,
    ' | '.join(l for l in _inv.stdout.splitlines() if l.startswith('состояний') or
               l.startswith('сессий')))
# ВЫПУСКНОЙ БАРЬЕР ВКЛЮЧАЕТ REPLAY И СЦЕНАРНЫЕ ПРОВЕРКИ (девятнадцатый круг, №21): прежде
# selfcheck гонял только invariants+mutation, и эквивалентность 1e-12 с книгами/сериями,
# как и сценарии contour_test, не входили в «ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ» вовсе.
_rep = _sp2.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 'live', 'replay_check.py')], capture_output=True, text=True)
chk('контур: replay-эквивалентность (NAV, книги, серии, упаковка) в выпускном барьере',
    _rep.returncode == 0 and 'ЭКВИВАЛЕНТНОСТЬ ДОКАЗАНА' in (_rep.stdout or ''),
    ((_rep.stdout or _rep.stderr).strip().splitlines() or ['нет вывода'])[-1])
_ct = _sp2.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                'live', 'contour_test.py')], capture_output=True, text=True)
chk('контур: сценарные проверки contour_test в выпускном барьере',
    _ct.returncode == 0,
    ((_ct.stdout or _ct.stderr).strip().splitlines() or ['нет вывода'])[-1])
chk('контур: состояние переживает перезапуск и ловит позднее исполнение',
        _b1 == _b0 and _s1 == 3 and _ST.reconcile(_b0, 'F', _exp) == []
        and len(_ST.reconcile(_b0, 'F', _late)) == 1)
chk('МР: порог §8 отсекает маршрут Ф ниже 3 млн',
    _M2.MIN_NLV_F == 3_000_000.0)
import depr_real as _DR
_dd = _DR.build()
_pk, _ctd = _DR.pack(*_dd, _DR.UNIT_A_TODAY, _DR.UNIT_B_TODAY)
_old = S.CTD_RATIO; S.CTD_RATIO = _ctd
try:
    _r, _nv, _stD = sim164(*_pk, cap=2.0, first_day='fixed', band=0.10, cap0=_DR.CAP0)
finally:
    S.CTD_RATIO = _old
_tot, _ddm = _DR.metrics(_r, _dd[0])
chk('Депрессия на точной механике (полоса 10%)',
    abs(_tot + 6.0) <= 0.15 and abs(_ddm + 47.1) <= 0.2
    and abs(_stD['max_close_lev'] - 2.1201) <= 0.001 and _stD['viol_after_costs'] == 0,
    f"{_tot:.1f}% / {_ddm:.1f}% / плечо {_stD['max_close_lev']:.4f} / наруш {_stD['viol_after_costs']}")

import transition as T
import shutil, json
from datetime import date
_, tiers = M.load_tariff('mr_tariff.csv', date(2026, 8, 8))
chk('МР: точная Е(10 млн)', abs(M.eq_E(10e6, tiers)-0.8793) <= 0.001)
chk('МР: точная Е(1 млн)', abs(M.eq_E(1e6, tiers)-1.0456) <= 0.001)
shutil.copy('mr_journal.csv', _JP('sc_j.csv')); shutil.copy('mr_state.csv', _JP('sc_s.csv'))
M.test_configure(_JP('sc_j.csv'))
chk('МР: ретро = HOLD, маршрут Ф', M.run('mr_inputs_retro.csv', 'mr_tariff.csv', _JP('sc_s.csv'), _JP('sc_j.csv'),
    '2026-08-08', 10e6, write=False) == M.HOLD)
HDR = 'quarter_id,window_start,window_end,s_eq_pp,quote_base,basis_to_effr_pp,s_zn_pp,source_url,fix_date,status\n'
U = 'https://x.example/a'
W3, W4 = ('2025-11-25', '2026-02-25'), ('2026-02-25', '2026-05-26')
SW = [f'A,{W3[0]},{W3[1]},1.6,EFFR,NA,0,{U},2026-03-01,OK', f'B,{W4[0]},{W4[1]},1.6,EFFR,NA,0,{U},2026-06-01,OK']
def mk(rows, j=_JP('sc_j2.csv'), s=_JP('sc_s2.csv'), write=False, nlv=10e6, tariff='mr_tariff.csv'):
    open(_JP('sc_i.csv'), 'w').write(HDR + '\n'.join(rows) + '\n')
    if not os.path.exists(j): open(j, 'w').write('asof,event,detail\n')
    M.test_configure(j)
    return M.run(_JP('sc_i.csv'), tariff, s, j, '2026-08-08', nlv, write=write)
for f in (_JP('sc_j2.csv'), _JP('sc_s2.csv')):
    if os.path.exists(f): os.remove(f)
chk('МР: старая пара при мёртвой зоне последних = HOLD', mk([
    f'X,2025-05-27,2025-08-26,1.6,EFFR,NA,0,{U},2025-09-01,OK', f'Y,2025-08-26,2025-11-25,1.6,EFFR,NA,0,{U},2025-12-01,OK',
    f'C,{W3[0]},{W3[1]},0.5,EFFR,NA,0,{U},2026-03-01,OK', f'D,{W4[0]},{W4[1]},0.5,EFFR,NA,0,{U},2026-06-01,OK']) == M.HOLD)
chk('МР: NA/фиктивные/inf/будущее/NaN-NLV — INVALID',
    mk([f'A,{W3[0]},{W3[1]},1.6,EFFR,NA,NA,{U},2026-03-01,OK', SW[1]]) == M.INVALID
    and mk([f'A,{W3[0]},{W3[1]},1.6,EFFR,NA,0,https://,2026-03-01,OK', SW[1]]) == M.INVALID
    and mk([f'A,{W3[0]},{W3[1]},inf,EFFR,NA,0,{U},2026-03-01,OK', SW[1]]) == M.INVALID
    and mk(SW, nlv=float('nan')) == M.INVALID)
open(_JP('sc_tf.csv'), 'w').write('as_of,tier_limit,spread_pct\n2026-08-08,1000000000,nan\n')
chk('МР: NaN в тарифе — INVALID', mk(SW, tariff=_JP('sc_tf.csv')) == M.INVALID)
c1 = mk(SW, write=True)
chk('МР: двухфазность — сигнал не меняет маршрут', c1 == M.SWITCH_E
    and open(_JP('sc_s2.csv')).read().splitlines()[-1].startswith('F,E,'))
chk('МР: COMPLETE с чужим sid отклоняется',
    M.confirm_transition(_JP('sc_j2.csv'), _JP('sc_s2.csv'), '2026-08-08', 'E', kind='complete', tid='t1', sid='alien') is False)
_sid = open(_JP('sc_j2.csv')).read().strip().splitlines()[-1].split('|')[-1].strip('"')
chk('МР: OPEN регистрирует tid; COMPLETE по своему sid+tid принимается',
    M.confirm_transition(_JP('sc_j2.csv'), _JP('sc_s2.csv'), '2026-08-08', 'E', kind='open', tid='t1', sid=_sid) is True
    and M.confirm_transition(_JP('sc_j2.csv'), _JP('sc_s2.csv'), '2026-08-08', 'E', kind='complete', tid='t9', sid=_sid) is False
    and M.confirm_transition(_JP('sc_j2.csv'), _JP('sc_s2.csv'), '2026-08-08', 'E', kind='complete', tid='t1', sid=_sid) is True
    and open(_JP('sc_s2.csv')).read().splitlines()[-1].startswith('E,,'))
chk('МР: повторный COMPLETE без нового сигнала отклоняется',
    M.confirm_transition(_JP('sc_j2.csv'), _JP('sc_s2.csv'), '2026-08-08', 'E', kind='complete', tid='t1', sid=_sid) is False)
chk('МР: RESOLVED без открытого MIXED отклоняется',
    M.confirm_transition(_JP('sc_j2.csv'), _JP('sc_s2.csv'), '2026-08-08', 'F', kind='resolved') is False)
open(_JP('sc_jL2.csv'), 'w').write('asof,event,detail\n2026-01-01,SWITCH_SIGNAL,E|s1\n2026-01-01,TRANSITION_OPEN,E|s1|tidA\n2026-01-02,TRANSITION_COMPLETE,E|s1|tidA\n'
    '2027-01-01,SWITCH_SIGNAL,F|s2\n2027-01-01,TRANSITION_OPEN,F|s2|tidB\n2027-01-02,TRANSITION_COMPLETE,F|s2|tidB\n'
    '2031-01-01,SWITCH_SIGNAL,E|s3\n2031-01-01,TRANSITION_OPEN,E|s3|tidA\n2031-01-02,TRANSITION_COMPLETE,E|s3|tidA\n')
M.test_configure(_JP('sc_jL2.csv'))
chk('МР: легитимный повтор tid спустя 3+ года учитывается',
    M.derive_state(_JP('sc_jL2.csv'), date(2031, 1, 3))[0] == 'E' and M.switches_3y(_JP('sc_jL2.csv'), date(2031, 1, 3)) == 1)
open(_JP('sc_jMx.csv'), 'w').write('asof,event,detail\n2026-08-01,SWITCH_SIGNAL,E|s1\n2026-08-01,TRANSITION_OPEN,E|s1|t1\n2026-08-02,TRANSITION_MIXED,E|s1|t1\n2026-08-03,TRANSITION_RESOLVED,E|s1|t1\n')
M.test_configure(_JP('sc_jMx.csv'))
M.test_configure(_JP('sc_jMx.csv'))
chk('МР: MIXED→RESOLVED меняет маршрут и входит в счётчик',
    M.derive_state(_JP('sc_jMx.csv'), date(2026, 8, 8))[0] == 'E' and M.switches_3y(_JP('sc_jMx.csv'), date(2026, 8, 8)) == 1)
open(_JP('sc_jL3.csv'), 'w').write('asof,event,detail\nBAD,TRANSITION_COMPLETE,E|s|t\n')
M.test_configure(_JP('sc_jL3.csv'))
try:
    M.derive_state(_JP('sc_jL3.csv'), date(2026, 8, 8)); chk('МР: повреждённая строка журнала', False)
except M.JournalCorrupt:
    chk('МР: повреждённая строка журнала', True)
open(_JP('sc_jL6.csv'), 'w').write('asof,event,detail\n2026-08-01,SWITCH_SIGNAL,E,EXTRA\n')
M.test_configure(_JP('sc_jL6.csv'))
try:
    M.derive_state(_JP('sc_jL6.csv'), date(2026, 8, 8)); chk('МР: лишнее CSV-поле = порча', False)
except M.JournalCorrupt:
    chk('МР: лишнее CSV-поле = порча', True)
open(_JP('sc_jL4.csv'), 'w').write('asof,event,detail\n')
M.test_configure(_JP('sc_jL4.csv'))
M.append_event(_JP('sc_jL4.csv'), '2026-08-08', 'REDUCE_SIGNAL', '20-дн >1,5, альтернатива недоступна')
import csv as _csv
chk('МР: запятые в detail экранируются',
    list(_csv.DictReader(open(_JP('sc_jL4.csv'))))[0]['detail'] == '20-дн >1,5, альтернатива недоступна')
open(_JP('sc_jAn.csv'), 'w').write('asof,event,detail\n2026-01-02,TRANSITION_COMPLETE,E|s0|orphan\n')
M.test_configure(_JP('sc_jAn.csv'))
open(_JP('sc_jV2.csv'), 'w').write('asof,event,detail\n2026-08-01,SWITCH_SIGNAL,E|s1\n2026-08-01,TRANSITION_OPEN,E|s1|T1\n2026-08-02,TRANSITION_MIXED,E|s1|T1\n2026-08-03,TRANSITION_RESOLVED,F|sX|TX\n')
M.test_configure(_JP('sc_jV2.csv'))
M.test_configure(_JP('sc_jV2.csv'))
_v2 = M.derive_state(_JP('sc_jV2.csv'), date(2026, 8, 8))
chk('МР: чужой RESOLVED не закрывает MIXED (аномалия)', _v2[2] is True and len(_v2[3]) == 1)
open(_JP('sc_jV3.csv'), 'w').write('asof,event,detail\n2026-08-01,TRANSITION_COMPLETED_X,E|s|t\n')
M.test_configure(_JP('sc_jV3.csv'))
try:
    M.derive_state(_JP('sc_jV3.csv'), date(2026, 8, 8)); chk('МР: фиктивное имя события = порча', False)
except M.JournalCorrupt:
    chk('МР: фиктивное имя события = порча', True)
open(_JP('sc_iSW.csv'), 'w').write(HDR + '\n'.join(SW) + '\n')
M.test_configure(_JP('sc_jAn.csv'))
chk('МР: аномалия журнала блокирует переключения (REVIEW)',
    M.run(_JP('sc_iSW.csv'), 'mr_tariff.csv', _JP('sc_sAn.csv'), _JP('sc_jAn.csv'), '2026-08-08', 10e6, write=False) == M.REVIEW)
open(_JP('sc_jG.csv'), 'w').write('asof,event,detail\n')
M.test_configure(_JP('sc_jG.csv'))
chk('МР: журнальное разрешение GRANULARITY_EXCEPTION находится по sid',
    (M.grant_granularity(_JP('sc_jG.csv'), _JP('sc_sG.csv'), '2026-08-08', 'sX', 98560.0) or True)
    and M.find_grant(_JP('sc_jG.csv'), '2026-08-08', 'sX') == 98560.0
    and M.find_grant(_JP('sc_jG.csv'), '2026-08-08', 'sY') is None)

# --- Контртесты вердиктов ред. 12-13 ---
open(_JP('w1j.csv'), 'w').write('asof,event,detail\n2026-08-08,SWITCH_SIGNAL,E|s1\n')
M.test_configure(_JP('w1j.csv'))
chk('МР: MIXED без OPEN отклоняется', M.confirm_transition(_JP('w1j.csv'), _JP('w1s.csv'), '2026-08-08', 'E', kind='mixed', tid='T1', sid='s1') is False)
chk('МР: OPEN эксклюзивен и идемпотентен',
    M.confirm_transition(_JP('w1j.csv'), _JP('w1s.csv'), '2026-08-08', 'E', kind='open', tid='T1', sid='s1') is True
    and M.confirm_transition(_JP('w1j.csv'), _JP('w1s.csv'), '2026-08-08', 'E', kind='open', tid='T1', sid='s1') is True
    and open(_JP('w1j.csv')).read().count('TRANSITION_OPEN') == 1
    and M.confirm_transition(_JP('w1j.csv'), _JP('w1s.csv'), '2026-08-08', 'E', kind='open', tid='T2', sid='s1') is False)
chk('МР: MIXED/ABORT с чужим tid отклоняются',
    M.confirm_transition(_JP('w1j.csv'), _JP('w1s.csv'), '2026-08-08', 'E', kind='mixed', tid='TX', sid='s1') is False
    and M.confirm_transition(_JP('w1j.csv'), _JP('w1s.csv'), '2026-08-08', 'E', kind='abort', tid='TX', sid='s1') is False)
M.confirm_transition(_JP('w1j.csv'), _JP('w1s.csv'), '2026-08-08', 'E', kind='mixed', tid='T1', sid='s1')
chk('МР: COMPLETE после MIXED запрещён; RESOLVED с чужой привязкой/целью X отклоняется',
    M.confirm_transition(_JP('w1j.csv'), _JP('w1s.csv'), '2026-08-08', 'E', kind='complete', tid='T1', sid='s1') is False
    and M.confirm_transition(_JP('w1j.csv'), _JP('w1s.csv'), '2026-08-08', 'X', kind='resolved', tid='T1', sid='s1') is False
    and M.confirm_transition(_JP('w1j.csv'), _JP('w1s.csv'), '2026-08-08', 'E', kind='resolved', tid='TX', sid='s1') is False
    and M.derive_state(_JP('w1j.csv'), date(2026, 8, 8))[2] is True)
open(_JP('w3j.csv'), 'w').write('asof,event,detail\n2026-08-08,GRANULARITY_EXCEPTION_FAKE,s1|98560\n')
M.test_configure(_JP('w3j.csv'))
try:
    M.find_grant(_JP('w3j.csv'), '2026-08-08', 's1'); chk('МР: фиктивное событие гранта = порча', False)
except M.JournalCorrupt:
    chk('МР: фиктивное событие гранта = порча', True)
open(_JP('w4j.csv'), 'w').write('asof,event,detail\n2026-08-08,GRANULARITY_EXCEPTION,s1|nan\n')
M.test_configure(_JP('w4j.csv'))
try:
    M.find_grant(_JP('w4j.csv'), '2026-08-08', 's1'); chk('МР: nan в разрешении = порча', False)
except M.JournalCorrupt:
    chk('МР: nan в разрешении = порча', True)
for f in (_JP('wpj.csv'), _JP('wps.csv')):
    if os.path.exists(f): os.remove(f)
open(_JP('wpi.csv'), 'w').write(HDR + '\n'.join(SW) + '\n')
if not os.path.exists(_JP('wpj.csv')): open(_JP('wpj.csv'), 'w').write('asof,event,detail\n')
M.test_configure(_JP('wpj.csv'))
_c1 = M.run(_JP('wpi.csv'), 'mr_tariff.csv', _JP('wps.csv'), _JP('wpj.csv'), '2026-08-08', 10e6, write=True)
_c2 = M.run(_JP('wpi.csv'), 'mr_tariff.csv', _JP('wps.csv'), _JP('wpj.csv'), '2026-08-08', 10e6, write=True)
chk('МР: параллельный/повторный запуск не плодит сигналы (HOLD, сигнал один)',
    _c1 == M.SWITCH_E and _c2 == M.HOLD and open(_JP('wpj.csv')).read().count('SWITCH_SIGNAL') == 1)

def _seed_books_from(netpos):
    """КНИГИ МАРШРУТОВ СОГЛАСОВАНЫ С ПОЗИЦИЯМИ СТАБА (тридцатый круг, №4).

    Предполёт перехода теперь сверяет книгу ИСХОДНОГО маршрута с брокером: прежде она
    грузилась только ради существования, и переход «отмывал» расхождение — новая книга
    строилась из фактического счёта, а старое расхождение исчезало вместе со старой
    книгой, без О-5. Но стенды описывали состояние, которого в бою не бывает: общая
    фикстура Ф несла 26 единиц сетки и 10 ZN, а стабы отдавали один ZN — и переходы
    «проходили». Это ровно тот системный источник ложной зелени, что записан в §12:
    фикстура беднее реальности.

    Здесь книга собирается ИЗ ТЕХ ЖЕ позиций, что отдаёт стаб, для обоих маршрутов сразу —
    какой из них окажется исходным, решает план. Одно место вместо сорока пяти.
    """
    import daily as _DLs, state as _STs, journal as _Js
    _n = {str(k): float(v) for k, v in (netpos or {}).items()}
    _es = _n.get('ES', 0.0) + _n.get('ESU26', 0.0)
    _mes = _n.get('MES', 0.0) + _n.get('MESU26', 0.0)
    _zn = _n.get('ZN', 0.0) + _n.get('ZNU26', 0.0)
    _eq, _bd = _n.get('CSPX', 0.0), _n.get('CBU0', 0.0)

    def _j(route):
        # У ТОРГОВАВШЕЙ КНИГИ ЕСТЬ ЖУРНАЛ (двадцать пятый круг, №14): книга с сессией №1
        # без журнала описывает состояние, которого в бою не бывает, и предполёт честно
        # объявляет историю утраченной. Первая редакция этого конструктора писала книгу
        # без журнала — и выпуск упал ровно на этом, поделом.
        _jp = _STs.lock_dir() / f'journal-{route}.csv'
        if not _jp.exists():
            _Js.append(_jp, dict(date='2026-08-08', leg='', instrument='ИТОГ', qty=0,
                                 px_order='-', px_fill='', commission='', reason='',
                                 nav='1000000', leverage='1.99', roll_spread_near='',
                                 roll_spread_far='', note='итог сессии 1: строк 0'))

    # КНИГА ЗАВОДИТСЯ ТОЛЬКО ДЛЯ МАРШРУТА С ПОЗИЦИЯМИ. Пустая книга с номером сессии — это
    # тоже состояние, которого в бою не бывает: маршрут либо торговал, либо его нет.
    if _es or _mes or _zn:
        _STs.save(_STs.book_path('F'),
                  _DLs.Book(d_fix=7.9, n_e=int(_es * 10 + _mes), n_b=int(_zn),
                            unit_is_mes=True, prev_st_eq=True, prev_st_bd=True,
                            ser_a='U26', ser_b='U26', es_held=int(_es),
                            last_session='2026-08-08', close_provisional=False,
                            prev_close_lev=1.99),
                  'F', 1, note='фикстура стендов исполнителя: собрана из позиций стаба')
        _j('F')
    if _eq or _bd:
        _STs.save(_STs.book_path('E'),
                  _DLs.BookE(n_eq=_eq, n_bd=_bd, prev_st_eq=True, prev_st_bd=True,
                             last_session='2026-08-08', close_provisional=False,
                             prev_close_lev=1.99),
                  'E', 1, note='фикстура стендов исполнителя: собрана из позиций стаба')
        _j('E')


# --- Исполнитель v6.1: журнал пишет сам, хук устранён ---
class _B:
    def __init__(s, preview=True, timeout=False, frac=False, netpos=None, cancelnone=False, oo=None,
                 nlv=1e6, seed=True):
        s.p = preview; s.t = timeout; s.f = frac; s.np = netpos or {}; s.nlv = nlv
        s.cn = cancelnone; s.oo = oo or []; s.n = 0; s.calls = []; s.u = 0.0; s.maxu = 0.0
        # seed=False — ЧЕСТНЫЙ RESUME (тридцать восьмой круг, №1). Пересев книг из позиций
        # САМОГО брокера делает в фикстуре то, чего в бою не бывает: книга на диске начинает
        # совпадать с промежуточным счётом. Именно это скрывало, что предполёт безусловно
        # сверял книгу с брокером и потому отвергал resume частично исполненного перехода
        # ДО кода восстановления. Для сценария продолжения книга обязана остаться ИСХОДНОЙ.
        if seed:
            _seed_books_from(s.np)  # книги обоих маршрутов согласованы со стабом (№4)
        # ЛИЧНОСТЬ СЧЁТА (тридцатый круг, №6): брокер обязан её сообщать, иначе
        # позиции могут относиться к чужому счёту. Стенды пинуют DUTEST01.
        s.account = __import__('os').environ.get('ADDFUT_ACCOUNT') or 'DUTEST01'
    def _px(s, i):
        i = str(i)
        if i.startswith('ZN'): return 98560.0
        if i == 'CBU0': return 5.0
        if i == 'CSPX': return 700.0
        return 38428.0
    # net_liquidation ОБЯЗАТЕЛЕН (двадцать второй круг, №4): сверка капитала стала
    # fail-closed, брокер без метода отвергается. Стенды зовут execute с capital 1e6 и
    # 10e6 — стаб настраивается на фактический капитал вызова через атрибут nlv.
    def net_liquidation(s): return getattr(s, 'nlv', None) or 1e6
    # ОТЧЁТЫ ОБ ИСПОЛНЕНИИ (тридцатый круг, №5): ABORT требует ДОКАЗАННОГО отсутствия
    # сделок. Стаб честно отдаёт то, что исполнил: пусто — значит нечего и доказывать
    # иначе. Без метода любой исход становился бы MIXED, и ABORT стал бы недостижим.
    def todays_executions(s):
        return [c for c in s.calls if c[0] in ('sell', 'buy')]
    # unit_ref ОБЯЗАТЕЛЕН (двадцать девятый круг, №3): цены плана сверяются с рынком.
    def unit_ref(s, i, cls):
        p = s._px(i); return (p * 0.5, p * 2.0)
    # done_all — четвёртый аргумент боевого preview (разбор /code-review 21.08):
    # исполнитель подаёт его по имени, и заглушка со старой сигнатурой роняла бы
    # выпуск TypeError'ом ВМЕСТО вердикта — ошибка кода в списке CODE_ERRORS.
    def preview(s, orders=None, emergency=False, done_all=False): return s.p
    def sell_units(s, i, u):
        s.n += 1; f = (u - 0.5 if s.f and u > 0 else u)
        s.calls.append(('sell', i, u)); s.np[i] = s.np.get(i, 0) - int(f)
        s.u += abs(f)*s._px(i); s.maxu = max(s.maxu, s.u)
        return (f's{s.n}', f)
    def buy_units(s, i, u):
        s.n += 1; s.calls.append(('buy', i, u)); s.np[i] = s.np.get(i, 0) + u
        s.u -= u*s._px(i)
        return (f'b{s.n}', u)
    def cancel_order(s, o): return None if s.cn else True
    def open_orders(s): return list(s.oo)
    def net_positions(s): return dict(s.np)
    def minutes_since(s, k): return 99 if s.t else 0
    def gross(s, d_fix=None): return 1.99
SP = _JP('sc_tr.json'); JX = _JP('sc_jx.csv'); SX = _JP('sc_sx.csv')
def _cl():
    for f in (SP, JX, SX):
        if os.path.exists(f): os.remove(f)
def _sp(**kw):
    """Состояние перехода С DIGEST — как его пишет сам исполнитель (31-й круг, №16).

    Фикстуры resume клали СЫРОЙ json.dump без поля digest, а _execute_locked с тридцатого
    круга (№13) отвергает такое состояние ПЕРВЫМ делом. Три стенда из-за этого доказывали
    не то, что заявлено: «cancel_order=None на resume = инцидент» получал инцидент по
    поводу digest и до cancel_order не доходил, а сценарии, ожидающие POSTPONED/COMPLETE,
    обрывали бы весь прогон. Тот же класс, что №11 тридцатого круга: успешный путь никем
    не исполнялся. Состояние собирается ровно тем же кодом, что в бою."""
    st = dict(kw)
    st['digest'] = T._state_digest(st)
    with open(SP, 'w') as _f:
        json.dump(st, _f)
    return st
def _sig(target, grant=None, sid='s1', approve=True):
    hist = ''
    if target == 'F':
        hist = ('2026-08-01,SWITCH_SIGNAL,E|s0\n2026-08-01,OWNER_APPROVE,E|s0\n'
                '2026-08-01,TRANSITION_OPEN,E|s0|t0\n'
                '2026-08-01,TRANSITION_COMPLETE,E|s0|t0\n')
    # ХРОНОЛОГИЯ НЕУБЫВАЮЩАЯ (двадцать первый круг): сигнал и одобрение обязаны быть НЕ
    # ПОЗЖЕ даты исполнения перехода (asof=07.08, торговый день), иначе OPEN оказывается
    # ретро-записью и отвергается защитой девятнадцатого круга (№14). Тариф при этом
    # остаётся датированным 08.08 — это другая величина и другая потребность.
    ok = ('2026-08-10,OWNER_APPROVE,' + target + '|' + sid + '\n') if approve else ''
    open(JX, 'w').write('asof,event,detail\n' + hist + '2026-08-10,SWITCH_SIGNAL,'
                        + target + '|' + sid + '\n' + ok)
    M.test_configure(JX)
    if grant: M.grant_granularity(JX, SX, '2026-08-10', sid, grant)
# ASOF ИСПОЛНИТЕЛЯ — ТОРГОВЫЙ ДЕНЬ (двадцать первый круг, №2): ворота общего
# окна стали fail-closed и отвергают выходной, а 08.08.2026 — суббота.
# Остальные стенды продолжают жить на 08-08: этой датой помечен тариф.
# ИЗОЛЯЦИЯ ОТ ЖИВЫХ ДАННЫХ О МАРЖЕ (двадцать первый круг). Стенды исполнителя проверяют
# ЕГО ПОВЕДЕНИЕ — тайм-аут, дробное исполнение, обрыв preview, чужую позицию, — а не
# содержимое margins_live.json. Без явной фикстуры они читали ЖИВОЙ замер и падали на
# «замер не покрывает серии реестра», то есть на ЧУЖОМ дефекте: отказ приходил до всякого
# поведения. Согласованность ПОСТАВЛЕННОЙ пары «замер <-> реестр» — отдельная проверка
# в конце файла, и подменять её фикстурой нельзя.
#
# РЕЕСТР НЕ ПОДМЕНЯЕТСЯ: исполнитель пинует его SHA-256, и чужой файл он отвергает
# («реестр не совпадает с пинованным SHA-256»). Поэтому ADDFUT_REGISTRY указывает на ТОТ ЖЕ
# instruments.csv пакета, а фикстура — только у замера маржи, под его FUT-серии (ES/MES/ZN).
_MFIX = _JP('margins_fix.json')
import json as _json1, datetime as _dt1
open(_MFIX, 'w').write(_json1.dumps({
    'ES': {'init': 34910.0, 'maint': 25059.0},
    'MES': {'init': 3491.0, 'maint': 2506.0},
    'ZN': {'init': 2157.0, 'maint': 1876.0},
    # ПРИВЯЗКА К ПОКОЛЕНИЮ РЕЕСТРА (тридцать второй круг, №8): con_id каждой серии из того
    # же instruments.csv, на который смотрит ADDFUT_REGISTRY ниже. Без поля замер теперь
    # отвергается — имя серии переживает исправление con_id, а маржа нет.
    '_meta': {'date': _dt1.datetime.now(_dt1.timezone.utc).strftime('%Y-%m-%d'),
              'account': 'DUTEST01', 'series': list(_CTsc.FUT_ROOTS),
              'con_ids': {r['instrument']: r['con_id']
                          for r in csv.DictReader(open('instruments.csv', encoding='utf-8'))
                          if r['instrument'] in _CTsc.FUT_ROOTS}}}))
_os1.environ['ADDFUT_MARGINS'] = _MFIX
_os1.environ['ADDFUT_REGISTRY'] = _os1.path.abspath('instruments.csv')
_os1.environ['ADDFUT_ACCOUNT'] = 'DUTEST01'

# ИСХОДНАЯ КНИГА ДЛЯ ПЕРЕДАЧИ (двадцать первый круг, №13). hand_over_book теперь ОТКАЗЫВАЕТ
# при отсутствии книги маршрута-источника: без неё теряются roll_pending (поставочное
# обязательство) и prev_st_* обеих ног, а d_fix обнуляется — замыкание считало бы вклад
# реального ZN нулевым. Стенды исполнителя доходят до COMPLETE, то есть до передачи, и
# обязаны иметь книгу — настоящий переход без неё не бывает. Пишется в ADDFUT_LOCK_DIR
# (строка 75), то есть во временный каталог прогона: машинное состояние не трогается.
import daily as _DL0, state as _ST0
_ST0.save(_ST0.book_path('F'),
          _DL0.Book(d_fix=7.9, n_e=26, n_b=10, unit_is_mes=True, prev_st_eq=True,
                    prev_st_bd=True, ser_a='U26', ser_b='U26', es_held=2,
                    last_session='2026-08-08', close_provisional=False,
                    prev_close_lev=1.99),
          'F', 1, note='фикстура стендов исполнителя')
# У ТОРГОВАВШЕЙ КНИГИ ЕСТЬ ЖУРНАЛ (двадцать пятый круг, №14 — предполёт передачи). Книга с
# сессией №1 без journal-F.csv описывает состояние, которого в бою не бывает: журнал ведётся
# с первой же сессии. Без него предполёт честно объявляет историю утраченной.
import journal as _J0
_J0.append(_ST0.lock_dir() / 'journal-F.csv', dict(
    date='2026-08-08', leg='', instrument='ИТОГ', qty=0, px_order='-', px_fill='',
    commission='', reason='', nav='1000000', leverage='1.99',
    roll_spread_near='', roll_spread_far='', note='итог сессии 1: строк 0'))

# ASOF ИСПОЛНИТЕЛЯ — ПОНЕДЕЛЬНИК 10.08 (двадцать первый круг, №2; двадцать второй, №22).
# Ворота общего окна стали fail-closed и отвергают выходной, а 08.08.2026 — суббота.
# Дата берётся ПОЗЖЕ всех журнальных событий и тарифа (08.08), а не раньше: события
# журнала МР неубывающие, и переход, датированный раньше сигнала или гранта, был бы
# ретро-записью. Сдвиг НАЗАД ломал и тариф, и гранты — проверено тремя прогонами.
# КАЛИТКА ДАТЫ ДЛЯ СТЕНДОВ (двадцать второй круг, №1): asof теперь сверяется с БИРЖЕВЫМ
# сегодня, иначе вчерашняя дата отключала часы, праздники и барьер «resume в той же
# сессии». Стенды обязаны ходить фиксированной датой, поэтому калитка включается ЯВНО и
# только здесь. Сама защита проверяется стендом 'переход задним числом запрещён' в
# invariants.py — БЕЗ калитки, иначе она была бы отключена во всём выпуске разом.
os.environ['ADDFUT_ASOF_OVERRIDE'] = '1'
# КАЛИТКА d_fix ДЛЯ СТЕНДОВ (тридцать третий круг, №3): брокер-макет _B не имеет .ib, а
# живая доходность нужна для Е→Ф с ногой Б. Прежде это молчаливо покрывалось откатом к
# старой книге Ф — то есть стенд закреплял как норму именно тот путь, который в бою даёт
# месяцы оценки ZN по чужой дюрации. Дверь названа явно, в бою не выставляется (env_guard
# автопилота запрещает переопределения ADDFUT_* на торговом входе).
os.environ['ADDFUT_DFIX_TEST'] = '1'
KW = dict(journal=JX, mr_state=SX, asof='2026-08-10')
L1 = {'BOND': dict(src=[('ZN', 1, 98560.0)], dst=('CBU0', 5.0, 'ETF'))}
_cl(); _sig('E', grant=98565.0); rr = []
for _ in range(3):
    try: rr.append(T.execute(_B(preview=False), SP, 1e6, L1, signal_id='s1', **KW)['postponed'])
    except T.Incident: rr.append('INC')
chk('Исполнитель: счётчик preview стоек', rr == [1, 2, 'INC'])
_cl(); _sig('E', grant=98565.0)
try: T.execute(_B(timeout=True, netpos={'ZN': 1, 'CBU0': 0}), SP, 1e6, L1, signal_id='s1', **KW)
except T.Incident: pass
_d = M.derive_state(JX, date(2026, 8, 11))
chk('Исполнитель: тайм-аут после исполнений = MIXED', _d[0] == 'F' and _d[2])
_cl(); _sig('E', grant=98565.0); inc = False
try: T.execute(_B(frac=True, netpos={'ZN': 1, 'CBU0': 0}), SP, 1e6, L1, signal_id='s1', **KW)
except T.Incident: inc = True
chk('Исполнитель: дробный fill = инцидент + MIXED', inc and M.derive_state(JX, date(2026, 8, 11))[2])
_cl(); _sig('E'); b0 = _B(netpos={'MES': 4}); inc = False
try: T.execute(b0, SP, 1e6, {'EQ': dict(src=[('MES', 4, 38428.0)], dst=('CSPX', 700.0, 'ETF'))}, signal_id='s1')
except T.Incident: inc = True
chk('Исполнитель: без journal/mr_state = инцидент до ордеров (обходной хук устранён)', inc and len(b0.calls) == 0)
# ФИКСТУРА ЖУРНАЛА ТОЖЕ ПРОВИЗИОНИРУЕТСЯ (тридцать первый круг, №13): содержимое журнала МР
# теперь заверяется digest в конфигурации развёртывания, и файл, написанный руками мимо
# append_event, — ровно то, что защита обязана поймать. Стенд проверяет ДРУГОЕ («журнал без
# сигнала = отказ open»), поэтому обязан предъявить согласованный журнал, а не подделку.
_cl(); open(JX, 'w').write('asof,event,detail\n'); M.test_configure(JX)
b1 = _B(netpos={'MES': 4}); inc = False
try: T.execute(b1, SP, 1e6, {'EQ': dict(src=[('MES', 4, 38428.0)], dst=('CSPX', 700.0, 'ETF'))}, signal_id='s1', **KW)
except T.Incident: inc = True
chk('Исполнитель: журнал без сигнала = отказ open до ордеров', inc and len(b1.calls) == 0)
# ЦЕНА ПЛАНА СВЕРЯЕТСЯ С РЫНКОМ (двадцать девятый круг, №3): сквозной путь, а не только
# сама функция — защита обязана стоять ДО первой заявки, иначе занижённая цена цели
# успевает превратиться в десятикратную покупку.
_cl(); _sig('E', grant=98565.0); bpx = _B(netpos={'ZN': 1, 'CBU0': 0}); inc = False
try:
    T.execute(bpx, SP, 1e6, {'BOND': dict(src=[('ZN', 1, 98560.0)], dst=('CBU0', 0.5, 'ETF'))},
              signal_id='s1', **KW)
except T.Incident as _ex:
    inc = 'вне рыночной полосы' in str(_ex)
chk('Исполнитель: цена цели вне рыночной полосы = отказ до ордеров', inc and len(bpx.calls) == 0)
_cl(); _sig('E'); L4 = {'EQ': dict(src=[('MES', 4, 38428.0)], dst=('CSPX', 700.0, 'ETF'))}
_plan = T.plan_lots(L4, 1e6)
_sp(asof='2026-08-10', tid=T.transition_id('s1', 'F', 'E', 1e6, _plan), postponed=0, done=[], executed_usd=1.0,
               order_ids=[], snapshot={'MES': 4, 'CSPX': 0}, log=[], opened=True)
b2 = _B(netpos={'MES': 4, 'CSPX': 0}, cancelnone=True, oo=['oX']); inc = False
try: T.execute(b2, SP, 1e6, L4, signal_id='s1', resume=True, **KW)
except T.Incident: inc = True
chk('Исполнитель: cancel_order=None на resume = инцидент до новых заявок', inc and len(b2.calls) == 0)
# ТРИДЦАТЬ СЕДЬМОЙ КРУГ, №13: отказ кэша состояния ПОСЛЕ долговечной записи TRANSITION_OPEN
# уходил обычным исключением, и исполнитель считал это ЧИСТЫМ отказом открытия: снимал обе
# метки handover-inflight, не писал ни ALARM, ни ABORT. Журнал держал OPEN, а единственный
# файловый барьер ежедневного контура был удалён — автопилот торговал бы поверх открытого
# перехода. Событие в журнале обязано быть закрыто нормативно, а метки не сняты молча.
_cl(); _sig('E', grant=98565.0)
_wsc_orig = M.write_state_cache
def _wsc_fail(*a, **k):
    raise RuntimeError('кэш состояния недоступен')
M.write_state_cache = _wsc_fail
_b13 = _B(netpos={'ZN': 1, 'CBU0': 0}); _inc13 = ''
try:
    T.execute(_b13, SP, 1e6, L1, signal_id='s1', **KW)
except BaseException as _ex13:
    _inc13 = str(_ex13)
finally:
    M.write_state_cache = _wsc_orig
_st13 = M.derive_state(JX, date(2026, 8, 11))
chk('Исполнитель: отказ кэша ПОСЛЕ OPEN закрывает переход нормативно, а не молча (37-й, №13)',
    ('кэш' in _inc13) and not _st13[5] and len(_b13.calls) == 0)

# --- Девятнадцатый круг: №11, №12, №14; долг пар восемнадцатого: №3 ---
_cl(); _sig('E', grant=98565.0)
_bLive = _B(netpos={'ZN': 1, 'CBU0': 0}, oo=['осиротевшая']); inc = False
try: T.execute(_bLive, SP, 1e6, L1, signal_id='s1', **KW)
except T.Incident as _ex: inc = 'живые заявки' in str(_ex)
chk('Исполнитель: живые заявки до свежего перехода = отказ до ордеров (18-й, №3)',
    inc and len(_bLive.calls) == 0)
_cl(); _sig('E', grant=98565.0)
_plan6 = T.plan_lots(L1, 1e6)
_tid6 = T.transition_id('s1', 'F', 'E', 1e6, _plan6)
M.append_event(JX, '2026-08-10', 'TRANSITION_OPEN', 'E|s1|' + _tid6)
_sp(asof='2026-08-10', tid=_tid6, postponed=0, done=[], executed_usd=1.0, order_ids=[],
               snapshot={'ZN': 1, 'CBU0': 0}, log=[], opened=True)
class _BPrevRaise(_B):
    def preview(s, orders=None, emergency=False, done_all=False): raise RuntimeError('margin preview оборван связью')
inc = False
try: T.execute(_BPrevRaise(netpos={'ZN': 1, 'CBU0': 0}, oo=['oY']), SP, 1e6, L1,
               signal_id='s1', resume=True, **KW)
except T.Incident as _ex: inc = 'preview оборван' in str(_ex)
chk('Исполнитель: обрыв preview на resume = MIXED, а не сырое исключение (19-й, №11)',
    inc and M.derive_state(JX, date(2026, 8, 11))[2])
_cl(); _sig('E', grant=98565.0)
class _BAlien(_B):
    def sell_units(s, i, u):
        r = _B.sell_units(s, i, u)
        s.np['NQ'] = 7                      # чужая позиция появилась ВО ВРЕМЯ перехода
        return r
inc = False
try: T.execute(_BAlien(netpos={'ZN': 1, 'CBU0': 0}), SP, 1e6, L1, signal_id='s1', **KW)
except T.Incident as _ex: inc = 'вне плана' in str(_ex)
chk('Исполнитель: позиция, появившаяся во время перехода, запрещает COMPLETE (19-й, №12)',
    inc and M.derive_state(JX, date(2026, 8, 11))[2])
_retro = False
try: M.append_event(JX, '2026-08-01', 'SWITCH_SIGNAL', 'F|s9')
except ValueError: _retro = True
chk('МР: ретро-датированная запись в журнал отклоняется (19-й, №14)', _retro)
_cl(); _sig('E', grant=98565.0)
L5 = {'BOND': dict(src=[('ZN', 2, 98560.0)], dst=('CBU0', 5.0, 'ETF'))}
_plan = T.plan_lots(L5, 1e6)
_sp(asof='2026-08-10', tid=T.transition_id('s1', 'F', 'E', 1e6, _plan), postponed=0, done=[], executed_usd=1.0,
               order_ids=[], snapshot={'ZN': 2, 'CBU0': 0}, log=[], opened=True)
# RESUME БЕЗ ФАЙЛА ПРОГРЕССА — О-5, А НЕ СТАРТ С НУЛЯ (тридцать девятый круг, №1).
# Прежде отсутствие state_path молча давало СВЕЖЕЕ состояние со snapshot уже промежуточных
# позиций: продолжение видело нулевой прогресс и исполняло план ЗАНОВО, вплоть до short
# источника, а финальная сверка считала дельту от того же ложного snapshot.
_cl(); _sig('E', grant=98565.0)
_seed_books_from({'ZN': 2, 'CBU0': 0})
try: os.unlink(SP)
except FileNotFoundError: pass
_inc_np = ''
try:
    T.execute(_B(netpos={'ZN': 1, 'CBU0': 0}, seed=False), SP, 1e6, L5,
              signal_id='s1', resume=True, **KW)
except T.Incident as _ex: _inc_np = str(_ex)
chk('Исполнитель: resume без файла прогресса = О-5, план НЕ исполняется заново (39-й, №1)',
    'файла прогресса нет' in _inc_np)
# СОСТОЯНИЕ ВОССТАНАВЛИВАЕТСЯ: следующие стенды resume опираются на него, а этот его удалил.
# Без восстановления они молча начали бы проверять НЕ СВОЙ отказ — ровно тот класс, который
# круг и ищет (стенд проверяет не то, что заявляет).
_sp(asof='2026-08-10', tid=T.transition_id('s1', 'F', 'E', 1e6, _plan), postponed=0, done=[],
    executed_usd=1.0, order_ids=[], snapshot={'ZN': 2, 'CBU0': 0}, log=[], opened=True)

# КНИГА ОСТАЁТСЯ ИСХОДНОЙ, СЧЁТ — ПРОМЕЖУТОЧНЫЙ (тридцать восьмой круг, №1). План требует
# продать 2 ZN, один лот уже исполнен: у брокера ZN=1, а книга Ф по-прежнему несёт 2. Это
# ШТАТНОЕ состояние прерванного перехода, и именно в нём resume обязан работать. Прежде
# фикстура пересевала книгу из позиций САМОГО брокера, книга становилась ZN=1, и
# безусловная сверка предполёта проходила тривиально — дефект «resume недостижим» не был
# наблюдаем ничем. seed=False убирает эту подпорку.
_seed_books_from({'ZN': 2, 'CBU0': 0})
b3 = _B(netpos={'ZN': 1, 'CBU0': 0}, preview=False, seed=False)
r3 = T.execute(b3, SP, 1e6, L5, signal_id='s1', resume=True, **KW)
chk('Исполнитель: margin preview РАНЬШЕ восстановительных ордеров', r3['status'] == 'POSTPONED' and len(b3.calls) == 0)
chk('Исполнитель: resume частично исполненного перехода ДОСТИЖИМ (38-й, №1)',
    r3.get('status') == 'POSTPONED')
_seed_books_from({'ZN': 2, 'CBU0': 0})
r3b = T.execute(_B(netpos={'ZN': 1, 'CBU0': 0}, seed=False), SP, 1e6, L5, signal_id='s1', resume=True, **KW)
chk('Исполнитель: восстановление после preview завершается', r3b['status'] == 'COMPLETE')
# ПАРА К ИСПРАВЛЕННЫМ ФИКСТУРАМ (тридцать первый круг, №16): выше resume прошёл на
# ДЕЙСТВИТЕЛЬНОМ состоянии — теперь то же состояние с подделанным прогрессом обязано
# отказать. Без этой пары «отказ по digest» снова доказывался бы отсутствием digest.
_cl(); _sig('E', grant=98565.0)
_plan = T.plan_lots(L5, 1e6)
_st_ok = _sp(asof='2026-08-10', tid=T.transition_id('s1', 'F', 'E', 1e6, _plan), postponed=0,
             done=[], executed_usd=1.0, order_ids=[], snapshot={'ZN': 2, 'CBU0': 0},
             log=[], opened=True)
_st_bad = dict(_st_ok, done=['BOND'], executed_usd=98560.0)   # digest СТАРЫЙ: правка «на месте»
json.dump(_st_bad, open(SP, 'w'))
_bdg = _B(netpos={'ZN': 1, 'CBU0': 0}); inc = False
try: T.execute(_bdg, SP, 1e6, L5, signal_id='s1', resume=True, **KW)
except T.Incident as _ex: inc = 'ПОВРЕЖДЕНО' in str(_ex)
chk('Исполнитель: подделанный прогресс resume отвергается по digest (не по его отсутствию)',
    inc and len(_bdg.calls) == 0)
_sp_no = dict(_st_ok); _sp_no.pop('digest')
json.dump(_sp_no, open(SP, 'w'))
_bdg2 = _B(netpos={'ZN': 1, 'CBU0': 0}); inc = False
try: T.execute(_bdg2, SP, 1e6, L5, signal_id='s1', resume=True, **KW)
except T.Incident as _ex: inc = 'без digest' in str(_ex)
chk('Исполнитель: состояние перехода БЕЗ digest отвергается', inc and len(_bdg2.calls) == 0)
# ТРИДЦАТЬ ПЕРВЫЙ КРУГ, №12: метки незавершённой передачи ставятся ДО журнального OPEN
# (иначе смерть в зазоре оставляет TRANSITION_OPEN без барьера, который читает дневной
# контур), но ЧИСТЫЙ отказ обязан убирать их за собой — ни одной заявки не подано.
_cl(); _sig('E', grant=98565.0)
# ЧУЖОЙ ОТКРЫТЫЙ ПЕРЕХОД — НЕ «ЧИСТЫЙ ОТКАЗ» (тридцать девятый круг, №7). Этот стенд
# подсовывал в журнал TRANSITION_OPEN с ЧУЖИМ tid и требовал, чтобы метки НЕ остались, —
# то есть закреплял как норму снятие ЧУЖИХ барьеров при живом открытом переходе. Старый
# OPEN не доказывает отсутствие прошлых заявок: это О-5. Проверяем два разных случая.
_cl(); _sig('E', grant=98565.0)
M.append_event(JX, '2026-08-10', 'TRANSITION_OPEN', 'E|s1|ЧУЖОЙ-tid')
import state as _STho
_hodir = _STho.lock_dir()
_hoalien = _hodir / 'handover-inflight-E.txt'
_hoalien.write_text('чужая метка незавершённого перехода\n', encoding='utf-8')
_bho = _B(netpos={'ZN': 1, 'CBU0': 0}); inc = False
try: T.execute(_bho, SP, 1e6, L1, signal_id='s1', **KW)
except T.Incident as _ex: inc = 'открыт ДРУГОЙ переход' in str(_ex)
chk('Исполнитель: ЧУЖОЙ открытый переход = О-5 до первой заявки, чужие метки целы (39-й, №7)',
    inc and len(_bho.calls) == 0 and _hoalien.exists())
try: _hoalien.unlink()
except FileNotFoundError: pass

# А вот СВОЙ отказ открытия (журнал не даёт разрешения, чужого OPEN нет) обязан убирать
# метки за собой — иначе оставленный барьер запер бы торговлю и замыкание навсегда.
# ЗОНД ОБЯЗАН ДОХОДИТЬ ДО ПРОВЕРЯЕМОЙ ВЕТКИ (сороковой круг, №6). Здесь стоял _sig('F'):
# та фикстура сперва ЗАВЕРШАЕТ переход в Е, из-за чего действующий маршрут журнала
# становится E, и execute(from_route='F') падал раньше — «from_route=F не совпадает с
# маршрутом журнала E». Ни _mark_handover, ни hook('open'), ни _drop_handover не
# исполнялись: утверждение проверяло, что уже отсутствующие метки остались отсутствующими.
# Ровно недостижимый зелёный зонд — тот самый класс, который я ищу у себя вторым проходом.
# Берём маршрут E и сигнал в E, но БЕЗ одобрения заказчика: hook('open') откажет по
# существу, дойдя до самой ветки.
# ДОХОДИМ ДО САМОЙ ВЕТКИ, А НЕ ДО СОСЕДНЕЙ (сороковой круг, №6, вторая попытка).
# approve=False останавливает исполнение ещё на проверке OWNER_APPROVE — выше меток; зонд
# достижимости показал `_drop_handover вызван 0 раз`, то есть стенд снова подтверждал, что
# НЕсозданные метки остались несозданными. Проверяемая ветка — «журнал ОТВЕРГ открытие уже
# после того, как метки поставлены», поэтому отказ моделируется там, где он и происходит:
# в confirm_transition(kind='open'). Подмена названа вслух и снимается сразу.
_cl(); _sig('E', grant=98565.0)
_hobefore = sorted(x.name for x in _hodir.glob('handover-inflight-*.txt'))
_bho2 = _B(netpos={'ZN': 1, 'CBU0': 0}); inc2 = False
_ct_orig = M.confirm_transition
def _ct_deny_open(journal, state_path, asof, target, kind='complete', tid='', sid=''):
    if kind == 'open':
        return False
    return _ct_orig(journal, state_path, asof, target, kind=kind, tid=tid, sid=sid)
M.confirm_transition = _ct_deny_open
try: T.execute(_bho2, SP, 1e6, L1, signal_id='s1', **KW)
except T.Incident as _ex: inc2 = 'отклонил открытие' in str(_ex)
finally: M.confirm_transition = _ct_orig
_hoafter = sorted(x.name for x in _hodir.glob('handover-inflight-*.txt'))
chk('Исполнитель: СВОЙ чистый отказ открытия не оставляет меток незавершённой передачи',
    inc2 and len(_bho2.calls) == 0 and _hoafter == _hobefore)
# ТРИДЦАТЬ ПЕРВЫЙ КРУГ, №13: пин журнала МР защищал ПУТЬ и ЛИЧНОСТЬ файла, но не
# СОДЕРЖИМОЕ. open(path,'w') сохраняет inode, и синтаксически правильная дописанная строка
# OWNER_APPROVE разрешала полный переход маршрута — одной валидной правкой CSV. Обширные
# тесты symlink/hardlink/inode доказывали личность файла, а не неизменность событий.
_jdg = _JP('mr_digest.csv')
open(_jdg, 'w').write('asof,event,detail\n')
M.test_configure(_jdg)
M.append_event(_jdg, '2026-08-08', 'SWITCH_SIGNAL', 'E|s1')
_read_ok = len(M.journal_rows(_jdg)) == 1
open(_jdg, 'a').write('2026-08-08,OWNER_APPROVE,E|s1\n')      # правка «на месте»
_caught = ''
try: M.journal_rows(_jdg)
except M.JournalCorrupt as _ex: _caught = str(_ex)
chk('МР: правка нормативного журнала «на месте» ловится по digest',
    _read_ok and 'мимо append_event' in _caught)
# ПАРА К ОТКАЗУ: правка отменена, журнал заверен заново — законная запись обязана пройти.
# Стенд, проверяющий только ОТКАЗ, пропустил бы поломку успешного пути (класс №11 30-го).
open(_jdg, 'w').write('asof,event,detail\n2026-08-08,SWITCH_SIGNAL,E|s1\n')
M.test_configure(_jdg)
M.append_event(_jdg, '2026-08-09', 'SWITCH_SIGNAL', 'F|s2')
chk('МР: законная запись заверяется и читается', len(M.journal_rows(_jdg)) == 2)
# ТРИДЦАТЬ ТРЕТИЙ КРУГ, №14: ПРОИЗВОДСТВЕННЫЙ вызов перехода — БЕЗ калитки ADDFUT_ASOF_
# OVERRIDE. Все полные переходы выше идут с ней, а отказной стенд «переход задним числом»
# передаёт object() вместо брокера и /nonexistent-пути: он гарантированно падает на
# следующем, постороннем барьере. Значит регрессия «любой вызов без калитки отвергается»
# осталась бы незамеченной, а маршрут не сменился бы перед роллом. Проверяем ровно одно и
# честно: сегодняшний asof НЕ отвергается воротами даты. Дальше вызов может отказать по
# окну или календарю — это другие ворота, и они названы.
_cl(); _sig('E', grant=98565.0)
import feed as _FDsc
_today_sc = _FDsc.exchange_today().strftime('%Y-%m-%d')
_keep_ov = _os1.environ.pop('ADDFUT_ASOF_OVERRIDE', None)
_asof_blocked = ''
try:
    _KWn = dict(KW); _KWn['asof'] = _today_sc
    T.execute(_B(netpos={'ZN': 1, 'CBU0': 0}), SP, 1e6, L1, signal_id='s1', **_KWn)
except Exception as _exn:
    _asof_blocked = str(_exn)
finally:
    _os1.environ.pop('ADDFUT_ASOF_OVERRIDE', None)
    _os1.environ.update({'ADDFUT_ASOF_OVERRIDE': _keep_ov} if _keep_ov else {})
chk('Исполнитель: производственный вызов (без калитки asof) не отвергается воротами даты',
    'биржевым сегодня' not in _asof_blocked, _asof_blocked[:80] or 'прошёл до конца')
_cl(); _sig('F', sid='s1')
b9 = _B(netpos={'CBU0': 197120, 'ZN': 0}, nlv=10e6)
r9 = T.execute(b9, SP, 10e6, {'BOND': dict(src=[('CBU0', 197120, 5.0)], dst=('ZN', 98560.0, 'FUT'))},
               signal_id='s1', from_route='E', to_route='F', **KW)
chk('Исполнитель: полный Е→Ф на 10 млн в пределах owner-cap', r9['status'] == 'COMPLETE' and b9.maxu <= 100_000.0 + 1e-6)
_cl(); _sig('E', grant=38780.0)
b4 = _B(netpos={'MES': 29, 'CSPX': 0})
r4 = T.execute(b4, SP, 1e6, {'EQ': dict(src=[('MES', 29, 38428.0)], dst=('CSPX', 700.0, 'ETF'))}, signal_id='s1', **KW)
_cspx = b4.np['CSPX']
os.remove(SP)
M.append_event(JX, '2026-08-10', 'SWITCH_SIGNAL', 'F|s2')
M.append_event(JX, '2026-08-10', 'OWNER_APPROVE', 'F|s2'); M.grant_granularity(JX, SX, '2026-08-10', 's2', 38780.0)
b4b = _B(netpos={'CSPX': _cspx, 'MES': 0}, nlv=10e6)
# Обратный переход идёт на капитале ВЫШЕ порога §8. Прежде тест выполнял E->F при 1 млн и
# засчитывал COMPLETE как успех, то есть канонизировал прямое нарушение §8 (найдено внешней
# рецензией). Ниже порога тот же вызов обязан подниматься инцидентом — см. следующую проверку.
r4b = T.execute(b4b, SP, 10e6, {'EQ': dict(src=[('CSPX', _cspx, 700.0)], dst=('MES', 38428.0, 'FUT'))},
                signal_id='s2', from_route='E', to_route='F', **KW)
chk('Исполнитель: round-trip автоматичен (хвост кванта в пределах лимита)',
    r4['status'] == 'COMPLETE' and r4b['status'] == 'COMPLETE' and b4b.np['MES'] == 29 and b4b.np['CSPX'] == 0
    and M.derive_state(JX, date(2026, 8, 11))[0] == 'F')
_blocked = False
try:
    _bthr = _B(netpos={'CSPX': 1, 'MES': 0}, nlv=2_999_999.0)
    T.execute(_bthr, _JP('sp_thr.json'), 2_999_999.0,
              {'EQ': dict(src=[('CSPX', 1, 700.0)], dst=('MES', 38428.0, 'FUT'))},
              signal_id='s2', from_route='E', to_route='F', **KW)
except T.Incident as _ex:
    _blocked = 'порога' in str(_ex)
chk('Исполнитель: переход в Ф ниже порога §8 отклоняется инцидентом', _blocked)
import subprocess, sys
import shutil as _sh
_CLIJ = _JP('cli_journal.csv')
_sh.copy('mr_journal.csv', _CLIJ)
_CLIHOME = os.path.join(_TD, 'cli_home'); os.makedirs(_CLIHOME, exist_ok=True)
_CLIENV = dict(os.environ); _CLIENV['HOME'] = _CLIHOME       # боевой ~/.add-fut не затрагивается
_REALDIR_BEFORE = None   # боевой каталог установки самопроверкой не читается и не пишется
_rno = subprocess.run(['python3', 'mr_engine.py', '--journal', _CLIJ, '--asof', '2026-08-08',
                       '--nlv', '10000000', '--dry-run'], capture_output=True, text=True, env=_CLIENV)
chk('МР: CLI без конфигурации развёртывания отказывается работать', _rno.returncode != 0)
_rpv = subprocess.run(['python3', 'mr_engine.py', '--provision-journal', _CLIJ],
                      capture_output=True, text=True, env=_CLIENV)
chk('МР: провизия командой --provision-journal (отдельная команда, без --asof/--nlv)',
    _rpv.returncode == 0 and os.path.exists(os.path.join(_CLIHOME, '.add-fut',
                                                         'deploy.' + M.STRATEGY_ID + '.' + M.ACCOUNT_ID)))
_r = subprocess.run(['python3', 'mr_engine.py', '--journal', _CLIJ, '--asof', '2026-08-08',
                     '--nlv', '10000000', '--dry-run'], capture_output=True, text=True, env=_CLIENV)
chk('МР: документированный CLI жив (DECISION= в выводе)', 'DECISION=' in _r.stdout and _r.returncode == 0)
chk('Самопроверка: боевой каталог установки не используется (CLI-тесты в изолированном HOME)',
    os.environ.get('HOME') != _CLIHOME and M._STATE['dir'].startswith(_TD))
open(_JP('y2j.csv'), 'w').write('asof,event,detail\n2026-08-08,SWITCH_SIGNAL,E|s1|EXTRA\n')
M.test_configure(_JP('y2j.csv'))
try:
    M.derive_state(_JP('y2j.csv'), date(2026, 8, 8)); chk('МР: лишний компонент detail = порча', False)
except M.JournalCorrupt:
    chk('МР: лишний компонент detail = порча', True)
_cl(); _sig('E'); M.grant_granularity(JX, SX, '2026-08-10', 's1', 38428.0)
_L = {'EQ': dict(src=[('MES', 29, 38428.0)], dst=('CSPX', 700.0, 'ETF'))}
inc = False
try: T.execute(_B(netpos={'MES': 29}), SP, 1e6, _L, signal_id='s1', **KW)
except T.Incident: inc = True
chk('Исполнитель: грант без зазора округления недостаточен = инцидент', inc)
_cl(); _sig('E'); M.grant_granularity(JX, SX, '2026-08-10', 's1', 38780.0)
_b = _B(netpos={'MES': 29, 'CSPX': 0})
_r3 = T.execute(_b, SP, 1e6, _L, signal_id='s1', **KW)
chk('Исполнитель: абсолютный грант держится строго', _r3['status'] == 'COMPLETE' and _b.maxu <= 38780.0 + 1e-6)
class _BX(_B):
    def sell_units(s, i, u):
        oid, f = super().sell_units(i, u)
        raise RuntimeError('TWS disconnect')
_cl(); _sig('E'); M.grant_granularity(JX, SX, '2026-08-10', 's1', 98565.0)
inc = False
try: T.execute(_BX(netpos={'ZN': 1, 'CBU0': 0}), SP, 1e6, L1, signal_id='s1', **KW)
except T.Incident: inc = True
chk('Исполнитель: исключение адаптера после продажи = инцидент + MIXED',
    inc and M.derive_state(JX, date(2026, 8, 11))[2])
_cl(); _sig('E'); M.grant_granularity(JX, SX, '2026-08-10', 's1', 98565.0)
inc = False
for _ in range(3):
    try: T.execute(_B(preview=False), SP, 1e6, L1, signal_id='s1', **KW)
    except T.Incident: inc = True
chk('Исполнитель: третий отказ preview журналируется (ABORT до OPEN, pending снят)',
    inc and 'TRANSITION_ABORT' in open(JX).read() and M.derive_state(JX, date(2026, 8, 11))[1] is None)
_cl(); _sig('E')
inc = False
try: T.execute(_B(), SP, float('nan'), L1, signal_id='s1', **KW)
except T.Incident: inc = True
chk('Исполнитель: capital=NaN = инцидент до OPEN', inc and 'TRANSITION_OPEN' not in open(JX).read())
ETFc = lambda n, p: (n, p, 'ETF'); FUTc = lambda n, p: (n, p, 'FUT')
_cl(); _sig('F')
# ЧЕСТНЫЙ СЧЁТЧИК (семнадцатый круг, №10): полный Е→Ф на 10 млн дробится лимитом §8б на
# ~1200 заявок и В ОДИН ДЕНЬ под Priority Customer не влезает — прежняя формула «2 на
# лот» это скрывала. Отказ ДО первой заявки — правильный исход, а не поломка.
_LEF = {'EQ': dict(src=[('CSPX', 54896, 700.0)], dst=FUTc('MES', 38428.0)),
        'BOND': dict(src=[('CBU0', 1971200, 5.0)], dst=FUTc('ZN', 98560.0))}
_bz = _B(netpos={'CSPX': 54896, 'CBU0': 1971200, 'MES': 0, 'ZN': 0}, nlv=10e6)
inc = False
try: T.execute(_bz, SP, 10e6, _LEF, signal_id='s1', from_route='E', to_route='F', **KW)
except T.Incident as _ex: inc = 'Priority Customer' in str(_ex)
chk('17-й, №10: полный Е→Ф на 10 млн честно упирается в дневной лимит заявок ДО ордеров',
    inc and len(_bz.calls) == 0)
_cl(); _sig('F')
_LEF2 = {'EQ': dict(src=[('CSPX', 6000, 700.0)], dst=FUTc('MES', 38428.0)),
         'BOND': dict(src=[('CBU0', 250000, 5.0)], dst=FUTc('ZN', 98560.0))}
_bz = _B(netpos={'CSPX': 6000, 'CBU0': 250000, 'MES': 0, 'ZN': 0}, nlv=10e6)
_rz = T.execute(_bz, SP, 10e6, _LEF2, signal_id='s1', from_route='E', to_route='F', **KW)
chk('Исполнитель: порядок ног канонический — EQ→BOND на входе, лимит держится',
    _rz['status'] == 'COMPLETE' and _bz.maxu <= 100_000.0 + 1e-6 and _bz.calls[0][1] == 'CBU0')
_cl(); _sig('E', grant=98565.0)
_plz = T.plan_lots(L1, 1e6)
_sp(asof='2026-08-10', tid=T.transition_id('s1', 'F', 'E', 1e6, _plz), postponed=1, done=[], executed_usd=0.0,
               order_ids=[], snapshot={'ZN': 1, 'CBU0': 0}, log=[], opened=True)
_bz2 = _B(netpos={'ZN': 1, 'CBU0': 0}); inc = False
try: T.execute(_bz2, SP, 1e6, L1, signal_id='s1', **KW)
except T.Incident: inc = True
chk('Исполнитель: открытый переход без resume = инцидент до заявок', inc and len(_bz2.calls) == 0)
open(_JP('z3j.csv'), 'w').write('asof,event,detail\n2026-08-08,SWITCH_SIGNAL,E|s1\n')
M.test_configure(_JP('z3j.csv'))
chk('МР: pre-OPEN ABORT отклоняется всегда (строгая привязка к OPEN, ред. 32)',
    M.confirm_transition(_JP('z3j.csv'), _JP('z3s.csv'), '2026-08-08', 'E', kind='abort', tid='', sid='s1') is False
    and M.confirm_transition(_JP('z3j.csv'), _JP('z3s.csv'), '2026-08-08', 'E', kind='abort', tid='T1', sid='s1') is False)
open(_JP('z4j.csv'), 'w').write('asof,event,detail,extra\n2026-08-08,SWITCH_SIGNAL,E|s1,x\n')
try:
    M.test_configure(_JP('z4j.csv'))
    M.derive_state(_JP('z4j.csv'), date(2026, 8, 8)); chk('МР: объявленный лишний столбец = порча', False)
except M.JournalCorrupt:
    chk('МР: объявленный лишний столбец = порча (отсекается уже на провизии схемы)', True)
for _npos in (0, 3):
    _cl(); _sig('E', grant=38780.0)
    _b5 = _B(netpos={'MES': _npos, 'CSPX': 0}); inc = False
    try: T.execute(_b5, SP, 1e6, {'EQ': dict(src=[('MES', 2, 38428.0)], dst=ETFc('CSPX', 700.0))}, signal_id='s1', **KW)
    except T.Incident: inc = True
    chk(f'Исполнитель: книга MES={_npos} против плана 2 = инцидент до заявок', inc and len(_b5.calls) == 0)
_cl(); _sig('E'); inc = False
try: T.execute(_B(netpos={'CSPX': 110}), SP, 1e6, {'EQ': dict(src=[('CSPX', 110, 700.0)], dst=FUTc('MES', 38428.0))},
               signal_id='s1', to_route='E', **KW)
except T.Incident: inc = True
chk('Исполнитель: класс цели привязан к маршруту (FUT при SWITCH_E = инцидент)', inc)
# --- Контртесты вердикта ред. 16 ---
_cl(); open(JX, 'w').write('asof,event,detail\n2026-08-10,SWITCH_SIGNAL,E|s1\n2026-08-10,OWNER_APPROVE,E|s1\n'); M.test_configure(JX)   # фикстура провизионируется (31-й, №13)
inc = False
try: T.execute(_B(netpos={'CSPX': 110}), SP, 1e6, {'EQ': dict(src=[('CSPX', 110, 700.0)], dst=('MES', 38428.0, 'ETF'))},
               signal_id='s1', from_route='E', to_route='E', **KW)
except T.Incident: inc = True
chk('Исполнитель: ложная метка класса против реестра = инцидент', inc)
_cl(); _sig('E', grant=38780.0)
inc = False; _bA = _B(netpos={'MES': 2, 'ZN': 1, 'CSPX': 0})
try: T.execute(_bA, SP, 1e6, {'EQ': dict(src=[('MES', 2, 38428.0)], dst=ETFc('CSPX', 700.0))}, signal_id='s1', **KW)
except T.Incident: inc = True
chk('Исполнитель: позиция класса источника вне плана (ZN) = инцидент до заявок', inc and len(_bA.calls) == 0)
_cl(); open(JX, 'w').write('asof,event,detail\n2026-08-10,SWITCH_SIGNAL,E|s1\n2026-08-10,OWNER_APPROVE,E|s1\n'); M.test_configure(JX)   # фикстура провизионируется (31-й, №13)
inc = False
try: T.execute(_B(netpos={'CBU0': 19712}), SP, 1e6, {'BOND': dict(src=[('CBU0', 19712, 5.0)], dst=FUTc('ZN', 98560.0))},
               signal_id='s1', from_route='E', to_route='F', **KW)
except T.Incident: inc = True
chk('Исполнитель: from_route не совпадает с маршрутом журнала = инцидент', inc)
import fcntl as _f
_cl(); _sig('E', grant=98565.0)
_plL = T.plan_lots(L1, 1e6); _tidL = T.transition_id('s1', 'F', 'E', 1e6, _plL)
_lf = open(JX, 'r+'); _f.flock(_lf, _f.LOCK_EX)
inc = False; _bL = _B(netpos={'ZN': 1, 'CBU0': 0})
try: T.execute(_bL, SP, 1e6, L1, signal_id='s1', **KW)
except T.Incident: inc = True
_f.flock(_lf, _f.LOCK_UN); _lf.close()
chk('Исполнитель: журнал заблокирован извне = инцидент, заявок ноль', inc and len(_bL.calls) == 0)
_cl(); open(JX, 'w').write('asof,event,detail\n2026-08-10,SWITCH_SIGNAL,E|s1\n2026-08-10,OWNER_APPROVE,E|s1\n'); M.test_configure(JX)   # фикстура провизионируется (31-й, №13)
chk('МР: произвольный pre-OPEN ABORT отклоняется (строгая привязка к OPEN)',
    M.confirm_transition(JX, SX, '2026-08-10', 'E', kind='abort', tid='RANDOM', sid='s1') is False)
M.grant_granularity(JX, SX, '2026-08-10', 's1', 98565.0)
inc = False
for _ in range(3):
    try: T.execute(_B(preview=False), SP, 1e6, L1, signal_id='s1', **KW)
    except T.Incident: inc = True
chk('Исполнитель: третий отказ preview = честная пара OPEN+ABORT, pending снят',
    inc and 'TRANSITION_OPEN' in open(JX).read() and 'TRANSITION_ABORT' in open(JX).read()
    and M.derive_state(JX, date(2026, 8, 11))[1] is None)
chk('Реестр: пять инструментов с sec_type/pair_group',
    len(T.load_registry('instruments.csv')) == 5 and T.load_registry('instruments.csv')['MES']['sec_type'] == 'FUT')

# --- Ред. 31: подпись заказчика как условие открытия перехода ---
_cl(); _sig('E', grant=98565.0, approve=False)          # сигнал есть, одобрения нет
_bNA = _B(netpos={'ZN': 1, 'CBU0': 0}); inc = False
try: T.execute(_bNA, SP, 1e6, L1, signal_id='s1', **KW)
except T.Incident as _ex: inc = 'OWNER_APPROVE' in str(_ex)
chk('Ред.31: SWITCH_SIGNAL без OWNER_APPROVE = отказ, заявок ноль и журнал не тронут',
    inc and len(_bNA.calls) == 0 and 'TRANSITION_OPEN' not in open(JX).read())

_cl(); _sig('E', grant=98565.0, approve=False)          # одобрение на ЧУЖУЮ цель не годится
M.append_event(JX, '2026-08-10', 'OWNER_APPROVE', 'F|s1')
inc = False
try: T.execute(_B(netpos={'ZN': 1, 'CBU0': 0}), SP, 1e6, L1, signal_id='s1', **KW)
except T.Incident: inc = True
chk('Ред.31: одобрение на другую цель не разрешает переход', inc)

_cl(); _sig('E', grant=98565.0, approve=False)          # одобрение на ЧУЖОЙ сигнал не годится
M.append_event(JX, '2026-08-10', 'OWNER_APPROVE', 'E|sОТHER')
inc = False
try: T.execute(_B(netpos={'ZN': 1, 'CBU0': 0}), SP, 1e6, L1, signal_id='s1', **KW)
except T.Incident: inc = True
chk('Ред.31: одобрение на другой sid не переносится', inc)

_cl(); _sig('E', grant=98565.0)                          # штатный путь с подписью — переход открывается
_bOK = _B(netpos={'ZN': 1, 'CBU0': 0}); _rOK = T.execute(_bOK, SP, 1e6, L1, signal_id='s1', **KW)
chk('Ред.31: с OWNER_APPROVE переход открывается штатно',
    'TRANSITION_OPEN' in open(JX).read() and M.find_approval(JX, '2026-08-10', 's1', 'E') is True)

chk('Ред.31: OWNER_APPROVE в словаре событий и не ломает разбор журнала',
    'OWNER_APPROVE' in M.KNOWN_EVENTS
    and M.derive_state(JX, date(2026, 8, 11))[3] == [])

# --- Ред. 32: маржа и число заявок по отображённой книге ---
_reg = T.load_registry('instruments.csv')
import sim_v164 as _S164
chk('Ред.32: map_mes исполнителя совпадает с sim_v164 на всей сетке 0..999',
    all(T.map_mes(n) == _S164.map_mes(n) for n in range(1000)))
chk('Ред.32: отображение книги — целые ES плюс остаток MES',
    T.mapped_book('MES', 264) == {'ES': 26, 'MES': 4} and T.mapped_book('MES', 20) == {'ES': 2}
    and T.mapped_book('ZN', 101) == {'ZN': 101})

# СВЕРКА С §7: 26 ES + 101 ZN на 10 млн — порядок 1,12 млн, ~11,2% капитала, запас ~8,9x.
# ЛИТЕРАЛ 1_122_960 УБРАН (15.08): он введён после ДЕСЯТОГО круга по маржам того времени, а
# при разборе 20-21 кругов фикстура замера получила ФАКТИЧЕСКИЕ маржи (ES 34 910, ZN 2 157)
# — ожидание осталось от старой таблицы и разошлось на 2 557 долларов. Пин на литерал делал
# утверждение заложником биржевых требований, которые меняются сами по себе. Проверяется то,
# что действительно обязано держаться: СОСТАВ суммы (маржа книги = сумма по инструментам
# из замера, а не выдуманное число) и попадание в полосу §7 по доле капитала и запасу.
_mES, _mZN = _json1.loads(open(_MFIX).read())['ES']['init'], _json1.loads(open(_MFIX).read())['ZN']['init']
_mF = T.book_margin({'ES': 26, 'ZN': 101}, _reg)
chk('Ред.32: маржа книги Ф — сумма по замеру и в полосе §7 (26 ES + 101 ZN, ~11,2%, запас ~8,9x)',
    abs(_mF - (26 * _mES + 101 * _mZN)) < 1.0
    and 0.110 <= _mF / 10e6 <= 0.115 and 8.6 <= 10e6 / _mF <= 9.1)
# сверка с §8: маршрут Е при номинале 2,0x = 25% позиций, запас 2,00x
_mE = T.book_margin({'CSPX': 10_000, 'CBU0': 200_000}, _reg, {'CSPX': 1000.0, 'CBU0': 50.0})
chk('Ред.32: маржа книги Е = 25% позиций, запас 2,00x при номинале 2,0x',
    abs(_mE - 5_000_000.0) < 1.0 and abs(10e6/_mE - 2.00) < 1e-9)

# запас ниже О-3-Е -> отказ ДО первой заявки
_legsE = {'BOND': dict(src=[('ZN', 40, 98560.0)], dst=('CBU0', 5.0, 'ETF'))}
_cl(); _sig('E', grant=98570.0)
_bLow = _B(netpos={'ZN': 40, 'CBU0': 0}); inc = False
try: T.execute(_bLow, SP, 1e6, _legsE, signal_id='s1', **KW)
except T.Incident as _ex: inc = 'О-3-Е' in str(_ex)
chk('Ред.32: запас целевой книги ниже О-3-Е = отказ, заявок ноль, TRANSITION_OPEN не записан',
    inc and len(_bLow.calls) == 0 and 'TRANSITION_OPEN' not in open(JX).read())

# лимит заявок Priority Customer — СЧИТАЕТСЯ ПРОГОНОМ ПЛАНА (семнадцатый круг, №10):
# формула «2 на лот» занижала на порядок — лимит §8б дробит лот на десятки пар.
_Lc = {'BOND': dict(src=[('ZN', 7, 98560.0)], dst=('CBU0', 5.0, 'ETF'))}
_pl7 = T.plan_lots(_Lc, 10e6)
_n7, _m7 = T.plan_orders(_pl7, _Lc, T.unpaired_limit(_Lc, 10e6))
chk('Ред.32: просторный лимит — пара заявок на лот, потолок с компенсациями',
    _n7 == 2*len(_pl7) and _m7 == _n7 + len(_pl7))
_Lt = {'EQ': dict(src=[('CBU0', 20000, 5.0)], dst=('CSPX', 700.0, 'ETF'))}
_plt = T.plan_lots(_Lt, 1e6)
_nt, _mt = T.plan_orders(_plt, _Lt, T.unpaired_limit(_Lt, 1e6))
chk('17-й, №10: тесный лимит дробит лот — заявок БОЛЬШЕ, чем 2 на лот',
    _nt > 2*len(_plt))
_Lp = {'EQ': dict(src=[('CBU0', 400000, 5.0)], dst=('CSPX', 700.0, 'ETF'))}
inc = False
try: T.preflight_margin_orders(_Lp, T.plan_lots(_Lp, 1e6), 1e6,
                               {'CSPX': dict(sec_type='STK')}, 'E')
except T.Incident as _ex: inc = 'Priority Customer' in str(_ex)
chk('Ред.32: превышение лимита 390 заявок в день = отказ', inc)

# штатный путь: сводка попадает в состояние исполнителя
_cl(); _sig('E', grant=98565.0)
T.execute(_B(netpos={'ZN': 1, 'CBU0': 0}), SP, 1e6, L1, signal_id='s1', **KW)
_pf = json.load(open(SP)).get('preflight', {})
chk('Ред.32: сводка маржи и заявок сохраняется в состоянии перехода',
    _pf.get('cushion', 0) > T.CUSHION_E and _pf.get('orders', 0) > 0
    and _pf.get('margin_usd', 0) > 0)
# --- Контртесты вердикта ред. 17 ---
import hashlib as _hl
open(_JP('fakereg.csv'), 'w').write('instrument,sec_type,pair_group,exchange,currency,con_id\nMES,ETF,EQ,CME,USD,x\nCSPX,FUT,EQ,LSEETF,USD,x\n')
_cl(); open(JX, 'w').write('asof,event,detail\n2026-08-10,SWITCH_SIGNAL,E|s1\n2026-08-10,OWNER_APPROVE,E|s1\n'); M.test_configure(JX)   # фикстура провизионируется (31-й, №13)
inc = False
try: T.execute(_B(netpos={'CSPX': 110}), SP, 1e6, {'EQ': dict(src=[('CSPX', 110, 700.0)], dst=('MES', 38428.0, 'ETF'))},
               signal_id='s1', from_route='E', to_route='E', registry=_JP('fakereg.csv'), **KW)
except T.Incident: inc = True
chk('Исполнитель: подменный реестр (SHA-пиновка) = инцидент', inc)
chk('Реестр: SHA файла совпадает с пинованным в коде',
    _hl.sha256(open('instruments.csv', 'rb').read()).hexdigest() == T.REGISTRY_SHA256)
_cl(); _sig('E', grant=38780.0)
for _nm, _np in [('неизвестный NQ в книге', {'MES': 2, 'NQ': 1, 'CSPX': 0}),
                 ('предсуществующая цель CSPX', {'MES': 2, 'CSPX': 50})]:
    _bB = _B(netpos=dict(_np)); inc = False
    try: T.execute(_bB, SP, 1e6, {'EQ': dict(src=[('MES', 2, 38428.0)], dst=ETFc('CSPX', 700.0))}, signal_id='s1', **KW)
    except T.Incident: inc = True
    chk(f'Исполнитель: {_nm} = инцидент до заявок', inc and len(_bB.calls) == 0)
inc = False
try: T.plan_lots({'A': dict(src=[('MES', 2, 38428.0)], dst=ETFc('CSPX', 700.0)),
                  'B': dict(src=[('MES', 2, 38428.0)], dst=ETFc('CBU0', 5.0))}, 1e6)
except T.Incident: inc = True
chk('Исполнитель: дублированный источник = инцидент', inc)
inc = False
try: T.plan_lots({'A': dict(src=[('MES', 2, -38428.0)], dst=ETFc('CSPX', 700.0))}, 1e6)
except T.Incident: inc = True
chk('Исполнитель: отрицательная/неконечная цена = инцидент', inc)
_cl(); _sig('E', grant=98565.0)
_plB = T.plan_lots(L1, 1e6); _tidB = T.transition_id('s1', 'F', 'E', 1e6, _plB)
import fcntl as _f2
_lfB = open(JX, 'r+'); _f2.flock(_lfB, _f2.LOCK_EX)
inc = False; _bC = _B(netpos={'ZN': 1, 'CBU0': 0})
try: T.execute(_bC, _JP('sc_other_state.json'), 1e6, L1, signal_id='s1', **KW)
except T.Incident: inc = True
_f2.flock(_lfB, _f2.LOCK_UN); _lfB.close()
chk('Исполнитель: блокировка журнала действует при любом state_path = инцидент до заявок', inc and len(_bC.calls) == 0)
_cl(); _sig('E', grant=98565.0)
try: T.execute(_B(netpos={'ZN': 5, 'CBU0': 0}), SP, 1e6, L1, signal_id='s1', **KW)
except T.Incident: pass
_rB = T.execute(_B(netpos={'ZN': 1, 'CBU0': 0}), SP, 1e6, L1, signal_id='s1', **KW)
chk('Исполнитель: finally освобождает lease, свежий заход пересоздаёт снапшот', _rB['status'] == 'COMPLETE')
# --- Контртесты вердикта ред. 18 (TOCTOU) ---
import builtins as _bi
_cnt = {'n': 0}; _orig_open = _bi.open
def _counting_open(path, *a, **k):
    if str(path).endswith('instruments.csv'): _cnt['n'] += 1
    return _orig_open(path, *a, **k)
_bi.open = _counting_open
try:
    T.load_registry('instruments.csv')
finally:
    _bi.open = _orig_open
chk('Реестр: хэш и разбор одного body — файл читается РОВНО один раз', _cnt['n'] == 1)
_cl(); _sig('E', grant=98565.0)
_plC = T.plan_lots(L1, 1e6); _tidC = T.transition_id('s1', 'F', 'E', 1e6, _plC)
import fcntl as _f3
_lfC = open(JX, 'r+'); _f3.flock(_lfC, _f3.LOCK_EX)
for _lnm in (_JP('sc_alias_l.csv'), _JP('sc_alias_h.csv')):
    if os.path.lexists(_lnm): os.remove(_lnm)
os.symlink(os.path.abspath(JX), _JP('sc_alias_l.csv'))
os.link(os.path.abspath(JX), _JP('sc_alias_h.csv'))
_bS = _B(netpos={'ZN': 1, 'CBU0': 0}); inc = False
try: T.execute(_bS, SP + 'sl', 1e6, L1, signal_id='s1', journal=_JP('sc_alias_l.csv'), mr_state=SX, asof='2026-08-10')
except T.Incident: inc = True
chk('Исполнитель: symlink-алиас журнала запрещён/делит lease — заявок ноль', inc and len(_bS.calls) == 0)
_bH = _B(netpos={'ZN': 1, 'CBU0': 0}); inc = False
try: T.execute(_bH, SP + 'hl', 1e6, L1, signal_id='s1', journal=_JP('sc_alias_h.csv'), mr_state=SX, asof='2026-08-10')
except T.Incident: inc = True
chk('Исполнитель: hardlink-алиас делит lease (device+inode) — заявок ноль', inc and len(_bH.calls) == 0)
_cwd0 = os.getcwd(); os.chdir(os.path.dirname(os.path.abspath(JX)) or '/tmp')
_bR = _B(netpos={'ZN': 1, 'CBU0': 0}); inc = False
try: T.execute(_bR, SP + 'rl', 1e6, L1, signal_id='s1', journal='./' + os.path.basename(JX), mr_state=SX,
               asof='2026-08-10', registry=os.path.join(_cwd0, 'instruments.csv'))
except T.Incident: inc = True
os.chdir(_cwd0)
chk('Исполнитель: относительный путь делит lease с абсолютным — заявок ноль', inc and len(_bR.calls) == 0)
_f3.flock(_lfC, _f3.LOCK_UN); _lfC.close()
M.test_configure(JX)
_ino0 = os.stat(JX).st_ino
M.append_event(JX, '2026-08-10', 'REVIEW_SIGNAL', 'проба')
chk('Замок: журнал дописывается НА МЕСТЕ, inode (объект блокировки) не меняется',
    os.stat(JX).st_ino == _ino0)
try:
    M.derive_state(_JP('sc_alias_l.csv'), date(2026, 8, 8)); chk('МР: движок отвергает symlink-журнал', False)
except M.JournalCorrupt:
    chk('МР: движок отвергает symlink-журнал', True)
# --- Контртесты вердикта ред. 21 (пин/конкурентность/печать) ---
import threading as _th, io as _io, contextlib as _cl
open(_JP('f1_j.csv'), 'w').write('asof,event,detail\n')
M.test_configure(_JP('f1_j.csv'))
open(_JP('f1_other.csv'), 'w').write('asof,event,detail\n')
inc = False
try: M.configure(_JP('f1_other.csv'))
except M.JournalCorrupt: inc = True
chk('МР: перепиновка в процессе запрещена', inc)
if os.path.lexists(_JP('f1_hard.csv')): os.remove(_JP('f1_hard.csv'))
os.link(_JP('f1_j.csv'), _JP('f1_hard.csv'))
_code = ("import sys; sys.path.insert(0, %r)\n"
         "import mr_engine as M\n"
         "M.set_state_dir_for_tests(%r)\n"
         "try:\n M.configure(%r); print('PASSED')\n"
         "except M.JournalCorrupt: print('REJECTED')\n") % (os.getcwd(), os.path.join(_TD, 'install'), _JP('f1_hard.csv'))
_rp = subprocess.run([sys.executable, '-c', _code], capture_output=True, text=True)
chk('МР: НАСТОЯЩИЙ свежий процесс + hardlink отклоняется конфигурацией развёртывания',
    _rp.stdout.strip() == 'REJECTED')
_clean = _JP('clean_j.csv')
open(_clean, 'w').write('asof,event,detail\n')
_code2 = ("import sys; sys.path.insert(0, %r)\n"
          "import mr_engine as M\n"
          "M.set_state_dir_for_tests(%r)\n"
          "try:\n M.configure(%r); print('PASSED')\n"
          "except M.JournalCorrupt: print('REJECTED')\n") % (os.getcwd(), os.path.join(_TD, 'clean_install'), _clean)
_rc = subprocess.run([sys.executable, '-c', _code2], capture_output=True, text=True)
chk('МР: чистая установка без провизии — configure отказывает (конфигурация не самоназначается)',
    _rc.stdout.strip() == 'REJECTED')
M.configure(_JP('f1_j.csv'))
_res = {'blocked': None, 'done': False}
def _wr():
    M.append_event(_JP('f1_j.csv'), '2026-08-08', 'REVIEW_SIGNAL', 'поток'); _res['done'] = True
with M.hold_strategy_lock(_JP('f1_j.csv')):
    _t = _th.Thread(target=_wr, daemon=True); _t.start(); _t.join(0.5)
    _res['blocked'] = _t.is_alive()
_t.join(3)
chk('МР: чужой поток блокируется при удержании замка и дописывает после', _res['blocked'] and _res['done'])
with M.hold_strategy_lock(_JP('f1_j.csv')):
    _pid = os.fork()
    if _pid == 0:
        try:
            with M.hold_strategy_lock(_JP('f1_j.csv')):
                os._exit(7)
        except RuntimeError:
            os._exit(0)
    _, _st = os.waitpid(_pid, 0)
chk('МР: fork-потомок НЕ наследует владение замком (занято)', os.WEXITSTATUS(_st) == 0)
open(_JP('f3_j.csv'), 'w').write('asof,event,detail\n2026-08-01,SWITCH_SIGNAL,E|s0\n'
    '2026-08-01,TRANSITION_OPEN,E|s0|t0\n2026-08-01,TRANSITION_COMPLETE,E|s0|t0\n')
M.test_configure(_JP('f3_j.csv'))
open(_JP('f3_i.csv'), 'w').write(HDR + '\n'.join(SW) + '\n')
_buf = _io.StringIO()
with _cl.redirect_stdout(_buf):
    _c3 = M.run(_JP('f3_i.csv'), 'mr_tariff.csv', _JP('f3_s.csv'), _JP('f3_j.csv'), '2026-08-08', 10e6, write=True)
_out = _buf.getvalue()
chk('МР: при параллельном COMPLETE печатается ТОЛЬКО итоговый DECISION=HOLD',
    _c3 == M.HOLD and 'DECISION=SWITCH' not in _out and 'DECISION=HOLD' in _out)
# --- Контртесты вердикта ред. 22: бюджет до и под замком, изоляция самопроверки ---
import contextlib as _cx
_HDR2 = HDR; _U2 = 'https://x.example/a'
open(_JP('b1_i.csv'), 'w').write(_HDR2 + '\n'.join(SW) + '\n')
_rows = ['asof,event,detail']
for _k, _t in enumerate(['E', 'F']):
    _rows += [f'2026-0{_k+1}-01,SWITCH_SIGNAL,{_t}|s{_k}', f'2026-0{_k+1}-01,TRANSITION_OPEN,{_t}|s{_k}|t{_k}',
              f'2026-0{_k+1}-02,TRANSITION_COMPLETE,{_t}|s{_k}|t{_k}']
open(_JP('b1_j.csv'), 'w').write('\n'.join(_rows) + '\n')
M.test_configure(_JP('b1_j.csv'))
_cb = M.run(_JP('b1_i.csv'), 'mr_tariff.csv', _JP('b1_s.csv'), _JP('b1_j.csv'), '2026-08-08', 10e6, write=True)
chk('МР: бюджет — третье переключение при двух завершённых = REVIEW (до замка)',
    _cb == M.REVIEW and open(_JP('b1_j.csv')).read().count('SWITCH_SIGNAL') == 2)
chk('МР: budget_exhausted — единое правило (1 -> можно, 2 -> нельзя)',
    M.budget_exhausted(2) and not M.budget_exhausted(1) and M.MAX_SWITCHES_3Y == 2)
open(_JP('b2_j.csv'), 'w').write('asof,event,detail\n')
M.test_configure(_JP('b2_j.csv'))
open(_JP('b2_i.csv'), 'w').write(_HDR2 + '\n'.join(SW) + '\n')   # цель E, маршрут стартово F
_orig_locked = M._locked
@_cx.contextmanager
def _racing(path):
    if not getattr(_racing, 'fired', False):        # параллельный процесс между решением и захватом замка
        _racing.fired = True
        for _ev, _det in [('SWITCH_SIGNAL', 'E|sW'), ('TRANSITION_OPEN', 'E|sW|tW'), ('TRANSITION_COMPLETE', 'E|sW|tW'),
                          ('SWITCH_SIGNAL', 'F|sX'), ('TRANSITION_OPEN', 'F|sX|tX'), ('TRANSITION_COMPLETE', 'F|sX|tX')]:
            M._append_raw(M.canonical_journal(path), '2026-03-01', _ev, _det)
    with _orig_locked(path):
        yield
M._locked = _racing
_cr = M.run(_JP('b2_i.csv'), 'mr_tariff.csv', _JP('b2_s.csv'), _JP('b2_j.csv'), '2026-08-08', 10e6, write=True)
M._locked = _orig_locked
chk('МР: бюджет ПОД ЗАМКОМ — параллельные переходы (цель не достигнута) дают REVIEW без нового сигнала',
    _cr == M.REVIEW and open(_JP('b2_j.csv')).read().count('SWITCH_SIGNAL,E') == 1)
_stray = [f for f in os.listdir('.') if f.startswith('.journal.pin.') or f.startswith('.deploy.') and 'backup' not in f and f != os.path.basename(M.deploy_config_path())]
chk('Самопроверка: фикстуры и журналы — только в каталоге прогона, посторонних артефактов нет', not _stray)
# --- Контртесты вердикта ред. 23 ---
_j1 = _JP('dep1_journal.csv')
_j2dir = os.path.join(_TD, 'dep2')
os.makedirs(_j2dir, exist_ok=True)
open(_j1, 'w').write('asof,event,detail\n')
inc = 0
for _bad in (os.path.relpath(_j1), os.path.join(os.path.dirname(_j1), 'nope.csv')):
    try: M.provision_journal_pin(_bad, force=True)
    except M.JournalCorrupt: inc += 1
open(os.path.join(os.path.dirname(_j1), 'bad.csv'), 'w').write('foo,bar\n')
try: M.provision_journal_pin(os.path.join(os.path.dirname(_j1), 'bad.csv'), force=True)
except M.JournalCorrupt: inc += 1
chk('МР: провизия отклоняет относительный, несуществующий и файл с чужой схемой', inc == 3)
M._clear_pin(); M.provision_journal_pin(_j1, force=True); M.configure(_j1)
if os.path.lexists(_j2dir + '/hard.csv'): os.remove(_j2dir + '/hard.csv')
os.link(_j1, _j2dir + '/hard.csv')
_probe = ("import sys, fcntl\n"
          "f = open(%r, 'r+')\n"
          "try:\n fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); print('FREE')\n"
          "except OSError: print('LOCKED')\n") % _j1
with M.hold_strategy_lock(_j1):
    _rl = subprocess.run([sys.executable, '-c', _probe], capture_output=True, text=True)
chk('МР: единый замок стратегия+счёт по фиксированному пути занят для чужого процесса',
    _rl.stdout.strip() == 'LOCKED')
M.append_event(_j1, '2026-08-08', 'REVIEW_SIGNAL', 'до reprovision')
M.provision_journal_pin(_j1, force=True)
inc = False
try: M.append_event(_j1, '2026-08-08', 'REVIEW_SIGNAL', 'после reprovision')
except M.JournalCorrupt: inc = True
M._clear_pin(); M.configure(_j1)
M.append_event(_j1, '2026-08-08', 'REVIEW_SIGNAL', 'после перезапуска')
chk('МР: --force-reprovision инвалидирует уже работающий процесс (эпоха), после переконфигурации пишет',
    inc and open(_j1).read().count('после перезапуска') == 1)
_jp = _JP('prio_j.csv'); open(_jp, 'w').write('asof,event,detail\n')
M._PINNED['journal'] = None; M.test_configure(_jp)
open(_JP('prio_i.csv'), 'w').write(HDR + '\n'.join(SW) + '\n')
import contextlib as _cx2
_ol = M._locked
def _mk_race(events):
    @_cx2.contextmanager
    def _r(path):
        if not getattr(_r, 'f', False):
            _r.f = True
            for _e, _d in events:
                M._append_raw(M.canonical_journal(path), '2026-03-01', _e, _d)
        with _ol(path):
            yield
    return _r
M._locked = _mk_race([('SWITCH_SIGNAL', 'E|a'), ('TRANSITION_OPEN', 'E|a|ta'), ('TRANSITION_COMPLETE', 'E|a|ta'),
                      ('SWITCH_SIGNAL', 'F|b'), ('TRANSITION_OPEN', 'F|b|tb'), ('TRANSITION_COMPLETE', 'F|b|tb'),
                      ('SWITCH_SIGNAL', 'E|c'), ('TRANSITION_OPEN', 'E|c|tc'), ('TRANSITION_COMPLETE', 'E|c|tc')])
_cp = M.run(_JP('prio_i.csv'), 'mr_tariff.csv', _JP('prio_s.csv'), _jp, '2026-08-08', 10e6, write=True)
M._locked = _ol
chk('МР: под замком цель достигнута И бюджет исчерпан -> HOLD (приоритет как до замка)', _cp == M.HOLD)
_jc = _JP('cnt_j.csv'); open(_jc, 'w').write('asof,event,detail\n')
M._PINNED['journal'] = None; M.test_configure(_jc)
M._locked = _mk_race([('SWITCH_SIGNAL', 'F|z')])
_cc = M.run(_JP('prio_i.csv'), 'mr_tariff.csv', _JP('cnt_s.csv'), _jc, '2026-08-08', 10e6, write=True)
M._locked = _ol
chk('МР: под замком встречный pending -> REVIEW (как до замка)', _cc == M.REVIEW)
# --- Контртесты вердикта ред. 24: единый замок, единая конфигурация, долговечность ---
_s1 = _JP('s1a_journal.csv'); _s2dir = os.path.join(_TD, 's1b'); os.makedirs(_s2dir, exist_ok=True)
_s2 = os.path.join(_s2dir, 's1b_journal.csv')
for _p in (_s1, _s2): open(_p, 'w').write('asof,event,detail\n')
M._clear_pin(); M.provision_journal_pin(_s1, force=True); M.configure(_s1)
M.append_event(_s1, '2026-08-08', 'REVIEW_SIGNAL', 'до')
M.provision_journal_pin(_s2, force=True)
inc = False
try: M.append_event(_s1, '2026-08-08', 'REVIEW_SIGNAL', 'после')
except M.JournalCorrupt: inc = True
_side = [f for f in os.listdir(os.path.dirname(_s1)) + os.listdir(_s2dir) if f.startswith('.deploy')]
chk('МР: конфигурация ЕДИНСТВЕННА — reprovision в другой каталог инвалидирует старый процесс',
    inc and not _side and os.path.exists(M.deploy_config_path()))
M._clear_pin(); M.configure(_s2)
open(os.path.join(_s2dir, 'fresh.csv'), 'w').write('asof,event,detail\n')
os.replace(os.path.join(_s2dir, 'fresh.csv'), _s2)          # прежний путь, НОВЫЙ inode
inc = False
try: M.append_event(_s2, '2026-08-08', 'REVIEW_SIGNAL', 'после подмены')
except M.JournalCorrupt: inc = True
_codeid = ("import sys; sys.path.insert(0, %r)\n"
           "import mr_engine as M\n"
           "M.set_state_dir_for_tests(%r)\n"
           "try:\n M.configure(%r); print('CONFIGURED')\n"
           "except M.JournalCorrupt: print('REJECTED')\n") % (os.getcwd(), os.path.join(_TD, 'install'), _s2)
_rid = subprocess.run([sys.executable, '-c', _codeid], capture_output=True, text=True)
chk('МР: подмена файла новым inode по прежнему пути инвалидирует процесс и свежий запуск',
    inc and _rid.stdout.strip() == 'REJECTED')
M._clear_pin(); M.provision_journal_pin(_s2, force=True); M.configure(_s2)
import threading as _th2, time as _tm2
def _holder():
    with M.hold_strategy_lock(_s2):
        _tm2.sleep(1.2)
_thh = _th2.Thread(target=_holder); _thh.start(); _tm2.sleep(0.3)
_codepv = ("import sys, time; sys.path.insert(0, %r)\n"
           "import mr_engine as M\n"
           "M.set_state_dir_for_tests(%r)\n"
           "t0 = time.time(); M.provision_journal_pin(%r, force=True)\n"
           "print('WAITED=%%.2f' %% (time.time() - t0))\n") % (os.getcwd(), os.path.join(_TD, 'install'), _s2)
_rpv2 = subprocess.run([sys.executable, '-c', _codepv], capture_output=True, text=True)
_thh.join()
_waited = float(_rpv2.stdout.strip().split('=')[1]) if 'WAITED=' in _rpv2.stdout else -1.0
chk('МР: смена журнала СЕРИАЛИЗУЕТСЯ с работающим исполнителем (провизия ждёт замок)', _waited >= 0.5)
M._PINNED['journal'] = None; M.test_configure(_s2)
_fs = {'n': 0}; _origfs = os.fsync
os.fsync = lambda fd: (_fs.__setitem__('n', _fs['n'] + 1), _origfs(fd))[1]
try:
    M.append_event(_s2, '2026-08-08', 'REVIEW_SIGNAL', 'durable')
    _n_journal = _fs['n']
    M.provision_journal_pin(_s2, force=True)
finally:
    os.fsync = _origfs
chk('МР: долговечность — fsync при записи журнала и конфигурации', _n_journal >= 1 and _fs['n'] > _n_journal)
# --- Контртесты вердикта ред. 25 ---
_copy_dir = os.path.join(_TD, 'codecopy'); os.makedirs(_copy_dir, exist_ok=True)
_sh0.copy('mr_engine.py', _copy_dir)
_code_paths = ("import sys; sys.path.insert(0, %r)\n"
               "import mr_engine as M\n"
               "print(M.lock_path()); print(M.deploy_config_path())\n")
_p1 = subprocess.run([sys.executable, '-c', _code_paths % os.getcwd()], capture_output=True, text=True).stdout.strip().splitlines()
_p2 = subprocess.run([sys.executable, '-c', _code_paths % _copy_dir], capture_output=True, text=True).stdout.strip().splitlines()
chk('МР: две ФИЗИЧЕСКИЕ копии кода дают один и тот же замок и одну конфигурацию',
    _p1 == _p2 and len(_p1) == 2 and os.path.isabs(_p1[0]) and _copy_dir not in _p1[0])
_lkj = _JP('lockid_journal.csv'); open(_lkj, 'w').write('asof,event,detail\n')
M._clear_pin(); M.provision_journal_pin(_lkj, force=True); M.configure(_lkj)
_INSTB = os.path.join(_TD, 'installB')
_pre = ("import sys; sys.path.insert(0, %r)\n"
        "import mr_engine as M\n"
        "M.set_state_dir_for_tests(%r); M.provision_journal_pin(%r, force=True)\n") % (os.getcwd(), _INSTB, _lkj)
subprocess.run([sys.executable, '-c', _pre], capture_output=True, text=True)
_probe_hold = ("import sys; sys.path.insert(0, %r)\n"
               "import mr_engine as M\n"
               "M.set_state_dir_for_tests(%r); M.configure(%r)\n"
               "try:\n"
               "    with M.hold_strategy_lock(%r): print('ACQUIRED')\n"
               "except RuntimeError: print('DENIED')\n") % (os.getcwd(), _INSTB, _lkj, _lkj)
with M.hold_strategy_lock(_lkj):                     # A держит журнал
    _lp = M.lock_path()
    open(_lp + '.new', 'w').write(''); os.replace(_lp + '.new', _lp)   # подмена служебного файла
    _r2 = subprocess.run([sys.executable, '-c', _probe_hold], capture_output=True, text=True, timeout=60)
chk('МР: другой каталог установки/HOME на ТОМ ЖЕ журнале не даёт второго владельца',
    _r2.stdout.strip() == 'DENIED')
_r2b = subprocess.run([sys.executable, '-c', _probe_hold], capture_output=True, text=True, timeout=60)
chk('МР: после освобождения журнал доступен', _r2b.stdout.strip() == 'ACQUIRED')
_lnk = _JP('symlink_probe.csv')
if os.path.lexists(_lnk + '.link'): os.remove(_lnk + '.link')
open(_lnk, 'w').write('asof,event,detail\n'); os.symlink(_lnk, _lnk + '.link')
inc = False
try: M._open_journal_verified(_lnk + '.link')
except OSError: inc = True
chk('МР: O_NOFOLLOW — symlink вместо журнала отклоняется на уровне открытия', inc)
_swp = _JP('swap_journal.csv'); open(_swp, 'w').write('asof,event,detail\n')
M._clear_pin(); M.provision_journal_pin(_swp, force=True); M.configure(_swp)
open(_swp + '.att', 'w').write('asof,event,detail\n2026-01-01,SWITCH_SIGNAL,E|attacker\n')
os.replace(_swp + '.att', _swp)                      # подмена ПОСЛЕ проверки личности
inc = False
try: M.journal_rows(_swp, date(2026, 8, 8))
except M.JournalCorrupt: inc = True
chk('МР: подмена журнала после проверки — чтение через проверенный fd отклонено', inc)
_pj = _JP('provlock_journal.csv'); open(_pj, 'w').write('asof,event,detail\n')
M._clear_pin(); M.provision_journal_pin(_pj, force=True); M.configure(_pj)
import threading as _th3, time as _tm3
def _hold2():
    with M.hold_strategy_lock(_pj):
        _tm3.sleep(1.0)
_t3 = _th3.Thread(target=_hold2); _t3.start(); _tm3.sleep(0.2)
open(_pj + '.repl', 'w').write('foo,bar\n')
os.replace(_pj + '.repl', _pj)                     # подмена журнала во время ожидания провизии
_codepv2 = ("import sys; sys.path.insert(0, %r)\n"
            "import mr_engine as M\n"
            "M.set_state_dir_for_tests(%r)\n"
            "try:\n M.provision_journal_pin(%r, force=True); print('PROVISIONED')\n"
            "except M.JournalCorrupt: print('REJECTED')\n") % (os.getcwd(), os.path.join(_TD, 'install'), _pj)
_rpv3 = subprocess.run([sys.executable, '-c', _codepv2], capture_output=True, text=True)
_t3.join()
chk('МР: журнал проверяется ПОД замком — подмена во время ожидания провизии отклоняется',
    _rpv3.stdout.strip() == 'REJECTED')
_fs2 = {'n': 0}; _of2 = os.fsync
os.fsync = lambda fd: (_fs2.__setitem__('n', _fs2['n'] + 1), _of2(fd))[1]
try:
    T._atomic(_JP('state_probe.json'), {'x': 1})
finally:
    os.fsync = _of2
chk('Исполнитель: состояние пишется с fsync файла и каталога', _fs2['n'] >= 2)
_tj = _JP('toctou_journal.csv'); open(_tj, 'w').write('asof,event,detail\n')
M._clear_pin(); M.provision_journal_pin(_tj, force=True); M.configure(_tj)
_bad_body = 'foo,bar\n'
_orig_stat = os.stat
def _racing_stat(p, *a, **k):
    if isinstance(p, str) and p == _tj and not getattr(_racing_stat, 'fired', False):
        _racing_stat.fired = True
        open(_tj + '.x', 'w').write(_bad_body); os.replace(_tj + '.x', _tj)   # подмена внутри окна
    return _orig_stat(p, *a, **k)
os.stat = _racing_stat
inc = False
try:
    M.provision_journal_pin(_tj, force=True)
except M.JournalCorrupt:
    inc = True
finally:
    os.stat = _orig_stat
chk('МР: подмена журнала между чтением схемы и сверкой личности отклоняется (единый дескриптор)', inc)
# --- Контртесты вердикта ред. 28: два замка, отсоединённый fd, сериализация смены журнала ---
_dj = _JP('dual_journal.csv'); _dj2 = _JP('dual_journal2.csv')
for _p in (_dj, _dj2): open(_p, 'w').write('asof,event,detail\n')
M._clear_pin(); M.provision_journal_pin(_dj, force=True); M.configure(_dj)
_probe_dual = ("import sys; sys.path.insert(0, %r)\n"
               "import mr_engine as M\n"
               "M.set_state_dir_for_tests(%r)\n"
               "try:\n"
               "    M.configure(%r)\n"
               "    with M.hold_strategy_lock(%r): print('ACQUIRED')\n"
               "except Exception as e: print(type(e).__name__)\n") % (os.getcwd(), os.path.join(_TD, 'install'), _dj, _dj)
with M.hold_strategy_lock(_dj):
    open(_dj + '.n', 'w').write('asof,event,detail\n'); os.replace(_dj + '.n', _dj)   # подмена inode журнала
    _rd = subprocess.run([sys.executable, '-c', _probe_dual], capture_output=True, text=True, timeout=60)
chk('МР: подмена inode журнала при удерживаемом замке не даёт второго владельца',
    _rd.stdout.strip() in ('JournalCorrupt', 'DENIED') and _rd.stdout.strip() != 'ACQUIRED')
M._clear_pin(); M.provision_journal_pin(_dj, force=True); M.configure(_dj)
inc = False
with M.hold_strategy_lock(_dj):
    open(_dj + '.n2', 'w').write('asof,event,detail\n'); os.replace(_dj + '.n2', _dj)
    try: M.append_event(_dj, '2026-08-08', 'REVIEW_SIGNAL', 'ghost')
    except M.JournalCorrupt: inc = True
chk('МР: запись через отсоединённый дескриптор запрещена (нормативный журнал не расходится)', inc)
M._clear_pin(); M.provision_journal_pin(_dj, force=True); M.configure(_dj)
import threading as _th4, time as _tm4
def _hold4():
    with M.hold_strategy_lock(_dj):
        _tm4.sleep(1.0)
_t4 = _th4.Thread(target=_hold4); _t4.start(); _tm4.sleep(0.2)
_code4 = ("import sys, time; sys.path.insert(0, %r)\n"
          "import mr_engine as M\n"
          "M.set_state_dir_for_tests(%r)\n"
          "t0 = time.time(); M.provision_journal_pin(%r, force=True)\n"
          "print('WAITED=%%.2f' %% (time.time() - t0))\n") % (os.getcwd(), os.path.join(_TD, 'install'), _dj2)
_r4 = subprocess.run([sys.executable, '-c', _code4], capture_output=True, text=True, timeout=90)
_t4.join()
_w4 = float(_r4.stdout.strip().split('=')[1]) if 'WAITED=' in _r4.stdout else -1.0
chk('МР: смена журнала на ДРУГОЙ ждёт владельца старого журнала (сериализация)', _w4 >= 0.4)
inc = False
try: M.append_event(_dj, '2026-08-08', 'REVIEW_SIGNAL', 'old')
except M.JournalCorrupt: inc = True
chk('МР: после смены журнала старый процесс инвалидирован', inc)
ok = all(map_mes(n)[0]*10 + map_mes(n)[1] == n and 0 <= map_mes(n)[1] <= 9 for n in [0, 1, 9, 10, 171, 1234])
chk('execution-mapper map_mes', ok)


# --- ПОСТАВЛЕННАЯ ПАРА «ЗАМЕР МАРЖИ <-> РЕЕСТР» (двадцатый круг, №17) ------------------
# Рецензент указал: выпускные тесты пользуются временной полной фикстурой и НЕ ВИДЯТ, что
# в самом пакете margins_live.json покрывает не все FUT-серии instruments_live.csv. С такой
# парой любой preflight цели Ф останавливается, то есть поставленный комплект не способен
# перейти обратно из Е — и об этом ничего не сказано.
#
# ДОЛГ С ДАТОЙ ПОГАШЕНИЯ. 14.08.2026 замер снять не удалось: paper-whatIf днём не отдаёт
# маржу (проверено дважды, 10:00 и 13:20 Чикаго). Маршрут Ф маржу не читает, а переход не
# раньше первого ролла 26.08 — поэтому до 20.08.2026 несогласованность даёт ГРОМКОЕ
# предупреждение, а начиная с 20.08 — ПРОВАЛ выпуска. Так долг не превращается в вечный.
_MRG_DEADLINE = '2026-08-20'
try:
    _mj = _json1.loads(open('live/margins_live.json',
                            encoding='utf-8').read())
    _mser = {k for k in _mj if k != '_meta'}
    with open('live/instruments_live.csv', encoding='utf-8') as _f:
        _rser = {r['instrument'] for r in csv.DictReader(_f) if r.get('sec_type') == 'FUT'}
    _miss = sorted(_rser - _mser)
    _pair_here = True
except FileNotFoundError:
    # ФАЙЛОВ НЕТ — ПРОВЕРКА НЕПРИМЕНИМА, А НЕ ПРОЙДЕНА (двадцать шестой круг, №20).
    # Реестр и замер принадлежат СЧЁТУ и в архив намеренно не входят. На чистой распаковке
    # проверять нечего, и объявлять это успехом — ложное доказательство: именно так
    # «согласованность поставленной пары» подтверждалась на архиве, где пары нет вовсе.
    _miss, _pair_here = [], False
except Exception as _ex:
    _miss, _pair_here = [f'проверка невозможна: {_ex}'], True
_today_s = _dt1.datetime.now(_dt1.timezone.utc).strftime('%Y-%m-%d')
if not _pair_here:
    # СРОК ПРОВЕРЯЕТСЯ ПО МАШИНЕ, А НЕ ПО АРХИВУ (двадцать девятый круг, №18). Архив
    # намеренно не содержит живой пары, поэтому на распакованной копии проверка всегда
    # «неприменима» — то есть требование «с 20.08 отсутствие замера = провал выпуска» было
    # НЕДОСТИЖИМО по построению. Читаем пару там, где она живёт: в ~/.addfut рядом с кодом
    # контура. Отсутствие ПОСЛЕ срока — провал; до срока — предупреждение.
    # живой слой лежит рядом с рабочим деревом проекта, а не в распакованном архиве
    _REAL_LIVE = _os1.path.join(_os1.path.expanduser('~'), 'claude-projects', 'vol-down12',
                                'r33build', 'live')
    _mj_m = _os1.path.join(_REAL_LIVE, 'margins_live.json')
    _rg_m = _os1.path.join(_REAL_LIVE, 'instruments_live.csv')
    try:
        _mjm = _json1.loads(open(_mj_m, encoding='utf-8').read())
        _mserm = {k for k in _mjm if k != '_meta'}
        with open(_rg_m, encoding='utf-8') as _fm:
            _rserm = {r['instrument'] for r in csv.DictReader(_fm)
                      if r.get('sec_type') == 'FUT'}
        _missm = sorted(_rserm - _mserm)
        # ПОКРЫТИЕ ИМЁН — НЕ ЗАМЕР (тридцать первый круг, №17). Ворота проверяли ровно
        # разность множеств ключей: файл с нужными именами и нулями, NaN, чужим счётом или
        # прошлогодней датой печатал «машинная пара согласована», то есть требование «с
        # 20.08 замер РЕАЛЬНО получен» подменялось доказательством существования ключей.
        # Живой переход всё равно упал бы в _live_margins — уже после того, как выпуск
        # объявил готовность. Спрашиваем ровно у того кода, который решает в бою: _meta.
        # account против машинного пина, возраст, совпадение _meta.series с содержимым,
        # конечность и положительность init/maint. Дублировать эти правила здесь значило бы
        # завести второй норматив, который разойдётся с первым.
        if not _missm:
            # ПРОВЕРЯЕТСЯ МАШИННАЯ ПАРА ЦЕЛИКОМ, А НЕ ГИБРИД (тридцать второй круг, №18).
            # Выше selfcheck выставляет ADDFUT_REGISTRY на АРХИВНЫЙ instruments.csv и
            # ADDFUT_ACCOUNT='DUTEST01'; подменяя только ADDFUT_MARGINS, я сверял живой
            # замер с ФИКТИВНЫМ реестром и ФИКТИВНЫМ счётом — успешный путь был невозможен
            # по построению, и после 20.08 корректный живой замер всё равно валил бы выпуск,
            # а строка «ворота проверили машинную пару» не соответствовала исполненному
            # коду. Подменяем все три величины на машинные и возвращаем обратно.
            _keep3 = {k: _os1.environ.get(k)
                      for k in ('ADDFUT_MARGINS', 'ADDFUT_REGISTRY', 'ADDFUT_ACCOUNT')}
            _pin_m = _os1.path.join(_os1.path.expanduser('~'), '.addfut', 'account.txt')
            _os1.environ['ADDFUT_MARGINS'] = _mj_m
            _os1.environ['ADDFUT_REGISTRY'] = _rg_m
            try:
                with open(_pin_m, encoding='utf-8') as _fp:
                    _os1.environ['ADDFUT_ACCOUNT'] = _fp.read().strip()
            except OSError as _exp:
                raise RuntimeError(f'машинный пин счёта не читается ({_exp}) — пара '
                                   f'непроверяема')
            try:
                T._live_margins()
            finally:
                for _k3, _v3 in _keep3.items():
                    _os1.environ.pop(_k3, None)
                    if _v3 is not None:
                        _os1.environ[_k3] = _v3
    except Exception as _exm:
        _missm = [f'машинная пара недоступна либо замер непригоден: {_exm}']
    # ДВЕ РАЗНЫЕ ПРОВЕРКИ БЫЛИ СМЕШАНЫ В ОДНУ (27.08.2026, находка №13 сплошного аудита).
    # Одна проверяет АРХИВ и обязана работать у любого аудитора; вторая проверяет МАШИНУ и
    # по существу требует ~/.addfut и рабочего дерева. Двадцать девятый круг верно заметил,
    # что на распакованной копии живой пары нет, и стал читать её по ЖЁСТКОМУ пути
    # ~/claude-projects/vol-down12 — то есть по пути ОДНОЙ конкретной машины.
    # ЧТО ИЗ ЭТОГО ВЫШЛО: аудитор делает ровно то, что предписывает корень доверия
    # («распаковать в пустой каталог и выполнить python selfcheck_v192.py»), не находит
    # чужого домашнего каталога и получает ИТОГ: ПРОВАЛ. Корень доверия оказался
    # непроверяемым НИКЕМ, кроме этой машины, — а смысл корня ровно в обратном.
    # РАЗЛИЧИТЕЛЬ ЧЕСТНЫЙ: существует ли рабочее дерево. Есть — мы у себя, ворота выпуска
    # обязаны работать в полную силу (release.py гоняет selfcheck именно здесь). Нет —
    # проверяющий смотрит архив со стороны, и требовать от него машинного состояния
    # бессмысленно: он его не имеет и иметь не должен.
    # ЧЕГО ЭТО НЕ ОСЛАБЛЯЕТ: у нас на машине дерево есть всегда, поэтому провал выпуска при
    # непокрытых сериях остаётся достижимым — та самая недостижимость, которую закрывал
    # 29-й круг, не возвращается.
    _tree_here = _os1.path.isdir(_REAL_LIVE)
    if not _tree_here:
        print('[НЕПРИМЕНИМО ВНЕ РАБОЧЕГО ДЕРЕВА] машинная пара «замер маржи <-> реестр» '
              'живёт рядом с контуром, а не в архиве: проверка пропущена. Это ВНЕШНЯЯ '
              'проверка архива — она не должна требовать чужого машинного состояния.')
    elif not _missm:
        chk('машинная пара «замер маржи <-> реестр» согласована', True, 'все серии покрыты')
    elif _today_s < _MRG_DEADLINE:
        print(f'[ВНИМАНИЕ] машинный замер не покрывает серии реестра {_missm} — переход '
              f'Ф<->Е ЗАБЛОКИРОВАН; провал выпуска с {_MRG_DEADLINE}')
    else:
        chk('машинная пара «замер маржи <-> реестр» согласована', False,
            f'не покрыты {_missm}; срок {_MRG_DEADLINE} истёк')
    print('[НЕПРИМЕНИМО НА АРХИВЕ] живой пары в архиве нет — она принадлежит счёту')
elif not _miss:
    chk('поставленная пара «замер маржи <-> реестр» согласована', True, 'все серии покрыты')
elif _today_s < _MRG_DEADLINE:
    print(f'[ВНИМАНИЕ] замер маржи не покрывает серии реестра {_miss} — ПЕРЕХОД Ф<->Е '
          f'ЗАБЛОКИРОВАН до утреннего замера (первый ролл 26.08; провал выпуска с '
          f'{_MRG_DEADLINE})')
else:
    chk('поставленная пара «замер маржи <-> реестр» согласована', False,
        f'не покрыты {_miss}; срок {_MRG_DEADLINE} истёк')

_m1 = _machine_snapshot()
chk('стенды не тронули машинное состояние ~/.addfut',
    _m1 == _MACHINE0,
    'без изменений' if _m1 == _MACHINE0 else 'ИЗМЕНЕНЫ: ' + ', '.join(
        sorted(set(_m1) ^ set(_MACHINE0)
               | {k for k in set(_m1) & set(_MACHINE0) if _m1[k] != _MACHINE0[k]})))
print('ИТОГ: ' + ('ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ' if not FAIL else 'ПРОВАЛ: ' + ', '.join(FAIL)))
sys.exit(1 if FAIL else 0)
