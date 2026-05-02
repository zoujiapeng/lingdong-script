#!/usr/bin/env python3
"""
灵动脚本 - 高级自动化引擎

Standalone entry point. Supports:
- Interactive mode: reads JSONL commands from stdin (compatible with the Electron app)
- DSL mode: run a .flow / .txt file directly
- CLI mode: command-line automation commands
"""
import sys
import json
import time
import threading
import argparse
from datetime import datetime

from core.base import AutomationEngine


def run_interactive(engine: AutomationEngine, debug: bool = False):
    """Run in interactive mode, reading JSONL commands from stdin.

    Compatible with the Electron app's existing protocol.
    """
    engine.add_listener(lambda event, data: _emit_log(event, data))

    def _emit_log(event, data):
        log_entry = {
            "type": "log",
            "level": "info",
            "message": str(data) if data else event,
            "timestamp": datetime.now().isoformat(),
        }
        print(json.dumps(log_entry), flush=True)

    # Notify ready
    print(json.dumps({
        "type": "log",
        "level": "info",
        "message": "Advanced engine ready (v2.0)",
        "timestamp": datetime.now().isoformat(),
    }), flush=True)

    def _stdin_reader():
        try:
            if hasattr(sys.stdin, 'reconfigure'):
                sys.stdin.reconfigure(encoding='utf-8')
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    pass
        except (EOFError, OSError):
            pass

    for msg in _stdin_reader():
        msg_type = msg.get('type', '')

        if msg_type == 'execute':
            payload = msg.get('payload', {})
            script = payload.get('script', {})
            script_type = script.get('type', 'flow')

            if script_type == 'dsl':
                code = script.get('code', '')
                threading.Thread(target=engine.run_dsl, args=(code,), daemon=True).start()
            elif script_type == 'flow':
                # Node-based flow execution (compatible with old engine)
                nodes = script.get('nodes', [])
                edges = script.get('edges', [])
                # Execute via DSL executor with generated code
                _emit_log('info', f'Flow: {len(nodes)} nodes, {len(edges)} edges')
                threading.Thread(target=_execute_flow, args=(engine, nodes, edges), daemon=True).start()
            else:
                _emit_log('info', f'Unknown script type: {script_type}')

        elif msg_type == 'stop':
            engine.stop()
            _emit_log('info', 'Engine stopping...')

        elif msg_type == 'pause':
            engine.pause()
            _emit_log('info', 'Engine paused')

        elif msg_type == 'resume':
            engine.resume()
            _emit_log('info', 'Engine resumed')

        elif msg_type == 'status':
            state = engine.get_engine_state()
            print(json.dumps({
                "type": "status_report",
                **state,
            }), flush=True)

        elif msg_type == 'run_dsl':
            code = msg.get('code', '')
            threading.Thread(target=engine.run_dsl, args=(code,), daemon=True).start()

        elif msg_type == 'click':
            x, y = msg.get('x', 0), msg.get('y', 0)
            engine.click(x, y, human=msg.get('human', False))
            _emit_log('info', f'Click: ({x}, {y})')

        elif msg_type == 'click_text':
            text = msg.get('text', '')
            success = engine.click_text(text)
            _emit_log('info', f'Click text "{text}": {"OK" if success else "FAIL"}')

        elif msg_type == 'type':
            text = msg.get('text', '')
            engine.type_text(text)
            _emit_log('info', f'Type: "{text[:30]}"')

        elif msg_type == 'screenshot':
            import pyautogui
            path = msg.get('path') or f'screenshot_{datetime.now().strftime("%Y%m%d_%H%M%S")}.png'
            pyautogui.screenshot(path)
            _emit_log('info', f'Screenshot saved: {path}')

        elif msg_type == 'detect_text':
            results = engine.ocr.recognize()
            print(json.dumps({
                "type": "detect_result",
                "results": [{"text": t, "confidence": c, "box": b} for t, c, b in results],
            }), flush=True)

        elif msg_type == 'resolve':
            condition = msg.get('condition', '')
            result = engine.dsl_resolver.resolve(condition)
            print(json.dumps({
                "type": "resolve_result",
                "condition": condition,
                "result": result,
            }), flush=True)

    engine._emit('info', 'Engine process exiting')


def _execute_flow(engine: AutomationEngine, nodes: list, edges: list):
    """Execute a node-based flow by translating to DSL."""
    # Find start node
    start_nodes = [n for n in nodes if n.get('type') == 'start']
    if not start_nodes:
        engine._emit('error', 'No start node found')
        return

    # Build node map
    node_map = {n['id']: n for n in nodes}

    # Build edge map: node_id -> [(from_port, to_node_id)]
    edge_map = {}
    for e in edges:
        from_id = e.get('from_node', e.get('from'))
        to_id = e.get('to_node', e.get('to'))
        from_port = e.get('from_port', 'output')
        edge_map.setdefault(from_id, []).append((from_port, to_id))

    # Traverse and execute
    current = start_nodes[0]['id']
    while current and not engine._stopped:
        node = node_map.get(current)
        if not node:
            break

        engine._emit('node_status', {'node_id': current, 'status': 'running'})
        ntype = node.get('type')
        params = node.get('params', {})

        try:
            if ntype == 'click':
                engine.click(params.get('x', 0), params.get('y', 0))
            elif ntype == 'image_click':
                targets = params.get('targets', [])
                for t in targets:
                    engine.click_image(t.get('path', ''))
            elif ntype == 'wait':
                d = params.get('duration', 1)
                engine.wait(d)
            elif ntype == 'input_text':
                engine.type_text(params.get('text', ''))
            elif ntype == 'keyboard':
                import pyautogui
                keys = params.get('keys', [])
                mods = params.get('modifiers', [])
                if mods:
                    pyautogui.hotkey(*mods, *keys)
                else:
                    for k in keys:
                        pyautogui.press(k)
            elif ntype == 'swipe':
                import pyautogui
                sx, sy = params['start_x'], params['start_y']
                ex, ey = params['end_x'], params['end_y']
                pyautogui.drag(ex - sx, ey - sy, duration=params.get('duration', 0.5))
            elif ntype == 'screenshot':
                import pyautogui
                pyautogui.screenshot()
            elif ntype == 'variable_set':
                engine.variables.set(params.get('name', ''), params.get('value', ''))
            elif ntype == 'sub_script':
                pass  # Not implemented in this mode
            elif ntype == 'end':
                break
        except Exception as e:
            engine._emit('error', {'node_id': current, 'error': str(e)})
            break

        # Follow edge
        edges_out = edge_map.get(current, [])
        if not edges_out:
            break
        # For condition nodes, evaluate and pick branch
        if ntype == 'condition':
            cond_type = params.get('condition_type', 'always_true')
            result = 'no'
            if cond_type == 'always_true':
                result = 'yes'
            elif cond_type == 'image_exists':
                found, _, _ = engine.matcher.find_best(
                    template_path=params.get('template_path'),
                    template_b64=params.get('template_base64'),
                    similarity=params.get('similarity', 85) / 100.0 if 'similarity' in params else 0.85
                )
                result = 'yes' if found else 'no'
            elif cond_type == 'window_exists':
                result = 'yes' if engine.window.window_exists(params.get('window_title', '')) else 'no'

            # Find matching edge
            next_node = None
            for port, to_id in edges_out:
                if port == result:
                    next_node = to_id
                    break
            if not next_node:
                next_node = edges_out[0][1]
            current = next_node
        else:
            current = edges_out[0][1]

    engine._emit('info', 'Flow execution complete')


def run_cli(args: argparse.Namespace, engine: AutomationEngine):
    """Run CLI commands."""
    if args.command == 'run':
        if args.file:
            if args.file.endswith('.flow') or args.file.endswith('.txt'):
                engine.run_dsl_file(args.file)
            else:
                # Try as JSON flow
                with open(args.file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if 'nodes' in data:
                    _execute_flow(engine, data.get('nodes', []), data.get('edges', []))
                elif 'code' in data:
                    engine.run_dsl(data['code'])
        elif args.code:
            engine.run_dsl(args.code)

    elif args.command == 'detect':
        if args.ocr:
            results = engine.ocr.recognize()
            for text, conf, box in results:
                print(f"  [{conf:.2f}] {text}  at ({box[0]}, {box[1]}, {box[2]}x{box[3]})")
            print(f"Total: {len(results)} text elements")

        if args.color:
            x, y, w, h = args.region or (0, 0, 100, 100)
            h, s, v = engine.color.get_region_hsv(x, y, w, h)
            print(f"Region HSV: ({h:.1f}, {s:.1f}, {v:.1f})")

    elif args.command == 'wait':
        condition = args.condition
        timeout = args.timeout or 10
        result = engine.wait_until(condition, timeout=timeout)
        print(f"Condition '{condition}': {'MET' if result else 'TIMEOUT'}")

    elif args.command == 'interactive':
        run_interactive(engine, debug=True)


def main():
    parser = argparse.ArgumentParser(
        description='灵动脚本 - 高级自动化引擎 v2.0',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run a DSL script file
  python main.py run my_script.flow

  # Run inline DSL code
  python main.py run -c "click 500 500; wait 1; type hello"

  # Interactive mode (stdin JSONL protocol)
  python main.py interactive

  # Detect text on screen
  python main.py detect --ocr

  # Wait for condition
  python main.py wait "text:领取" --timeout 30

  # Get region color
  python main.py detect --color --region 0 0 100 100
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Command')

    # Run
    run_parser = subparsers.add_parser('run', help='Run a script')
    run_parser.add_argument('file', nargs='?', help='Script file (.flow, .txt, .json)')
    run_parser.add_argument('-c', '--code', help='Inline DSL code')

    # Detect
    detect_parser = subparsers.add_parser('detect', help='Detect elements on screen')
    detect_parser.add_argument('--ocr', action='store_true', help='Detect text')
    detect_parser.add_argument('--color', action='store_true', help='Detect color')
    detect_parser.add_argument('--region', nargs=4, type=int, default=None,
                               metavar=('X', 'Y', 'W', 'H'), help='Region')

    # Wait
    wait_parser = subparsers.add_parser('wait', help='Wait for a condition')
    wait_parser.add_argument('condition', help='Condition (text:, img:, etc.)')
    wait_parser.add_argument('--timeout', type=float, default=10.0, help='Timeout seconds')

    # Interactive
    subparsers.add_parser('interactive', help='Start interactive mode')

    args = parser.parse_args()

    engine = AutomationEngine(debug=(args.command != 'interactive'))

    if args.command == 'run':
        run_cli(args, engine)
    elif args.command == 'detect':
        run_cli(args, engine)
    elif args.command == 'wait':
        run_cli(args, engine)
    elif args.command == 'interactive':
        run_interactive(engine)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
