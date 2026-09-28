"""Hard rules applied AFTER the model answers.

Why this file exists: a prompt can be talked around, code cannot.
These rules can only make the system MORE cautious (force needs_human=True);
they never make it less cautious.
"""
import os
import re

from dotenv import load_dotenv

from src.schema import Priority, TriageDecision

load_dotenv()

try:
    THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.7"))
except ValueError:
    THRESHOLD = 0.7

LARGE_AMOUNT = 1000  # any money amount at or above this escalates

# --- 1. Prompt-injection / code-injection patterns -------------------------
INJECTION_PATTERNS = [
    r"ignore\s+(all\s+|any\s+)?(previous|prior|above|earlier|your)\s+(instructions|rules|prompt)",
    r"disregard\s+(all\s+|any\s+)?(previous|prior|above|your)",
    r"you\s+are\s+now\b",
    r"(admin|developer|god|dan)\s+mode",
    r"system\s*prompt",
    r"reveal\s+your\s+(instructions|prompt)",
    r"(print|show|repeat)\s+your\s+(full\s+)?(instructions|prompt)",
    r"\bSYSTEM\s*:",
    r"override\s+(the\s+)?(rules|priority|instructions)",
    r"\"?needs_human\"?\s*[:=]",
    r"\"?confidence\"?\s*[:=]\s*[\d.]+",
    r"drop\s+table|union\s+select|;\s*--",
]
INJECTION_RE = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]

# --- 2. High-stakes topics (always need a human) ---------------------------
STAKES_PATTERNS = {
    "legal threat": r"\b(legal action|lawyer|attorney|sue\b|lawsuit|court|data protection authority|gdpr)",
    "security issue": r"\b(hacked|unauthori[sz]ed|someone accessed|breach|stolen|fraud|phishing|login alert)",
    "safety hazard": r"\b(caught fire|on fire|overheat\w*|smoke|electric shock|exploded|burn(ed|t) )",
}
STAKES_RE = {k: re.compile(v, re.IGNORECASE) for k, v in STAKES_PATTERNS.items()}

MONEY_RE = re.compile(r"(?:[$₹€£]|rs\.?\s?)\s?(\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)


def _large_money(text: str) -> bool:
    for match in MONEY_RE.findall(text):
        try:
            if float(match.replace(",", "")) >= LARGE_AMOUNT:
                return True
        except ValueError:
            continue
    return False


def _invented_numbers(message: str, summary: str) -> list[str]:
    """Numbers of 3+ digits in the summary that never appear in the message.
    A cheap anti-hallucination check (catches invented order numbers, amounts)."""
    msg = message.replace(",", "")
    summ = summary.replace(",", "")
    return [n for n in re.findall(r"\d{3,}", summ) if n not in msg]


def apply_rules(message: str, decision: TriageDecision):
    """Return (possibly stricter decision, list of human-readable flags)."""
    d = decision.model_copy()
    flags: list[str] = []
    text = message or ""

    if any(p.search(text) for p in INJECTION_RE):
        flags.append("prompt/code-injection pattern detected")

    for label, pattern in STAKES_RE.items():
        if pattern.search(text):
            flags.append(label)

    if _large_money(text):
        flags.append(f"large money amount (>= {LARGE_AMOUNT})")

    invented = _invented_numbers(text, d.summary)
    if invented:
        flags.append(f"summary has numbers not in message: {', '.join(invented)}")

    if d.priority == Priority.P0:
        flags.append("P0 priority always needs human review")

    if d.confidence < THRESHOLD:
        flags.append(f"confidence {d.confidence:.2f} below {THRESHOLD}")

    if flags:
        d.needs_human = True  # rules can only escalate, never de-escalate
    return d, flags