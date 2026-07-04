def test_memory_pack_filters_coding_and_volatile_tasks(tmp_path):
    from winpilot.memory.runtime import prepare_run_memory
    from winpilot.memory.store import MemoryStore

    store = MemoryStore(tmp_path / "memory.db")
    store.add_node(
        "skill",
        "login and place order",
        body='click "Login"\nclick "Order"',
        payload={"steps": ['click "Login"', 'click "Order"'], "script": "order.wps.json"},
        conditions=["same shopping workflow"],
        evidence=["observe confirms order placed"],
        anchors=["login", "order"],
    )
    store.add_node(
        "fact",
        "acme uses pnpm",
        body="pnpm install",
        manual=True,
        anchors=["acme", "pnpm"],
    )

    coding_pack = prepare_run_memory(
        "acme login order code task",
        {"coding": True, "notes_only": True},
        store=store,
    )
    assert "pnpm" in coding_pack.text
    assert "order.wps.json" not in coding_pack.text

    volatile_pack = prepare_run_memory(
        "只检查当前已经打开的QQ窗口，找出哪个已经登录、哪个是登录页",
        store=store,
    )
    assert "order.wps.json" not in volatile_pack.text


def test_memory_pack_skips_superseded_and_includes_warning(tmp_path):
    from winpilot.memory.runtime import prepare_run_memory
    from winpilot.memory.store import MemoryStore

    store = MemoryStore(tmp_path / "memory.db")
    old = store.add_node("fact", "old acme rule", body="old", anchors=["acme"])
    store.retire_node(old["id"], "superseded")
    skill = store.add_node(
        "skill",
        "open browser settings and clear cache",
        body="open settings\nclear cache",
        payload={"steps": ["open settings", "clear cache"], "script": "clear-cache.wps.json"},
        conditions=["browser settings page is visible"],
        evidence=["read_text confirms cache cleared"],
        anchors=["browser", "clear cache"],
    )
    warning = store.add_node(
        "warning",
        "Cache dialog changed once",
        summary="If replay opens the wrong tab, stop and observe.",
    )
    store.add_edge(warning["id"], skill["id"], "warning_for")

    pack = prepare_run_memory("open browser settings and clear cache", store=store)

    assert "old acme rule" not in pack.text
    assert "Cache dialog changed once" in pack.text
    assert 'reuse_skill(memory_id="' in pack.text
    assert len(pack.text) <= 4800
