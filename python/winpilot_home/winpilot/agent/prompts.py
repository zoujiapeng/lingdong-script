"""System prompt for the WinPilot agent."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..utils.screenshot import virtual_screen_region


_COMPACT_SYSTEM_TEMPLATE = """You are WinPilot, an agent controlling a real Windows desktop.
{vision_line}
Virtual screen: ({screen_left},{screen_top}) {screen_w}x{screen_h}. Desktop={desktop}
Current local date/time: {current_datetime} ({timezone_name}).
{runtime_section}
{execution_contract}
{vlm_section}{planning_section}{playbook_section}{experience_section}"""

_NATIVE_EXECUTION_CONTRACT = """
# Native visual contract
- The latest injected frame is authoritative.
- Decide from the user's request and the frame whether GUI interaction is needed.
  When it is, choose one exposed action, then inspect the fresh post-action frame.
  When the requested result is an answer or explanation, respond from the frame
  without inventing a GUI mutation.
- Native visual tools use one coordinate representation only: image_* pixel
  values in the exact authoritative full image attached to the latest frame.
- Structured signals are optional hints only when fresh, unique, same-frame,
  and demonstrably relevant. They are not a visual fallback or semantic authority.
- Tool execution is not semantic success. Judge fresh pixels against the user's criteria.
- A changed or unexpected frame may invalidate the prior interpretation;
  re-understand the latest frame and freely change strategy instead of forcing the old route.
- Call done only when fresh evidence and the causal action record prove every
  clause of the user's request.
"""

_TEXT_EXECUTION_CONTRACT = """
# Execution contract
Use targeted structured observations and deterministic checks. Do not use a
verify-only execute_subgoal instead of open_app/launch/focus_window. After each
mutation, verify its effect. Call done only when evidence proves every clause;
otherwise change strategy or call blocked.
"""

_VLM_SECTION = """
# Visual model
When vlm_describe is available, use it for any pixel-rendered state that
OCR/UIA/CV cannot describe. Use vlm_verify for semantic visible success claims.
"""

_PLANNING_SECTION = """
# Todo planning
Use plan tools only when they are exposed. For genuinely dependent work,
revise_plan may replace/clear the whole ordered list or apply
add/update/remove/move/reopen operations at any time. Change, reorder, or reopen
it whenever user direction, visible state, or new evidence changes the best
route; do not keep a stale plan. Avoid a
no-op rewrite that only paraphrases the same list. Mark grounded completed items
with complete_subgoal(index=...). Plan state is guidance, never completion evidence.
"""

_EXPERIENCE_HEADER = """
# Past successful experience
These hints are references only. The current screen may differ — verify before reusing.
{recipes}
"""

_VISION_NATIVE = (
    "You have native vision. A current GUI frame is injected initially and after "
    "each visual transaction or observe."
)
_VISION_TEXT_ONLY = (
    "You do not have native vision. Rely on structured observations: "
    "OCR text, UIA elements, CV icon candidates, and deterministic checks."
)

def system_prompt(
    experience_hints: str = "",
    vlm_enabled: bool = False,
    planning_enabled: bool = False,
    playbook_text: str = "",
    style: str = "compact",
    main_model_has_vision: bool = False,
    runtime_section: str = "",
) -> str:
    screen = virtual_screen_region()
    home = Path.home()
    now = datetime.now().astimezone()
    # Native visual execution must reason from the current frame.  Historical
    # recipes and window-specific playbooks are deliberately kept out of that
    # context; they can turn an unrelated visual state into a scripted tactic.
    experience_section = (
        _EXPERIENCE_HEADER.format(recipes=experience_hints)
        if (experience_hints and not main_model_has_vision)
        else ""
    )
    vision_line = _VISION_NATIVE if main_model_has_vision else _VISION_TEXT_ONLY
    # Domain playbooks and task-test recipes previously made the "full" prompt
    # both slower and less general. Keep the option as a compatibility alias,
    # but always send the compact closed-loop contract.
    del style
    template = _COMPACT_SYSTEM_TEMPLATE
    return template.format(
        screen_left=screen.left,
        screen_top=screen.top,
        screen_w=screen.width,
        screen_h=screen.height,
        current_datetime=now.isoformat(timespec="seconds"),
        timezone_name=str(now.tzname() or now.utcoffset() or "local"),
        vision_line=vision_line,
        execution_contract=(
            _NATIVE_EXECUTION_CONTRACT
            if main_model_has_vision
            else _TEXT_EXECUTION_CONTRACT
        ),
        runtime_section=("\n" + runtime_section.strip()) if runtime_section else "",
        # A second visual model would re-encode the same frame, add latency,
        # and can contradict the native model. Keep VLM tools/instructions as
        # a fallback only for text-only main models.
        vlm_section=_VLM_SECTION if (vlm_enabled and not main_model_has_vision) else "",
        # Native visual decisions do not need a planning tutorial; any exposed
        # progress tools already declare their own action semantics.
        planning_section=(
            _PLANNING_SECTION
            if (planning_enabled and not main_model_has_vision)
            else ""
        ),
        playbook_section=playbook_text if not main_model_has_vision else "",
        desktop=str(home / "Desktop"),
        documents=str(home / "Documents"),
        downloads=str(home / "Downloads"),
        experience_section=experience_section,
    )
