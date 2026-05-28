"""
MIMO Agent 完整实操：视觉操控 + 每步截屏 + 提取小图
"""
import sys, os, json, time, base64

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'python'))
os.environ['OPENAI_API_BASE'] = 'https://api.xiaomimimo.com/v1'
os.environ['OPENAI_API_KEY'] = 'REDACTED_SILICONFLOW_KEY_2'
os.environ['VLM_MODEL'] = 'mimo-v2-omni'
os.environ['LLM_MULTIMODAL'] = 'true'

import cv2
from perception.vision import ScreenCapture

SAVE_DIR = os.path.join(os.path.dirname(__file__), 'vision_demo2')
os.makedirs(SAVE_DIR, exist_ok=True)
os.makedirs(os.path.join(SAVE_DIR, 'templates'), exist_ok=True)

step_screenshots = []
click_points = []

def save_screenshot(name):
    img = ScreenCapture.capture()
    path = os.path.join(SAVE_DIR, name)
    cv2.imwrite(path, img)
    step_screenshots.append({'name': name, 'path': path, 'img': img})
    return img, path

def crop_templates():
    """从所有截图中，在点击位置周围裁剪小图。"""
    for sp in click_points:
        x, y, w, h, label, step = sp['x'], sp['y'], sp['w'], sp['h'], sp['label'], sp['step']
        # 用点击前的那张截图裁剪
        before_img = None
        for s in step_screenshots:
            if f'step{step}_before' in s['name']:
                before_img = s['img']
                break
        if before_img is None:
            continue
        hi, wi = before_img.shape[:2]
        pad = 15
        x1 = max(0, x - pad)
        y1 = max(0, y - pad)
        x2 = min(wi, x + w + pad)
        y2 = min(hi, y + h + pad)
        crop = before_img[y1:y2, x1:x2]
        if crop.size > 0:
            fname = f'step{step}_{label[:20]}_{x}_{y}.png'
            path = os.path.join(SAVE_DIR, 'templates', fname)
            cv2.imwrite(path, crop)
            print(f'  [小图] {fname} ({crop.shape[1]}x{crop.shape[0]})')

# ── 运行 Agent ──
from agent.mimo_agent import MimoAgent

agent = MimoAgent()

def event_cb(data):
    t = data.get('type', '')
    if t == 'step_complete':
        sn = data.get('step_num', 0)
        tool = data.get('tool', '')
        status = data.get('status', '')
        args = data.get('args', {})
        result = data.get('result', {})

        # 每步执行前截屏
        save_screenshot(f'step{sn}_{tool}_before.png')

        ok = 'OK' if status == 'completed' else 'FAIL'
        print(f'  [{ok}] Step {sn}: {tool}')
        print(f'        args: {json.dumps(args, ensure_ascii=False)[:120]}')
        if status == 'completed':
            # 如果是点击类操作，记录点击位置
            if 'x' in args and 'y' in args:
                click_points.append({
                    'step': sn, 'label': tool,
                    'x': int(args.get('x', 0)), 'y': int(args.get('y', 0)),
                    'w': 20, 'h': 20,
                })
        else:
            err = result.get('error', '')
            if err:
                print(f'        error: {err[:100]}')

        # 执行后截屏
        time.sleep(0.5)
        save_screenshot(f'step{sn}_{tool}_after.png')

    elif t == 'log':
        msg = data.get('message', '')
        if len(msg) > 120:
            msg = msg[:120] + '...'
        print(f'  [MIMO] {msg}')

agent._event_cb = event_cb

print('=' * 60)
print('MIMO Agent 开始执行...')
print('=' * 60)

result = agent.run('打开记事本, 输入 "hello from mimo", 保存到桌面')

print()
print('=' * 60)
print('执行结果')
print('=' * 60)
print(f'成功: {result.get("success")}')
print(f'步数: {result.get("total_steps")}')
print(f'耗时: {result.get("elapsed_ms")}ms')
print(f'总结: {result.get("summary", "")[:200]}')

print()
print('截图:', len([s for s in step_screenshots if os.path.exists(s['path'])]), '张')
print('提取小图:')
crop_templates()
print(f'小图目录: {os.path.join(SAVE_DIR, "templates")}')

# 保存日志
with open(os.path.join(SAVE_DIR, 'result.json'), 'w', encoding='utf-8') as f:
    json.dump({
        'result': result,
        'screenshots': [s['name'] for s in step_screenshots],
        'click_points': click_points,
    }, f, ensure_ascii=False, indent=2)
print(f'结果已保存到: {SAVE_DIR}')
