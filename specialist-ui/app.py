import html as html_lib
import os

import httpx
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse

PREPROCESSING_URL = os.getenv("PREPROCESSING_URL", "http://preprocessing-service:8000")

app = FastAPI(title="Specialist UI", version="2.1.0")

STYLE = """
<style>
body { font-family: Arial, sans-serif; max-width: 1200px; margin: 30px auto; line-height: 1.45; }
table { border-collapse: collapse; width: 100%; margin-top: 16px; }
th, td { border: 1px solid #ddd; padding: 8px; vertical-align: top; }
th { background: #f2f2f2; }
.badge { color: #b36b00; font-weight: bold; }
.card { border: 1px solid #ddd; border-radius: 8px; padding: 14px; margin: 14px 0; }
button { padding: 8px 12px; cursor: pointer; margin: 4px 0; }
.approve { background: #e7f7e7; border: 1px solid #75b875; }
.reject { background: #fde8e8; border: 1px solid #d77; }
textarea { width: 260px; height: 55px; }
.small { color: #666; font-size: 13px; }
pre { white-space: pre-wrap; margin: 0; font-size: 13px; }
</style>
"""


def esc(value) -> str:
    return html_lib.escape(str(value if value is not None else ""))


def html(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"""
    <!doctype html>
    <html lang="ru">
    <head><meta charset="utf-8"><title>{title}</title>{STYLE}</head>
    <body><h1>{title}</h1>{body}</body>
    </html>
    """)


def source_data_view(source_data: dict) -> str:
    if not source_data:
        return "<span class='small'>Исходные данные не найдены</span>"
    labels = {
        "age": "Возраст",
        "monthly_income": "Доход, руб.",
        "current_debt_payment": "Долговая нагрузка, руб.",
        "requested_amount": "Сумма кредита, руб.",
        "requested_term_months": "Срок, мес.",
        "employment_type": "Тип занятости",
    }
    rows = []
    for key, label in labels.items():
        rows.append(f"<b>{label}:</b> {esc(source_data.get(key, '-'))}")
    return "<br>".join(rows)


def features_view(features: dict) -> str:
    if not features:
        return "<span class='small'>Признаки не рассчитаны</span>"
    labels = {
        "income_norm": "income_norm",
        "debt_ratio": "debt_ratio",
        "age_score": "age_score",
        "employment_score": "employment_score",
    }
    return "<br>".join(f"<b>{label}:</b> {esc(features.get(key, '-'))}" for key, label in labels.items())


@app.get("/", response_class=HTMLResponse)
async def manual_review() -> HTMLResponse:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{PREPROCESSING_URL}/manual-review")
    data = response.json()
    rows = ""
    for item in data.get("applications", []):
        request_id = esc(item.get("request_id"))
        rows += f"""
        <tr>
          <td><b>{request_id}</b></td>
          <td><span class="badge">{esc(item.get('status'))}</span><br>Score: <b>{esc(item.get('score'))}</b></td>
          <td>{source_data_view(item.get('source_data', {}))}</td>
          <td>{features_view(item.get('features', {}))}</td>
          <td>
            <form method="post" action="/review/{request_id}">
              <textarea name="comment" placeholder="Комментарий специалиста"></textarea><br>
              <button class="approve" name="decision" value="APPROVED" type="submit">Одобрить</button><br>
              <button class="reject" name="decision" value="REJECTED" type="submit">Отклонить</button>
            </form>
          </td>
        </tr>
        """
    if not rows:
        rows = "<tr><td colspan='5'>Нет заявок на ручную проверку</td></tr>"
    return html("АРМ кредитного специалиста", f"""
    <div class="card">
      <p>Количество заявок со статусом MANUAL_REVIEW: <b>{data.get('count', 0)}</b></p>
      <p class="small">В таблице отображаются исходные параметры заявки, расчетные признаки и действия специалиста.</p>
    </div>
    <table>
      <thead>
        <tr>
          <th>Заявка</th>
          <th>Решение системы</th>
          <th>Характеристики заявителя</th>
          <th>Расчетные признаки</th>
          <th>Решение специалиста</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    <p><a href="/">Обновить список</a></p>
    """)


@app.post("/review/{request_id}")
async def review_application(
    request_id: str,
    decision: str = Form(...),
    comment: str = Form(""),
) -> RedirectResponse:
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(
            f"{PREPROCESSING_URL}/manual-review/{request_id}/decision",
            json={"decision": decision, "comment": comment},
        )
    return RedirectResponse(url="/", status_code=303)


@app.get("/health")
def health():
    return {"status": "ok"}
