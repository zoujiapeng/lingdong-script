# Workflow 引擎深度分析

> **文档**: `docs/workflow.md`
> **核心文件**: `python/engine/executor.py` (1591行), `dsl_parser.py` (593行), `dsl_flow_converter.py` (1476行)
> **前置阅读**: [system-map.md](system-map.md)

---

## 1. DSL 执行引擎架构

```mermaid
graph TB
    subgraph Input["输入"]
        DSL_Text["DSL 文本<br/>CLICK 100 200<br/>WAIT 2<br/>TYPE hello"]
        FlowScript["FlowScript JSON<br/>{nodes, edges}"]
    end

    subgraph Conversion["转换层"]
        Flow2DSL["dsl_flow_converter.py<br/>flow_to_dsl()"]
    end

    subgraph Parse["解析层"]
        Parser["dsl_parser.py<br/>DSLParser.parse()"]
        Tokenizer["_tokenize()<br/>→ [(lineno, keyword, args)]"]
        Indent["_indent_to_end()<br/>缩进→END 终止"]
        Block["_parse_block()<br/>递归下降<br/>70+ 关键字"]
        Registry["_load_registry_keywords()<br/>@feature DSL 关键字"]
    end

    subgraph Execute["执行层"]
        Executor["executor.py<br/>DSLExecutor"]
        State["状态机<br/>IDLE→RUNNING→..."]
        Dispatch["_execute_node()<br/>70+ 分支调度"]
        Action["_call_action()<br/>3次重试→HealingPipeline"]
        Dialog["_try_handle_dialog()<br/>后台对话框线程"]
        Scope["作用域链<br/>变量插值 {var}"]
    end

    subgraph Post["执行后"]
        Crystal["crystallizer.py<br/>结晶为模板"]
        Store["store.py<br/>遥测日志"]
        Telemetry["optimizer.py<br/>性能分析"]
    end

    DSL_Text --> Parser
    FlowScript --> Flow2DSL
    Flow2DSL --> DSL_Text

    Parser --> Tokenizer
    Tokenizer --> Indent
    Indent --> Block
    Block --> Registry

    Block --> Executor
    Executor --> State
    State --> Dispatch
    Dispatch --> Action
    Action --> Dialog
    Dialog --> Scope

    Executor --> Crystal
    Executor --> Store
    Store --> Telemetry
```

---

## 2. DSL 语法系统

### 78 个内置关键字

| 类别 | 关键字 | 数量 |
|------|--------|------|
| **交互** | CLICK, TYPE, INPUT_TEXT, CLICK_IMAGE, CLICK_TEXT, SWIPE, SCROLL, DRAG | 8 |
| **键盘** | HOTKEY | 1 |
| **导航/焦点** | FOCUS | 1 |
| **等待/计时** | WAIT, WAIT_RANDOM, DELAY, WAITSCREEN, SCREENSTABLE | 5 |
| **控制流** | LOOP, IF, ELSE, ELSEIF, ELIF, END, BREAK, WHILE, UNTIL, PARALLEL, WITH, RETURN | 12 |
| **子程序** | SUBROUTINE, CALL, IMPORT, RUN | 4 |
| **事件** | WHEN | 1 |
| **视觉/OCR** | OCR_FIND, OCR_EXTRACT, IMAGE_FIND, IMAGE_CLICK, SCENE_CONDITION, VLM_CONDITION, VLM_ANALYZE, FINDCANVAS | 8 |
| **截图** | SCREENSHOT, REGION_SELECT, TEMPLATE_CAPTURE, REGION_WAIT, POPUP_CLOSE | 5 |
| **进度条** | PROGRESS_WAIT, PROGRESS_INIT, PROGRESS_RELOCATE | 3 |
| **颜色** | COLOR_CHECK | 1 |
| **变量** | SET, VARIABLE_INC | 2 |
| **文件** | WAIT_FILE | 1 |
| **系统电源** | SHUTDOWN, REBOOT, LOGOFF, HIBERNATE, LOCK, SLEEP, DISPLAY_OFF, DESKTOP, BATTERY, EMPTY_BIN, BRIGHTNESS | 11 |
| **音频** | VOLUME, MUTE | 2 |
| **进程** | KILL, WAIT_PROCESS, WAIT_EXIT, LAUNCH | 4 |
| **窗口** | MINIMIZE, MAXIMIZE, CLOSE_WINDOW, RESIZE_WINDOW | 4 |
| **桌面** | WALLPAPER | 1 |
| **剪贴板** | CLIPBOARD_SET, CLIPBOARD_GET | 2 |
| **文件对话框** | FILEDIALOG_SAVE, FILEDIALOG_OPEN, FILEDIALOG_FOLDER | 3 |

### 5 个别名
BROWSER_NAV, BROWSER_WAIT, BROWSER_FIND, BROWSER_TEXT, BROWSER_JS

### N 个动态关键字
从 @feature 注册表自动注入（dsl_keyword 不为空的 feature）

---

## 3. 执行状态机

```mermaid
stateDiagram-v2
    [*] --> IDLE
    IDLE --> RUNNING: run_dsl() / run_dsl_sync()
    
    RUNNING --> PAUSED: pause()
    RUNNING --> STOPPED: stop()
    RUNNING --> ERROR: 异常
    RUNNING --> FINISHED: 正常完成
    
    PAUSED --> RUNNING: resume()
    PAUSED --> STOPPED: stop()
    
    ERROR --> IDLE: reset()
    FINISHED --> IDLE: reset()
    
    STOPPED --> IDLE: reset()
```

### 状态字段

| 状态 | 说明 | 可执行? | 可暂停? | 可停止? |
|------|------|---------|---------|---------|
| `IDLE` | 空闲待命 | ✅ | ❌ | ❌ |
| `RUNNING` | 执行中 | ❌ | ✅ | ✅ |
| `PAUSED` | 已暂停 | ❌ | ❌ | ✅ |
| `STOPPED` | 已停止 | ❌ | ❌ | ❌ |
| `ERROR` | 错误 | ❌ | ❌ | ❌ |
| `FINISHED` | 完成 | ❌ | ❌ | ❌ |

### 线程控制

```
_pause_event = threading.Event()  # set() = 运行中, clear() = 暂停
_stop_flag = threading.Event()    # set() = 停止请求
_action_lock = threading.Lock()   # 并行分支线程安全

_check_control():
  _pause_event.wait()  # 阻塞直到 resume()
  if _stop_flag: raise StopExecution
```

---

## 4. DSL 执行流水线

```mermaid
sequenceDiagram
    participant User as 用户
    participant Engine as engine.py
    participant Parser as DSLParser
    participant Executor as DSLExecutor
    participant Action as ActionHandler
    participant Screen as 屏幕/系统

    User->>Engine: FlowScript JSON 或 DSL 文本
    
    alt FlowScript 输入
        Engine->>Converter: flow_to_dsl()
        Converter-->>Engine: DSL 文本
    end
    
    Engine->>Parser: parse(dsl_text)
    Parser->>Parser: _tokenize() 行分割
    Parser->>Parser: _indent_to_end() 缩进→END
    Parser->>Parser: _parse_block() 递归下降
    Parser-->>Engine: AST (ASTNode 树)
    
    Engine->>Executor: run_dsl_sync(ast)
    Executor->>Executor: _state = RUNNING
    
    loop 遍历 AST
        Executor->>Executor: _execute_node(node)
        
        alt 控制流节点
            Executor->>Executor: _exec_loop() / _exec_if()
            Note over Executor: 递归处理子节点
        else 动作节点
            Executor->>Executor: _call_action(action, **kwargs)
            
            loop 最多3次重试
                Executor->>Action: action_handler(action, **kwargs)
                
                alt 失败重建
                    Action->>Registry: registry.execute()
                    Registry-->>Action: result
                end
                
                Action-->>Executor: {success, result}
                
                alt 失败
                    Executor->>Healing: heal_action() → 4级降级
                    Healing->>Action: 偏移/重试/切换模式
                end
            end
            
            Executor->>Executor: _try_handle_dialog()
        end
        
        Executor-->>Engine: node_done 事件
    end
    
    Engine->>Crystallizer: crystallize() 保存模板
    Engine->>Store: 遥测记录
    Engine-->>User: execution_done 事件
```

---

## 5. FlowScript ↔ DSL 双向转换

```mermaid
flowchart LR
    subgraph Canvas["流程图画布"]
        Nodes["节点 [{type, x, y, params}]"]
        Edges["边 [{from, to, port}]"]
    end

    subgraph DSL["DSL 文本"]
        Code["CLICK 100 200<br/>WAIT 2<br/>TYPE hello"]
    end

    subgraph AST["AST 树"]
        ASTNode["SEQUENCE<br/>├ CLICK<br/>├ WAIT<br/>└ TYPE"]
    end

    Canvas -->|dsl_flow_converter<br/>flow_to_dsl()| DSL
    DSL -->|dsl_parser<br/>parse()| AST
    AST -->|dsl_flow_converter<br/>dsl_to_flow()| Canvas
    
    DSL -->|executor<br/>run_dsl_sync()| AST
```

### 转换映射规则

| FlowScript 节点类型 | DSL 关键字 | 说明 |
|---------------------|-----------|------|
| `click` | `CLICK x y` | 坐标点击 |
| `click_text` | `CLICK_TEXT text` | OCR 文字点击 |
| `image_click` | `IMAGE_CLICK targets` | 图片识别点击 |
| `input_text` | `TYPE text x y` | 输入文字 |
| `keyboard` | `HOTKEY keys` | 快捷键 |
| `wait` | `WAIT duration` 或 `WAITSCREEN` | 等待 |
| `condition` | `IF` / `ELSE` / `END` | 条件分支 |
| `loop` | `LOOP N` / `END` | 循环 |
| `screenshot` | `SCREENSHOT` | 截图 |
| `swipe` | `SWIPE x1 y1 x2 y2` | 滑动 |

---

## 6. 结晶化系统

```mermaid
flowchart LR
    Exec["DSL 执行成功"] --> Crystal
    
    subgraph Crystallization["crystallizer.py"]
        Crystal["crystallize()"]
        Name["_derive_template_name()"]
        Save["_crystallize_flow()"]
        Category["_categorize()"]
    end

    Crystal --> Name
    Name --> Save
    Save --> Category

    subgraph Storage["存储"]
        Flow["templates/*.flow"]
        DSL_File["templates/*.dsl"]
        Index["skill_tree.json"]
    end

    Save --> Flow
    Save --> DSL_File
    Category --> Index

    subgraph Usage["使用"]
        Browser["TemplateBrowser 浏览"]
        Search["语义搜索"]
        Reuse["模板加载到画布"]
    end

    Flow --> Browser
    DSL_File --> Search
    Index --> Reuse
```

### 分类映射

| 首个 DSL 关键字 | 模板分类 | 说明 |
|-----------------|----------|------|
| CLICK / TYPE | 屏幕操作 | 交互操作 |
| LAUNCH | 软件启动 | 程序启动 |
| WAIT / WAITSCREEN | 等待监控 | 等待类 |
| SCREENSHOT / IMAGE_FIND | 截图处理 | 视觉操作 |
| LOOP | 循环流程 | 循环结构 |
| IF | 条件判断 | 条件分支 |
| HOTKEY | 快捷键 | 键盘操作 |
| SET | 变量操作 | 变量使用 |
| CALL | 子流程 | 子程序调用 |
| WHEN | 事件触发 | 触发器 |

---

## 7. 条件求值系统

```mermaid
graph TB
    subgraph ConditionEvaluator["condition_evaluator.py"]
        Eval["evaluate(rules, operator)"]
        EvalOne["evaluate_one(rule)"]
        Registry["注册表: 11 种类型"]
    end

    subgraph ConditionTypes["内置条件类型"]
        Always["always_true<br/>无条件通过"]
        Window["window_exists<br/>win32gui 窗口匹配"]
        Process["process_running<br/>psutil 进程检测"]
        Weekday["is_weekday / is_weekend<br/>日期判断"]
        Dow["day_of_week<br/>特定星期"]
        Time["time_between<br/>时间范围(支持跨天)"]
        Image["image_exists<br/>@feature image_find"]
        Text["text_exists<br/>@feature ocr_find"]
        Color["color_match<br/>@feature color_check"]
        Scene["scene_match<br/>@feature scene_condition"]
    end

    subgraph Usage["使用场景"]
        DSL_IF["DSL IF 条件"]
        DSL_WHILE["WHILE/UNTIL"]
        Trigger["触发器守卫条件"]
        Scheduler["调度器条件"]
    end

    Eval --> Always
    Eval --> Window
    Eval --> Process
    Eval --> Weekday
    Eval --> Dow
    Eval --> Time
    Eval --> Image
    Eval --> Text
    Eval --> Color
    Eval --> Scene

    DSL_IF --> Eval
    DSL_WHILE --> Eval
    Trigger --> Eval
    Scheduler --> Eval
```
