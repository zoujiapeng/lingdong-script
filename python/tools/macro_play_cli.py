"""
命令行宏回放。
用法: python tools/macro_play_cli.py <macro_name>
"""
import sys, json

sys.path.insert(0, '.')
from features.macro_features import macro_playback

if __name__ == '__main__':
    name = sys.argv[1] if len(sys.argv) > 1 else ''
    result = macro_playback(name)
    print(json.dumps(result))
    sys.stdout.flush()
