#!/usr/bin/env python3
"""
调度器守护进程 — 持久化 Python 进程
管理 schedules.json 中的调度规则，评估触发器 + 守卫条件，触发模板执行。

协议（stdin/stdout JSON-line）:
  → init {type: "init", config: {schedules_path, poll_interval}}
  → reload {type: "reload", schedules?: [...]}  # schedules 为 None 时从文件重新读取
  → ping {type: "ping"}
  → shutdown {type: "shutdown"}

  ← ready {type: "ready", pid: 12345}
  ← fire {type: "fire", schedule_id, schedule, timestamp}
  ← pong {type: "pong"}
  ← log {type: "log", level, message}
  ← error {type: "error", message}
"""
import json, sys, os, time, threading, logging, uuid, concurrent.futures
from datetime import datetime, timedelta
from typing import Optional

# 强制 UTF-8
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stdin, 'reconfigure'):
    sys.stdin.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.condition_evaluator import evaluate

logging.basicConfig(level=logging.WARNING, format='%(name)s %(message)s')
logger = logging.getLogger("scheduler")


class SchedulerDaemon:
    def __init__(self):
        self._schedules: list[dict] = []
        self._schedules_path = ""
        self._poll_interval = 15
        self._running = False
        self._tick_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._cron_cache: dict[str, datetime] = {}  # dedup
        self._pending_confirm: dict[str, dict] = {}  # confirm_id → info
        self._confirm_lock = threading.Lock()
        self._condition_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    def send(self, msg: dict):
        try:
            line = json.dumps(msg, ensure_ascii=False)
            sys.stdout.write(line + '\n')
            sys.stdout.flush()
        except BrokenPipeError:
            self._running = False

    def handle_init(self, config: dict):
        self._poll_interval = config.get("poll_interval", 15)
        sp = config.get("schedules_path", "")
        if sp:
            self._schedules_path = sp
            self._load_schedules()
        self.send({"type": "ready", "pid": os.getpid()})
        self.start()

    def handle_reload(self, schedules: list = None):
        if schedules is not None:
            with self._lock:
                self._schedules = schedules
        else:
            self._load_schedules()
        self.send({"type": "reloaded", "count": len(self._schedules)})

    def _load_schedules(self):
        if not self._schedules_path or not os.path.exists(self._schedules_path):
            self._schedules = []
            return
        try:
            with open(self._schedules_path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            self._schedules = raw if isinstance(raw, list) else []
        except Exception as e:
            self.send({"type": "error", "message": f"读取 schedules 失败: {e}"})
            self._schedules = []

    def start(self):
        if self._running:
            return
        self._running = True
        self._cron_cache.clear()
        self._tick_thread = threading.Thread(target=self._tick_loop, daemon=True)
        self._tick_thread.start()
        self.send({"type": "log", "level": "info",
                    "message": f"调度器已启动 (间隔={self._poll_interval}s, {len(self._schedules)} 个调度)"})

    def stop(self):
        self._running = False

    def _tick_loop(self):
        while self._running:
            try:
                self._cleanup_stale_confirmations()
                self._cleanup_cron_cache()
                with self._lock:
                    schedules = list(self._schedules)
                now = datetime.now()
                for sched in schedules:
                    if not sched.get("enabled", False):
                        continue
                    if self._is_triggered(sched, now):
                        try:
                            future = self._condition_executor.submit(self._check_conditions, sched)
                            cond_ok = future.result(timeout=30)
                        except concurrent.futures.TimeoutError:
                            self.send({"type": "log", "level": "warning",
                                        "message": f"调度 {sched.get('id')} 条件评估超时(>30s)，已跳过"})
                            cond_ok = False
                        except Exception as ce:
                            self.send({"type": "log", "level": "error",
                                        "message": f"调度 {sched.get('id')} 条件评估异常: {ce}"})
                            cond_ok = False
                        if cond_ok:
                            self._handle_trigger(sched, now)
                time.sleep(self._poll_interval)
            except Exception as e:
                self.send({"type": "error", "message": f"tick error: {e}"})
                time.sleep(5)

    def _handle_trigger(self, sched: dict, now: datetime):
        risk = sched.get("risk_level", "info")
        if risk in ("confirm", "critical"):
            self._request_confirm(sched, now)
        else:
            self._do_fire(sched, now)

    def _do_fire(self, sched: dict, now: datetime):
        self.send({
            "type": "fire",
            "schedule_id": sched["id"],
            "schedule": sched,
            "timestamp": now.isoformat(),
        })

    def _request_confirm(self, sched: dict, now: datetime):
        confirm_id = f"cf_{sched['id']}_{int(time.time())}"
        event = threading.Event()
        with self._confirm_lock:
            self._pending_confirm[confirm_id] = {
                "schedule_id": sched["id"],
                "schedule": sched,
                "timestamp": now.isoformat(),
                "event": event,
                "result": None,
            }

        self.send({
            "type": "pre_fire",
            "confirm_id": confirm_id,
            "schedule_id": sched["id"],
            "schedule": sched,
            "risk_level": sched.get("risk_level", "info"),
            "timestamp": now.isoformat(),
        })

        # Background thread waits for user response
        t = threading.Thread(target=self._wait_confirm, args=(confirm_id,), daemon=True)
        t.start()

    def _wait_confirm(self, confirm_id: str):
        with self._confirm_lock:
            info = self._pending_confirm.get(confirm_id)
            if not info:
                return
            event = info["event"]

        confirmed = event.wait(timeout=60)

        with self._confirm_lock:
            info = self._pending_confirm.pop(confirm_id, None)
            if not info:
                return
            if confirmed and info.get("result") == "proceed":
                sched = info["schedule"]
                ts = info["timestamp"]
                now = datetime.fromisoformat(ts) if isinstance(ts, str) else ts
                self._do_fire(sched, now)
            elif confirmed and info.get("result") == "cancel":
                self.send({"type": "log", "level": "info",
                           "message": f"调度 {info['schedule_id']} 已被用户取消"})
            else:
                self.send({"type": "log", "level": "info",
                           "message": f"调度 {info['schedule_id']} 确认超时，已跳过"})

    def _cleanup_stale_confirmations(self):
        now = datetime.now()
        stale = []
        with self._confirm_lock:
            for cid, info in list(self._pending_confirm.items()):
                ts = info["timestamp"]
                created = datetime.fromisoformat(ts) if isinstance(ts, str) else ts
                if (now - created).total_seconds() > 120:
                    stale.append(cid)
        for cid in stale:
            with self._confirm_lock:
                info = self._pending_confirm.pop(cid, None)
                if info:
                    info["event"].set()

    def _cleanup_cron_cache(self):
        """限制 cron cache 大小，防止无限增长"""
        if len(self._cron_cache) > 200:
            # 保留最近 100 条
            sorted_items = sorted(self._cron_cache.items(), key=lambda x: x[1])
            for k, _ in sorted_items[:-100]:
                del self._cron_cache[k]

    def handle_fire_response(self, msg: dict):
        confirm_id = msg.get("confirm_id", "")
        action = msg.get("action", "")
        with self._confirm_lock:
            info = self._pending_confirm.get(confirm_id)
            if info:
                info["result"] = action
                info["event"].set()
                self.send({"type": "log", "level": "info",
                           "message": f"调度 {info['schedule_id']}: 用户已{action}"})

    def _is_triggered(self, sched: dict, now: datetime) -> bool:
        triggers = sched.get("triggers", [])
        if not triggers:
            return False
        for t in triggers:
            ttype = t.get("type")
            if ttype == "cron":
                expr = t.get("cron_expr", "")
                if not expr:
                    continue
                if self._match_cron(expr, now):
                    key = f"{sched['id']}:cron:{expr}"
                    last = self._cron_cache.get(key)
                    if last and (now - last).total_seconds() < 59:
                        continue
                    self._cron_cache[key] = now
                    return True
            elif ttype == "interval":
                interval = t.get("interval_seconds", 300)
                key = f"{sched['id']}:interval"
                last = self._cron_cache.get(key)
                if last and (now - last).total_seconds() < interval:
                    continue
                self._cron_cache[key] = now
                return True
            elif ttype == "startup":
                # startup 由 Electron 的 runStartupSchedules 处理
                continue
            elif ttype == "event":
                # 事件型触发目前由 TriggerEngine 独立监控
                # 这里只做轻量状态检查（进程/窗口存在性由条件系统处理）
                continue
        return False

    def _check_conditions(self, sched: dict) -> bool:
        cond_group = sched.get("conditions")
        if not cond_group:
            return True
        rules = cond_group.get("rules", [])
        operator = cond_group.get("operator", "AND")
        return evaluate(rules, operator)

    @staticmethod
    def _match_cron(expr: str, now: datetime) -> bool:
        parts = expr.strip().split()
        if len(parts) != 5:
            return False
        minute, hour, day, month, week = parts
        return (
            SchedulerDaemon._cron_match(now.minute, minute, 0, 59)
            and SchedulerDaemon._cron_match(now.hour, hour, 0, 23)
            and SchedulerDaemon._cron_match(now.day, day, 1, 31)
            and SchedulerDaemon._cron_match(now.month, month, 1, 12)
            and SchedulerDaemon._cron_match((now.weekday() + 1) % 7, week, 0, 6)
        )

    @staticmethod
    def _cron_match(value: int, pattern: str, lo: int, hi: int) -> bool:
        if pattern == '*':
            return True
        if ',' in pattern:
            return any(SchedulerDaemon._cron_match(value, p.strip(), lo, hi)
                       for p in pattern.split(','))
        if '/' in pattern:
            if '-' in pattern:
                base, step = pattern.rsplit('/', 1)
                start, end = base.split('-')
                start, end = int(start), int(end)
            else:
                base, step = pattern.split('/')
                start = int(base) if base != '*' else lo
                end = hi
            step = int(step)
            if step == 0:
                return False
            return lo <= value <= hi and value >= start and (value - start) % step == 0
        if '-' in pattern:
            a, b = pattern.split('-')
            return int(a) <= value <= int(b)
        return int(pattern) == value


# ── 主事件循环 ────────────────────────────────────────────

def main():
    daemon = SchedulerDaemon()
    daemon.send({"type": "log", "level": "info", "message": "调度器守护进程已启动"})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            daemon.send({"type": "error", "message": f"JSON 解析失败: {line[:80]}"})
            continue

        mt = msg.get("type", "")
        if mt == "init":
            daemon.handle_init(msg.get("config", {}))
        elif mt == "reload":
            daemon.handle_reload(msg.get("schedules"))
        elif mt == "ping":
            daemon.send({"type": "pong"})
        elif mt == "fire_response":
            daemon.handle_fire_response(msg)
        elif mt == "shutdown":
            daemon.stop()
            break
        else:
            daemon.send({"type": "error", "message": f"未知消息: {mt}"})

    daemon.stop()
    daemon.send({"type": "log", "level": "info", "message": "调度器守护进程已退出"})
    sys.exit(0)


if __name__ == '__main__':
    main()
