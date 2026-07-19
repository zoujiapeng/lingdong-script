import json

from winpilot.agent.tools import ToolSession


def test_undo_last_without_run_owned_token_does_not_send_ctrl_z(monkeypatch):
    session = ToolSession(task="recover from the last action")
    monkeypatch.setattr(
        "winpilot.agent.system_tools.executor.press_keys",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("tokenless undo must not mutate the foreground window")
        ),
    )

    payload = json.loads(session._t_undo_last({}).text)

    assert payload == {
        "ok": False,
        "undone": False,
        "blocked": True,
        "error": "No reversible mutation token from this run is available.",
    }
