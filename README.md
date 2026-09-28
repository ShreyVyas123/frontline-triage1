# FRONTLINE: AI Customer-Message Triage

Reads raw, messy customer messages and outputs one structured decision per message:

```json
{ "category": "billing", "priority": "P2", "summary": "...",
  "suggested_action": "route_to_billing", "needs_human": false, "confidence": 0.95 }
```

It is a backend decision engine, not a chatbot: it never replies to customers. It turns
unstructured, sometimes adversarial text into decisions software can act on, and it knows
when to call a human.

## Run it

```bash
python -m venv .venv
.venv\Scripts\activate                # Windows  (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
# create a .env file (never commit it) with:
#   GEMINI_API_KEY=your_key
#   MODEL_NAME=your_gemini_flash_model
#   CONFIDENCE_THRESHOLD=0.7
python run.py                         # 40 messages -> CLI table + results.json
python run.py --only-labelled         # only the hand-labelled messages (fast)
python run.py --resume                # continue an interrupted run
python evaluate.py                    # compare against data/ground_truth.json
```

## .env

GEMINI_API_KEY=API_KEY
MODEL_NAME=gemini-3.5-flash-lite
CONFIDENCE_THRESHOLD=0.7

```

## AI Decisions

**Model and tools.** [FILL: model name, e.g. Gemini Flash-Lite] through the Google Gemini
API (free tier), Python, Pydantic (schema validation), Rich (CLI table). The provider call
lives in one function (`call_llm` in `src/triage.py`), so switching providers is a
one-place change.

**Prompt strategy.** One system prompt containing: security rules first; explicit category
and priority definitions (so "P1" means the same thing every run); confidence bands (so
0.9 is not the default); a "no inventing details" rule; "anger alone does not raise
priority"; English-only summaries; and four few-shot examples, including an injection
mixed with a real issue. Temperature 0 and JSON mode give consistent output. The customer
message is wrapped in `<customer_message>` tags and treated as data. Our own tags are
stripped from the message so it cannot close the tag early.

**Uncertainty.** Three layers. (1) The model reports confidence and sets `needs_human`.
(2) `src/rules.py` overrides in code: confidence below the threshold (0.7, configurable),
legal / security / safety keywords, money amounts of 1000 or more, injection patterns,
summaries containing numbers not present in the message, and every P0 all force
`needs_human=true`. Rules can only escalate, never de-escalate. (3) Any failure falls back
to a safe object (`other`, `P2`, `flag_for_review`, `needs_human=true`, `confidence=0`).

**Bad input.** Empty, whitespace-only, and symbol/emoji-only messages are handled locally
with no LLM call (cheaper and deterministic). Very long messages are truncated to 4000
characters. Output is validated with Pydantic (enums and ranges); invalid output or API
errors are retried up to 3 times with backoff, then replaced by the fallback. Each message
is processed in isolation and results are saved after every message, so one failure never
stops or loses a run.

**Prompt injection.** Four layers: the message is treated as data (tags plus tag
stripping); the prompt tells the model to ignore embedded commands; regex detection in
code forces human review; and the test set includes direct, hidden, fake-JSON, prompt-leak
and SQL-style attacks. In the first full run all five attack messages (M13, M14, M31,
M32, M35) were resisted and escalated. In M14 (a real damaged-package complaint with an
attack attached) the system triaged the real issue and ignored the command.

**How I know it works.** I hand-labelled 12 messages (`data/ground_truth.json`) covering
clear, vague, sarcastic, non-English, injection, garbage, security, large-money and outage
cases, then ran `evaluate.py`. A 12-message sample is small, so treat percentages as
indicative, not precise.

| Metric | Before fixes | After fixes |
|---|---|---|
| Category exact match | 12/12 = 100% | [FILL] |
| Priority exact match | 11/12 = 92% (12/12 within one level) | [FILL] |
| needs_human match | 10/12 = 83% | [FILL] |
| All three fields correct | 9/12 = 75% | [FILL] |
| Dangerous misses (human needed, system said no) | 1 (M33) | [FILL] |

**Where it failed, and what I did.**
- **M33** (platform down for 3 hours): correct P0, but `needs_human=false` at 0.95
  confidence. The prompt defined an outage as P0 but never said P0 needs a human. Fix: a
  code rule that escalates every P0 (and the same line added to the prompt).
- **Confidence is not a reliable error signal.** Average confidence was 0.88 when right
  and 0.90 when wrong (small sample). Self-reported confidence cannot be the only safety
  mechanism, which is why the hard rules exist.
- **M10** (Hinglish, payment deducted but no order): system said P2, my label was P1.
  Within one level, and arguably a labelling judgement call.
- **M26** ("I never received my order"): system escalated at 0.80 confidence, my label said
  no human needed. Cautious but safe; the label is debatable.
- **Over-escalation.** 26 of 40 messages were marked `needs_human`. Part of that is right
  (the dataset is full of attacks and garbage), but clear messages like M02, M07 and M39
  were also escalated, which would swamp a real review queue. Hindi (M28) and Gujarati
  (M29) were escalated at 0.95 confidence with no rule firing; I added "non-English is not
  a reason to escalate" to the prompt. [FILL: did that change after the re-run?]

**Cost and latency (first full run, 37 LLM calls).** About 1,249 tokens per message
(43,702 in / 2,511 out in total), about 2.35 s per call, and about $0.000145 per message at
assumed paid rates of $0.10 in / $0.40 out per million tokens (the free tier costs $0;
verify current pricing for your model). About 95% of tokens are input, mostly the
system prompt re-sent with every message. **One idea to cut cost:** use the provider's
prompt caching (or shorten the examples) so the fixed prompt is not re-billed each time,
and screen obvious spam and injections with rules before the LLM. Three of 40 messages
were already handled locally with no LLM call.

**What I'd fix with more time.**
- A larger labelled set (50+) and a check of whether confidence can be calibrated.
- Reduce over-escalation so the human queue stays manageable.
- A cheaper model for easy messages, escalating to a stronger one for hard cases.
- A customer-facing web app: department dashboards with priority alerts, and auto-replies
  built only from an approved knowledge base, sent only when no human is needed.
- A tool call to look up order status so shipping and billing messages can be resolved
  with real data instead of being escalated.