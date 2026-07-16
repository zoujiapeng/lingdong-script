def test_ocr_warm_async_starts_once(monkeypatch):
    from winpilot.perception import ocr

    calls = {"count": 0}

    def fake_get_engine():
        calls["count"] += 1
        ocr._engine = object()

    old_engine = ocr._engine
    old_thread = ocr._warm_thread
    monkeypatch.setattr(ocr, "_engine", None)
    monkeypatch.setattr(ocr, "_warm_thread", None)
    monkeypatch.setattr(ocr, "get_engine", fake_get_engine)
    try:
        assert ocr.warm_async() is True
        thread = ocr._warm_thread
        assert thread is not None
        thread.join(timeout=2)
        assert calls["count"] == 1
        assert ocr.warm_async() is False
    finally:
        monkeypatch.setattr(ocr, "_engine", old_engine)
        monkeypatch.setattr(ocr, "_warm_thread", old_thread)


def test_ocr_warm_async_skips_existing_engine(monkeypatch):
    from winpilot.perception import ocr

    old_engine = ocr._engine
    monkeypatch.setattr(ocr, "_engine", object())
    try:
        assert ocr.warm_async() is False
    finally:
        monkeypatch.setattr(ocr, "_engine", old_engine)


def test_foreground_priority_defers_predictive_ocr(monkeypatch):
    import threading
    import time

    import numpy as np

    from winpilot.perception import ocr

    calls: list[str] = []
    attempted = threading.Event()

    class FakeEngine:
        def __call__(self, _image):
            calls.append(threading.current_thread().name)
            return None, 0.0

    monkeypatch.setattr(ocr, "get_engine", lambda: FakeEngine())
    image = np.zeros((24, 80, 3), dtype=np.uint8)

    def background() -> None:
        attempted.set()
        with ocr.inference_scope("background"):
            ocr.recognize(image)

    worker = threading.Thread(target=background, name="predictive-test")
    with ocr.foreground_priority():
        worker.start()
        assert attempted.wait(timeout=1.0)
        time.sleep(0.03)
        assert calls == []
        ocr.recognize(image)
        assert calls == [threading.current_thread().name]

    worker.join(timeout=1.0)
    assert not worker.is_alive()
    assert calls == [threading.current_thread().name, "predictive-test"]


def test_waiting_foreground_runs_before_second_background(monkeypatch):
    import threading

    import numpy as np

    from winpilot.perception import ocr

    order: list[str] = []
    first_started = threading.Event()
    release_first = threading.Event()
    foreground_waiting = threading.Event()
    image = np.zeros((24, 80, 3), dtype=np.uint8)

    class FakeEngine:
        def __call__(self, _image):
            name = threading.current_thread().name
            order.append(name)
            if name == "background-1":
                first_started.set()
                assert release_first.wait(timeout=1.0)
            return None, 0.0

    monkeypatch.setattr(ocr, "get_engine", lambda: FakeEngine())

    def background() -> None:
        with ocr.inference_scope("background"):
            ocr.recognize(image)

    def foreground() -> None:
        foreground_waiting.set()
        ocr.recognize(image)

    first = threading.Thread(target=background, name="background-1")
    second = threading.Thread(target=background, name="background-2")
    direct = threading.Thread(target=foreground, name="foreground")
    first.start()
    assert first_started.wait(timeout=1.0)
    second.start()
    direct.start()
    assert foreground_waiting.wait(timeout=1.0)
    release_first.set()
    for thread in (first, second, direct):
        thread.join(timeout=1.0)
        assert not thread.is_alive()

    assert order == ["background-1", "foreground", "background-2"]


def test_canceled_queued_background_never_calls_engine(monkeypatch):
    import threading
    import time

    import numpy as np

    from winpilot.perception import ocr

    calls: list[str] = []
    attempted = threading.Event()
    canceled = threading.Event()
    image = np.zeros((24, 80, 3), dtype=np.uint8)

    class FakeEngine:
        def __call__(self, _image):
            calls.append(threading.current_thread().name)
            return None, 0.0

    monkeypatch.setattr(ocr, "get_engine", lambda: FakeEngine())

    def background() -> None:
        attempted.set()
        with ocr.inference_scope("background", cancel_event=canceled):
            ocr.recognize(image)

    worker = threading.Thread(target=background, name="canceled-background")
    with ocr.foreground_priority():
        worker.start()
        assert attempted.wait(timeout=1.0)
        time.sleep(0.03)
        canceled.set()
        worker.join(timeout=1.0)
        assert not worker.is_alive()
    assert calls == []
