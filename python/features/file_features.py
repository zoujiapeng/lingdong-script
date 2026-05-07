"""
文件操作功能
直接文件 I/O：读写文件、目录列表、复制移动删除
"""
import os
import shutil
from pathlib import Path

from features.registry import feature, P, TC, FeatureCategory as F


@feature(
    name="file_read",
    display_name="读取文件",
    description="【文件】读取指定文件的全部内容，返回文本。用于查看源代码、配置文件、日志等",
    category=F.SYSTEM,
    params=[
        P("path", "str", "文件路径", example="C:/Users/test.txt"),
        P("encoding", "str", "文件编码", required=False, default="utf-8"),
    ],
    returns="dict{success, content, size}",
    tags=["file", "read"],
)
def file_read(path: str, encoding: str = "utf-8") -> dict:
    try:
        p = Path(path)
        if not p.exists():
            return {"success": False, "error": f"文件不存在: {path}"}
        if not p.is_file():
            return {"success": False, "error": f"路径不是文件: {path}"}
        content = p.read_text(encoding=encoding)
        return {"success": True, "content": content, "size": len(content)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@feature(
    name="file_write",
    display_name="写入文件",
    description="【文件】将文本写入指定文件。若文件不存在则创建，存在则覆盖。用于保存生成的代码、报告、配置等",
    category=F.SYSTEM,
    params=[
        P("path", "str", "文件路径", example="C:/Users/output.txt"),
        P("content", "str", "要写入的内容", example="Hello World"),
        P("encoding", "str", "文件编码", required=False, default="utf-8"),
    ],
    returns="dict{success, size}",
    tags=["file", "write"],
)
def file_write(path: str, content: str, encoding: str = "utf-8") -> dict:
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding=encoding)
        return {"success": True, "size": len(content)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@feature(
    name="file_append",
    display_name="追加文件",
    description="【文件】将文本追加到指定文件末尾。用于写日志、增量记录",
    category=F.SYSTEM,
    params=[
        P("path", "str", "文件路径", example="C:/Users/log.txt"),
        P("content", "str", "要追加的内容", example="new log entry"),
        P("encoding", "str", "文件编码", required=False, default="utf-8"),
    ],
    returns="dict{success, size}",
    tags=["file", "append"],
)
def file_append(path: str, content: str, encoding: str = "utf-8") -> dict:
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding=encoding) as f:
            f.write(content)
        return {"success": True, "size": len(content)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@feature(
    name="file_delete",
    display_name="删除文件",
    description="【文件】删除指定文件或空目录。安全删除，非永久擦除",
    category=F.SYSTEM,
    params=[
        P("path", "str", "要删除的文件或空目录路径", example="C:/Users/temp.txt"),
    ],
    returns="dict{success}",
    tags=["file", "delete"],
)
def file_delete(path: str) -> dict:
    try:
        p = Path(path)
        if not p.exists():
            return {"success": False, "error": f"路径不存在: {path}"}
        if p.is_file():
            p.unlink()
        elif p.is_dir():
            try:
                p.rmdir()  # only empty dirs
            except OSError:
                return {"success": False, "error": "目录非空，请使用 file_remove_recursive"}
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


@feature(
    name="file_list",
    display_name="列出目录",
    description="【文件】列出目录中的文件和子目录。用于浏览文件系统、查找文件",
    category=F.SYSTEM,
    params=[
        P("path", "str", "目录路径", example="C:/Users"),
        P("pattern", "str", "通配符过滤（如 *.txt）", required=False, default=""),
        P("recursive", "boolean", "是否递归子目录", required=False, default=False),
    ],
    returns="dict{success, files: list[{name, path, is_dir, size}], total}",
    tags=["file", "list", "directory"],
)
def file_list(path: str, pattern: str = "", recursive: bool = False) -> dict:
    try:
        p = Path(path)
        if not p.exists():
            return {"success": False, "error": f"目录不存在: {path}"}
        if not p.is_dir():
            return {"success": False, "error": f"路径不是目录: {path}"}

        if recursive:
            items = p.rglob(pattern) if pattern else p.rglob("*")
        else:
            items = p.glob(pattern) if pattern else p.glob("*")

        files = []
        for item in sorted(items):
            try:
                files.append({
                    "name": item.name,
                    "path": str(item),
                    "is_dir": item.is_dir(),
                    "size": item.stat().st_size if item.is_file() else 0,
                })
            except OSError:
                continue

        return {"success": True, "files": files, "total": len(files)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@feature(
    name="file_exists",
    display_name="检查文件存在",
    description="【文件】检查文件或目录是否存在。用于判断前置条件或确认输出",
    category=F.SYSTEM,
    params=[
        P("path", "str", "路径", example="C:/Users/test.txt"),
    ],
    returns="dict{success, exists, is_dir}",
    tags=["file", "exists"],
)
def file_exists(path: str) -> dict:
    try:
        p = Path(path)
        return {"success": True, "exists": p.exists(), "is_dir": p.is_dir()}
    except Exception as e:
        return {"success": False, "error": str(e)}


@feature(
    name="file_copy",
    display_name="复制文件",
    description="【文件】复制文件到目标路径。用于备份、部署",
    category=F.SYSTEM,
    params=[
        P("src", "str", "源路径", example="C:/Users/source.txt"),
        P("dst", "str", "目标路径", example="C:/Users/backup.txt"),
    ],
    returns="dict{success}",
    tags=["file", "copy"],
)
def file_copy(src: str, dst: str) -> dict:
    try:
        shutil.copy2(src, dst)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


@feature(
    name="file_move",
    display_name="移动文件",
    description="【文件】移动或重命名文件/目录",
    category=F.SYSTEM,
    params=[
        P("src", "str", "源路径", example="C:/Users/old.txt"),
        P("dst", "str", "目标路径", example="C:/Users/new.txt"),
    ],
    returns="dict{success}",
    tags=["file", "move"],
)
def file_move(src: str, dst: str) -> dict:
    try:
        shutil.move(src, dst)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


@feature(
    name="file_edit",
    display_name="精确编辑文件",
    description="【文件】在文件中精确查找一段内容并替换为新的内容。用于修改代码、配置文件等，比 file_write 更安全——只改匹配的部分，不影响文件其余内容",
    category=F.SYSTEM,
    params=[
        P("path", "str", "文件路径", example="C:/Users/main.py"),
        P("search", "str", "要查找的原始内容（必须精确匹配，唯一出现）", example="print('hello')"),
        P("replace", "str", "替换后的新内容", example="print('hello world')"),
        P("encoding", "str", "文件编码", required=False, default="utf-8"),
        P("dry_run", "boolean", "仅预览 diff，不实际修改", required=False, default=False),
    ],
    returns="dict{success, diff, changes} — 包含 SEARCH/REPLACE 的 diff 预览",
    tags=["file", "edit", "patch"],
)
def file_edit(path: str, search: str, replace: str, encoding: str = "utf-8", dry_run: bool = False) -> dict:
    import difflib
    try:
        p = Path(path)
        if not p.exists():
            return {"success": False, "error": f"文件不存在: {path}"}
        if not p.is_file():
            return {"success": False, "error": f"路径不是文件: {path}"}

        content = p.read_text(encoding=encoding)

        # Count occurrences
        count = content.count(search)
        if count == 0:
            return {"success": False, "error": f"未在文件中找到匹配内容（search 字符串不匹配）"}
        if count > 1:
            return {"success": False, "error": f"找到 {count} 处匹配，编辑不明确。请提供更多上下文使得 search 唯一匹配"}

        new_content = content.replace(search, replace, 1)

        # Generate diff
        old_lines = content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        diff = "".join(difflib.unified_diff(
            old_lines, new_lines,
            fromfile=str(p), tofile=str(p),
            n=3,
        ))

        if not dry_run:
            p.write_text(new_content, encoding=encoding)
            return {"success": True, "diff": diff, "changes": len([l for l in diff.splitlines() if l.startswith("+") or l.startswith("-")]) - 2}
        else:
            return {"success": True, "diff": diff, "dry_run": True, "changes": len([l for l in diff.splitlines() if l.startswith("+") or l.startswith("-")]) - 2}

    except Exception as e:
        return {"success": False, "error": str(e)}
