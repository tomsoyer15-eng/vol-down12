#!/usr/bin/env python3
"""Рецензирование через OpenAI Responses API: gpt-5.6, режим Pro, фоновое выполнение.

ПРИНЦИП. Каждый запрос заканчивается ЛИБО готовой рецензией, ЛИБО явной ошибкой со
статусом, error.code, error.message, response_id и request_id. Пустых строк при ошибке,
проглоченных исключений и молчаливых повторов здесь нет: неудачный запрос обязан быть
виден, потому что он оплачен.

ЗАДАНИЕ СОХРАНЯЕТСЯ СРАЗУ. response_id пишется в реестр ДО первого опроса. Если программа
перезапустилась, она продолжает опрашивать существующее задание, а не создаёт новую
платную рецензию: команда `resume`.

АРХИВ НЕ ОТПРАВЛЯЕТСЯ КАК ФАЙЛ. Пакет распаковывается программно, из него исключаются
двоичные, временные и повторяющиеся файлы, считается объём текста, и лишь затем строится
запрос; при переполнении бюджета проверка делится на части, а последним запросом
собирается общий вердикт.

Команды:
    selftest            три контрольных теста (простой / pro+background / объём)
    account             что можно проверить по ключу, и что проверяется только в консоли
    collect <путь>      разбор пакета без отправки: что войдёт в запрос и сколько это
    review <путь> ...   рецензия (при необходимости по частям + сводный вердикт)
    resume <id> [...]   продолжить опрос существующих заданий
    jobs                реестр заданий
"""
import hashlib, json, os, sys, time, zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOBS = ROOT / 'docs/openai-jobs.json'
# Голой gpt-5.6 у ключа нет: семейство выдано вариантами luna/sol/terra.
# Выбран sol (решение заказчика 11.08.2026); все три приняли reasoning.mode=pro и фон.
MODEL = os.environ.get('OPENAI_REVIEW_MODEL', 'gpt-5.6-sol')
# «Ультра» — команда заказчика: если он просит проверить «через ультра», запускать с
# OPENAI_REVIEW_MODE=pro OPENAI_REVIEW_EFFORT=max (верхняя ступень API; effort 'ultra'
# не существует). Умолчание остаётся прежним — medium.
REASONING = {'mode': os.environ.get('OPENAI_REVIEW_MODE', 'pro'),
             'effort': os.environ.get('OPENAI_REVIEW_EFFORT', 'medium')}
POLL_S = 5
CHARS_PER_PART = 400_000          # бюджет части; в токенах примерно вчетверо меньше

TEXT_EXT = {'.py', '.md', '.txt', '.ini', '.cfg', '.toml', '.json', '.sh', '.yml', '.yaml'}
SKIP_DIRS = {'__pycache__', '.git', '.install4j', 'archive', '.venv', 'node_modules'}
SKIP_EXT = {'.pdf', '.zip', '.gz', '.jar', '.png', '.jpg', '.pkl', '.so', '.class'}
DATA_CSV_MAX = 8_000              # CSV крупнее — ряд данных, а не конфигурация


def _client():
    from openai import OpenAI
    key_file = Path.home() / '.openai-key'
    key = os.environ.get('OPENAI_API_KEY') or (
        key_file.read_text().strip() if key_file.exists() else None)
    if not key:
        raise SystemExit('нет ключа: ни OPENAI_API_KEY, ни ~/.openai-key')
    # max_retries=0: повтор POST на стороне SDK способен создать ВТОРОЕ платное задание,
    # если первый запрос дошёл, а ответ потерялся. Повторы делаем сами и только там, где
    # это безопасно (retrieve), а создание задания не повторяем никогда.
    return OpenAI(api_key=key, max_retries=0)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _jobs_load():
    return json.loads(JOBS.read_text()) if JOBS.exists() else []


def _jobs_add(rec):
    """Запись задания ДО первого опроса — иначе перезапуск потеряет оплаченный запрос."""
    js = _jobs_load(); js.append(rec)
    JOBS.parent.mkdir(parents=True, exist_ok=True)
    tmp = JOBS.with_suffix('.tmp')
    tmp.write_text(json.dumps(js, ensure_ascii=False, indent=1))
    os.replace(tmp, JOBS)


def _jobs_update(rid, **kw):
    js = _jobs_load()
    for r in js:
        if r.get('response_id') == rid:
            r.update(kw)
    tmp = JOBS.with_suffix('.tmp')
    tmp.write_text(json.dumps(js, ensure_ascii=False, indent=1))
    os.replace(tmp, JOBS)


# ---------------------------------------------------------------- ошибки

RETRY_CODES = {'rate_limit_exceeded'}
FATAL_CODES = {'credit_balance_exhausted', 'project_spend_limit_exceeded',
               'organization_spend_limit_exceeded', 'organization_usage_limit_exceeded',
               'insufficient_quota'}


def describe(e):
    """Развернуть исключение SDK в разбираемый вид. Ничего не скрывает."""
    d = dict(type=type(e).__name__, message=str(e), status=None, code=None,
             err_message=None, request_id=None, retry_after=None, body=None)
    d['request_id'] = getattr(e, 'request_id', None)
    resp = getattr(e, 'response', None)
    if resp is not None:
        d['status'] = getattr(resp, 'status_code', None)
        try:
            d['body'] = resp.text[:2000]
        except Exception:
            pass
        try:
            h = dict(resp.headers)
            d['retry_after'] = h.get('retry-after') or h.get('Retry-After')
            d['request_id'] = d['request_id'] or h.get('x-request-id')
        except Exception:
            pass
    body = getattr(e, 'body', None)
    if isinstance(body, dict):
        err = body.get('error') or {}
        d['code'] = err.get('code') or err.get('type')
        d['err_message'] = err.get('message')
    return d


def explain(d):
    """Что означает 429 и лечится ли он повтором."""
    c = d.get('code')
    if d.get('status') != 429:
        return f"HTTP {d.get('status')}, код {c}"
    if c in FATAL_CODES:
        table = {
            'credit_balance_exhausted': 'кончились деньги на API-балансе — пополнить, повторы не помогут',
            'insufficient_quota': 'нет квоты/баланса на проекте — пополнить, повторы не помогут',
            'project_spend_limit_exceeded': 'достигнут spending limit ПРОЕКТА — поднять лимит проекта',
            'organization_spend_limit_exceeded': 'достигнут spending limit ОРГАНИЗАЦИИ — поднять лимит',
            'organization_usage_limit_exceeded': 'достигнут разрешённый usage limit — запросить повышение',
        }
        return f'429 {c}: {table[c]}'
    if c in RETRY_CODES:
        return f"429 {c}: превышена частота — снизить параллельность, ждать Retry-After ({d.get('retry_after')})"
    return f'429 с кодом {c}: причина не распознана, повтор НЕ выполняется без разбора'


class ApiFailure(RuntimeError):
    def __init__(self, d, phase):
        self.detail = d; self.phase = phase
        super().__init__(f'[{phase}] {explain(d)} | request_id={d.get("request_id")} '
                         f'| {d.get("err_message") or d.get("message")}')


# ---------------------------------------------------------------- запрос

def _fingerprint(model, reasoning, text):
    """Отпечаток запроса: модель, режим рассуждения и вход. По нему находится уже
    оплаченное задание, чтобы перезапуск программы не создавал второе."""
    h = hashlib.sha256()
    h.update(json.dumps([model, reasoning], sort_keys=True, ensure_ascii=False).encode())
    h.update(b'\x1e'); h.update(text.encode('utf-8'))
    return h.hexdigest()


def find_paid(client, fp):
    """Уже созданное задание с тем же отпечатком, если оно ещё живо или готово."""
    import openai
    for r in reversed(_jobs_load()):
        if r.get('fingerprint') != fp or not r.get('response_id'):
            continue
        try:
            resp = client.responses.retrieve(r['response_id'])
        except openai.APIStatusError:
            continue
        if resp.status in ('queued', 'in_progress', 'completed'):
            return resp
    return None


def create(client, text, label, model=MODEL, reasoning=None, background=True):
    import openai
    t0 = time.time()
    rs = REASONING if reasoning is None else reasoning
    fp = _fingerprint(model, rs, text)
    prev = find_paid(client, fp)
    if prev is not None:
        print(f'  [{label}] НАЙДЕНО ОПЛАЧЕННОЕ ЗАДАНИЕ {prev.id} ({prev.status}) — '
              f'новое не создаётся', flush=True)
        return prev
    try:
        resp = client.responses.create(model=model, input=text, reasoning=rs,
                                       background=background)
    except openai.APIStatusError as e:
        d = describe(e)
        _jobs_add(dict(created=_now(), label=label, model=model, response_id=None,
                       status='create_failed', fingerprint=fp, error=d))
        raise ApiFailure(d, 'create')
    rid = resp.id
    _jobs_add(dict(created=_now(), label=label, model=model, response_id=rid,
                   status=resp.status, chars=len(text), fingerprint=fp,
                   request_id=getattr(resp, '_request_id', None)))
    print(f'  [{label}] response_id={rid} status={resp.status} '
          f'request_id={getattr(resp, "_request_id", None)} '
          f'вход {len(text):,} знаков, создано за {time.time()-t0:.1f} с', flush=True)
    return resp


def wait(client, resp, label, timeout_s=7200):
    import openai
    t0 = time.time(); last = None
    while resp.status in {'queued', 'in_progress'}:
        if time.time() - t0 > timeout_s:
            _jobs_update(resp.id, status='timeout_local')
            raise RuntimeError(f'[{label}] задание {resp.id} не завершилось за {timeout_s} с; '
                               f'оно НЕ отменено — продолжить: resume {resp.id}')
        time.sleep(POLL_S)
        try:
            resp = client.responses.retrieve(resp.id)
        except openai.APIStatusError as e:
            raise ApiFailure(describe(e), f'retrieve {resp.id}')
        if resp.status != last:
            print(f'  [{label}] status={resp.status} ({time.time()-t0:.0f} с)', flush=True)
            last = resp.status
    _jobs_update(resp.id, status=resp.status, finished=_now(),
                 seconds=round(time.time() - t0, 1))
    if resp.status != 'completed':
        d = dict(status=resp.status, error=getattr(resp, 'error', None),
                 incomplete_details=getattr(resp, 'incomplete_details', None),
                 response_id=resp.id)
        raise RuntimeError(f'[{label}] задание не выполнено: {json.dumps(d, default=str, ensure_ascii=False)}')
    txt = resp.output_text
    if not txt:
        raise RuntimeError(f'[{label}] статус completed, но output_text пустой; response_id={resp.id}')
    return txt, round(time.time() - t0, 1)


def ask(client, text, label, **kw):
    return wait(client, create(client, text, label, **kw), label)


# ---------------------------------------------------------------- сбор пакета

def collect(paths):
    """Развернуть пути (каталоги и архивы) в список текстовых файлов без повторов."""
    seen = {}; items = []; skipped = {'двоичные': 0, 'ряды данных': 0, 'повторы': 0, 'служебные': 0}

    def take(name, data):
        ext = Path(name).suffix.lower()
        if any(p in SKIP_DIRS for p in Path(name).parts):
            skipped['служебные'] += 1; return
        if ext in SKIP_EXT:
            skipped['двоичные'] += 1; return
        if ext == '.csv' and len(data) > DATA_CSV_MAX:
            skipped['ряды данных'] += 1; return
        if ext not in TEXT_EXT and ext != '.csv':
            skipped['двоичные'] += 1; return
        try:
            txt = data.decode('utf-8')
        except UnicodeDecodeError:
            skipped['двоичные'] += 1; return
        h = hashlib.sha256(data).hexdigest()
        if h in seen:
            skipped['повторы'] += 1; return
        seen[h] = name
        items.append((name, txt))

    for p in paths:
        p = Path(p)
        if p.is_dir():
            for f in sorted(p.rglob('*')):
                if f.is_file():
                    take(str(f.relative_to(p.parent)), f.read_bytes())
        elif p.suffix.lower() == '.zip':
            with zipfile.ZipFile(p) as z:
                for n in sorted(z.namelist()):
                    if not n.endswith('/'):
                        take(f'{p.stem}/{n}', z.read(n))
        elif p.is_file():
            take(p.name, p.read_bytes())
        else:
            raise SystemExit(f'нет такого пути: {p}')
    return items, skipped


def parts_of(items, budget=CHARS_PER_PART):
    """Разбить на части по бюджету знаков, не разрывая файлы."""
    parts = []; cur = []; n = 0
    for name, txt in items:
        block = f'\n===== ФАЙЛ: {name} ({len(txt):,} знаков) =====\n{txt}\n'
        if cur and n + len(block) > budget:
            parts.append(cur); cur = []; n = 0
        cur.append((name, block)); n += len(block)
    if cur:
        parts.append(cur)
    return parts


# ---------------------------------------------------------------- команды

BRIEF_HEAD = """Ты — враждебный рецензент. Задача: СЛОМАТЬ представленное, а не похвалить.
Ищи ошибки метода, выводы без опоры на данные, решения, обоснованные задним числом,
пропущенные проверки. Отвечай по-русски, со ссылкой на конкретный файл и место,
и приоритизируй: сначала то, что способно стоить денег.
"""


def cmd_selftest():
    client = _client(); rows = []

    def run(label, text, reasoning, background):
        t0 = time.time()
        try:
            out, sec = ask(client, text, label, reasoning=reasoning, background=background)
            rows.append(dict(тест=label, вход=len(text), статус='completed',
                             сек=sec, ответ=out[:60].replace('\n', ' ')))
            return True
        except (ApiFailure, RuntimeError) as e:
            d = getattr(e, 'detail', {})
            rows.append(dict(тест=label, вход=len(text), статус=d.get('status') or 'ошибка',
                             сек=round(time.time() - t0, 1), ответ=str(e)[:200]))
            print(f'  ОШИБКА: {e}', flush=True)
            return False

    print('Тест 1: обычный вызов, без pro и без фона')
    ok1 = run('простой', 'Ответь одним словом OK', {'effort': 'low'}, False)
    print('Тест 2: reasoning.mode=pro, background=True')
    ok2 = run('pro+фон', 'Ответь одним словом OK', REASONING, True)
    print('Тест 3: полный запрос на рецензию (объём пакета)')
    items, sk = collect([ROOT / 'docs/addfut-v1.6.0-r33.txt'])
    txt = BRIEF_HEAD + ''.join(f'\n===== {n} =====\n{t}' for n, t in items)
    ok3 = run('рецензия', txt, REASONING, True)

    print('\n' + '-' * 100)
    for r in rows:
        print(f"{r['тест']:<12}вход {r['вход']:>9,}  статус {str(r['статус']):<12}"
              f"{r['сек']:>8} с  {r['ответ']}")
    if ok1 and ok2 and not ok3:
        print('\nВЫВОД: первые два проходят, третий нет — дело в размере пакета, '
              'обработке файлов или нашем тайм-ауте, а не в доступе к API.')
    elif not ok1:
        print('\nВЫВОД: не проходит даже простой вызов — разбираться с ключом, '
              'проектом и балансом (команда account).')
    return 0 if (ok1 and ok2 and ok3) else 1


def cmd_account():
    """Что можно проверить ключом, а что — только в консоли платформы."""
    import openai
    client = _client()
    print('Проверяется ключом:')
    try:
        ms = [m.id for m in client.models.list().data]
        print(f'  ключ действителен, моделей видно {len(ms)}')
        for want in (MODEL, 'gpt-5', 'gpt-5-pro'):
            print(f'    {want:<12} {"есть" if want in ms else "НЕ ВИДНА этому ключу"}')
    except openai.APIStatusError as e:
        d = describe(e)
        print(f'  ключ НЕ работает: {explain(d)} | {d.get("err_message")}')
        return 1
    print('\nПроверяется ТОЛЬКО в консоли platform.openai.com (обычным ключом не читается,\n'
          'нужен admin-ключ организации; подписка ChatGPT Pro обращения по API НЕ оплачивает):')
    for u in ('Billing → Credit balance: есть ли деньги на API-балансе',
              'Usage: фактический расход по дням и моделям',
              'Settings → Limits: лимиты организации',
              'Project → Limits: spending limit проекта',
              'API keys: тот ли ключ и тот ли проект'):
        print(f'  - {u}')
    return 0


def cmd_collect(paths):
    items, sk = collect(paths)
    total = sum(len(t) for _, t in items)
    parts = parts_of(items)
    print(f'файлов отобрано: {len(items)}, знаков {total:,} (≈{total//4:,} токенов), '
          f'частей: {len(parts)}')
    print('пропущено: ' + ', '.join(f'{k} {v}' for k, v in sk.items() if v))
    for i, p in enumerate(parts, 1):
        print(f'  часть {i}: файлов {len(p)}, знаков {sum(len(b) for _, b in p):,}')
        for n, b in p[:40]:
            print(f'      {n} ({len(b):,})')
    return 0


def cmd_review(paths):
    client = _client()
    items, sk = collect(paths)
    if not items:
        raise SystemExit('нечего отправлять: после отбора не осталось файлов')
    parts = parts_of(items)
    total = sum(len(t) for _, t in items)
    print(f'к отправке: файлов {len(items)}, знаков {total:,}, частей {len(parts)}; '
          f'пропущено ' + ', '.join(f'{k} {v}' for k, v in sk.items() if v))
    outs = []
    for i, part in enumerate(parts, 1):
        head = (BRIEF_HEAD + f'\nЭто ЧАСТЬ {i} из {len(parts)}. Разбирай только её; '
                f'общий вердикт будет отдельным запросом.\n' if len(parts) > 1 else BRIEF_HEAD)
        txt = head + ''.join(b for _, b in part)
        out, sec = ask(client, txt, f'часть {i}/{len(parts)}')
        outs.append(f'## Часть {i} из {len(parts)}\n\n{out}')
        (ROOT / f'docs/kritika-chast-{i}.md').write_text(outs[-1], encoding='utf-8')
    if len(parts) > 1:
        syn = ('Ниже — разборы частей одного пакета, сделанные раздельно. Собери ОБЩИЙ '
               'вердикт: что из найденного действительно способно стоить денег, что '
               'является следствием раздельного чтения и на самом деле закрыто в другой '
               'части, и чего не хватает во всём пакете сразу. По-русски, по приоритету.\n\n'
               + '\n\n'.join(outs))
        out, sec = ask(client, syn, 'сводный вердикт')
        outs.append(f'## Сводный вердикт\n\n{out}')
    dst = ROOT / 'docs/kritika-r33-gpt56pro.md'
    dst.write_text(f'# Внешняя критика ({MODEL}, режим pro)\n\n' + '\n\n'.join(outs) + '\n',
                   encoding='utf-8')
    print(f'\nзаписано: {dst}')
    return 0


def cmd_resume(ids):
    client = _client()
    for rid in ids:
        resp = client.responses.retrieve(rid)
        print(f'{rid}: status={resp.status}')
        if resp.status in {'queued', 'in_progress'}:
            txt, sec = wait(client, resp, rid)
        elif resp.status == 'completed':
            txt = resp.output_text
            if not txt:
                raise RuntimeError(f'{rid}: completed, но output_text пустой')
        else:
            raise RuntimeError(f'{rid}: статус {resp.status}, error={getattr(resp, "error", None)}, '
                               f'incomplete_details={getattr(resp, "incomplete_details", None)}')
        dst = ROOT / f'docs/kritika-{rid[:12]}.md'
        dst.write_text(txt, encoding='utf-8')
        print(f'  записано: {dst} ({len(txt):,} знаков)')
    return 0


def cmd_jobs():
    for r in _jobs_load():
        print(f"{r.get('created')}  {str(r.get('label')):<16}{str(r.get('model')):<10}"
              f"{str(r.get('response_id')):<45}{r.get('status')}")
    return 0


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__); raise SystemExit(2)
    cmd, rest = sys.argv[1], sys.argv[2:]
    fn = {'selftest': lambda: cmd_selftest(), 'account': lambda: cmd_account(),
          'collect': lambda: cmd_collect(rest), 'review': lambda: cmd_review(rest),
          'resume': lambda: cmd_resume(rest), 'jobs': lambda: cmd_jobs()}.get(cmd)
    if fn is None:
        print(__doc__); raise SystemExit(2)
    raise SystemExit(fn())
