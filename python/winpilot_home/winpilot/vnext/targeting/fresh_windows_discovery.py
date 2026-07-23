"""Fresh, read-only Windows facts composition for target discovery.

The adapter has no desktop mutation dependency.  It samples Windows process
and window facts only after an explicit ``discover(query)`` call, then delegates
all candidate aggregation to the canonical :class:`TargetDiscovery`.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from itertools import islice
from threading import Event
import time

from .contracts import (
    TargetDiscoverySnapshot,
    canonical_supported_launch_kinds,
    launch_identity_for,
)
from .discovery import TargetDiscovery
from .windows_facts import (
    WindowsTargetFactsAdapter,
    WindowsTargetFactsReader,
)


InstalledResolver = Callable[
    [str, int, tuple[str, ...]],
    Iterable[Mapping[str, object]],
]
FactsReaderFactory = Callable[[], WindowsTargetFactsReader]


def _installed_apps(
    query: str,
    limit: int,
    supported_launch_kinds: tuple[str, ...],
) -> Iterable[Mapping[str, object]]:
    # Keep the legacy catalog import and its potentially expensive index lazy.
    from ...perception import apps

    general = tuple(islice(
        apps.resolve(query, max_results=limit),
        limit,
    ))
    supported = (
        tuple(islice(
            apps.resolve(
                query,
                max_results=limit,
                launch_kinds=supported_launch_kinds,
            ),
            limit,
        ))
        if supported_launch_kinds
        else ()
    )
    seen: set[str] = set()
    result = []
    for entry in (*general, *supported):
        try:
            identity = launch_identity_for(
                kind=entry.get("kind"),
                target=entry.get("target"),
            )
        except (AttributeError, ValueError):
            continue
        if identity not in seen:
            seen.add(identity)
            result.append(entry)
    return tuple(result)


class FreshWindowsTargetDiscovery:
    """Build one immutable Windows facts view per explicit discovery request."""

    __slots__ = (
        "_facts_reader_factory",
        "_installed_resolver",
        "_supported_launch_kinds",
        "_clock",
    )

    def __init__(
        self,
        *,
        supported_launch_kinds: tuple[str, ...],
        facts_reader_factory: FactsReaderFactory | None = None,
        installed_resolver: InstalledResolver | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        supported = canonical_supported_launch_kinds(supported_launch_kinds)
        if facts_reader_factory is not None and not callable(
            facts_reader_factory
        ):
            raise TypeError("facts_reader_factory must be callable or null")
        if installed_resolver is not None and not callable(installed_resolver):
            raise TypeError("installed_resolver must be callable or null")
        if not callable(clock):
            raise TypeError("clock must be callable")

        if facts_reader_factory is None:
            facts_reader_factory = lambda: WindowsTargetFactsReader(clock=clock)
        self._facts_reader_factory = facts_reader_factory
        self._installed_resolver = installed_resolver or _installed_apps
        self._supported_launch_kinds = supported
        self._clock = clock

    @staticmethod
    def _check_stop(stop_event: Event | None) -> None:
        if stop_event is not None and not isinstance(stop_event, Event):
            raise TypeError("stop_event must be threading.Event or null")
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError("target discovery was canceled")

    def discover(
        self,
        query: str,
        *,
        stop_event: Event | None = None,
    ) -> TargetDiscoverySnapshot:
        """Sample once and delegate the unchanged query to TargetDiscovery."""

        self._check_stop(stop_event)
        reader = self._facts_reader_factory()
        if not isinstance(reader, WindowsTargetFactsReader):
            raise TypeError(
                "facts_reader_factory must return WindowsTargetFactsReader"
            )
        snapshot = reader.read()
        self._check_stop(stop_event)
        adapter = WindowsTargetFactsAdapter(snapshot)

        def installed(
            requested_query: str,
            limit: int,
            supported_launch_kinds: tuple[str, ...],
        ) -> Iterable[Mapping[str, object]]:
            self._check_stop(stop_event)
            for item in self._installed_resolver(
                requested_query,
                limit,
                supported_launch_kinds,
            ):
                self._check_stop(stop_event)
                yield item
            self._check_stop(stop_event)

        discovery = TargetDiscovery(
            installed_resolver=installed,
            visible_windows=adapter.visible_windows,
            process_windows=adapter.process_windows,
            process_identity=adapter.process_identity,
            supported_launch_kinds=self._supported_launch_kinds,
            # The candidates were derived from this exact OS-facts snapshot.
            # A later installed-app lookup must not make those facts look newer.
            clock=lambda: snapshot.captured_at,
        )
        result = discovery.discover(query)
        self._check_stop(stop_event)
        return result


__all__ = [
    "FactsReaderFactory",
    "FreshWindowsTargetDiscovery",
    "InstalledResolver",
]
