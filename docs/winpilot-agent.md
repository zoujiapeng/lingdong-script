# WinPilot Agent

褰撳墠 WinPilot 涓讳綋鍦?`python/winpilot_home/winpilot/`銆傛湰鏂囧彧鎻忚堪褰撳墠浠ｇ爜鏈哄埗鍜屾帓闅滄柟娉曘€?

## 璺敱鍜?Runtime

`agent/router.py` 涓嶅啀鎸変换鍔″垏鎹?runner锛?

- 鎵€鏈変换鍔￠兘杩涘叆 `agent/loop.py` 鐨?`AgentRunner`銆?
- `agent/runtime_profile.py` 鏍规嵁浠诲姟鏂囨湰鍜岄厤缃惎鐢?Desktop/Coding capability銆?
- GUI-only 浠诲姟浼氬叧闂?Coding Runtime锛屽苟閫氳繃 schema 鍜屾墽琛屽眰 guard 绂佹 shell/file 缁曡矾銆?
- `agent/coding.py` 鍙繚鐣欏吋瀹规棫瀵煎叆鐨?`CodingRunner` shim锛屼笉鍐嶆湁鐙珛 planner/tool loop銆?

浜у搧琛ㄨ揪锛氱敤鎴烽潰瀵圭殑鏄竴涓?Agent锛孋oding銆丆omputer Use銆乀erminal銆丅rowser銆丷eplay 鏄畠鐨勮兘鍔涖€?

## 涓诲惊鐜?

涓€娆?`/api/chat` 浠诲姟鐨勭湡瀹炶矾寰勶細

```text
server.py _RunState
  -> router.build_runner()
  -> AgentRunner.run()
  -> build messages + tool_schemas()
  -> model_adapter.chat()
  -> _handle_tool_calls()
  -> ToolSession.dispatch()
  -> tool mixin / FastGuiController / CodingSession
  -> BUS + trace + screenshots
  -> done_verifier.py
```

`AgentRunner` 璐熻矗锛?

- system prompt銆乽ser/task銆乼ool history銆乮nterjection銆乧ontext folding銆?
- OpenAI-compatible function-calling銆?
- usage/cache/cost 鑱氬悎銆?
- tool result 鍘嬬缉鍐欏洖 LLM history锛屽悓鏃朵繚鐣欏畬鏁?trace銆?
- done 浜屾鏍搁獙鍜屽亣鎴愬姛闃叉姢銆?

`agent/gui_plan_helpers.py` 鏀剧疆涓庝富寰幆鏃犲叧鐨勭函 helper锛歴chema profile 閫夋嫨銆佺粯鍥捐鍒掕В鏋愩€佺粯鍥捐瑙夌‘璁ゅ垽鏂€佺函鎵撳紑绐楀彛璁″垝椤瑰垽鏂€傛祴璇曚粛鍙粠 `agent.loop` 瀵煎叆杩欎簺鏃?helper 鍚嶇О锛屼絾鏂颁唬鐮佸簲浼樺厛浠?helper 妯″潡寮曠敤銆?

## ToolSession

`agent/tools.py` 鎸佹湁涓€娆?run 鐨勫伐鍏风姸鎬侊細

- 褰撳墠 target hwnd/title/process銆?
- 鏈€杩?`Snapshot`銆?
- plan/world/undo stack銆?
- RuntimeProfile銆?
- `dispatch()` 鍒板悇涓?mixin銆?

宸ュ叿瀹炵幇鍒嗗竷锛?

| 鏂囦欢 | 宸ュ叿鑼冨洿 |
| --- | --- |
| `window_tools.py` | `list_windows`銆乣scan_windows`銆乣focus_window`銆乣find_app`銆乣find_program` |
| `perception_tools.py` | `observe`銆乣read_text`銆乣find_image`銆乣vlm_describe`銆乼emplate/color 绛夋劅鐭?|
| `interaction_tools.py` | `click`銆乣type_text`銆乣hotkey`銆乣scroll`銆乣drag`銆乣draw_strokes`銆乣wait` |
| `system_tools.py` | `launch`銆乣run_shell`銆乣run_detached`銆乣tray_click`銆乣search_files`銆乣undo_last` |
| `fast_tools.py` | `execute_subgoal`銆乣run_action_batch`銆乣screen_watch` |
| `plan_tools.py` | `revise_plan`銆乣complete_subgoal`銆乣done`銆乣fail` |
| `file_tools.py` | `list_dir`銆乣read_file`銆乣write_file`銆乣search_text` |
| `memory_tools.py` | `remember`銆乣forget`銆乣reuse_skill` |
| `desktop_tools.py` | clipboard銆乵ouse low-level銆乻creenshot銆乨isplays銆乮nstalled apps |
| `coding_tools.py` | workspace-confined repo file/shell/patch/GitHub tools |
| `scheduler.py` | persistent internal AgentRunner schedules (`schedule_task`, `list_scheduled_tasks`, `cancel_scheduled_task`) |

## Tool Schema

`agent/tool_schemas.py` 鏋勯€犳ā鍨嬪彲瑙佸伐鍏枫€傛墽琛屽眰鑳藉姏涓嶇瓑浜庢ā鍨嬫瘡杞彲瑙佸伐鍏枫€?

涓昏寮€鍏筹細

- `allow_shell`
- `allow_files`
- `planning`
- `coding_runtime`
- `profile`

GUI-only 浠诲姟榛樿涓嶆毚闇?`search_files` / file tools锛涘嵆浣挎煇浜涗换鍔″厑璁告渶缁堝彧璇?`run_shell` 鏍搁獙锛屼篃涓嶈兘璁╂ā鍨嬬敤鏂囦欢鎼滅储/璇诲啓鏉ュ噯澶囨垨鏇夸唬鍙 GUI 鎿嶄綔銆?

Profile锛?

- `standard`锛氭櫘閫?desktop/coding 榛樿銆?
- `lean_gui`锛氳緝绐勭殑 GUI schema銆?
- `drawing_gui`锛氫弗鏍?GUI-only 缁樺浘/鐢诲竷浠诲姟锛屼繚鐣?GUI/controller 鏍稿績宸ュ叿锛岄殣钘忔棤鍏冲伐鍏凤紝骞舵妸 `execute_subgoal` 鍙傛暟瑁佸壀鍒扮粯鍥?楠岃瘉鐩稿叧瀛楁銆?

鎺掓煡 GUI-only 缁曡矾鏃讹紝鐩存帴鎵撳紑 `traces/<run_id>/NNN_llm_request.json` 鐪?tools 鍒楄〃锛屼笉瑕佸彧鐪?agent 璇翠簡浠€涔堛€?

## 浣庡欢杩?GUI Controller

### execute_subgoal

`execute_subgoal` 鏄悓涓€ AgentRunner 閲岀殑 controller-first routine锛屼笉鏄浜屼釜 agent銆傚畠瑕佹眰鑷冲皯鏈変竴涓叿浣?routine 瀛楁锛?

- `input`
- `click`
- `clicks`
- `draw`
- `draws`
- `verify_texts`

鍙湁鑷劧璇█ `subgoal` 浼氬揩閫?blocked锛屼笉鍒濆鍖?controller/perception銆?

鍏稿瀷鐢ㄩ€旓細

- 杈撳叆 literal 鍒板彲瀹氫綅杈撳叆妗嗭紝鐐瑰嚮 Apply/Search/Send锛屽苟楠岃瘉鍙鐘舵€併€?
- 鐐瑰嚮鏈夊彲瑙?label 鐨勬寜閽紝骞堕獙璇佸彲瑙佺粨鏋溿€?
- 鎸夐『搴忕偣鍑诲涓槑鏄炬帶浠躲€?
- 鍦ㄧ畝鍗曠敾甯冧腑缁樺埗 triangle/rectangle/circle/line/check 绛?shape銆?

鍏抽敭缁撴灉瀛楁锛?

- `subgoal_route`
- `actions_executed`
- `controller_state`
- `evidence_packet.matched_texts`
- `evidence_packet.missing_texts`
- `evidence_packet.candidate_click_labels`
- `evidence_packet.matched_visual_colors`
- `evidence_packet.missing_visual_colors`
- `evidence_packet.visible_colors`

濡傛灉閿氱偣 label 娌″懡涓絾 `candidate_click_labels` 鏈夊€硷紝涓嬩竴姝ュ簲璁╂ā鍨嬬敤鏈€鎺ヨ繎鐨勬湰鍦板寲/鍚屼箟鍙鏍囩閲嶈瘯锛岃€屼笉鏄浆鍘?shell 鎴栨簮鐮佹悳绱?GUI 鏂囨銆?

### run_action_batch

`run_action_batch` 鍦ㄦ湰鍦拌繛缁墽琛屽涓姩浣滐紝骞惰繑鍥炴瘡涓瓙鍔ㄤ綔缁撴灉銆傞《灞?`click/type_text/hotkey/scroll/focus_window/draw_strokes/drag` 涔熶細鑷姩 fast-wrap 鎴愬崟鍔ㄤ綔 batch銆?

鎺掓煡瀛楁锛?

- `wrapped_by_fast_controller`
- `metrics`
- `screen_delta`
- `action_target`
- `auto_visual_verify`
- `visual_change_detected`

`screen_delta.changed=true` 鍙鏄庡儚绱犲彉鍖栵紝涓嶆槸涓氬姟鎴愬姛璇佹嵁銆傝缁撳悎 target window銆乪xpected evidence 鍜屾埅鍥俱€?

### 鍙樺寲鍖哄煙瑙傚療

`screen_watch` 璁板綍鏈€杩戝彉鍖?ROI锛涘姩浣滃伐鍏疯繕浼氳褰?action ROI銆俙observe(changed_only=true)` 浼樺厛鐪?action/recent ROI銆?

濡傛灉 ROI 涓病鏈夊懡涓?query锛屼細杩斿洖鏄庣‘鐨?query fallback锛屽啀閫€鍥炲叏绐楀彛 observe锛岄伩鍏嶅眬閮ㄨ鍓瀵兼ā鍨嬨€?

### 杈撳叆妗嗗畾浣?

`fast/input_locator.py` 涓嶆槸鏌愪釜杞欢瑙勫垯銆傚畠缁勫悎锛?

- UIA role/name銆?
- OCR label / placeholder / search-like 鏂囨湰銆?
- 闄勮繎鐭╁舰銆?
- focused non-button銆?
- caret-like 杩囨护銆?
- synthetic candidate銆?

鐢ㄤ簬 UIA 瑕嗙洊涓嶅埌鐨勮嚜缁樿緭鍏ユ锛屼緥濡?QQ銆佹祻瑙堝櫒椤甸潰銆佽嚜瀹氫箟瀹㈡埛绔€?

### 缁樺浘/鐢诲竷

`execute_subgoal.draw` / `draws` 浼氾細

- 鎵?likely canvas銆?
- 蹇呰鏃堕€氳繃閫氱敤棰滆壊闈㈡澘/鑹插潡妫€娴嬮€夎壊銆?
- 鐢熸垚 strokes銆?
- 璋?`draw_strokes`銆?
- 鐢ㄥ眬閮ㄨ瑙夐鑹?evidence 楠岃瘉銆?

棰滆壊闈㈡澘 opener 鍙互缂撳瓨锛屼絾鍏蜂綋鑹插潡鍧愭爣姣忔閲嶆壂锛岄伩鍏?stale swatch銆俙draws[]` 鍙悎骞剁浉閭?shape/color/region 璁″垝锛涚函瑙嗚纭椤瑰彲鐢?controller 棰滆壊璇佹嵁鑷姩瀹屾垚銆倀erminal done 浠嶈蛋 `done_verifier.py`銆?

## 鎰熺煡

鎰熺煡鏉ユ簮锛?

- UIA
- Win32
- OCR
- CV
- VLM锛堥粯璁ゅ叧闂級

`perception/fusion.py` 鐢熸垚 `Snapshot`銆俙perception/reducer.py` 缁?LLM compact 鎽樿锛屽寘鎷?stage銆佺洰鏍囩獥鍙ｃ€佸€欓€夊厓绱犮€佺枒鐐广€乺ecent changes 鍜屾帹鑽?controller action銆傚師濮?snapshot 浠嶅啓鍏?trace銆?

VLM 鍙槸鍏滃簳鎰熺煡锛屼笉搴旀垚涓烘瘡姝ヤ富寰幆銆俙low_latency.vlm_call_count` 鍙粺璁＄湡瀹?VLM 宸ヤ綔锛屼笉缁熻鏈厤缃椂鐨?0ms 鍗犱綅銆?

## Done Gate

`done_verifier.py` 闃叉鍋囨垚鍔熴€傛湁鏁堣瘉鎹寘鎷細

- fresh screen OCR/read_text銆?
- action/batch/subgoal evidence銆?
- 鏂囦欢瀛樺湪/鍐呭銆?
- 杩涚▼/绐楀彛鐘舵€併€?
- VLM 鎴栬瑙夐鑹茶瘉鎹€?
- 鏄庣‘ replay 缁撴灉鍔犲悗缁綋鍓嶇姸鎬侀獙璇併€?

涓嶆帴鍙楋細

- 妯″瀷鍙ｅご鎬荤粨銆?
- 鐐瑰嚮鍓嶅氨瀛樺湪鐨勬寜閽?label銆?
- 涓?target hwnd 涓嶅尮閰嶇殑鍓嶅彴绐楀彛璇佹嵁銆?
- replay 鎴愬姛浣嗘病鏈夊綋鍓嶇姸鎬佺‘璁ゃ€?
- 鐢诲浘浠诲姟涓嚟绌虹敓鎴愮殑 `file_exists` 妫€鏌ワ紝闄ら潪鐢ㄦ埛鐪熺殑瑕佹眰淇濆瓨鏂囦欢銆?

## 璁板繂鍜?Replay

| 鏈哄埗 | 鏂囦欢 | 褰撳墠浜嬪疄 |
| --- | --- | --- |
| long-term memory | `memory/experience.py` | 鏈満 `experience_memory.json`锛宺ecipe/note 鏈?conditions/evidence |
| session memory | `agent/session.py` | 鍙В鏋愨€滅户缁?鍒氭墠閭ｄ釜/鍐嶆潵涓€娆♀€濈瓑鎸囦唬 |
| world advisory | `perception/world.py`銆乣context_guidance.py`銆乣run_state.py` | 鎻愮ず閲嶅銆佸崱浣忋€侀渶瑕?observe銆佸彉鍖栦俊鍙?|
| reuse_skill | `agent/memory_tools.py` | 寮哄尮閰?`.wps.json` replay锛屾垚鍔熷悗浠嶈 done evidence |
| trace/replay | `recorder/trace.py` | trace銆乣.wps.json`銆乧ontrol flow replay |

娓呴櫎璁板繂鍚?agent 鍙樼锛岄€氬父璇存槑 routine/鎰熺煡/楠岃瘉纭疄鍔涜繕涓嶅锛涗笉鑳介潬鐢ㄦ埛鍘嗗彶璁板繂鎾戦€氱敤鑳藉姏銆?


Memory Graph note: long-term memory now uses the SQLite Memory Graph (`memory.db`).
`memory/store.py`, `memory/runtime.py`, `memory/retriever.py`, `memory/packer.py`,
`memory/feedback.py`, and `memory/compiler.py` hold the graph runtime; `memory/experience.py`
is the compatibility facade for old recipe APIs. `experience_memory.json` is migration/compat
data only, not the primary runtime store. See `docs/memory-graph.md`.

## Coding Runtime

Coding 涓嶆槸鐙珛 agent銆傝Е鍙戝悗锛?

- `RuntimeProfile.coding=true`銆?
- schema 鍔犲叆 repo 宸ュ叿锛屼緥濡?`apply_patch`銆乣github_search`銆?
- `list_dir/read_file/write_file/search_text/run_shell` 濮旀墭鍒?workspace-confined `CodingSession`銆?
- clone/build/test/run 杩欑被浠诲姟浼氳蛋 anti-fake-success gate锛氭病鏈夊疄闄?shell/鏋勫缓/娴嬭瘯/鐜妫€鏌ュ皾璇曪紝涓嶈兘 done銆?

GUI-only 浠诲姟涓嶅簲鏆撮湶 coding/file/shell 宸ュ叿銆?

## 濡備綍澶嶇洏宸ュ叿缁嗚妭

1. 纭 runtime锛歚/api/status.runtime` 鎴栧墠绔?runtime badge銆?
2. 鎵?`run_id`銆?
3. 鐪?`python/winpilot_home/traces/<run_id>.jsonl`锛?
   - `agent_start`
   - `agent_step`
   - `agent_tool_start`
   - `agent_tool`
   - `agent_batch_action*`
   - `done_check`
   - `agent_done`
4. 鎵撳紑鍚岀洰褰曟埅鍥撅紝浼樺厛鏍稿 `batch_after_NNN.jpg` 鍜?`after_NNN.jpg`銆?
5. 鐪?`NNN_llm_request.json` 鍜?`NNN_llm_response.json`锛岀‘璁ゆā鍨嬪疄闄呯湅瑙佷簡浠€涔堛€?
6. 鐪?`NNN_tool_result.json` 鑾峰彇瀹屾暣鍘熷宸ュ叿缁撴灉锛汱LM history 閲岀殑 tool result 鍙兘宸茶 compact銆?
7. 濡傛灉鎴浘鍜屽伐鍏风粨鏋滀笉涓€鑷达紝鎸夊伐鍏峰悕瀹氫綅 mixin锛?
   - 绐楀彛锛歚window_tools.py`
   - 鎰熺煡锛歚perception_tools.py`
   - 杈撳叆鍔ㄤ綔锛歚interaction_tools.py`
   - 鍚姩/shell/tray锛歚system_tools.py`
   - batch/subgoal锛歚fast_tools.py`銆乣fast/controller.py`銆乣fast/subgoal.py`
   - done/plan锛歚plan_tools.py`銆乣done_verifier.py`

## 閰嶇疆

閰嶇疆榛樿鍊煎湪 `config.py`锛屾湰鏈鸿鐩栧湪 `python/winpilot_home/config.json`銆?

甯歌椤癸細

- `api.provider/base_url/api_key/model`
- `api.thinking`
- `api.pricing`
- `vlm.enabled/base_url/api_key/model`
- `perception.uia/win32/ocr/cv/vlm`
- `fusion.screen_watch_autostart`
- `agent.shell_enabled`
- `agent.planning_enabled`
- `agent.experience_memory`
- `agent.tool_profile`
- `coding.workspace`

涓嶈鎻愪氦鏈満 `config.json` 鎴?key銆?

## 楠岃瘉

```bash
python -m pytest python/winpilot_home/tests -q
npx vitest run
npm run build
```

GUI 鑳藉姏鏀瑰姩杩橀渶瑕佽窇 `/api/chat` 璺緞锛屽苟浜哄伐鏍稿鎴浘銆傛垚鏈晱鎰熸椂浼樺厛鐢ㄤ究瀹滄ā鍨嬨€佸皬姝ユ暟銆佸崟涓湡瀹炰换鍔″洖褰掋€?
