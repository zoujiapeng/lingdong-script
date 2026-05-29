"""
模板质量验证器 — 检查裁剪小图是否准确可用。
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'python'))
import cv2
import numpy as np

def verify_template(image_path: str, min_size: int = 10, max_size: int = 200) -> dict:
    """验证单张模板图片质量。返回 {valid, reason, info}。"""
    img = cv2.imread(image_path)
    if img is None:
        return {"valid": False, "reason": "无法读取"}

    h, w = img.shape[:2]
    info = {"w": w, "h": h, "channels": img.shape[2] if len(img.shape) > 2 else 1}

    # 拒绝超大图
    if w > max_size or h > max_size:
        return {"valid": False, "reason": f"太大 ({w}x{h}) 不像是按钮/图标", "info": info}

    # 拒绝过小图
    if w < min_size or h < min_size:
        return {"valid": False, "reason": f"太小 ({w}x{h}) 无法做模板匹配", "info": info}

    # 拒绝纯色/近似纯色图
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) > 2 else img
    std = gray.std()
    if std < 3:
        return {"valid": False, "reason": f"纯色/低纹理 (std={std:.1f})", "info": info}

    # 拒绝全白/全黑
    mean = gray.mean()
    if mean > 250 or mean < 5:
        return {"valid": False, "reason": f"全白或全黑 (mean={mean:.0f})", "info": info}

    return {"valid": True, "reason": "通过", "info": info}


def verify_directory(dir_path: str) -> list[dict]:
    """验证目录下所有模板。"""
    results = []
    for fname in sorted(os.listdir(dir_path)):
        fpath = os.path.join(dir_path, fname)
        if not fname.lower().endswith(('.png', '.jpg', '.jpeg')):
            continue
        r = verify_template(fpath)
        r["file"] = fname
        results.append(r)
    return results


if __name__ == "__main__":
    # 检查本次运行的经验库模板
    exp_dir = os.path.join(os.path.dirname(__file__), 'python', 'experience', 'templates')
    demo_dir = os.path.join(os.path.dirname(__file__), 'vision_qq_demo', 'templates')

    for label, d in [("经验库", exp_dir), ("演示裁剪", demo_dir)]:
        if os.path.isdir(d):
            results = verify_directory(d)
            bad = [r for r in results if not r["valid"]]
            good = [r for r in results if r["valid"]]
            print(f"\n{label} ({d}):")
            print(f"  总计: {len(results)}, 合格: {len(good)}, 不合格: {len(bad)}")
            for r in bad:
                print(f"  [✗] {r['file']}: {r['reason']} ({r.get('info', {}).get('w','?')}x{r.get('info', {}).get('h','?')})")
            for r in good:
                print(f"  [✓] {r['file']}: {r['info']['w']}x{r['info']['h']}")
        else:
            print(f"\n{label}: 目录不存在")
