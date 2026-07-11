import json


class FakeRecorder:
    def __init__(self, scripts_dir):
        self.scripts_dir = scripts_dir
        self.recording = False
        self.name = ""

    def is_recording(self):
        return self.recording

    def start(self, name):
        self.name = name
        self.recording = True

    def stop(self):
        self.recording = False
        path = self.scripts_dir / "lesson.wps.json"
        path.write_text(
            json.dumps({
                "format": "wps/2",
                "name": self.name,
                "source": "human",
                "steps": [{"tool": "click", "text": "Send", "coords": [100, 200]}],
            }),
            encoding="utf-8",
        )
        return {"ok": True, "file": path.name, "steps": 1}


def test_apprentice_lesson_becomes_editable_script_and_provisional_skill(tmp_path, monkeypatch):
    from winpilot.recorder import apprentice

    captured = []
    fake = FakeRecorder(tmp_path)
    monkeypatch.setattr(apprentice, "SCRIPTS_DIR", tmp_path)
    monkeypatch.setattr(
        apprentice.experience,
        "record",
        lambda *args, **kwargs: captured.append((args, kwargs)) or {
            "id": "skill-candidate",
            "script": "lesson.automation.json",
            "provisional": True,
        },
    )
    manager = apprentice.ApprenticeManager(fake)

    started = manager.start("Send a message", app="QQ")
    assert started["recording"] is True
    assert manager.status()["task"] == "Send a message"

    result = manager.stop()
    saved = json.loads((tmp_path / "lesson.wps.json").read_text(encoding="utf-8"))
    canonical = json.loads((tmp_path / "lesson.automation.json").read_text(encoding="utf-8"))

    assert result["mode"] == "apprentice"
    assert result["memory"]["provisional"] is True
    assert result["file"] == "lesson.automation.json"
    assert result["legacy_file"] == "lesson.wps.json"
    assert canonical["format"] == "automation-ir/1"
    assert saved["task"] == "Send a message"
    assert saved["source"] == "apprentice"
    assert saved["source_session"] == started["session_id"]
    assert captured[0][0][:2] == ("Send a message", "QQ")
    assert captured[0][1]["verified"] is False
    assert captured[0][1]["script"] == "lesson.automation.json"
    assert manager.status()["recording"] is False


def test_apprentice_requires_named_task(tmp_path):
    from winpilot.recorder import apprentice

    manager = apprentice.ApprenticeManager(FakeRecorder(tmp_path))
    try:
        manager.start("  ")
    except ValueError as exc:
        assert "requires a task" in str(exc)
    else:
        raise AssertionError("blank apprentice task should fail")


def test_replay_mode_contract_lists_all_three_execution_paths():
    from winpilot.recorder.apprentice import REPLAY_MODES

    assert [mode["id"] for mode in REPLAY_MODES] == ["offline_graph", "skill", "apprentice"]
    assert all(mode["llm_in_loop"] is False for mode in REPLAY_MODES)
