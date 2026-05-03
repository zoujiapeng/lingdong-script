#!/usr/bin/env python3
"""
Real-time progress bar monitor - displays in a separate terminal window.
Receives progress data from engine.py via TCP and renders live progress bars.
"""
import json, socket, sys, time, os, threading

HOST, PORT = '127.0.0.1', 18765

# Force UTF-8 for Windows GBK consoles
if sys.platform == 'win32':
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    # Enable ANSI escape sequences
    os.system('')


class Display:
    def __init__(self):
        self.nodes = {}
        self._lock = threading.Lock()
        self._start_time = time.time()

    def update(self, node_id, pct=None, label=None, status=None):
        with self._lock:
            n = self.nodes.get(node_id, {
                'pct': 0.0, 'label': '', 'status': 'waiting',
                'start': time.time(), 'last': time.time()
            })
            if label:
                n['label'] = label
            if pct is not None:
                n['pct'] = pct
            if status:
                n['status'] = status
                if status == 'monitoring' and n.get('start') is None:
                    n['start'] = time.time()
            n['last'] = time.time()
            self.nodes[node_id] = n

    def render(self):
        with self._lock:
            nodes = dict(self.nodes)
            total_elapsed = time.time() - self._start_time

        lines = ['', '  +== Real-time Progress Monitor ==+', '']

        if not nodes:
            lines.append('     Waiting for progress signal...')
            lines.append('')
        else:
            for nid, n in nodes.items():
                pct = min(max(n['pct'], 0), 100)
                label = n['label'] or nid
                status = n['status']

                # Status indicators (ASCII safe)
                icons = {
                    'monitoring': '>',
                    'done': 'OK',
                    'waiting': '..',
                    'error': '!!',
                }
                icon = icons.get(status, '??')
                status_colors = {
                    'monitoring': '\033[96m',
                    'done': '\033[92m',
                    'waiting': '\033[93m',
                    'error': '\033[91m',
                }
                color = status_colors.get(status, '')
                reset = '\033[0m'

                # Progress bar using ASCII block chars (these work in most Windows consoles)
                bar_w = 40
                filled = int(pct / 100 * bar_w)
                bar = '#' * filled + '-' * (bar_w - filled)

                # Percentage with color
                if pct >= 95:
                    pct_color = '\033[92m'
                elif pct >= 50:
                    pct_color = '\033[93m'
                else:
                    pct_color = '\033[96m'

                lines.append(f'  {color}{icon}{reset} \033[1m[{nid}]\033[0m {label}')
                lines.append(f'  |{bar}| {pct_color}{pct:6.1f}%{reset}')

                if status == 'monitoring':
                    elapsed = time.time() - n.get('start', n['last'])
                    lines.append(f'   \033[90mElapsed: {elapsed:.1f}s  Status: {color}{status}{reset}\033[0m')
                elif status == 'done':
                    elapsed = n['last'] - n.get('start', n['last'])
                    lines.append(f'   \033[90mDone in: {elapsed:.1f}s\033[0m')
                else:
                    lines.append(f'   \033[90mStatus: {status}\033[0m')
                lines.append('')

        lines.append(f'  \033[90mTotal runtime: {total_elapsed:.1f}s\033[0m')
        lines.append(f'  \033[90mClose this window to stop monitoring\033[0m')
        lines.append('')
        return '\n'.join(lines)


def server_thread(display):
    """TCP server listening for progress updates."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((HOST, PORT))
    except OSError:
        print(f'\033[93mPort {PORT} already in use - another display may be running\033[0m', flush=True)
        sock.close()
        return
    sock.listen(5)
    sock.settimeout(1)

    while True:
        try:
            conn, addr = sock.accept()
            data = conn.recv(4096)
            if data:
                msg = json.loads(data.decode('utf-8'))
                msg_type = msg.get('type', '')
                if msg_type == 'progress':
                    display.update(
                        msg.get('node_id'),
                        pct=msg.get('pct'),
                        label=msg.get('label'),
                        status=msg.get('status')
                    )
                elif msg_type == 'reset':
                    display.nodes.clear()
                elif msg_type == 'shutdown':
                    break
            conn.close()
        except socket.timeout:
            continue
        except (ConnectionError, json.JSONDecodeError):
            continue
        except Exception:
            break
    sock.close()


def main():
    """Main display loop: renders at ~10fps."""
    display = Display()

    print('\033[92m[OK] Progress monitor started\033[0m', flush=True)
    print(f'  \033[90mPort: {PORT}  |  Waiting for engine connection...\033[0m', flush=True)

    t = threading.Thread(target=server_thread, args=(display,), daemon=True)
    t.start()

    last_render = 0
    try:
        while True:
            now = time.time()
            if now - last_render >= 0.1:  # 10 fps max
                os.system('cls' if sys.platform == 'win32' else 'clear')
                print(display.render(), flush=True)
                last_render = now
            time.sleep(0.03)
    except KeyboardInterrupt:
        pass

    print('\n\033[93mProgress monitor closed\033[0m', flush=True)


if __name__ == '__main__':
    main()
