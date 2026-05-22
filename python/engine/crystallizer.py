"""
结晶化 — 执行成功后将流程自动保存为可复用模板。

每次 DSL/FlowScript 执行成功后调用 crystallize()，
生成 .flow 文件存入 templates/ 目录，同时更新 skill_tree.json 索引。
"""
from __future__ import annotations

import json
import os
import re
import time
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("lobster.crystallizer")

# ── 路径 ──────────────────────────────────────────────────────
_PYTHON_DIR = Path(__file__).resolve().parent.parent  # python/
_TEMPLATES_DIR = _PYTHON_DIR.parent / "templates"      # templates/
_TREE_PATH = _TEMPLATES_DIR / "skill_tree.json"


def _ensure_dirs():
    _TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)


# ── 名称推导 ──────────────────────────────────────────────────

def _derive_template_name(script: dict, dsl_text: str) -> str:
    """从 script meta 或 DSL 内容生成模板名称。"""
    # 优先用已有名称
    meta = script.get("meta", {}) if script else {}
    name = meta.get("name", "") or ""
    if name and name not in ("未命名流程", "新流程", ""):
        return _sanitize_name(name)

    # 从 DSL 第一行提取动作+目标
    first_line = (dsl_text or "").strip().split("\n")[0].strip()
    if first_line:
        # "CLICK 500 400" → "click_500_400"
        short = re.sub(r"[^a-zA-Z0-9一-鿿_]", "_", first_line)
        short = re.sub(r"_+", "_", short).strip("_")[:40]
        if short:
            return f"自动_{short}"

    return f"结晶_{int(time.time())}"


def _sanitize_name(name: str) -> str:
    """清理文件名。"""
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return name.strip()[:80]


# ── Skill Tree 索引 ───────────────────────────────────────────

def _load_tree() -> dict:
    """读取 skill_tree.json。"""
    if _TREE_PATH.exists():
        try:
            return json.loads(_TREE_PATH.read_text("utf-8"))
        except Exception:
            pass
    return {"version": 1, "categories": {}}


def _save_tree(tree: dict):
    """写 skill_tree.json。"""
    _TREE_PATH.write_text(json.dumps(tree, ensure_ascii=False, indent=2), "utf-8")


def _categorize(script: dict, dsl_text: str) -> str:
    """用 DSL 首个关键字或脚本 tags 确定分类。简单实现。"""
    tags = (script.get("meta", {}) if script else {}).get("tags", [])
    if tags and isinstance(tags, list):
        return tags[0]

    first_kw = (dsl_text or "").strip().split("\n")[0].strip().split()[0].upper() if dsl_text else ""
    category_map = {
        "CLICK": "屏幕操作",
        "TYPE": "屏幕操作",
        "LAUNCH": "软件启动",
        "WAIT": "等待监控",
        "WAITSCREEN": "等待监控",
        "SCREENSHOT": "截图处理",
        "IMAGE_FIND": "截图处理",
        "IMAGE_CLICK": "截图处理",
        "LOOP": "循环流程",
        "IF": "条件判断",
        "HOTKEY": "快捷键",
        "KEYBOARD": "快捷键",
        "SET": "变量操作",
        "FILEDIALOG_SAVE": "文件操作",
        "FILEDIALOG_OPEN": "文件操作",
        "CALL": "子流程",
        "WHEN": "事件触发",
    }
    return category_map.get(first_kw, "通用")


# ── 模板保存 ──────────────────────────────────────────────────

def _make_flow_id(name: str) -> str:
    """从名称生成文件 ID。"""
    safe = re.sub(r"[^a-zA-Z0-9一-鿿_-]", "_", name)
    safe = re.sub(r"_+", "_", safe).strip("_")[:60]
    return safe or f"flow_{int(time.time())}"


def _crystallize_flow(script: dict, dsl_text: str, name: str) -> Optional[str]:
    """将 FlowScript 保存为 .flow 模板文件并更新索引。"""
    _ensure_dirs()
    flow_id = _make_flow_id(name)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    # 更新 script meta
    if script and "meta" in script:
        script["meta"]["id"] = flow_id
        script["meta"]["name"] = name
        script["meta"]["updated_at"] = now
        if not script["meta"].get("created_at"):
            script["meta"]["created_at"] = now
        script["meta"]["version"] = "1.0"

    # 写 .flow 文件
    flow_path = _TEMPLATES_DIR / f"{flow_id}.flow"
    flow_data = script if script else {
        "meta": {"id": flow_id, "name": name, "created_at": now, "updated_at": now, "version": "1.0"},
        "nodes": [],
        "edges": [],
        "variables": {},
        "settings": {"default_delay": 0.5, "error_handling": "stop", "max_retries": 0, "retry_delay": 1, "log_level": "info"},
    }
    flow_path.write_text(json.dumps(flow_data, ensure_ascii=False, indent=2), "utf-8")

    # 有 DSL 文本时也写 .dsl 文件
    if dsl_text and dsl_text.strip():
        dsl_path = _TEMPLATES_DIR / f"{flow_id}.dsl"
        dsl_path.write_text(dsl_text.strip(), "utf-8")

    # 更新 skill tree 索引
    category = _categorize(script, dsl_text)
    tree = _load_tree()
    cat = tree.setdefault("categories", {}).setdefault(category, {})
    patterns = cat.setdefault("patterns", {})
    # 用 name 的前两个词做 pattern key
    pattern_key = "_".join(name.split("_")[:2]) if "_" in name else name[:10]
    pattern_list = patterns.setdefault(pattern_key, [])
    if flow_id not in pattern_list:
        pattern_list.append(flow_id)
    tree["version"] = tree.get("version", 1) + 1
    _save_tree(tree)

    logger.info(f"[crystallizer] 已结晶: {flow_path.name} (分类: {category})")
    return flow_id


# ── 公开 API ──────────────────────────────────────────────────

def crystallize(script: dict, dsl_text: str = "") -> Optional[str]:
    """执行成功后调用。script 可以是 FlowScript dict 或 None。

    Args:
        script: FlowScript JSON dict，可为 None（只传 DSL 时）
        dsl_text: 原始 DSL 文本

    Returns:
        模板 ID（成功）或 None
    """
    if not script and not dsl_text:
        return None

    name = _derive_template_name(script, dsl_text)

    # 已有 FlowScript → 直接保存
    if script and isinstance(script, dict) and script.get("nodes") is not None:
        return _crystallize_flow(script, dsl_text, name)

    # 只有 DSL 文本 → 先转 FlowScript，再保存
    if dsl_text and dsl_text.strip():
        try:
            from engine.dsl_flow_converter import dsl_to_flow
            flow = dsl_to_flow(dsl_text)
            return _crystallize_flow(flow, dsl_text, name)
        except Exception as e:
            logger.warning(f"[crystallizer] DSL→Flow 转换失败，保存 DSL 文本: {e}")
            # 至少保存 DSL 文本
            _ensure_dirs()
            flow_id = _make_flow_id(name)
            dsl_path = _TEMPLATES_DIR / f"{flow_id}.dsl"
            dsl_path.write_text(dsl_text.strip(), "utf-8")
            return flow_id

    return None


def list_crystallized() -> list[dict]:
    """列出所有已结晶模板。"""
    if not _TEMPLATES_DIR.exists():
        return []
    results = []
    for f in sorted(_TEMPLATES_DIR.glob("*.flow"), key=os.path.getmtime, reverse=True):
        try:
            data = json.loads(f.read_text("utf-8"))
            meta = data.get("meta", {})
            results.append({
                "id": meta.get("id", f.stem),
                "name": meta.get("name", f.stem),
                "description": meta.get("description", ""),
                "path": str(f),
                "size": f.stat().st_size,
                "created": meta.get("created_at", ""),
            })
        except Exception:
            continue
    # 也包含纯 .dsl 文件
    seen_ids = {r["id"] for r in results}
    for f in sorted(_TEMPLATES_DIR.glob("*.dsl"), key=os.path.getmtime, reverse=True):
        if f.stem not in seen_ids:
            results.append({
                "id": f.stem,
                "name": f.stem,
                "description": "DSL 文本模板",
                "path": str(f),
                "size": f.stat().st_size,
                "created": "",
            })
    return results


def get_skill_tree() -> dict:
    """获取 skill tree 索引。"""
    return _load_tree()
