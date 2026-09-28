import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.triage import triage_message


# ---------------------------------------------------------
# Paths
# ---------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"

KB_FILE = DATA_DIR / "knowledge_base.json"
ALERTS_FILE = DATA_DIR / "alerts.json"
OUTBOX_FILE = DATA_DIR / "outbox.json"


# ---------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------

app = FastAPI(
    title="FRONTLINE",
    description="AI customer-message triage and support routing system",
    version="1.0.0",
)


# ---------------------------------------------------------
# Request models
# ---------------------------------------------------------

class CustomerMessage(BaseModel):
    message: str = Field(min_length=1, max_length=10000)
    email: str = Field(default="demo@example.com")


class HealthResponse(BaseModel):
    status: str
    service: str


# ---------------------------------------------------------
# File helpers
# ---------------------------------------------------------

def read_json(path: Path, default):
    try:
        if not path.exists():
            return default

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception:
        return default


def write_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )


# ---------------------------------------------------------
# Knowledge base
# ---------------------------------------------------------

def load_knowledge_base():
    data = read_json(KB_FILE, {"articles": []})

    if not isinstance(data, dict):
        return []

    articles = data.get("articles", [])

    if not isinstance(articles, list):
        return []

    return articles


def find_approved_answer(message: str, category: str):
    """
    Find an answer ONLY inside the approved knowledge base.

    The system never asks the LLM to write the answer.
    """

    text = message.lower()
    articles = load_knowledge_base()

    candidates = [
        article
        for article in articles
        if article.get("category") == category
    ]

    best_article = None
    best_score = 0

    for article in candidates:
        keywords = article.get("keywords", [])

        score = 0

        for keyword in keywords:
            if keyword.lower() in text:
                score += 1

        if score > best_score:
            best_score = score
            best_article = article

    if best_article and best_score > 0:
        return {
            "available": True,
            "article_id": best_article.get("id"),
            "title": best_article.get("title"),
            "answer": best_article.get("answer"),
            "match_score": best_score,
        }

    return {
        "available": False,
        "article_id": None,
        "title": None,
        "answer": None,
        "match_score": 0,
    }


# ---------------------------------------------------------
# Department routing
# ---------------------------------------------------------

DEPARTMENT_MAP = {
    "billing": "Billing Operations",
    "technical_issue": "Technical Support",
    "account_access": "Account & Security",
    "shipping": "Shipping Operations",
    "refund": "Refunds & Payments",
    "feature_request": "Product Team",
    "complaint": "Customer Relations",
    "general_inquiry": "Customer Support",
    "spam_or_abuse": "Trust & Safety",
    "other": "General Support",
}


def get_department(category: str):
    return DEPARTMENT_MAP.get(category, "General Support")


# ---------------------------------------------------------
# Alert creation
# ---------------------------------------------------------

def create_human_alert(
    customer_email: str,
    message: str,
    decision,
    reason: str,
):
    alerts = read_json(ALERTS_FILE, [])

    alert = {
        "id": f"ALERT-{len(alerts) + 1:04d}",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "customer_email": customer_email,
        "message": message,
        "category": decision.category.value,
        "priority": decision.priority.value,
        "confidence": decision.confidence,
        "department": get_department(decision.category.value),
        "reason": reason,
        "status": "new",
    }

    alerts.append(alert)
    write_json(ALERTS_FILE, alerts)

    return alert


# ---------------------------------------------------------
# Simulated email
# ---------------------------------------------------------

def create_email(
    customer_email: str,
    message: str,
    decision,
    knowledge_answer,
):
    outbox = read_json(OUTBOX_FILE, [])

    email = {
        "id": f"EMAIL-{len(outbox) + 1:04d}",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "to": customer_email,
        "subject": "FRONTLINE support response",
        "body": knowledge_answer["answer"],
        "category": decision.category.value,
        "priority": decision.priority.value,
        "knowledge_base_article": knowledge_answer["article_id"],
        "status": "simulated",
        "original_message": message,
    }

    outbox.append(email)
    write_json(OUTBOX_FILE, outbox)

    return email


# ---------------------------------------------------------
# Main triage endpoint
# ---------------------------------------------------------

@app.post("/api/triage")
def triage_customer_message(request: CustomerMessage):

    message = request.message.strip()

    if not message:
        raise HTTPException(
            status_code=400,
            detail="Message cannot be empty."
        )

    # ---------------------------------------------
    # 1. Run existing FRONTLINE AI triage engine
    # ---------------------------------------------

    result = triage_message(message)

    decision = result["decision"]

    category = decision.category.value
    priority = decision.priority.value

    # ---------------------------------------------
    # 2. Check approved knowledge base
    # ---------------------------------------------

    knowledge_answer = find_approved_answer(
        message,
        category
    )

    # ---------------------------------------------
    # 3. Decide whether human intervention is needed
    # ---------------------------------------------

    human_reasons = list(result.get("flags", []))

    if decision.needs_human:
        human_required = True

        if not human_reasons:
            human_reasons.append(
                "AI determined that human review is required."
            )

    elif not knowledge_answer["available"]:
        human_required = True

        human_reasons.append(
            "No approved knowledge-base answer was found."
        )

    else:
        human_required = False

    # ---------------------------------------------
    # 4. Human path
    # ---------------------------------------------

    alert = None
    email = None

    if human_required:

        reason = "; ".join(human_reasons)

        alert = create_human_alert(
            customer_email=request.email,
            message=message,
            decision=decision,
            reason=reason,
        )

        customer_response = (
            "Your message has been received and routed to "
            f"{alert['department']}. A support representative "
            "will review it shortly."
        )

        system_path = "human"

    # ---------------------------------------------
    # 5. Automated path
    # ---------------------------------------------

    else:

        email = create_email(
            customer_email=request.email,
            message=message,
            decision=decision,
            knowledge_answer=knowledge_answer,
        )

        customer_response = (
            "Your message has been processed. "
            "A response based on our approved support information "
            "has been prepared."
        )

        system_path = "automated"

    # ---------------------------------------------
    # 6. Return complete UI response
    # ---------------------------------------------

    return {
        "success": True,
        "path": system_path,

        "triage": {
            "category": category,
            "priority": priority,
            "summary": decision.summary,
            "suggested_action": decision.suggested_action.value,
            "needs_human": human_required,
            "confidence": decision.confidence,
        },

        "department": get_department(category),

        "knowledge_base": knowledge_answer,

        "customer_response": customer_response,

        "human_alert": alert,

        "email": email,

        "metrics": {
            "used_llm": result["used_llm"],
            "tokens_in": result["tokens_in"],
            "tokens_out": result["tokens_out"],
            "latency_s": result["latency_s"],
            "attempts": result["attempts"],
            "flags": result["flags"],
        },
    }


# ---------------------------------------------------------
# Dashboard APIs
# ---------------------------------------------------------

@app.get("/api/alerts")
def get_alerts():
    return read_json(ALERTS_FILE, [])


@app.get("/api/outbox")
def get_outbox():
    return read_json(OUTBOX_FILE, [])


@app.get("/api/knowledge-base")
def get_knowledge_base():
    return load_knowledge_base()


@app.get("/api/health", response_model=HealthResponse)
def health():
    return {
        "status": "healthy",
        "service": "FRONTLINE"
    }


# ---------------------------------------------------------
# Frontend
# ---------------------------------------------------------

@app.get("/")
def home():
    return FileResponse(
        STATIC_DIR / "index.html"
    )