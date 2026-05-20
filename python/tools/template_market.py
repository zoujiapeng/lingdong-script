"""
Self-hosted Template Market — 社区模板市场

从 GitHub raw 源获取模板索引，支持搜索/安装/发布。

用法:
  python tools/template_market.py search <query>
  python tools/template_market.py install <id>
  python tools/template_market.py list
  python tools/template_market.py publish <path> [--name <name>]
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

# ── 默认模板源 ────────────────────────────────────────────────
# 指向一个公开 GitHub 仓库的 raw 内容。
# 格式: {owner}/{repo}[/{path}]  # raw.githubusercontent.com 自动解析
# 用户可以设置 LOBSTER_TEMPLATE_SOURCE 环境变量覆盖
DEFAULT_SOURCE = (
    "zou1724/lobster-templates"
)
RAW_BASE = "https://raw.githubusercontent.com"
INDEX_CACHE_TTL = 300  # 5 分钟缓存

_INDEX_CACHE: dict = {"data": None, "time": 0}


def _get_template_dir() -> Path:
    """获取本地模板目录 (项目根目录下的 templates/)"""
    return Path(__file__).resolve().parent.parent.parent / "templates"


def _get_source_url() -> str:
    """获取完整的 index.json URL"""
    source = os.environ.get("LOBSTER_TEMPLATE_SOURCE", DEFAULT_SOURCE)
    return f"{RAW_BASE}/{source}/main/index.json"


def _get_raw_base() -> str:
    source = os.environ.get("LOBSTER_TEMPLATE_SOURCE", DEFAULT_SOURCE)
    return f"{RAW_BASE}/{source}/main"


def fetch_index(force: bool = False) -> list[dict]:
    """
    从远程源获取模板索引。
    结果缓存在内存中 (INDEX_CACHE_TTL 秒)。
    """
    now = time.time()
    if not force and _INDEX_CACHE["data"] and (now - _INDEX_CACHE["time"]) < INDEX_CACHE_TTL:
        return _INDEX_CACHE["data"]

    import urllib.request

    url = _get_source_url()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "lobster-template-market/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"❌ 无法获取模板索引: {e}", file=sys.stderr)
        print(f"   源: {url}", file=sys.stderr)
        print(f"   请检查网络连接或设置 LOBSTER_TEMPLATE_SOURCE 环境变量指向你的模板仓库", file=sys.stderr)
        sys.exit(1)

    templates = data.get("templates", [])
    _INDEX_CACHE["data"] = templates
    _INDEX_CACHE["time"] = now
    return templates


def search(query: str, max_results: int = 10) -> list[dict]:
    """搜索远程模板（客户端过滤）"""
    templates = fetch_index()
    q = query.lower().strip()

    scored: list[tuple[dict, int]] = []
    for t in templates:
        score = 0
        name = (t.get("name", "") or "").lower()
        desc = (t.get("description", "") or "").lower()
        tags = [tag.lower() for tag in t.get("tags", [])]
        category = (t.get("category", "") or "").lower()

        if q in name:
            score += 50
        if name == q:
            score = 100
        if q in desc:
            score += 30
        if any(q in tag for tag in tags):
            score += 20
        if q in category:
            score += 10

        if score > 0:
            scored.append((t, score))

    scored.sort(key=lambda x: -x[1])
    return [t for t, s in scored[:max_results]]


def install(template_id: str) -> Path:
    """
    从远程源安装模板到本地 templates/ 目录。
    返回本地文件路径。
    """
    templates = fetch_index()

    # 按 id 查找
    match = None
    for t in templates:
        if t.get("id") == template_id:
            match = t
            break
    # 按 name 查找
    if not match:
        for t in templates:
            if t.get("name") == template_id:
                match = t
                break
    if not match:
        print(f"❌ 未找到模板: {template_id}", file=sys.stderr)
        print(f"   可用: lobster template search <关键字>", file=sys.stderr)
        sys.exit(1)

    remote_file = match.get("file", f"templates/{template_id}.flow")
    url = f"{_get_raw_base()}/{remote_file}"

    import urllib.request

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "lobster-template-market/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read()
    except Exception as e:
        print(f"❌ 下载模板失败: {e}", file=sys.stderr)
        print(f"   URL: {url}", file=sys.stderr)
        sys.exit(1)

    # 校验内容是否为有效 flow JSON
    try:
        flow_data = json.loads(content)
        if "nodes" not in flow_data:
            print("⚠️  下载的文件缺少 nodes 字段，可能不是有效的 .flow 文件", file=sys.stderr)
    except json.JSONDecodeError:
        pass  # 可能是 .dsl 格式

    # 保存到本地
    template_dir = _get_template_dir()
    template_dir.mkdir(parents=True, exist_ok=True)

    local_filename = f"{template_id}.flow"
    local_path = template_dir / local_filename

    # 如果文件已存在，先备份
    if local_path.exists():
        backup = local_path.with_suffix(f".flow.bak")
        local_path.rename(backup)
        print(f"📦 已备份旧模板: {backup.name}")

    local_path.write_bytes(content)

    name = match.get("name", template_id)
    desc = match.get("description", "")
    print(f"✅ 已安装模板: {name}")
    print(f"   路径: {local_path}")
    if desc:
        print(f"   说明: {desc}")
    return local_path


def list_installed() -> list[dict]:
    """列出本地已安装的模板"""
    template_dir = _get_template_dir()
    if not template_dir.exists():
        return []

    installed = []
    for f in sorted(template_dir.iterdir()):
        if f.suffix in (".flow", ".dsl"):
            try:
                if f.suffix == ".flow":
                    data = json.loads(f.read_bytes())
                    name = data.get("meta", {}).get("name", f.stem)
                    desc = data.get("meta", {}).get("description", "")
                else:
                    name = f.stem
                    desc = ""
                installed.append({
                    "id": f.stem,
                    "name": name,
                    "description": desc,
                    "path": str(f),
                    "format": f.suffix[1:],
                    "size": f.stat().st_size,
                })
            except Exception:
                installed.append({
                    "id": f.stem,
                    "name": f.stem,
                    "description": "",
                    "path": str(f),
                    "format": f.suffix[1:],
                    "size": f.stat().st_size,
                })
    return installed


# ── 发布功能（可选：生成 PR 到源仓库，或打印内容供手动提交）──

def publish(template_path: str, name: Optional[str] = None) -> dict:
    """
    准备发布模板：验证 + 生成 index 条目。
    返回模板信息和 index 条目，不直接推送。
    """
    p = Path(template_path)
    if not p.exists():
        print(f"❌ 文件不存在: {template_path}", file=sys.stderr)
        sys.exit(1)

    content = p.read_bytes()
    try:
        flow_data = json.loads(content)
    except json.JSONDecodeError:
        print("❌ 文件不是有效的 JSON", file=sys.stderr)
        sys.exit(1)

    if "nodes" not in flow_data:
        print("❌ 文件缺少 nodes 字段，不是有效的 .flow 文件", file=sys.stderr)
        sys.exit(1)

    meta = flow_data.get("meta", {})
    template_name = name or meta.get("name", p.stem)
    import re, hashlib
    entry_id = re.sub(r'[^a-zA-Z0-9_-]', '', template_name.replace(' ', '-').lower())
    if not entry_id or len(entry_id) < 2:
        # Chinese-only names: use first 8 chars of hex digest
        entry_id = hashlib.md5(p.stem.encode()).hexdigest()[:8]
    description = meta.get("description", "")

    index_entry = {
        "id": entry_id,
        "name": template_name,
        "description": description,
        "category": "general",
        "tags": [],
        "author": "<your-github-username>",
        "version": "1.0.0",
        "file": f"templates/{p.name}",
    }

    print(f"\n📤 准备发布: {template_name}")
    print(f"   文件: {p.resolve()}")
    print(f"   节点数: {len(flow_data.get('nodes', []))}")
    print(f"\n📋 Index 条目 (添加到 index.json 的 templates 数组中):")
    print(json.dumps(index_entry, ensure_ascii=False, indent=2))
    print(f"\n📌 操作步骤:")
    print(f"   1. Fork https://github.com/{DEFAULT_SOURCE}")
    print(f"   2. 将 {p.name} 复制到仓库的 templates/ 目录")
    print(f"   3. 将以上 index 条目添加到 index.json 的 templates 数组中")
    print(f"   4. 提交 PR")

    return index_entry


# ── CLI ───────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "search":
        query = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""
        if not query:
            print("用法: python tools/template_market.py search <query>", file=sys.stderr)
            sys.exit(1)
        results = search(query)
        if not results:
            print("未找到匹配的模板")
            return
        print(f"📋 找到 {len(results)} 个模板:\n")
        for r in results:
            tags = ", ".join(r.get("tags", []))
            print(f"  [{r['id']}]")
            print(f"    名称: {r.get('name', '?')}")
            print(f"    说明: {r.get('description', '')}")
            if tags:
                print(f"    标签: {tags}")
            print()

    elif cmd == "install":
        if len(sys.argv) < 3:
            print("用法: python tools/template_market.py install <id>", file=sys.stderr)
            sys.exit(1)
        install(sys.argv[2])

    elif cmd == "list":
        installed = list_installed()
        if not installed:
            print("本地未安装任何模板")
            print(f"模板目录: {_get_template_dir()}")
            return
        print(f"📦 本地模板 ({len(installed)}):\n")
        for t in installed:
            size_kb = t["size"] / 1024
            print(f"  [{t['id']}] {t['name']}  ({size_kb:.1f} KB)")
            if t["description"]:
                print(f"    {t['description']}")

    elif cmd == "publish":
        if len(sys.argv) < 3:
            print("用法: python tools/template_market.py publish <path> [--name <name>]", file=sys.stderr)
            sys.exit(1)
        path = sys.argv[2]
        name = None
        if "--name" in sys.argv:
            idx = sys.argv.index("--name")
            if idx + 1 < len(sys.argv):
                name = sys.argv[idx + 1]
        publish(path, name)

    elif cmd == "refresh":
        fetch_index(force=True)
        print("✅ 模板索引已刷新")

    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
