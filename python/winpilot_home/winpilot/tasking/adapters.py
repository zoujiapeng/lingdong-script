"""Plugin boundary for optional communication providers.

The core runtime owns artifact identity and evidence contracts.  A separately
installed provider may implement application-specific communication, but the
core contains no client names, UI trees, OCR rules, or coordinate layouts.
Without a registered provider the caller must continue through the ordinary
closed-loop visual desktop agent.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .artifacts import ArtifactStore
from .evidence import EvidenceRegistry


class CommunicationAdapter(ABC):
    """Minimal contract implemented by an external communication provider."""

    id = ""
    app_name = ""
    channels: tuple[str, ...] = ()
    auto_discover = False

    def __init__(
        self,
        *,
        run_id: str,
        task: str,
        store: ArtifactStore,
        evidence: EvidenceRegistry,
        stop_event=None,
        task_intent: Any | None = None,
    ) -> None:
        self.run_id = str(run_id or "")
        self.task = str(task or "")
        self.store = store
        self.evidence = evidence
        self.stop_event = stop_event
        self.task_intent = task_intent
        self.recipient = ""

    @abstractmethod
    def open_conversation(
        self,
        recipient: str,
        *,
        stage_id: str = "",
    ) -> dict[str, Any]: ...

    @abstractmethod
    def resolve_composer(self) -> dict[str, Any]: ...

    @abstractmethod
    def paste_or_attach_artifact(
        self,
        artifact_id: str,
        *,
        stage_id: str = "",
    ) -> dict[str, Any]: ...

    @abstractmethod
    def send_current_composer(
        self,
        artifact_id: str = "",
        *,
        stage_id: str = "",
    ) -> dict[str, Any]: ...

    @abstractmethod
    def send_text(self, text: str, *, stage_id: str = "") -> dict[str, Any]: ...

    @abstractmethod
    def verify_latest_outbound(
        self,
        *,
        artifact_id: str = "",
        text: str = "",
        stage_id: str = "",
    ) -> dict[str, Any]: ...

    def close(self) -> None:
        """Release provider-owned resources, if any."""


class DomainAdapterRegistry:
    """Explicit registry populated only by installed provider plugins."""

    _types: dict[str, type[CommunicationAdapter]] = {}
    _channel_names: dict[str, str] = {}

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(str(value or "").strip().split()).casefold()

    @classmethod
    def _component_id(cls, adapter_type: type[CommunicationAdapter]) -> str:
        adapter_id = cls._normalize(getattr(adapter_type, "id", ""))
        return f"{adapter_id}_adapter" if adapter_id else ""

    @classmethod
    def register(
        cls,
        adapter_type: type[CommunicationAdapter],
        *,
        channels: tuple[str, ...] | list[str] = (),
        replace: bool = False,
    ) -> type[CommunicationAdapter]:
        """Register one provider and all of its explicitly declared aliases."""

        if not isinstance(adapter_type, type) or not issubclass(
            adapter_type, CommunicationAdapter,
        ):
            raise TypeError("communication provider must subclass CommunicationAdapter")
        adapter_id = str(getattr(adapter_type, "id", "") or "").strip()
        if not adapter_id:
            raise ValueError("communication provider requires a non-empty id")
        declared = getattr(adapter_type, "channels", ()) or ()
        app_name = str(getattr(adapter_type, "app_name", "") or "").strip()
        aliases = [adapter_id, app_name, *declared, *channels]
        normalized_aliases = {
            cls._normalize(alias): str(alias).strip()
            for alias in aliases
            if cls._normalize(alias)
        }
        conflicts = {
            key: existing
            for key, existing in cls._types.items()
            if key in normalized_aliases and existing is not adapter_type
        }
        if conflicts and not replace:
            names = ", ".join(sorted(conflicts))
            raise KeyError(f"communication provider alias already registered: {names}")
        for key, display_name in normalized_aliases.items():
            cls._types[key] = adapter_type
            cls._channel_names[key] = display_name
        return adapter_type

    @classmethod
    def unregister(cls, provider_or_channel: type[CommunicationAdapter] | str) -> bool:
        """Remove a provider and every alias that resolves to it."""

        if isinstance(provider_or_channel, type):
            adapter_type = provider_or_channel
        else:
            key = cls._normalize(provider_or_channel)
            adapter_type = cls._types.get(key)
            if adapter_type is None and key.endswith("_adapter"):
                adapter_type = next(
                    (
                        candidate
                        for candidate in set(cls._types.values())
                        if cls._component_id(candidate) == key
                    ),
                    None,
                )
        if adapter_type is None:
            return False
        aliases = [
            key for key, candidate in cls._types.items()
            if candidate is adapter_type
        ]
        for key in aliases:
            cls._types.pop(key, None)
            cls._channel_names.pop(key, None)
        return bool(aliases)

    @classmethod
    def clear(cls) -> None:
        """Clear registrations; intended for plugin reload and isolated tests."""

        cls._types.clear()
        cls._channel_names.clear()

    @classmethod
    def component_for_channel(cls, channel: str) -> str:
        adapter_type = cls._types.get(cls._normalize(channel))
        return cls._component_id(adapter_type) if adapter_type is not None else ""

    @classmethod
    def probe_registration(
        cls,
        *,
        channel: str = "",
        component: str = "",
    ) -> dict[str, Any]:
        """Probe static registration without touching an application or desktop."""

        channel_key = cls._normalize(channel)
        component_key = cls._normalize(component)
        channel_type = cls._types.get(channel_key) if channel_key else None
        registered_types = set(cls._types.values())
        component_type = next(
            (
                adapter_type
                for adapter_type in registered_types
                if cls._component_id(adapter_type) == component_key
            ),
            None,
        ) if component_key else None
        selected_type = channel_type or component_type
        registered = bool(
            selected_type is not None
            and (not channel_key or channel_type is selected_type)
            and (not component_key or component_type is selected_type)
        )
        return {
            "registered": registered,
            "channel": str(channel or "").strip(),
            "component": (
                cls._component_id(selected_type)
                if selected_type is not None else component_key
            ),
            "adapter_id": (
                str(getattr(selected_type, "id", "") or "")
                if selected_type is not None else ""
            ),
            "probe_scope": "static_registry_only",
            "runtime_limitations": [
                "Static registration does not prove that the application is installed or running.",
                "Static registration does not prove login/session readiness or recipient availability.",
            ],
        }

    @classmethod
    def probe(
        cls,
        *,
        channel: str = "",
        component: str = "",
    ) -> dict[str, Any]:
        return cls.probe_registration(channel=channel, component=component)

    @classmethod
    def create(cls, channel: str, **kwargs: Any) -> CommunicationAdapter:
        adapter_type = cls._types.get(cls._normalize(channel))
        if adapter_type is None:
            raise KeyError(f"no communication provider registered for channel: {channel}")
        return adapter_type(**kwargs)

    @classmethod
    def registered_adapters(cls) -> list[dict[str, Any]]:
        grouped: dict[type[CommunicationAdapter], set[str]] = {}
        for channel, adapter_type in cls._types.items():
            grouped.setdefault(adapter_type, set()).add(
                cls._channel_names.get(channel, channel)
            )
        descriptors: list[dict[str, Any]] = []
        for adapter_type, channels in grouped.items():
            adapter_id = str(getattr(adapter_type, "id", "") or "").strip()
            if not adapter_id:
                continue
            app_name = str(
                getattr(adapter_type, "app_name", "") or adapter_id
            ).strip()
            descriptors.append({
                "adapter_id": adapter_id,
                "app_name": app_name,
                "component": cls._component_id(adapter_type),
                "channels": sorted(channels, key=str.casefold),
                "auto_discover": bool(
                    getattr(adapter_type, "auto_discover", False)
                ),
            })
        return sorted(
            descriptors,
            key=lambda item: str(item["adapter_id"]).casefold(),
        )

    @classmethod
    def registered_channels(cls) -> list[str]:
        return sorted(cls._types)
