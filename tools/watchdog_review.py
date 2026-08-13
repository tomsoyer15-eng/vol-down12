#!/usr/bin/env python3
"""Сторож платного запроса: доводит рецензию до результата без участия человека.

ЗАЧЕМ. Сегодня одно задание простояло в очереди 47 минут и не начало считаться, притом что
пробный запрос той же модели проходил за 7 секунд. Ждать такое бессмысленно, а бросать —
терять работу. Сторож различает два состояния и действует по-разному:

  in_progress — модель считает, ждать сколько угодно;
  queued дольше порога — задание зависло; оно снимается и подаётся заново.

ПОВТОР НЕ УДВАИВАЕТ ОПЛАТУ: зависшее задание сначала отменяется (выход не считался, значит
не оплачен), и лишь затем создаётся новое. Отпечаток запроса при этом совпадает, поэтому
защита от дубля в openai_review не мешает — она ищет ЖИВОЕ задание, а отменённое таковым
не является.

Порог повторов ограничен: если задание зависает раз за разом, это не случайность, и
сторож останавливается с явной ошибкой, а не крутится вечно.
"""
import sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import openai_review as R

QUEUE_STUCK_S = 600        # queued дольше — считаем зависшим
MAX_RESUBMIT = 3
POLL_S = 30


def watch(label, build_input, out_path, header):
    """build_input() -> (текст, метка); повторяется при зависании."""
    text = build_input()
    attempts = 0
    while True:
        c = R._client()
        resp = R.create(c, text, f'{label}' + (f' (повтор {attempts})' if attempts else ''))
        t0 = time.time(); queued_since = time.time() if resp.status == 'queued' else None
        while resp.status in ('queued', 'in_progress'):
            time.sleep(POLL_S)
            resp = c.responses.retrieve(resp.id)
            if resp.status == 'queued':
                if queued_since is None:
                    queued_since = time.time()
            else:
                queued_since = None
            el = time.time() - t0
            print(f'  [{label}] {resp.status}, {el/60:.0f} мин', flush=True)
            if queued_since and time.time() - queued_since > QUEUE_STUCK_S:
                print(f'  [{label}] ЗАВИСЛО в очереди дольше {QUEUE_STUCK_S//60} мин — '
                      f'снимаю и подаю заново', flush=True)
                try:
                    c.responses.cancel(resp.id)
                except Exception as ex:
                    print(f'    отмена не удалась: {type(ex).__name__}: {str(ex)[:120]}',
                          flush=True)
                attempts += 1
                if attempts > MAX_RESUBMIT:
                    raise SystemExit(f'[{label}] задание зависало {attempts} раз подряд — '
                                     f'это не случайность, разбираться вручную')
                break
        else:
            if resp.status != 'completed':
                raise SystemExit(f'[{label}] статус {resp.status}, error={getattr(resp,"error",None)}, '
                                 f'incomplete={getattr(resp,"incomplete_details",None)}, id={resp.id}')
            if not resp.output_text:
                raise SystemExit(f'[{label}] completed, но output_text пуст; id={resp.id}')
            Path(out_path).write_text(header + resp.output_text + '\n', encoding='utf-8')
            print(f'  [{label}] готово за {(time.time()-t0)/60:.0f} мин, '
                  f'{len(resp.output_text):,} знаков -> {out_path}', flush=True)
            return resp.output_text


if __name__ == '__main__':
    import review_new_code as RN
    def build():
        items, _ = R.collect([ROOT / 'r33build/live', ROOT / 'r33build/transition.py',
                              ROOT / 'r33build/mr_engine.py', ROOT / 'r33build/sim_v13.py'])
        return RN.BRIEF + '\n' + ''.join(f'\n===== ФАЙЛ: {n} =====\n{t}\n' for n, t in items)
    watch('рецензия нового кода', build, ROOT / 'docs/kritika-novyy-kod.md',
          f'# Рецензия нового кода ред. 33 ({R.MODEL}, pro, effort=max)\n\n')
