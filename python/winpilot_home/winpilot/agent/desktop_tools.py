"""General desktop utility tool handlers for ToolSession."""
from __future__ import annotations

import base64
import ctypes
import threading
from io import BytesIO
from pathlib import Path

from ..action import executor
from ..action.verify import _match_visible_text
from ..config import CONFIG
from ..perception import vision_cv
from ..utils.log import BUS, logger
from ..utils.screenshot import Region, capture, desktop_geometry, screen_size, window_region
from .interaction_tools import _call_with_optional_hwnd
from .tool_result import ToolResult


class DesktopToolMixin:
    def _cancel_pointer_hold_timeout(self) -> None:
        timer = getattr(self, "_pointer_hold_timer", None)
        self._pointer_hold_timer = None
        self._pointer_hold_generation = int(
            getattr(self, "_pointer_hold_generation", 0) or 0
        ) + 1
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                logger.debug("pointer hold timer cancellation failed", exc_info=True)

    def _arm_pointer_hold_timeout(self, button: str) -> float:
        self._cancel_pointer_hold_timeout()
        timeout_s = max(15.0, min(180.0, float(CONFIG.get(
            "controller", "pointer_hold_timeout_s", default=75.0,
        ) or 75.0)))
        generation = int(getattr(self, "_pointer_hold_generation", 0) or 0)
        self._pointer_hold_button = str(button or "left")

        def expire() -> None:
            if generation != int(getattr(self, "_pointer_hold_generation", 0) or 0):
                return
            held_button = str(getattr(self, "_pointer_hold_button", "") or "")
            if not held_button:
                return
            try:
                result = executor.mouse_up(held_button)
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
            checker = getattr(executor, "agent_holds_mouse_button", None)
            still_active = (
                bool(checker(held_button))
                if callable(checker)
                else not bool(result.get("ok"))
            )
            self._pointer_hold_button = held_button if still_active else ""
            if not still_active:
                self._clear_pointer_constraint()
            self._pointer_hold_timer = None
            BUS.publish(
                "input_cleanup",
                run_id=getattr(self, "run_id", None),
                released_mouse_buttons=([held_button] if not still_active else []),
                reason=(
                    "pointer_hold_timeout_release_failed"
                    if still_active else "pointer_hold_timeout"
                ),
                result=result,
            )

        timer = threading.Timer(timeout_s, expire)
        timer.daemon = True
        self._pointer_hold_timer = timer
        timer.start()
        return timeout_s

    def _pointer_hold_status(self) -> dict:
        button = str(getattr(self, "_pointer_hold_button", "") or "")
        if not button:
            self._clear_pointer_constraint()
            return {"active": False, "button": ""}
        checker = getattr(executor, "agent_holds_mouse_button", None)
        active = bool(checker(button)) if callable(checker) else True
        if not active:
            self._cancel_pointer_hold_timeout()
            self._pointer_hold_button = ""
            self._clear_pointer_constraint()
        status = {"active": active, "button": button if active else ""}
        frame_constraint = getattr(
            self, "_pointer_hold_constraint_frame_region", None,
        )
        if active and isinstance(frame_constraint, dict):
            status["constraint_frame_region"] = dict(frame_constraint)
        return status

    def _clear_pointer_constraint(self) -> None:
        self._pointer_hold_constraint_region = None
        self._pointer_hold_constraint_frame_region = None

    def _pointer_args_with_inherited_constraint(self, args: dict) -> dict:
        effective = dict(args)
        if not any(key in effective for key in (
            "_constraint_region", "constraint_region", "constraint_frame_region",
        )):
            inherited = getattr(self, "_pointer_hold_constraint_region", None)
            if isinstance(inherited, dict):
                effective["_constraint_region"] = dict(inherited)
        return effective

    def _managed_artifacts(self):
        run_id = str(getattr(self, "run_id", "") or "").strip()
        if not run_id:
            return None, None
        from ..tasking.artifacts import ArtifactStore
        from ..tasking.evidence import EvidenceRegistry

        store = getattr(self, "managed_artifact_store", None)
        if not isinstance(store, ArtifactStore):
            store = ArtifactStore(run_id)
            self.managed_artifact_store = store
        registry = getattr(self, "evidence_registry", None)
        if not isinstance(registry, EvidenceRegistry):
            registry = EvidenceRegistry(run_id)
            self.evidence_registry = registry
        return store, registry

    def _t_clipboard_read(self, _args: dict) -> ToolResult:
        text = executor.clipboard_read()
        return ToolResult(text or "（剪贴板为空）",
                          summary=f"[已折叠] clipboard_read {len(text)}字符")

    def _t_clipboard_write(self, args: dict) -> ToolResult:
        text = str(args.get("text", ""))
        result = executor.clipboard_write(text)
        return ToolResult(self._fmt(result))

    def _t_clipboard_paste(self, _args: dict) -> ToolResult:
        target = getattr(self, "_action_hwnd", lambda: None)()
        result = _call_with_optional_hwnd(
            executor.clipboard_paste, hwnd=target,
        )
        return ToolResult(self._fmt(result))

    def _t_cursor_position(self, _args: dict) -> ToolResult:
        result = executor.cursor_position()
        return ToolResult(self._fmt(result))

    def _t_mouse_down(self, args: dict) -> ToolResult:
        button = str(args.get("button", "left"))
        active_hold = self._pointer_hold_status()
        if active_hold.get("active"):
            held_button = str(active_hold.get("button") or "left")
            return ToolResult(self._fmt({
                "ok": False,
                "error": (
                    f"cannot press {button}; the agent already holds {held_button}"
                ),
                "pointer_hold_active": True,
                "pointer_hold_button": held_button,
            }))
        target = getattr(self, "_action_hwnd", lambda: None)()
        preposition = None
        if args.get("x") is not None or args.get("y") is not None:
            if args.get("x") is None or args.get("y") is None:
                return ToolResult(self._fmt({
                    "ok": False,
                    "error": "mouse_down requires x and y together",
                }))
            constraint_block = self._spatial_constraint_block(
                args,
                [(int(args["x"]), int(args["y"]))],
                action="mouse_down",
            )
            if constraint_block is not None:
                return constraint_block
            preposition = _call_with_optional_hwnd(
                executor.move_mouse,
                int(args["x"]),
                int(args["y"]),
                hwnd=target,
            )
            if preposition.get("ok") is not True:
                return ToolResult(self._fmt({
                    "ok": False,
                    "error": preposition.get("error") or "mouse_down preposition failed",
                    "preposition": preposition,
                }))
        result = _call_with_optional_hwnd(
            executor.mouse_down, button, hwnd=target,
        )
        if result.get("ok") is True:
            timeout_s = self._arm_pointer_hold_timeout(button)
            physical_constraint = args.get("_constraint_region")
            if physical_constraint is None:
                physical_constraint = args.get("constraint_region")
            self._pointer_hold_constraint_region = (
                dict(physical_constraint)
                if isinstance(physical_constraint, dict) else None
            )
            source_frame = args.get("source_frame_coordinates")
            frame_constraint = (
                source_frame.get("constraint_frame_region")
                if isinstance(source_frame, dict) else None
            )
            self._pointer_hold_constraint_frame_region = (
                dict(frame_constraint)
                if isinstance(frame_constraint, dict) else None
            )
            result.update({
                "pointer_hold_active": True,
                "pointer_hold_timeout_s": timeout_s,
                "next_action": (
                    "inspect the fresh frame; release with mouse_up if the final "
                    "target is reached, otherwise make one move_mouse continuation"
                ),
            })
        if preposition is not None:
            result["preposition"] = preposition
        return ToolResult(self._fmt(result))

    def _t_mouse_up(self, args: dict) -> ToolResult:
        args = self._pointer_args_with_inherited_constraint(args)
        hold = self._pointer_hold_status()
        held_button = str(hold.get("button") or "") if hold.get("active") else ""
        requested_button = str(args.get("button") or "")
        if held_button and requested_button and requested_button != held_button:
            return ToolResult(self._fmt({
                "ok": False,
                "error": (
                    f"mouse_up requested {requested_button}, but the active "
                    f"agent-held button is {held_button}"
                ),
                "pointer_hold_active": True,
                "pointer_hold_button": held_button,
            }))
        button = requested_button or held_button or "left"
        target = getattr(self, "_action_hwnd", lambda: None)()
        preposition = None
        if args.get("x") is not None or args.get("y") is not None:
            if args.get("x") is None or args.get("y") is None:
                return ToolResult(self._fmt({
                    "ok": False,
                    "error": "mouse_up requires x and y together",
                }))
            release_point = (int(args["x"]), int(args["y"]))
            constraint_block = self._spatial_constraint_block(
                args, [release_point], action="mouse_up",
            )
            if constraint_block is not None:
                return constraint_block
            preposition = _call_with_optional_hwnd(
                executor.move_mouse,
                int(args["x"]),
                int(args["y"]),
                hwnd=target,
            )
        elif any(key in args for key in ("_constraint_region", "constraint_region")):
            cursor = executor.cursor_position()
            if cursor.get("ok") is not True:
                return ToolResult(self._fmt({
                    "ok": False,
                    "blocked": True,
                    "error": "cannot verify current cursor against the held-pointer constraint",
                    "pointer_hold_active": bool(held_button),
                }))
            constraint_block = self._spatial_constraint_block(
                args,
                [(int(cursor["x"]), int(cursor["y"]))],
                action="mouse_up",
            )
            if constraint_block is not None:
                return constraint_block
        # Releasing an agent-owned button is safety cleanup and must still run
        # if cursor prepositioning was interrupted by lost ownership.
        result = executor.mouse_up(button=button)
        checker = getattr(executor, "agent_holds_mouse_button", None)
        still_active = bool(checker(button)) if callable(checker) else not bool(
            result.get("ok")
        )
        if still_active:
            self._pointer_hold_button = button
            result["ok"] = False
            result["pointer_hold_active"] = True
            result["pointer_hold_button"] = button
            result.setdefault("error", "mouse button remains held after mouse_up")
        else:
            self._cancel_pointer_hold_timeout()
            self._pointer_hold_button = ""
            self._clear_pointer_constraint()
            result["pointer_hold_active"] = False
        if preposition is not None:
            result["preposition"] = preposition
            if preposition.get("ok") is not True and not still_active:
                result["ok"] = False
                result["error"] = (
                    preposition.get("error")
                    or "mouse_up released safely but endpoint preposition failed"
                )
        return ToolResult(self._fmt(result))

    def _t_move_mouse(self, args: dict) -> ToolResult:
        args = self._pointer_args_with_inherited_constraint(args)
        x, y = int(args["x"]), int(args["y"])
        target = getattr(self, "_action_hwnd", lambda: None)()
        hold = self._pointer_hold_status()
        constraint_block = self._spatial_constraint_block(
            args, [(x, y)], action="move_mouse",
        )
        if constraint_block is not None:
            return constraint_block
        result = _call_with_optional_hwnd(
            executor.move_mouse, x, y, hwnd=target,
        )
        if result.get("ok") is True and hold.get("active"):
            physical_constraint = args.get("_constraint_region")
            if isinstance(physical_constraint, dict):
                self._pointer_hold_constraint_region = dict(physical_constraint)
            result.update({
                "pointer_hold_active": True,
                "pointer_hold_button": hold["button"],
                "next_action": (
                    "inspect the fresh frame; call mouse_up now if the intended "
                    "release condition is visible, otherwise move once more"
                ),
            })
        return ToolResult(self._fmt(result))

    def _t_screenshot(self, args: dict) -> ToolResult:
        quality = int(args.get("quality", 75))
        if args.get("managed_artifact") is True:
            try:
                store, registry = self._managed_artifacts()
                if store is None:
                    return ToolResult(self._fmt({"ok": False, "error": "managed artifact store is unavailable"}))
                scope = str(args.get("scope") or "screen").strip().casefold()
                source_hwnd = int(args.get("hwnd") or 0)
                if scope in {"window", "canvas"} and not source_hwnd:
                    action_hwnd = getattr(self, "_action_hwnd", None)
                    source_hwnd = int(action_hwnd() or 0) if callable(action_hwnd) else 0
                artifact = store.capture_artifact(
                    scope=scope,
                    source_hwnd=source_hwnd,
                    region=args.get("region"),
                    source_window=str(args.get("source_window") or ""),
                    purpose=str(args.get("purpose") or "user_output"),
                    sendable=bool(args.get("sendable", True)),
                    metadata={"flow_managed_artifact": True},
                )
                if registry is not None:
                    registry.record(
                        "artifact.current_run_valid",
                        provider="artifact_store_capture",
                        verified=True,
                        artifact_id=artifact.artifact_id,
                        metadata={"flow_managed_artifact": True, "sha256": artifact.sha256},
                    )
                return ToolResult(self._fmt({
                    "ok": True,
                    "artifact_id": artifact.artifact_id,
                    "path": artifact.managed_path,
                    "managed_path": artifact.managed_path,
                    "sha256": artifact.sha256,
                    "mime_type": artifact.mime_type,
                    "width": artifact.width,
                    "height": artifact.height,
                    "size_bytes": artifact.size_bytes,
                    "scope": artifact.source_scope,
                    "region": artifact.metadata.get("region"),
                }))
            except Exception as exc:
                return ToolResult(self._fmt({"ok": False, "error": str(exc)}))
        save_path = str(args.get("path") or "").strip()
        if save_path:
            target = Path(save_path).expanduser()
            if not target.is_absolute():
                return ToolResult("screenshot path must be absolute")
            if target.suffix.casefold() not in {".png", ".jpg", ".jpeg"}:
                return ToolResult("screenshot path must end in .png, .jpg, or .jpeg")
            if target.exists() and not bool(args.get("overwrite")):
                return ToolResult(f"screenshot path already exists: {target}")
            if not target.parent.is_dir():
                return ToolResult(f"screenshot directory does not exist: {target.parent}")
            try:
                region = None
                hwnd = None
                if bool(args.get("window")):
                    hwnd = int(args.get("hwnd") or getattr(self, "hwnd", 0) or 0)
                    if not hwnd:
                        action_hwnd = getattr(self, "_action_hwnd", None)
                        hwnd = int(action_hwnd() or 0) if callable(action_hwnd) else 0
                    if not hwnd:
                        return ToolResult("screenshot window=true requires an active target window")
                    region = window_region(hwnd)
                elif all(key in args for key in ("x", "y", "width", "height")):
                    region = Region(
                        int(args["x"]), int(args["y"]),
                        int(args["width"]), int(args["height"]),
                    ).clamp_to_screen()
                frame = capture(region)
                from PIL import Image

                image = Image.fromarray(frame[:, :, ::-1].copy())
                payload = BytesIO()
                image_format = "PNG" if target.suffix.casefold() == ".png" else "JPEG"
                save_options = {"format": image_format}
                if image_format == "JPEG":
                    save_options.update({"quality": max(1, min(100, quality)), "optimize": True})
                image.save(payload, **save_options)
                target.write_bytes(payload.getvalue())
                result = {
                    "ok": True,
                    "path": str(target),
                    "width": int(image.width),
                    "height": int(image.height),
                    "size_bytes": len(payload.getvalue()),
                    "scope": "window" if hwnd else ("region" if region else "screen"),
                }
                if region is not None:
                    result["region"] = {
                        "left": region.left,
                        "top": region.top,
                        "width": region.width,
                        "height": region.height,
                    }
                saved = getattr(self, "saved_screenshot_paths", None)
                if isinstance(saved, list):
                    saved.append(str(target))
                    self.saved_screenshot_paths = saved[-12:]
                store, registry = self._managed_artifacts()
                if store is not None:
                    artifact = store.import_file(
                        target,
                        purpose="user_output",
                        sendable=True,
                        metadata={"compatibility_tool": "screenshot"},
                    )
                    result["artifact_id"] = artifact.artifact_id
                    result["managed_path"] = artifact.managed_path
                    result["sha256"] = artifact.sha256
                    if registry is not None:
                        registry.record(
                            "artifact.current_run_valid",
                            provider="artifact_store_capture",
                            verified=True,
                            artifact_id=artifact.artifact_id,
                            metadata={"compatibility_tool": "screenshot", "sha256": artifact.sha256},
                        )
                return ToolResult(self._fmt(result))
            except Exception as exc:
                return ToolResult(self._fmt({"ok": False, "error": str(exc)}))
        result = executor.screenshot_base64(quality=quality)
        self._fold_base64(result)
        return ToolResult(self._fmt(result))

    def _t_capture_artifact(self, args: dict) -> ToolResult:
        store, registry = self._managed_artifacts()
        if store is None:
            return ToolResult(self._fmt({"ok": False, "error": "capture_artifact requires an active run_id"}))
        scope = str(args.get("scope") or "window").strip().casefold()
        if scope not in {"window", "screen", "canvas", "region"}:
            return ToolResult(self._fmt({
                "ok": False,
                "verified": False,
                "failure_type": "invalid_artifact_scope",
                "error": (
                    "capture_artifact scope must be one of window, screen, canvas, or region; "
                    f"received {scope!r}. Do not invent semantic scope names."
                ),
            }))
        hwnd = int(args.get("source_hwnd") or getattr(self, "hwnd", 0) or 0)
        region = args.get("region")
        try:
            artifact = store.capture_artifact(
                scope=scope,
                source_hwnd=hwnd,
                region=region,
                purpose="user_output",
                sendable=True,
                metadata={"tool": "capture_artifact"},
            )
        except Exception as exc:
            return ToolResult(self._fmt({"ok": False, "error": str(exc)}))
        evidence = registry.record(
            "artifact.current_run_valid",
            provider="artifact_store_capture",
            verified=True,
            artifact_id=artifact.artifact_id,
            metadata={"sha256": artifact.sha256, "source_window": artifact.source_window},
        ) if registry is not None else None
        return ToolResult(self._fmt({
            "ok": True,
            "artifact_id": artifact.artifact_id,
            "sha256": artifact.sha256,
            "managed_path": artifact.managed_path,
            "artifact": artifact.model_dump(mode="json"),
            "evidence": evidence,
            "fast_path_advice": (
                f"The exact managed artifact_id is {artifact.artifact_id}. Pass this exact "
                "value to send_artifact; do not invent or rename the handle."
            ),
        }))

    @staticmethod
    def _communication_gui_fallback(
        operation: str,
        *,
        app: str = "",
        recipient: str = "",
        error: str = "",
        **details,
    ) -> dict:
        return {
            "ok": False,
            "verified": False,
            "provider_available": False,
            "failure_type": "provider_unavailable",
            "operation": str(operation or "communication"),
            "app": str(app or ""),
            "recipient": str(recipient or ""),
            "error": str(error or (
                "No explicit communication provider is registered for this route."
            )),
            **details,
        }

    def _communication_adapter(self, app: str = "", recipient: str = ""):
        from ..tasking.adapters import CommunicationAdapter, DomainAdapterRegistry

        adapter = getattr(self, "communication_adapter", None)
        if isinstance(adapter, CommunicationAdapter):
            return adapter
        app = str(app or "").strip()
        if not app:
            return None
        if not DomainAdapterRegistry.probe(channel=app).get("registered"):
            return None
        store, registry = self._managed_artifacts()
        if store is None or registry is None:
            raise ValueError("communication adapter requires an active run_id")
        adapter = DomainAdapterRegistry.create(
            app,
            run_id=str(getattr(self, "run_id", "")),
            task=str(getattr(self, "task", "")),
            store=store,
            evidence=registry,
            stop_event=getattr(self, "stop_event", None),
            task_intent=getattr(self, "task_intent", None),
        )
        self.communication_adapter = adapter
        if recipient:
            adapter.recipient = str(recipient)
        return adapter

    def _discover_communication_route(self, recipient: str) -> dict:
        """Ground and bind one installed communication route for a recipient.

        Adapter registration is extensible capability metadata, not proof that
        an app exists or that a recipient is available.  Rank only candidates
        grounded by the live process/window list or installed-app index, then
        require the adapter to prove both the recipient header and composer.
        """

        from ..perception import apps as app_catalog
        from ..perception import win32 as win32_perception
        from ..tasking.adapters import DomainAdapterRegistry

        recipient = str(recipient or "").strip()
        if not recipient:
            return {
                "ok": False,
                "verified": False,
                "failure_type": "recipient_not_resolved",
                "error": "open_conversation requires a recipient",
            }
        descriptors = [
            descriptor
            for descriptor in DomainAdapterRegistry.registered_adapters()
            if descriptor.get("auto_discover") is True
        ]
        if not descriptors:
            return self._communication_gui_fallback(
                "open_conversation",
                recipient=recipient,
                registered_providers=DomainAdapterRegistry.registered_adapters(),
            )
        store, registry = self._managed_artifacts()
        if store is None or registry is None:
            return {
                "ok": False,
                "verified": False,
                "failure_type": "run_context_missing",
                "error": "communication route discovery requires an active run_id",
            }

        candidates: list[dict] = []
        discovery_errors: list[dict] = []
        for descriptor in descriptors:
            app_name = str(descriptor.get("app_name") or "").strip()
            if not app_name:
                continue
            running: dict = {}
            visible_window: dict | None = None
            installed: list[dict] = []
            try:
                running = win32_perception.find_app(app_name) or {}
            except Exception as exc:
                discovery_errors.append({"app": app_name, "source": "process", "error": str(exc)})
            try:
                visible_window = win32_perception.find_window(app_name)
            except Exception as exc:
                discovery_errors.append({"app": app_name, "source": "window", "error": str(exc)})
            try:
                installed = app_catalog.resolve(app_name, max_results=3) or []
            except Exception as exc:
                discovery_errors.append({"app": app_name, "source": "installed_apps", "error": str(exc)})

            best_install = installed[0] if installed else {}
            match_score = int(best_install.get("match_score") or (100 if best_install else 0))
            installed_grounded = bool(
                best_install
                and (
                    match_score >= 65
                    or str(best_install.get("match_kind") or "") != "fuzzy"
                )
            )
            running_grounded = bool(running.get("running"))
            window_grounded = bool(visible_window)
            if not (running_grounded or window_grounded or installed_grounded):
                continue
            rank = (
                300 if bool(running.get("has_visible_window")) or window_grounded
                else 200 if running_grounded
                else 100
            ) + min(match_score, 100)
            candidates.append({
                **descriptor,
                "rank": rank,
                "grounding": {
                    "running": running_grounded,
                    "visible_window": window_grounded,
                    "installed": installed_grounded,
                    "installed_match": str(best_install.get("name") or ""),
                    "installed_match_score": match_score,
                },
            })

        candidates.sort(
            key=lambda item: (
                -int(item.get("rank") or 0),
                str(item.get("adapter_id") or "").casefold(),
            )
        )
        attempts: list[dict] = []
        for candidate in candidates[:4]:
            adapter = None
            try:
                adapter = DomainAdapterRegistry.create(
                    str(candidate.get("adapter_id") or ""),
                    run_id=str(getattr(self, "run_id", "")),
                    task=str(getattr(self, "task", "")),
                    store=store,
                    evidence=registry,
                    stop_event=getattr(self, "stop_event", None),
                    task_intent=getattr(self, "task_intent", None),
                )
                opened = adapter.open_conversation(recipient)
                composer = adapter.resolve_composer() if opened.get("ok") is True else {}
                route_ok = bool(opened.get("ok") is True and composer.get("ok") is True)
                attempt = {
                    "adapter_id": str(candidate.get("adapter_id") or ""),
                    "app": str(candidate.get("app_name") or ""),
                    "grounding": candidate.get("grounding") or {},
                    "recipient_verified": bool(opened.get("ok") is True),
                    "composer_verified": bool(composer.get("ok") is True),
                }
                if not route_ok:
                    attempt["failure_type"] = str(
                        composer.get("failure_type")
                        or opened.get("failure_type")
                        or "communication_route_unverified"
                    )
                    attempt["error"] = str(
                        composer.get("error") or opened.get("error") or ""
                    )[:1200]
                attempts.append(attempt)
                if route_ok:
                    self.communication_adapter = adapter
                    hwnd = int(
                        composer.get("target_hwnd")
                        or opened.get("target_hwnd")
                        or 0
                    )
                    if hwnd:
                        self._remember_target(
                            hwnd,
                            str(
                                composer.get("target_title")
                                or opened.get("target_title")
                                or candidate.get("app_name")
                                or ""
                            ),
                        )
                    return {
                        "ok": True,
                        "verified": True,
                        "route_discovered": True,
                        "channel": str(candidate.get("adapter_id") or ""),
                        "app": str(candidate.get("app_name") or ""),
                        "recipient": recipient,
                        "target_hwnd": hwnd or None,
                        "grounding": candidate.get("grounding") or {},
                        "conversation": opened,
                        "composer": composer,
                        "attempts": attempts,
                        "fast_path_advice": (
                            "The recipient and composer are now verified and bound. "
                            "Continue directly with send_text or send_artifact; do not rediscover the route."
                        ),
                    }
            except Exception as exc:
                attempts.append({
                    "adapter_id": str(candidate.get("adapter_id") or ""),
                    "app": str(candidate.get("app_name") or ""),
                    "grounding": candidate.get("grounding") or {},
                    "failure_type": "communication_route_error",
                    "error": str(exc)[:1200],
                })
            if adapter is not None:
                try:
                    adapter.close()
                except Exception:
                    pass

        return self._communication_gui_fallback(
            "open_conversation",
            recipient=recipient,
            error=(
                "No explicitly auto-discoverable provider proved the requested recipient "
                "and composer. Continue through the unified visual desktop Agent."
            ),
            route_unresolved=True,
            grounded_candidates=[
                {
                    "adapter_id": str(item.get("adapter_id") or ""),
                    "app": str(item.get("app_name") or ""),
                    "grounding": item.get("grounding") or {},
                }
                for item in candidates
            ],
            attempts=attempts,
            discovery_errors=discovery_errors,
        )

    def _t_open_conversation(self, args: dict) -> ToolResult:
        try:
            app = str(args.get("app") or "").strip()
            recipient = str(args.get("recipient") or "").strip()
            if not app:
                return ToolResult(self._fmt(
                    self._discover_communication_route(recipient)
                ))
            adapter = self._communication_adapter(app, recipient)
            if adapter is None:
                return ToolResult(self._fmt(self._communication_gui_fallback(
                    "open_conversation",
                    app=app,
                    recipient=recipient,
                )))
            result = adapter.open_conversation(recipient)
            if result.get("ok") is True:
                composer = adapter.resolve_composer()
                result["composer"] = composer
                result["verified"] = bool(composer.get("ok") is True)
                if composer.get("ok") is not True:
                    result["ok"] = False
                    result["failure_type"] = str(
                        composer.get("failure_type") or "composer_not_resolved"
                    )
                    result["error"] = str(
                        composer.get("error")
                        or "recipient was opened but the composer could not be verified"
                    )
            target_hwnd = int(
                result.get("target_hwnd")
                or (result.get("composer") or {}).get("target_hwnd")
                or 0
            )
            if target_hwnd:
                self.hwnd = target_hwnd
            return ToolResult(self._fmt(result))
        except Exception as exc:
            return ToolResult(self._fmt({"ok": False, "error": str(exc)}))

    def _t_send_artifact(self, args: dict) -> ToolResult:
        requested_artifact_id = str(args.get("artifact_id") or "").strip()
        try:
            app = str(args.get("app") or "")
            recipient = str(args.get("recipient") or "")
            adapter = self._communication_adapter(app, recipient)
            if adapter is None:
                return ToolResult(self._fmt(self._communication_gui_fallback(
                    "send_artifact",
                    app=app,
                    recipient=recipient,
                    requested_artifact_id=requested_artifact_id,
                )))
            store, _registry = self._managed_artifacts()
            if store is None:
                raise ValueError("send_artifact requires an active run_id")
            artifact, resolution = store.resolve_sendable(requested_artifact_id)
            artifact_id = artifact.artifact_id
            staged = adapter.paste_or_attach_artifact(artifact_id)
            if staged.get("ok") is not True:
                return ToolResult(self._fmt({
                    **staged,
                    "artifact_id": artifact_id,
                    "requested_artifact_id": requested_artifact_id,
                    "artifact_resolution": resolution,
                }))
            sent = adapter.send_current_composer(artifact_id=artifact_id)
            return ToolResult(self._fmt({
                "ok": sent.get("ok") is True,
                "artifact_id": artifact_id,
                "requested_artifact_id": requested_artifact_id,
                "artifact_resolution": resolution,
                "staged": staged,
                "sent": sent,
                "fast_path_advice": (
                    f"Artifact {artifact_id} was resolved and sent through the bound route. "
                    "Use this resolved id for verify_delivery."
                ),
            }))
        except Exception as exc:
            return ToolResult(self._fmt({
                "ok": False,
                "verified": False,
                "failure_type": "artifact_reference_unresolved",
                "requested_artifact_id": requested_artifact_id,
                "error": str(exc),
            }))

    def _t_send_text(self, args: dict) -> ToolResult:
        try:
            adapter = self._communication_adapter()
            if adapter is None:
                return ToolResult(self._fmt(self._communication_gui_fallback(
                    "send_text",
                    app=str(args.get("app") or ""),
                    recipient=str(args.get("recipient") or ""),
                )))
            return ToolResult(self._fmt(adapter.send_text(str(args.get("text") or ""))))
        except Exception as exc:
            return ToolResult(self._fmt({"ok": False, "error": str(exc)}))

    def _t_verify_delivery(self, args: dict) -> ToolResult:
        try:
            adapter = self._communication_adapter()
            if adapter is None:
                return ToolResult(self._fmt(self._communication_gui_fallback(
                    "verify_delivery",
                    app=str(args.get("app") or ""),
                    recipient=str(args.get("recipient") or ""),
                )))
            return ToolResult(self._fmt(adapter.verify_latest_outbound(
                artifact_id=str(args.get("artifact_id") or ""),
                text=str(args.get("text") or ""),
            )))
        except Exception as exc:
            return ToolResult(self._fmt({"ok": False, "error": str(exc)}))

    def _t_hold_key(self, args: dict) -> ToolResult:
        keys = args.get("keys") or []
        target = getattr(self, "_action_hwnd", lambda: None)()
        result = executor.hold_keys(
            keys,
            int(args.get("duration_ms", 0)),
            hwnd=target,
            stop_event=getattr(self, "stop_event", None),
        )
        return ToolResult(self._fmt(result))

    def _t_list_displays(self, _args: dict) -> ToolResult:
        result = executor.list_displays()
        return ToolResult(self._fmt(result))

    def _t_coordinate_diagnostics(self, args: dict) -> ToolResult:
        """Report coordinate/capture geometry for DPI/ROI mismatch debugging."""
        payload: dict = {"ok": True}
        try:
            geometry = desktop_geometry()
            sw, sh = screen_size()
            payload["screen_size"] = {
                "left": geometry.virtual.left,
                "top": geometry.virtual.top,
                "right": geometry.virtual.right,
                "bottom": geometry.virtual.bottom,
                "width": int(sw),
                "height": int(sh),
                "source": "GetSystemMetrics virtual desktop",
            }
            payload["primary_screen"] = {
                "left": geometry.primary.left,
                "top": geometry.primary.top,
                "width": geometry.primary.width,
                "height": geometry.primary.height,
            }
        except Exception as exc:
            payload["screen_size_error"] = str(exc)
        try:
            full = capture()
            payload["full_capture"] = {
                "left": payload.get("screen_size", {}).get("left", 0),
                "top": payload.get("screen_size", {}).get("top", 0),
                "width": int(full.shape[1]),
                "height": int(full.shape[0]),
                "source": "mss",
            }
        except Exception as exc:
            payload["full_capture_error"] = str(exc)
        try:
            payload["displays"] = executor.list_displays()
        except Exception as exc:
            payload["displays_error"] = str(exc)

        hwnd = args.get("hwnd") or getattr(self, "hwnd", None)
        region = None
        if hwnd:
            try:
                hwnd = int(hwnd)
                region = window_region(hwnd)
                payload["target_window"] = {
                    "hwnd": hwnd,
                    "region": {
                        "left": region.left,
                        "top": region.top,
                        "width": region.width,
                        "height": region.height,
                        "right": region.right,
                        "bottom": region.bottom,
                    },
                }
                try:
                    dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
                    payload["target_window"]["dpi"] = int(dpi)
                    payload["target_window"]["scale_from_96dpi"] = round(float(dpi) / 96.0, 4)
                except Exception as exc:
                    payload["target_window"]["dpi_error"] = str(exc)
                try:
                    cropped = capture(region)
                    payload["target_window"]["capture"] = {
                        "width": int(cropped.shape[1]),
                        "height": int(cropped.shape[0]),
                        "matches_region": (
                            int(cropped.shape[1]) == int(region.width)
                            and int(cropped.shape[0]) == int(region.height)
                        ),
                    }
                except Exception as exc:
                    payload["target_window"]["capture_error"] = str(exc)
            except Exception as exc:
                payload["target_window_error"] = str(exc)

        if args.get("x") is not None and args.get("y") is not None:
            x, y = int(args["x"]), int(args["y"])
            point = {"x": x, "y": y}
            if region is not None:
                point["relative_to_target"] = {
                    "x": x - region.left,
                    "y": y - region.top,
                    "inside": region.left <= x < region.right and region.top <= y < region.bottom,
                }
            try:
                probe = capture(Region(x - 2, y - 2, 5, 5))
                if getattr(probe, "size", 0):
                    center = probe[min(2, probe.shape[0] - 1), min(2, probe.shape[1] - 1)].tolist()
                    point["bgr_at_point"] = [int(v) for v in center[:3]]
            except Exception as exc:
                point["probe_error"] = str(exc)
            payload["point"] = point

        warnings: list[str] = []
        screen = payload.get("screen_size") or {}
        full_capture = payload.get("full_capture") or {}
        if screen and full_capture and (
            int(screen.get("width") or -1) != int(full_capture.get("width") or -2)
            or int(screen.get("height") or -1) != int(full_capture.get("height") or -2)
        ):
            warnings.append("screen_size differs from full_capture; check DPI awareness or virtual monitor origin.")
        target = payload.get("target_window") or {}
        target_capture = target.get("capture") if isinstance(target, dict) else {}
        if isinstance(target_capture, dict) and target_capture.get("matches_region") is False:
            warnings.append("target window capture size does not match window_region; ROI coordinate mapping is suspect.")
        payload["warnings"] = warnings
        return ToolResult(self._fmt(payload))

    def _t_coordinate_click_test(self, args: dict) -> ToolResult:
        """Perform one real mouse click and return coordinate evidence."""
        x, y = int(args["x"]), int(args["y"])
        remembered_hwnd = getattr(self, "hwnd", None)
        requested_hwnd = args.get("hwnd")
        if CONFIG.get("controller", "multi_input_cooperation", default=False):
            try:
                mismatched_target = bool(
                    requested_hwnd
                    and int(requested_hwnd) != int(remembered_hwnd or 0)
                )
            except (TypeError, ValueError):
                mismatched_target = True
            if mismatched_target:
                return ToolResult(self._fmt({
                    "ok": False,
                    "verified": False,
                    "blocked": True,
                    "cooperative_input": True,
                    "error": (
                        "coordinate_click_test cannot override the remembered "
                        "cooperative target hwnd"
                    ),
                    "target_hwnd": int(remembered_hwnd or 0),
                }))
            hwnd = remembered_hwnd
        else:
            hwnd = requested_hwnd or remembered_hwnd
        try:
            hwnd = int(hwnd) if hwnd else None
        except (TypeError, ValueError):
            hwnd = None
        button = str(args.get("button") or "left")
        double = bool(args.get("double", False))
        payload: dict = {
            "ok": False,
            "requested": {"x": x, "y": y},
            "button": button,
            "double": double,
            "real_mouse_path": True,
            "note": (
                "This tool calls executor.click with physical screen coordinates. "
                "Compare requested.x/y with the target app's own click echo. "
                "Browser event.screenX/screenY may be CSS screen coordinates under DPI scaling, "
                "not physical pixels."
            ),
        }
        try:
            virtual = desktop_geometry().virtual
            payload["virtual_screen"] = {
                "left": virtual.left,
                "top": virtual.top,
                "right": virtual.right,
                "bottom": virtual.bottom,
            }
            if not (virtual.left <= x < virtual.right and virtual.top <= y < virtual.bottom):
                payload["blocked"] = True
                payload["error"] = "Requested point is outside the virtual desktop bounds."
                return ToolResult(self._fmt(payload))
        except Exception as exc:
            payload["geometry_error"] = str(exc)
        try:
            payload["cursor_before"] = executor.cursor_position()
        except Exception as exc:
            payload["cursor_before_error"] = str(exc)
        if hwnd:
            try:
                region = window_region(hwnd)
                payload["target_window"] = {
                    "hwnd": hwnd,
                    "region": {
                        "left": region.left,
                        "top": region.top,
                        "width": region.width,
                        "height": region.height,
                        "right": region.right,
                        "bottom": region.bottom,
                    },
                    "requested_relative": {
                        "x": x - region.left,
                        "y": y - region.top,
                        "inside": region.left <= x < region.right and region.top <= y < region.bottom,
                    },
                }
                try:
                    dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
                    payload["target_window"]["dpi"] = int(dpi)
                    payload["target_window"]["scale_from_96dpi"] = round(float(dpi) / 96.0, 4)
                except Exception as exc:
                    payload["target_window"]["dpi_error"] = str(exc)
            except Exception as exc:
                payload["target_window_error"] = str(exc)
        try:
            click_result = executor.click(x, y, button=button, double=double, hwnd=hwnd)
            payload["click_result"] = click_result
            payload["ok"] = bool(click_result.get("ok"))
        except Exception as exc:
            payload["error"] = str(exc)
            return ToolResult(self._fmt(payload))
        try:
            cursor_after = executor.cursor_position()
            payload["cursor_after"] = cursor_after
            if cursor_after.get("ok"):
                dx = int(cursor_after.get("x", x)) - x
                dy = int(cursor_after.get("y", y)) - y
                payload["cursor_error"] = {"dx": dx, "dy": dy}
                payload["coordinate_verified"] = abs(dx) <= 1 and abs(dy) <= 1
                if not payload["coordinate_verified"]:
                    payload["ok"] = False
                    payload["error"] = "Real cursor position does not match the requested physical coordinate."
        except Exception as exc:
            payload["cursor_after_error"] = str(exc)
        wait_ms = args.get("wait_ms", 250)
        try:
            import time
            time.sleep(max(0.0, min(float(wait_ms), 3000.0)) / 1000.0)
        except Exception:
            pass
        expected = str(args.get("expect_text") or "").strip()
        if expected:
            try:
                read = self._t_read_text({"hwnd": hwnd} if hwnd else {})
                payload["read_text_preview"] = read.text[:1200]
                payload["verified"] = bool(_match_visible_text(expected, read.text))
                payload["expect_text"] = expected
            except Exception as exc:
                payload["verify_error"] = str(exc)
                payload["verified"] = False
        return ToolResult(self._fmt(payload))

    def _t_zoom(self, args: dict) -> ToolResult:
        result = executor.zoom_region(
            int(args["x"]), int(args["y"]),
            int(args["width"]), int(args["height"]),
            quality=int(args.get("quality", 80)),
        )
        self._fold_base64(result)
        return ToolResult(self._fmt(result))

    def _t_list_installed_apps(self, args: dict) -> ToolResult:
        try:
            import winreg
            results: dict[str, dict] = {}
            reg_paths = [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            ]
            for hive, sub_key in reg_paths:
                try:
                    key = winreg.OpenKey(hive, sub_key)
                except OSError:
                    continue
                try:
                    i = 0
                    while True:
                        try:
                            name = winreg.EnumKey(key, i)
                            i += 1
                        except OSError:
                            break
                        try:
                            app_key = winreg.OpenKey(key, name)
                        except OSError:
                            continue
                        try:
                            display_name = winreg.QueryValueEx(app_key, "DisplayName")[0]
                        except OSError:
                            winreg.CloseKey(app_key)
                            continue
                        try:
                            install_location = winreg.QueryValueEx(app_key, "InstallLocation")[0]
                        except OSError:
                            install_location = ""
                        try:
                            display_icon = winreg.QueryValueEx(app_key, "DisplayIcon")[0]
                        except OSError:
                            display_icon = ""
                        normalized_icon = str(display_icon).split(",")[0].strip().strip('"')
                        normalized_install_location = str(install_location).strip().strip('"')
                        bundle_id = name
                        for candidate in (normalized_icon, normalized_install_location):
                            if not candidate:
                                continue
                            candidate_path = Path(candidate)
                            if candidate_path.suffix.lower() == ".exe":
                                bundle_id = candidate_path.stem
                                break
                        app_path = normalized_icon or normalized_install_location or ""
                        if bundle_id not in results:
                            results[bundle_id] = {
                                "bundleId": bundle_id,
                                "displayName": str(display_name),
                                "path": app_path,
                            }
                        winreg.CloseKey(app_key)
                finally:
                    winreg.CloseKey(key)
            apps = sorted(results.values(), key=lambda item: item["displayName"].lower())
            offset = max(0, int(args.get("offset") or 0))
            limit = max(1, min(200, int(args.get("limit") or 80)))
            page = apps[offset:offset + limit]
            next_offset = offset + len(page) if offset + len(page) < len(apps) else None
            lines = [
                f'{i}. "{a["displayName"]}" [{a["bundleId"]}] {a["path"]}'
                for i, a in enumerate(page, offset + 1)
            ]
            range_text = (
                f"{offset + 1}-{offset + len(page)}" if page else "empty"
            )
            pagination = (
                f" next_offset={next_offset}; call again with offset={next_offset} to continue."
                if next_offset is not None else " complete=true."
            )
            return ToolResult(
                f"已安装应用 total={len(apps)} range={range_text};{pagination}\n" + "\n".join(lines)
                or "未找到已安装应用",
                summary=f"[已折叠] list_installed_apps {len(apps)}个应用",
            )
        except Exception as exc:
            return ToolResult(f"列举已安装应用失败: {exc}")

    @staticmethod
    def _fold_base64(result: dict) -> None:
        if result.get("ok") and result.get("base64"):
            b64 = result["base64"]
            DesktopToolMixin._add_nonvisual_summary(result, b64)
            result["base64_preview"] = b64[:120] + "...(" + str(len(b64)) + " chars total)"
            result["base64_available"] = True
            result["text_only_note"] = (
                "Base64 image bytes are for trace/debug. A text-only main model "
                "cannot inspect this image directly; use nonvisual_summary, "
                "observe OCR/UIA/CV, or a VLM tool when available."
            )
            del result["base64"]

    @staticmethod
    def _add_nonvisual_summary(result: dict, b64: str) -> None:
        try:
            import cv2
            import numpy as np

            raw = base64.b64decode(b64)
            image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                return
            summary = DesktopToolMixin._compact_image_summary(image)
            canvas = vision_cv.likely_canvas_bbox(image)
            if canvas:
                summary["likely_canvas_bbox"] = canvas
                x1, y1, x2, y2 = canvas
                crop = image[y1:y2, x1:x2]
                summary["likely_canvas_summary"] = DesktopToolMixin._compact_image_summary(crop)
            result["nonvisual_summary"] = summary
        except Exception as exc:
            result["nonvisual_summary_error"] = str(exc)

    @staticmethod
    def _compact_image_summary(image) -> dict:
        ink = vision_cv.image_ink_stats_from_array(image)
        colors = vision_cv.color_presence(image).get("colors", {})
        visible_colors = {
            name: {
                key: value
                for key, value in stats.items()
                if key in {"pixels", "ratio", "bbox", "largest_bbox", "largest_area"}
            }
            for name, stats in colors.items()
            if stats.get("visible")
        }
        return {
            "width": ink.get("width"),
            "height": ink.get("height"),
            "blankish": ink.get("blankish"),
            "ink_ratio": ink.get("ink_ratio"),
            "ink_bbox": ink.get("ink_bbox"),
            "component_count": ink.get("component_count"),
            "visible_colors": visible_colors,
        }
