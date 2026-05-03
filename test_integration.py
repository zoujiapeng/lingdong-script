"""Integration test for the merged Python engine."""
import sys
import json
sys.path.insert(0, 'python')

# 1. Engine imports
from engine import (
    DSLParser, ASTNode, NodeType, DSLParseError,
    DSLExecutor, ExecutorState, ExecutionContext, ExecutionError, BreakLoop
)
from engine.scheduler import TaskScheduler, TaskPriority
from engine.healing import HealingPipeline
from engine.optimizer import TelemetryAnalyzer
from engine.store import ExecutionStore
from engine.triggers import TriggerEngine, TriggerSpec, TriggerInstance
print("[PASS] All engine imports")

# 2. DSL Parser - uppercase keywords (original lobster format)
p = DSLParser()
ast = p.parse("CLICK 1\nWAIT 2\nLOOP 3\n  CLICK 1\n  WAIT 0.5\nEND\nCLICK 2")
assert len(ast.children) == 4
assert ast.children[0].type == NodeType.CLICK
assert ast.children[1].type == NodeType.WAIT
assert ast.children[2].type == NodeType.LOOP
assert ast.children[3].type == NodeType.CLICK
assert len(ast.children[2].children) == 2
print("[PASS] DSL parser: uppercase keywords")

# 3. DSL Parser - lowercase keywords (lingdong format)
ast2 = DSLParser.from_string("click 1\nwait 1\nloop 3\n  click 1\nend")
assert ast2.children[0].type == NodeType.CLICK
assert ast2.children[1].type == NodeType.WAIT
assert ast2.children[2].type == NodeType.LOOP
print("[PASS] DSL parser: lowercase keywords")

# 4. New node types
ast3 = DSLParser.from_string("SWIPE 100 200 300 400\nSCROLL -3\nDELAY 0.5")
assert ast3.children[0].type == NodeType.SWIPE
assert ast3.children[1].type == NodeType.SCROLL
assert ast3.children[2].type == NodeType.DELAY
print("[PASS] DSL parser: SWIPE/SCROLL/DELAY")

# 5. IF/ELSE
ast4 = DSLParser.from_string("IF text:check\n  CLICK 1\nELSE\n  WAIT 5\nEND")
assert ast4.children[0].type == NodeType.IF
assert len(ast4.children[0].children) == 1
assert len(ast4.children[0].else_children) == 1
print("[PASS] DSL parser: IF/ELSE")

# 6. WHILE/UNTIL with END
ast5 = DSLParser.from_string("WHILE text:loading\n  WAIT 1\nEND\nUNTIL text:done\n  WAIT 0.5\nEND")
assert ast5.children[0].type == NodeType.LOOP
assert ast5.children[0].loop_type == "while"
assert ast5.children[1].type == NodeType.LOOP
assert ast5.children[1].loop_type == "until"
print("[PASS] DSL parser: WHILE/UNTIL")

# 6b. WHILE/UNTIL lowercase without END
ast5b = DSLParser.from_string("while text:loading\n  wait 1\nuntil text:done\n  wait 0.5")
assert ast5b.children[0].loop_type == "while"
assert ast5b.children[1].loop_type == "until"
print("[PASS] DSL parser: WHILE/UNTIL lowercase")

# 7. SUBROUTINE/CALL
ast6 = DSLParser.from_string("SUBROUTINE login(user, pass)\n  CLICK 1\n  TYPE {user}\nEND\nCALL login(\"admin\", \"123\")")
assert ast6.children[0].type == NodeType.SUBROUTINE
assert ast6.children[1].type == NodeType.CALL
assert ast6.children[1].call_args == ["admin", "123"]
print("[PASS] DSL parser: SUBROUTINE/CALL")

# 8. Executor creation and state
executor = DSLExecutor()
assert executor.state == ExecutorState.IDLE
print("[PASS] Executor: idle state")

# 9. Executor sync mode
result = executor.run_dsl_sync("CLICK test\nWAIT 1")
assert result["success"] == True
print("[PASS] Executor: sync execution")

# 10. Executor with WHILE (false condition = no iteration)
executor2 = DSLExecutor()
result = executor2.run_dsl_sync("WHILE false\n  WAIT 0.1\nEND")
assert result["success"] == True
print("[PASS] Executor: WHILE false condition")

# 11. Features system
import features._all
from features.registry import registry
all_f = registry.all()
assert len(all_f) > 0, "No features registered!"
print(f"[PASS] Features system: {len(all_f)} features")

# 12. Feature details
click_feature = registry.get("click_target")
assert click_feature is not None
assert click_feature.dsl_keyword == "CLICK"
print(f"[PASS] Feature click_target: DSL={click_feature.dsl_keyword}")

# 13. MCP server import
from mcp.server import handle_tool_call
print("[PASS] MCP server import")

# 14. Scheduler
scheduler = TaskScheduler(executor)
assert scheduler is not None
print("[PASS] Scheduler creation")

# 15. Healing pipeline
healing = HealingPipeline()
assert healing is not None
print("[PASS] Healing pipeline creation")

# 16. Store
store = ExecutionStore()
assert store is not None
print("[PASS] Execution store creation")

print()
print("=" * 50)
print("ALL INTEGRATION TESTS PASSED")
print("=" * 50)
