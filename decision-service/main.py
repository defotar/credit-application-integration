import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any

import pika

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
QUEUE_NAME = os.getenv("QUEUE_NAME", "processed_data_queue")
RESULTS_PATH = Path(os.getenv("RESULTS_PATH", "/data/results.json"))

FINAL_STATUSES = {"APPROVED", "MANUAL_REVIEW", "REJECTED"}


def ensure_results_file() -> None:
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not RESULTS_PATH.exists():
        RESULTS_PATH.write_text("{}", encoding="utf-8")


def read_results() -> Dict[str, Any]:
    ensure_results_file()
    try:
        return json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def write_results(results: Dict[str, Any]) -> None:
    ensure_results_file()
    tmp_path = RESULTS_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(RESULTS_PATH)


def calculate_score(features: Dict[str, float]) -> float:
    income_norm = float(features["income_norm"])
    debt_ratio = float(features["debt_ratio"])
    age_score = float(features["age_score"])
    employment_score = float(features["employment_score"])
    score = 100 * (
        0.4 * income_norm
        + 0.3 * (1 - debt_ratio)
        + 0.2 * age_score
        + 0.1 * employment_score
    )
    return round(score, 2)


def make_decision(score: float, debt_ratio: float) -> str:
    if score >= 70 and debt_ratio <= 0.45:
        return "APPROVED"
    if (40 <= score < 70) or (0.45 < debt_ratio <= 0.60):
        return "MANUAL_REVIEW"
    return "REJECTED"


def save_decision(message: Dict[str, Any], score: float, status: str) -> None:
    request_id = message["request_id"]
    results = read_results()
    existing = results.get(request_id, {})

    if existing.get("status") in FINAL_STATUSES:
        print(f"[decision-service] Duplicate message skipped: request_id={request_id}", flush=True)
        return

    results[request_id] = {
        "request_id": request_id,
        "status": status,
        "score": score,
        "manual_review_required": status == "MANUAL_REVIEW",
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "source_data": existing.get("source_data", {}),
        "features": message.get("features", {}),
        "source": message.get("source"),
        "schema_version": message.get("schema_version"),
    }
    write_results(results)


def validate_message(message: Dict[str, Any]) -> None:
    required_top = ["request_id", "processed_at", "preprocessing_status", "features", "source", "schema_version"]
    for field in required_top:
        if field not in message:
            raise ValueError(f"Missing field: {field}")
    required_features = ["income_norm", "debt_ratio", "age_score", "employment_score"]
    for field in required_features:
        if field not in message["features"]:
            raise ValueError(f"Missing feature: {field}")


def on_message(ch, method, properties, body) -> None:
    try:
        message = json.loads(body.decode("utf-8"))
        validate_message(message)
        features = message["features"]
        score = calculate_score(features)
        status = make_decision(score, float(features["debt_ratio"]))
        save_decision(message, score, status)
        print(
            "[decision-service] Получено и обработано сообщение: "
            f"request_id={message['request_id']}, score={score}, status={status}, features={features}",
            flush=True,
        )
        ch.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as exc:
        print(f"[decision-service] Ошибка обработки сообщения: {exc}", flush=True)
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)


def main() -> None:
    print(f"[decision-service] Waiting for RabbitMQ at host={RABBITMQ_HOST}", flush=True)
    while True:
        try:
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(host=RABBITMQ_HOST, heartbeat=600, blocked_connection_timeout=300)
            )
            channel = connection.channel()
            channel.queue_declare(queue=QUEUE_NAME, durable=True)
            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(queue=QUEUE_NAME, on_message_callback=on_message)
            print(f"[decision-service] Waiting for messages from {QUEUE_NAME}", flush=True)
            channel.start_consuming()
        except Exception as exc:
            print(f"[decision-service] RabbitMQ connection error: {exc}. Retry in 3 seconds", flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()
