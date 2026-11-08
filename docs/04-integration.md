# 4. Интеграция

## Канал

| Параметр | Значение | Зачем |
|---|---|---|
| Брокер | RabbitMQ, обменник по умолчанию | — |
| Очередь | `processed_data_queue`, `durable` | переживает перезапуск брокера |
| Сообщение | `delivery_mode = 2` (persistent), `application/json` | не теряется при перезапуске |
| Потребитель | `prefetch_count = 1`, ручное подтверждение после записи результата | сообщение не пропадёт, если потребитель упадёт во время обработки |
| Ошибка обработки | `basic_nack(requeue=False)`, **очереди ошибок нет** | бесконечный повтор исключён, но сообщение теряется — G-6 |
| Схема | [JSON Schema](../schemas/processed_message_schema.json), `schema_version = "1.0"` | версионирование контракта |
| Гарантия доставки | at-least-once; дубликаты отсекает проверка финального статуса у потребителя | — |

Контракты: [OpenAPI](api/openapi.yaml) для REST, [AsyncAPI](api/asyncapi.yaml) для очереди.

## Основной сценарий с ручной проверкой

```mermaid
sequenceDiagram
    autonumber
    actor C as Клиент
    participant UI as credit-application-ui
    participant P as preprocessing-service
    participant F as results.json
    participant Q as RabbitMQ
    participant D as decision-service
    actor S as Специалист

    C->>UI: форма заявки
    UI->>P: POST /applications
    P->>P: валидация, признаки
    P->>F: PROCESSING
    P->>Q: publish (persistent)
    P-->>UI: 202, request_id
    UI-->>C: номер заявки
    Q->>D: deliver
    D->>D: score = 52, debt_ratio = 0,53
    D->>F: MANUAL_REVIEW
    D->>Q: ack
    S->>P: GET /manual-review
    P->>F: читать
    P-->>S: список заявок
    S->>P: POST /manual-review/{id}/decision: APPROVED
    P->>F: APPROVED, комментарий, время
    C->>UI: проверить статус
    UI->>P: GET /status/{id}
    P-->>C: APPROVED
```

## Система решений недоступна — работает (сц. 8)

```mermaid
sequenceDiagram
    participant P as preprocessing-service
    participant Q as RabbitMQ
    participant D as decision-service
    Note over D: остановлен
    P->>Q: publish
    Note over Q: messages = 1
    D->>Q: запуск, подписка
    Q->>D: deliver
    D->>Q: ack
```

## Брокер недоступен — не работает (сц. 10)

```mermaid
sequenceDiagram
    actor C as Клиент
    participant P as preprocessing-service
    participant F as results.json
    participant Q as RabbitMQ
    Note over Q: остановлен
    C->>P: POST /applications
    P->>F: PROCESSING
    loop 10 попыток × 2 с
        P-xQ: connect
    end
    P-->>C: 503 через 20 с
    Note over F: заявка навсегда в PROCESSING,<br/>сообщения нет нигде
```

### TO-BE: outbox

```mermaid
sequenceDiagram
    actor C as Клиент
    participant P as Сервис заявок
    participant DB as PostgreSQL
    participant R as Релей outbox
    participant Q as RabbitMQ
    C->>P: POST /applications
    P->>DB: BEGIN; заявка + запись в outbox; COMMIT
    P-->>C: 202 за миллисекунды
    loop каждую секунду
        R->>DB: неотправленные записи outbox
        R->>Q: publish
        R->>DB: отметить отправленные
    end
```

Заявка и сообщение сохраняются атомарно. Брокер может лежать сколько угодно: клиент получает ответ сразу, сообщения уйдут после восстановления.
