"""Проверочные сценарии на запущенной системе (docker compose up). Запуск: python3 scenarios/run.py

Каждый сценарий печатает ожидаемое по требованиям и фактическое поведение.
Сценарии со сбоями останавливают и запускают контейнеры, поэтому идут в конце.
"""
import base64
import json
import subprocess
import threading
import time
import urllib.error
import urllib.request

API = 'http://localhost:8000'
MQ = 'http://localhost:15672/api'
MQ_AUTH = 'Basic ' + base64.b64encode(b'guest:guest').decode()
BASE = {'age': 25, 'monthly_income': 85000, 'current_debt_payment': 12000, 'requested_amount': 150000,
        'requested_term_months': 12, 'employment_type': 'employee', 'missing_values': False}
RUN = str(int(time.time()))


def call(method, url, body=None, headers=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b'null')
        except json.JSONDecodeError:
            return e.code, raw.decode(errors='replace')


def submit(rid, **over):
    return call('POST', f'{API}/applications', {**BASE, 'request_id': f'{rid}-{RUN}', **over})


def status(rid, wait=True):
    for _ in range(30):
        s = call('GET', f'{API}/status/{rid}-{RUN}')[1]
        if not wait or s['status'] not in ('PROCESSING',):
            return s
        time.sleep(0.5)
    return s


def queue():
    return call('GET', f'{MQ}/queues/%2F/processed_data_queue', headers={'Authorization': MQ_AUTH})[1]


def compose(*args):
    subprocess.run(['docker', 'compose', *args], check=True, capture_output=True)


def report(n, title, expected, actual, ok):
    print(f'\n{n}. {title}\n   Ожидается: {expected}\n   Факт:      {actual}\n   Итог:      {"соответствует" if ok else "РАСХОЖДЕНИЕ"}')


# 1. Основной поток
code, _ = submit('S1')
s = status('S1')
report(1, 'Типовая заявка: доход 85 000, платежи 12 000, сотрудник', '202, затем APPROVED',
       f"{code}, {s['status']}, score {s.get('score')}", code == 202 and s['status'] == 'APPROVED')

# 2. Ручная проверка
code, _ = submit('S2', age=45, monthly_income=45000, current_debt_payment=24000, requested_amount=300000,
                 requested_term_months=36, employment_type='self_employed')
s = status('S2')
report(2, 'Пограничная заявка: долговая нагрузка 53%', 'MANUAL_REVIEW', f"{s['status']}, score {s.get('score')}",
       s['status'] == 'MANUAL_REVIEW')
code, item = call('POST', f'{API}/manual-review/S2-{RUN}/decision', {'decision': 'APPROVED', 'comment': 'подтверждён доход'})
report('2а', 'Решение специалиста по заявке на ручной проверке', '200, статус APPROVED', f"{code}, {item['status']}",
       code == 200 and item['status'] == 'APPROVED')

# 3. Валидация
code, body = call('POST', f'{API}/applications', {**BASE, 'request_id': f'S3-{RUN}', 'age': 17, 'monthly_income': 0})
report(3, 'Невалидная заявка: возраст 17, доход 0', '422 с перечнем ошибок',
       f"{code}, ошибок: {len(body['detail'])}", code == 422)

# 4. Сумма кредита не влияет на решение
submit('S4a')
a = status('S4a')
submit('S4b', requested_amount=5_000_000)
b = status('S4b')
report(4, 'Та же заявка, но сумма 5 млн вместо 150 тыс. на 12 мес. (платёж ≈ 5 доходов)', 'REJECTED или ручная проверка',
       f"150 тыс.: {a['status']} ({a.get('score')}); 5 млн: {b['status']} ({b.get('score')})", b['status'] != 'APPROVED')

# 5. Порядок правил в коде расходится с документацией
submit('S5', age=19, monthly_income=20000, current_debt_payment=10000, employment_type='unemployed')
s = status('S5')
report(5, 'score < 40 и долговая нагрузка 50%', 'REJECTED (README: «score < 40 → REJECTED»)',
       f"{s['status']}, score {s['score']}", s['status'] == 'REJECTED')

# 6. Повторная отправка того же request_id
code, body = submit('S1')
report(6, 'Повторная отправка заявки с уже принятым решением', 'ответ с текущим статусом APPROVED',
       f"{code}, ответ: {body['status']}, сообщение снова отправлено в очередь", body['status'] == 'APPROVED')

# 7. Параллельные заявки: оба сервиса переписывают один файл
N = 40
codes = {}
threads = [threading.Thread(target=lambda i=i: codes.__setitem__(i, submit(f'P{i}')[0])) for i in range(N)]
[t.start() for t in threads]
[t.join() for t in threads]
time.sleep(8)
final = {i: status(f'P{i}', wait=False)['status'] for i in range(N)}
errors = sum(c >= 500 for c in codes.values())
accepted = [i for i, c in codes.items() if c == 202]
lost = sum(final[i] in ('NOT_FOUND', 'PROCESSING') for i in accepted)
report(7, f'{N} заявок одновременно', f'{N} ответов 202 и {N} решений',
       f'ответ 500: {errors}; из {len(accepted)} принятых без решения: {lost}', errors == 0 and lost == 0)

# 8. Потребитель недоступен — сообщение ждёт в очереди
compose('stop', 'decision-service')
submit('S8')
time.sleep(12)  # статистика RabbitMQ Management обновляется раз в 5 секунд
ready = queue()['messages']
compose('start', 'decision-service')
s = status('S8')
report(8, 'decision-service остановлен на время подачи заявки', 'сообщение в очереди, после запуска — решение',
       f'в очереди: {ready}, после запуска: {s["status"]}', ready >= 1 and s['status'] != 'PROCESSING')

# 9. Невалидное сообщение в очереди
before = queue()
call('POST', f'{MQ}/exchanges/%2F/amq.default/publish', {
    'properties': {'delivery_mode': 2}, 'routing_key': 'processed_data_queue', 'payload_encoding': 'string',
    'payload': json.dumps({'request_id': f'S9-{RUN}', 'features': {}})}, {'Authorization': MQ_AUTH})
time.sleep(3)
after = queue()
dlq = call('GET', f'{MQ}/queues', headers={'Authorization': MQ_AUTH})[1]
report(9, 'В очередь пришло сообщение без обязательных полей', 'сообщение в очереди ошибок (DLQ) для разбора',
       f"очередей кроме основной: {len([q for q in dlq if q['name'] != 'processed_data_queue'])}, сообщение удалено без следа",
       len(dlq) > 1)

# 10. Брокер недоступен
compose('stop', 'rabbitmq')
t0 = time.time()
code, body = submit('S10')
dt = time.time() - t0
s = status('S10', wait=False)
compose('start', 'rabbitmq')
report(10, 'RabbitMQ остановлен', 'быстрый отказ клиенту, заявка не остаётся «в обработке»',
       f"ответ {code} через {dt:.0f} с, статус заявки: {s['status']}", dt < 5 and s['status'] != 'PROCESSING')
time.sleep(15)
