"""Measure how often the system agrees with your hand-labelled ground truth.

Usage:  python evaluate.py
Needs:  results.json (from run.py) and data/ground_truth.json
"""
import json
import sys

from rich.console import Console
from rich.table import Table

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

console = Console()
PRI_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        console.print(f"[red]Could not read {path}: {e}[/red]")
        sys.exit(1)


def main():
    truth = load("data/ground_truth.json")
    results = {r["id"]: r for r in load("results.json")}

    table = Table(title="System vs ground truth", show_lines=True)
    for col in ["ID", "Category (exp -> got)", "Priority (exp -> got)",
                "Human (exp -> got)", "Conf", "Result"]:
        table.add_column(col, overflow="fold")

    n = cat_ok = pri_ok = pri_near = hum_ok = all_ok = 0
    dangerous_misses = []
    conf_ok, conf_bad = [], []

    for t in truth:
        r = results.get(t["id"])
        if r is None:
            console.print(f"[yellow]{t['id']} not found in results.json (skipped)[/yellow]")
            continue
        d = r["decision"]
        n += 1
        c = d["category"] == t["category"]
        p = d["priority"] == t["priority"]
        near = abs(PRI_ORDER[d["priority"]] - PRI_ORDER[t["priority"]]) <= 1
        h = d["needs_human"] == t["needs_human"]
        cat_ok += c
        pri_ok += p
        pri_near += near
        hum_ok += h
        everything = c and p and h
        all_ok += everything
        (conf_ok if everything else conf_bad).append(d["confidence"])
        if t["needs_human"] and not d["needs_human"]:
            dangerous_misses.append(t["id"])

        def cell(exp, got, ok):
            return f"{exp} -> {got}" if ok else f"[red]{exp} -> {got}[/red]"

        table.add_row(
            t["id"],
            cell(t["category"], d["category"], c),
            cell(t["priority"], d["priority"], p),
            cell(t["needs_human"], d["needs_human"], h),
            f"{d['confidence']:.2f}",
            "[green]PASS[/green]" if everything else "[red]FAIL[/red]",
        )

    if n == 0:
        console.print("[red]No labelled messages matched results.json.[/red]")
        sys.exit(1)

    console.print(table)
    pct = lambda x: f"{x}/{n} = {100 * x / n:.0f}%"
    console.print("\n[bold]Agreement[/bold]")
    console.print(f"  Category exact match      : {pct(cat_ok)}")
    console.print(f"  Priority exact match      : {pct(pri_ok)}")
    console.print(f"  Priority within 1 level   : {pct(pri_near)}")
    console.print(f"  needs_human match         : {pct(hum_ok)}")
    console.print(f"  All three fields correct  : {pct(all_ok)}")
    console.print(
        f"  [bold]Dangerous misses[/bold] (human needed, system said no): "
        f"{len(dangerous_misses)} {dangerous_misses}"
    )
    if conf_ok and conf_bad:
        console.print(
            f"  Avg confidence when right : {sum(conf_ok) / len(conf_ok):.2f}   "
            f"when wrong: {sum(conf_bad) / len(conf_bad):.2f}"
        )

    with open("eval_report.json", "w", encoding="utf-8") as f:
        json.dump({
            "labelled": n, "category_acc": cat_ok / n, "priority_acc": pri_ok / n,
            "priority_within_one": pri_near / n, "needs_human_acc": hum_ok / n,
            "all_correct": all_ok / n, "dangerous_misses": dangerous_misses,
        }, f, indent=2)
    console.print("\nSaved to [bold]eval_report.json[/bold]")


if __name__ == "__main__":
    main()