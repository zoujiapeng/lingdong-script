"""Irreversible-command guard for shell execution.

Only irreversible machine-level commands are blocked (format, shutdown,
diskpart, boot-config edits, recursive delete of system/user-root dirs,
shadow-copy delete).  Everything else — including destructive-but-plausible
commands such as a recursive delete of a normal path — is left to the Agent's
own judgment, in line with the model-first principle.  There is no approval
mode and no per-command approval gate.
"""
from __future__ import annotations

import re

from ..utils.log import logger

# Always-block: irreversible, machine-level. These run for cmd or powershell.
_CATASTROPHIC = [
    # Obfuscation vectors — refuse outright (a hidden command can't be vetted).
    (re.compile(r"-e(nc(odedcommand)?|c)?\b\s+[A-Za-z0-9+/=]{16,}", re.I),
     "PowerShell EncodedCommand(无法审查的编码命令,一律拒绝)"),
    (re.compile(r"\bformat\b\s+[a-z]:", re.I), "格式化磁盘"),
    (re.compile(r"\bFormat-Volume\b", re.I), "格式化卷(Format-Volume)"),
    (re.compile(r"\bdiskpart\b", re.I), "diskpart 磁盘操作"),
    (re.compile(r"\b(Stop|Restart)-Computer\b", re.I), "关机/重启"),
    (re.compile(r"\bshutdown\b\s+/[rsgp]", re.I), "shutdown 关机/重启"),
    (re.compile(r"\bbcdedit\b", re.I), "引导配置修改"),
    (re.compile(r"\bvssadmin\b.*\bdelete\b", re.I), "删除卷影副本"),
    (re.compile(r"\bcipher\b\s+/w", re.I), "擦除磁盘空间"),
    (re.compile(r"\bwmic\b.*\bos\b.*\b(shutdown|reboot)\b", re.I), "WMIC 关机"),
    # recursive/force delete of a system or user-root directory — the destructive
    # FLAG is required (single-file delete in a system path is not catastrophic).
    (re.compile(
        r"(Remove-Item|rm|del|rd|rmdir)\b[^|;\n]*"
        r"(-Recurse|-Force|-r\b|-rf\b|/s\b)[^|;\n]*"
        r"(C:\\?\s*$|C:\\Windows|System32|%SystemRoot%|\$env:WINDIR|"
        r"%USERPROFILE%\\?\s*$|\$env:USERPROFILE\\?\s*$|C:\\Users\\?\s*$)",
        re.I), "递归删除系统/用户根目录"),
    (re.compile(r"\brm\b\s+-rf\s+(/|~|\$HOME)\s*$", re.I), "rm -rf 根/家目录"),
]


def evaluate(command: str) -> tuple[str, str]:
    """Classify a command. Returns (action, reason): "block" only for the
    irreversible machine-level set, otherwise "allow"."""
    cmd = command or ""
    for pat, why in _CATASTROPHIC:
        if pat.search(cmd):
            return "block", f"不可逆操作已拦截（{why}）：此类操作被永久禁止。"
    return "allow", ""


def guard(command: str) -> dict | None:
    """Return a blocking result dict if the command must not run, else None
    (allowed). Logs blocks."""
    action, reason = evaluate(command)
    if action == "allow":
        return None
    logger.warning("安全层 %s: %s | cmd=%s", action, reason, command[:120])
    return {"ok": False, "blocked": True, "error": reason}
