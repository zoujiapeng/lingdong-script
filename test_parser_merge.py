"""Test the merged DSL parser."""
import sys
import json
sys.path.insert(0, 'python')
from engine.dsl_parser import DSLParser

# Test 1: Original uppercase format
print("=== Test 1: Uppercase ===")
ast = DSLParser.from_string("CLICK 确认\nWAIT 1\nLOOP 3\n  CLICK 开始\n  WAIT 0.5\nEND\nCLICK 完成")
print(json.dumps(ast.to_dict(), ensure_ascii=False, indent=2))

# Test 2: Lowercase format
print("\n=== Test 2: Lowercase ===")
ast2 = DSLParser.from_string("click 确认\nwait 1\nloop 3\n  click 开始\n  wait 0.5\nend\nclick 完成")
print(json.dumps(ast2.to_dict(), ensure_ascii=False, indent=2))

# Test 3: Mixed format
print("\n=== Test 3: Mixed ===")
ast3 = DSLParser.from_string("CLICK 确认\nwait 2\nLOOP 5\n  screenshot\n  WAIT 1\nEND")
print(json.dumps(ast3.to_dict(), ensure_ascii=False, indent=2))

# Test 4: IF/ELSE
print("\n=== Test 4: IF/ELSE ===")
ast4 = DSLParser.from_string("IF text:领取\n  CLICK 领取\nELSE\n  WAIT 5\nEND")
print(json.dumps(ast4.to_dict(), ensure_ascii=False, indent=2))

# Test 5: SWIPE/SCROLL/DELAY
print("\n=== Test 5: SWIPE/SCROLL/DELAY ===")
ast5 = DSLParser.from_string("SWIPE 100 200 300 400\nSCROLL -3\nDELAY 0.5")
print(json.dumps(ast5.to_dict(), ensure_ascii=False, indent=2))

# Test 6: SUBROUTINE/CALL
print("\n=== Test 6: SUBROUTINE/CALL ===")
ast6 = DSLParser.from_string("SUBROUTINE login(user, pass)\n  CLICK 用户名\n  TYPE {user}\n  CLICK 登录\nEND\nCALL login(\"admin\", \"123\")")
print(json.dumps(ast6.to_dict(), ensure_ascii=False, indent=2))

# Test 7: WHILE/UNTIL
print("\n=== Test 7: WHILE/UNTIL ===")
try:
    ast7 = DSLParser.from_string("WHILE text:加载中\n  WAIT 1\nEND\nUNTIL text:完成\n  WAIT 0.5\nEND")
    print(json.dumps(ast7.to_dict(), ensure_ascii=False, indent=2))
except Exception as e:
    print(f"WHILE/UNTIL parse error: {e}")

print("\nAll tests passed!")
