"""Mint-Gate policy engine: turns raw check measurements into PASS / REVIEW / FAIL.

Policy lives in `policy.json` (thresholds are produced by
`training/calibrate.py` from data, not hand-picked). Its SHA-256 is recorded
on-chain with every verdict, so anyone can later prove which policy was
applied.

Per-check risk in [0, 1]:  0.5 at the `review` threshold, 1.0 at `fail`,
linear in between and beyond (clipped). Works for both directions because
`fail - review` is negative when lower values are worse (e.g. pHash distance).

Checks marked `"max_status": "REVIEW"` can send an upload to human review but
never FAIL it on their own (independent tests showed the ResNet-18 flags 36 %
of genuine art from NFT collections it has never seen).

Checks marked `"advisory": true` lower the authenticity score but can never
trigger REVIEW/FAIL on their own (status "INFO"). Calibration showed pHash and
ORB cannot separate genuine siblings from copies in a generative collection
(ROC-AUC < 0.5), so letting them block mints would reject genuine art.
"""
import hashlib
import json

from config import POLICY_FILE

PASS, REVIEW, FAIL = "PASS", "REVIEW", "FAIL"


class Policy:
    def __init__(self, path=POLICY_FILE):
        raw = path.read_bytes()
        self.data = json.loads(raw)
        self.version = self.data["version"]
        self.sha256 = hashlib.sha256(raw).hexdigest()
        self.checks = self.data["checks"]
        self.weights = self.data["weights"]
        self.pass_min = self.data["score"]["pass_min"]
        self.review_min = self.data["score"]["review_min"]

    def risk(self, name, value):
        c = self.checks[name]
        t = (value - c["review"]) / (c["fail"] - c["review"])
        return min(1.0, max(0.0, 0.5 + 0.5 * t))

    def status(self, name, risk):
        c = self.checks[name]
        if c.get("advisory"):
            return "INFO" if risk >= 0.5 else PASS
        s = FAIL if risk >= 1.0 else REVIEW if risk >= 0.5 else PASS
        # "max_status": "REVIEW" - the check may ask for a human, never auto-reject on its own
        # (used for detectors that do not generalise reliably to unseen art styles).
        return REVIEW if (s == FAIL and c.get("max_status") == REVIEW) else s

    def describe(self):
        """Public view of the policy for the API (no internal calibration data)."""
        return {"version": self.version, "sha256": self.sha256, "checks": self.checks,
                "weights": self.weights, "score": self.data["score"]}

    def decide(self, values: dict):
        """values: {check_name: measurement}. Returns (decision, score, per_check, reasons)."""
        per_check, reasons = {}, []
        for name, v in values.items():
            r = self.risk(name, v)
            per_check[name] = {"value": v, "risk": round(r, 4), "status": self.status(name, r),
                               "advisory": bool(self.checks[name].get("advisory")),
                               "review_at": self.checks[name]["review"],
                               "fail_at": self.checks[name]["fail"]}
        score = 100.0 * (1.0 - sum(self.weights[n] * c["risk"] for n, c in per_check.items()))
        score = int(round(max(0.0, min(100.0, score))))

        failed = [n for n, c in per_check.items() if c["status"] == FAIL]
        flagged = [n for n, c in per_check.items() if c["status"] == REVIEW]
        if failed:
            decision = FAIL
            reasons += [f"{self.checks[n]['label']} crossed the hard-fail threshold" for n in failed]
        elif score < self.review_min:
            decision = FAIL
            reasons.append(f"authenticity score {score} < {self.review_min}")
        elif flagged or score < self.pass_min:
            decision = REVIEW
            reasons += [f"{self.checks[n]['label']} is suspicious" for n in flagged]
            if score < self.pass_min:
                reasons.append(f"authenticity score {score} < {self.pass_min}")
        else:
            decision = PASS
            reasons.append("all checks passed")
        return decision, score, per_check, reasons
