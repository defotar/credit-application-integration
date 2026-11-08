import os
import uuid
import httpx
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse

PREPROCESSING_URL = os.getenv("PREPROCESSING_URL", "http://preprocessing-service:8000")

app = FastAPI(title="Credit Application UI", version="2.0.0")

PAGE_STYLE = """
<style>
body { font-family: Arial, sans-serif; max-width: 960px; margin: 30px auto; line-height: 1.45; }
label { display: block; margin-top: 10px; font-weight: bold; }
input, select { width: 360px; padding: 8px; margin-top: 4px; }
button { margin-top: 16px; padding: 10px 16px; cursor: pointer; }
.card { border: 1px solid #ddd; border-radius: 8px; padding: 16px; margin: 16px 0; }
.ok { color: #0a7a0a; font-weight: bold; }
.warn { color: #b36b00; font-weight: bold; }
.err { color: #b00020; font-weight: bold; }
pre { background: #f6f6f6; padding: 12px; overflow: auto; }
</style>
"""


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"""
    <!doctype html>
    <html lang="ru">
    <head><meta charset="utf-8"><title>{title}</title>{PAGE_STYLE}</head>
    <body><h1>{title}</h1>{body}</body>
    </html>
    """)


@app.get("/", response_class=HTMLResponse)
def form() -> HTMLResponse:
    return page("Подача кредитной заявки", """
    <div class="card">
      <p>Идентификатор заявки будет сформирован автоматически после отправки формы.</p>
      <form method="post" action="/submit">
        <label>Возраст</label>
        <input name="age" type="number" value="25" min="18" max="75" required>

        <label>Ежемесячный доход, руб.</label>
        <input name="monthly_income" type="number" value="85000" min="1" required>

        <label>Ежемесячная долговая нагрузка, руб.</label>
        <input name="current_debt_payment" type="number" value="12000" min="0" required>

        <label>Запрашиваемая сумма кредита, руб.</label>
        <input name="requested_amount" type="number" value="150000" min="1" required>

        <label>Срок кредита, месяцев</label>
        <input name="requested_term_months" type="number" value="12" min="1" max="84" required>

        <label>Тип занятости</label>
        <select name="employment_type">
          <option value="employee">Наемный сотрудник</option>
          <option value="self_employed">Самозанятый / ИП</option>
          <option value="pensioner">Пенсионер</option>
          <option value="student">Студент</option>
          <option value="unemployed">Безработный</option>
        </select>

        <button type="submit">Отправить заявку</button>
      </form>
    </div>

    <div class="card">
      <h2>Проверка статуса существующей заявки</h2>
      <p>Введите идентификатор заявки, который был показан после отправки формы.</p>
      <form method="get" action="/status-view">
        <label>Идентификатор заявки</label>
        <input name="request_id" placeholder="REQ-XXXXXXXX" required>
        <button type="submit">Проверить по идентификатору</button>
      </form>
    </div>
    """)


@app.post("/submit")
async def submit(
    age: int = Form(...),
    monthly_income: float = Form(...),
    current_debt_payment: float = Form(...),
    requested_amount: float = Form(...),
    requested_term_months: int = Form(...),
    employment_type: str = Form(...),
) -> HTMLResponse:
    request_id = f"REQ-{uuid.uuid4().hex[:8].upper()}"
    payload = {
        "request_id": request_id,
        "age": age,
        "monthly_income": monthly_income,
        "current_debt_payment": current_debt_payment,
        "requested_amount": requested_amount,
        "requested_term_months": requested_term_months,
        "employment_type": employment_type,
        "missing_values": False,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(f"{PREPROCESSING_URL}/applications", json=payload)

    if response.status_code >= 400:
        return page("Ошибка подачи заявки", f"""
        <p class="err">Заявка не принята: {response.text}</p>
        <p><a href="/">Вернуться к форме</a></p>
        """)

    return page("Заявка принята", f"""
    <div class="card">
      <p class="ok">Заявка принята в обработку.</p>
      <p>Идентификатор заявки сформирован автоматически: <b>{request_id}</b></p>
      <p>Итоговый статус формируется системой принятия решений.</p>
      <form method="get" action="/status-view">
        <input type="hidden" name="request_id" value="{request_id}">
        <button type="submit">Проверить статус заявки</button>
      </form>
    </div>
    """)


@app.get("/status", response_class=HTMLResponse)
def status_form() -> HTMLResponse:
    return page("Проверка статуса заявки", """
    <form method="get" action="/status-view">
      <label>request_id</label>
      <input name="request_id" placeholder="REQ-001" required>
      <button type="submit">Проверить</button>
    </form>
    <p><a href="/">Подать новую заявку</a></p>
    """)


@app.get("/status-view")
def status_view(request_id: str) -> RedirectResponse:
    return RedirectResponse(url=f"/status/{request_id}", status_code=303)


@app.get("/status/{request_id}", response_class=HTMLResponse)
async def status(request_id: str) -> HTMLResponse:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{PREPROCESSING_URL}/status/{request_id}")
    data = response.json()
    status_value = data.get("status", "UNKNOWN")
    css = "ok" if status_value == "APPROVED" else "warn" if status_value in {"MANUAL_REVIEW", "PROCESSING"} else "err"
    return page("Статус кредитной заявки", f"""
    <div class="card">
      <p>Заявка: <b>{request_id}</b></p>
      <p>Статус: <span class="{css}">{status_value}</span></p>
      <p>Score: <b>{data.get('score', '-')}</b></p>
      <pre>{data}</pre>
    </div>
    <p><a href="/status/{request_id}">Обновить статус</a></p>
    <p><a href="/">Подать новую заявку</a></p>
    """)


@app.get("/health")
def health():
    return {"status": "ok"}
