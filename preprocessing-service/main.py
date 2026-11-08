import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Literal

import pika
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
QUEUE_NAME = os.getenv("QUEUE_NAME", "processed_data_queue")
RESULTS_PATH = Path(os.getenv("RESULTS_PATH", "/data/results.json"))

app = FastAPI(title="Preprocessing Service", version="2.0.0")

EmploymentType = Literal["employee", "self_employed", "pensioner", "student", "unemployed"]


class CreditApplication(BaseModel):
    request_id: str = Field(..., min_length=1)
    age: int = Field(..., ge=18, le=75)
    monthly_income: float = Field(..., gt=0)
    current_debt_payment: float = Field(..., ge=0)
    requested_amount: float = Field(..., gt=0)
    requested_term_months: int = Field(..., ge=1, le=84)
    employment_type: EmploymentType
    missing_values: bool

    @field_validator("request_id")
    @classmethod
    def request_id_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("request_id must not be blank")
        return value.strip()


class ApplicationAccepted(BaseModel):
    request_id: str
    status: str
    message: str


class ReviewDecision(BaseModel):
    decision: Literal["APPROVED", "REJECTED"]
    comment: str = ""


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


def upsert_processing_record(application: CreditApplication) -> None:
    results = read_results()
    current = results.get(application.request_id, {})
    if current.get("status") in {"APPROVED", "MANUAL_REVIEW", "REJECTED"}:
        return
    results[application.request_id] = {
        "request_id": application.request_id,
        "status": "PROCESSING",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_data": application.model_dump(),
        "message": "Заявка принята в обработку"
    }
    write_results(results)


def calculate_features(application: CreditApplication) -> Dict[str, float]:
    income_norm = min(application.monthly_income / 150000.0, 1.0)
    debt_ratio = application.current_debt_payment / application.monthly_income

    if 21 <= application.age <= 60:
        age_score = 1.0
    else:
        age_score = 0.7

    employment_scores = {
        "employee": 0.8,
        "self_employed": 0.6,
        "pensioner": 0.5,
        "student": 0.4,
        "unemployed": 0.2,
    }
    employment_score = employment_scores[application.employment_type]

    return {
        "income_norm": round(income_norm, 4),
        "debt_ratio": round(debt_ratio, 4),
        "age_score": round(age_score, 4),
        "employment_score": round(employment_score, 4),
    }


def build_message(application: CreditApplication, features: Dict[str, float]) -> Dict[str, Any]:
    return {
        "request_id": application.request_id,
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "preprocessing_status": "SUCCESS",
        "features": features,
        "source": "credit_application_web_ui",
        "schema_version": "1.0",
    }


def publish_message(message: Dict[str, Any]) -> None:
    last_error = None
    for attempt in range(1, 11):
        try:
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(host=RABBITMQ_HOST, heartbeat=600, blocked_connection_timeout=300)
            )
            channel = connection.channel()
            channel.queue_declare(queue=QUEUE_NAME, durable=True)
            channel.basic_publish(
                exchange="",
                routing_key=QUEUE_NAME,
                body=json.dumps(message, ensure_ascii=False).encode("utf-8"),
                properties=pika.BasicProperties(
                    content_type="application/json",
                    delivery_mode=2,
                ),
            )
            connection.close()
            print(f"[preprocessing-service] Message published: request_id={message['request_id']}", flush=True)
            return
        except Exception as exc:
            last_error = exc
            print(f"[preprocessing-service] RabbitMQ unavailable, attempt {attempt}/10: {exc}", flush=True)
            time.sleep(2)
    raise RuntimeError(f"Cannot publish message to RabbitMQ: {last_error}")


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/applications", status_code=202, response_model=ApplicationAccepted)
def create_application(application: CreditApplication) -> ApplicationAccepted:
    if application.missing_values:
        raise HTTPException(status_code=400, detail="Заявка содержит признак пропущенных значений")

    features = calculate_features(application)
    message = build_message(application, features)
    upsert_processing_record(application)

    try:
        publish_message(message)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return ApplicationAccepted(
        request_id=application.request_id,
        status="PROCESSING",
        message="Заявка принята в обработку и передана в очередь сообщений",
    )


@app.get("/status/{request_id}")
def get_status(request_id: str) -> Dict[str, Any]:
    results = read_results()
    return results.get(
        request_id,
        {
            "request_id": request_id,
            "status": "NOT_FOUND",
            "message": "Заявка с указанным request_id не найдена",
        },
    )


@app.get("/manual-review")
def get_manual_review_applications() -> Dict[str, Any]:
    results = read_results()
    applications = [item for item in results.values() if item.get("status") == "MANUAL_REVIEW"]
    return {"count": len(applications), "applications": applications}


@app.post("/manual-review/{request_id}/decision")
def set_manual_review_decision(request_id: str, review: ReviewDecision) -> Dict[str, Any]:
    results = read_results()
    item = results.get(request_id)
    if not item:
        raise HTTPException(status_code=404, detail="Заявка с указанным request_id не найдена")

    if item.get("status") != "MANUAL_REVIEW":
        raise HTTPException(
            status_code=409,
            detail="Решение специалиста можно принять только по заявке со статусом MANUAL_REVIEW",
        )

    item["status"] = review.decision
    item["manual_review_required"] = False
    item["specialist_decision"] = review.decision
    item["specialist_comment"] = review.comment
    item["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    results[request_id] = item
    write_results(results)

    print(
        f"[preprocessing-service] Manual review completed: request_id={request_id}, decision={review.decision}",
        flush=True,
    )
    return item
