# WinPilot Memory Graph

WinPilot 的长期记忆现在以 SQLite Memory Graph / Memory Capsule 为主存储，旧的 `experience_memory.json` 只作为迁移来源和兼容数据保留。

## Memory Capsule

每条长期记忆是一个 `memory_nodes` 节点，至少包含：

- 稳定 `id`
- `kind`：`fact`、`preference`、`rule`、`episode`、`procedure`、`skill`、`warning`、`observation` 等
- `title`、`summary`、`body`
- `status`：`active`、`provisional`、`retired`、`superseded`
- `confidence`
- `payload_json`：保留旧 recipe 字段和扩展元数据
- anchors、conditions、evidence、attempts、artifacts、relations

`observation` 默认是易变记忆，不能当长期 fact。`skill` 必须有 conditions 和 evidence；否则会降级为 provisional `procedure`。绑定 `.wps.json` 的 replay 脚本通过 payload 或 `memory_artifacts` 保存。

## Graph Relations

关系存在 `memory_edges`，常用类型：

- `derived_from`：skill/procedure 来自某次 episode
- `supported_by` / `verified_by`：证据支持或验证
- `warning_for`：warning 关联到 skill/procedure
- `supersedes`：新记忆替代旧记忆，旧节点变为 `superseded`
- `requires`：复用前需要满足的前置条件
- `used_in` / `expanded_in`：运行或展开关系

retriever 会在召回 skill/procedure 时展开相关 warning、requires、verified_by、supported_by，但不会把 raw artifact/chunk 直接塞进 LLM。

## Memory Pack

`runtime.prepare_run_memory(task, runtime_profile, session_context, run_id)` 是运行时入口：

1. 解析 TaskFrame。
2. hard filters：过滤过期、retired、superseded；volatile live-state task 不召回旧 GUI skill/procedure；coding task 只召回 manual/factual notes。
3. recall：anchor、FTS/LIKE、中文 bigram、app match。
4. graph expansion：带出 warning/requires/verified_by/supported_by。
5. pack：生成短 Memory Pack，默认不超过 4800 字符。

Memory Pack 作为 task 后面的 user message 注入，system prompt 保持稳定。Pack 会明确提醒：当前屏幕、文件和工具证据优先；长期记忆只是 hint；replay 成功不是 done 证据。

每次 pack 会写入 `memory_packs`，保存 `selected_ids_json` 和 `scores_json`，用于排查某条记忆为什么被召回。

## Replay And Feedback

`reuse_skill(memory_id=...)` 只代表 0-token replay。replay 成功后仍必须用 `observe`、`read_text`、VLM、nonvisual summary 或确定性工具结果验证最终状态，才能 `done`。

反馈规则：

- 只是 packed/retrieved 不加成功分。
- 只有实际 used 且 replay 成功，并且最终 run `done` 的 skill 才加成功分。
- replay 失败后即使 live fallback 最终完成，也不能给该 skill 加成功分。
- 连续失败且从未成功的 auto skill/procedure 会变为 `retired`。
- user/manual fact、preference、rule 不会被自动淘汰。

## Session Memory Boundary

`agent/session.py` 仍只服务“刚才/继续/再来一次/previous/same/it”等指代型任务。它不是长期记忆，不进入 Memory Graph 的长期 fact 召回，也不会替代当前 GUI/文件/工具证据。

## Coding Tasks

coding task 只使用 manual/factual notes：`fact`、`preference`、`rule` 或 manual memory。GUI action skill/procedure 不会注入 coding 任务，避免把桌面 replay 经验污染代码修改上下文。

## Migration

首次访问 memory graph 时，如果 `experience_memory.json` 存在且当前 `memory.db` 尚未迁移，会幂等导入旧 recipes：

- `manual=True` -> `fact` 或 `preference`
- verified script -> `skill`
- 有 steps 但无 verified script -> `procedure`
- `provisional=True` 降低 confidence
- conditions/evidence 进入独立表
- 旧字段完整保存在 payload

迁移后不会删除旧 JSON。`MEMORY_PATH` 保留用于兼容和迁移，`MEMORY_DB_PATH = ROOT_DIR / "memory.db"` 是新主存储。

## Debugging

排查召回问题优先看：

- `memory_packs.selected_ids_json`
- `memory_packs.scores_json`
- `memory_attempts` 中的 `used`、`replay_ok`、`replay_failed`、`expanded`
- `memory_edges` 中的 `warning_for`、`supersedes`、`verified_by`

memory 系统初始化失败、SQLite 损坏或 FTS5 不可用时，Agent 会退化为无长期记忆运行；FTS5 不可用会自动 fallback 到 LIKE 搜索。
