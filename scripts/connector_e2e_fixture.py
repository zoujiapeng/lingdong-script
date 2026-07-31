#!/usr/bin/env python3
"""One-tool stdio MCP fixture for the generic connector Electron E2E.

The server is deliberately process-stateless.  Its only durable effect is an
append-only oracle record written when (and only when) the tool is physically
called.  Tool discovery therefore cannot be mistaken for task completion.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations


TOKEN_ENV = "WINPILOT_CONNECTOR_E2E_TOKEN"
ORACLE_ENV = "WINPILOT_CONNECTOR_E2E_ORACLE"
FIXTURE_KEY = "verification"


def _required_env(name: str) -> str:
    value = str(os.environ.get(name) or "")
    if not value:
        raise RuntimeError(f"missing required fixture environment: {name}")
    return value


def _append_oracle(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wire = json.dumps(
        event,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(wire + "\n")
        stream.flush()
        os.fsync(stream.fileno())


mcp = FastMCP(
    "Generic connector E2E fixture",
    instructions=(
        "This test service exposes one read-only value. Its supported key is "
        f"{FIXTURE_KEY}."
    ),
)


@mcp.tool(
    name="read_value",
    description=(
        "Read a value from this connected service by key. The available "
        f"verification key is `{FIXTURE_KEY}`."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
    structured_output=True,
)
def read_value(key: str) -> dict[str, str]:
    """Return the fixture value without accepting a mutation."""

    if key != FIXTURE_KEY:
        raise ValueError(f"unknown key: {key}")
    token = _required_env(TOKEN_ENV)
    oracle_path = Path(_required_env(ORACLE_ENV))
    arguments_wire = json.dumps(
        {"key": key},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    _append_oracle(
        oracle_path,
        {
            "format": "connector-e2e-invocation/1",
            "time_ns": time.time_ns(),
            "pid": os.getpid(),
            "tool": "read_value",
            "key": key,
            "physical_arguments_sha256": hashlib.sha256(
                arguments_wire.encode("utf-8")
            ).hexdigest(),
            "value_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        },
    )
    return {"key": key, "value": token}


def main() -> int:
    # Resolve configuration before entering the JSON-RPC loop so a broken
    # fixture fails as a transport error instead of advertising a false tool.
    _required_env(TOKEN_ENV)
    _required_env(ORACLE_ENV)
    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
