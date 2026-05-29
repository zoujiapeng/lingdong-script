"""Test tool path selector."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python'))

from agent.tool_path_selector import select_best_path, AccessPattern, classify_tool, get_tools_by_pattern, build_system_prompt
from features.registry import registry
import features._all  # noqa

# 1. Verify all 196 tools classified
errors = []
for spec in registry.all():
    if spec.deprecated or spec.hidden:
        continue
    pattern = classify_tool(spec.name, spec.category.value if spec.category else '', spec.description)
    if pattern is None:
        errors.append(spec.name)

total = len([s for s in registry.all() if not s.deprecated and not s.hidden])
print(f"Total: {total}, errors: {len(errors)}")
assert len(errors) == 0, f"Classification errors: {errors}"

# 2. Group stats
grouped = get_tools_by_pattern()
for p in sorted(AccessPattern):
    count = len(grouped.get(p, []))
    print(f"  {p.label_cn:16s} L{p.value}: {count:3d} tools")

# 3. Path selection tests
test_cases = [
    ("打开记事本输入hello",            AccessPattern.DIRECT_API),
    ("在百度搜索python教程",           AccessPattern.BROWSER_CDP),
    ("打开浏览器搜索今天的天气",         AccessPattern.BROWSER_CDP),
    ("把音量调到30%",                 AccessPattern.DIRECT_API),
    ("读取桌面data.txt内容",          AccessPattern.DIRECT_API),
    ("复制这段文字到剪贴板",           AccessPattern.DIRECT_API),
    ("关闭当前窗口",                  AccessPattern.DIRECT_API),
    ("重启电脑",                      AccessPattern.DIRECT_API),
    ("设置壁纸",                      AccessPattern.DIRECT_API),
    ("多步骤工作流先打开浏览器搜索",     AccessPattern.DSL_GEN),
    ("创建一个自动化流程",             AccessPattern.DSL_GEN),
    ("打开百度",                      AccessPattern.BROWSER_CDP),
    ("在搜索引擎查询python",          AccessPattern.BROWSER_CDP),
    ("搜索Python教程",                AccessPattern.BROWSER_CDP),
    ("关机",                          AccessPattern.DIRECT_API),
    ("保存文件到桌面",                 AccessPattern.DIRECT_API),
    ("打开设置面板",                   AccessPattern.DIRECT_API),
]

print("\n=== Path Selection ===")
failures = []
for goal, expected in test_cases:
    result = select_best_path(goal)
    ok = result == expected
    status = "PASS" if ok else "FAIL"
    if not ok:
        failures.append((goal, expected, result))
    print(f"  {status}: {goal[:35]:35s} → {result.label_cn if result else 'AUTO':16s} (expect {expected.label_cn})")

if failures:
    print(f"\n{len(failures)} FAILURES:")
    for goal, exp, got in failures:
        print(f"  {goal}: expect {exp.label_cn}, got {got.label_cn if got else 'AUTO'}")

# 4. Verify system prompt builds
prompt = build_system_prompt()
assert len(prompt) > 1000, "Prompt too short"
# Verify path table is present
assert "访问模式" in prompt, "Missing path table"
assert "DIRECT_API" not in prompt, "Internal enum leaked into prompt"
print(f"\n=== System prompt: {len(prompt)} chars ===")
print("Template OK" if prompt.count("**L") >= 4 else "Missing path entries")

print("\nALL TESTS PASSED" if not failures else f"\n{len(failures)} FAILURES")
