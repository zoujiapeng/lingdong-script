"""Per-run structured fact collection for cross-application workflows."""
from __future__ import annotations

import json
import re

from ..utils.log import BUS
from .tool_result import ToolResult


class WorkingSetToolMixin:
    def _visible_source_url(self) -> str:
        snapshot = getattr(self, "snapshot", None)
        region = getattr(snapshot, "region", None)
        if snapshot is None or region is None:
            return ""
        candidates = []
        for element in getattr(snapshot, "elements", []) or []:
            role = str(getattr(element, "role", "") or "").casefold()
            text = str(getattr(element, "text", "") or "").strip()
            width = int(getattr(element, "width", 0) or 0)
            cy = int(getattr(element, "cy", 0) or 0)
            if role not in {"edit", "textbox", "searchbox", "input"}:
                continue
            if width < int(region.width * 0.40) or cy > region.top + max(100, int(region.height * 0.15)):
                continue
            match = re.search(
                r"(?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s]*)?",
                text,
                re.IGNORECASE,
            )
            if match:
                value = match.group(0).rstrip(".,;:!?)]}")
                candidates.append(value if value.startswith(("http://", "https://")) else f"https://{value}")
        return candidates[0] if candidates else ""

    def _t_record_facts(self, args: dict) -> ToolResult:
        subject = str(args.get("subject") or "").strip()
        facts = args.get("facts")
        if not subject or not isinstance(facts, dict) or not facts:
            return ToolResult(json.dumps({
                "ok": False,
                "blocked": True,
                "error": "record_facts requires subject and a non-empty facts object",
            }, ensure_ascii=False))
        prior_records = self.world.working_set()
        subject_key = " ".join(subject.split()).casefold()
        requested_source_url = str(args.get("source_url") or "").strip()
        visible_source_url = self._visible_source_url()
        source_url = visible_source_url or requested_source_url
        source_url_corrected = bool(
            visible_source_url
            and requested_source_url
            and visible_source_url.casefold().rstrip("/") != requested_source_url.casefold().rstrip("/")
        )
        normalized_facts = dict(facts)
        if source_url:
            for key in list(normalized_facts):
                normalized_key = re.sub(r"[^a-z\u4e00-\u9fff]+", "", str(key).casefold())
                if normalized_key in {"来源", "来源网址", "来源链接", "source", "url", "sourceurl"}:
                    normalized_facts[key] = source_url
        prior_subject = next(
            (
                item for item in prior_records
                if " ".join(str(item.get("subject") or "").split()).casefold() == subject_key
            ),
            None,
        )
        duplicate_source = next(
            (
                item for item in prior_records
                if source_url
                and str(item.get("source_url") or "").strip().casefold() == source_url.casefold()
                and " ".join(str(item.get("subject") or "").split()).casefold() != subject_key
            ),
            None,
        )
        if duplicate_source is not None:
            return ToolResult(json.dumps({
                "ok": False,
                "verified": False,
                "blocked": True,
                "duplicate_source_url": True,
                "error": (
                    "This source URL is already bound to a different subject in the "
                    "working set. Navigate to and verify the distinct source before "
                    "recording another subject."
                ),
                "existing_record": duplicate_source,
                "working_set": prior_records,
            }, ensure_ascii=False, default=str))
        record = self.world.record_facts(
            subject=subject,
            facts=normalized_facts,
            source_url=source_url,
            source_title=str(args.get("source_title") or "").strip(),
            evidence=args.get("evidence") if isinstance(args.get("evidence"), list) else [],
        )
        BUS.publish(
            "working_set",
            action="record_facts",
            subject=subject[:120],
            field_count=len(record.get("facts") or {}),
            source_url=str(record.get("source_url") or "")[:240],
        )
        return ToolResult(json.dumps({
            "ok": True,
            "verified": prior_subject is None,
            "created_new_source": prior_subject is None,
            "updated_existing_source": prior_subject is not None,
            "visible_source_url": visible_source_url,
            "source_url_corrected": source_url_corrected,
            "record": record,
            "working_set": self.world.working_set(),
            "fast_path_advice": (
                "A new source-bound record was created. Navigate to the next distinct source."
                if prior_subject is None else
                "The existing source record was updated; this does not count as a new source. "
                "Navigate to the next distinct source URL instead of completing another source item."
            ),
        }, ensure_ascii=False, default=str))
