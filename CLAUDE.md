# AI 鎺ユ墜閫熻

杩欎唤鏂囦欢鍙啓褰撳墠浜嬪疄鍜屾帓闅滃叆鍙ｏ紝閬垮厤 AI 琚棫 run 鏃ュ織甯﹀亸銆傛洿闀跨殑璇存槑瑙?`docs/`銆?

## 褰撳墠椤圭洰褰㈡€?

1. `python/winpilot_home/winpilot/`
   - 褰撳墠 WinPilot desktop agent銆?
   - 鐢ㄦ埛濮嬬粓闈㈠涓€涓?Agent銆?
   - 鍞竴涓诲惊鐜槸 `agent/loop.py` 鐨?`AgentRunner`銆?
   - GUI / Computer Use 鏄粯璁?Runtime锛涚紪绋?浠撳簱浠诲姟鐢?`agent/runtime_profile.py` 鍦ㄥ悓涓€ runner 鍐呭惎鐢?Coding Runtime銆?
   - `agent/coding.py` 鍙槸鍏煎鏃у鍏ョ殑 shim锛屼笉鍐嶅疄鐜扮嫭绔?Coding 瀛?agent銆?

2. `src/renderer/` + `python/engine/` + `python/features/`
   - legacy 娴佺▼鍥?/ DSL / `@feature` 鑷姩鍖栫郴缁熴€?
   - 浠嶅湪鐢紝浣嗕笉鏄?WinPilot 涓?agent 鐨勫伐鍏锋睜銆?

## 鍏堣鍝簺鏂囦欢

| 鏂囦欢 | 浣滅敤 |
| --- | --- |
| `python/winpilot_home/winpilot/server.py` | FastAPI `/api/chat`銆佺姸鎬併€乼race銆乺eplay 鍏ュ彛 |
| `python/winpilot_home/serve.py` | 鍞竴鏀寔鐨?WinPilot 鏈嶅姟鍚姩鍏ュ彛 |
| `python/winpilot_home/winpilot/config.py` | 鐪熷疄榛樿閰嶇疆鍜屾湰鏈?`config.json` 璇诲啓 |
| `python/winpilot_home/winpilot/agent/router.py` | 鍙瀯寤虹粺涓€ `AgentRunner` |
| `python/winpilot_home/winpilot/agent/runtime_profile.py` | 姣忔 run 鐨?Runtime/Capability 寮€鍏?|
| `python/winpilot_home/winpilot/agent/gui_plan_helpers.py` | GUI schema profile銆佺粯鍥捐鍒掕В鏋愩€佹墦寮€绐楀彛璁″垝鑷姩瀹屾垚绛夌函 helper |
| `python/winpilot_home/winpilot/agent/loop.py` | LLM 涓诲惊鐜€佷笂涓嬫枃鎶樺彔銆佸伐鍏疯皟鐢ㄣ€乨one gate 缂栨帓 |
| `python/winpilot_home/winpilot/agent/tool_schemas.py` | OpenAI function schema 鍜?schema profile |
| `python/winpilot_home/winpilot/agent/tools.py` | `ToolSession` 鐘舵€併€乨ispatch銆佺洰鏍囩獥鍙ｈВ鏋?|
| `python/winpilot_home/winpilot/agent/*_tools.py` | 绐楀彛銆佹劅鐭ャ€佸姩浣溿€佺郴缁熴€佽鍒掋€佹枃浠躲€佽蹇嗙瓑宸ュ叿 mixin |
| `python/winpilot_home/winpilot/fast/` | 浣庡欢杩?controller銆乻ubgoal銆亀atcher銆乮nput locator |
| `python/winpilot_home/winpilot/perception/` | UIA/Win32/OCR/CV/VLM 铻嶅悎銆乺educer銆亀orld |
| `python/winpilot_home/winpilot/recorder/trace.py` | trace銆乣.wps.json` 瀵煎嚭銆佺绾?replay |
| `python/winpilot_home/winpilot/model_adapter.py` | provider 鎺ㄦ柇銆乼hinking 榛樿銆乽sage/cache/cost 褰掍竴鍖?|
| `src/main/winpilotServer.ts` | Electron 鎵樼 WinPilot FastAPI 瀛愯繘绋?|
| `python/agent/__main__.py` | Electron IPC 鍒?WinPilot BUS 浜嬩欢鐨勯€傞厤鍣?|

## 涓嶈韪╁潙

- 涓嶈鐩存帴杩愯 `uvicorn winpilot.server:app`锛涚敤 `python/winpilot_home/serve.py`銆?
- 涓嶈鎶?`python/features/` 褰撴垚 WinPilot 鐨勫伐鍏锋睜銆?
- 涓嶈鍐欒绠楀櫒銆丵Q銆佺綉鏄撲簯绛夊崟杞欢涓撳睘瑙勫垯锛涜鎻愬崌閫氱敤鎰熺煡銆佺獥鍙ｉ檺瀹氥€侀獙璇佸拰鎭㈠銆?
- 涓嶈鎶?Coding 鎻忚堪鎴愮敤鎴峰彲瑙佺殑鍙︿竴涓?agent銆傚畠鏄悓涓€ AgentRunner 涓嬬殑 Coding Runtime銆?
- 涓嶈鐩镐俊 `done` 鏂囨湰绛変簬鐪熷疄鎴愬姛锛涘繀椤绘牳瀵规埅鍥俱€佹枃浠躲€佽繘绋嬨€佺獥鍙ｃ€乼race 鎴栧伐鍏?evidence銆?
- 涓嶈鎻愪氦 `config.json`銆乣traces/`銆乣evals/`銆乣scripts/`銆乣experience_memory.json`銆佺紦瀛樻垨鏋勫缓浜х墿銆?
- 鎺掓煡鍓嶅厛鐪?`/api/status.runtime` 鎴栧墠绔?runtime badge锛岀‘璁ゅ綋鍓嶆湇鍔℃潵鑷摢涓?repo root銆乥ranch銆乧ommit銆丳ID 鍜岀鍙ｃ€?

## 杩愯鏃惰矾寰?

```text
Frontend Agent page or scripts/gui_eval_run.py
  -> WinPilot FastAPI /api/chat
  -> agent/router.py builds one AgentRunner
  -> RuntimeProfile enables desktop/coding capabilities
  -> AgentRunner builds messages + tool schema
  -> LLM function calling
  -> ToolSession.dispatch()
  -> concrete *_tools.py mixin / fast controller
  -> BUS events + trace files + screenshots
  -> done_verifier.py before final success
```

`AgentRunner.status` 鍙兘鏄?`pending`銆乣running`銆乣done`銆乣fail`銆乣reply`銆乣stopped`銆乣error`銆俙done` 浠嶈鐪?`done_check` 鍜屾渶缁堣瘉鎹€?

## 浣庡欢杩?GUI 褰撳墠浜嬪疄

宸茶惤鍦帮細

- `execute_subgoal`锛氬悓涓€ `ToolSession` 涓嬬殑 controller-first routine锛屼笉鏄浜屼釜 agent銆傛敮鎸佽緭鍏?鎸夐挳銆佸崟鍑汇€佹湁搴忓鍑汇€乧lick-only 楠岃瘉銆佺畝鍗曠敾甯?`draw/draws`銆?
- `run_action_batch`锛氫竴缁勫眬閮?GUI 鍔ㄤ綔鍦ㄦ湰鍦?controller 鍐呮墽琛岋紝涓€斿仛蹇€熼獙璇併€?
- 椤跺眰 `click/type_text/hotkey/scroll/focus_window/draw_strokes/drag` 浼氳嚜鍔?fast-wrap 鎴愬崟鍔ㄤ綔 batch銆?
- `screen_watch`銆佸姩浣滃墠鍚?`screen_delta/action_target`銆乣observe(changed_only=true)` 鐨?recent/action ROI 鍜?query fallback銆?
- `perception/reducer.py`锛氱粰 LLM 灏戦噺鍊欓€夊拰涓嶇‘瀹氭€э紝鍘熷 snapshot 浠嶇暀鍦?trace銆?
- `fast/input_locator.py`锛歎IA-off 杈撳叆妗嗗畾浣嶏紝缁勫悎 UIA銆丱CR label銆乸laceholder/search-like 鏂囨湰銆侀檮杩戠煩褰€乫ocused non-button 鍜?synthetic candidate銆?
- `agent/scope_guard.py`锛欸UI mutation 鍓嶆鏌ョ洰鏍囩獥鍙?鍓嶅彴锛岄伩鍏嶇偣鍒?Codex銆佹棩蹇楃獥鍙ｆ垨閿欒绐楀彛銆?
- `drawing_gui` schema profile锛氫弗鏍?GUI-only 缁樺浘/鐢诲竷浠诲姟鍙毚闇插繀瑕?GUI/controller 宸ュ叿锛岄檷浣庡浐瀹?prompt 鎴愭湰銆?
- 缁樺浘 routine 浼氬悎骞剁浉閭?`shape/color/region` 璁″垝涓轰竴娆?`draws[]`锛岀敤鍙棰滆壊 evidence 閫氳繃 done gate銆?
- verified `launch` / `focus_window` 鍙互鑷姩瀹屾垚绾墦寮€绐楀彛璁″垝椤癸紱娣峰悎 open+draw/verify 涓嶄細鐢?launch 鍗曠嫭瀹屾垚銆?

浠嶆湭瀹屾垚锛?

- 娌℃湁鎴愮啛瑙嗚浼烘湇鎺у埗銆?
- 娌℃湁甯搁┗杞婚噺 UI 瑙嗚妯″瀷/YOLO 涓诲惊鐜€?
- 杩樻病鍋氬埌鎵€鏈夊父瑙勫姩浣滈兘涓嶇粡杩?LLM銆丩LM 鍙鐞嗗紓甯搞€?
- Skill 澶嶇敤鐜囥€乂LM 鍏滃簳璋冨害鍜屼綆寤惰繜 dashboard 浠嶉渶瑕佺户缁墦纾ㄣ€?

## 濡備綍澶嶇洏涓€娆?GUI 浠诲姟

1. 璧扮湡瀹炶矾寰勶細鍓嶇 Agent 椤甸潰锛屾垨 `python scripts/gui_eval_run.py "浠诲姟"`銆傚悗鑰呭悓鏍?POST `/api/chat`锛屼笉鏄绾跨粫璺€?
2. 鎷?`run_id`銆?
3. 鐪?`python/winpilot_home/traces/<run_id>.jsonl`锛?
   - `agent_step`锛氱鍑犺疆 LLM銆乼ool call 鏁般€?
   - `agent_tool_start`锛氬伐鍏锋墽琛屽墠鍙傛暟銆?
   - `agent_tool`锛氬伐鍏风粨鏋溿€佽€楁椂銆乼erminal銆佹埅鍥剧储寮曘€?
   - `agent_batch_action*`锛歜atch 鍐呴儴瀛愬姩浣溿€?
   - `done_check`锛歞one gate 鎺ュ彈鎴栨嫆缁濆師鍥犮€?
4. 鎵撳紑鎴浘锛歚python/winpilot_home/traces/<run_id>/<before|after|batch_before|batch_after>_NNN.jpg`銆傛埅鍥炬槸鍒ゆ柇鍋囨垚鍔熺殑绗竴璇佹嵁銆?
5. 鐪?`python/winpilot_home/traces/<run_id>/NNN_llm_request.json` 鍜?`NNN_llm_response.json`锛岀‘璁ゆā鍨嬫瘡涓€姝ュ疄闄呮敹鍒颁簡浠€涔堟劅鐭ユ枃鏈€佸伐鍏风粨鏋溿€佺敤鎴锋彃璇濆拰鍥剧墖銆?
6. 瀵?`execute_subgoal` 鐪嬶細
   - `subgoal_route`
   - `actions_executed`
   - `controller_state`
   - `evidence_packet.matched_texts/missing_texts`
   - `evidence_packet.candidate_click_labels`
   - `matched_visual_colors/missing_visual_colors/visible_colors`
7. 瀵规櫘閫氬姩浣滅湅锛?
   - `wrapped_by_fast_controller`
   - `screen_delta.changed`
   - `action_target.after.foreground_matches_target`
   - `auto_visual_verify`
8. 濡傛灉 GUI-only 浠诲姟鐨?LLM request 閲屽嚭鐜?`search_files/list_dir/read_file/write_file/search_text`锛屽厛鏌?`agent/loop.py` 浼犵粰 `tool_schemas.py` 鐨?`allow_files`锛汫UI-only 鍙互鏈夊彈闄愬彧璇?shell 鏍搁獙锛屼絾涓嶅簲鏆撮湶鏂囦欢宸ュ叿缁曡矾銆?

## 鏂囨。鍜屼唬鐮佺淮鎶?

- README 鍐欓」鐩€昏锛孋LAUDE 鍐?AI 鏈€鐭帴鎵嬶紝`docs/architecture.md` 鍐欑粨鏋勮竟鐣岋紝`docs/winpilot-agent.md` 鍐欒繍琛屽拰澶嶇洏缁嗚妭銆?
- 鍘嗗彶 run id 涓嶈缁х画鍫嗚繘涓绘枃妗ｏ紱闇€瑕佷繚鐣欐椂鍐欏叆 eval 鎶ュ憡鎴?trace 鐩綍銆?
- 鏂拌兘鍔涗紭鍏堟媶杩涚嫭绔嬫ā鍧楋紝涓嶇户缁啫鑳€ `agent/loop.py`銆乣agent/tools.py`銆乣python/engine/executor.py`銆?
- 淇敼宸ュ叿 schema 鏃惰璁颁綇锛氭瘡涓?description 閮戒細杩涘叆 LLM prompt銆?
- 淇敼 replay / `.wps.json` / FlowScript 鏄犲皠鏃讹紝鍚屾椂鐪?`recorder/trace.py`銆乣src/renderer/utils/wpsToFlowScript.ts` 鍜岀浉鍏虫祴璇曘€?


Memory Graph note: long-term memory now uses the SQLite Memory Graph (`memory.db`).
`memory/store.py`, `memory/runtime.py`, `memory/retriever.py`, `memory/packer.py`,
`memory/feedback.py`, and `memory/compiler.py` hold the graph runtime; `memory/experience.py`
is the compatibility facade for old recipe APIs. `experience_memory.json` is migration/compat
data only, not the primary runtime store. See `docs/memory-graph.md`.

## 楠岃瘉

```bash
python -m pytest python/winpilot_home/tests -q
npx vitest run
npm run build
```

鐪熷疄 GUI 鑳藉姏蹇呴』鍐嶈窇 `/api/chat` 璺緞骞朵汉宸ユ牳瀵规埅鍥撅紱鍗曞厓娴嬭瘯鍜?agent 鍙ｅご鎴愬姛閮戒笉澶熴€?
