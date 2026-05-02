"""Tests for DSL parser, executor, and condition resolver."""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from dsl.parser import DSLParser, ASTNode
from dsl.resolver import ConditionResolver
from logic.variables import VariableSystem
from core.screenshot import ScreenshotManager


class TestDSLParser(unittest.TestCase):
    def setUp(self):
        self.parser = DSLParser()

    def test_parse_simple_click(self):
        ast = self.parser.parse("click 500 500")
        self.assertEqual(ast.node_type, 'program')
        self.assertEqual(len(ast.children), 1)
        self.assertEqual(ast.children[0].node_type, 'click')
        self.assertEqual(ast.children[0].x, 500)
        self.assertEqual(ast.children[0].y, 500)

    def test_parse_click_with_offset(self):
        ast = self.parser.parse("click 500 500 offset: 10 20")
        click_node = ast.children[0]
        self.assertEqual(click_node.offset_x, 10)
        self.assertEqual(click_node.offset_y, 20)

    def test_parse_click_double(self):
        ast = self.parser.parse("click 500 500 double")
        self.assertEqual(ast.children[0].clicks, 2)

    def test_parse_type(self):
        ast = self.parser.parse('type "hello world"')
        self.assertEqual(ast.children[0].node_type, 'input_text')
        self.assertEqual(ast.children[0].text, 'hello world')

    def test_parse_type_single_quotes(self):
        ast = self.parser.parse("type 'test'")
        self.assertEqual(ast.children[0].text, 'test')

    def test_parse_wait(self):
        ast = self.parser.parse("wait 2.5")
        self.assertEqual(ast.children[0].node_type, 'wait')
        self.assertEqual(ast.children[0].duration, 2.5)

    def test_parse_wait_random(self):
        ast = self.parser.parse("wait 0.5 - 1.5")
        self.assertEqual(ast.children[0].duration_min, 0.5)
        self.assertEqual(ast.children[0].duration_max, 1.5)

    def test_parse_keyboard(self):
        ast = self.parser.parse("key ctrl+c")
        self.assertEqual(ast.children[0].node_type, 'keyboard')

    def test_parse_keyboard_with_plus(self):
        ast = self.parser.parse("key ctrl+shift+z")
        self.assertEqual(ast.children[0].node_type, 'keyboard')

    def test_parse_swipe(self):
        ast = self.parser.parse("swipe 100 200 300 400")
        self.assertEqual(ast.children[0].node_type, 'swipe')
        self.assertEqual(ast.children[0].x1, 100)

    def test_parse_screenshot(self):
        ast = self.parser.parse("screenshot")
        self.assertEqual(ast.children[0].node_type, 'screenshot')

    def test_parse_screenshot_path(self):
        ast = self.parser.parse('screenshot "test.png"')
        self.assertEqual(ast.children[0].path, 'test.png')

    def test_parse_variable_set(self):
        ast = self.parser.parse("set counter = 5")
        self.assertEqual(ast.children[0].node_type, 'variable_set')
        self.assertEqual(ast.children[0].name, 'counter')
        self.assertEqual(ast.children[0].value, '5')

    def test_parse_variable_set_string(self):
        ast = self.parser.parse('set name = "hello"')
        self.assertEqual(ast.children[0].name, 'name')
        self.assertEqual(ast.children[0].value, '"hello"')

    def test_parse_loop_count(self):
        ast = self.parser.parse("LOOP 3:\n    click 500 500")
        loop = ast.children[0]
        self.assertEqual(loop.node_type, 'loop')
        self.assertEqual(loop.count, 3)
        self.assertEqual(len(loop.body), 1)

    def test_parse_loop_body(self):
        ast = self.parser.parse("LOOP 2:\n    click 500 500\n    wait 1")
        self.assertEqual(len(ast.children[0].body), 2)

    def test_parse_loop_nested(self):
        ast = self.parser.parse("LOOP 2:\n    LOOP 3:\n        click 500 500")
        outer = ast.children[0]
        self.assertEqual(outer.count, 2)
        self.assertEqual(outer.body[0].node_type, 'loop')
        self.assertEqual(outer.body[0].count, 3)

    def test_parse_loop_infinite(self):
        ast = self.parser.parse("LOOP inf:\n    click 500 500")
        self.assertEqual(ast.children[0].count, -1)

    def test_parse_if(self):
        ast = self.parser.parse('IF text:领取:\n    click 500 500')
        if_node = ast.children[0]
        self.assertEqual(if_node.node_type, 'if')
        self.assertEqual(if_node.condition, 'text:领取')

    def test_parse_if_else(self):
        ast = self.parser.parse('IF text:领取:\n    click 500 500\nELSE:\n    wait 1')
        if_node = ast.children[0]
        self.assertEqual(len(if_node.body), 1)
        self.assertTrue(hasattr(if_node, 'else_body'))
        self.assertEqual(len(if_node.else_body), 1)

    def test_parse_while(self):
        ast = self.parser.parse('WHILE text:loading:\n    wait 0.5')
        while_node = ast.children[0]
        self.assertEqual(while_node.node_type, 'while')
        self.assertEqual(while_node.condition, 'text:loading')

    def test_parse_until(self):
        ast = self.parser.parse('UNTIL text:完成:\n    click 500 500')
        until_node = ast.children[0]
        self.assertEqual(until_node.node_type, 'until')

    def test_parse_loop_while(self):
        ast = self.parser.parse('LOOP WHILE text:战斗:\n    click 500 500')
        loop = ast.children[0]
        self.assertEqual(loop.node_type, 'loop')
        self.assertEqual(loop.condition, 'text:战斗')

    def test_parse_multi_statement(self):
        code = """click 100 100
wait 0.5
click 200 200"""
        ast = self.parser.parse(code)
        self.assertEqual(len(ast.children), 3)

    def test_parse_comments(self):
        code = """# This is a comment
click 500 500
// Also a comment
wait 1"""
        ast = self.parser.parse(code)
        self.assertEqual(len(ast.children), 2)

    def test_parse_empty(self):
        ast = self.parser.parse("")
        self.assertEqual(len(ast.children), 0)

    def test_parse_image_click(self):
        ast = self.parser.parse('clickImage "button.png"')
        self.assertEqual(ast.children[0].node_type, 'image_click')
        self.assertEqual(ast.children[0].path, 'button.png')

    def test_parse_scroll(self):
        ast = self.parser.parse("scroll -3")
        self.assertEqual(ast.children[0].node_type, 'scroll')

    def test_parse_delay(self):
        ast = self.parser.parse("delay 0.5")
        self.assertEqual(ast.children[0].node_type, 'delay')

    def test_parse_call(self):
        ast = self.parser.parse('call "sub_script.flow"')
        self.assertEqual(ast.children[0].node_type, 'sub_script')

    def test_format_ast(self):
        ast = self.parser.parse("click 500 500\nwait 1")
        formatted = self.parser.format_ast(ast)
        self.assertIn('click', formatted)
        self.assertIn('wait', formatted)


class TestConditionResolver(unittest.TestCase):
    def setUp(self):
        self.screenshot = ScreenshotManager()
        self.variables = VariableSystem()
        self.variables.set('score', '100')
        self.variables.set('name', 'test')
        self.resolver = ConditionResolver(
            self.screenshot, variables=self.variables
        )

    def test_resolve_true(self):
        self.assertTrue(self.resolver.resolve("true"))
        self.assertTrue(self.resolver.resolve("True"))
        self.assertTrue(self.resolver.resolve("TRUE"))

    def test_resolve_false(self):
        self.assertFalse(self.resolver.resolve("false"))

    def test_resolve_variable_equals(self):
        self.assertTrue(self.resolver.resolve("var:score == 100"))
        self.assertFalse(self.resolver.resolve("var:score == 50"))

    def test_resolve_variable_gt(self):
        self.assertTrue(self.resolver.resolve("var:score > 50"))
        self.assertFalse(self.resolver.resolve("var:score > 200"))

    def test_resolve_variable_lt(self):
        self.assertTrue(self.resolver.resolve("var:score < 200"))
        self.assertFalse(self.resolver.resolve("var:score < 50"))

    def test_resolve_variable_exists(self):
        self.assertTrue(self.resolver.resolve("var:name"))
        self.assertFalse(self.resolver.resolve("var:nonexistent"))

    def test_resolve_not(self):
        self.assertTrue(self.resolver.resolve("not:false"))
        self.assertFalse(self.resolver.resolve("not:true"))

    def test_resolve_and(self):
        self.assertTrue(self.resolver.resolve("and:true,true"))
        self.assertFalse(self.resolver.resolve("and:true,false"))

    def test_resolve_or(self):
        self.assertTrue(self.resolver.resolve("or:true,false"))
        self.assertFalse(self.resolver.resolve("or:false,false"))


class TestVariableSystem(unittest.TestCase):
    def setUp(self):
        self.vars = VariableSystem()

    def test_set_get(self):
        self.vars.set('x', 10)
        self.assertEqual(self.vars.get('x'), 10)

    def test_get_default(self):
        self.assertEqual(self.vars.get('nonexistent', 'default'), 'default')

    def test_has(self):
        self.vars.set('x', 1)
        self.assertTrue(self.vars.has('x'))
        self.assertFalse(self.vars.has('y'))

    def test_delete(self):
        self.vars.set('x', 1)
        self.vars.delete('x')
        self.assertFalse(self.vars.has('x'))

    def test_export_import(self):
        self.vars.set('a', 1)
        self.vars.set('b', 'hello')
        data = self.vars.export_dict()
        self.assertEqual(data['a'], 1)
        self.assertEqual(data['b'], 'hello')

        v2 = VariableSystem()
        v2.import_dict(data)
        self.assertEqual(v2.get('a'), 1)

    def test_interpolate_simple(self):
        self.vars.set('name', 'world')
        result = self.vars.interpolate('hello {{name}}')
        self.assertEqual(result, 'hello world')

    def test_interpolate_default(self):
        result = self.vars.interpolate('{{missing|default_val}}')
        self.assertEqual(result, 'default_val')

    def test_interpolate_no_var(self):
        result = self.vars.interpolate('plain text')
        self.assertEqual(result, 'plain text')

    def test_interpolate_multiple(self):
        self.vars.set('a', '1')
        self.vars.set('b', '2')
        result = self.vars.interpolate('{{a}} + {{b}} = 3')
        self.assertEqual(result, '1 + 2 = 3')

    def test_readonly(self):
        self.vars.set('x', 1, readonly=True)
        with self.assertRaises(ValueError):
            self.vars.set('x', 2)
        with self.assertRaises(ValueError):
            self.vars.delete('x')

    def test_clear(self):
        self.vars.set('a', 1)
        self.vars.clear()
        self.assertFalse(self.vars.has('a'))

    def test_to_number(self):
        self.vars.set('x', '42')
        self.assertEqual(self.vars.to_number('x'), 42.0)
        self.assertIsNone(self.vars.to_number('nonexistent'))

    def test_to_bool_true(self):
        self.vars.set('a', True)
        self.vars.set('b', 'true')
        self.vars.set('c', '1')
        self.assertTrue(self.vars.to_bool('a'))
        self.assertTrue(self.vars.to_bool('b'))
        self.assertTrue(self.vars.to_bool('c'))

    def test_to_bool_false(self):
        self.vars.set('a', False)
        self.vars.set('b', '')
        self.assertFalse(self.vars.to_bool('a'))
        self.assertFalse(self.vars.to_bool('b'))

    def test_evaluate_condition(self):
        self.vars.set('x', '10')
        self.assertTrue(self.vars.evaluate_condition('x', '==', '10'))
        self.assertFalse(self.vars.evaluate_condition('x', '>', '20'))
        self.assertTrue(self.vars.evaluate_condition('x', '<', '20'))
        self.assertTrue(self.vars.evaluate_condition('x', 'exists'))
        self.assertFalse(self.vars.evaluate_condition('y', 'exists'))

    def test_keys_and_items(self):
        self.vars.set('a', 1)
        self.vars.set('b', 2)
        self.assertIn('a', self.vars.keys())
        self.assertIn('b', self.vars.keys())
        items = dict(self.vars.items())
        self.assertEqual(items['a'], 1)
        self.assertEqual(items['b'], 2)


if __name__ == '__main__':
    unittest.main()
