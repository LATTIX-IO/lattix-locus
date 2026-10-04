"""``lattix loop report``: loop throughput and quality from the loop home (LOCUS-339).

Read-only. Built from the files the runner already writes under
``LOCUS_LOOP_HOME``: ``runs.jsonl`` (one line per finished run),
``eval-history.jsonl``, ``perf-history.jsonl`` / ``perf-baseline.json``,
``scorecard-history.jsonl`` (RSI scorecards, LOCUS-351) and the ledger
(``state.json``). :func:`build_report` is pure over the loaded records;
:func:`load_report` reads the files.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from locus_runtime.loop_runner.eval_gate import EvalHistory
from locus_runtime.loop_runner.perf_budget import METRICS, PerfStore
from locus_runtime.loop_runner.scorecard_gate import ScorecardHistory
from locus_runtime.loop_runner.state import Ledger, default_loop_home, read_run_history

_TREND_POINTS = 10
_STAMP = "%Y-%m-%dT%H:%M:%SZ"


def _parse(stamp: Any) -> datetime | None:
    try:
        return datetime.strptime(str(stamp or ""), _STAMP).replace(tzinfo=UTC)
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _within(
    records: Sequence[Mapping[str, Any]], key: str, since: datetime
) -> list[Mapping[str, Any]]:
    out = []
    for record in records:
        moment = _parse(record.get(key))
        if moment is not None and moment >= since:
            out.append(record)
    return out


def build_report(
    runs: Sequence[Mapping[str, Any]],
    evals: Sequence[Mapping[str, Any]],
    perf: Sequence[Mapping[str, Any]],
    *,
    perf_baseline: Mapping[str, float] | None = None,
    open_prs: Sequence[Mapping[str, Any]] = (),
    now: datetime | None = None,
    days: int = 30,
    scorecards: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """The report over the last ``days`` days (pure)."""
    moment = now or datetime.now(UTC)
    since = moment - timedelta(days=max(1, int(days)))
    window = _within(runs, "finished_at", since)
    outcomes = Counter(str(r.get("outcome") or "unknown") for r in window)
    user_stops = sum(
        1 for r in window if r.get("outcome") == "stopped" and str(r.get("kind") or "") == "user"
    )
    attempted = len(window) - user_stops
    done = outcomes.get("done", 0)
    per_day = Counter(str(r.get("finished_at") or "")[:10] for r in window)
    cost = 0.0
    tokens = 0
    for r in window:
        usage = r.get("usage") if isinstance(r.get("usage"), Mapping) else {}
        cost += _number((usage or {}).get("cost_usd")) or 0.0
        tokens += int(_number((usage or {}).get("tokens")) or 0)
    gate_failures: Counter[str] = Counter()
    for r in window:
        for check in r.get("gate_failures") or []:
            gate_failures[str(check)[:40]] += 1

    eval_window = _within(evals, "at", since)
    measured = [e for e in eval_window if _number(e.get("resolve_rate")) is not None]
    rates = [float(e["resolve_rate"]) for e in measured]
    eval_trend = [
        {"at": e.get("at"), "status": e.get("status"), "resolve_rate": e.get("resolve_rate")}
        for e in measured[-_TREND_POINTS:]
    ]
    eval_statuses = Counter(str(e.get("status") or "unknown") for e in eval_window)

    perf_window = _within(perf, "at", since)
    perf_trend: dict[str, Any] = {}
    for metric in METRICS:
        values = [
            (p.get("at"), v)
            for p in perf_window
            if (v := _number((p.get("metrics") or {}).get(metric))) is not None
        ]
        perf_trend[metric] = {
            "baseline_ms": (perf_baseline or {}).get(metric),
            "latest_ms": values[-1][1] if values else None,
            "points": [{"at": at, "median_ms": v} for at, v in values[-_TREND_POINTS:]],
        }
    perf_statuses = Counter(str(p.get("status") or "unknown") for p in perf_window)

    card_window = _within(scorecards, "at", since)
    card_measured = [c for c in card_window if _number(c.get("heldout_pass_rate")) is not None]
    heldout = [float(c["heldout_pass_rate"]) for c in card_measured]
    scorecard = {
        "runs": len(card_window),
        "statuses": dict(
            sorted(Counter(str(c.get("status") or "unknown") for c in card_window).items())
        ),
        "latest_heldout_pass_rate": heldout[-1] if heldout else None,
        "change": round(heldout[-1] - heldout[0], 4) if len(heldout) >= 2 else None,
        "trend": [
            {
                "at": c.get("at"),
                "status": c.get("status"),
                "git_sha": str(c.get("git_sha") or "")[:12],
                "heldout_pass_rate": c.get("heldout_pass_rate"),
                "dev_pass_rate": c.get("dev_pass_rate"),
            }
            for c in card_measured[-_TREND_POINTS:]
        ],
    }

    return {
        "window_days": max(1, int(days)),
        "generated_at": moment.strftime(_STAMP),
        "throughput": {
            "runs": len(window),
            "runs_per_day": dict(sorted(per_day.items())),
            "prs_opened": sum(1 for r in window if r.get("pr_url")),
            "open_loop_prs": len(open_prs),
        },
        "outcomes": dict(sorted(outcomes.items())),
        "success_rate": round(done / attempted, 4) if attempted else None,
        "cost": {
            "usd": round(cost, 6),
            "tokens": tokens,
            "note": "sum of usage cost_usd; about 0 on the NIM free tier and local Ollama",
        },
        "gate_failures_by_check": dict(gate_failures.most_common()),
        "eval": {
            "runs": len(eval_window),
            "statuses": dict(sorted(eval_statuses.items())),
            "latest_resolve_rate": rates[-1] if rates else None,
            "mean_resolve_rate": round(sum(rates) / len(rates), 4) if rates else None,
            "change": round(rates[-1] - rates[0], 4) if len(rates) >= 2 else None,
            "trend": eval_trend,
        },
        "perf": {"statuses": dict(sorted(perf_statuses.items())), "metrics": perf_trend},
        "scorecard": scorecard,
    }


def load_report(
    home: Path | None = None, *, days: int = 30, now: datetime | None = None
) -> dict[str, Any]:
    base = (home or default_loop_home()).resolve()
    store = PerfStore(base)
    report = build_report(
        read_run_history(base),
        EvalHistory(base).load(),
        store.history(),
        perf_baseline=store.baseline(),
        open_prs=Ledger.load(base).open_prs,
        now=now,
        days=days,
        scorecards=ScorecardHistory(base).load(),
    )
    report["home"] = str(base)
    return report


def _pct(value: Any) -> str:
    number = _number(value)
    return "n/a" if number is None else f"{number * 100:.1f}%"


def render_text(report: Mapping[str, Any]) -> str:
    """A short human-readable rendering of :func:`build_report`."""
    t = report.get("throughput") or {}
    ev = report.get("eval") or {}
    lines = [
        f"Locus loop report (last {report.get('window_days')} days)",
        f"  runs: {t.get('runs', 0)}  PRs opened: {t.get('prs_opened', 0)}  "
        f"open loop PRs: {t.get('open_loop_prs', 0)}",
        f"  outcomes: {report.get('outcomes') or {}}",
        f"  success rate: {_pct(report.get('success_rate'))}",
        f"  cost: ${(report.get('cost') or {}).get('usd', 0):.4f} "
        f"({(report.get('cost') or {}).get('tokens', 0)} tokens)",
        f"  gate failures by check: {report.get('gate_failures_by_check') or {}}",
        f"  eval: latest {_pct(ev.get('latest_resolve_rate'))}, mean "
        f"{_pct(ev.get('mean_resolve_rate'))}, statuses {ev.get('statuses') or {}}",
    ]
    card = report.get("scorecard") or {}
    lines.append(
        f"  RSI scorecard: latest held-out {_pct(card.get('latest_heldout_pass_rate'))}, "
        f"change {_pct(card.get('change'))}, statuses {card.get('statuses') or {}}"
    )
    for metric, data in ((report.get("perf") or {}).get("metrics") or {}).items():
        latest = _number(data.get("latest_ms"))
        base = _number(data.get("baseline_ms"))
        lines.append(
            f"  perf {metric}: latest "
            + (f"{latest:.2f} ms" if latest is not None else "n/a")
            + ", baseline "
            + (f"{base:.2f} ms" if base is not None else "n/a")
        )
    return "\n".join(lines)
