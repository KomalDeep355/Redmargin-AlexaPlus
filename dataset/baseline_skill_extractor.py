
"""Train and evaluate a multi-label job-skill extraction baseline.

Run from the repository root:
    python -m dataset.baseline_skill_extractor --top-k 50

The test split is held out until the threshold is selected on validation data.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from .common import PROCESSED, read_jsonl


def normalize_skill(skill: object) -> str:
    return str(skill).strip().casefold()


def select_training_vocabulary(
    train_rows: list[dict], top_k: int
) -> list[str]:
    counts = Counter(
        normalize_skill(skill)
        for row in train_rows
        for skill in row.get("skills", [])
        if normalize_skill(skill)
    )
    return [label for label, _ in counts.most_common(top_k)]


def score_predictions(y_true, y_pred) -> dict:
    from sklearn.metrics import f1_score, precision_score, recall_score

    return {
        "micro_f1": round(
            f1_score(y_true, y_pred, average="micro", zero_division=0), 4
        ),
        "macro_f1": round(
            f1_score(y_true, y_pred, average="macro", zero_division=0), 4
        ),
        "micro_precision": round(
            precision_score(y_true, y_pred, average="micro", zero_division=0), 4
        ),
        "micro_recall": round(
            recall_score(y_true, y_pred, average="micro", zero_division=0), 4
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--postings", default=str(PROCESSED / "postings.jsonl"))
    parser.add_argument("--top-k", type=int, default=50)
    parser.add_argument("--min-df", type=int, default=2)
    parser.add_argument("--out", default=str(PROCESSED / "baseline_report.json"))
    parser.add_argument(
        "--model-out",
        default="models/skill_extractor.joblib",
        help="Path where the fitted model artifact will be saved.",
    )
    args = parser.parse_args()

    if args.top_k < 2:
        parser.error("--top-k must be at least 2")
    if args.min_df < 1:
        parser.error("--min-df must be positive")

    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import classification_report, f1_score
        from sklearn.multiclass import OneVsRestClassifier
        from sklearn.preprocessing import MultiLabelBinarizer
        import joblib
    except ImportError as exc:
        raise SystemExit(
            "Install dataset dependencies: pip install -r requirements-dataset.txt"
        ) from exc

    rows = [
        row for row in read_jsonl(Path(args.postings))
        if row.get("skills") and row.get("text")
    ]
    if len(rows) < 100:
        raise SystemExit(
            f"Only {len(rows)} labeled postings found; at least 100 are required."
        )

    train = [r for r in rows if r.get("split") == "train"]
    val = [r for r in rows if r.get("split") == "val"]
    test = [r for r in rows if r.get("split") == "test"]
    if not train or not val or not test:
        raise SystemExit("Train, validation, and test splits must all be nonempty.")

    vocabulary = select_training_vocabulary(train, args.top_k)
    if len(vocabulary) < 2:
        raise SystemExit("Fewer than two usable training labels.")

    known = set(vocabulary)

    def labels_for(row: dict) -> list[str]:
        return list(dict.fromkeys(
            normalize_skill(skill)
            for skill in row.get("skills", [])
            if normalize_skill(skill) in known
        ))

    y_train_rows = [labels_for(r) for r in train]
    y_val_rows = [labels_for(r) for r in val]
    y_test_rows = [labels_for(r) for r in test]

    binarizer = MultiLabelBinarizer(classes=vocabulary)
    y_train = binarizer.fit_transform(y_train_rows)
    y_val = binarizer.transform(y_val_rows)
    y_test = binarizer.transform(y_test_rows)

    # Remove labels with no positive examples in training.
    usable = y_train.sum(axis=0) > 0
    vocabulary = [
        label for label, keep in zip(vocabulary, usable) if keep
    ]
    y_train = y_train[:, usable]
    y_val = y_val[:, usable]
    y_test = y_test[:, usable]

    if len(vocabulary) < 2:
        raise SystemExit("Fewer than two labels occur in the training split.")

    vectorizer = TfidfVectorizer(
        ngram_range=(1, 2),
        min_df=args.min_df,
        max_features=50000,
        sublinear_tf=True,
    )
    try:
        x_train = vectorizer.fit_transform([r["text"] for r in train])
        x_val = vectorizer.transform([r["text"] for r in val])
        x_test = vectorizer.transform([r["text"] for r in test])
    except ValueError as exc:
        raise SystemExit(f"Could not build text features: {exc}") from exc

    classifier = OneVsRestClassifier(
        LogisticRegression(max_iter=300, C=4.0),
        n_jobs=-1,
    )
    classifier.fit(x_train, y_train)

    # Tune one global probability threshold on validation data only.
    val_probabilities = classifier.predict_proba(x_val)
    candidates = [round(x / 100, 2) for x in range(10, 91, 5)]
    threshold_scores = []
    for threshold in candidates:
        pred = (val_probabilities >= threshold).astype(int)
        threshold_scores.append(
            (f1_score(y_val, pred, average="micro", zero_division=0), threshold)
        )

    best_val_f1, best_threshold = max(threshold_scores)
    model_path = Path(args.model_out)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "vectorizer": vectorizer,
        "classifier": classifier,
        "vocabulary": list(vocabulary),
        "threshold": float(best_threshold),
        "metadata": {
            "method": "TF-IDF word/bigram + one-vs-rest logistic regression",
            "train_size": len(train),
            "validation_size": len(val),
            "labels": len(vocabulary),
            "threshold_selection": "validation split",
            "purpose": "job-posting skill extraction",
            "version": 1,
        },
    }
    joblib.dump(artifact, model_path)
    print(f"Saved model artifact to: {model_path.resolve()}")

    test_probabilities = classifier.predict_proba(x_test)
    test_predictions = (test_probabilities >= best_threshold).astype(int)

    per_label_report = classification_report(
        y_test,
        test_predictions,
        target_names=vocabulary,
        output_dict=True,
        zero_division=0,
    )

    per_role = {}
    for role in sorted({str(r.get("role", "unknown")) for r in test}):
        indices = [
            i for i, row in enumerate(test)
            if str(row.get("role", "unknown")) == role
        ]
        if indices:
            per_role[role] = {
                "postings": len(indices),
                **score_predictions(y_test[indices], test_predictions[indices]),
            }

    known_labels = set(vocabulary)
    report = {
        "method": "TF-IDF word/bigram features + one-vs-rest logistic regression",
        "train_size": len(train),
        "validation_size": len(val),
        "test_size": len(test),
        "labels": len(vocabulary),
        "selected_threshold": best_threshold,
        "validation_micro_f1_at_selected_threshold": round(best_val_f1, 4),
        "test_metrics": score_predictions(y_test, test_predictions),
        "per_role_test_metrics": per_role,
        "per_label": {label: per_label_report[label] for label in vocabulary},
        "test_label_occurrences_outside_training_vocabulary": sum(
            1
            for row in test
            for skill in row.get("skills", [])
            if normalize_skill(skill) not in known_labels
        ),
        "caveats": [
            "The label vocabulary is limited to the most frequent training labels.",
            "Threshold selection uses validation data; test data is used only for final evaluation.",
            "Per-role metrics may be unstable for roles with few test postings.",
            "This predicts skills from job descriptions; it does not evaluate GitHub repository evidence.",
        ],
    }

    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\nSaved report to: {output}")


if __name__ == "__main__":
    main()
