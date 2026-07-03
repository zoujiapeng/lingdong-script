"""Done-gate verification helpers for AgentRunner.

The runner loop calls ``_verify_done`` whenever the model claims completion.
Keeping this policy outside ``loop.py`` makes the orchestration path easier to
read while preserving the same guard behavior.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from ..config import CONFIG
from ..perception import fusion, intent, vision_cv
from ..utils.log import BUS, logger
from .task_rules import (
    _desktop_icon_target_names,
    _gui_search_payloads,
    _is_desktop_icon_drag_task,
    _is_communication_task,
    _is_drawing_task,
    _is_gui_search_task,
    _looks_like_contact_list,
    _historical_visible_state_targets,
    _quoted_payloads,
    _required_drawing_colors,
    _required_image_paths,
    _required_literal_tokens,
    _required_visible_payloads,
    _visible_state_targets,
)
from ..utils.screenshot import screen_size


_JUDGE_MAX_TOKENS = 2500


def _loop_attr(name: str, default):
    """Preserve historical monkeypatch paths on winpilot.agent.loop."""
    loop_mod = sys.modules.get("winpilot.agent.loop")
    return getattr(loop_mod, name, default) if loop_mod is not None else default


def _config():
    return _loop_attr("CONFIG", CONFIG)


class DoneVerifierMixin:
    @staticmethod
    def _evidence_has_true_flag(evidence: str, flag: str) -> bool:
        normalized = (evidence or "").casefold()
        # Tool results embedded inside run_action_batch previews are JSON
        # strings, so quotes often appear as \" or double-escaped \\\".
        normalized = normalized.replace("\\\\\"", '"').replace('\\"', '"')
        flag = flag.casefold()
        return (
            f'{flag}": true' in normalized
            or f"{flag}=true" in normalized
            or f"{flag}: true" in normalized
        )

    def _history_mentions_action(self, *names: str) -> bool:
        """Return true when a direct tool call or batch sub-action was recorded."""
        if not names:
            return False
        recent_results = getattr(self, "_recent_results", [])
        joined = "\n".join([
            *getattr(self, "_steps_log", []),
            *[
                f"{tool}\n{text}"
                for tool, text in recent_results
            ],
        ])
        for name in names:
            if any(step.startswith(f"{name}(") for step in self._steps_log):
                return True
            if re.search(rf'"(?:action|tool)"\s*:\s*"{re.escape(name)}"', joined):
                return True
            if re.search(rf'"{re.escape(name)}"\s*:', joined):
                return True
        return False

    def _recent_missing_visual_colors(self, colors: list[str]) -> list[str]:
        if not colors:
            return []
        status = {color: "" for color in colors}
        for _tool, text in getattr(self, "_recent_results", []):
            normalized = str(text or "").casefold()
            normalized = normalized.replace("\\\\\"", '"').replace('\\"', '"')
            for color in colors:
                color_key = color.casefold()
                if re.search(rf'"matched_visual_colors"\s*:\s*\[[^\]]*"{re.escape(color_key)}"', normalized):
                    status[color] = "matched"
                elif re.search(rf'"missing_visual_colors"\s*:\s*\[[^\]]*"{re.escape(color_key)}"', normalized):
                    status[color] = "missing"
        return [color for color, value in status.items() if value == "missing"]

    def _recent_matched_visual_colors(self, colors: list[str]) -> set[str]:
        if not colors:
            return set()
        matched: set[str] = set()
        for _tool, text in getattr(self, "_recent_results", []):
            normalized = str(text or "").casefold()
            normalized = normalized.replace("\\\\\"", '"').replace('\\"', '"')
            for color in colors:
                color_key = color.casefold()
                if re.search(rf'"matched_visual_colors"\s*:\s*\[[^\]]*"{re.escape(color_key)}"', normalized):
                    matched.add(color_key)
        return matched

    def _reject_done(self, reason: str, content: str) -> bool:
        """Record a failed done-gate check and nudge the model to continue."""
        self._last_done_reject_reason = reason
        BUS.publish("done_check", run_id=self.run_id, passed=False, reason=reason)
        self._done_rejects += 1
        self.messages.append({"role": "user", "content": content})
        return False

    def _fresh_done_screen(self) -> str:
        """Capture fresh perception for the skeptical final-state check."""
        try:
            fusion_mod = _loop_attr("fusion", fusion)
            intent_mod = _loop_attr("intent", intent)
            hwnd = self.session.hwnd
            title = self.session.snapshot.title if self.session.snapshot else ""
            pmode = intent_mod.perception_mode(self.task, title)
            snap = (
                fusion_mod.observe(hwnd, include_icons=False, perception_mode=pmode)
                if hwnd else self.session.snapshot
            )
            limit = 220 if _is_desktop_icon_drag_task(self.task) else 50
            return snap.to_text(limit) if snap else ""
        except Exception as exc:  # perception must never break the gate
            logger.debug("done 核验 observe 失败: %s", exc)
            return ""

    def _done_evidence(self) -> tuple[str, str]:
        recent = "；".join(self._steps_log[-6:]) or "(无动作记录)"
        evidence = "\n".join(f"- {name}: {out}" for name, out in self._recent_results) \
            or "(无)"
        ledger = getattr(self, "_visible_evidence_ledger", [])
        if ledger:
            evidence += "\n- historical_visible_evidence: " + json.dumps(
                ledger[-20:], ensure_ascii=False
            )[:2000]
        if _is_drawing_task(self.task):
            stats = []
            for path in _required_image_paths(self.task):
                try:
                    item = vision_cv.image_ink_stats(path)
                    item["color_stats"] = vision_cv.image_color_stats(path)
                    stats.append(item)
                except Exception as exc:
                    stats.append({"path": path, "error": str(exc)})
            if stats:
                evidence += "\n- image_ink_stats: " + json.dumps(
                    stats, ensure_ascii=False
                )[:1200]
        return recent, evidence

    def _verify_visible_payloads(self, screen: str, evidence: str) -> bool:
        required_payloads = _required_visible_payloads(self.task)
        if not required_payloads:
            return True
        haystack = f"{screen}\n{evidence}"
        missing = [payload for payload in required_payloads if payload not in haystack]
        if not missing:
            return True
        reason = (
            "写入/输入任务缺少最终可见证据：最终 observe/read_text 或确定性证据里没有出现 "
            + "、".join(missing[:3])
        )
        logger.info("done 被快速核验拒绝(输入文本缺失): %s", reason)
        return self._reject_done(
            reason,
            f"[完成校验未通过] {reason}。请先在目标窗口真正输入这些文本，"
            "然后重新 observe/read_text 验证文本可见，再调用 done。",
        )

    def _verify_required_literal_tokens(self, screen: str, evidence: str) -> bool:
        tokens = _required_literal_tokens(self.task)
        if not tokens:
            return True
        haystack = f"{screen}\n{evidence}"
        normalized_haystack = self._normalize_visible_text(haystack)
        missing = [
            token for token in tokens
            if token not in haystack
            and self._normalize_visible_text(token) not in normalized_haystack
        ]
        if not missing:
            return True
        reason = (
            "Task-specific literal evidence is missing from the final screen/tool evidence: "
            + ", ".join(missing[:5])
            + ". Do not treat a different/equivalent UI state as completion."
        )
        return self._reject_done(
            reason,
            f"[done check failed] {reason} Put or reveal the exact requested literal(s), "
            "then use observe/read_text or an expect check so the exact text appears before done.",
        )

    @staticmethod
    def _normalize_visible_text(text: str) -> str:
        return re.sub(r"[\s_\-:：]+", "", text or "").casefold()

    @staticmethod
    def _task_accepts_historical_visible_state(task: str) -> bool:
        text = (task or "").casefold()
        markers = (
            "appeared", "has appeared", "have appeared", "ever appeared",
            "showed up", "confirmed earlier",
            "曾出现", "曾经出现", "出现过", "都曾出现", "曾经可见",
            "都出现过", "已经出现", "曾显示",
        )
        return any(marker in text for marker in markers)

    def _visible_state_has_deterministic_done_evidence(self, screen: str, evidence: str = "") -> bool:
        historical = self._task_accepts_historical_visible_state(self.task)
        targets = (
            _historical_visible_state_targets(self.task)
            if historical else _visible_state_targets(self.task)
        )
        if not targets:
            return False
        if historical:
            allowed_sources = {
                "observe",
                "read_text",
                "click:verified_expect",
                "type_text:verified_expect",
                "hotkey:verified_expect",
                "launch:verified_expect",
                "run_action_batch:verified_expect",
            }
            ledger = getattr(self, "_visible_evidence_ledger", [])
            seen = {
                self._normalize_visible_text(str(item.get("target") or ""))
                for item in ledger
                if item.get("source") in allowed_sources
            }
            missing = [
                target for target in targets
                if self._normalize_visible_text(target) not in seen
            ]
            source = "historical visible evidence"
        else:
            haystack = self._normalize_visible_text(screen)
            missing = [
                target for target in targets
                if self._normalize_visible_text(target) not in haystack
            ]
            source = "fresh screen evidence"
        if missing:
            return False
        reason = (
            f"Visible state accepted from {source}: "
            + ", ".join(targets[:3])
        )
        BUS.publish("done_check", run_id=self.run_id, passed=True, reason=reason)
        self._done_verified = True
        return True

    def _verify_drawing_done(self, evidence: str) -> bool:
        if not _is_drawing_task(self.task):
            return True
        saw_drag = self._history_mentions_action("drag", "draw_strokes")
        saw_visual_change = self._evidence_has_true_flag(evidence, "visual_change_detected")
        image_paths = _required_image_paths(self.task)
        missing_paths = [path for path in image_paths if not Path(path).exists()]
        required_colors = _required_drawing_colors(self.task)
        missing_recent_colors = self._recent_missing_visual_colors(required_colors)
        if missing_recent_colors:
            reason = (
                "Drawing/canvas task requested specific colors, but recent controller "
                "evidence says those colors are still missing: "
                + ", ".join(missing_recent_colors)
            )
            return self._reject_done(
                reason,
                f"[done check failed] {reason}. Select the requested visible color, "
                "draw again on the canvas, and verify matched_visual_colors before done.",
            )
        if required_colors and not image_paths:
            matched_recent_colors = self._recent_matched_visual_colors(required_colors)
            unmatched_colors = [
                color for color in required_colors
                if color.casefold() not in matched_recent_colors
            ]
            if unmatched_colors:
                reason = (
                    "Drawing/canvas task requested specific colors, but recent controller "
                    "evidence has not matched them yet: "
                    + ", ".join(unmatched_colors)
                )
                return self._reject_done(
                    reason,
                    f"[done check failed] {reason}. Select the requested visible color, "
                    "draw again on the canvas, and verify matched_visual_colors before done.",
                )
        if not saw_drag:
            reason = (
                "Drawing/canvas task has no recorded drag stroke. Tool selection or clicks "
                "are not enough evidence that visible drawing occurred."
            )
            return self._reject_done(
                reason,
                f"[done check failed] {reason} Use drag on the canvas/work surface, "
                "observe the result, then save/verify before done.",
            )
        if image_paths and missing_paths:
            reason = (
                "Drawing/canvas task requested output image file(s), but they do not exist: "
                + ", ".join(missing_paths[:3])
            )
            return self._reject_done(
                reason,
                f"[done check failed] {reason}. Save through the GUI, handle any "
                "Save As/replace dialog, then verify file_exists before done.",
            )
        if image_paths and required_colors:
            missing_colors: dict[str, list[str]] = {}
            for path in image_paths:
                try:
                    stats = vision_cv.image_color_stats(path)
                except Exception as exc:
                    stats = {"path": path, "error": str(exc)}
                colors = stats.get("colors", {}) if isinstance(stats, dict) else {}
                for color in required_colors:
                    if not colors.get(color, {}).get("visible"):
                        missing_colors.setdefault(path, []).append(color)
            if missing_colors:
                reason = (
                    "Drawing/canvas task requested specific colors, but the saved image lacks "
                    "visible color evidence: "
                    + "; ".join(
                        f"{Path(path).name}: {', '.join(colors)}"
                        for path, colors in list(missing_colors.items())[:3]
                    )
                )
                return self._reject_done(
                    reason,
                    f"[done check failed] {reason}. Use the drawing app's visible color "
                    "controls to select the requested colors, add visible strokes/shapes "
                    "in those colors, save again, and verify before done.",
                )
        if not saw_visual_change:
            self.messages.append({
                "role": "user",
                "content": (
                    "[drawing evidence warning] A drag occurred, but recent evidence does not "
                    "show visual_change_detected=true. Before done, observe or add another "
                    "visible stroke inside the canvas and verify the saved image exists."
                ),
            })
        return True

    def _drawing_has_deterministic_done_evidence(self, evidence: str) -> bool:
        """Accept simple canvas drawing tasks without an expensive LLM judge.

        The LLM judge can spend its entire budget in provider-side reasoning and
        return no parseable JSON. For drawing tasks that only require visible
        strokes on the current canvas, the action log and visual summaries are
        stronger evidence than another model's prose.
        """
        if not _is_drawing_task(self.task):
            return False
        if _required_image_paths(self.task) or _required_drawing_colors(self.task):
            return False
        saw_stroke = self._history_mentions_action("drag", "draw_strokes")
        normalized = evidence.lower()
        saw_visual_change = self._evidence_has_true_flag(normalized, "visual_change_detected")
        return saw_stroke and saw_visual_change

    def _desktop_drag_has_deterministic_done_evidence(self, screen: str, evidence: str) -> bool:
        if not _is_desktop_icon_drag_task(self.task):
            return False
        targets = _desktop_icon_target_names(self.task)
        if not targets:
            return False
        normalized = evidence.lower()
        saw_drag = self._history_mentions_action("drag")
        saw_visual_change = self._evidence_has_true_flag(normalized, "visual_change_detected")
        if not (saw_drag and saw_visual_change):
            return False
        wants_top_right = any(term in (self.task or "").casefold() for term in ("top-right", "top right")) \
            or "\u53f3\u4e0a" in (self.task or "")
        sw, sh = screen_size()
        for target in targets:
            line_match = re.search(
                rf'listitem\s+"{re.escape(target)}".*?center=\((\d+),(\d+)\).*?bbox=\((\d+),(\d+),(\d+),(\d+)\)',
                screen,
                flags=re.IGNORECASE,
            )
            if not line_match:
                continue
            cx, cy, left, top, width, height = [int(v) for v in line_match.groups()]
            if wants_top_right and not (
                cx >= int(sw * 0.72) and cy <= int(sh * 0.20)
                and left + width >= int(sw * 0.78)
            ):
                continue
            return True
        return False

    def _verify_gui_search_done(self, screen: str) -> bool:
        if not _is_gui_search_task(self.task):
            return True
        payloads = _gui_search_payloads(self.task)
        missing = [
            payload for payload in payloads
            if payload.casefold() not in screen.casefold()
        ]
        if not missing:
            return True
        reason = (
            "GUI search/navigation task lacks final on-screen evidence. "
            "The target search text/page is not visible in the current app window: "
            + ", ".join(missing[:3])
            + ". Local files, installed-game manifests, or shell output cannot replace "
            "opening the requested app/client and searching inside its GUI."
        )
        return self._reject_done(
            reason,
            f"[done check failed] {reason} Open/focus the requested app, use its "
            "visible search field or page navigation, then observe/read_text until "
            "the target text is visible before done.",
        )

    def _verify_communication_done(self, screen: str) -> bool:
        if not _is_communication_task(self.task):
            return True
        payloads = _quoted_payloads(self.task)
        if payloads and not any(payload in screen for payload in payloads):
            reason = (
                "通信任务缺少最终可见证据：目标窗口当前画面没有出现要发送的消息文本，"
                "不能把 click/type_text/hotkey 的 ok 当成发送成功"
            )
            logger.info("done 被快速核验拒绝(通信任务-有payload): %s", reason)
            return self._reject_done(
                reason,
                f"[完成核验未通过] {reason}。请先打开目标会话/输入区，"
                "发送后重新 observe 或 read_text，确认消息出现在目标会话中再 done。",
            )
        if payloads:
            return True

        has_chat_evidence = any(
            name == "read_text" and len(out.strip()) > 50
            and not _looks_like_contact_list(out)
            for name, out in self._recent_results
        )
        if has_chat_evidence:
            return True
        reason = (
            "通信任务未见聊天窗口内容证据：尚未调用 read_text 确认聊天窗口打开且有内容。"
            "请打开目标联系人的聊天窗口，read_text 确认后再 done"
        )
        logger.info("done 被快速核验拒绝(通信任务-无payload): %s", reason)
        return self._reject_done(reason, f"[完成核验未通过] {reason}。")

    def _call_done_judge(self, client, screen: str, recent: str, evidence: str) -> bool:
        cfg = _config()
        api = cfg.get("api", default={})
        judge_model = (
            cfg.get("agent", "escalation_model", default="")
            or api.get("done_judge_model")
            or api.get("model", "deepseek-v4-flash")
        )
        sys_p = (
            "你是严格、怀疑的任务完成核验员。判断用户任务是否【真正达成】，不要轻信 agent 的自述。"
            "重要：文件/文件夹/进程/命令类任务，以【确定性证据】(run_shell 输出、Test-Path/Exists 结果、"
            "verified 字段)为准——这类结果在桌面/后台,GUI 界面上【本来就看不到】,"
            "**绝不能因为‘最终界面没显示’就判未完成**。只有可视交互(网页填表/界面内容)才看界面。"
            "同理，窗口清点/状态检查类任务应优先相信 list_windows、scan_windows、read_text 的真实工具输出；"
            "scan_windows 可能会恢复原前台窗口，所以不能仅因当前前台不是目标窗口就判失败。"
            "对发消息/发送聊天/邮件/评论等对外通信任务，click/type_text/hotkey 的 ok 或 verified "
            "只证明动作执行，不证明消息已送达；必须有目标会话/收件上下文中的最终可见证据"
            "（例如聊天记录出现要发送的文本、输入框清空并记录出现、或应用明确发送成功状态）才可判 done。"
            '只输出 JSON: {"done": true/false, "reason": "简短中文理由"}。'
        )
        user_p = (
            f"用户任务：{self.task}\n\n"
            f"agent 声称完成：{self.result_text[:200]}\n\n"
            f"已执行动作(最近)：{recent}\n\n"
            f"确定性证据(工具真实输出/验证结果)：\n{evidence}\n\n"
            f"当前前台界面元素(仅供参考,文件/进程类勿据此判负)：\n{screen[:1000]}"
        )
        self._trace_debug("done_check_request", {
            "run_id": self.run_id,
            "model": judge_model,
            "messages": [
                {"role": "system", "content": sys_p},
                {"role": "user", "content": user_p},
            ],
        })
        try:
            resp = client.chat.completions.create(
                model=judge_model,
                messages=[
                    {"role": "system", "content": sys_p},
                    {"role": "user", "content": user_p},
                ],
                temperature=0.0,
                max_tokens=_JUDGE_MAX_TOKENS,
            )
            self._trace_debug("done_check_response", {
                "run_id": self.run_id,
                "model": judge_model,
                "response": resp,
            })
            judge_msg = resp.choices[0].message
            raw = judge_msg.content or ""
            if not re.search(r"\{.*\}", raw, re.DOTALL):
                reasoning_text = getattr(judge_msg, "reasoning_content", "") or ""
                if reasoning_text:
                    raw = raw + "\n" + reasoning_text
            matches = list(re.finditer(r"\{[^{}]*\"done\"[^{}]*\}", raw, re.DOTALL))
            m = matches[-1] if matches else re.search(r"\{.*\}", raw, re.DOTALL)
            if not m:
                is_done = False
                reason = "完成核验未返回可解析 JSON，不能接受为完成"
            else:
                try:
                    verdict = json.loads(m.group(0))
                    is_done = bool(verdict.get("done", False))
                    reason = str(verdict.get("reason", ""))[:200]
                except json.JSONDecodeError:
                    is_done = False
                    reason = "完成核验返回的 JSON 损坏，不能接受为完成"
        except Exception as exc:  # fail-open - never deadlock on judge errors
            logger.warning("done 核验调用失败，放行: %s", exc)
            self._trace_debug("done_check_error", {
                "run_id": self.run_id,
                "model": judge_model,
                "error": str(exc),
            })
            return True

        BUS.publish("done_check", run_id=self.run_id, passed=is_done, reason=reason)
        if is_done:
            self._done_verified = True
            return True
        self._last_done_reject_reason = reason
        self._done_rejects += 1
        self.messages.append({
            "role": "user",
            "content": f"[完成核验未通过] {reason}。任务尚未真正达成，请不要直接调 done——"
                       "先把缺的部分补完（必要时重新 observe / 用确定性 expect 验证），完成后再调 done。",
        })
        logger.info("done 被核验拦截(第%d次): %s", self._done_rejects, reason)
        return False

    def _verify_done(self, client, steps_left: int = 99) -> bool:
        """Independently verify a ``done`` claim before accepting it."""
        cfg = _config()
        if not cfg.get("agent", "verify_done", default=True):
            return True
        max_rejects = int(cfg.get("agent", "done_max_rejects", default=2))
        if self._done_rejects >= max_rejects:
            reason = (
                "Done force-accepted after done_max_rejects; result is marked "
                f"unverified (done_rejects={self._done_rejects})."
            )
            BUS.publish("done_check", run_id=self.run_id, passed=True, reason=reason, unverified=True)
            self._done_unverified = True
            return True
        force_unverified = steps_left <= 2

        recent, evidence = self._done_evidence()
        if _is_drawing_task(self.task):
            if not self._verify_drawing_done(evidence):
                return False
            required_colors = _required_drawing_colors(self.task)
            if required_colors and not _required_image_paths(self.task):
                reason = (
                    "Drawing task accepted from controller visual color evidence: "
                    + ", ".join(required_colors[:4])
                    + "."
                )
                BUS.publish("done_check", run_id=self.run_id, passed=True, reason=reason)
                self._done_verified = True
                return True
            if self._drawing_has_deterministic_done_evidence(evidence):
                reason = (
                    "Drawing task accepted from deterministic evidence: recorded "
                    "strokes and visual_change_detected=true."
                )
                BUS.publish("done_check", run_id=self.run_id, passed=True, reason=reason)
                self._done_verified = True
                return True

        screen = self._fresh_done_screen()
        if not self._verify_visible_payloads(screen, evidence):
            return False
        if not self._verify_required_literal_tokens(screen, evidence):
            return False
        if self._visible_state_has_deterministic_done_evidence(screen, evidence):
            return True
        if self._desktop_drag_has_deterministic_done_evidence(screen, evidence):
            reason = (
                "Desktop icon drag accepted from deterministic evidence: target "
                "listitem at requested area plus drag visual_change_detected=true."
            )
            BUS.publish("done_check", run_id=self.run_id, passed=True, reason=reason)
            self._done_verified = True
            return True
        if not self._verify_gui_search_done(screen):
            return False
        if not self._verify_communication_done(screen):
            return False
        if force_unverified:
            reason = (
                "Done accepted without LLM judge after deterministic guards passed "
                f"(steps_left={steps_left}, done_rejects={self._done_rejects})."
            )
            BUS.publish("done_check", run_id=self.run_id, passed=True, reason=reason, unverified=True)
            self._done_unverified = True
            return True
        return self._call_done_judge(client, screen, recent, evidence)
