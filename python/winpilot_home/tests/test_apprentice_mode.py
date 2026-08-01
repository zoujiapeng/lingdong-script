import json

import pytest


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
            json.dumps(
                {
                    "format": "wps/2",
                    "name": self.name,
                    "source": "human",
                    "target_window": {"title": "Chat", "process": "chat.exe"},
                    "steps": [{"tool": "click", "text": "Send", "coords": [100, 200]}],
                }
            ),
            encoding="utf-8",
        )
        return {"ok": True, "file": path.name, "steps": 1}


def test_apprentice_lesson_becomes_editable_script_and_provisional_skill(
    tmp_path, monkeypatch
):
    from winpilot.recorder import apprentice

    captured = []
    fake = FakeRecorder(tmp_path)
    monkeypatch.setattr(apprentice, "SCRIPTS_DIR", tmp_path)
    monkeypatch.setattr(
        apprentice.experience,
        "record",
        lambda *args, **kwargs: captured.append((args, kwargs))
        or {
            "id": "skill-candidate",
            "script": "lesson.automation.json",
            "provisional": True,
        },
    )
    manager = apprentice.ApprenticeManager(fake)

    started = manager.start(
        "Send a message",
        app="QQ",
        verification=[{"predicate": "visible.text", "value": "sent"}],
    )
    assert started["recording"] is True
    assert manager.status()["task"] == "Send a message"

    result = manager.stop()
    saved = json.loads((tmp_path / "lesson.wps.json").read_text(encoding="utf-8"))
    canonical = json.loads(
        (tmp_path / "lesson.automation.json").read_text(encoding="utf-8")
    )

    assert result["mode"] == "apprentice"
    assert result["memory"]["provisional"] is True
    assert result["file"] == "lesson.automation.json"
    assert result["legacy_file"] == "lesson.wps.json"
    assert canonical["format"] == "automation-ir/1"
    assert canonical["conditions"]["target_window"] == {
        "title": "Chat",
        "process": "chat.exe",
    }
    assert canonical["safety"]["requires_preflight"] is True
    assert saved["task"] == "Send a message"
    assert saved["source"] == "apprentice"
    assert saved["evidence"]["required"] == [
        {"predicate": "visible.text", "value": "sent"}
    ]
    assert captured[0][1]["verification"] == [
        {"predicate": "visible.text", "value": "sent"}
    ]
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


@pytest.mark.parametrize("name", ["CON", "../lesson", "lesson."])
def test_apprentice_rejects_unsafe_explicit_macro_name(tmp_path, monkeypatch, name):
    from winpilot.recorder import apprentice

    fake = FakeRecorder(tmp_path)
    monkeypatch.setattr(apprentice, "SCRIPTS_DIR", tmp_path)
    manager = apprentice.ApprenticeManager(fake)

    with pytest.raises(ValueError, match="Windows-safe"):
        manager.start(
            "Record a safe lesson",
            name=name,
            verification=[{"predicate": "visible.text", "value": "done"}],
        )

    assert fake.recording is False


def test_apprentice_rejects_existing_macro_name_case_insensitively(
    tmp_path, monkeypatch
):
    from winpilot.recorder import apprentice

    (tmp_path / "Lesson.wps.json").write_text("keep", encoding="utf-8")
    fake = FakeRecorder(tmp_path)
    monkeypatch.setattr(apprentice, "SCRIPTS_DIR", tmp_path)
    manager = apprentice.ApprenticeManager(fake)

    with pytest.raises(ValueError, match="conflicts with existing script"):
        manager.start(
            "Record without replacing an old macro",
            name="lesson",
            verification=[{"predicate": "visible.text", "value": "done"}],
        )

    assert (tmp_path / "Lesson.wps.json").read_text(encoding="utf-8") == "keep"
    assert fake.recording is False


def test_apprentice_blank_macro_name_reuses_unique_session_id(tmp_path, monkeypatch):
    from winpilot.recorder import apprentice

    fake = FakeRecorder(tmp_path)
    monkeypatch.setattr(apprentice, "SCRIPTS_DIR", tmp_path)
    manager = apprentice.ApprenticeManager(fake)

    started = manager.start(
        r"Open C:\temp? then click",
        verification=[{"predicate": "visible.text", "value": "done"}],
    )

    assert started["name"] == started["session_id"]


def test_apprentice_keeps_existing_canonical_file_on_stop_race(tmp_path, monkeypatch):
    from winpilot.recorder import apprentice

    fake = FakeRecorder(tmp_path)
    monkeypatch.setattr(apprentice, "SCRIPTS_DIR", tmp_path)
    monkeypatch.setattr(
        apprentice.experience,
        "record",
        lambda *args, **kwargs: pytest.fail("a conflicted recording is not promoted"),
    )
    manager = apprentice.ApprenticeManager(fake)
    manager.start(
        "Record without replacing canonical output",
        name="lesson",
        verification=[{"predicate": "visible.text", "value": "done"}],
    )
    canonical = tmp_path / "lesson.automation.json"
    canonical.write_text("keep", encoding="utf-8")

    with pytest.raises(
        RuntimeError, match="recorded actions were kept.*already exists"
    ):
        manager.stop()

    assert canonical.read_text(encoding="utf-8") == "keep"
    assert (tmp_path / "lesson.wps.json").exists()
    assert manager.status()["session_id"] is None


def test_apprentice_clears_failed_recording_session(tmp_path):
    from winpilot.recorder import apprentice

    class FailingRecorder(FakeRecorder):
        def stop(self):
            self.recording = False
            return {"ok": False, "error": "recording crossed target windows"}

    manager = apprentice.ApprenticeManager(FailingRecorder(tmp_path))
    manager.start(
        "Cross-window demonstration",
        verification=[{"predicate": "visible.text", "value": "done"}],
    )

    try:
        manager.stop()
    except RuntimeError as exc:
        assert "crossed target windows" in str(exc)
    else:
        raise AssertionError("unsafe demonstration must fail")
    assert manager.status()["session_id"] is None
    assert manager.status()["recording"] is False


def test_apprentice_keeps_session_when_recorder_stop_is_retryable(tmp_path):
    from winpilot.recorder import apprentice

    class SlowRecorder(FakeRecorder):
        def stop(self):
            return {"ok": False, "error": "recorded input is still draining; try again"}

    manager = apprentice.ApprenticeManager(SlowRecorder(tmp_path))
    started = manager.start(
        "Retry a slow recording",
        verification=[{"predicate": "visible.text", "value": "done"}],
    )

    try:
        manager.stop()
    except RuntimeError as exc:
        assert "still draining" in str(exc)
    else:
        raise AssertionError("draining recording must ask the user to retry")

    assert manager.status()["session_id"] == started["session_id"]
    assert manager.status()["recording"] is True
    # The same session remains available for the user's next stop attempt.
