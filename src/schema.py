from enum import Enum
from pydantic import BaseModel, Field


class Category(str, Enum):
    billing = "billing"
    technical_issue = "technical_issue"
    account_access = "account_access"
    shipping = "shipping"
    refund = "refund"
    feature_request = "feature_request"
    complaint = "complaint"
    general_inquiry = "general_inquiry"
    spam_or_abuse = "spam_or_abuse"
    other = "other"


class Priority(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class Action(str, Enum):
    route_to_billing = "route_to_billing"
    route_to_tech_support = "route_to_tech_support"
    route_to_account_team = "route_to_account_team"
    route_to_shipping = "route_to_shipping"
    send_faq_link = "send_faq_link"
    escalate_security = "escalate_security"
    ask_for_clarification = "ask_for_clarification"
    flag_for_review = "flag_for_review"
    no_action = "no_action"


class TriageDecision(BaseModel):
    category: Category
    priority: Priority
    summary: str = Field(max_length=300)
    suggested_action: Action
    needs_human: bool
    confidence: float = Field(ge=0.0, le=1.0)


def fallback_decision(reason: str) -> TriageDecision:
    """Safe output when the model fails or returns invalid data."""
    return TriageDecision(
        category=Category.other,
        priority=Priority.P2,
        summary=f"Automatic triage failed: {reason}",
        suggested_action=Action.flag_for_review,
        needs_human=True,
        confidence=0.0,
    )