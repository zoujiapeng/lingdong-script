# 状态机分析

> **文档**: `docs/state-machine.md`
> **覆盖**: DSL执行器 / Agent / 流程图节点 / Plan DAG / 触发器

---

## 1. DSL 执行器状态机

**文件**: `python/engine/executor.py` (类 `DSLExecutor`, 状态字段 `_state`)

```mermaid
stateDiagram-v2
    [*] --> IDLE
    
    IDLE --> RUNNING: run_dsl() / run_dsl_sync()
    IDLE --> IDLE: reset()
    
    RUNNING --> PAUSED: pause()
    RUNNING --> STOPPED: stop()
    RUNNING --> ERROR: 未处理异常
    RUNNING --> FINISHED: 全部节点执行完成
    
    PAUSED --> RUNNING: resume()
    PAUSED --> STOPPED: stop()
    PAUSED --> ERROR: 暂停中异常
    
    ERROR --> IDLE: reset()
    FINISHED --> IDLE: reset()
    STOPPED --> IDLE: reset()
    
    note right of RUNNING
        线程控制:
        _pause_event.wait()
        _stop_flag check
        重试: 3次 + HealingPipeline
        对话框守护线程
    end note
    
    note right of PAUSED
        保留执行上下文
        可安全停止
        _pause_event.clear()
    end note
```

| 状态 | 含义 | `_pause_event` | `_stop_flag` | 可恢复? |
|------|------|---------------|-------------|---------|
| `IDLE` | 空闲待命 | set | clear | - |
| `RUNNING` | 执行中 | set | clear | - |
| `PAUSED` | 已暂停 | clear | clear | ✅ resume() |
| `STOPPED` | 已停止 | clear | set | ❌ 需要 reset() |
| `ERROR` | 出错 | set | clear | ❌ 需要 reset() |
| `FINISHED` | 完成 | set | clear | ❌ 需要 reset() |

---

## 2. Frontend 执行状态机

**文件**: `src/renderer/types/index.ts` (类型 `ExecutionState.status`)

```mermaid
stateDiagram-v2
    [*] --> idle
    
    idle --> running: startExecution()
    
    running --> paused: pauseExecution()
    running --> stopped: stopExecution()
    running --> completed: 执行成功
    running --> error: 执行失败
    
    paused --> running: resumeExecution()
    paused --> stopped: stopExecution()
    
    error --> idle: 重置
    completed --> idle: 重置
    stopped --> idle: 重置
    
    note right of running
        触发的操作:
        updateNodeStatus(nodeId, 'running')
        高亮当前节点
        进程栏动画
        日志实时追加
    end note
```

---

## 3. 流程图节点状态机

**文件**: `src/renderer/types/index.ts` (类型 `FlowNode.status`)

```mermaid
stateDiagram-v2
    [*] --> idle
    
    idle --> running: 开始执行该节点
    running --> success: 执行成功
    running --> error: 执行失败
    running --> skipped: 条件不满足跳过
    
    success --> idle: 重置
    error --> idle: 重置
    skipped --> idle: 重置
    
    note right of idle
        初始状态
        等待执行
        重置后回到此状态
    end note
    
    note right of running
        触发:
        EdgeLayer 动画
        Loader 旋转
        TopBar 状态显示
    end note
```

---

## 4. Plan DAG 节点状态机

**文件**: `python/agent/planner.py` (类 `PlanNode`, 字段 `status`)

```mermaid
stateDiagram-v2
    [*] --> pending
    
    pending --> running: get_ready_nodes() → 拓扑序就绪
    pending --> skipped: 依赖节点失败(非关键)
    pending --> failed: 依赖环检测
    
    running --> completed: 执行成功
    running --> failed: 重试+fallback 全部失败
    running --> skipped: 非关键节点失败
    running --> pending: 需要重规划
    
    completed --> [*]
    failed --> [*]
    skipped --> [*]
    
    note right of pending
        条件: 所有 depends_on 完成
        排序: path_level + id
    end note
    
    note right of running
        重试: max_retries=2
        fallback: fallback_tools[]
        path 降级: fallback_paths[]
        关键节点失败→全计划终止
    end note
```

---

## 5. 触发器实例状态机

**文件**: `python/engine/triggers.py` (类 `TriggerInstance`, 字段 `status`)

```mermaid
stateDiagram-v2
    [*] --> running: TriggerEngine.start_instance()
    
    running --> fired: watcher 检测到事件
    running --> paused: pause 请求
    running --> stopped: 停止请求
    
    paused --> running: resume 请求
    paused --> stopped: 停止请求
    
    fired --> running: 周期性触发器(重新布防)
    fired --> stopped: 一次性触发器(自动停止)
    
    note right of running
        watcher 线程循环:
        while not _stop_event:
            调用 watcher_fn()
            如果触发:
                检查 guard_conditions
                执行 handler_actions
                fired_count++
    end note
```

---

## 6. 状态机对比汇总

| 状态机 | 位置 | 状态数 | 并发 | 持久化 |
|--------|------|--------|------|--------|
| DSL 执行器 | `engine/executor.py` | 6 | 单线程 + 并行分支 | SQLite checkpoint |
| Frontend 执行 | `types/index.ts` | 6 | N/A | localStorage |
| 流程图节点 | `types/index.ts` | 5 | N/A | FlowScript JSON |
| Plan DAG 节点 | `agent/planner.py` | 5 | 串行拓扑序 | 内存 |
| 触发器实例 | `engine/triggers.py` | 4 | 每实例一线程 | schedules.json |

### 通用状态模式

```
IDLE/RUNNING/PAUSED/STOPPED → 出现在 DSL Executor + Frontend Executor
pending/running/completed/failed → 出现在 Plan DAG + 流程图节点
running/fired/stopped → 出现在触发器
```

### 状态管理最佳实践

1. **线程安全**: 所有状态变更需通过 lock (`threading.RLock` / `threading.Event`)
2. **状态去重**: Agent token 系统防止旧进程事件污染新进程
3. **状态恢复**: DSL Executor 通过 SQLite checkpoint 保存/恢复运行状态
4. **UI 同步**: IPC 事件 (`state_change`, `node_status`) 驱动前端状态更新
