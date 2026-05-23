"""
程序别名注册表 — 持久化程序名→完整路径的映射。
自动发现已安装软件，来源：
1. Windows Registry App Paths (HKLM/HKCU)
2. PATH 环境变量（通过 shutil.which / where.exe）
3. 开始菜单快捷方式
4. 用户自定义别名覆盖

用法:
    from features.program_registry import resolve_program
    path = resolve_program("qq")  # → "C:\\Program Files\\Tencent\\QQ\\Bin\\QQ.exe"
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 数据目录 ───────────────────────────────────────────────────
_DATA_DIR = Path.home() / ".lingdong"
_CACHE_PATH = _DATA_DIR / "program_registry.json"
_SCAN_INTERVAL = 86400  # 24 小时后重新扫描

# ── 扫描源配置 ─────────────────────────────────────────────────
_START_MENU_DIRS = [
    Path(os.environ.get("ProgramData", "C:\\ProgramData"))
    / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    Path(os.environ.get("APPDATA", ""))
    / "Microsoft" / "Windows" / "Start Menu" / "Programs",
]

_DESKTOP_DIRS = [
    Path(os.environ.get("USERPROFILE", "")) / "Desktop",
    Path(os.environ.get("PUBLIC", "C:\\Users\\Public")) / "Desktop",
]

_LOCALAPPDATA_PROGRAMS = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
]

_COMMON_APP_DIRS = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Discord",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Spotify",
    Path(os.environ.get("APPDATA", "")) / "Obsidian",
    Path(os.environ.get("LOCALAPPDATA", "")) / "slack",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Postman",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Teams",
    # ── 腾讯系（QQ、QQNT、微信等经常安装在 LOCALAPPDATA）──
    Path(os.environ.get("LOCALAPPDATA", "")) / "Tencent",
    Path(os.environ.get("APPDATA", "")) / "Tencent",
]

_PROGRAM_FILES_DIRS = [
    Path(os.environ.get("ProgramFiles", "C:\\Program Files")),
    Path(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")),
]

# ── 中文别名映射表 ─────────────────────────────────────────────
# key = 扫描到的 exe 名 (小写), value = [中文别名列表]
# 自动合并到注册表中
_ZH_ALIAS_MAP: dict[str, list[str]] = {
    # ── 系统工具 ──
    "notepad":         ["记事本"],
    "notepad++":       ["Notepad++"],
    "calc":            ["计算器"],
    "mspaint":         ["画图", "画图工具"],
    "paint":           ["画图"],
    "cmd":             ["命令提示符", "命令行", "cmd命令行"],
    "powershell":      ["PowerShell", "终端"],
    "explorer":        ["资源管理器", "文件管理器"],
    "regedit":         ["注册表编辑器", "注册表"],
    "taskmgr":         ["任务管理器"],
    "control":         ["控制面板"],
    "msconfig":        ["系统配置"],
    "snippingtool":    ["截图工具", "截图"],
    "snipping":        ["截图"],
    "magnify":         ["放大镜"],
    "osk":             ["屏幕键盘", "虚拟键盘"],
    "charmap":         ["字符映射表"],
    "cleanmgr":        ["磁盘清理"],
    "dfrgui":          ["磁盘碎片整理"],
    "resmon":          ["资源监视器"],
    "perfmon":         ["性能监视器"],

    # ── 浏览器 ──
    "chrome":          ["谷歌浏览器", "Chrome浏览器", "浏览器"],
    "msedge":          ["Edge浏览器", "微软浏览器", "浏览器"],
    "firefox":         ["火狐浏览器", "Firefox浏览器"],
    "opera":           ["Opera浏览器"],
    "brave":           ["Brave浏览器"],

    # ── Office ──
    "winword":         ["Word", "微软Word", "word文档"],
    "word":            ["Word"],
    "excel":           ["Excel", "微软Excel"],
    "powerpnt":        ["PowerPoint", "PPT", "幻灯片"],
    "msaccess":        ["Access", "数据库"],
    "outlook":         ["Outlook", "微软邮箱"],
    "msoobe":          ["Office激活"],
    "onenote":         ["OneNote", "笔记"],
    "onenotem":        ["OneNote"],
    "winproj":         ["Project"],
    "visio":           ["Visio"],

    # ── 开发工具 ──
    "code":            ["VS Code", "Visual Studio Code", "VSCode"],
    "cursor":          ["Cursor编辑器"],
    "windsurf":        ["Windsurf"],
    "studio64":        ["Android Studio"],
    "android studio":  ["安卓工作室"],
    "idea64":          ["IntelliJ IDEA"],
    "idea":            ["IntelliJ"],
    "pycharm64":       ["PyCharm"],
    "pycharm":         ["Python编辑器"],
    "clion64":         ["CLion"],
    "clion":           ["CLion"],
    "webstorm64":      ["WebStorm"],
    "webstorm":        ["WebStorm"],
    "goland64":        ["GoLand"],
    "goland":          ["GoLand"],
    "rider64":         ["Rider"],
    "rider":           ["Rider"],
    "datagrip64":      ["DataGrip"],
    "datagrip":        ["DataGrip"],
    "phpstorm64":      ["PhpStorm"],
    "phpstorm":        ["PhpStorm"],
    "rubymine64":      ["RubyMine"],
    "rubymine":        ["RubyMine"],
    "sublime_text":    ["Sublime Text", "Sublime"],
    "sublime":         ["Sublime"],
    "atom":            ["Atom编辑器"],
    "bash":            ["Git Bash", "Bash终端"],
    "git-bash":        ["Git Bash"],
    "git":             ["Git命令行", "git工具"],
    "node":            ["Node.js", "node环境"],
    "npm":             ["npm"],
    "python":          ["Python环境", "python解释器"],
    "cmder":           ["Cmder终端"],
    "putty":           ["Putty", "SSH客户端"],
    "windterm":        ["WindTerm"],
    "winscp":          ["WinSCP"],
    "filezilla":       ["FileZilla"],
    "heidisql":        ["HeidiSQL"],
    "dbeaver":         ["DBeaver"],
    "navicat":         ["Navicat"],
    "postman":         ["Postman"],
    "insomnia":        ["Insomnia"],
    "docker desktop":  ["Docker"],
    "docker":          ["Docker"],

    # ── 通讯工具 ──
    "qq":              ["QQ", "腾讯QQ"],
    "qqnt":            ["QQ"],
    "tim":             ["TIM", "腾讯TIM"],
    "wechat":          ["微信"],
    "weixin":          ["微信"],
    "wxwork":          ["企业微信"],
    "wecom":           ["企业微信"],
    "dingtalk":        ["钉钉"],
    "feishu":          ["飞书", "Lark"],
    "lark":            ["飞书"],
    "discord":         ["Discord"],
    "telegram":        ["Telegram", "电报"],
    "slack":           ["Slack"],
    "whatsapp":        ["WhatsApp"],

    # ── 影音娱乐 ──
    "potplayer":       ["PotPlayer"],
    "potplayermini64": ["PotPlayer"],
    "vlc":             ["VLC播放器", "VLC"],
    "wmplayer":        ["Windows Media Player", "媒体播放器"],
    "mplayer":         ["媒体播放器"],
    "spotify":         ["Spotify"],
    "neteasemusic":    ["网易云音乐"],
    "cloudmusic":      ["网易云音乐"],
    "baidunetdisk":    ["百度网盘"],
    "panbaidu":        ["百度网盘"],
    "thunder":         ["迅雷"],
    "xunlei":          ["迅雷"],
    "thunderbird":     ["Thunderbird"],
    "foobar2000":      ["Foobar2000"],
    "obs":             ["OBS Studio", "直播工具"],

    # ── 游戏 ──
    "steam":           ["Steam"],
    "steamchina":      ["蒸汽平台"],
    "epic games launcher": ["Epic启动器", "Epic"],
    "epic":            ["Epic"],
    "eadekstop":       ["EA Desktop", "EA"],
    "ea":              ["EA"],
    "ubisoftconnect":  ["育碧"],
    "battlenet":       ["战网"],
    "leagueclient":    ["英雄联盟", "LOL"],
    "lolclient":       ["英雄联盟"],
    "genshin impact":  ["原神"],
    "genshin":         ["原神"],
    "yuanshen":        ["原神"],
    "wow":             ["魔兽世界"],
    "wow-64":          ["魔兽世界"],
    "wowclassic":      ["魔兽世界怀旧服"],
    "blizzard":        ["暴雪"],
    "valorant":        ["Valorant", "无畏契约"],
    "valorant-win64":  ["Valorant", "无畏契约"],
    "cs2":             ["CS2"],
    "csgo":            ["CSGO"],
    "dota2":           ["Dota2"],
    "minecraft":       ["我的世界"],
    "minecraftlauncher": ["我的世界"],
    "starrail":        ["崩坏星穹铁道", "星穹铁道"],
    "hkrpg":           ["崩坏星穹铁道"],
    "honkai impact 3": ["崩坏3"],
    "honkai impact 3rd": ["崩坏3"],
    "zenless zone zero": ["绝区零", "ZZZ"],
    "launcher":        ["游戏启动器"],

    # ── 图像设计 ──
    "photoshop":       ["Photoshop", "PS"],
    "photoshop":       ["Photoshop"],
    "photosh":         ["Photoshop"],
    "psexe":           ["Photoshop"],
    "illustrator":     ["Illustrator", "AI"],
    "coreldraw":       ["CorelDRAW", "CDR"],
    "autocad":         ["AutoCAD", "CAD"],
    "acad":            ["AutoCAD"],
    "3dsmax":          ["3ds Max", "3DMAX"],
    "maya":            ["Maya"],
    "blender":         ["Blender"],
    "cinema 4d":       ["C4D"],
    "c4d":             ["C4D"],
    "sketchup":        ["SketchUp"],
    "premiere":        ["Premiere", "PR"],
    "premiere pro":    ["Premiere Pro"],
    "afterfx":         ["After Effects", "AE"],
    "after effects":   ["After Effects"],
    "ae":              ["After Effects"],
    "davinci resolve": ["达芬奇"],
    "resolve":         ["达芬奇"],
    "final cut pro":   ["Final Cut Pro"],
    "lightroom":       ["Lightroom"],
    "lightroomclassic": ["Lightroom Classic"],
    "capture one":     ["Capture One"],
    "snipaste":        ["Snipaste", "截图工具"],
    "sharex":          ["ShareX"],
    "greenshot":       ["Greenshot"],

    # ── 工具软件 ──
    "7z":              ["7-Zip"],
    "7-zip":           ["7-Zip"],
    "7zfm":            ["7-Zip", "压缩"],
    "winrar":          ["WinRAR"],
    "rar":             ["WinRAR"],
    "bandizip":        ["Bandizip", "压缩"],
    "everything":      ["Everything", "文件搜索"],
    "listary":         ["Listary"],
    "wox":             ["Wox", "启动器"],
    "flow launcher":   ["Flow Launcher"],
    "utools":          ["uTools", "启动器"],
    "obsidian":        ["Obsidian", "笔记"],
    "typora":          ["Typora", "Markdown编辑器"],
    "notion":          ["Notion"],
    "evernote":        ["Evernote", "印象笔记"],
    "joplin":          ["Joplin"],
    "foxit":           ["Foxit Reader", "PDF阅读器"],
    "foxitreader":     ["Foxit Reader"],
    "acrobat":         ["Adobe Acrobat", "Acrobat"],
    "acrord32":        ["Adobe Reader"],
    "sumatra":         ["Sumatra PDF"],
    "wps":             ["WPS Office", "WPS"],
    "wps office":      ["WPS Office"],
    "et":              ["WPS表格"],
    "wpp":             ["WPS演示"],
    "wps文字":         ["WPS文字"],
    "clash verge":     ["Clash Verge", "代理"],
    "clash-verge":     ["Clash Verge"],
    "v2ray":           ["V2Ray"],
    "ss":              ["Shadowsocks"],
    "qv2ray":          ["Qv2ray"],
    "netch":           ["Netch"],

    # ── 硬件工具 ──
    "msi afterburner":      ["微星小飞机", "小飞机", "Afterburner"],
    "msiafterburner":       ["微星小飞机", "小飞机"],
    "rivatuner statistics server": ["RivaTuner", "RTSS"],
    "rtss":                 ["RivaTuner"],
    "hwinfo":               ["HWiNFO"],
    "hwinfo64":             ["HWiNFO"],
    "cpuid":                ["CPU-Z"],
    "cpuz":                 ["CPU-Z"],
    "gpuz":                 ["GPU-Z"],
    "aida64":               ["AIDA64"],
    "crystaldiskinfo":      ["CrystalDiskInfo"],
    "crystaldiskmark":      ["CrystalDiskMark"],
    "crystaldiskinfo8":     ["CrystalDiskInfo"],
    "speccy":               ["Speccy"],

    # ── 录屏直播 ──
    "obs64":            ["OBS Studio", "OBS", "直播"],
    "obs studio":       ["OBS Studio"],
    "bandicam":         ["Bandicam"],
    "fraps":            ["Fraps"],
    "share":            ["Xbox录屏", "录屏"],
    "gamingoverlay":    ["游戏工具栏"],

    # ── 腾讯系 ──
    "tencent meeting":  ["腾讯会议"],
    "wemeet":           ["腾讯会议"],
    "tencent video":    ["腾讯视频"],
    "wecloud":          ["腾讯微云"],
    "weiyun":           ["微云"],
    "wechatdevtools":   ["微信开发者工具"],
    "qqmusic":          ["QQ音乐"],
    "qqbrowser":        ["QQ浏览器"],
    "qqpcmgr":          ["腾讯电脑管家"],

    # ── 百度系 ──
    "baidunetdisk":     ["百度网盘"],
    "baidupinyin":      ["百度输入法"],
    "baidu输入法":      ["百度输入法"],

    # ── 输入法 ──
    "sogoucloud":       ["搜狗输入法"],
    "sogouinput":       ["搜狗输入法"],
    "google输入法":     ["谷歌输入法"],
    "googlepinyin":     ["谷歌拼音"],

    # ── 虚拟机 ──
    "vmware":           ["VMware", "虚拟机"],
    "vmplayer":         ["VMware Player"],
    "vmware workstation": ["VMware"],
    "virtualbox":       ["VirtualBox"],
    "vbox":             ["VirtualBox"],
    "docker desktop":   ["Docker Desktop"],
    "docker":           ["Docker"],

    # ── AI 工具 ──
    "ollama":           ["Ollama"],
    "ollama app":       ["Ollama"],
    "lmstudio":         ["LM Studio"],
    "text generation webui": ["Text Generation WebUI", "Oobabooga"],
    "comfyui":          ["ComfyUI"],
    "stable diffusion": ["Stable Diffusion"],
    "sdcn":             ["Stable Diffusion"],
    "fooocus":          ["Fooocus"],
}

# 反向映射：中文别名 → 最可能的英文 exe 名
# 用于 resolve() 输入中文别名时能找到程序
_ZH_REVERSE_MAP: dict[str, list[str]] = {}
for en_name, zh_names in _ZH_ALIAS_MAP.items():
    for zh in zh_names:
        key = zh.lower().strip()
        if key not in _ZH_REVERSE_MAP:
            _ZH_REVERSE_MAP[key] = []
        if en_name not in _ZH_REVERSE_MAP[key]:
            _ZH_REVERSE_MAP[key].append(en_name)


class ProgramRegistry:
    """程序注册表 — 单例，线程安全。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._aliases: dict[str, list[str]] = {}  # name → [possible paths]
        self._last_scan: float = 0
        self._loaded = False

    # ── 公开 API ──────────────────────────────────────────────

    def resolve(self, name: str) -> str | None:
        """解析程序名到完整路径。支持中文别名。"""
        if not name:
            return None
        low = name.lower().strip()

        # 如果已经是完整路径/带扩展名的文件，直接返回
        if os.path.isfile(name):
            return name

        # 1. 检查注册表别名（含英文名）
        with self._lock:
            if low in self._aliases:
                candidates = self._aliases[low]
                for p in candidates:
                    if os.path.isfile(p):
                        return p

        # 2. 中文别名 → 转英文 exe 名再查
        # 先试原样，再试去掉 .exe 后缀（兼容 "微信.exe" 这类输入）
        for lookup in (low, low[:-4] if low.endswith(".exe") else None):
            if lookup and lookup in _ZH_REVERSE_MAP:
                for en_name in _ZH_REVERSE_MAP[lookup]:
                    en_low = en_name.lower()
                    with self._lock:
                        if en_low in self._aliases:
                            for p in self._aliases[en_low]:
                                if os.path.isfile(p):
                                    return p
                    import shutil
                    exe_path = en_name if en_name.endswith(".exe") else en_name + ".exe"
                    which = shutil.which(exe_path)
                    if which:
                        return which
                break  # 找到了中文映射就不继续了

        # 3. 尝试直接带 .exe 后缀查找
        if not low.endswith(".exe"):
            return self.resolve(name + ".exe")

        # 4. shutil.which 回退
        import shutil
        which = shutil.which(name)
        if which:
            return which

        return None

    def resolve_launch(self, name: str) -> str:
        """解析程序名，返回适合 subprocess.Popen 的路径或原始值。"""
        resolved = self.resolve(name)
        return resolved or name

    def get_all(self) -> dict[str, list[str]]:
        """返回完整别名映射的副本。"""
        with self._lock:
            return dict(self._aliases)

    def get_name_list(self) -> list[dict]:
        """返回别名列表，用于前端展示。"""
        with self._lock:
            result = []
            for name, paths in sorted(self._aliases.items()):
                valid = [p for p in paths if os.path.isfile(p)]
                if valid:
                    result.append({"name": name, "paths": valid, "path": valid[0]})
            return result

    def add_alias(self, name: str, path: str) -> None:
        """添加自定义别名。"""
        with self._lock:
            self._aliases[name.lower().strip()] = [path]
            self._save_cache()

    def remove_alias(self, name: str) -> bool:
        """删除自定义别名。"""
        with self._lock:
            if name.lower().strip() in self._aliases:
                del self._aliases[name.lower().strip()]
                self._save_cache()
                return True
            return False

    def refresh(self, background: bool = False) -> dict:
        """刷新扫描。background=True 时在后台线程执行。"""
        if background:
            thread = threading.Thread(target=self._do_scan, daemon=True)
            thread.start()
            return {"status": "started", "message": "后台扫描已启动"}
        return self._do_scan()

    def ensure_loaded(self) -> None:
        """确保缓存已加载（模块导入时自动调用）。"""
        if not self._loaded:
            self._load_cache()
            # 如果缓存过期，后台刷新
            if time.time() - self._last_scan > _SCAN_INTERVAL:
                self.refresh(background=True)

    # ── 内部实现 ──────────────────────────────────────────────

    def _load_cache(self) -> None:
        """从 JSON 加载缓存。版本不匹配时丢弃并触发重新扫描。"""
        try:
            if _CACHE_PATH.exists():
                data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
                cache_ver = data.get("version", 0)
                if cache_ver < 3:
                    logger.info("缓存版本过时 (%d < 3)，触发重新扫描", cache_ver)
                    self._loaded = True
                    self.refresh(background=True)
                    return
                self._aliases = {k.lower(): v for k, v in data.get("aliases", {}).items()}
                self._last_scan = data.get("last_scan", 0)
                logger.info("已加载程序注册表: %d 个别名", len(self._aliases))
        except Exception as e:
            logger.warning("加载程序注册表缓存失败: %s", e)
        self._loaded = True

    def _save_cache(self) -> None:
        """保存缓存到 JSON。"""
        try:
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "version": 3,
                "last_scan": self._last_scan,
                "aliases": self._aliases,
            }
            _CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("保存程序注册表缓存失败: %s", e)

    def _do_scan(self) -> dict:
        """执行完整扫描。返回统计。"""
        logger.info("开始扫描已安装程序...")
        start = time.time()
        merged: dict[str, list[str]] = {}

        # 保留用户自定义别名
        for k, v in self._aliases.items():
            if k.startswith("__custom__"):
                merged[k] = v

        scanned = {
            "registry_app_paths": 0,
            "path_exes": 0,
            "start_menu": 0,
            "start_menu_lnk": 0,
            "desktop": 0,
            "program_files": 0,
            "localappdata_programs": 0,
            "common_app_dirs": 0,
        }

        try:
            # 1. Registry App Paths
            n = self._scan_registry_app_paths(merged)
            scanned["registry_app_paths"] = n
        except Exception as e:
            logger.debug("扫描注册表 App Paths 失败: %s", e)

        try:
            # 2. PATH 中的可执行文件
            n = self._scan_path_exes(merged)
            scanned["path_exes"] = n
        except Exception as e:
            logger.debug("扫描 PATH 失败: %s", e)

        try:
            # 3. 开始菜单快捷方式（含 .lnk 目标解析）
            n, n_lnk = self._scan_start_menu(merged)
            scanned["start_menu"] = n
            scanned["start_menu_lnk"] = n_lnk
        except Exception as e:
            logger.debug("扫描开始菜单失败: %s", e)

        try:
            # 4. 桌面快捷方式
            n = self._scan_desktop(merged)
            scanned["desktop"] = n
        except Exception as e:
            logger.debug("扫描桌面失败: %s", e)

        try:
            # 5. Program Files 常见入口
            n = self._scan_program_files(merged)
            scanned["program_files"] = n
        except Exception as e:
            logger.debug("扫描 Program Files 失败: %s", e)

        try:
            # 6. %LOCALAPPDATA%\Programs\* (现代应用: Discord, Obsidian 等)
            n = self._scan_localappdata_programs(merged)
            scanned["localappdata_programs"] = n
        except Exception as e:
            logger.debug("扫描 LOCALAPPDATA Programs 失败: %s", e)

        try:
            # 7. 常见特定应用目录 (Discord, Spotify, Obsidian, Slack 等)
            n = self._scan_common_app_dirs(merged)
            scanned["common_app_dirs"] = n
        except Exception as e:
            logger.debug("扫描常见应用目录失败: %s", e)

        # ── 合并中文别名 ──
        # 为扫描到的每个程序添加中文别名（如果 _ZH_ALIAS_MAP 中有对应）
        for en_name, zh_names in _ZH_ALIAS_MAP.items():
            if en_name in merged and any(os.path.isfile(p) for p in merged[en_name] if p):
                for zh in zh_names:
                    zh_low = zh.lower().strip()
                    if zh_low not in merged:
                        merged[zh_low] = []
                    for p in merged[en_name]:
                        if p and p not in merged[zh_low]:
                            merged[zh_low].append(p)

        # 过滤非可执行文件路径（.txt, .pdf, .url 等）
        _EXE_EXTS = (".exe", ".com", ".bat", ".cmd", ".msi", ".ps1", ".vbs")
        for name in list(merged.keys()):
            merged[name] = [p for p in merged[name] if p and any(p.lower().endswith(e) for e in _EXE_EXTS)]
            if not merged[name]:
                del merged[name]

        # 过滤不需要的别名：卸载xxx、发送至xxx、帮助xxx 等

        # 过滤不需要的别名：卸载xxx、发送至xxx、帮助xxx 等
        _SKIP_PREFIXES = ("uninstall", "卸载", "发送至", "help", "帮助", "readme", "faq", "文档")
        filtered = {}
        for name, paths in merged.items():
            if any(name.startswith(p) for p in _SKIP_PREFIXES):
                continue
            filtered[name] = paths
        merged = filtered

        # 去重：保留同名的最优路径（优先保留存在的）
        with self._lock:
            self._aliases = {}
            for name, paths in merged.items():
                deduped = list(dict.fromkeys(p for p in paths if p))  # 去重
                # 按存在性排序：存在的排前面
                deduped.sort(key=lambda p: (0 if os.path.isfile(p) else 1, p))
                self._aliases[name] = deduped[:5]  # 最多保留 5 条

            self._last_scan = time.time()
            self._save_cache()

        elapsed = time.time() - start
        scanned["total_aliases"] = len(self._aliases)
        scanned["elapsed_seconds"] = round(elapsed, 2)
        logger.info(
            "程序扫描完成: %d 个别名, %d AppPaths, %d PATH, %d 开始菜单, "
            "%d ProgramFiles, 耗时 %.1fs",
            scanned["total_aliases"],
            scanned["registry_app_paths"],
            scanned["path_exes"],
            scanned["start_menu"],
            scanned["program_files"],
            elapsed,
        )
        return {"status": "ok", "scanned": scanned}

    # ── 扫描源 ────────────────────────────────────────────────

    def _scan_registry_app_paths(self, merged: dict[str, list[str]]) -> int:
        """扫描 Windows 注册表 App Paths。

        HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths
        HKCU\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths
        """
        count = 0
        try:
            import winreg
        except ImportError:
            return 0

        roots = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        ]

        for hkey, key_path in roots:
            try:
                with winreg.OpenKey(hkey, key_path) as key:
                    i = 0
                    while True:
                        try:
                            sub_key_name = winreg.EnumKey(key, i)
                            i += 1
                        except OSError:
                            break

                        try:
                            with winreg.OpenKey(key, sub_key_name) as sub_key:
                                path_val, _ = winreg.QueryValueEx(sub_key, "")
                                if not path_val or not isinstance(path_val, str):
                                    continue
                                path_val = path_val.strip().strip('"')
                                if not path_val:
                                    continue

                                # 别名：去掉 .exe 后缀
                                name = sub_key_name.lower()
                                if name.endswith(".exe"):
                                    name = name[:-4]

                                if name not in merged:
                                    merged[name] = []
                                if path_val not in merged[name]:
                                    merged[name].append(path_val)
                                count += 1
                        except (OSError, FileNotFoundError):
                            continue
            except (OSError, FileNotFoundError):
                continue

        return count

    def _scan_path_exes(self, merged: dict[str, list[str]]) -> int:
        """扫描 PATH 中的 .exe 文件（跳过 Windows 系统目录，避免误收录）。"""
        count = 0
        path_dirs = os.environ.get("PATH", "").split(os.pathsep)
        # 跳过 Windows 系统目录，这些目录中的 exe 大多不是用户可启动的应用
        _SKIP_DIRS = {os.path.normpath(p).lower() for p in (
            os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32"),
            os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32", "Wbem"),
            os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32", "WindowsPowerShell", "v1.0"),
            os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "SysWOW64"),
            os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "Microsoft.NET"),
        )}

        for path_dir in path_dirs:
            if not path_dir or not os.path.isdir(path_dir):
                continue
            # 跳过系统目录
            if os.path.normpath(path_dir).lower() in _SKIP_DIRS:
                continue
            try:
                for fname in os.listdir(path_dir):
                    if fname.lower().endswith(".exe"):
                        name = fname[:-4].lower()
                        full = os.path.join(path_dir, fname)
                        if name not in merged:
                            merged[name] = []
                        if full not in merged[name]:
                            merged[name].append(full)
                            count += 1
            except (PermissionError, FileNotFoundError):
                continue

        return count

    def _resolve_lnk(self, lnk_path: str) -> str | None:
        """解析 .lnk 快捷方式的目标路径。"""
        try:
            import win32com.client
            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortCut(str(lnk_path))
            target = shortcut.TargetPath
            if target and os.path.isfile(target):
                return target
        except Exception:
            pass
        return None

    def _scan_start_menu(self, merged: dict[str, list[str]]) -> tuple[int, int]:
        """扫描开始菜单中的快捷方式，并解析 .lnk 目标路径。"""
        count = 0
        lnk_count = 0
        for start_dir in _START_MENU_DIRS:
            if not start_dir.exists():
                continue
            try:
                for lnk_file in start_dir.rglob("*.lnk"):
                    stem = lnk_file.stem.lower()
                    if stem in ("desktop", "desktop.ini", "default"):
                        continue
                    if stem not in merged:
                        merged[stem] = []
                    # 解析 .lnk 目标路径
                    target = self._resolve_lnk(lnk_file)
                    if target:
                        if target not in merged[stem]:
                            merged[stem].append(target)
                        lnk_count += 1
                    count += 1
            except (PermissionError, FileNotFoundError):
                continue
        return count, lnk_count

    def _scan_desktop(self, merged: dict[str, list[str]]) -> int:
        """扫描桌面快捷方式。"""
        count = 0
        for desk_dir in _DESKTOP_DIRS:
            if not desk_dir.exists():
                continue
            try:
                for lnk_file in desk_dir.glob("*.lnk"):
                    stem = lnk_file.stem.lower()
                    if stem in merged:
                        continue  # 开始菜单已收录
                    if stem not in merged:
                        merged[stem] = []
                    target = self._resolve_lnk(lnk_file)
                    if target:
                        if target not in merged[stem]:
                            merged[stem].append(target)
                        count += 1
            except (PermissionError, FileNotFoundError):
                continue
        return count

    def _scan_program_files(self, merged: dict[str, list[str]]) -> int:
        """扫描 Program Files 中常见的主可执行文件。

        递归深度限制为 4 层（vendor/sub/sub2/app.exe），
        平衡覆盖面和性能。可覆盖 QQ（Tencent/QQ/Bin/QQ.exe）等嵌套较深的程序。
        """
        count = 0
        MAX_DEPTH = 4  # vendor/sub/sub2/app.exe = 4 层（相对 Program Files）
        for pf_dir in _PROGRAM_FILES_DIRS:
            if not pf_dir.exists():
                continue
            try:
                for exe in pf_dir.rglob("*.exe"):
                    rel = exe.relative_to(pf_dir).parts
                    if len(rel) > MAX_DEPTH:
                        continue
                    exe_stem = exe.stem.lower()
                    if exe_stem not in merged:
                        merged[exe_stem] = [str(exe)]
                        count += 1
            except (PermissionError, FileNotFoundError):
                continue
        return count

    def _scan_localappdata_programs(self, merged: dict[str, list[str]]) -> int:
        """扫描 %LOCALAPPDATA%\\Programs\\* 下的可执行文件（Discord、Obsidian 等现代应用）。"""
        count = 0
        MAX_DEPTH = 4
        for prog_dir in _LOCALAPPDATA_PROGRAMS:
            if not prog_dir.exists():
                continue
            try:
                for exe in prog_dir.rglob("*.exe"):
                    rel = exe.relative_to(prog_dir).parts
                    if len(rel) > MAX_DEPTH:
                        continue
                    exe_stem = exe.stem.lower()
                    if exe_stem not in merged:
                        merged[exe_stem] = [str(exe)]
                        count += 1
            except (PermissionError, FileNotFoundError):
                continue
        return count

    def _scan_common_app_dirs(self, merged: dict[str, list[str]]) -> int:
        """扫描常见应用的特定安装目录。"""
        count = 0
        for app_dir in _COMMON_APP_DIRS:
            if not app_dir.exists():
                continue
            try:
                for exe in app_dir.rglob("*.exe"):
                    exe_stem = exe.stem.lower()
                    if exe_stem not in merged:
                        # 对于常见应用，用相对通用的名字
                        merged[exe_stem] = [str(exe)]
                        count += 1
            except (PermissionError, FileNotFoundError):
                continue
        return count


# ── 全局单例 ───────────────────────────────────────────────────
_registry = ProgramRegistry()


def resolve_program(name: str) -> str | None:
    """快捷方式：解析程序名到完整路径。"""
    return _registry.resolve(name)


def get_program_registry() -> ProgramRegistry:
    """获取注册表单例（确保已加载）。"""
    _registry.ensure_loaded()
    return _registry


# ── @feature 注册 ─────────────────────────────────────────────
from features.registry import feature, P, FeatureCategory as F


@feature(
    name="program_refresh",
    display_name="刷新程序注册表",
    description="【系统·注册表】扫描已安装程序，更新程序名→路径映射。用于让「启动」功能识别新安装的软件",
    category=F.SYSTEM,
    params=[
        P("background", "boolean", "是否后台扫描（不阻塞）", required=False, default=True),
    ],
    returns="dict{status, scanned?} - 扫描结果",
    tags=["system", "program", "registry"],
)
def program_refresh(background: bool = True) -> dict:
    """刷新程序注册表 — 扫描已安装程序。"""
    reg = get_program_registry()
    return reg.refresh(background=background)


@feature(
    name="program_resolve",
    display_name="解析程序路径",
    description="【系统·注册表】查询程序名对应的完整路径。例如 query='qq' → C:\\Program Files\\Tencent\\QQ\\Bin\\QQ.exe",
    category=F.SYSTEM,
    params=[
        P("query", "str", "程序名（不含路径和 .exe）", example="qq"),
    ],
    returns="dict{found, name, path, candidates}",
    tags=["system", "program", "registry"],
)
def program_resolve(query: str) -> dict:
    """查询程序路径。"""
    reg = get_program_registry()
    path = reg.resolve(query)
    all_info = reg.get_name_list()
    candidates = [p for p in all_info if query.lower() in p["name"]]
    return {
        "found": path is not None,
        "name": query,
        "path": path or "",
        "candidates": candidates[:20],
    }


@feature(
    name="program_list",
    display_name="列出程序注册表",
    description="【系统·注册表】列出所有已注册的程序别名和路径",
    category=F.SYSTEM,
    params=[
        P("query", "str", "可选筛选关键词", required=False, default=""),
    ],
    returns="list[dict{name, paths, path}] - 程序列表",
    tags=["system", "program", "registry"],
)
def program_list(query: str = "") -> list:
    """列出已注册的程序。"""
    reg = get_program_registry()
    all_items = reg.get_name_list()
    if query:
        q = query.lower()
        all_items = [it for it in all_items if q in it["name"]]
    return all_items[:100]


@feature(
    name="program_add_alias",
    display_name="添加程序别名",
    description="【系统·注册表】手动添加程序别名。用于自动扫描未覆盖的自定义程序",
    category=F.SYSTEM,
    params=[
        P("name", "str", "别名（如 myapp）", example="myapp"),
        P("path", "str", "完整路径（如 C:\\Tools\\myapp.exe）", example="C:\\Tools\\myapp.exe"),
    ],
    returns="dict{success}",
    tags=["system", "program", "registry"],
)
def program_add_alias(name: str, path: str) -> dict:
    """添加自定义别名。"""
    reg = get_program_registry()
    reg.add_alias(name, path)
    return {"success": True}


# 模块导入时确保缓存加载
_registry.ensure_loaded()
