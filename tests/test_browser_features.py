import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "python"))

from features import browser_features


def test_set_value_passes_text_as_playwright_argument(monkeypatch) -> None:
    calls = []

    class Page:
        def eval_on_selector(self, selector, expression, value):
            calls.append((selector, expression, value))

    monkeypatch.setattr(browser_features, "_get_page", lambda: Page())

    value = "apostrophe ' and backslash \\ and newline\n"
    result = browser_features._do_set_value("#field", value)

    assert result == {"success": True, "result": True}
    assert calls == [
        ("#field", "(el, nextValue) => { el.value = nextValue; }", value),
    ]


def test_storage_helpers_pass_keys_and_values_as_arguments(monkeypatch) -> None:
    calls = []

    class Page:
        def evaluate(self, expression, argument):
            calls.append((expression, argument))
            return "stored value"

    monkeypatch.setattr(browser_features, "_get_page", lambda: Page())

    key = "quote ' and backslash \\"
    value = "line one\nline two"
    get_result = browser_features._do_get_storage("localStorage", key)
    set_result = browser_features._do_set_storage("sessionStorage", key, value)

    assert get_result == {"success": True, "result": "stored value"}
    assert set_result == {"success": True, "result": True}
    assert calls == [
        (
            "([storageName, key]) => window[storageName].getItem(key)",
            ["localStorage", key],
        ),
        (
            "([storageName, key, value]) => window[storageName].setItem(key, value)",
            ["sessionStorage", key, value],
        ),
    ]
