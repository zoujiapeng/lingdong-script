"""Offline replay control flow + variable interpolation.

These helpers power the ``.wps/2`` control-flow steps (``ctrl.if`` / ``ctrl.loop``)
and ``${var}`` interpolation. They are deliberately dependency-free (stdlib
only) and never import perception/action/verify directly — every side effect
goes through methods on the ``replayer`` object passed in:

    replayer._check_condition(cond: dict, timeout_s: float) -> bool
    replayer._exec_steps(steps: list, prefix: str) -> bool
    replayer._should_stop() -> bool

That keeps this module fully unit-testable with a tiny fake replayer (no
screen, no Windows APIs) and keeps ``trace.py`` focused on leaf-action I/O.

Failure semantics mirror the linear replayer: a child block that returns
``False`` (a step failed verification) aborts the enclosing block and
propagates up, so ``reuse_skill`` can still locate the first failing step.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("winpilot.replay_control")

# Keys whose values are nested step lists — interpolation must NOT descend into
# them (each nested step is interpolated lazily when it actually executes).
NESTED_STEP_KEYS = frozenset({"then", "else", "body"})

_VAR_RE = re.compile(r"\$\{(\w+)\}")

# Hard backstop for unbounded loops even when a script omits max_iters.
_DEFAULT_MAX_ITERS = 1000


def interpolate(value: Any, variables: dict[str, str]) -> Any:
    """Recursively replace ``${name}`` in strings using ``variables``.

    Lists and dicts are walked; non-string scalars pass through unchanged. A
    missing variable is left verbatim (``${name}``) and logged, so a typo is
    visible rather than silently blanked.
    """
    if isinstance(value, str):
        def _sub(match: re.Match) -> str:
            name = match.group(1)
            if name in variables:
                return str(variables[name])
            logger.warning("插值变量未定义: ${%s}（保留原文）", name)
            return match.group(0)
        return _VAR_RE.sub(_sub, value)
    if isinstance(value, list):
        return [interpolate(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: interpolate(val, variables) for key, val in value.items()}
    return value


def interpolate_step(step: dict, variables: dict[str, str]) -> dict:
    """Interpolate every field of one step EXCEPT nested step lists.

    ``then`` / ``else`` / ``body`` are passed through untouched so their inner
    steps get interpolated when they actually run (with whatever the variables
    are by then — loops/branches can mutate them via ext.var_set)."""
    out: dict[str, Any] = {}
    for key, val in step.items():
        out[key] = val if key in NESTED_STEP_KEYS else interpolate(val, variables)
    return out


def _max_iters(step: dict) -> int:
    try:
        value = int(step.get("max_iters", _DEFAULT_MAX_ITERS))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_ITERS
    return value if value > 0 else _DEFAULT_MAX_ITERS


def exec_if(replayer: Any, step: dict, idx: str) -> bool:
    """``ctrl.if``: evaluate cond, run the ``then`` (or ``else``) sub-block."""
    cond = step.get("cond")
    if not isinstance(cond, dict) or not cond:
        replayer._emit_ctrl(idx, "ctrl.if", False,
                            detail={"error": "ctrl.if requires cond object"})
        return False
    timeout_s = float(step.get("timeout_s", 6.0))
    branch_taken = True if cond.get("always") is True else replayer._check_condition(cond, timeout_s)
    replayer._emit_ctrl(idx, "ctrl.if", branch_taken,
                        detail={"branch": "then" if branch_taken else "else"})
    block = step.get("then") if branch_taken else step.get("else")
    return replayer._exec_steps(block or [], prefix=f"{idx}.")


def exec_loop(replayer: Any, step: dict, idx: str) -> bool:
    """``ctrl.loop``: count / while / until / infinite, bounded by max_iters."""
    loop_type = step.get("loop_type", "count")
    body = step.get("body") or []
    cond = step.get("cond") or {}
    timeout_s = float(step.get("timeout_s", 6.0))
    cap = _max_iters(step)
    iters = 0

    def run_body() -> bool:
        return replayer._exec_steps(body, prefix=f"{idx}.{iters + 1}.")

    if loop_type == "count":
        try:
            target = int(step.get("count", 1))
        except (TypeError, ValueError):
            target = 1
        target = min(max(0, target), cap)
        while iters < target:
            if replayer._should_stop():
                return False
            if not run_body():
                return False
            iters += 1
        return True

    if loop_type == "while":
        if not cond:
            logger.warning("while loop requires cond; aborting instead of treating it as a successful no-op")
            return False
        while iters < cap:
            if replayer._should_stop():
                return False
            if not replayer._check_condition(cond, timeout_s):
                break
            if not run_body():
                return False
            iters += 1
        return True

    if loop_type == "until":
        if not cond:
            logger.warning("until loop requires cond; aborting instead of running to max_iters")
            return False
        while iters < cap:
            if replayer._should_stop():
                return False
            if not run_body():
                return False
            iters += 1
            if cond and replayer._check_condition(cond, timeout_s):
                break
        return True

    if loop_type == "infinite":
        while iters < cap:
            if replayer._should_stop():
                return True  # graceful stop is success for an open-ended loop
            if not run_body():
                return False
            iters += 1
        logger.info("infinite 循环到达 max_iters=%d 上限，停止", cap)
        return True

    logger.warning("未知 loop_type: %s（跳过）", loop_type)
    return False


def count_leaves(steps: list[dict]) -> int:
    """Total leaf (non-control) steps, recursing into branches/loops.

    Informational only — used for the replay "total" in the start event."""
    total = 0
    for step in steps:
        tool = step.get("tool", "")
        if tool == "ctrl.if":
            total += count_leaves(step.get("then") or [])
            total += count_leaves(step.get("else") or [])
        elif tool == "ctrl.loop":
            body_count = count_leaves(step.get("body") or [])
            loop_type = step.get("loop_type", "count")
            if loop_type == "count":
                try:
                    count = int(step.get("count", 1))
                except (TypeError, ValueError):
                    count = 1
                total += body_count * min(max(0, count), _max_iters(step))
            elif step.get("max_iters") is not None:
                total += body_count * _max_iters(step)
            else:
                total += body_count
        else:
            total += 1
    return total
