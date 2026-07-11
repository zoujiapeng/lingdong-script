"""Shared OCR line reconstruction for incremental and on-demand perception."""
from __future__ import annotations

from .model import UIElement


def merge_line_elements(elements: list[UIElement]) -> list[UIElement]:
    """Build line-level evidence without replacing precise OCR word boxes."""
    rows: list[list[UIElement]] = []
    for element in sorted(elements, key=lambda item: (item.cy, item.left)):
        if not element.text:
            continue
        row = next((
            candidate for candidate in rows
            if abs(sum(item.cy for item in candidate) / len(candidate) - element.cy)
            <= max(8, element.height)
        ), None)
        if row is None:
            rows.append([element])
        else:
            row.append(element)

    merged: list[UIElement] = []
    for row in rows:
        segments: list[list[UIElement]] = []
        for element in sorted(row, key=lambda item: item.left):
            if not segments:
                segments.append([element])
                continue
            previous = segments[-1][-1]
            gap = element.left - (previous.left + previous.width)
            if gap > max(64, max(previous.height, element.height) * 4):
                segments.append([element])
            else:
                segments[-1].append(element)
        for segment in segments:
            if len(segment) < 2:
                continue
            left = min(item.left for item in segment)
            top = min(item.top for item in segment)
            right = max(item.left + item.width for item in segment)
            bottom = max(item.top + item.height for item in segment)
            states = sorted({state for item in segment for state in item.states})
            if "ocr_line_merge" not in states:
                states.append("ocr_line_merge")
            merged.append(UIElement(
                source="ocr",
                role="text",
                text=" ".join(item.text.strip() for item in segment if item.text.strip()),
                cx=(left + right) // 2,
                cy=(top + bottom) // 2,
                left=left,
                top=top,
                width=max(1, right - left),
                height=max(1, bottom - top),
                confidence=min(float(item.confidence) for item in segment),
                states=states,
            ))
    return merged
