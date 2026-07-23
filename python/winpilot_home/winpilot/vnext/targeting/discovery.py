"""One deterministic aggregation owner for desktop target candidates."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from itertools import islice
from pathlib import Path
import re
import time
import unicodedata

from .contracts import (
    MAX_TARGET_CANDIDATES,
    TargetActivation,
    TargetCandidate,
    TargetContractError,
    TargetDiscoverySnapshot,
    TargetState,
    candidate_ref_for,
    canonical_supported_launch_kinds,
    canonical_supported_launch_target,
    launch_identity_for,
)

_INSTALLED_LANE_BUDGET = MAX_TARGET_CANDIDATES
_INSTALLED_UNION_BUDGET = _INSTALLED_LANE_BUDGET * 2


def _normalized(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return re.sub(r"\s+", " ", text)


def _compact(value: object) -> str:
    return "".join(char for char in _normalized(value) if char.isalnum())


def _matches(text: object, query: object) -> bool:
    haystack, needle = _normalized(text), _normalized(query)
    if not needle:
        return False
    if needle in haystack:
        return True
    compact_needle = _compact(needle)
    allow_compact = len(compact_needle) >= 3 or (
        len(compact_needle) >= 2
        and any(ord(char) > 127 and char.isalnum() for char in needle)
    )
    return allow_compact and compact_needle in _compact(haystack)


def _texts(values: Iterable[object]) -> tuple[str, ...]:
    seen: set[str] = set()
    result = []
    for raw in values:
        value = str(raw or "").strip()
        folded = value.casefold()
        if value and folded not in seen:
            seen.add(folded)
            result.append(value)
    return tuple(result)


def _entry_terms(entry: Mapping[str, object]) -> tuple[str, ...]:
    process = str(entry.get("process_name") or "").strip()
    target = str(entry.get("target") or "").strip()
    raw_aliases = entry.get("aliases")
    aliases = (
        tuple(raw_aliases)
        if isinstance(raw_aliases, (list, tuple))
        else tuple(sorted(raw_aliases, key=lambda item: str(item).casefold()))
        if isinstance(raw_aliases, (set, frozenset))
        else ((raw_aliases,) if isinstance(raw_aliases, str) else ())
    )
    return _texts((
        entry.get("name"),
        process,
        Path(process).stem if process else "",
        *aliases,
        Path(target).stem if target and str(entry.get("kind")) != "appid" else "",
    ))


class TargetDiscovery:
    """Merge existing OS/catalog facts without selecting or activating a target."""

    def __init__(
        self,
        *,
        installed_resolver: Callable[
            [str, int, tuple[str, ...]],
            Iterable[Mapping[str, object]],
        ],
        visible_windows: Callable[[], Iterable[Mapping[str, object]]],
        process_windows: Callable[[str], Mapping[str, object]],
        process_identity: Callable[[int], str],
        supported_launch_kinds: tuple[str, ...],
        clock: Callable[[], float] = time.time,
    ) -> None:
        supported = canonical_supported_launch_kinds(supported_launch_kinds)
        for value, name in (
            (installed_resolver, "installed_resolver"),
            (visible_windows, "visible_windows"),
            (process_windows, "process_windows"),
            (process_identity, "process_identity"),
            (clock, "clock"),
        ):
            if not callable(value):
                raise TypeError(f"{name} must be callable")
        self._installed = installed_resolver
        self._visible = visible_windows
        self._process_windows = process_windows
        self._process_identity = process_identity
        self._supported_launch_kinds = supported
        self._clock = clock

    @staticmethod
    def _window_candidate(
        raw: Mapping[str, object],
        *,
        label: str,
        provenance: Iterable[str],
        process_identity: str,
    ) -> TargetCandidate | None:
        hwnd = raw.get("hwnd")
        pid = raw.get("pid")
        if type(hwnd) is not int or hwnd <= 0 or type(pid) is not int or pid <= 0:
            return None
        if not process_identity:
            return None
        raw_visible = raw.get("visible", True)
        if type(raw_visible) is not bool:
            return None
        visible = raw_visible
        try:
            return TargetCandidate(
                candidate_ref=candidate_ref_for(
                    activation=TargetActivation.SWITCH_WINDOW,
                    identity=f"{hwnd}|{pid}|{process_identity}",
                ),
                activation=TargetActivation.SWITCH_WINDOW,
                state=TargetState.VISIBLE if visible else TargetState.RECOVERABLE,
                label=label or str(raw.get("title") or process_identity),
                window_title=raw.get("title") or "",
                provenance=tuple(sorted(set(provenance))),
                hwnd=hwnd,
                process_id=pid,
                process_identity=process_identity,
            )
        except TargetContractError:
            return None

    def discover(self, query: str) -> TargetDiscoverySnapshot:
        if type(query) is not str or not query.strip():
            raise TargetContractError("target query must be a non-empty string")
        query = query.strip()
        if len(query) > 256:
            raise TargetContractError("target query is too long")
        notices: list[str] = []
        try:
            installed = tuple(islice(
                self._installed(
                    query,
                    _INSTALLED_LANE_BUDGET,
                    self._supported_launch_kinds,
                ),
                _INSTALLED_UNION_BUDGET,
            ))
        except Exception:
            installed = ()
            notices.append("installed_catalog_unavailable")
        try:
            visible = tuple(islice(self._visible(), MAX_TARGET_CANDIDATES * 16))
        except Exception:
            visible = ()
            notices.append("visible_window_catalog_unavailable")

        windows: dict[tuple[int, int, str], TargetCandidate] = {}
        running_application_ids: set[str] = set()
        bindable_application_ids: set[str] = set()
        unknown_running_state_ids: set[str] = set()

        def add_window(
            raw: Mapping[str, object],
            *,
            label: str,
            application_identity: str,
            provenance: Iterable[str],
        ) -> None:
            pid = raw.get("pid")
            if type(pid) is not int or pid <= 0:
                return
            try:
                raw_identity = self._process_identity(pid)
                identity = raw_identity.strip() if type(raw_identity) is str else ""
            except Exception:
                identity = ""
            candidate = self._window_candidate(
                raw,
                label=label,
                provenance=provenance,
                process_identity=identity,
            )
            if candidate is None:
                return
            if application_identity:
                running_application_ids.add(application_identity)
                bindable_application_ids.add(application_identity)
            key = (candidate.hwnd or 0, candidate.process_id or 0, identity)
            prior = windows.get(key)
            if prior is None:
                windows[key] = candidate
                return
            preferred = min(
                (prior, candidate),
                key=lambda item: (
                    0 if "installed_catalog" in item.provenance else 1,
                    item.label.casefold(),
                    item.label,
                ),
            )
            windows[key] = replace(
                preferred,
                state=(
                    TargetState.VISIBLE
                    if TargetState.VISIBLE in {prior.state, candidate.state}
                    else TargetState.RECOVERABLE
                ),
                provenance=tuple(sorted(set((
                    *prior.provenance, *candidate.provenance,
                )))),
            )

        for raw in visible:
            if not isinstance(raw, Mapping):
                continue
            title = str(raw.get("title") or "")
            if _matches(title, query):
                add_window(
                    raw,
                    label=title or query,
                    application_identity="",
                    provenance=("visible_windows", "title_match"),
                )

        installed_groups: dict[str, list[Mapping[str, object]]] = {}
        for raw in installed:
            if not isinstance(raw, Mapping):
                continue
            try:
                app_identity = launch_identity_for(
                    kind=raw.get("kind"), target=raw.get("target"),
                )
            except TargetContractError:
                continue
            installed_groups.setdefault(app_identity, []).append(raw)

        installed_rows: list[tuple[Mapping[str, object], str, tuple[str, ...]]] = []
        for app_identity, rows in sorted(installed_groups.items()):
            raw = min(rows, key=lambda item: (
                _normalized(item.get("name")), str(item.get("name") or ""),
                _normalized(item.get("target")), str(item.get("target") or ""),
                _normalized(item.get("process_name")),
            ))
            terms = _texts(sorted(
                (term for row in rows for term in _entry_terms(row)),
                key=lambda item: (_normalized(item), item),
            ))
            installed_rows.append((raw, app_identity, terms))
            process_names = _texts(sorted(
                (row.get("process_name") for row in rows),
                key=lambda item: (_normalized(item), str(item or "")),
            ))
            process_terms = _texts((*process_names, *terms))
            state_unknown = not process_terms or len(process_terms) > 8
            for term in process_terms[:8]:
                try:
                    state = self._process_windows(term)
                except Exception:
                    state_unknown = True
                    continue
                if not isinstance(state, Mapping):
                    state_unknown = True
                    continue
                running = state.get("running")
                if type(running) is not bool:
                    state_unknown = True
                elif running:
                    running_application_ids.add(app_identity)
                raw_windows = state.get("windows")
                source_windows = (
                    raw_windows if isinstance(raw_windows, (list, tuple)) else ()
                )
                if raw_windows is not None and not isinstance(
                    raw_windows, (list, tuple)
                ):
                    state_unknown = True
                for window in islice(source_windows, 64):
                    if isinstance(window, Mapping):
                        add_window(
                            window,
                            label=str(raw.get("name") or term),
                            application_identity=app_identity,
                            provenance=("installed_catalog", "running_processes"),
                        )
            if state_unknown:
                unknown_running_state_ids.add(app_identity)
            for window in visible:
                if not isinstance(window, Mapping):
                    continue
                if any(_matches(window.get("title"), term) for term in terms):
                    add_window(
                        window,
                        label=str(raw.get("name") or query),
                        application_identity="",
                        provenance=("installed_catalog", "visible_windows", "title_match"),
                    )

        candidates = list(windows.values())
        for raw, app_identity, _terms in installed_rows:
            if app_identity in running_application_ids:
                if app_identity not in bindable_application_ids:
                    notices.append("running_without_bindable_window")
                continue
            if app_identity in unknown_running_state_ids:
                notices.append("running_state_unavailable")
                continue
            try:
                kind, target = canonical_supported_launch_target(
                    kind=raw.get("kind"),
                    target=raw.get("target"),
                    supported_launch_kinds=self._supported_launch_kinds,
                )
                candidates.append(TargetCandidate(
                    candidate_ref=candidate_ref_for(
                        activation=TargetActivation.OPEN_APP,
                        identity=app_identity,
                    ),
                    activation=TargetActivation.OPEN_APP,
                    state=TargetState.INSTALLED,
                    label=str(raw.get("name") or query),
                    provenance=("installed_catalog",),
                    launch_kind=kind,
                    launch_target=target,
                ))
            except TargetContractError:
                continue

        candidates.sort(key=lambda item: (
            0 if "running_processes" in item.provenance else
            1 if item.activation is TargetActivation.OPEN_APP else 2,
            0 if item.state is TargetState.VISIBLE else 1,
            item.label.casefold(),
            item.candidate_ref,
        ))
        return TargetDiscoverySnapshot(
            query=query,
            candidates=tuple(candidates[:MAX_TARGET_CANDIDATES]),
            notices=tuple(dict.fromkeys(notices)),
            captured_at=float(self._clock()),
        )


__all__ = ["TargetDiscovery"]
