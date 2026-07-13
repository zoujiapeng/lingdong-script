"""Done-gate verification helpers for AgentRunner.

The runner loop calls ``_verify_done`` whenever the model claims completion.
Keeping this policy outside ``loop.py`` makes the orchestration path easier to
read while preserving the same guard behavior.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from ..config import CONFIG
from ..model_adapter import model_profile_from_config, model_supports_vision
from ..perception import fusion, intent, ocr, vision_cv, vlm as vlm_mod
from ..utils.image_capture import capture_window_jpeg_b64
from ..utils.log import BUS, logger
from .task_rules import (
    _communication_mode,
    _desktop_icon_target_names,
    _gui_search_payloads,
    _is_desktop_icon_drag_task,
    _is_communication_task,
    _is_drawing_task,
    _is_arithmetic_task,
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
from ..utils.screenshot import capture, virtual_screen_region, window_region


_JUDGE_MAX_TOKENS = 2500
_SAVED_FILE_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])([A-Za-z0-9][A-Za-z0-9_. -]{0,120}\."
    r"(?:txt|md|csv|docx?|xlsx?|pptx?|pdf|png|jpe?g|bmp))\b",
    re.IGNORECASE,
)
_COLORFUL_DRAWING_RE = re.compile(
    r"\b(?:colorful|colourful|multicolou?red|multi[- ]?color|rainbow)\b|"
    r"\u4e94\u989c\u516d\u8272|\u4e94\u5f69|\u591a\u5f69|\u5f69\u8272",
    re.IGNORECASE,
)
_DETAILED_DRAWING_RE = re.compile(
    r"\b(?:detailed|intricate|elaborate|high[- ]?detail|polished|beautiful|"
    r"good[- ]?looking|not\s+(?:simple|crude)|non[- ]?(?:simple|crude))\b|"
    r"\u7ec6\u8282|\u7cbe\u7ec6|\u7cbe\u81f4|\u8be6\u7ec6|\u597d\u770b|"
    r"\u6f02\u4eae|\u7f8e\u89c2|\u7cbe\u7f8e|\u4e0d\u8981\u7b80\u964b|"
    r"\u4e0d\u7b80\u964b|\u522b\u7b80\u964b|\u4e0d\u8981\u7b80\u5355|\u4e0d\u7b80\u5355",
    re.IGNORECASE,
)
_VISUAL_COLOR_NAMES = {
    "red", "orange", "yellow", "lime", "green", "blue", "purple", "pink",
    "black", "gray", "white", "brown", "cyan", "magenta",
}

_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

_ARITHMETIC_EXPRESSION_RE = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?(?:\s*(?:[+*/xX\u00d7\u00f7-])\s*"
    r"\d+(?:\.\d+)?){1,10})(?![\d.])"
)


def _safe_arithmetic_value(expression: str) -> int | float:
    normalized = (
        str(expression or "")
        .replace("\u00d7", "*")
        .replace("\u00f7", "/")
        .replace("X", "*")
        .replace("x", "*")
        .replace(",", "")
    )
    tree = ast.parse(normalized, mode="eval")

    def visit(node: ast.AST) -> int | float:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                if right == 0:
                    raise ValueError("division by zero")
                return left / right
        raise ValueError("unsupported arithmetic expression")

    value = visit(tree)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _task_arithmetic_expression(task: str, now: datetime | None = None) -> str:
    if not _is_arithmetic_task(task):
        return ""
    candidates = _ARITHMETIC_EXPRESSION_RE.findall(task or "")
    preferred = next(
        (item for item in candidates if re.search(r"[*/xX\u00d7\u00f7+]", item)),
        candidates[0] if candidates else "",
    )
    if preferred:
        return preferred
    text = (task or "").casefold()
    if (
        any(marker in text for marker in ("today", "date", "\u4eca\u5929", "\u65e5\u671f"))
        and any(marker in text for marker in (
            "multiply", "multiplied", "product", "\u76f8\u4e58", "\u4e58\u79ef",
        ))
    ):
        current = now or datetime.now().astimezone()
        return f"{current.year}*{current.month}*{current.day}"
    return ""


def _deterministic_arithmetic_verdict(
    task: str,
    screen: str,
    *,
    now: datetime | None = None,
) -> tuple[bool, str] | None:
    expression = _task_arithmetic_expression(task, now=now)
    if not expression:
        return None
    try:
        expected_value = _safe_arithmetic_value(expression)
    except (SyntaxError, ValueError, TypeError):
        return None
    expected = (
        str(expected_value)
        if isinstance(expected_value, int)
        else format(expected_value, ".12g")
    )
    expected_compact = expected.replace(",", "")
    screen_text = str(screen or "")
    visual_marker = "Fresh visual OCR:"
    candidate_text = (
        screen_text.rsplit(visual_marker, 1)[1]
        if visual_marker in screen_text
        else screen_text
    )
    for line in candidate_text.splitlines():
        compact = re.sub(r"[\s,]", "", line)
        if re.search(rf"(?<!\d){re.escape(expected_compact)}(?!\d)", compact):
            return True, (
                f"Arithmetic result accepted from fresh screen evidence: "
                f"{expression} = {expected}."
            )
    return False, (
        f"Fresh screen does not show the deterministic result {expected} "
        f"for {expression}."
    )


def _word_attr(name: str) -> str:
    return f"{{{_WORD_NS}}}{name}"


def _docx_document_evidence(path: Path) -> tuple[str, list[dict[str, object]], str]:
    """Read text and direct paragraph formatting from a DOCX artifact."""
    try:
        with ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
    except (OSError, KeyError, BadZipFile, ElementTree.ParseError) as exc:
        return "", [], f"invalid DOCX package: {exc}"

    ns = {"w": _WORD_NS}
    paragraphs: list[dict[str, object]] = []
    for paragraph in root.findall(".//w:body/w:p", ns):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", ns))
        if not text.strip():
            continue
        properties = paragraph.find("w:pPr", ns)
        alignment = ""
        if properties is not None:
            node = properties.find("w:jc", ns)
            if node is not None:
                alignment = str(node.get(_word_attr("val"), "") or "").casefold()
        run_bold: list[bool] = []
        for run in paragraph.findall("w:r", ns):
            run_text = "".join(node.text or "" for node in run.findall(".//w:t", ns))
            if not run_text:
                continue
            run_properties = run.find("w:rPr", ns)
            bold = False
            if run_properties is not None:
                bold_node = run_properties.find("w:b", ns)
                if bold_node is not None:
                    bold = str(bold_node.get(_word_attr("val"), "1")).casefold() not in {
                        "0", "false", "off", "none",
                    }
            run_bold.append(bold)
        paragraphs.append({
            "text": text,
            "alignment": alignment,
            "bold_all": bool(run_bold) and all(run_bold),
            "bold_any": any(run_bold),
        })
    return "\n".join(str(item["text"]) for item in paragraphs), paragraphs, ""


def _docx_task_format_errors(task: str, paragraphs: list[dict[str, object]]) -> list[str]:
    """Check only formatting constraints explicitly requested by the task."""
    if not paragraphs:
        return ["DOCX contains no non-empty paragraphs"]
    text = str(task or "").casefold()
    first_line_scope = bool(re.search(
        r"\bfirst\s+(?:line|paragraph)\b|\b(?:heading|title)\b|\u7b2c\u4e00\u884c|\u9996\u884c|\u6807\u9898",
        text,
    ))
    wants_bold = bool(re.search(r"\bbold\b|\u52a0\u7c97", text))
    wants_center = bool(re.search(r"\bcent(?:er|re)(?:ed)?\b|\u5c45\u4e2d", text))
    wants_body_left = bool(
        re.search(r"\b(?:remaining|rest|other|body)\b|\u5176\u4f59|\u6b63\u6587|\u5269\u4f59", text)
        and re.search(r"\bleft(?:[ -]align(?:ed|ment)?)?\b|\u5de6\u5bf9\u9f50", text)
    )

    errors: list[str] = []
    first = paragraphs[0]
    if first_line_scope and wants_bold and first.get("bold_all") is not True:
        errors.append("first paragraph is not fully bold")
    if first_line_scope and wants_center and first.get("alignment") not in {"center", "centre"}:
        errors.append(f"first paragraph alignment is {first.get('alignment') or 'default'}, not center")
    if wants_body_left:
        bad = [
            index
            for index, paragraph in enumerate(paragraphs[1:], start=2)
            if paragraph.get("alignment") not in {"", "left", "start"}
        ]
        if bad:
            errors.append("body paragraph(s) not left aligned: " + ", ".join(map(str, bad)))
    return errors


def _xlsx_column_number(label: str) -> int:
    value = 0
    for char in str(label or "").upper():
        if not "A" <= char <= "Z":
            return 0
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value


def _xlsx_column_label(number: int) -> str:
    value = int(number)
    chars: list[str] = []
    while value > 0:
        value, remainder = divmod(value - 1, 26)
        chars.append(chr(ord("A") + remainder))
    return "".join(reversed(chars))


def _xlsx_workbook_evidence(path: Path) -> tuple[str, dict[str, dict[str, object]], str]:
    """Read first-sheet values, formulas and effective bold state from XLSX."""
    ns = {"s": _SHEET_NS}
    try:
        with ZipFile(path) as archive:
            names = set(archive.namelist())
            sheet_names = sorted(
                name for name in names
                if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
            )
            if not sheet_names:
                return "", {}, "XLSX contains no worksheet XML"

            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in names:
                shared_root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                for item in shared_root.findall("s:si", ns):
                    shared_strings.append("".join(node.text or "" for node in item.findall(".//s:t", ns)))

            bold_styles: set[int] = set()
            if "xl/styles.xml" in names:
                styles_root = ElementTree.fromstring(archive.read("xl/styles.xml"))
                bold_fonts: set[int] = set()
                fonts = styles_root.find("s:fonts", ns)
                if fonts is not None:
                    for index, font in enumerate(fonts.findall("s:font", ns)):
                        bold_node = font.find("s:b", ns)
                        if bold_node is None:
                            continue
                        value = str(bold_node.get("val", "1") or "1").casefold()
                        if value not in {"0", "false", "off", "none"}:
                            bold_fonts.add(index)
                cell_xfs = styles_root.find("s:cellXfs", ns)
                if cell_xfs is not None:
                    for index, xf in enumerate(cell_xfs.findall("s:xf", ns)):
                        try:
                            font_id = int(xf.get("fontId", "0") or 0)
                        except ValueError:
                            font_id = 0
                        if font_id in bold_fonts:
                            bold_styles.add(index)

            sheet_root = ElementTree.fromstring(archive.read(sheet_names[0]))
    except (OSError, KeyError, BadZipFile, ElementTree.ParseError) as exc:
        return "", {}, f"invalid XLSX package: {exc}"

    cells: dict[str, dict[str, object]] = {}
    for row in sheet_root.findall(".//s:sheetData/s:row", ns):
        try:
            row_number = int(row.get("r", "0") or 0)
        except ValueError:
            row_number = 0
        try:
            row_style = int(row.get("s", "0") or 0) if row.get("s") is not None else None
        except ValueError:
            row_style = None
        for cell in row.findall("s:c", ns):
            ref = str(cell.get("r") or "").upper()
            if not ref:
                continue
            cell_type = str(cell.get("t") or "")
            value_node = cell.find("s:v", ns)
            raw_value = value_node.text if value_node is not None and value_node.text is not None else ""
            if cell_type == "s" and raw_value:
                try:
                    value = shared_strings[int(raw_value)]
                except (ValueError, IndexError):
                    value = raw_value
            elif cell_type == "inlineStr":
                value = "".join(node.text or "" for node in cell.findall(".//s:t", ns))
            elif cell_type == "b":
                value = "TRUE" if raw_value == "1" else "FALSE"
            else:
                value = raw_value
            formula_node = cell.find("s:f", ns)
            formula = "=" + (formula_node.text or "") if formula_node is not None else ""
            try:
                style = int(cell.get("s", "0") or 0) if cell.get("s") is not None else row_style
            except ValueError:
                style = row_style
            cells[ref] = {
                "value": value,
                "formula": formula,
                "bold": bool(style is not None and style in bold_styles),
                "style": style,
                "row": row_number,
            }
    lines: list[str] = []
    for ref, item in sorted(cells.items(), key=lambda pair: (int(re.search(r"\d+", pair[0]).group()), _xlsx_column_number(re.search(r"[A-Z]+", pair[0]).group()))):
        value = str(item.get("value") or "")
        formula = str(item.get("formula") or "")
        if value or formula:
            lines.append(f"{ref}={value}" + (f" formula:{formula}" if formula else ""))
    return "\n".join(lines), cells, ""


def _xlsx_task_errors(task: str, cells: dict[str, dict[str, object]]) -> list[str]:
    """Compare explicit cell-block, formula, value and row-style requirements."""
    text = str(task or "").replace("\r\n", "\n").replace("\r", "\n")
    errors: list[str] = []
    range_re = re.compile(
        r"(?<![A-Z0-9])([A-Z]{1,3})(\d+)\s*:\s*([A-Z]{1,3})(\d+)\s*[:：]\s*\n",
        re.IGNORECASE,
    )
    expected_rows: dict[int, list[str]] = {}
    for match in range_re.finditer(text):
        start_col, start_row_raw, end_col, end_row_raw = match.groups()
        start_row = int(start_row_raw)
        end_row = int(end_row_raw)
        start_col_number = _xlsx_column_number(start_col)
        end_col_number = _xlsx_column_number(end_col)
        row_count = end_row - start_row + 1
        column_count = end_col_number - start_col_number + 1
        raw_lines = text[match.end():].split("\n")
        if row_count < 1 or column_count < 1 or len(raw_lines) < row_count:
            continue
        for row_offset, raw_line in enumerate(raw_lines[:row_count]):
            values = raw_line.split("\t")
            if len(values) > column_count:
                break
            values.extend([""] * (column_count - len(values)))
            row_number = start_row + row_offset
            expected_rows[row_number] = values
            for col_offset, expected in enumerate(values):
                ref = f"{_xlsx_column_label(start_col_number + col_offset)}{row_number}"
                actual = cells.get(ref, {})
                expected_value = str(expected or "")
                if expected_value.startswith("="):
                    actual_formula = re.sub(r"\s+", "", str(actual.get("formula") or "")).upper()
                    if actual_formula != re.sub(r"\s+", "", expected_value).upper():
                        errors.append(f"{ref} formula is {actual.get('formula') or 'missing'}, expected {expected_value}")
                elif expected_value:
                    actual_value = str(actual.get("value") or "")
                    equal = actual_value == expected_value
                    if not equal:
                        try:
                            equal = abs(float(actual_value) - float(expected_value)) < 1e-9
                        except ValueError:
                            equal = False
                    if not equal:
                        errors.append(f"{ref} value is {actual_value or 'missing'}, expected {expected_value}")
        break

    for match in re.finditer(
        r"([A-Z]{1,3}\d+)\s+(?:evaluates?|calculates?)\s+to\s+(-?\d+(?:\.\d+)?)|"
        r"([A-Z]{1,3}\d+)\s*(?:计算结果|结果|值)\s*(?:为|是)\s*(-?\d+(?:\.\d+)?)",
        text,
        re.IGNORECASE,
    ):
        ref = str(match.group(1) or match.group(3) or "").upper()
        expected = str(match.group(2) or match.group(4) or "")
        actual = str(cells.get(ref, {}).get("value") or "")
        try:
            equal = abs(float(actual) - float(expected)) < 1e-9
        except ValueError:
            equal = actual == expected
        if not equal:
            errors.append(f"{ref} cached result is {actual or 'missing'}, expected {expected}")

    bold_rows: list[int] = []
    for pattern in (
        r"\brow\s*(\d+)\b.{0,30}\bbold\b",
        r"\bbold\b.{0,30}\brow\s*(\d+)\b",
        r"第?\s*(\d+)\s*行.{0,30}加粗",
        r"加粗.{0,30}第?\s*(\d+)\s*行",
    ):
        for match in re.finditer(pattern, text, re.IGNORECASE):
            row_number = int(match.group(1))
            if row_number not in bold_rows:
                bold_rows.append(row_number)
    for row_number in bold_rows:
        row_cells = [
            (ref, item) for ref, item in cells.items()
            if int(item.get("row") or 0) == row_number
            and (str(item.get("value") or "") or str(item.get("formula") or ""))
        ]
        if not row_cells:
            errors.append(f"bold row {row_number} contains no saved cells")
            continue
        not_bold = [ref for ref, item in row_cells if item.get("bold") is not True]
        if not_bold:
            errors.append(f"row {row_number} is not fully bold at: {', '.join(not_bold[:8])}")
    return errors


def _loop_attr(name: str, default):
    """Preserve historical monkeypatch paths on winpilot.agent.loop."""
    loop_mod = sys.modules.get("winpilot.agent.loop")
    return getattr(loop_mod, name, default) if loop_mod is not None else default


def _config():
    return _loop_attr("CONFIG", CONFIG)


def _configured_vision_judge_model(cfg) -> str:
    """Return the configured image-capable fallback judge, if any.

    Capture eligibility and judge routing must use the same contract. A
    text-only main model must not skip the fresh target-window capture merely
    because the configured VLM will consume it later.
    """
    settings = dict(cfg.get("vlm", default={}) or {})
    model = str(settings.get("model") or "").strip()
    if not (
        settings.get("enabled")
        and settings.get("base_url")
        and model
        and model_supports_vision(model)
    ):
        return ""
    return model


def _explicit_saved_paths(task: str) -> list[Path]:
    text = str(task or "")
    low = text.casefold()
    if not any(marker in low for marker in (
        "save", "save as", "export", "write to",
        "\u4fdd\u5b58", "\u53e6\u5b58\u4e3a", "\u5bfc\u51fa", "\u5199\u5165",
    )):
        return []
    absolute = re.findall(
        r"[A-Za-z]:\\[^\r\n\"'<>|]+?\."
        r"(?:txt|md|csv|docx?|xlsx?|pptx?|pdf|png|jpe?g|bmp)",
        text,
        flags=re.IGNORECASE,
    )
    paths = [Path(value.rstrip(".,;:，。；")) for value in absolute]
    # An explicit absolute path is authoritative. Do not also reinterpret a
    # later prose fragment ending in an extension as another Desktop basename.
    if paths:
        unique: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            key = str(path).casefold()
            if key not in seen:
                seen.add(key)
                unique.append(path)
        return unique
    basenames = [match.group(1).strip() for match in _SAVED_FILE_RE.finditer(text)]
    if any(marker in low for marker in ("desktop", "\u684c\u9762")):
        base = Path.home() / "Desktop"
    elif any(marker in low for marker in ("downloads", "download folder", "\u4e0b\u8f7d")):
        base = Path.home() / "Downloads"
    elif any(marker in low for marker in ("documents", "document folder", "\u6587\u6863")):
        base = Path.home() / "Documents"
    else:
        base = None
    if base is not None:
        paths.extend(base / name for name in basenames)
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


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

    def _recent_all_matched_visual_colors(self) -> set[str]:
        """Collect controller-confirmed ROI colors without trusting palette OCR."""
        texts = [str(text or "") for _tool, text in getattr(self, "_recent_results", [])]
        texts.extend(
            str(text or "")
            for _tool, _args, text in getattr(self, "_tool_history", [])
        )
        matched: set[str] = set()

        def visit(value) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == "matched_visual_colors" and isinstance(child, list):
                        matched.update(
                            str(item).casefold()
                            for item in child
                            if str(item).casefold() in _VISUAL_COLOR_NAMES
                        )
                    else:
                        visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        for text in texts:
            try:
                visit(json.loads(text))
                continue
            except (json.JSONDecodeError, TypeError):
                pass
            normalized = text.casefold().replace("\\\"", '"')
            for block in re.findall(
                r'"matched_visual_colors"\s*:\s*\[([^\]]*)\]',
                normalized,
            ):
                matched.update(
                    color for color in _VISUAL_COLOR_NAMES
                    if re.search(rf'"{re.escape(color)}"', block)
                )
        return matched

    @staticmethod
    def _semantic_visual_records(value) -> list[dict]:
        """Return complete semantic-verification records without mixing siblings."""
        records: list[dict] = []

        def visit(item) -> None:
            if isinstance(item, dict):
                method = str(item.get("verify_method") or "").casefold()
                if method in {"vlm", "semantic-vlm", "object-detector"}:
                    records.append(item)
                for child in item.values():
                    visit(child)
            elif isinstance(item, list):
                for child in item:
                    visit(child)

        visit(value)
        return records

    @staticmethod
    def _drawing_mutation_in_history_entry(name: str, args: dict) -> bool:
        if name in {"draw_strokes", "drag"}:
            return True
        if name == "execute_subgoal" and bool(args.get("draws")):
            return True
        if name == "run_action_batch":
            return any(
                isinstance(action, dict)
                and action.get("action") in {"draw_strokes", "drag"}
                for action in (args.get("actions") or [])
            )
        return False

    @staticmethod
    def _semantic_visual_record_verdict(record: dict) -> tuple[bool, str]:
        method = str(record.get("verify_method") or "semantic visual verifier")
        verdict = record.get("verdict") if isinstance(record.get("verdict"), dict) else {}
        verified = record.get("verified") is True
        semantic_verified = record.get("semantic_visual_verified") is True
        verdict_passed = verdict.get("passed") is True
        passed = verified and (
            semantic_verified
            or verdict_passed
            or method.casefold() == "object-detector"
        )
        if passed:
            return True, f"Latest {method} verification passed."

        details: list[str] = []
        detail_level = str(verdict.get("detail_level") or "").strip()
        if detail_level:
            details.append(f"detail_level={detail_level}")
        issues = verdict.get("issues")
        if isinstance(issues, list):
            details.extend(str(issue).strip() for issue in issues if str(issue).strip())
        reason = str(verdict.get("reason") or record.get("reason") or "").strip()
        if reason:
            details.append(reason)
        suffix = ": " + "; ".join(details[:6]) if details else ""
        return False, f"Latest {method} verification failed{suffix}."

    def _latest_semantic_visual_drawing_verdict(
        self,
        evidence: str = "",
    ) -> tuple[bool, str] | None:
        """Use one fresh visual verdict; never combine flags from other tools."""
        history = list(getattr(self, "_tool_history", []) or [])
        latest: tuple[int, dict] | None = None
        for index, (_name, _args, output) in enumerate(history):
            try:
                parsed = json.loads(str(output or ""))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            records = self._semantic_visual_records(parsed)
            if records:
                latest = (index, records[-1])

        if latest is None and evidence:
            try:
                parsed_evidence = json.loads(evidence)
            except (json.JSONDecodeError, TypeError, ValueError):
                parsed_evidence = None
            records = self._semantic_visual_records(parsed_evidence)
            if records:
                return self._semantic_visual_record_verdict(records[-1])
            return None
        if latest is None:
            return None

        index, record = latest
        for name, args, _output in history[index + 1:]:
            if self._drawing_mutation_in_history_entry(name, args or {}):
                return False, (
                    "Semantic visual verification is stale because the canvas was changed "
                    "after the latest visual check."
                )
        return self._semantic_visual_record_verdict(record)

    def _has_semantic_visual_drawing_evidence(self, evidence: str) -> bool:
        verdict = self._latest_semantic_visual_drawing_verdict(evidence)
        return bool(verdict and verdict[0])

    def _reject_done(self, reason: str, content: str) -> bool:
        """Record a failed done-gate check and nudge the model to continue."""
        self._last_done_reject_reason = reason
        self._last_done_reject_generation = self._done_evidence_generation()
        BUS.publish("done_check", run_id=self.run_id, passed=False, reason=reason)
        self._done_rejects += 1
        self.messages.append({"role": "user", "content": content})
        return False

    def _done_evidence_generation(self) -> tuple[int, int, int]:
        """Identify whether repair work or a new grounded frame happened.

        Repeated identical completion claims are capped, but the cap must not
        become a permanent latch after the agent repairs the UI. Terminal
        self-claims are deliberately excluded: another identical ``done`` is
        not new evidence. Successful tool evidence and authoritative native
        frames are monotonic causal signals that earn a fresh attempt.
        """
        causal_records: set[str] = set()
        deterministic_text_tools = {
            "file_exists", "read_file", "run_shell", "process_exists",
            "list_windows", "scan_windows", "vlm_verify",
        }
        for name, _args, output in getattr(self, "_tool_history", []) or []:
            tool_name = str(name or "")
            if tool_name in {"done", "fail", "reply"}:
                continue
            raw = str(output or "").strip()
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, TypeError, ValueError):
                payload = None
            if isinstance(payload, dict):
                if payload.get("error") or payload.get("blocked") is True:
                    continue
                if any(payload.get(key) is True for key in (
                    "ok", "success", "verified", "matches", "found", "exists",
                )):
                    causal_records.add(tool_name + "\0" + raw)
                continue
            if tool_name == "observe":
                # Native observation freshness is represented by the actual
                # frame digest below. Structured observe text carries volatile
                # trace ids/request counters, so hashing it would let an
                # identical re-observe masquerade as repaired evidence.
                continue
            if tool_name == "read_text":
                lowered = raw.casefold()
                if (
                    not raw
                    or "no elements matched" in lowered
                    or "not found" in lowered
                    or "error" in lowered
                ):
                    continue
                causal_records.add(tool_name + "\0" + raw)
                continue
            if tool_name in deterministic_text_tools and raw:
                lowered = raw.casefold()
                if not any(marker in lowered for marker in (
                    "error", "failed", "failure", "not found", "blocked",
                )):
                    causal_records.add(tool_name + "\0" + raw)

        def digest(parts: set[str] | list[str]) -> int:
            if not parts:
                return 0
            payload = "\n".join(sorted(set(parts))).encode("utf-8", errors="replace")
            return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")

        visual_b64 = str(getattr(self, "_last_visual_feedback_b64", "") or "")
        visual_digest = (
            int.from_bytes(hashlib.sha256(visual_b64.encode("ascii", errors="ignore")).digest()[:8], "big")
            if visual_b64 else 0
        )
        ledger_records = [
            json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            for item in (getattr(self, "_visible_evidence_ledger", []) or [])
            if isinstance(item, dict)
        ]
        return (
            digest(causal_records),
            visual_digest,
            digest(ledger_records),
        )

    def _fresh_done_screen(self) -> str:
        """Capture fresh perception for the skeptical final-state check."""
        # Never let a frame from an earlier done attempt survive a failed
        # capture. Literal checks may defer to the image judge only when this
        # invocation produced a real, fresh target-window frame.
        self._fresh_done_image_b64 = ""
        try:
            fusion_mod = _loop_attr("fusion", fusion)
            intent_mod = _loop_attr("intent", intent)
            hwnd = self.session.hwnd
            title = self.session.snapshot.title if self.session.snapshot else ""
            pmode = intent_mod.perception_mode(self.task, title)
            cfg = _config()
            api = cfg.get("api", default={})
            fallback_visual_judge = _configured_vision_judge_model(cfg)
            native_visual = bool(
                hwnd
                and (
                    model_profile_from_config(api).supports_vision
                    or (_is_drawing_task(self.task) and fallback_visual_judge)
                )
            )
            if native_visual:
                try:
                    self._fresh_done_image_b64 = capture_window_jpeg_b64(hwnd)
                except Exception as exc:
                    logger.debug("fresh done visual capture failed: %s", exc)
                # Do not decompose a successfully captured real frame through a
                # slow and lossy UIA/OCR/VLM pipeline.  Keep the latest
                # structured state only as optional context for deterministic
                # checks; the independent judge receives the fresh image below.
                # Never label the pre-action/session snapshot as fresh when a
                # new image is authoritative. Native visible predicates must
                # be checked on that image by the independent judge.
                snap = None if self._fresh_done_image_b64 else fusion_mod.observe(
                    hwnd, include_icons=False, perception_mode=pmode,
                )
            else:
                snap = (
                    fusion_mod.observe(hwnd, include_icons=False, perception_mode=pmode)
                    if hwnd else self.session.snapshot
                )
            limit = 220 if _is_desktop_icon_drag_task(self.task) else 50
            text = snap.to_text(limit) if snap else ""
            if self._fresh_done_image_b64:
                text += (
                    "\nFresh native-vision frame attached to the independent done judge; "
                    "it is authoritative over stale structured elements."
                )
            if hwnd and _is_arithmetic_task(self.task):
                try:
                    region = window_region(hwnd).clamp_to_screen()
                    image = capture(region)
                    visual = ocr.recognize(
                        image,
                        origin=(region.left, region.top),
                    )
                    visual_lines = list(dict.fromkeys(
                        str(element.text or "").strip()
                        for element in visual
                        if str(element.text or "").strip()
                    ))
                    if visual_lines:
                        text += "\nFresh visual OCR:\n" + "\n".join(visual_lines)
                    try:
                        from ..recorder.trace import RECORDER

                        RECORDER.save_evidence_image(image, "fresh_done_screen")
                    except Exception:
                        pass
                except Exception as exc:
                    logger.debug("fresh arithmetic visual OCR failed: %s", exc)
            return text
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

    def _deterministic_saved_file_verdict(
        self,
        screen: str,
        evidence: str,
    ) -> tuple[bool, str] | None:
        """Verify an explicitly named saved artifact without another LLM turn."""
        paths = _explicit_saved_paths(self.task)
        if not paths:
            return None
        try:
            run_started = time.mktime(time.strptime(self.run_id[:15], "%Y%m%d-%H%M%S"))
        except (ValueError, OverflowError):
            run_started = 0.0
        missing: list[str] = []
        stale: list[str] = []
        empty: list[str] = []
        file_texts: list[str] = []
        artifact_errors: list[str] = []
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                missing.append(str(path))
                continue
            if run_started and stat.st_mtime + 2.0 < run_started:
                stale.append(str(path))
            if stat.st_size <= 0:
                empty.append(str(path))
            if path.suffix.casefold() in {".txt", ".md", ".csv"}:
                try:
                    file_texts.append(path.read_text(encoding="utf-8", errors="replace")[:20_000])
                except OSError:
                    pass
            elif path.suffix.casefold() == ".docx":
                docx_text, paragraphs, package_error = _docx_document_evidence(path)
                if docx_text:
                    file_texts.append(docx_text[:20_000])
                if package_error:
                    artifact_errors.append(f"{path.name}: {package_error}")
                else:
                    artifact_errors.extend(
                        f"{path.name}: {error}"
                        for error in _docx_task_format_errors(self.task, paragraphs)
                    )
            elif path.suffix.casefold() == ".xlsx":
                workbook_text, cells, package_error = _xlsx_workbook_evidence(path)
                if workbook_text:
                    file_texts.append(workbook_text[:20_000])
                if package_error:
                    artifact_errors.append(f"{path.name}: {package_error}")
                else:
                    artifact_errors.extend(
                        f"{path.name}: {error}"
                        for error in _xlsx_task_errors(self.task, cells)
                    )
        if missing or stale or empty:
            details = []
            if missing:
                details.append("missing=" + ", ".join(missing[:3]))
            if stale:
                details.append("not modified in this run=" + ", ".join(stale[:3]))
            if empty:
                details.append("empty=" + ", ".join(empty[:3]))
            return False, "Saved output verification failed: " + "; ".join(details)
        if artifact_errors:
            return False, "Saved Office artifact content/format verification failed: " + "; ".join(
                artifact_errors[:4]
            )

        parsed_content_suffixes = {".txt", ".md", ".csv", ".docx", ".xlsx"}
        content_is_directly_parsed = bool(paths) and all(
            path.suffix.casefold() in parsed_content_suffixes for path in paths
        )
        combined = "\n".join(
            file_texts if content_is_directly_parsed else [screen, evidence, *file_texts]
        )
        missing_payloads = [
            value
            for value in _required_visible_payloads(self.task)
            if not self._visible_snippet(combined, value)
        ]
        if missing_payloads:
            return False, (
                "Saved file exists, but requested content evidence is missing: "
                + ", ".join(missing_payloads[:3])
            )
        basenames = [path.name for path in paths]
        title_evidence = screen
        hwnd = int(getattr(getattr(self, "session", None), "hwnd", 0) or 0)
        if hwnd and not any(self._visible_snippet(title_evidence, name) for name in basenames):
            # Office-style apps can update the native window title a few
            # hundred milliseconds after the file has already been written.
            # Poll only the cheap Win32 title instead of paying for another
            # full OCR/VLM observation.
            try:
                import win32gui

                for attempt in range(4):
                    current_title = str(win32gui.GetWindowText(hwnd) or "").strip()
                    if current_title:
                        title_evidence += "\n" + current_title
                    if any(self._visible_snippet(title_evidence, name) for name in basenames):
                        break
                    if attempt < 3:
                        time.sleep(0.2)
            except Exception:
                pass
        if not any(self._visible_snippet(title_evidence, name) for name in basenames):
            return False, (
                "Saved file exists, but the fresh target window does not show its name: "
                + ", ".join(basenames[:3])
            )
        return True, (
            "Saved output accepted from fresh file metadata, requested content, and "
            "target-window filename evidence: " + ", ".join(str(path) for path in paths[:3])
        )

    def _verify_visible_payloads(self, screen: str, evidence: str) -> bool:
        self._pending_visual_payloads = []
        required_payloads = _required_visible_payloads(self.task)
        if not required_payloads:
            return True
        if (
            self._task_accepts_historical_visible_state(self.task)
            and self._visible_state_has_deterministic_done_evidence(screen, evidence)
        ):
            return True
        if bool(getattr(self, "_saved_artifact_verified", False)):
            # The artifact verifier parsed the newly written file and checked
            # requested payloads. File-internal content need not all remain
            # simultaneously visible in the editor after a successful save.
            return True
        if str(getattr(self, "_fresh_done_image_b64", "") or ""):
            # Once a fresh native frame exists, current-state claims must be
            # decided from that frame.  Tool arguments and older structured
            # observations can establish what was attempted, but cannot prove
            # that the requested payload is still visible now.
            self._pending_visual_payloads = list(required_payloads)
            return True
        missing = [
            payload for payload in required_payloads
            if not self._visible_snippet(screen, payload)
            and not self._visible_snippet(evidence, payload)
        ]
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
        self._pending_visual_literal_tokens = []
        tokens = _required_literal_tokens(self.task)
        if not tokens:
            return True
        if (
            self._task_accepts_historical_visible_state(self.task)
            and self._visible_state_has_deterministic_done_evidence(screen, evidence)
        ):
            return True
        if bool(getattr(self, "_saved_artifact_verified", False)):
            return True
        if str(getattr(self, "_fresh_done_image_b64", "") or ""):
            # A fresh frame outranks every pre-action/current-state string in
            # the ledger.  Always make the image judge confirm exact literals
            # so stale UIA/OCR or action payloads cannot produce early success.
            self._pending_visual_literal_tokens = list(tokens)
            return True
        missing = [
            token for token in tokens
            if not self._visible_snippet(screen, token)
            and not self._visible_snippet(evidence, token)
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
            missing = [
                target for target in targets
                if not self._visible_snippet(screen, target)
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
        colorful_requested = bool(_COLORFUL_DRAWING_RE.search(self.task or ""))
        detailed_requested = bool(_DETAILED_DRAWING_RE.search(self.task or ""))
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
        if colorful_requested:
            matched_colors = self._recent_all_matched_visual_colors()
            if len(matched_colors) < 3:
                reason = (
                    "Colorful drawing requested, but controller ROI evidence confirmed only "
                    f"{len(matched_colors)} distinct color(s): "
                    + (", ".join(sorted(matched_colors)) if matched_colors else "none")
                )
                return self._reject_done(
                    reason,
                    f"[done check failed] {reason}. Use visible color controls and "
                    "controller draw routines until at least three canvas colors are "
                    "reported in matched_visual_colors.",
                )
        semantic_verdict = (
            self._latest_semantic_visual_drawing_verdict(evidence)
            if detailed_requested else None
        )
        if (
            detailed_requested
            and not str(getattr(self, "_fresh_done_image_b64", "") or "")
            and not (semantic_verdict and semantic_verdict[0])
        ):
            reason = (
                semantic_verdict[1]
                if semantic_verdict is not None
                else (
                    "Detailed drawing cannot be verified from drag execution or pixel change "
                    "alone; fresh semantic visual evidence is missing."
                )
            )
            return self._reject_done(
                reason,
                f"[done check failed] {reason} Obtain a fresh full-frame visual verdict "
                "after the final canvas mutation. Only call done when it confirms the "
                "requested subject and detail level; otherwise repair or call fail.",
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

    def _verify_drawing_semantic_evidence(self, evidence: str) -> bool:
        """Require one goal-conditioned visual view of the final drawing.

        A drag and a pixel delta are necessary action evidence, but they cannot
        establish that the requested subject, shape, or level of detail exists.
        Native visual runs use the fresh final frame. Text-only runs must supply
        one current semantic visual verdict from an explicit verifier.
        """
        if not _is_drawing_task(self.task):
            return True
        if str(getattr(self, "_fresh_done_image_b64", "") or ""):
            return True
        semantic_verdict = self._latest_semantic_visual_drawing_verdict(evidence)
        if semantic_verdict and semantic_verdict[0]:
            return True
        reason = (
            semantic_verdict[1]
            if semantic_verdict is not None
            else (
                "Drawing completion cannot be inferred from drag execution or pixel change "
                "alone; no fresh goal-conditioned visual evidence is available."
            )
        )
        return self._reject_done(
            reason,
            f"[done check failed] {reason} Obtain a fresh full-frame visual verdict after "
            "the final canvas mutation. Only call done when it confirms the requested "
            "subject, shape, colors, and detail; otherwise repair the drawing or call fail.",
        )

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
        desktop = virtual_screen_region()
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
                cx >= int(desktop.left + desktop.width * 0.72)
                and cy <= int(desktop.top + desktop.height * 0.20)
                and left + width >= int(desktop.left + desktop.width * 0.78)
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
        if str(getattr(self, "_fresh_done_image_b64", "") or ""):
            pending = list(getattr(self, "_pending_visual_payloads", []) or [])
            self._pending_visual_payloads = list(dict.fromkeys([*pending, *missing]))
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
            if str(getattr(self, "_fresh_done_image_b64", "") or ""):
                pending = list(getattr(self, "_pending_visual_payloads", []) or [])
                self._pending_visual_payloads = list(dict.fromkeys([*pending, *payloads]))
                self._pending_visual_communication_check = _communication_mode(self.task)
                return True
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

        if str(getattr(self, "_fresh_done_image_b64", "") or ""):
            self._pending_visual_communication_check = _communication_mode(self.task)
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

    @staticmethod
    def _communication_text_visible(needle: str, text: str) -> bool:
        needle_norm = re.sub(r"\s+", "", str(needle or "").casefold())
        text_norm = re.sub(r"\s+", "", str(text or "").casefold())
        return bool(needle_norm and len(needle_norm) >= 2 and needle_norm in text_norm)

    @staticmethod
    def _communication_mutation_succeeded(output: str) -> bool:
        try:
            payload = json.loads(str(output or ""))
        except (json.JSONDecodeError, TypeError, ValueError):
            return False
        if not isinstance(payload, dict) or payload.get("error"):
            return False
        if "blocked" in payload and payload.get("blocked") is not False:
            return False
        execution_keys = [key for key in ("ok", "success") if key in payload]
        if execution_keys:
            return all(payload.get(key) is True for key in execution_keys)
        return payload.get("verified") is True

    @staticmethod
    def _communication_normalize_action(action: object) -> tuple[str, dict]:
        """Normalize a controller child action without inventing execution."""
        if not isinstance(action, dict):
            return "", {}
        name = str(action.get("action") or action.get("tool") or "")
        if name:
            child_args = {
                key: value
                for key, value in action.items()
                if key not in {"action", "tool", "params"}
            }
            params = action.get("params")
            if isinstance(params, dict):
                child_args = {**params, **child_args}
            return name, child_args
        if len(action) != 1:
            return "", {}
        name, raw_args = next(iter(action.items()))
        if isinstance(raw_args, dict):
            return str(name), dict(raw_args)
        if str(name) == "hotkey":
            keys = list(raw_args) if isinstance(raw_args, list) else [raw_args]
            return str(name), {"keys": [str(key) for key in keys]}
        if str(name) in {"type_text", "type_into_element"}:
            return str(name), {"text": str(raw_args)}
        return str(name), {"value": raw_args}

    @staticmethod
    def _communication_exact_enter(args: dict) -> bool:
        """Accept only a bare Enter/Return key, never a newline shortcut."""
        keys = args.get("keys") or []
        if isinstance(keys, str):
            keys = [keys]
        if not isinstance(keys, (list, tuple)):
            return False
        normalized = [str(key).strip().casefold() for key in keys]
        return len(normalized) == 1 and normalized[0] in {"enter", "return"}

    @staticmethod
    def _communication_send_click(args: dict) -> bool:
        label = str(
            args.get("label")
            or args.get("fallback_text")
            or args.get("text")
            or ""
        ).strip().casefold()
        return any(marker in label for marker in ("send", "发送", "發送"))

    @staticmethod
    def _communication_action_family(name: str) -> str:
        folded = str(name or "").casefold()
        if folded in {"type_text", "type_into_element"}:
            return "type"
        if folded in {"click", "click_element"}:
            return "click"
        return folded

    @classmethod
    def _communication_child_succeeded(cls, item: object) -> bool:
        """Require the concrete nested action result to report literal success."""
        if not isinstance(item, dict) or item.get("ok") is not True:
            return False
        if item.get("error") or item.get("optional_failed") is True:
            return False
        if str(item.get("terminal") or "").casefold() in {
            "fail", "error", "stopped", "reply",
        }:
            return False
        data = item.get("data")
        if isinstance(data, dict):
            if data.get("error"):
                return False
            if "blocked" in data and data.get("blocked") is not False:
                return False
            for key in ("ok", "success"):
                if key in data and data.get(key) is not True:
                    return False
        return True

    @classmethod
    def _communication_successful_nested_actions(
        cls,
        name: str,
        args: dict,
        output: str,
    ) -> list[tuple[str, dict]]:
        """Recover only controller children proven to have actually executed."""
        if name not in {"run_action_batch", "execute_subgoal"}:
            return []
        try:
            payload = json.loads(str(output or ""))
        except (json.JSONDecodeError, TypeError, ValueError):
            return []
        if not isinstance(payload, dict):
            return []
        actions = (
            payload.get("actions_executed")
            if name == "execute_subgoal"
            else args.get("actions")
        )
        results = payload.get("results")
        if not isinstance(actions, list) or not isinstance(results, list):
            return []

        by_index: dict[int, dict] = {}
        for fallback_index, item in enumerate(results, start=1):
            if not isinstance(item, dict):
                continue
            try:
                index = int(item.get("index") or fallback_index)
            except (TypeError, ValueError):
                continue
            by_index.setdefault(index, item)

        successful: list[tuple[str, dict]] = []
        for index, raw_action in enumerate(actions, start=1):
            action_name, action_args = cls._communication_normalize_action(raw_action)
            item = by_index.get(index)
            if not action_name or not cls._communication_child_succeeded(item):
                continue
            result_name = str(item.get("action") or "") if isinstance(item, dict) else ""
            if (
                result_name
                and cls._communication_action_family(result_name)
                != cls._communication_action_family(action_name)
            ):
                continue
            successful.append((action_name, action_args))
        return successful

    @classmethod
    def _communication_action_events(cls, name: str, args: dict) -> list[tuple[str, str]]:
        folded = str(name or "").casefold()
        if folded in {"type_text", "type_into_element"}:
            value = str(args.get("text") or args.get("value") or "").strip()
            return [("typed", value)] if value else []
        if folded == "hotkey" and cls._communication_exact_enter(args):
            return [("send", "")]
        if folded in {"click", "click_element"} and cls._communication_send_click(args):
            return [("send", "")]
        return []

    @classmethod
    def _communication_entry_events(
        cls,
        name: str,
        args: dict,
        output: str,
    ) -> list[tuple[str, str]]:
        """Return ordered, proven typed/send events for one history entry."""
        if name in {"run_action_batch", "execute_subgoal"}:
            events: list[tuple[str, str]] = []
            for action_name, action_args in cls._communication_successful_nested_actions(
                name, args, output,
            ):
                events.extend(cls._communication_action_events(action_name, action_args))
            return events
        if not cls._communication_mutation_succeeded(output):
            return []
        return cls._communication_action_events(name, args)

    @classmethod
    def _communication_entry_sends(cls, name: str, args: dict, output: str) -> bool:
        return any(
            kind == "send"
            for kind, _value in cls._communication_entry_events(name, args, output)
        )

    @classmethod
    def _communication_sent_turns(
        cls,
        history: list[tuple[str, dict, str]],
    ) -> list[tuple[int, str]]:
        """Pair each real send with at most one latest unsent typed turn."""
        pending: list[str] = []
        sent: list[tuple[int, str]] = []
        for index, (name, args, output) in enumerate(history):
            for kind, value in cls._communication_entry_events(
                str(name or ""), args or {}, str(output or ""),
            ):
                if kind == "typed":
                    pending.append(value)
                elif kind == "send":
                    if pending:
                        sent.append((index, pending[-1]))
                    # Older fragments belonged to the same composer turn and
                    # must never be recycled as another outbound message.
                    pending.clear()
        return sent

    def _communication_outbound_payloads(self) -> list[str]:
        """Recover text the controller really typed from structured history."""
        values: list[str] = []
        for name, args, output in getattr(self, "_tool_history", []):
            for kind, candidate in self._communication_entry_events(
                str(name or ""), args or {}, str(output or ""),
            ):
                if kind != "typed":
                    continue
                value = str(candidate or "").strip()
                if 2 <= len(value) <= 240 and value not in values:
                    values.append(value)
        return values

    def _communication_readouts(self) -> list[str]:
        return [
            str(output or "")
            for name, _args, output in getattr(self, "_tool_history", [])
            if name == "read_text" and str(output or "").strip()
        ]

    def _causally_visible_outbound_payloads(self) -> list[str]:
        """Return successful typed payloads seen only after a real send action."""
        history = list(getattr(self, "_tool_history", []) or [])
        sent_turns = self._communication_sent_turns(history)
        readout_records = [
            (index, str(output or ""))
            for index, (name, _args, output) in enumerate(history)
            if name == "read_text" and str(output or "").strip()
        ]
        visible: list[str] = []
        for send_index, value in sent_turns:
            if any(
                send_index < read_index
                and self._communication_text_visible(value, readout)
                for read_index, readout in readout_records
            ):
                visible.append(value)
        return visible

    def _communication_native_mutation_ready(self) -> bool:
        """Require successful, ordered outbound mutations before visual delivery judgment."""
        history = list(getattr(self, "_tool_history", []) or [])
        typed = [
            value
            for name, args, output in history
            for kind, value in self._communication_entry_events(
                str(name or ""), args or {}, str(output or ""),
            )
            if kind == "typed" and str(value or "").strip()
        ]
        sent_turns = self._communication_sent_turns(history)
        mode = _communication_mode(self.task)
        if mode == "draft":
            return bool(typed) and not any(
                self._communication_entry_sends(name, args or {}, output)
                for name, args, output in history
            )
        if mode == "bounded_chat":
            return len(sent_turns) >= 2
        return bool(sent_turns)

    def _image_send_native_mutation_ready(self) -> bool:
        history = list(getattr(self, "_tool_history", []) or [])
        paste_indices = [
            index
            for index, (name, _args, output) in enumerate(history)
            if name == "paste_image_file" and self._communication_mutation_succeeded(output)
        ]
        send_indices = [
            index
            for index, (name, args, output) in enumerate(history)
            if self._communication_entry_sends(name, args or {}, output)
        ]
        return any(
            send_index >= paste_index
            for paste_index in paste_indices
            for send_index in send_indices
        )

    def _deterministic_communication_verdict(self) -> tuple[bool, str] | None:
        """Return a local done verdict when real send/read evidence is enough."""
        if not _is_communication_task(self.task):
            return None
        mode = _communication_mode(self.task)
        if mode == "continuous_chat":
            return False, (
                "这是持续聊天任务；除非用户明确停止或给出结束条件，否则不能自行 done。"
                "继续等待可见回复并基于最新回复作答。"
            )

        if mode == "draft":
            history = list(getattr(self, "_tool_history", []) or [])
            if any(
                self._communication_entry_sends(name, args or {}, output)
                for name, args, output in history
            ):
                return False, (
                    "Draft-only communication task contains a successful send action; "
                    "the requested unsent state was not preserved."
                )
            outbound = self._communication_outbound_payloads()
            declared = _quoted_payloads(self.task)
            drafted = [
                value for value in outbound
                if not declared or any(
                    self._communication_text_visible(item, value) for item in declared
                )
            ]
            if drafted:
                return True, (
                    "Requested communication text was entered as a draft without a send action."
                )
            return False, "Draft-only communication task has no grounded typed-text evidence yet."

        readouts = self._communication_readouts()
        if not readouts:
            return None
        declared = _quoted_payloads(self.task)
        visible_outbound = self._causally_visible_outbound_payloads()
        visible_declared = [
            value for value in declared
            if any(self._communication_text_visible(value, sent) for sent in visible_outbound)
        ]

        if declared and visible_declared:
            return True, "目标会话的 read_text 已出现用户指定的发送正文。"
        if mode == "send" and visible_outbound:
            return True, "目标会话的 read_text 已出现控制器实际输入的发送正文。"
        if mode == "bounded_chat":
            if len(visible_outbound) >= 2:
                return True, "普通聊天已完成一轮往返，且两条实际发送内容均在聊天记录中可见。"
            if visible_outbound:
                return False, (
                    "普通聊天默认需要完成一轮往返：开场消息已经发送并可见，"
                    "请等待对方的新回复，基于该回复再回复一次，read_text 确认后 done。"
                )
        return None

    def _deterministic_image_send_verdict(self) -> tuple[bool, str] | None:
        """Accept a sent image only with current paste and visible delivery evidence."""
        if not _is_communication_task(self.task):
            return None
        task = str(self.task or "").casefold()
        if not any(marker in task for marker in ("截图", "screenshot", "图片", "image", "photo", "照片")):
            return None
        history = list(getattr(self, "_tool_history", []) or [])
        paste_index = next(
            (
                index for index in range(len(history) - 1, -1, -1)
                if history[index][0] == "paste_image_file"
                and self._communication_mutation_succeeded(history[index][2])
            ),
            None,
        )
        if paste_index is None:
            return False, "图片发送任务尚未成功执行 paste_image_file。"
        send_index = next(
            (
                index
                for index in range(paste_index + 1, len(history))
                if self._communication_entry_sends(
                    history[index][0], history[index][1] or {}, history[index][2],
                )
            ),
            None,
        )
        if send_index is None:
            return False, (
                "Image was pasted into the composer, but no successful Send/Enter action "
                "occurred afterward; a preview is not delivery evidence."
            )
        after_paste = "\n".join(
            str(output or "")
            for index, (name, _args, output) in enumerate(history)
            if index > send_index and name in {"observe", "read_text"}
        )
        has_sent_marker = any(marker in after_paste.casefold() for marker in ("已发送", "发送成功", " sent"))
        has_attachment_size = bool(re.search(r"\d+(?:\.\d+)?\s*(?:kb|mb)", after_paste, re.IGNORECASE))
        target_match = re.search(r"(?:给|to)\s*(我的手机|[^，。,.\s]{2,20})", str(self.task or ""), re.IGNORECASE)
        target = target_match.group(1) if target_match else ""
        target_visible = not target or target in after_paste
        if has_sent_marker and has_attachment_size and target_visible:
            return True, "目标会话可见图片附件大小与“已发送”状态，且本轮已执行图片粘贴。"
        return False, "图片已粘贴，但尚未看到当前目标会话中的附件大小和已发送状态。"

    def _call_done_judge(self, client, screen: str, recent: str, evidence: str) -> bool:
        cfg = _config()
        api = cfg.get("api", default={})
        judge_model = (
            cfg.get("agent", "escalation_model", default="")
            or api.get("done_judge_model")
            or api.get("model", "deepseek-v4-flash")
        )
        judge_client = client
        judge_extra_body: dict | None = None
        fresh_image = str(getattr(self, "_fresh_done_image_b64", "") or "")
        pending_literals = list(
            getattr(self, "_pending_visual_literal_tokens", []) or []
        )
        pending_payloads = list(
            getattr(self, "_pending_visual_payloads", []) or []
        )
        pending_spatial_check = str(
            getattr(self, "_pending_visual_spatial_check", "") or ""
        )
        pending_arithmetic_check = str(
            getattr(self, "_pending_visual_arithmetic_check", "") or ""
        )
        pending_communication_check = str(
            getattr(self, "_pending_visual_communication_check", "") or ""
        )
        pending_visual_text = list(dict.fromkeys([
            *pending_literals,
            *pending_payloads,
        ]))
        pending_visual_checks = [
            *pending_visual_text,
            *([pending_spatial_check] if pending_spatial_check else []),
            *([pending_arithmetic_check] if pending_arithmetic_check else []),
            *([pending_communication_check] if pending_communication_check else []),
        ]
        if pending_visual_checks and not fresh_image:
            reason = (
                "A required task condition needs a fresh visual check, but the "
                "authoritative target-window frame is unavailable: "
                + ", ".join(pending_visual_checks[:5])
            )
            return self._reject_done(
                reason,
                f"[done check failed] {reason}. Capture fresh target-window evidence "
                "before calling done again.",
            )
        if fresh_image and not model_supports_vision(judge_model):
            main_model = str(api.get("model") or "")
            if model_supports_vision(main_model):
                judge_model = main_model
            else:
                vlm_model = _configured_vision_judge_model(cfg)
                if not vlm_model:
                    reason = (
                        "A fresh target-window image is required for completion, but the selected "
                        "judge/main model is text-only and no configured vision model can inspect it."
                    )
                    return self._reject_done(reason, f"[done check failed] {reason}")
                try:
                    judge_client = vlm_mod._client()
                    judge_extra_body = vlm_mod._extra_body()
                except Exception as exc:
                    reason = f"The configured visual completion judge could not be created: {exc}"
                    return self._reject_done(reason, f"[done check failed] {reason}")
                judge_model = vlm_model
        sys_p = (
            "你是严格、怀疑的任务完成核验员。判断用户任务是否【真正达成】，不要轻信 agent 的自述。"
            "重要：文件/文件夹/进程/命令类任务，以【确定性证据】(run_shell 输出、Test-Path/Exists 结果、"
            "verified 字段)为准——这类结果在桌面/后台,GUI 界面上【本来就看不到】,"
            "**绝不能因为‘最终界面没显示’就判未完成**。只有可视交互(网页填表/界面内容)才看界面。"
            "同理，窗口清点/状态检查类任务应优先相信 list_windows、scan_windows、read_text 的真实工具输出；"
            "scan_windows 可能会恢复原前台窗口，所以不能仅因当前前台不是目标窗口就判失败。"
            "对‘几分钟后/某时提醒或执行’这类【创建计划】请求，只要 schedule_task 的确定性输出"
            "证明正确任务、时间和 enabled 持久化计划已经建立，当前请求即已完成；不要要求当前 run "
            "一直等待未来触发。未来触发结果属于该计划生成的新 run。"
            "对发消息/发送聊天/邮件/评论等对外通信任务，click/type_text/hotkey 的 ok 或 verified "
            "只证明动作执行，不证明消息已送达；必须有目标会话/收件上下文中的最终可见证据"
            "（例如聊天记录出现要发送的文本、输入框清空并记录出现、或应用明确发送成功状态）才可判 done。"
            '只输出 JSON: {"done": true/false, "reason": "简短中文理由"}。'
        )
        user_p = (
            f"Current local date/time: {datetime.now().astimezone().isoformat()}\n\n"
            f"用户任务：{self.task}\n\n"
            f"agent 声称完成：{self.result_text[:200]}\n\n"
            f"已执行动作(最近)：{recent}\n\n"
            f"确定性证据(工具真实输出/验证结果)：\n{evidence}\n\n"
            f"当前前台界面元素(仅供参考,文件/进程类勿据此判负)：\n{screen[:1000]}"
        )
        if pending_literals:
            literal_checklist = json.dumps(pending_literals, ensure_ascii=False)
            user_p += (
                "\n\nAUTHORITATIVE EXACT-LITERAL VISUAL CHECK:\n"
                f"Required literals: {literal_checklist}\n"
                "Inspect only the attached fresh target-window frame for these literals. "
                "Every literal must be fully and legibly visible, character for character. "
                "Its presence in the task text, agent claim, action log, tool evidence, or "
                "an equivalent/different UI state is not evidence. If any literal is "
                "missing, ambiguous, clipped, or unreadable, return done=false."
            )
        if pending_payloads:
            payload_checklist = json.dumps(pending_payloads, ensure_ascii=False)
            user_p += (
                "\n\nAUTHORITATIVE VISIBLE-PAYLOAD CHECK:\n"
                f"Required visible content: {payload_checklist}\n"
                "Inspect only the attached fresh target-window frame. Every requested "
                "payload must be fully and legibly visible in the correct GUI content area. "
                "Task text, action logs, tool arguments, stale structure, and agent claims "
                "are not evidence. If any payload is absent, clipped, unreadable, or only "
                "appears outside the target content area, return done=false."
            )
        if pending_spatial_check:
            user_p += (
                "\n\nAUTHORITATIVE SPATIAL RESULT CHECK:\n"
                + pending_spatial_check
                + "\nInspect the attached fresh frame itself. Controller plan state, drag "
                  "execution, pixel change, and adjusted coordinates are not proof of the "
                  "final location. Return done=false unless the requested object is clearly "
                  "visible in the requested region."
            )
        if pending_arithmetic_check:
            user_p += (
                "\n\nAUTHORITATIVE ARITHMETIC DISPLAY CHECK:\n"
                + pending_arithmetic_check
                + "\nRead the current calculator display only from the attached fresh frame. "
                  "The task text, typed keys, logs, stale structure, and equivalent values "
                  "elsewhere are not evidence. If the display is unreadable or differs, "
                  "return done=false."
            )
        if pending_communication_check:
            mode_instruction = {
                "draft": (
                    "The requested content must be visibly present in the composer/input area "
                    "and must not appear as a sent transcript item."
                ),
                "send": (
                    "The requested content must appear as a sent message/transcript item in the "
                    "target conversation, not merely remain in the composer/input area."
                ),
                "bounded_chat": (
                    "The fresh transcript must visibly show the completed bounded exchange, "
                    "including the causal outbound turns required by the task."
                ),
            }.get(
                pending_communication_check,
                "The fresh target conversation must visibly show the requested delivered result.",
            )
            user_p += (
                "\n\nAUTHORITATIVE COMMUNICATION STATE CHECK:\n"
                + mode_instruction
                + " Successful typing/click tool results and text elsewhere in the window are "
                  "not delivery evidence. If recipient/conversation context or delivery state "
                  "is ambiguous, return done=false."
            )
        if _is_drawing_task(self.task):
            user_p += (
                "\n\nAUTHORITATIVE DRAWING-QUALITY CHECK:\n"
                "Judge the actual canvas pixels in the attached fresh target-window frame, not "
                "the plan, primitive count, action log, color statistics, or agent claim. The "
                "original user task is the only acceptance contract: do not turn a color, style, "
                "or detail invented only by the agent/plan into a required condition. "
                "requested subject must be immediately recognizable and visibly complete. "
                "Subject-defining parts, proportions, and spatial relationships must form one "
                "coherent composition. If the user requested a good-looking, polished, detailed, "
                "or non-crude result, a few circles/ellipses, a bare geometric outline, an "
                "obviously partial figure, or abandoned/selected shapes are insufficient. "
                "Require multiple meaningful details and intentional visual structure; return "
                "done=false whenever quality or completeness is ambiguous."
            )
        if fresh_image:
            user_p += (
                "\n\nAUTHORITATIVE NATIVE GUI EVIDENCE ORDER:\n"
                "The attached fresh whole-window frame is the authoritative current GUI state. "
                "A recent tool result with ok=true and native_action_executed=true proves that "
                "the low-level native mouse/keyboard path actually dispatched; it does not by "
                "itself prove the goal, so inspect the fresh pixels for the requested result. "
                "If those current pixels clearly show the exact requested result, accept it even "
                "when an earlier attempt failed, source_batch_verified is null, verified is absent, "
                "or a stale controller state says needs_observe. Those are historical or missing "
                "semantic metadata, not evidence that the later successful native action did not "
                "execute. Do not require a redundant observe/read_text call when the fresh frame "
                "is already clear. Reject when the latest native action failed, the current frame "
                "contradicts the claim, or the visual result remains ambiguous."
            )
            user_content: object = [
                {
                    "type": "text",
                    "text": (
                        user_p
                        + "\n\n下面附有刚刚重新捕获的真实目标窗口。对 GUI 任务，必须直接检查整张图："
                          "目标对象、当前页面、动作结果和完成条件有任何看不清或歧义，都判 done=false。"
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{fresh_image}"},
                },
            ]
        else:
            user_content = user_p
        judge_messages = [
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user_content},
        ]
        self._trace_debug("done_check_request", {
            "run_id": self.run_id,
            "model": judge_model,
            # Never copy the base64 frame into the debug trace.  It multiplies
            # trace size and can stall the final gate; the frame id/presence is
            # enough to diagnose routing while the actual request still gets
            # the image in ``judge_messages`` above.
            "messages": [
                {"role": "system", "content": sys_p},
                {
                    "role": "user",
                    "content": user_p + (
                        "\n[authoritative native-vision frame attached; bytes omitted]"
                        if fresh_image else ""
                    ),
                },
            ],
            "native_visual": bool(fresh_image),
        })
        try:
            request_kwargs = {
                "model": judge_model,
                "messages": judge_messages,
                "temperature": 0.0,
                "max_tokens": _JUDGE_MAX_TOKENS,
            }
            if judge_extra_body:
                request_kwargs["extra_body"] = judge_extra_body
            resp = judge_client.chat.completions.create(**request_kwargs)
            tracker = getattr(self, "_track_aux_model_response", None)
            if callable(tracker):
                tracker(resp, purpose="done_judge", model=judge_model)
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
                    # JSON booleans only. Strings such as "false", numbers,
                    # null, or any truthy surrogate must fail closed.
                    is_done = verdict.get("done") is True
                    reason = str(verdict.get("reason", ""))[:200]
                except json.JSONDecodeError:
                    is_done = False
                    reason = "完成核验返回的 JSON 损坏，不能接受为完成"
        except Exception as exc:
            logger.warning("done verification failed closed: %s", exc)
            self._trace_debug("done_check_error", {
                "run_id": self.run_id,
                "model": judge_model,
                "error": str(exc),
            })
            reason = f"Completion judge unavailable; task remains unverified: {exc}"
            return self._reject_done(
                reason,
                "[done check failed] The independent verifier is unavailable. "
                "Collect deterministic evidence and retry, or call fail with the exact blocker.",
            )

        BUS.publish("done_check", run_id=self.run_id, passed=is_done, reason=reason)
        if is_done:
            self._done_verified = True
            return True
        self._last_done_reject_reason = reason
        self._last_done_reject_generation = self._done_evidence_generation()
        self._done_rejects += 1
        if _is_drawing_task(self.task):
            quality_rejects = int(
                getattr(self, "_drawing_quality_rejects", 0) or 0
            ) + 1
            self._drawing_quality_rejects = quality_rejects
            if quality_rejects == 1:
                # The next canvas mutation is the only repair opportunity. Keep
                # an exact history boundary so the runtime can distinguish the
                # already-delivered scene from a later additive detail batch.
                self._drawing_quality_repair_history_start = len(
                    getattr(self, "_tool_history", []) or []
                )
                recovery_message = (
                    f"[drawing quality check failed] {reason}. Preserve the current canvas: "
                    "do not clear, restart, redraw the whole subject, or bulk-undo. Make at most "
                    "one incremental execute_subgoal repair containing only brand-new missing "
                    "semantic details, then call done for one fresh visual judgment. If the fresh "
                    "judge still rejects it and no safe additive repair is clear, call blocked/fail "
                    "honestly instead of continuing to mutate the canvas."
                )
            else:
                recovery_message = (
                    f"[drawing quality check failed again] {reason}. The single safe additive "
                    "repair opportunity has been consumed. Do not mutate, clear, redraw, undo, "
                    "or restart the canvas again; call blocked/fail with this exact visual "
                    "quality blocker instead of entering another repair loop."
                )
        else:
            recovery_message = (
                f"[完成核验未通过] {reason}。任务尚未真正达成，请不要直接调 done——"
                "先把缺的部分补完（必要时重新 observe / 用确定性 expect 验证），完成后再调 done。"
            )
        self.messages.append({
            "role": "user",
            "content": recovery_message,
        })
        logger.info("done 被核验拦截(第%d次): %s", self._done_rejects, reason)
        return False

    def _verify_done(self, client, steps_left: int = 99) -> bool:
        """Independently verify a ``done`` claim before accepting it."""
        self._pending_visual_literal_tokens = []
        self._pending_visual_payloads = []
        self._pending_visual_spatial_check = ""
        self._pending_visual_arithmetic_check = ""
        self._pending_visual_communication_check = ""
        self._saved_artifact_verified = False
        cfg = _config()
        if not cfg.get("agent", "verify_done", default=True):
            return True
        run_state = getattr(self, "run_state", None)
        pending_actions = int(getattr(run_state, "unverified_actions", 0) or 0)
        # Coding/file/shell runs use deterministic tool output and the final
        # independent judge. Their RunState counter also includes successful
        # run_shell/write_file actions, so treating it as pending *desktop*
        # evidence creates an impossible loop after a valid file check.
        session_epoch = int(getattr(self.session, "_screen_epoch", 0) or 0)
        visual_feedback_fresh = bool(
            getattr(self, "_last_visual_feedback_b64", "")
            and int(getattr(self, "_visual_frame_epoch", -1)) == session_epoch
            and int(getattr(self, "_visual_frame_hwnd", 0) or 0)
            == int(getattr(self.session, "hwnd", 0) or 0)
        )
        independent_visual_check_possible = bool(
            visual_feedback_fresh
            or str(getattr(run_state, "phase", "") or "") == "visually_grounded"
        )
        if (
            pending_actions > 0
            and not bool(getattr(self, "_is_coding", False))
            and not independent_visual_check_possible
        ):
            last_tool = str(getattr(run_state, "last_tool", "") or "")
            last_issue = str(getattr(run_state, "last_issue", "") or "")
            for item in reversed(getattr(run_state, "history", []) or []):
                if not isinstance(item, dict):
                    continue
                if str(item.get("phase") or "") not in {"recovering", "need_observe"}:
                    continue
                last_tool = str(item.get("tool") or last_tool)
                last_issue = str(item.get("issue") or last_issue)
                break
            reason = (
                f"Done is blocked because {pending_actions} desktop action(s) remain "
                f"failed or unverified after {last_tool or 'the last tool'}"
                + (f": {last_issue}" if last_issue else ".")
            )
            return self._reject_done(
                reason,
                f"[done check failed] {reason} Recover the failed action and collect "
                "fresh target-window evidence before calling done again.",
            )
        max_rejects = int(cfg.get("agent", "done_max_rejects", default=2))
        if self._done_rejects >= max_rejects:
            reject_generation = getattr(self, "_last_done_reject_generation", None)
            current_generation = self._done_evidence_generation()
            if reject_generation is not None and current_generation != reject_generation:
                # New causal evidence means this is not the same repeated
                # unsupported claim. Let the skeptical gate inspect it once.
                self._done_rejects = 0
            else:
                reason = (
                    "Done was rejected repeatedly and cannot be force-accepted. "
                    "Continue only if new deterministic evidence can be collected; "
                    "otherwise call fail with the exact blocker."
                )
                return self._reject_done(reason, f"[done check failed] {reason}")

        recent, evidence = self._done_evidence()
        screen: str | None = None
        if _is_communication_task(self.task):
            screen = self._fresh_done_screen()
        communication_evidence_passed = False
        image_send_verdict = self._deterministic_image_send_verdict()
        if image_send_verdict is not None:
            passed, reason = image_send_verdict
            if not passed:
                if (
                    str(getattr(self, "_fresh_done_image_b64", "") or "")
                    and self._image_send_native_mutation_ready()
                ):
                    communication_evidence_passed = True
                    evidence += (
                        "\n- causal_image_delivery_mutation: paste and send succeeded; "
                        "fresh frame must prove delivery"
                    )
                else:
                    return self._reject_done(reason, f"[done check failed] {reason}")
            else:
                communication_evidence_passed = True
                evidence += "\n- deterministic_image_delivery: " + reason
        if _is_communication_task(self.task) and not communication_evidence_passed:
            communication_verdict = self._deterministic_communication_verdict()
            if communication_verdict is None:
                if (
                    str(getattr(self, "_fresh_done_image_b64", "") or "")
                    and self._communication_native_mutation_ready()
                ):
                    evidence += (
                        "\n- causal_communication_mutation: outbound action succeeded; "
                        "fresh frame must prove draft/delivery state"
                    )
                else:
                    reason = (
                        "Communication completion lacks causal evidence: a successful outbound "
                        "mutation followed by a later target-conversation readout is required "
                        "when no fresh visual frame can prove delivery."
                    )
                    return self._reject_done(reason, f"[done check failed] {reason}")
            else:
                passed, reason = communication_verdict
                if not passed:
                    return self._reject_done(reason, f"[done check failed] {reason}")
                evidence += "\n- causal_communication_evidence: " + reason
        if _is_drawing_task(self.task):
            # Capture before the drawing gates so a native visual model can
            # satisfy semantic/detail verification directly, without first
            # calling a secondary VLM tool.
            screen = self._fresh_done_screen()
            if not self._verify_drawing_done(evidence):
                return False
            # Color presence and pixel deltas prove that paint changed, not that
            # the requested object/shape is actually present.  Keep them as a
            # necessary gate above, then let the fresh goal-conditioned visual
            # judge inspect the complete result instead of upgrading a local
            # color statistic into task success.

        if screen is None:
            screen = self._fresh_done_screen()
        if not self._verify_drawing_semantic_evidence(evidence):
            return False
        arithmetic_verdict = _deterministic_arithmetic_verdict(self.task, screen)
        if arithmetic_verdict is not None:
            passed, reason = arithmetic_verdict
            fresh_native_image = bool(
                str(getattr(self, "_fresh_done_image_b64", "") or "")
            )
            fresh_ocr_positive = bool(
                passed and "Fresh visual OCR:" in screen
            )
            if passed and (not fresh_native_image or fresh_ocr_positive):
                BUS.publish("done_check", run_id=self.run_id, passed=True, reason=reason)
                self._done_verified = True
                return True
            if fresh_native_image:
                # OCR is allowed to prove a positive exact display match, but
                # an OCR miss/contradiction cannot overrule the authoritative
                # fresh frame. Let the independent visual judge read it.
                self._pending_visual_arithmetic_check = reason
            else:
                return self._reject_done(reason, f"[done check failed] {reason}")
        saved_file_verdict = self._deterministic_saved_file_verdict(screen, evidence)
        if saved_file_verdict is not None:
            passed, reason = saved_file_verdict
            if not passed:
                return self._reject_done(reason, f"[done check failed] {reason}")
            # Parsed file content/format and current title are necessary
            # artifact evidence. Overall task completion still reaches the
            # independent judge below.
            artifact_paths = _explicit_saved_paths(self.task)
            self._saved_artifact_verified = bool(artifact_paths) and all(
                path.suffix.casefold() in {".txt", ".md", ".csv", ".docx", ".xlsx"}
                for path in artifact_paths
            )
            evidence += "\n- deterministic_saved_file: " + reason
        if not self._verify_visible_payloads(screen, evidence):
            return False
        fresh_native_image = bool(
            str(getattr(self, "_fresh_done_image_b64", "") or "")
        )
        if (
            not fresh_native_image
            and self._desktop_drag_has_deterministic_done_evidence(screen, evidence)
        ):
            reason = (
                "Desktop icon drag accepted from deterministic evidence: target "
                "listitem at requested area plus drag visual_change_detected=true."
            )
            BUS.publish("done_check", run_id=self.run_id, passed=True, reason=reason)
            self._done_verified = True
            return True
        if _is_desktop_icon_drag_task(self.task):
            if fresh_native_image:
                self._pending_visual_spatial_check = (
                    "Verify that the requested desktop icon/folder is now visibly located "
                    "in the exact destination region described by the user."
                )
            else:
                reason = (
                    "Desktop drag has action/pixel evidence, but neither fresh target "
                    "geometry nor a fresh visual frame proves the requested final location."
                )
                return self._reject_done(reason, f"[done check failed] {reason}")
        if not self._verify_required_literal_tokens(screen, evidence):
            return False
        if (
            not self._pending_visual_literal_tokens
            and not self._pending_visual_payloads
            and not _is_drawing_task(self.task)
            and (
                not fresh_native_image
                or self._task_accepts_historical_visible_state(self.task)
            )
            and self._visible_state_has_deterministic_done_evidence(screen, evidence)
        ):
            return True
        if not self._verify_gui_search_done(screen):
            return False
        if not self._verify_communication_done(screen):
            return False
        return self._call_done_judge(client, screen, recent, evidence)
