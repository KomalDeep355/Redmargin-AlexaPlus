# Dataset and evaluation workflow

## Current data status

The repository contains synthetic postings for UI/tests only. `dataset/raw/` and
`dataset/processed/` are ignored and should not be committed by default. The optional
posting-skill baseline has been run on the local corpus, but no trained model artifact
is bundled or integrated into app decisions. The app excludes synthetic postings from
market percentages.

## Prepare job postings

Use a dataset whose license permits this use, and retain its source, version/date, and
license information in your project notes. Do not commit raw files, personal contact
details, or data whose license does not permit redistribution. Put CSV files in
`dataset/raw/`; expected columns are a job title and description, with optional company,
ID, and source skill labels (`job_skills`).

If the source provides separate job, summary, and skill tables named
`job_postings.csv`, `job_summary.csv`, and `job_skills.csv`, join them first:

```powershell
pip install -r requirements-dataset.txt
python -m dataset.organize_job_data --input-dir dataset/raw
```

This writes `dataset/processed/organized_postings.csv` and an organization report
alongside it. It joins on `job_link`, resolves duplicate summaries by keeping the first
non-empty value, deduplicates skill labels, removes duplicate job links and rows without
a title or description, and reports row counts. It does not use alternate-schema files
such as `job_skills (2).csv` (`job_id` / `skill_abr`) or infer labels.

From the repository root:

```powershell
python -m dataset.build_postings --input dataset/processed/organized_postings.csv --out dataset/processed/postings.jsonl
```

Repeat `--input` for additional CSVs. The builder normalizes text, filters short and
non-English records, classifies roles from titles, removes exact normalized duplicates,
and assigns deterministic 70/15/15 splits by company (or record ID when company is not
provided). Review the emitted role counts and source columns. Title classification and
CSV skill tags are weak/noisy labels; manually inspect them before drawing conclusions.

Inspect the normalized JSONL records and split/role counts before training:

```powershell
python -m dataset.check_processed
```

This diagnostic checks JSONL validity and the expected `title`, `text`, `skills`, `role`,
and `split` fields. It does not validate whether the weak source labels are correct.

To add a small, curated set to the app:

```powershell
python -m dataset.build_postings --input dataset/raw/postings.csv --export-app 15
```

Only export postings you are permitted to redistribute. The app will use those real
postings instead of synthetic samples for that role.

## Train a skill-extraction baseline

This task predicts source-provided skill tags from posting text. It is distinct from the
app's core repository-evidence review task. Install optional ML packages and run the
TF-IDF/logistic-regression baseline:

```powershell
pip install -r requirements-dataset.txt
python -m dataset.baseline_skill_extractor --postings dataset/processed/postings.jsonl --top-k 50
```

The fitted vectorizer, classifier, vocabulary, validation-selected probability
threshold, and training metadata are saved by default to
`models/skill_extractor.joblib`. Override the location with `--model-out`. This artifact
is ignored by Git by default. Joblib artifacts use Python pickle serialization; only
load model files you trust.

The label vocabulary and TF-IDF vectorizer are fitted on the training split only; the
held-out test split is used for final metrics. Compare future models on the same fixed
split, vocabulary, and metrics. Do not select hyperparameters against the test set. The
baseline needs at least 100 labelled postings and at least two training-supported labels.
It reports micro/macro F1, per-label scores, and test label occurrences not represented
in the training vocabulary. It is an offline experiment and does not alter app predictions.

## Compare model labels with the app taxonomy

Compare the model's selected labels in `baseline_report.json` with canonical skills and
synonyms in `data/skills.json`:

```powershell
python -m dataset.compare_skill_taxonomy
```

The audit normalizes case, spaces, and punctuation, then matches exact normalized aliases.
It writes `dataset/processed/taxonomy_mapping_report.json`. Unique matches are suggested
for review; labels matching multiple skills and labels with no taxonomy match are listed
separately for manual review. Taxonomy-wide alias collisions are also reported. This is
an offline mapping aid only: it does not change the taxonomy or app behavior, and these
model labels must not be used to manufacture posting counts or market-demand percentages.

### Local baseline run (exploratory)

The three source CSVs currently present in this workspace produced 12,217 joined
postings (12,212 with source skill labels). Redmargin's builder retained 1,223 records:
729 Data Scientist, 270 NLP/ML Engineer, 188 Software Engineer, and 36 Forward Deployed
Engineer postings. The baseline used 841 training records, 183 validation records, 199
test records, and the 50 most frequent training labels. The validation split selected a
global probability threshold of 0.20 (validation micro-F1 0.5305). On the held-out test
split it scored **0.5483 micro-F1** and **0.3335 macro-F1**. The test set contained 3,670
skill label occurrences outside the training vocabulary, illustrating substantial
label long-tail / coverage limitations.

The taxonomy comparison matched 22 of the selected 50 labels, found no collision among
those model labels, and left 28 unmatched for manual review. For example, `scikitlearn`
normalizes to the existing `scikit-learn` synonym and maps to `ml_frameworks`. No
colliding aliases were found in the current taxonomy. If a collision exists in a future
taxonomy, the audit reports its competing canonical skills rather than assigning it
automatically.

The split is deterministic by company. A local integrity check found 693 distinct
companies and no company or exact duplicate text represented across multiple splits.
The per-label report shows stronger scores for frequent labels such as Python (F1 0.87),
Machine Learning (0.80), and Data Science (0.70); labels such as Business Intelligence,
Engineering, Git, and Predictive Modeling scored F1 0.00. The headline
micro/macro F1 scores cover only the selected 50-label vocabulary: the 3,670
out-of-vocabulary test-label occurrences are reported separately and are not counted as
false negatives in those scores. Treat the F1 values as limited-coverage metrics, not
overall skill-label performance. The validation split is recorded but is not used by
the classifier fit; it is used to select the threshold, while the test split is reserved
for final metrics.

These are initial results from source-provided, unreviewed tags, not repository-skill
detection performance or an estimate of real-world generalization. The dataset license
and redistribution rights still need independent confirmation before publishing the
data or presenting these results as a validated benchmark. The report is written to
ignored `dataset/processed/baseline_report.json`. The fitted job-posting classifier is
saved locally at ignored `models/skill_extractor.joblib` by default; it is not loaded by
or integrated into app review decisions.

## Evaluate repository signals with human labels

Repository collection uses the public GitHub API and requires `GITHUB_TOKEN` in the
ignored `.env` file. It records only bounded repository snapshots and deterministic
signal outputs:

```powershell
python -m dataset.collect_repos --per-query 30 --stars 1..100
python -m dataset.label_silver
python -m dataset.make_gold_sheet --n 50 --seed 7
```

Silver labels are the system's own output, not ground truth. Review each generated repo
on GitHub and fill the `human_*` columns in
`dataset/processed/gold_labeling.csv` with `y` or `n`. Prefer independent second-reviewer
adjudication for a credible benchmark. Then measure signal performance:

```powershell
python -m dataset.eval_gold
```

Report sample sizes and class balance alongside accuracy, precision, recall, and the
confusion counts. A tiny or single-class sample is not a reliable estimate. Review
false positives and false negatives, fix the detector, and evaluate again on a fresh
holdout rather than tuning to the same gold labels.

## How the agent fits

The app already has an agent-assisted workflow. `app.reviewer` calculates signals,
skill matches, percentages, and fix estimates deterministically; `app.llm` can call an
OpenAI-compatible model to write the explanation. The prompt treats repository content
as untrusted, and the reviewer keeps only findings with citations to fetched paths,
signals, or skills. In mock mode the app uses a rule-based explanation and requires no
model key.

Use labelled postings to build market-demand context, not as ground truth about a
candidate. Use hand-reviewed repositories to validate detection. Before enabling a
trained extractor in the review path, compare it with the deterministic baseline on a
separate, manually labelled repository benchmark and preserve evidence-linked outputs;
never let an LLM invent numeric scores or unsupported resume achievements.

`data/roles.json` scopes the app's skill matrix to each target role and assigns
provisional `skill_weights` between 0 and 1. These human-authored priorities influence
fix ranking and the transparent score; they are not trained weights or validated hiring
criteria. The UI reports market demand only from real postings, labels configured
priorities separately, and withholds the score when there are no matched real-posting
skills. Replace and validate the provisional priorities against reviewed postings
before presenting them as authoritative.

For a strong public submission, publish the data source/license and collection date,
dataset and label counts, split strategy, baseline and held-out results, error analysis,
limitations, reproducible commands, a deployed demo, screenshots, and CI status. Do not
claim a winning result or model accuracy before those results exist.
