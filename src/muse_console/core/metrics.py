"""Tiny process-local metrics registry rendered in Prometheus text format.

No external dependency and no label cardinality from user input: callers pass
only fixed, code-defined label values (kind, outcome, account id).
"""

from __future__ import annotations

import threading
from collections import defaultdict

_BUCKETS = (1, 2.5, 5, 10, 20, 30, 45, 60, 120, 180, 300, 600)

_lock = threading.Lock()
_counters: dict[str, dict[tuple, float]] = defaultdict(lambda: defaultdict(float))
_hists: dict[str, dict[tuple, list]] = defaultdict(dict)
_help: dict[str, tuple[str, str]] = {}


def _key(labels: dict[str, str]) -> tuple:
    return tuple(sorted((k, str(v)) for k, v in labels.items()))


def inc(name: str, help_text: str, amount: float = 1.0, **labels: str) -> None:
    with _lock:
        _help.setdefault(name, ("counter", help_text))
        _counters[name][_key(labels)] += amount


def observe(name: str, help_text: str, value: float, **labels: str) -> None:
    with _lock:
        _help.setdefault(name, ("histogram", help_text))
        h = _hists[name].setdefault(_key(labels), [[0] * len(_BUCKETS), 0.0, 0])
        for i, bound in enumerate(_BUCKETS):
            if value <= bound:
                h[0][i] += 1
        h[1] += value
        h[2] += 1


def _fmt(labels: tuple, extra: tuple = ()) -> str:
    items = labels + extra
    if not items:
        return ""
    body = ",".join(
        f'{k}="{v.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'
        for k, v in items
    )
    return "{" + body + "}"


def render() -> str:
    lines: list[str] = []
    with _lock:
        for name in sorted(_help):
            kind, help_text = _help[name]
            lines += [f"# HELP {name} {help_text}", f"# TYPE {name} {kind}"]
            if kind == "counter":
                for labels, value in sorted(_counters[name].items()):
                    lines.append(f"{name}{_fmt(labels)} {value:g}")
                continue
            for labels, (buckets, total, count) in sorted(_hists[name].items()):
                for bound, n in zip(_BUCKETS, buckets, strict=True):
                    lines.append(f"{name}_bucket{_fmt(labels, (('le', f'{bound:g}'),))} {n}")
                lines.append(f"{name}_bucket{_fmt(labels, (('le', '+Inf'),))} {count}")
                lines.append(f"{name}_sum{_fmt(labels)} {total:g}")
                lines.append(f"{name}_count{_fmt(labels)} {count}")
    return "\n".join(lines) + "\n"


def reset() -> None:
    """Test helper."""
    with _lock:
        _counters.clear()
        _hists.clear()
        _help.clear()


# --- named helpers so call sites stay one line and names stay consistent ---


def media_outcome(kind: str, outcome: str) -> None:
    inc(
        "muse_media_outcomes_total",
        "Browser media generation events by kind and outcome code.",
        kind=kind,
        outcome=outcome,
    )


def first_token(seconds: float, account: str) -> None:
    observe(
        "muse_chat_first_token_seconds",
        "Seconds from lease to first chat delta.",
        seconds,
        account=account,
    )


def generation(kind: str, seconds: float, ok: bool) -> None:
    observe(
        "muse_generation_seconds",
        "Media generation wall time.",
        seconds,
        kind=kind,
        result="ok" if ok else "error",
    )


def upstream_result(op: str, account: str, ok: bool, code: str = "") -> None:
    inc(
        "muse_upstream_requests_total",
        "Gateway upstream calls by operation, account and result.",
        op=op,
        account=account,
        result="ok" if ok else (code or "error"),
    )
