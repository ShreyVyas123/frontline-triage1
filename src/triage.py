"""Core triage logic: one message in, one validated decision (plus metrics) out.

Flow per message:
  1. Handle empty / symbol-only input locally (no LLM call, no cost).
  2. Ask the model (temperature 0, JSON mode) and validate with Pydantic.
  3. Retry on invalid output or API errors; then fall back to a safe decision.
  4. Apply hard rules (rules.py). Never raises: one bad message can't crash a run.
"""
import json
import os
import re
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

from src.prompt import SYSTEM_PROMPT, build_user_prompt
from src.rules import apply_rules
from src.schema import Action, Category, Priority, TriageDecision, fallback_decision

load_dotenv()

MODEL_NAME = os.getenv("MODEL_NAME", "")
MAX_ATTEMPTS = 3      # 1 normal try + up to 2 retries
MAX_CHARS = 4000      # cap very long messages (cost + attack surface)

_client = None


def get_client():
    """Create the Gemini client once, on first use."""
    global _client
    if _client is None:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is missing in .env")
        _client = genai.Client(api_key=key)
    return _client


def call_llm(user_prompt: str):
    """The ONLY function that talks to the provider. Swap providers here."""
    response = get_client().models.generate_content(
        model=MODEL_NAME,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",  # ask for JSON, not prose
            temperature=0.0,                        # most consistent output
        ),
    )
    usage = response.usage_metadata
    tokens_in = getattr(usage, "prompt_token_count", 0) or 0
    tokens_out = (getattr(usage, "candidates_token_count", 0) or 0) + (
        getattr(usage, "thoughts_token_count", 0) or 0
    )
    return response.text or "", tokens_in, tokens_out


def parse_decision(raw: str) -> TriageDecision:
    """Turn model text into a validated TriageDecision, or raise ValueError."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    data = json.loads(text)
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    if isinstance(data, dict) and isinstance(data.get("summary"), str):
        data["summary"] = data["summary"][:300]  # trim instead of failing
    return TriageDecision.model_validate(data)  # enums + ranges enforced here


def local_decision(text: str):
    """Deterministic handling of input the LLM adds nothing to."""
    if not text.strip():
        return TriageDecision(
            category=Category.other, priority=Priority.P3,
            summary="Message is empty.",
            suggested_action=Action.no_action, needs_human=True, confidence=0.95,
        )
    if not re.search(r"[^\W_]", text):  # no letters or digits at all
        return TriageDecision(
            category=Category.other, priority=Priority.P3,
            summary="Message contains only symbols or emoji and no readable text.",
            suggested_action=Action.flag_for_review, needs_human=True, confidence=0.95,
        )
    return None


def triage_message(text) -> dict:
    """Triage one message. Always returns a record, never raises."""
    start = time.time()
    record = {
        "tokens_in": 0, "tokens_out": 0, "attempts": 0,
        "used_llm": False, "error": None, "flags": [],
    }
    try:
        if not isinstance(text, str):
            text = "" if text is None else str(text)
        truncated = len(text) > MAX_CHARS

        decision = local_decision(text)
        if decision is None:
            last_error = ""
            for attempt in range(1, MAX_ATTEMPTS + 1):
                record["attempts"] = attempt
                record["used_llm"] = True
                try:
                    raw, t_in, t_out = call_llm(build_user_prompt(text[:MAX_CHARS]))
                    record["tokens_in"] += t_in
                    record["tokens_out"] += t_out
                    decision = parse_decision(raw)
                    break
                except ValueError as e:  # bad JSON or failed validation
                    last_error = f"invalid model output: {e}"
                    print(f"  ! attempt {attempt} failed: {last_error[:120]}")
                except Exception as e:   # API / network / quota error
                    last_error = f"API error: {e}"
                    print(f"  ! attempt {attempt} failed: {last_error[:120]}")
                    time.sleep(5 * attempt)  # back off before retrying
            if decision is None:
                record["error"] = last_error
                decision = fallback_decision(last_error[:150].replace("\n", " "))

        decision, flags = apply_rules(text, decision)
        if truncated:
            flags.append(f"message truncated to {MAX_CHARS} chars")
        record["flags"] = flags
    except Exception as e:  # last safety net
        record["error"] = str(e)
        decision = fallback_decision(str(e)[:150].replace("\n", " "))

    record["decision"] = decision
    record["latency_s"] = round(time.time() - start, 2)
    return record