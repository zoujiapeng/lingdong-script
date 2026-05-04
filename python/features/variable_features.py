"""
变量功能注册
set_variable / get_variable — 全局变量读写
"""
from features.registry import feature, P, TC, FeatureCategory as F


@feature(
    name="set_variable",
    display_name="设置变量",
    description="【DSL·变量】设置全局变量为指定值。存储的变量可在后续操作中引用，适用于传递数据、保存状态、动态参数",
    category=F.CONTROL,
    params=[
        P("name", "str", "变量名", example="user_name"),
        P("value", "str", "变量值", required=False, default="", example="admin"),
    ],
    returns="dict{name, value, previous} - 变量名、新值、旧值",
    dsl_keyword="SET",
    dsl_template="SET {name} {value}",
    flow_type="variable_set",
    examples=["SET user_name admin", "SET retry_count 3"],
    test_cases=[
        TC("set_and_get", {"name": "test_var", "value": "hello"}, "success",
           validator=lambda r: r.get("success") is True),
    ],
    tags=["variable", "dsl", "control"],
)
def set_variable(name: str, value: str = "") -> dict:
    from features.state import set_var, get_var
    previous = get_var(name)
    set_var(name, value)
    return {"name": name, "value": value, "previous": previous}


@feature(
    name="get_variable",
    display_name="读取变量",
    description="【DSL·变量】读取全局变量的当前值。用于获取之前保存的状态或参数",
    category=F.CONTROL,
    params=[
        P("name", "str", "变量名", example="user_name"),
        P("default", "str", "变量不存在时返回的默认值", required=False, default=""),
    ],
    returns="str - 变量值（或默认值）",
    dsl_keyword=None,
    examples=["get_variable user_name"],
    tags=["variable", "dsl", "control"],
)
def get_variable(name: str, default: str = "") -> str:
    from features.state import get_var
    return get_var(name, default)
