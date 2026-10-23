from app.github_client import RepoSnapshot, RepoError, parse_repo_url, choose_files
from app import config
from app.signals import compute_signals
from app.skills import build_skill_matrix, count_mentions, load_taxonomy
from app.postings import MIN_MARKET_POSTINGS
from app.planner import build_plan
from app.reviewer import is_valid_evidence, rule_based_critique, _validate_items
import pytest


def make_snap(tree, files=None, homepage=""):
    return RepoSnapshot("me", "proj", "main", "desc", homepage, 0, "", "Python", tree, False, files or {})


def test_parse_repo_url():
    assert parse_repo_url("https://github.com/a/b") == ("a", "b")
    assert parse_repo_url("https://github.com/a/b.git") == ("a", "b")
    for malformed in (
        "a/b",
        "not a repo",
        "https://github.com.evil.test/a/b",
        "https://example.com/a/b",
        "https://github.com/a/b/issues",
        "https://github.com/a/../b",
        "https://github.com/a/b?tab=readme",
        "http://github.com/a/b",
    ):
        with pytest.raises(RepoError):
            parse_repo_url(malformed)


def test_choose_files_skips_vendor_dirs_and_prefers_entrypoints():
    tree = ["README.md", "node_modules/x/index.js", "src/util.py", "main.py", "requirements.txt"]
    chosen = choose_files(tree)
    assert "node_modules/x/index.js" not in chosen
    assert chosen[0] == "README.md"
    assert chosen.index("main.py") < chosen.index("src/util.py")


def test_signals_detect_tests_ci_env():
    tree = ["README.md", "app.py", "tests/test_app.py", ".github/workflows/ci.yml", ".env"]
    sig = compute_signals(make_snap(tree, {"README.md": "Accuracy reached 92% on the test set."}))
    assert sig["has_tests"].value and sig["has_ci"].value
    assert sig["committed_env_file"].value and not sig["committed_env_file"].good_when_true
    assert sig["readme_has_metrics"].value
    assert not sig["has_license"].value


def test_planner_distinguishes_missing_and_minimal_readmes_and_demo_config():
    missing = make_snap([], {})
    missing_signals = compute_signals(missing)
    missing_plan = build_plan([], missing_signals, hours_per_day=2)
    missing_ids = {fix["fix_id"] for fix in missing_plan["fixes"]}
    assert "write_readme" in missing_ids
    assert "improve_readme" not in missing_ids

    minimal = make_snap(["README.md"], {"README.md": "A tiny README"})
    minimal_signals = compute_signals(minimal)
    minimal_plan = build_plan([], minimal_signals, hours_per_day=2)
    minimal_ids = {fix["fix_id"] for fix in minimal_plan["fixes"]}
    assert "improve_readme" in minimal_ids
    assert "write_readme" not in minimal_ids

    configured_deployment = make_snap(["render.yaml"], {}, homepage="")
    deployment_signals = compute_signals(configured_deployment)
    assert deployment_signals["has_deployment_config"].value
    assert not deployment_signals["has_live_demo"].value
    deployment_plan = build_plan([], deployment_signals, hours_per_day=2)
    assert "deploy_live" in {fix["fix_id"] for fix in deployment_plan["fixes"]}


def test_word_boundaries():
    tax = load_taxonomy()
    counts = count_mentions(tax, ["We write HTML and CSS.", "Experience with ML and SQL."])
    assert counts["ml_frameworks"] == 1  # "ml" in 2nd posting only, not inside "html"
    assert counts["sql_databases"] == 1


def test_taxonomy_regexes_are_validated_before_matrix_build(monkeypatch, tmp_path):
    (tmp_path / "skills.json").write_text(
        '[{"id":"broken","label":"Broken","synonyms":["broken"],'
        '"markers":{"paths":["["]},"fix_id":null}]',
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    with pytest.raises(ValueError, match="invalid path regex"):
        load_taxonomy()


def test_skill_dependencies_match_manifest_package_names():
    snap = make_snap(
        ["package.json", "requirements.txt"],
        {
            "package.json": '{"dependencies":{"react":"^18.0.0","not-react":"1.0.0"}}',
            "requirements.txt": "scikit_learn==1.5.0\nmy-fastapi-wrapper==1.0.0\n",
        },
    )
    matrix = {row["id"]: row for row in build_skill_matrix(
        snap, compute_signals(snap), ["React and FastAPI"] * MIN_MARKET_POSTINGS, "swe"
    )}
    nlp_matrix = {row["id"]: row for row in build_skill_matrix(
        snap, compute_signals(snap), ["scikit-learn"] * MIN_MARKET_POSTINGS, "nlp"
    )}
    assert matrix["frontend"]["status"] == "strength"
    assert matrix["rest_apis"]["status"] == "gap"
    assert nlp_matrix["ml_frameworks"]["status"] == "strength"
    assert "ml_frameworks" not in matrix and "frontend" not in nlp_matrix


def test_not_detectable_skill_is_not_reported_as_missing():
    snap = make_snap([], {})
    rows = build_skill_matrix(snap, compute_signals(snap), [], "fde")
    customer_skill = next(row for row in rows if row["id"] == "customer_communication")
    assert customer_skill["status"] == "not_detectable"
    assert customer_skill["evidence_status"] == "not_assessed"
    assert customer_skill["market_status"] == "insufficient_market_data"
    assert not customer_skill["evidence"]


def test_rule_based_gap_is_framed_as_missing_repository_evidence():
    result = rule_based_critique(
        "Data Scientist",
        {},
        [{
            "id": "statistics_experimentation",
            "label": "Statistics / experimentation",
            "status": "gap",
            "pct": 0.5,
            "postings_mentioning": 2,
            "total_postings": MIN_MARKET_POSTINGS,
            "evidence": [],
        }],
        {"total_hours_min": 0, "total_hours_max": 0},
    )
    assert result["findings"][0]["title"].startswith("No repository evidence found")
    assert "does not establish whether the candidate has the skill" in result["findings"][0]["detail"]
    assert "1 in-demand skill(s)" in result["summary"]


def test_rule_based_summary_discloses_unavailable_market_data():
    result = rule_based_critique(
        "Data Scientist",
        {},
        [{
            "id": "statistics_experimentation",
            "label": "Statistics / experimentation",
            "status": "market_unavailable",
            "pct": None,
            "postings_mentioning": 0,
            "total_postings": 0,
            "evidence": [],
        }],
        {"total_hours_min": 0, "total_hours_max": 0},
    )

    assert "at least 10 are needed for a market comparison" in result["summary"]
    assert "market-based skill gaps" in result["summary"]


def test_matrix_gap_strength_and_plan():
    tree = ["README.md", "main.py", "requirements.txt"]
    files = {"requirements.txt": "fastapi\nhttpx\n", "main.py": "from fastapi import FastAPI"}
    snap = make_snap(tree, files)
    sig = compute_signals(snap)
    postings = ["Python, FastAPI, Docker and pytest testing"] * MIN_MARKET_POSTINGS
    matrix = {r["id"]: r for r in build_skill_matrix(snap, sig, postings, "swe")}
    assert matrix["rest_apis"]["status"] == "strength"
    assert matrix["rest_apis"]["evidence_status"] == "evidence_found"
    assert matrix["rest_apis"]["market_status"] == "assessed"
    assert matrix["docker"]["status"] == "gap" and matrix["docker"]["pct"] == 1.0
    assert matrix["testing"]["status"] == "gap"

    plan = build_plan(list(matrix.values()), sig, hours_per_day=2)
    ids = [f["fix_id"] for f in plan["fixes"]]
    assert "add_dockerfile" in ids and "add_tests" in ids
    assert plan["total_hours_min"] > 0
    assert all(d["hours"] <= 2.0001 for d in plan["timetable"])
    assert abs(sum(d["hours"] for d in plan["timetable"]) - sum(f["hours_avg"] for f in plan["fixes"])) < 0.05


def test_small_posting_sample_does_not_create_market_gaps_or_fixes():
    snap = make_snap(["src/main.py"], {"src/main.py": "print('hello')"})
    signals = compute_signals(snap)
    matrix = build_skill_matrix(snap, signals, ["Python, Docker, and testing"], "swe")
    by_id = {row["id"]: row for row in matrix}

    assert by_id["docker"]["evidence_status"] == "no_evidence_found"
    assert by_id["docker"]["market_status"] == "insufficient_market_data"
    assert by_id["docker"]["status"] == "market_insufficient"
    assert by_id["docker"]["pct"] is None
    assert not any(row["status"] == "gap" for row in matrix)
    plan = build_plan(matrix, signals)
    assert not any(
        "postings mention" in reason
        for fix in plan["fixes"]
        for reason in fix["reasons"]
    )


def test_same_data_science_repository_is_assessed_differently_by_role():
    snap = make_snap(
        ["src/model.py", "requirements.txt", "tests/test_model.py"],
        {
            "src/model.py": "import pandas as pd\nfrom sklearn.model_selection import train_test_split",
            "requirements.txt": "pandas\nscikit-learn\nscipy\n",
        },
    )
    signals = compute_signals(snap)
    postings = [
        "Python, pandas, scikit-learn, statistics, REST APIs, automated testing"
    ] * MIN_MARKET_POSTINGS
    data_scientist = {
        row["id"]: row for row in build_skill_matrix(snap, signals, postings, "data_scientist")
    }
    software_engineer = {
        row["id"]: row for row in build_skill_matrix(snap, signals, postings, "swe")
    }

    assert data_scientist["data_analysis"]["status"] == "strength"
    assert data_scientist["ml_frameworks"]["status"] == "strength"
    assert data_scientist["statistics_experimentation"]["role_weight"] == 1.0
    assert "data_analysis" not in software_engineer
    assert software_engineer["testing"]["status"] == "strength"
    assert software_engineer["testing"]["role_weight"] == 1.0
    assert data_scientist["python"]["role_weight"] != software_engineer["python"]["role_weight"]


def test_evidence_validation_drops_invented_claims():
    tree, sigs, skills = {"main.py"}, {"has_tests"}, {"docker"}
    assert is_valid_evidence("main.py", tree, sigs, skills)
    assert is_valid_evidence("signal:has_tests", tree, sigs, skills)
    assert not is_valid_evidence("src/fake.py", tree, sigs, skills)
    kept, dropped = _validate_items(
        [{"title": "ok", "evidence": ["main.py", "nope.py"]}, {"title": "invented", "evidence": ["ghost.py"]}, {"title": "none"}],
        tree, sigs, skills,
    )
    assert len(kept) == 1 and kept[0]["evidence"] == ["main.py"] and dropped == 2
