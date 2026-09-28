"""Run the triage system on a messages file and show a CLI table.

Usage:
    python run.py                        # all messages -> results.json
    python run.py --limit 5              # only the first 5 messages
    python run.py --only-labelled        # only messages listed in data/ground_truth.json
    python run.py --resume               # skip messages already done in results.json
    python run.py --delay 8              # seconds between LLM calls (rate limits)

Results are saved after EVERY message, so you can stop (Ctrl+C) and resume safely.
"""
import argparse
import json
import os
import sys
import time

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from src.schema import TriageDecision
from src.triage import MODEL_NAME, triage_message

load_dotenv()
try:
    sys.stdout.reconfigure(encoding="utf-8")  # Hindi/Gujarati/emoji on Windows
except Exception:
    pass

console = Console()

# Example paid-tier prices per 1M tokens (USD). Check your model's real pricing.
# The free tier costs $0, so this shows what it WOULD cost on a paid plan.
COST_IN_PER_M = float(os.getenv("COST_IN_PER_M", "0.10"))
COST_OUT_PER_M = float(os.getenv("COST_OUT_PER_M", "0.40"))


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        console.print(f"[red]Could not read {path}: {e}[/red]")
        sys.exit(1)


def load_messages(path):
    """Load messages; tolerate odd entries instead of crashing."""
    data = read_json(path)
    if not isinstance(data, list):
        console.print("[red]Input file must contain a JSON list.[/red]")
        sys.exit(1)
    items = []
    for i, item in enumerate(data):
        if isinstance(item, dict):
            items.append((str(item.get("id", f"ROW{i + 1}")), item.get("text")))
        else:
            items.append((f"ROW{i + 1}", item))
    return items


def to_row(r):
    """Make a record JSON-safe (decision object -> plain dict)."""
    row = {k: v for k, v in r.items() if k != "decision"}
    d = r["decision"]
    row["decision"] = d.model_dump(mode="json") if hasattr(d, "model_dump") else d
    return row


def save(results, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump([to_row(r) for r in results], f, ensure_ascii=False, indent=2)


def load_previous(path):
    """For --resume: reuse earlier successful records, redo failed ones."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
    except Exception:
        return {}
    done = {}
    for row in rows:
        if row.get("error"):
            continue
        try:
            row["decision"] = TriageDecision.model_validate(row["decision"])
            done[row["id"]] = row
        except Exception:
            continue
    return done


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/messages.json")
    parser.add_argument("--output", default="results.json")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--delay", type=float, default=4.0,
                        help="seconds to wait between LLM calls (free-tier rate limits)")
    parser.add_argument("--resume", action="store_true",
                        help="skip messages already completed in the output file")
    parser.add_argument("--only-labelled", action="store_true",
                        help="only run messages listed in data/ground_truth.json")
    args = parser.parse_args()

    if not os.getenv("GEMINI_API_KEY") or not MODEL_NAME:
        console.print("[red]Set GEMINI_API_KEY and MODEL_NAME in .env first.[/red]")
        sys.exit(1)

    messages = load_messages(args.input)
    if args.only_labelled:
        wanted = {t["id"] for t in read_json("data/ground_truth.json")}
        messages = [m for m in messages if m[0] in wanted]
    if args.limit:
        messages = messages[: args.limit]
    total = len(messages)

    previous = load_previous(args.output) if args.resume else {}
    results = []
    try:
        for i, (msg_id, text) in enumerate(messages):
            if msg_id in previous:
                console.print(f"[dim]{i + 1}/{total}  {msg_id} (reused from earlier run)[/dim]")
                results.append(previous[msg_id])
                continue
            console.print(f"[dim]{i + 1}/{total}  {msg_id}[/dim]")
            record = triage_message(text)
            record["id"] = msg_id
            record["text"] = text if isinstance(text, str) else repr(text)
            results.append(record)
            save(results, args.output)  # checkpoint after every message
            if record["used_llm"] and i < total - 1:
                time.sleep(args.delay)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped. Progress is saved; continue with --resume.[/yellow]")

    if not results:
        return

    # ---------- table ----------
    table = Table(title="FRONTLINE triage results", show_lines=True)
    for col in ["ID", "Message", "Category", "Pri", "Human?", "Conf", "Action", "Summary"]:
        table.add_column(col, overflow="fold")
    pri_color = {"P0": "bold red", "P1": "orange3", "P2": "yellow", "P3": "green"}
    for r in results:
        d = r["decision"]
        preview = " ".join(r["text"].split())[:35] or "(empty)"
        human = "[red]YES[/red]" if d.needs_human else "[green]no[/green]"
        pri = f"[{pri_color[d.priority.value]}]{d.priority.value}[/]"
        table.add_row(r["id"], preview, d.category.value, pri, human,
                      f"{d.confidence:.2f}", d.suggested_action.value, d.summary)
    console.print(table)

    escalated = [r for r in results if r["decision"].needs_human]
    if escalated:
        console.print("\n[bold]Why messages were escalated (rule flags):[/bold]")
        for r in escalated:
            if r["flags"]:
                console.print(f"  {r['id']}: " + "; ".join(r["flags"]))
            else:
                console.print(f"  {r['id']}: model judged that a human is needed")

    # ---------- stats ----------
    llm_runs = [r for r in results if r["used_llm"]]
    t_in = sum(r["tokens_in"] for r in results)
    t_out = sum(r["tokens_out"] for r in results)
    cost = t_in / 1e6 * COST_IN_PER_M + t_out / 1e6 * COST_OUT_PER_M
    failed = [r for r in results if r["error"]]
    avg_lat = (sum(r["latency_s"] for r in llm_runs) / len(llm_runs)) if llm_runs else 0
    console.print("\n[bold]Run summary[/bold]")
    console.print(f"  Messages processed : {len(results)} of {total}")
    console.print(f"  Sent to LLM        : {len(llm_runs)} (rest handled locally)")
    console.print(f"  Needs human        : {len(escalated)}")
    console.print(f"  Fell back (errors) : {len(failed)}")
    console.print(f"  Avg latency / call : {avg_lat:.2f}s (includes retries and backoff)")
    console.print(f"  Tokens in / out    : {t_in} / {t_out}")
    if llm_runs:
        console.print(f"  Avg tokens / msg   : {(t_in + t_out) / len(llm_runs):.0f}")
        console.print(f"  Est. paid cost     : ${cost:.5f} total, ${cost / len(llm_runs):.6f} per message")

    save(results, args.output)
    console.print(f"\nSaved to [bold]{args.output}[/bold]")


if __name__ == "__main__":
    main()