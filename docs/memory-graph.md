# WinPilot Memory Graph

WinPilot 的长期记忆现在以 SQLite Memory Graph / Memory Capsule 为主存储，旧的 `python/winpilot_home/experience_memory.json` 只作为迁移来源和兼容数据保留。原生工作台在“技能与学习”中查看和复用这套本机数据；`python/winpilot_home/memory.db`、旧 JSON、录制脚本和 trace 都是被 Git 忽略的运行数据，不应提交到 GitHub。

## Memory Capsule

每条长期记忆是一个 `memory_nodes` 节点，至少包含：

- 稳定 `id`
- `kind`：`fact`、`preference`、`rule`、`episode`、`procedure`、`skill`、`warning`、`observation` 等
- `title`、`summary`、`body`
- `status`：`active`、`provisional`、`retired`、`superseded`
- `confidence`
- `payload_json`：保留旧 recipe 字段和扩展元数据
- anchors、conditions、evidence、attempts、artifacts、relations

`observation` 默认是易变记忆，不能当长期 fact。`skill` 必须有 conditions 和 evidence；否则会降级为 provisional `procedure`。新 Skill 绑定规范 Automation IR artifact；旧 `.wps.json` 仍可作为兼容来源迁移。

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

原生视觉的当前画面可能是经过验证的目标窗口裁剪，也可能是完整虚拟桌面。Memory 不保存“这次 JPEG 像素大小”等临时 capture scope 作为跨 run 坐标事实；Skill 必须依赖目标窗口、语义 selector、conditions 和 evidence，在新 run 中重新定位。`controller.target_window_capture_enabled` 被关闭或自动回退全桌面时，旧记忆也不能假定坐标空间未变化。

每次 pack 会写入 `memory_packs`，保存 `selected_ids_json` 和 `scores_json`，用于排查某条记忆为什么被召回。

## Replay And Feedback

`reuse_skill(memory_id=...)` 只代表 0-token replay。replay 成功后仍必须用 `observe`、`read_text`、VLM、nonvisual summary 或确定性工具结果验证最终状态，才能 `done`。

ARP 中只有非空、实际执行且每个叶子步骤通过 Replayer 核验的 Skill 才能产生节点 evidence。缓存命中、检索到一个候选、刚启动 replay、空脚本或只有计划文本都不能完成节点。

反馈规则：

- 只是 packed/retrieved 不加成功分。
- 只有实际 used 且 replay 成功，并且最终 run `done` 的 skill 才加成功分。
- replay 失败后即使 live fallback 最终完成，也不能给该 skill 加成功分。
- 连续失败且从未成功的 auto skill/procedure 会变为 `retired`。
- user/manual fact、preference、rule 不会被自动淘汰。

## Session Memory Boundary

`agent/session.py` 仍只服务“刚才/继续/再来一次/previous/same/it”等指代型任务。它不是长期记忆，不进入 Memory Graph 的长期 fact 召回，也不会替代当前 GUI/文件/工具证据。

Agent 的 live todo 也不是长期记忆。模型可在运行中用 `revise_plan` 替换、清空、增删、移动或重新打开任务项；这些变化只更新当前 run 的 `agent_plan` 状态，不会自动改写 Memory Capsule。反过来，召回的 procedure/skill 只能作为计划候选，不能冻结任务列表或阻止模型根据新鲜画面改路。

## Coding Tasks

coding task 只使用 manual/factual notes：`fact`、`preference`、`rule` 或 manual memory。GUI action skill/procedure 不会注入 coding 任务，避免把桌面 replay 经验污染代码修改上下文。

## Migration

首次访问 memory graph 时，如果 `python/winpilot_home/experience_memory.json` 存在且当前 `python/winpilot_home/memory.db` 尚未迁移，会幂等导入旧 recipes：

- `manual=True` -> `fact` 或 `preference`
- verified script -> `skill`
- 有 steps 但无 verified script -> `procedure`
- `provisional=True` 降低 confidence
- conditions/evidence 进入独立表
- 旧字段完整保存在 payload

迁移后不会删除旧 JSON。`MEMORY_PATH` 保留用于兼容和迁移，`MEMORY_DB_PATH = ROOT_DIR / "memory.db"` 对应 `python/winpilot_home/memory.db`，是新主存储。

## Debugging

排查召回问题优先看：

- `memory_packs.selected_ids_json`
- `memory_packs.scores_json`
- `memory_attempts` 中的 `used`、`replay_ok`、`replay_failed`、`expanded`
- `memory_edges` 中的 `warning_for`、`supersedes`、`verified_by`

memory 系统初始化失败、SQLite 损坏或 FTS5 不可用时，Agent 会退化为无长期记忆运行；FTS5 不可用会自动 fallback 到 LIKE 搜索。

运行时还必须通过 contract v2 和源码指纹检查。工作区源码变化后应重启 Python 服务，再判断某条记忆或 Skill 的表现；旧进程、旧 commit 与新前端混跑得到的结果不能作为记忆质量结论。

`controller.multi_input_cooperation` 和 Desktop Action Queue 属于当前执行安全策略，不属于 Skill 参数。Replay/Memory 不能通过记住旧配置绕开物理输入 single-writer、人类焦点/光标所有权或目标窗口 scope guard。
