"""Score deterministic repository signals against hand-labelled gold data.

Usage: python -m dataset.eval_gold
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from .common import PROCESSED


def evaluate_rows(rows: list[dict[str, str]], fieldnames: list[str] | None) -> dict:
    if not fieldnames:
        raise ValueError("The gold-label CSV is empty or has no header.")
    signals = sorted(
        name[5:] for name in fieldnames
        if name.startswith("pred_") and f"human_{name[5:]}" in fieldnames
    )
    if not signals:
        raise ValueError("The gold-label CSV needs matching pred_<signal> and human_<signal> columns.")

    report = {}
    print(f"{'signal':24} {'n':>4} {'acc':>6} {'prec':>6} {'rec':>6}")
    for signal in signals:
        tp = fp = fn = tn = 0
        for row in rows:
            human = (row.get(f"human_{signal}") or "").strip().lower()
            if human not in ("y", "n"):
                continue
            prediction = (row.get(f"pred_{signal}") or "").strip().lower()
            if prediction not in ("y", "n"):
                raise ValueError(f"Invalid prediction for {signal}: expected 'y' or 'n'.")
            predicted_positive = prediction == "y"
            human_positive = human == "y"
            tp += predicted_positive and human_positive
            fp += predicted_positive and not human_positive
            fn += not predicted_positive and human_positive
            tn += not predicted_positive and not human_positive

        n = tp + fp + fn + tn
        if not n:
            continue
        accuracy = (tp + tn) / n
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        report[signal] = {
            "n": n,
            "accuracy": round(accuracy, 3),
            "precision": round(precision, 3) if precision is not None else None,
            "recall": round(recall, 3) if recall is not None else None,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "tn": tn,
        }
        display = lambda value: "  n/a" if value is None else f"{value:6.2f}"
        print(f"{signal:24} {n:4} {accuracy:6.2f} {display(precision)} {display(recall)}")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sheet", default=str(PROCESSED / "gold_labeling.csv"))
    parser.add_argument("--out", default=str(PROCESSED / "gold_report.json"))
    args = parser.parse_args()

    with open(args.sheet, encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        report = evaluate_rows(list(reader), reader.fieldnames)
    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nSaved {output_path}. Report the misses (fp/fn) honestly in your post.")


if __name__ == "__main__":
    main()
