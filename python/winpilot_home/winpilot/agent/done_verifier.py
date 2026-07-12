"""Done-gate verification helpers for AgentRunner.

The runner loop calls ``_verify_done`` whenever the model claims completion.
Keeping this policy outside ``loop.py`` makes the orchestration path easier to
read while preserving the same guard behavior.
"""
from __future__ import annotations

import ast
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from ..config import CONFIG
from ..perception import fusion, intent, ocr, vision_cv
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
    r"\b(?:detailed|intricate|elaborate|high[- ]?detail)\b|"
    r"\u7ec6\u8282|\u7cbe\u7ec6|\u7cbe\u81f4|\u8be6\u7ec6",
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
            text = snap.to_text(limit) if snap else ""
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

        combined = "\n".join([screen, evidence, *file_texts])
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
        required_payloads = _required_visible_payloads(self.task)
        if not required_payloads:
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
        tokens = _required_literal_tokens(self.task)
        if not tokens:
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
        if detailed_requested and not (semantic_verdict and semantic_verdict[0]):
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
                f"[done check failed] {reason} Obtain a fresh VLM/object-level verification "
                "after the final canvas mutation. Only call done when that single visual "
                "verdict confirms the requested subject and detail level; otherwise repair "
                "the drawing or call fail with the remaining issues.",
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
        if (
            _COLORFUL_DRAWING_RE.search(self.task or "")
            or _DETAILED_DRAWING_RE.search(self.task or "")
        ):
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
        plan = getattr(getattr(self, "session", None), "plan", None)
        plan_done = bool(plan is not None and plan.all_done())
        controller_done = (
            "auto-completed desktop drag plan item" in normalized
            or "adjusted_drop_target" in normalized
        )
        if plan_done or controller_done:
            return True
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

    @staticmethod
    def _communication_text_visible(needle: str, text: str) -> bool:
        needle_norm = re.sub(r"\s+", "", str(needle or "").casefold())
        text_norm = re.sub(r"\s+", "", str(text or "").casefold())
        return bool(needle_norm and len(needle_norm) >= 2 and needle_norm in text_norm)

    def _communication_outbound_payloads(self) -> list[str]:
        """Recover text the controller really typed from structured history."""
        values: list[str] = []
        for name, args, _output in getattr(self, "_tool_history", []):
            candidates: list[object] = []
            if name == "type_text":
                candidates.append(args.get("text"))
            elif name == "execute_subgoal":
                input_spec = args.get("input")
                if isinstance(input_spec, dict):
                    candidates.append(input_spec.get("text") or input_spec.get("value"))
            elif name == "run_action_batch":
                for action in args.get("actions") or []:
                    if not isinstance(action, dict):
                        continue
                    if action.get("action") in {"type_text", "type_into_element"}:
                        candidates.append(action.get("text") or action.get("value"))
            for candidate in candidates:
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
        visible_text = "\n".join(readouts[-4:])
        declared = _quoted_payloads(self.task)
        outbound = self._communication_outbound_payloads()
        visible_declared = [
            value for value in declared
            if self._communication_text_visible(value, visible_text)
        ]
        visible_outbound = [
            value for value in outbound
            if self._communication_text_visible(value, visible_text)
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
                and '"ok": true' in str(history[index][2] or "").casefold()
            ),
            None,
        )
        if paste_index is None:
            return False, "图片发送任务尚未成功执行 paste_image_file。"
        after_paste = "\n".join(str(item[2] or "") for item in history[paste_index + 1:])
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
                    is_done = bool(verdict.get("done", False))
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
        run_state = getattr(self, "run_state", None)
        pending_actions = int(getattr(run_state, "unverified_actions", 0) or 0)
        # Coding/file/shell runs use deterministic tool output and the final
        # independent judge. Their RunState counter also includes successful
        # run_shell/write_file actions, so treating it as pending *desktop*
        # evidence creates an impossible loop after a valid file check.
        if pending_actions > 0 and not bool(getattr(self, "_is_coding", False)):
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
            reason = (
                "Done was rejected repeatedly and cannot be force-accepted. "
                "Continue only if new deterministic evidence can be collected; "
                "otherwise call fail with the exact blocker."
            )
            return self._reject_done(reason, f"[done check failed] {reason}")

        recent, evidence = self._done_evidence()
        image_send_verdict = self._deterministic_image_send_verdict()
        if image_send_verdict is not None:
            passed, reason = image_send_verdict
            if passed:
                BUS.publish("done_check", run_id=self.run_id, passed=True, reason=reason)
                self._done_verified = True
                return True
            return self._reject_done(reason, f"[完成核验未通过] {reason}")
        communication_verdict = self._deterministic_communication_verdict()
        if communication_verdict is not None:
            passed, reason = communication_verdict
            if passed:
                BUS.publish("done_check", run_id=self.run_id, passed=True, reason=reason)
                self._done_verified = True
                return True
            return self._reject_done(reason, f"[完成核验未通过] {reason}")
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
        arithmetic_verdict = _deterministic_arithmetic_verdict(self.task, screen)
        if arithmetic_verdict is not None:
            passed, reason = arithmetic_verdict
            if passed:
                BUS.publish("done_check", run_id=self.run_id, passed=True, reason=reason)
                self._done_verified = True
                return True
            return self._reject_done(reason, f"[done check failed] {reason}")
        if not self._verify_visible_payloads(screen, evidence):
            return False
        if self._desktop_drag_has_deterministic_done_evidence(screen, evidence):
            reason = (
                "Desktop icon drag accepted from deterministic evidence: target "
                "listitem at requested area plus drag visual_change_detected=true."
            )
            BUS.publish("done_check", run_id=self.run_id, passed=True, reason=reason)
            self._done_verified = True
            return True
        saved_file_verdict = self._deterministic_saved_file_verdict(screen, evidence)
        if saved_file_verdict is not None:
            passed, reason = saved_file_verdict
            if passed:
                BUS.publish("done_check", run_id=self.run_id, passed=True, reason=reason)
                self._done_verified = True
                return True
            return self._reject_done(reason, f"[done check failed] {reason}")
        if not self._verify_required_literal_tokens(screen, evidence):
            return False
        if self._visible_state_has_deterministic_done_evidence(screen, evidence):
            return True
        if not self._verify_gui_search_done(screen):
            return False
        if not self._verify_communication_done(screen):
            return False
        return self._call_done_judge(client, screen, recent, evidence)
