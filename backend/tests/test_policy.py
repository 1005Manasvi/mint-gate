"""Policy engine: risk mapping, status rules, score and decision."""

import hashlib
import json

import pytest

from detection.policy import FAIL, PASS, REVIEW, Policy

POLICY = {
    "version": "test-1",
    "weights": {"high": 0.4, "low": 0.3, "capped": 0.2, "advisory": 0.1},
    "checks": {
        "high": {"label": "higher is worse", "review": 0.5, "fail": 0.9},
        "low": {"label": "lower is worse", "review": 12, "fail": 4},
        "capped": {"label": "review-only", "review": 0.5, "fail": 0.9, "max_status": "REVIEW"},
        "advisory": {"label": "advisory", "review": 0.5, "fail": 0.9, "advisory": True},
    },
    "score": {"pass_min": 70, "review_min": 40},
}
CLEAN = {"high": 0.0, "low": 64, "capped": 0.0, "advisory": 0.0}


@pytest.fixture
def policy(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(POLICY))
    return Policy(path)


@pytest.mark.parametrize("value, risk", [(0.5, 0.5), (0.9, 1.0), (0.7, 0.75), (0.1, 0.0), (2.0, 1.0)])
def test_risk_higher_is_worse(policy, value, risk):
    assert policy.risk("high", value) == pytest.approx(risk)


@pytest.mark.parametrize("value, risk", [(12, 0.5), (4, 1.0), (8, 0.75), (64, 0.0), (0, 1.0)])
def test_risk_lower_is_worse(policy, value, risk):
    assert policy.risk("low", value) == pytest.approx(risk)


def test_status_thresholds(policy):
    assert policy.status("high", 0.49) == PASS
    assert policy.status("high", 0.5) == REVIEW
    assert policy.status("high", 1.0) == FAIL


def test_max_status_caps_at_review(policy):
    assert policy.status("capped", 1.0) == REVIEW
    assert policy.status("capped", 0.2) == PASS


def test_advisory_never_blocks(policy):
    assert policy.status("advisory", 1.0) == "INFO"
    assert policy.status("advisory", 0.1) == PASS
    decision, score, checks, _ = policy.decide({**CLEAN, "advisory": 1.0})
    assert checks["advisory"]["advisory"] is True
    assert decision == PASS and score == 90  # only lowers the score


def test_clean_upload_passes_with_full_score(policy):
    decision, score, checks, reasons = policy.decide(CLEAN)
    assert (decision, score, reasons) == (PASS, 100, ["all checks passed"])
    assert set(checks) == set(CLEAN)
    assert checks["high"]["review_at"] == 0.5 and checks["high"]["fail_at"] == 0.9


def test_hard_fail_on_any_failed_check(policy):
    decision, score, _, reasons = policy.decide({**CLEAN, "low": 0})
    assert decision == FAIL
    assert score == 70  # 100 * (1 - 0.3)
    assert "lower is worse crossed the hard-fail threshold" in reasons


def test_review_only_check_cannot_fail(policy):
    decision, score, checks, reasons = policy.decide({**CLEAN, "capped": 1.0})
    assert checks["capped"]["status"] == REVIEW
    assert decision == REVIEW and score == 80
    assert "review-only is suspicious" in reasons


def test_low_score_alone_triggers_review(policy):
    # every check below its review threshold, but together they cost 31 points
    values = {"high": 0.49, "low": 12.1, "capped": 0.49, "advisory": 0.9}
    decision, score, checks, reasons = policy.decide(values)
    assert all(c["status"] != REVIEW for c in checks.values())
    assert score < 70 and decision == REVIEW
    assert any("authenticity score" in r for r in reasons)


def test_very_low_score_fails(policy):
    values = {"high": 0.89, "low": 4.1, "capped": 1.0, "advisory": 1.0}  # nothing hard-fails
    decision, score, checks, reasons = policy.decide(values)
    assert not any(c["status"] == FAIL for c in checks.values())
    assert score < 40 and decision == FAIL
    assert reasons == [f"authenticity score {score} < 40"]


def test_sha256_identifies_exact_file(policy, tmp_path):
    raw = (tmp_path / "policy.json").read_bytes()
    assert policy.sha256 == hashlib.sha256(raw).hexdigest()
    assert policy.describe()["sha256"] == policy.sha256
    assert "calibration" not in policy.describe()


def test_shipped_policy_is_consistent():
    p = Policy()
    assert set(p.weights) == set(p.checks)
    assert sum(p.weights.values()) == pytest.approx(1.0)
    assert 0 < p.review_min < p.pass_min <= 100
    assert p.checks["resnet"].get("max_status") == REVIEW  # ResNet-18 must stay REVIEW-only
    for c in p.checks.values():
        assert c["review"] != c["fail"]
