# -*- coding: utf-8 -*-
"""RapidOCR 封装：截图裁剪 + 预处理 + 识别"""
import numpy as np
from rapidocr_onnxruntime import RapidOCR

_ocr = None


def get_engine():
    global _ocr
    if _ocr is None:
        _ocr = RapidOCR()
    return _ocr


def ocr_image(img_np, scale=2, keep_ratio=False):
    """识别图片中的文字，返回 [(box, text, score), ...]"""
    if scale != 1:
        h, w = img_np.shape[:2]
        img_np = np.ascontiguousarray(cv_resize(img_np, (w * scale, h * scale)))
    engine = get_engine()
    res, _ = engine(img_np)
    if not res:
        return []
    return [(r[0], r[1], r[2]) for r in res]


def cv_resize(img_np, size):
    """图像缩放（PIL 实现，替代 opencv 以减小打包体积）"""
    from PIL import Image
    img = Image.fromarray(img_np)
    img = img.resize(size, Image.BICUBIC)
    return np.array(img)


def crop_region(screenshot_np, region, normalized=True, win_size=(0, 0)):
    """按区域裁剪。region: (x, y, w, h)，若 normalized 则坐标为 0~1 的相对值"""
    h_img, w_img = screenshot_np.shape[:2]
    if normalized:
        x = int(region[0] * w_img)
        y = int(region[1] * h_img)
        w = int(region[2] * w_img)
        h = int(region[3] * h_img)
    else:
        x, y, w, h = region
    x = max(0, min(x, w_img - 1))
    y = max(0, min(y, h_img - 1))
    w = max(1, min(w, w_img - x))
    h = max(1, min(h, h_img - y))
    return screenshot_np[y:y + h, x:x + w]


def ocr_region(screenshot_np, region, normalized=True, win_size=(0, 0), scale=2):
    """识别指定区域的文字，返回拼接后的文本字符串"""
    crop = crop_region(screenshot_np, region, normalized, win_size)
    items = ocr_image(crop, scale=scale)
    if not items:
        return ''
    # 按纵向位置排序后拼接
    lines = sorted(items, key=lambda it: (it[0][0][1], it[0][0][0]))
    return ''.join(t for _, t, _ in lines)


def ocr_region_lines(screenshot_np, region, normalized=True, win_size=(0, 0), scale=2):
    """识别指定区域，按行返回 [(text, center_y, center_x), ...]
    center_x/y 为裁剪图内坐标（已按 scale 还原），窗口坐标需调用方加区域偏移"""
    crop = crop_region(screenshot_np, region, normalized, win_size)
    items = ocr_image(crop, scale=scale)
    lines = sorted(items, key=lambda it: (it[0][0][1], it[0][0][0]))
    out = []
    for box, text, score in lines:
        cy = (box[0][1] + box[2][1]) / 2 / scale
        cx = (box[0][0] + box[2][0]) / 2 / scale
        out.append((text, cy, cx))
    return out


def preprocess_white_text(img_np):
    """美术字预处理（已弃用，保留占位）"""
    return img_np
