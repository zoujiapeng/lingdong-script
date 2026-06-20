def test_clone_run_task_requires_real_run_attempt():
    from winpilot.agent.coding import CodingRunner

    runner = CodingRunner("clone github project cc-haha and run it")
    runner._tool_history = [
        ("github_search", {"query": "cc-haha"}, '{"ok": true}'),
        ("run_shell", {"command": "git clone https://github.com/NanmiCoder/cc-haha.git"}, '{"ok": true}'),
        ("read_file", {"path": "README.md"}, "# run with bun"),
    ]

    assert runner._requires_run_attempt()
    assert not runner._has_run_attempt()
    assert runner._reject_incomplete_run_task()


def test_clone_run_task_accepts_runtime_check_as_attempt():
    from winpilot.agent.coding import CodingRunner

    runner = CodingRunner("clone github project cc-haha and run it")
    runner._tool_history = [
        ("run_shell", {"command": "git clone https://github.com/NanmiCoder/cc-haha.git"}, '{"ok": true}'),
        ("run_shell", {"command": "Get-Command bun; bun --version"}, '{"ok": false}'),
    ]

    assert runner._requires_run_attempt()
    assert runner._has_run_attempt()
    assert not runner._reject_incomplete_run_task()
