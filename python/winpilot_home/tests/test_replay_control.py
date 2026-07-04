"""Unit tests for replay_control: interpolation + ctrl.if / ctrl.loop.

These exercise the control-flow orchestration with a tiny fake replayer — no
screen, no Windows APIs, no perception. Failure/stop semantics and loop bounds
are the focus.
"""
from winpilot.recorder import replay_control as rc


# --------------------------------------------------------------- interpolate
def test_interpolate_replaces_known_vars():
    assert rc.interpolate("hi ${name}", {"name": "alice"}) == "hi alice"


def test_interpolate_keeps_unknown_vars_verbatim():
    assert rc.interpolate("${missing}!", {}) == "${missing}!"


def test_interpolate_walks_lists_and_dicts():
    out = rc.interpolate(
        {"keys": ["ctrl", "${k}"], "cond": {"text_appears": "${who}"}},
        {"k": "s", "who": "Bob"},
    )
    assert out == {"keys": ["ctrl", "s"], "cond": {"text_appears": "Bob"}}


def test_interpolate_non_string_scalars_passthrough():
    assert rc.interpolate(5, {}) == 5
    assert rc.interpolate(True, {}) is True


def test_interpolate_step_skips_nested_step_lists():
    step = {
        "tool": "ctrl.if",
        "cond": {"text_appears": "${target}"},
        "then": [{"tool": "click", "text": "${target}"}],
    }
    out = rc.interpolate_step(step, {"target": "OK"})
    # cond is interpolated...
    assert out["cond"]["text_appears"] == "OK"
    # ...but the nested then-block is left untouched (interpolated lazily later)
    assert out["then"][0]["text"] == "${target}"


# ----------------------------------------------------------- fake replayer
class FakeReplayer:
    """Records executed leaf steps; scripts condition results in a queue."""

    def __init__(self, cond_results=None, stop_after=None):
        self.executed: list[str] = []
        self.cond_results = list(cond_results or [])
        self.cond_calls: list[dict] = []
        self._stop_after = stop_after  # stop once this many leaves ran
        self.emitted_ctrl: list[tuple] = []

    def _should_stop(self) -> bool:
        if self._stop_after is None:
            return False
        return len(self.executed) >= self._stop_after

    def _check_condition(self, cond: dict, timeout_s: float) -> bool:
        self.cond_calls.append(cond)
        return self.cond_results.pop(0) if self.cond_results else False

    def _emit_ctrl(self, idx, tool, ok, detail):
        self.emitted_ctrl.append((idx, tool, ok, detail.get("branch")))

    def _exec_steps(self, steps, prefix=""):
        """Each leaf step here is identified by its 'id' field for assertions.
        A step with fail=True returns False (aborts the block)."""
        for i, step in enumerate(steps, start=1):
            if self._should_stop():
                return False
            tool = step.get("tool", "")
            if tool == "ctrl.if":
                if not rc.exec_if(self, step, f"{prefix}{i}"):
                    return False
                continue
            if tool == "ctrl.loop":
                if not rc.exec_loop(self, step, f"{prefix}{i}"):
                    return False
                continue
            self.executed.append(step.get("id", tool))
            if step.get("fail"):
                return False
        return True


def leaf(node_id, **kw):
    return {"tool": "click", "id": node_id, **kw}


# ----------------------------------------------------------------- ctrl.if
def test_if_runs_then_when_cond_true():
    r = FakeReplayer(cond_results=[True])
    step = {"tool": "ctrl.if", "cond": {"text_appears": "x"},
            "then": [leaf("A")], "else": [leaf("B")]}
    assert rc.exec_if(r, step, "1") is True
    assert r.executed == ["A"]
    assert r.emitted_ctrl[0][3] == "then"


def test_if_runs_else_when_cond_false():
    r = FakeReplayer(cond_results=[False])
    step = {"tool": "ctrl.if", "cond": {"text_appears": "x"},
            "then": [leaf("A")], "else": [leaf("B")]}
    assert rc.exec_if(r, step, "1") is True
    assert r.executed == ["B"]


def test_if_false_without_else_is_ok_noop():
    r = FakeReplayer(cond_results=[False])
    step = {"tool": "ctrl.if", "cond": {"text_appears": "x"}, "then": [leaf("A")]}
    assert rc.exec_if(r, step, "1") is True
    assert r.executed == []


def test_if_no_cond_fails_instead_of_taking_then():
    r = FakeReplayer()
    step = {"tool": "ctrl.if", "then": [leaf("A")]}
    assert rc.exec_if(r, step, "1") is False
    assert r.executed == []
    assert r.emitted_ctrl[0][3] is None


def test_if_explicit_always_true_takes_then():
    r = FakeReplayer()
    step = {"tool": "ctrl.if", "cond": {"always": True}, "then": [leaf("A")]}
    assert rc.exec_if(r, step, "1") is True
    assert r.executed == ["A"]
    assert r.cond_calls == []


def test_if_propagates_branch_failure():
    r = FakeReplayer(cond_results=[True])
    step = {"tool": "ctrl.if", "cond": {"text_appears": "x"},
            "then": [leaf("A", fail=True), leaf("B")]}
    assert rc.exec_if(r, step, "1") is False
    assert r.executed == ["A"]  # B never ran


# --------------------------------------------------------------- ctrl.loop
def test_loop_count_runs_body_n_times():
    r = FakeReplayer()
    step = {"tool": "ctrl.loop", "loop_type": "count", "count": 3,
            "body": [leaf("X")]}
    assert rc.exec_loop(r, step, "1") is True
    assert r.executed == ["X", "X", "X"]


def test_loop_count_is_capped_by_max_iters():
    r = FakeReplayer()
    step = {"tool": "ctrl.loop", "loop_type": "count", "count": 100,
            "max_iters": 2, "body": [leaf("X")]}
    assert rc.exec_loop(r, step, "1") is True
    assert r.executed == ["X", "X"]


def test_loop_while_runs_until_cond_false():
    r = FakeReplayer(cond_results=[True, True, False])
    step = {"tool": "ctrl.loop", "loop_type": "while",
            "cond": {"text_appears": "next"}, "body": [leaf("P")]}
    assert rc.exec_loop(r, step, "1") is True
    assert r.executed == ["P", "P"]  # ran while true twice, stopped on false


def test_loop_until_runs_at_least_once_then_until_true():
    r = FakeReplayer(cond_results=[False, True])
    step = {"tool": "ctrl.loop", "loop_type": "until",
            "cond": {"text_appears": "done"}, "body": [leaf("Q")]}
    assert rc.exec_loop(r, step, "1") is True
    assert r.executed == ["Q", "Q"]  # body-first, checked after each iter


def test_loop_until_without_cond_aborts_instead_of_running_to_cap():
    r = FakeReplayer()
    step = {"tool": "ctrl.loop", "loop_type": "until", "body": [leaf("Q")]}
    assert rc.exec_loop(r, step, "1") is False
    assert r.executed == []


def test_loop_infinite_bounded_by_max_iters():
    r = FakeReplayer()
    step = {"tool": "ctrl.loop", "loop_type": "infinite", "max_iters": 4,
            "body": [leaf("L")]}
    assert rc.exec_loop(r, step, "1") is True
    assert r.executed == ["L", "L", "L", "L"]


def test_loop_body_failure_aborts():
    r = FakeReplayer()
    step = {"tool": "ctrl.loop", "loop_type": "count", "count": 5,
            "body": [leaf("Y", fail=True)]}
    assert rc.exec_loop(r, step, "1") is False
    assert r.executed == ["Y"]


def test_loop_stop_event_aborts_count():
    r = FakeReplayer(stop_after=2)
    step = {"tool": "ctrl.loop", "loop_type": "count", "count": 10,
            "body": [leaf("Z")]}
    assert rc.exec_loop(r, step, "1") is False
    assert r.executed == ["Z", "Z"]


def test_loop_infinite_stop_event_is_graceful_success():
    r = FakeReplayer(stop_after=3)
    step = {"tool": "ctrl.loop", "loop_type": "infinite", "max_iters": 999,
            "body": [leaf("W")]}
    assert rc.exec_loop(r, step, "1") is True
    assert r.executed == ["W", "W", "W"]


# --------------------------------------------------------------- count_leaves
def test_count_leaves_recurses_branches_and_loops():
    steps = [
        leaf("a"),
        {"tool": "ctrl.if", "then": [leaf("b"), leaf("c")], "else": [leaf("d")]},
        {"tool": "ctrl.loop", "loop_type": "count", "count": 3, "body": [leaf("e")]},
    ]
    assert rc.count_leaves(steps) == 7


def test_count_leaves_caps_count_loops_by_max_iters():
    steps = [
        {"tool": "ctrl.loop", "loop_type": "count", "count": 10, "max_iters": 2,
         "body": [leaf("a"), leaf("b")]},
        {"tool": "ctrl.loop", "loop_type": "while", "max_iters": 3,
         "body": [leaf("c")]},
        {"tool": "ctrl.loop", "loop_type": "until", "body": [leaf("d")]},
    ]
    assert rc.count_leaves(steps) == 8
