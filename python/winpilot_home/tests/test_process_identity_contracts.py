from __future__ import annotations

import pytest

from winpilot.process_identity import (
    MAX_PROCESS_EXECUTABLE_PATH_CHARS,
    ProcessIdentityError,
    canonical_executable_path,
)


@pytest.mark.parametrize(
    "path",
    (
        r"C:\Apps\Orbit\orbit.exe",
        r"D:\工具\绘图.EXE",
        r"\\server\share\client.exe",
    ),
)
def test_canonical_executable_path_preserves_valid_windows_path(path: str) -> None:
    assert canonical_executable_path(path) == path


@pytest.mark.parametrize(
    "value",
    (
        None,
        True,
        "",
        " orbit.exe",
        "orbit.exe",
        r"C:orbit.exe",
        r"C:\Apps\..\orbit.exe",
        r"C:\Apps\\orbit.exe",
        r"C:\Apps\orbit.lnk",
        'C:\\Apps\\"orbit.exe"',
        "C:\\Apps\\orbit.exe\x00ignored",
        "C:\\Apps\\orbit.exe\r",
        "C:\\Apps\\orbit.exe\n",
        "C:\\" + ("a" * MAX_PROCESS_EXECUTABLE_PATH_CHARS) + ".exe",
    ),
)
def test_canonical_executable_path_rejects_noncanonical_or_non_exe(
    value: object,
) -> None:
    with pytest.raises(ProcessIdentityError):
        canonical_executable_path(value)
