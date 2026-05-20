#!/usr/bin/env python3
"""
调度器守护进程 — 基于 APScheduler
管理 schedules.json 中的调度规则，评估触发器 + 守卫条件，触发模板执行。

协议（stdin/stdout JSON-line）:
  → init {type: "init", config: {schedules_path, poll_interval}}
  → reload {type: "reload", schedules?: [...]}
  → ping {type: "ping"}
  → shutdown {type: "shutdown"}

  ← ready {type: "ready", pid: 12345}
  ← fire {type: "fire", schedule_id, schedule, timestamp}
  ← pong {type: "pong"}
  ← log {type: "log", level, message}
  ← error {type: "error", message}
"""
import json, sys, os, time, threading, logging, uuid
from datetime import datetime, timedelta
from typing import Optional, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

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
        self._running = False

        # APScheduler 替代自研线程轮询
        self._aps = BackgroundScheduler(
            executors={"default": {"type": "threadpool", "max_workers": 4}},
            job_defaults={"coalesce": True, "max_instances": 1},
        )

        self._lock = threading.Lock()
        self._job_map: dict[str, str] = {}  # schedule_id → aps_job_id
        self._pending_confirm: dict[str, dict] = {}
        self._confirm_lock = threading.Lock()

    # ── IPC ──────────────────────────────────────────────────────

    def send(self, msg: dict):
        try:
            line = json.dumps(msg, ensure_ascii=False)
            sys.stdout.write(line + '\n')
            sys.stdout.flush()
        except BrokenPipeError:
            self._running = False

    def handle_init(self, config: dict):
        sp = config.get("schedules_path", "")
        if sp:
            self._schedules_path = sp
            self._load_schedules()
        self.send({"type": "ready", "pid": os.getpid()})
        self._start()

    def handle_reload(self, schedules: list = None):
        if schedules is not None:
            with self._lock:
                self._schedules = schedules
        else:
            self._load_schedules()
        self._rebuild_jobs()
        self.send({"type": "reloaded", "count": len(self._schedules)})

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

    # ── 调度管理 ──────────────────────────────────────────────────

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

    def _start(self):
        if self._running:
            return
        self._running = True
        self._rebuild_jobs()
        self._aps.start()
        self.send({"type": "log", "level": "info",
                    "message": f"调度器已启动 (APScheduler, {len(self._schedules)} 个调度)"})

    def stop(self):
        self._running = False
        if self._aps.running:
            self._aps.shutdown(wait=False)

    def _rebuild_jobs(self):
        """清除旧 job，按 schedules.json 重建 APScheduler 任务"""
        self._aps.remove_all_jobs()
        self._job_map.clear()

        with self._lock:
            schedules = list(self._schedules)

        for sched in schedules:
            if not sched.get("enabled", False):
                continue
            triggers = sched.get("triggers", [])
            for t in triggers:
                job = self._trigger_to_job(sched, t)
                if job:
                    job_id = str(uuid.uuid4())
                    self._aps.add_job(
                        func=lambda s=sched: self._on_trigger(s),
                        trigger=job["trigger"],
                        id=job_id,
                        name=f"sched-{sched.get('id','?')[:8]}",
                        replace_existing=True,
                    )
                    self._job_map[sched.get("id", "")] = job_id

    def _trigger_to_job(self, sched: dict, t: dict) -> Optional[dict]:
        """将 schedules.json 中的 trigger dict 转为 APScheduler trigger 对象"""
        ttype = t.get("type")
        try:
            if ttype == "cron":
                expr = t.get("cron_expr", "")
                if not expr:
                    return None
                # cron: "分 时 日 月 周"
                parts = expr.strip().split()
                if len(parts) != 5:
                    return None
                return {"trigger": CronTrigger(
                    minute=parts[0], hour=parts[1], day=parts[2],
                    month=parts[3], day_of_week=parts[4],
                    timezone=None,  # 使用系统本地时区
                )}
            elif ttype == "interval":
                interval = t.get("interval_seconds", 300)
                return {"trigger": IntervalTrigger(seconds=interval)}
            elif ttype == "startup":
                # startup 由 Electron 的 runStartupSchedules 处理
                return None
            elif ttype == "event":
                # 事件型触发由 TriggerEngine 独立监控
                return None
        except Exception as e:
            self.send({"type": "error", "message":
                       f"创建触发器失败 (schedule={sched.get('id','?')}, type={ttype}): {e}"})
        return None

    def _on_trigger(self, sched: dict):
        """APScheduler 触发时调用：检查守卫条件 → 发送 fire 事件"""
        now = datetime.now()
        try:
            cond_ok = self._check_conditions(sched)
        except Exception as e:
            self.send({"type": "log", "level": "warning",
                       "message": f"调度 {sched.get('id')} 条件评估异常: {e}"})
            cond_ok = False

        if not cond_ok:
            return

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

    def _check_conditions(self, sched: dict) -> bool:
        cond_group = sched.get("conditions")
        if not cond_group:
            return True
        rules = cond_group.get("rules", [])
        operator = cond_group.get("operator", "AND")
        return evaluate(rules, operator)

    def _request_confirm(self, sched: dict, now: datetime):
        confirm_id = f"cf_{sched['id']}_{int(time.time())}"
        event = threading.Event()
        with self._confirm_lock:
            self._pending_confirm[confirm_id] = {
                "schedule_id": sched["id"], "schedule": sched,
                "timestamp": now.isoformat(), "event": event, "result": None,
            }
        self.send({
            "type": "pre_fire", "confirm_id": confirm_id,
            "schedule_id": sched["id"], "schedule": sched,
            "risk_level": sched.get("risk_level", "info"),
            "timestamp": now.isoformat(),
        })
        t = threading.Thread(target=self._wait_confirm, args=(confirm_id,), daemon=True)
        t.start()

    def _wait_confirm(self, confirm_id: str):
        with self._confirm_lock:
            info = self._pending_confirm.get(confirm_id)
            if not info: return
            event = info["event"]
        confirmed = event.wait(timeout=60)
        with self._confirm_lock:
            info = self._pending_confirm.pop(confirm_id, None)
            if not info: return
            if confirmed and info.get("result") == "proceed":
                sched, ts = info["schedule"], info["timestamp"]
                now = datetime.fromisoformat(ts) if isinstance(ts, str) else ts
                self._do_fire(sched, now)
            elif confirmed and info.get("result") == "cancel":
                self.send({"type": "log", "level": "info",
                           "message": f"调度 {info['schedule_id']} 已被用户取消"})
            else:
                self.send({"type": "log", "level": "info",
                           "message": f"调度 {info['schedule_id']} 确认超时，已跳过"})


# ── 主事件循环 ────────────────────────────────────────────

def main():
    daemon = SchedulerDaemon()
    daemon.send({"type": "log", "level": "info", "message": "调度器守护进程已启动 (APScheduler)"})

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
