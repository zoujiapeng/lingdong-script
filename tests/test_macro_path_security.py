from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))


def test_macro_path_stays_inside_macro_directory(tmp_path, monkeypatch):
    from features import macro_features

    monkeypatch.setattr(macro_features, "_MACRO_DIR", tmp_path)

    assert macro_features._macro_path("demo") == (tmp_path / "demo.json").resolve()
    assert macro_features._macro_path("demo.json") == (tmp_path / "demo.json").resolve()


def test_macro_path_rejects_traversal_and_absolute_paths(tmp_path, monkeypatch):
    import pytest

    from features import macro_features

    monkeypatch.setattr(macro_features, "_MACRO_DIR", tmp_path)

    for name in ("../secret", "..\\secret", str(Path(tmp_path.parent, "secret")), "sub/demo"):
        with pytest.raises(ValueError):
            macro_features._macro_path(name)

    assert macro_features.macro_delete("../secret")["success"] is False
