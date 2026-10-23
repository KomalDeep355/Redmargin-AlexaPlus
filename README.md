# Redmargin

**An evidence-first GitHub repository reviewer that turns skill gaps into actionable improvement plans.**

Freshers often can't tell which of their projects matches a job, or what is missing from it. Redmargin takes a public GitHub repo and a target role, compares the repo with what job postings ask for, shows where repository evidence was **not found**, and gives a ranked fix plan with a day-by-day timetable sized to the hours you can spend.

> Missing repository evidence is not proof that a candidate lacks a skill. Redmargin reports what it can detect and marks the rest "not assessed".

**Live demo:** https://redmargin.onrender.com

## Design rule

Numbers are deterministic (signals, percentages, hours). The LLM only writes the explanation, and every claim must cite evidence that can be checked. Unsupported claims are dropped and counted, which gives a measurable "how often did the model invent things" number.

## Features

- **Repo review:** fetches a bounded set of files through the GitHub API, with no cloning and no code execution.
- **Skill-evidence matrix:** compares the share of job postings that mention a skill with the evidence found in the repo.
- **Ranked fix plan and timetable:** gaps and hygiene issues become fixes with editable hour estimates.
- **How May I Help Desk:** builds a plan around the hours you can give a project, using curated learning resources (`app/agent.py`, `data/agent_resources.json`).
- **Interview questions:** grounded in the repository's actual files.
- **Resume bullets:** checked against repository evidence.
- **Hackathon estimate:** a weighted score, with a deterministic fallback when no model is configured.
- **Skill predictor:** `app/skill_predictor.py` loads the trained baseline model (`models/skill_extractor.joblib`) to predict skills from job-description text.
- **Mock mode:** works with no API key, using a rule-based critique.

## How it works

```
 Public repo URL + target role + hours available
                    |
 1. FETCH          GitHub API, bounded file set
 2. SIGNALS        tests, CI, deploy files, README quality, hygiene issues
 3. SKILL MATRIX   skills.json taxonomy + repo markers vs. job postings
 4. PLANNER        ranked fixes -> timetable sized to your hours
 5. LLM (optional) writes the explanation
 6. VALIDATOR      unsupported claims are dropped and counted
                    |
        Ranked fix plan + day-by-day timetable
```

Role priorities are human-configured. They are not learned from any dataset.

Supported roles (`data/roles.json`): Data Scientist, NLP/ML Engineer, Software Engineer, Forward Deployed Engineer.

## Quick start

### Windows (PowerShell)

```powershell
git clone https://github.com/KomalDeep355/Redmargin.git
cd Redmargin
py -m venv .venv
.venv\Scripts\Activate.ps1        # if blocked: Set-ExecutionPolicy -Scope Process Bypass
pip install -r requirements.txt
copy .env.example .env            # then edit .env
uvicorn app.main:app --reload
```

### macOS / Linux

```bash
git clone https://github.com/KomalDeep355/Redmargin.git
cd Redmargin
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # then edit .env
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>. Run the tests with `python -m pytest`.

## Configuration

| Setting | Purpose |
|---|---|
| `LLM_PROVIDER` | `mock` (default, no key) or `openai_compat` |
| Base URL, model, API key | for any OpenAI-compatible endpoint, such as local Ollama or Groq (see `.env.example`) |
| `GITHUB_TOKEN` | raises GitHub's anonymous limit of about 60 calls/hour |
| `MAX_PASTED_POSTINGS_CHARS` | limits pasted job-description text |
| `MAX_REPO_URL_CHARS` | limits the repo URL input |

Invalid numeric settings are logged and fall back to defaults. Limits on repository tree size, fetched-file count, GitHub concurrency and prompt text have upper bounds even if the environment sets them too high. Repository URLs must be HTTPS links to `github.com/owner/repository`.

## Deploy on Render

The root `render.yaml` defines a Python web service (`app.main:app`) with a `/api/health` check. Create a Blueprint from this repository and set secrets such as `GITHUB_TOKEN` in the Render dashboard, never in the repo. The service defaults to `LLM_PROVIDER=mock`.

## Data and the offline ML experiment

The live review pipeline is separate from the dataset and model-training workflow in `dataset/` (see `dataset/README.md`).

**Dataset preparation.** Source CSV files of job postings were joined on `job_link`, deduplicated, cleaned, normalized and classified into target roles.

- Initial join: 12,217 postings (12,212 with source-provided skill labels).
- After filtering and deduplication: 1,223 postings: Data Scientist 729, NLP/ML Engineer 270, Software Engineer 188, Forward Deployed Engineer 36.
- Split by company, not by row, to reduce leakage: 841 train, 183 validation, 199 test.
- Labels come from the source data and are noisy, not verified ground truth.

**Baseline model.** A multi-label skill extractor using TF-IDF and Logistic Regression, limited to the 50 most frequent training labels, with a global threshold of 0.20 chosen on the validation split.

| Metric | Result |
|---|---:|
| Validation micro-F1 | 0.5305 |
| Test micro-F1 | 0.5483 |
| Test macro-F1 | 0.3335 |

Full numbers are in `dataset/processed/baseline_report.json`.

**Limits.**
- The test data contained 3,670 skill-label occurrences outside the training vocabulary.
- The metrics cover only the 50-label vocabulary. They are not overall skill-extraction performance and not repository-review accuracy.
- The original dataset's source, collection date and redistribution licence still need confirmation, so the raw CSVs are **not** included in this repository.

**Pipeline scripts (`dataset/`).**

| Script | Purpose |
|---|---|
| `organize_job_data.py` | organizes the raw job-data CSVs |
| `build_postings.py` | builds the cleaned postings file |
| `check_processed.py`, `check_splits.py` | sanity checks on processed data and splits |
| `baseline_skill_extractor.py` | trains and evaluates the baseline model |
| `compare_skill_taxonomy.py` | maps dataset skills to the `skills.json` taxonomy |
| `label_silver.py`, `make_gold_sheet.py`, `eval_gold.py` | silver labeling and a hand-labelled gold benchmark workflow |
| `collect_repos.py` | collects repositories for evaluation |

## Important before you trust any output

- `data/postings/*/sample-*.txt` are **synthetic test data**. The UI warns you while they are in use. They are excluded from market percentages.
- Replace them with 10-15 real postings per role, or paste postings into the form, separated by a line containing `---`.
- Skill findings describe detectable evidence in the inspected repository only. Skills such as customer communication that can't be inferred from source are marked "not assessed".
- Fix hours in `data/fix_library.json` are rough defaults, not predictions.
- Only public repositories are supported.
- Market-based conclusions are withheld when there isn't enough real-posting evidence.

## Project structure

```
app/
  main.py              FastAPI routes and health checks
  config.py            settings from .env
  schemas.py           bounded API request and response models
  github_client.py     fetch repo tree + key files (no clone)
  signals.py           deterministic checks: tests, CI, Docker, README, committed .env ...
  skills.py            skill matrix: postings vs. repo evidence
  skill_predictor.py   skill prediction from job text (TF-IDF + Logistic Regression)
  postings.py          loads stored/pasted postings, flags sample data
  planner.py           gaps + hygiene -> ranked fixes -> timetable
  agent.py             Help Desk: plan personalized to available hours
  llm.py               OpenAI-compatible client + mock mode
  prompts.py           all prompt text
  reviewer.py          pipeline + evidence validation + rule-based fallback
  interview.py         repository-grounded interview questions
  resume_verdict.py    deterministic verdict + evidence-checked bullets
  judge.py             weighted hackathon estimate
data/
  skills.json          skill taxonomy
  fix_library.json     fixes with hour ranges and steps
  agent_resources.json learning resources used by the Help Desk
  roles.json           target roles
  postings/<role>/     sample job postings (data_scientist, fde, nlp, swe)
dataset/               offline data pipeline and model training
  processed/           cleaned postings and reports
models/
  skill_extractor.joblib   trained baseline skill extractor
static/                index.html, style.css, app.js
tests/                 test_core, test_agent, test_config, test_dataset,
                       test_reliability, test_review_pipeline, test_skill_predictor
render.yaml            Render blueprint
```

## How to extend

- **New skill:** add an entry to `data/skills.json` (synonyms to find it in postings, plus dependency, path, keyword or signal markers to find it in repos).
- **New repo check:** add one `_add(...)` line in `signals.py`, and a row in `HYGIENE` in `planner.py` if it should produce a fix.
- **New fix:** add it to `data/fix_library.json` and point a skill's `fix_id` at it.
- **New learning resource:** add it to `data/agent_resources.json`.
- **New role:** add it to `data/roles.json` and create `data/postings/<id>/`.

## Roadmap

1. Confirm the dataset's source and licence, and review the noisy labels.
2. Collect 10-15 real postings per role.
3. Finish the human-labelled gold benchmark and report how often Redmargin finds real gaps.
4. Compare the offline skill model with the deterministic baseline before it influences any live decision.
5. Add recruiter-facing evaluation, a possible future direction that is not built yet.

## License

Released under the [MIT License](LICENSE).
