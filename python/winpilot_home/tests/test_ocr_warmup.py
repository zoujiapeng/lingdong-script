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
