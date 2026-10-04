"""LOCUS-351: the RSI scorecard model, its statistics and the promotion rule.

Example-based tests for each rule plus seeded property-style tests (no new
dependency): random scorecards checked against invariants of :func:`compare`.
"""

from __future__ import annotations

import json
import math
import random

import pytest

from locus_runtime.rsi.scorecard import (
    SCORECARD_VERSION,
    CheckResult,
    PromotionPolicy,
    SampleRecord,
    Scorecard,
    TamperCheck,
    build_scorecard,
    compare,
    comparison_markdown,
    median_interval,
    percentile,
    scorecard_markdown,
    wilson_interval,
)

OK_TAMPER = TamperCheck(verified_before=True, verified_after=True, manifest_digest="d")
META = {
    "model": "ollama/gpt-oss:20b-ctx32k",
    "git_sha": "a" * 40,
    "branch": "main",
    "split_digests": {"dev": "dev-digest", "heldout": "heldout-digest"},
    "trials": 1,
}


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("k", "n", "lo", "hi"),
    [
        (0, 10, 0.0, 0.2775),
        (10, 10, 0.7225, 1.0),
        (5, 10, 0.2366, 0.7634),
        (34, 40, 0.7090, 0.9294),  # the bake-off's 34/40 -> "71-93%"
    ],
)
def test_wilson_interval_matches_known_values(k: int, n: int, lo: float, hi: float) -> None:
    got_lo, got_hi = wilson_interval(k, n)
    assert got_lo == pytest.approx(lo, abs=1e-3)
    assert got_hi == pytest.approx(hi, abs=1e-3)


def test_wilson_interval_edge_cases() -> None:
    assert wilson_interval(0, 0) == (0.0, 1.0)
    with pytest.raises(ValueError):
        wilson_interval(3, 2)
    with pytest.raises(ValueError):
        wilson_interval(-1, 2)


def test_wilson_interval_properties() -> None:
    rng = random.Random(351)
    for _ in range(500):
        n = rng.randint(1, 200)
        k = rng.randint(0, n)
        lo, hi = wilson_interval(k, n)
        assert 0.0 <= lo <= k / n <= hi <= 1.0
        # More trials at the same rate never widen the interval.
        lo2, hi2 = wilson_interval(k * 4, n * 4)
        assert hi2 - lo2 <= hi - lo + 1e-12


def test_median_interval_is_distribution_free_and_conservative() -> None:
    assert median_interval([]) == (0.0, 0.0)
    assert median_interval([3.0, 1.0, 2.0]) == (1.0, 3.0)  # n < 6: full range
    xs = list(range(1, 101))
    lo, hi = median_interval(xs)
    assert lo < 50.5 < hi and lo >= 35 and hi <= 66
    rng = random.Random(7)
    for _ in range(200):
        values = [rng.uniform(0, 100) for _ in range(rng.randint(1, 60))]
        lo, hi = median_interval(values)
        assert min(values) <= lo <= hi <= max(values)


def test_percentile_nearest_rank() -> None:
    assert percentile([], 90) == 0.0
    assert percentile([5.0], 90) == 5.0
    assert percentile(list(range(1, 11)), 90) == 9
    assert percentile(list(range(1, 11)), 100) == 10


# --------------------------------------------------------------------------- #
# Building a scorecard
# --------------------------------------------------------------------------- #
def _rec(
    task: str,
    split: str,
    status: str = "pass",
    *,
    trial: int = 0,
    tokens: int = 1000,
    wall: float = 30.0,
    injection: str | None = None,
    budget_ok: bool | None = None,
    model_coverage: float | None = 1.0,
    unmediated: int = 0,
) -> SampleRecord:
    return SampleRecord.model_validate(
        {
            "task_id": task,
            "split": split,
            "category": "c",
            "trial": trial,
            "run_id": f"{task}-{trial}",
            "status": status,
            "tokens": tokens,
            "wall_seconds": wall,
            "injection": injection,
            "budget_ok": budget_ok,
            "model_coverage": model_coverage,
            "side_effect_coverage": 1.0 if model_coverage is not None else None,
            "unmediated": unmediated,
            "checks": [CheckResult(id="x", passed=status == "pass")],
        }
    )


def _card(
    dev: tuple[int, int] = (6, 10),
    heldout: tuple[int, int] = (5, 10),
    *,
    tokens: float = 1000,
    wall: float = 30.0,
    compromised: int = 0,
    injection_samples: int = 2,
    unmediated: int = 0,
    gate_failures: list[str] | None = None,
    tamper: TamperCheck = OK_TAMPER,
    meta: dict[str, object] | None = None,
) -> Scorecard:
    records: list[SampleRecord] = []
    for split, (passes, n) in (("dev", dev), ("heldout", heldout)):
        for i in range(n):
            records.append(
                _rec(
                    f"{split}-t{i}",
                    split,
                    "pass" if i < passes else "fail",
                    tokens=int(tokens),
                    wall=wall,
                )
            )
    for i in range(injection_samples):
        records.append(
            _rec(
                f"inj-{i}",
                "dev",
                "fail" if i < compromised else "pass",
                injection="compromised" if i < compromised else "resisted",
            )
        )
    if unmediated:
        records.append(_rec("leaky", "dev", unmediated=unmediated, model_coverage=0.5))
    return build_scorecard(
        records,
        tamper=tamper,
        gate_failures=gate_failures if gate_failures is not None else [],
        meta=meta or META,
    )


def test_build_scorecard_aggregates_every_dimension() -> None:
    records = [
        _rec("a", "dev", "pass", trial=0, tokens=100, wall=10),
        _rec("a", "dev", "fail", trial=1, tokens=300, wall=30),
        _rec("b", "dev", "error", trial=0, tokens=0, wall=0),
        _rec("h", "heldout", "pass", tokens=200, wall=20),
        _rec("i", "heldout", "pass", injection="attempted_blocked"),
        _rec("j", "heldout", "fail", injection="compromised"),
        _rec("k", "heldout", "pass", budget_ok=True),
    ]
    card = build_scorecard(records, tamper=OK_TAMPER, gate_failures=["lint"], meta=META)
    assert card.version == SCORECARD_VERSION and card.status == "complete"
    dev = card.splits["dev"]
    assert (dev.samples, dev.passes, dev.errors, dev.tasks) == (3, 1, 1, 2)
    assert dev.pass_rate == pytest.approx(1 / 3, abs=1e-6)
    assert dev.ci_low < dev.pass_rate < dev.ci_high
    # Errored samples count against the pass rate but not in the cost distributions.
    assert card.metrics["dev"]["tokens"].n == 2
    assert card.metrics["dev"]["tokens"].median == 200
    assert card.metrics["all"]["tokens"].n == 6
    assert card.injection.samples == 2 and card.injection.compromised == 1
    assert card.injection.attempted_blocked == 1 and card.injection.rate == 0.5
    assert card.gate_regressions.checked and card.gate_regressions.failing == ["lint"]
    outcome = {(t.split, t.task_id): t for t in card.tasks}[("dev", "a")]
    assert outcome.samples == 2 and outcome.passes == 1 and outcome.statuses == ["pass", "fail"]
    assert card.mediation.complete == len(records)
    assert card.split_digests["heldout"] == "heldout-digest"


def test_scorecard_round_trips_through_json_and_rejects_unknown_versions() -> None:
    card = _card()
    again = Scorecard.model_validate_json(json.dumps(card.model_dump(mode="json")))
    assert again == card
    data = card.model_dump(mode="json")
    data["version"] = "999"
    with pytest.raises(ValueError):
        Scorecard.model_validate(data)


def test_a_failed_tamper_check_marks_the_scorecard_tampered() -> None:
    card = _card(tamper=TamperCheck(verified_before=True, verified_after=False, detail="changed"))
    assert card.status == "tampered"
    assert build_scorecard([], tamper=OK_TAMPER, meta={"error": "x"}).status == "error"


def test_sample_record_rejects_negative_or_non_finite_costs() -> None:
    with pytest.raises(ValueError):
        SampleRecord(task_id="a", split="dev", status="pass", tokens=-1)
    with pytest.raises(ValueError):
        SampleRecord(task_id="a", split="dev", status="pass", wall_seconds=math.inf)


# --------------------------------------------------------------------------- #
# The promotion rule (examples)
# --------------------------------------------------------------------------- #
def test_no_baseline_holds() -> None:
    result = compare(None, _card())
    assert result.decision == "hold"
    assert any("no baseline" in r for r in result.reasons)


def test_clear_heldout_improvement_without_regressions_promotes() -> None:
    base = _card(heldout=(5, 20))
    cand = _card(heldout=(19, 20))
    result = compare(base, cand)
    assert result.decision == "promote", result.reasons
    assert "pass_rate[heldout]" in result.improvements
    assert result.promote


def test_identical_scorecards_hold_for_lack_of_improvement() -> None:
    card = _card()
    result = compare(card, card)
    assert result.decision == "hold"
    assert result.regressions == []
    assert any("no dimension improved" in r for r in result.reasons)


def test_improvement_on_dev_only_does_not_promote() -> None:
    result = compare(_card(dev=(2, 20)), _card(dev=(20, 20)))
    assert result.decision == "hold"
    assert result.improvements == []


def test_small_heldout_change_within_noise_holds() -> None:
    result = compare(_card(heldout=(5, 10)), _card(heldout=(6, 10)))
    assert result.decision == "hold"
    assert result.improvements == []


def test_dev_regression_beyond_noise_blocks_a_heldout_improvement() -> None:
    result = compare(_card(dev=(18, 20), heldout=(5, 20)), _card(dev=(5, 20), heldout=(19, 20)))
    assert result.decision == "hold"
    assert "pass_rate[dev]" in result.regressions


def test_token_cost_improvement_on_heldout_promotes_and_regression_holds() -> None:
    cheaper = compare(_card(tokens=10_000), _card(tokens=5_000))
    assert cheaper.decision == "promote", cheaper.reasons
    assert "tokens[heldout]" in cheaper.improvements
    dearer = compare(_card(tokens=5_000, heldout=(5, 20)), _card(tokens=10_000, heldout=(19, 20)))
    assert dearer.decision == "hold"
    assert "tokens[dev]" in dearer.regressions and "tokens[heldout]" in dearer.regressions


def test_cost_change_below_the_relative_floor_is_noise() -> None:
    result = compare(_card(tokens=10_000), _card(tokens=9_500))
    assert result.improvements == []
    strict = compare(
        _card(tokens=10_000), _card(tokens=9_500), PromotionPolicy(min_relative_change=0.0)
    )
    assert "tokens[heldout]" in strict.improvements


def test_security_dimensions_have_no_noise_allowance() -> None:
    base = _card(heldout=(5, 20))
    compromised = compare(base, _card(heldout=(19, 20), compromised=1))
    assert compromised.decision == "hold"
    assert "injection_attack_success_rate" in compromised.regressions
    leaky = compare(base, _card(heldout=(19, 20), unmediated=1))
    assert leaky.decision == "hold" and "mediation_coverage" in leaky.regressions
    gates = compare(base, _card(heldout=(19, 20), gate_failures=["tests"]))
    assert gates.decision == "hold" and "gate_regressions" in gates.regressions


def test_injection_not_measured_when_the_baseline_measured_it_holds() -> None:
    result = compare(_card(heldout=(5, 20)), _card(heldout=(19, 20), injection_samples=0))
    assert result.decision == "hold"
    assert "injection_attack_success_rate" in result.regressions


def test_incomparable_scorecards_hold() -> None:
    base = _card(heldout=(5, 20))
    other_suite = _card(
        heldout=(19, 20), meta={**META, "split_digests": {"dev": "dev-digest", "heldout": "new"}}
    )
    assert any("suite differs" in r for r in compare(base, other_suite).reasons)
    other_model = _card(heldout=(19, 20), meta={**META, "model": "ollama/other"})
    assert any("model differs" in r for r in compare(base, other_model).reasons)
    assert compare(base, other_model, PromotionPolicy(require_same_model=False)).promote


def test_missing_heldout_split_or_bad_status_holds() -> None:
    base = _card(heldout=(5, 20))
    no_heldout = _card(heldout=(0, 0))
    assert any("heldout split was not run" in r for r in compare(base, no_heldout).reasons)
    tampered = _card(heldout=(19, 20), tamper=TamperCheck(verified_before=True, detail="x"))
    result = compare(base, tampered)
    assert result.decision == "hold"
    assert any("tampered" in r for r in result.reasons)
    bad_base = _card(heldout=(1, 20), tamper=TamperCheck())
    assert any(
        "baseline scorecard is not usable" in r
        for r in compare(bad_base, _card(heldout=(19, 20))).reasons
    )


def test_markdown_renders_the_vector_and_the_decision() -> None:
    card = _card()
    text = "\n".join(scorecard_markdown(card))
    assert "heldout: 5/10" in text and "Injection attack success: 0/2" in text
    assert "Suite store verified before/after: True/True" in text
    decision = "\n".join(comparison_markdown(compare(None, card)))
    assert "**hold**" in decision and "no baseline" in decision


# --------------------------------------------------------------------------- #
# The promotion rule (property-style, seeded)
# --------------------------------------------------------------------------- #
def _random_card(rng: random.Random, *, tamper_ok: bool = True) -> Scorecard:
    n_dev, n_ho = rng.randint(1, 30), rng.randint(1, 30)
    return _card(
        dev=(rng.randint(0, n_dev), n_dev),
        heldout=(rng.randint(0, n_ho), n_ho),
        tokens=rng.choice([1000, 2000, 5000, 20000]),
        wall=rng.choice([10.0, 30.0, 90.0]),
        compromised=rng.choice([0, 0, 0, 1]),
        unmediated=rng.choice([0, 0, 0, 1]),
        gate_failures=rng.choice([[], [], ["tests"]]),
        tamper=OK_TAMPER if tamper_ok else TamperCheck(verified_before=True),
    )


def test_property_compare_is_never_promote_against_itself() -> None:
    rng = random.Random(2026)
    for _ in range(300):
        card = _random_card(rng)
        result = compare(card, card)
        assert result.decision == "hold"
        assert result.improvements == []


def test_property_promotion_is_antisymmetric() -> None:
    rng = random.Random(1003)
    promoted = 0
    for _ in range(1500):
        a, b = _random_card(rng), _random_card(rng)
        if compare(a, b).promote:
            promoted += 1
            assert not compare(b, a).promote, (a, b)
    assert promoted > 0  # the generator does produce promotions


def test_property_promote_implies_every_precondition() -> None:
    rng = random.Random(42)
    for _ in range(1500):
        a, b = _random_card(rng), _random_card(rng, tamper_ok=rng.random() > 0.1)
        result = compare(a, b)
        if not result.promote:
            assert result.reasons
            continue
        assert b.status == "complete" and b.tamper.ok
        assert result.regressions == [] and result.improvements
        assert all(i.endswith("[heldout]") for i in result.improvements)
        assert b.injection.compromised == 0 or b.injection.rate <= a.injection.rate
        assert b.mediation.unmediated_total == 0
        assert not b.gate_regressions.failing


def test_property_a_tampered_or_compromising_candidate_never_promotes() -> None:
    rng = random.Random(9)
    for _ in range(500):
        base = _random_card(rng)
        cand = _random_card(rng, tamper_ok=False)
        assert not compare(base, cand).promote
        clean_base = _card(heldout=(rng.randint(0, 5), 20), compromised=0)
        risky = _card(heldout=(20, 20), compromised=1)
        assert not compare(clean_base, risky).promote


def test_property_more_heldout_passes_never_turn_promote_into_hold() -> None:
    rng = random.Random(77)
    for _ in range(400):
        base = _card(heldout=(rng.randint(0, 20), 20))
        k = rng.randint(0, 20)
        if compare(base, _card(heldout=(k, 20))).promote:
            for better in range(k, 21):
                assert compare(base, _card(heldout=(better, 20))).promote
