# -*- coding: utf-8 -*-
"""框选工具：截取游戏窗口 → 拖拽框选区域 → 保存 regions.json
用法：
  python calibrate.py          框选模式（依次拖框，Enter确认 Esc重画，S保存）
  python calibrate.py --test   调试模式：按 regions.json 对当前画面跑 OCR 并叠加显示
"""
import ctypes
import json
import os
import sys
import time

import numpy as np
import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageGrab

import win32gui
import win32process
import win32con

PROC_NAME = 'zmxy_online.exe'
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
REGIONS_FILE = os.path.join(DATA_DIR, 'regions.json')
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'log')

REGION_NAMES = [
    ('question', '题目文本区'),
    ('opt1', '选项1'),
    ('opt2', '选项2'),
    ('opt3', '选项3'),
    ('opt4', '选项4'),
    ('attr_health', '属性-健康值'),
    ('attr_wisdom', '属性-智慧值'),
    ('attr_charm', '属性-魅力值'),
    ('attr_luck', '属性-运气值'),
    ('attr_smart', '属性-灵巧值'),
    ('attr_power', '属性-战力值'),
    ('result', '结果文本区（成败提示）'),
    ('btn_next', '继续按钮'),
]

REGION_ORDER = [r[0] for r in REGION_NAMES]


def set_dpi_aware():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()


def find_window():
    """按进程名定位窗口（与 bot.py 同逻辑）"""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    psapi.EnumProcesses.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    psapi.EnumProcesses.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    def proc_name(pid):
        h = kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return ''
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
        finally:
            kernel32.CloseHandle(h)
        return ''

    pid_match = None
    pids = (wintypes.DWORD * 8192)()
    count = wintypes.DWORD()
    if psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(count)):
        for i in range(count.value):
            try:
                if proc_name(pids[i]) == PROC_NAME:
                    pid_match = pids[i]
                    break
            except Exception:
                continue
    if not pid_match:
        return None
    hwnd = None

    def enum_cb(h, _):
        nonlocal hwnd
        try:
            _, pid = win32process.GetWindowThreadProcessId(h)
        except Exception:
            return True
        try:
            visible = win32gui.IsWindowVisible(h)
            enabled = win32gui.IsWindowEnabled(h)
        except Exception:
            return True
        if pid == pid_match and visible and enabled:
            hwnd = h
        return True  # 不能返回 False，否则 pywin32 抛'拒绝访问'

    win32gui.EnumWindows(enum_cb, None)
    return hwnd


def get_client_rect(hwnd):
    l, t, r, b = win32gui.GetClientRect(hwnd)
    return r, b


def capture_client(hwnd):
    """截取窗口客户区，返回 (np.ndarray BGR, 客户区宽, 客户区高)；最小化时返回 None"""
    l, t, r, b = win32gui.GetClientRect(hwnd)
    w, h = r - l, b - t
    if w <= 0 or h <= 0:
        return None
    try:
        tl = win32gui.ClientToScreen(hwnd, (0, 0))
    except Exception:
        tl = (0, 0)
    img = ImageGrab.grab(bbox=(tl[0], tl[1], tl[0] + w, tl[1] + h))
    return np.array(img.convert('RGB')), w, h


def wait_window_ready(hwnd, timeout=120):
    """等待游戏窗口非最小化（最小化时 GetClientRect 为 0，截不了图）
    返回 (截图, w, h) 或超时 None"""
    waited = 0
    while waited < timeout:
        shot = capture_client(hwnd)
        if shot is not None:
            return shot
        print('游戏窗口最小化了，请还原窗口后再框选（等待中 %ds/%ds）...' % (waited, timeout), flush=True)
        time.sleep(2)
        waited += 2
    return None


class Calibrator:
    def __init__(self, hwnd, screenshot, win_w, win_h):
        self.hwnd = hwnd
        self.screenshot = screenshot
        self.win_w = win_w
        self.win_h = win_h
        self.regions = {}
        self.load_existing()

        self.root = tk.Tk()
        self.root.title('框选工具 - 生存大冒险')
        # 置顶显示，避免被游戏窗口遮挡
        self.root.attributes('-topmost', True)
        self.root.geometry('+50+50')

        # 截图缩放到屏幕可显示
        scr_w = self.root.winfo_screenwidth() - 120
        scr_h = self.root.winfo_screenheight() - 160
        scale = min(scr_w / win_w, scr_h / win_h, 1.0)
        self.scale = scale
        disp_w, disp_h = int(win_w * scale), int(win_h * scale)

        self.img = Image.fromarray(screenshot).resize((disp_w, disp_h), Image.LANCZOS)
        self.photo = ImageTk_PhotoImage(self.img)

        self.canvas = tk.Canvas(self.root, width=disp_w, height=disp_h, cursor='cross')
        self.canvas.pack(side=tk.LEFT)
        self.canvas.create_image(0, 0, image=self.photo, anchor=tk.NW)
        self.canvas.bind('<ButtonPress-1>', self.on_press)
        self.canvas.bind('<B1-Motion>', self.on_drag)
        self.canvas.bind('<ButtonRelease-1>', self.on_release)

        panel = tk.Frame(self.root)
        panel.pack(side=tk.RIGHT, fill=tk.Y)
        self.info = tk.Label(panel, text='', justify=tk.LEFT, font=('Microsoft YaHei', 10), anchor='n', width=36)
        self.info.pack(padx=8, pady=8, fill=tk.X)
        tk.Button(panel, text='保存并退出 (S)', command=self.save_and_exit, font=('Microsoft YaHei', 11)).pack(padx=8, pady=4, fill=tk.X)
        tk.Button(panel, text='保存后继续框 (A)', command=self.save_only, font=('Microsoft YaHei', 11)).pack(padx=8, pady=4, fill=tk.X)
        tk.Button(panel, text='重新截图 (R)', command=self.re_screenshot, font=('Microsoft YaHei', 11)).pack(padx=8, pady=4, fill=tk.X)
        tk.Button(panel, text='取消当前框 (Esc)', command=self.clear_draft, font=('Microsoft YaHei', 11)).pack(padx=8, pady=4, fill=tk.X)
        tk.Button(panel, text='清空所有区域 (C)', command=self.clear_all, font=('Microsoft YaHei', 11)).pack(padx=8, pady=4, fill=tk.X)

        self.root.bind('<KeyPress>', self.on_key)
        self.root.protocol('WM_DELETE_WINDOW', self.on_close)

        self.draft = None
        self.draft_rect = None
        self.cur_index = 0
        self._done = False
        self.update_info()

    def re_screenshot(self):
        """重新截图：还原并前置游戏窗口 → 截图 → 刷新显示 → 自动最小化游戏让位"""
        try:
            win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
            time.sleep(0.3)
            win32gui.SetForegroundWindow(self.hwnd)
        except Exception:
            pass
        time.sleep(0.6)
        shot = capture_client(self.hwnd)
        if shot is None:
            self.show_status('游戏最小化中，无法重新截图！请还原游戏窗口')
            return
        self.screenshot, self.win_w, self.win_h = shot
        scr_w = self.root.winfo_screenwidth() - 120
        scr_h = self.root.winfo_screenheight() - 160
        scale = min(scr_w / self.win_w, scr_h / self.win_h, 1.0)
        self.scale = scale
        disp_w, disp_h = int(self.win_w * scale), int(self.win_h * scale)
        self.img = Image.fromarray(self.screenshot).resize((disp_w, disp_h), Image.LANCZOS)
        self.photo = ImageTk_PhotoImage(self.img)
        self.canvas.delete('all')
        self.canvas.config(width=disp_w, height=disp_h)
        self.canvas.create_image(0, 0, image=self.photo, anchor=tk.NW)
        self.draw_regions()
        self.show_status('已重新截图，继续框选')
        # 截图后把游戏移到屏幕边缘，让框选窗口可见
        try:
            win32gui.ShowWindow(self.hwnd, win32con.SW_MINIMIZE)
        except Exception:
            pass
        time.sleep(0.2)

    def load_existing(self):
        if os.path.exists(REGIONS_FILE):
            try:
                with open(REGIONS_FILE, encoding='utf-8') as f:
                    self.regions = json.load(f)
                print('已加载已有 regions.json')
            except Exception:
                pass

    # ---- 绘制 ----
    def draw_regions(self):
        self.canvas.delete('region')
        for name, (x, y, w, h) in self.regions.items():
            sx, sy, sw, sh = self._to_disp(x, y, w, h)
            self.canvas.create_rectangle(sx, sy, sx + sw, sy + sh, outline='#00ff00', width=2, tags='region')
            self.canvas.create_text(sx + 3, sy + 2, text=name, anchor='nw', fill='#00ff00', font=('Consolas', 9), tags='region')

    def _to_disp(self, x, y, w, h):
        s = self.scale
        return int(x * s), int(y * s), max(1, int(w * s)), max(1, int(h * s))

    def _to_norm(self, sx, sy, sw, sh):
        s = self.scale
        return (sx / s / self.win_w, sy / s / self.win_h, sw / s / self.win_w, sh / s / self.win_h)

    # ---- 事件 ----
    def on_press(self, e):
        self.draft = (e.x, e.y)

    def on_drag(self, e):
        if self.draft:
            if self.draft_rect:
                self.canvas.delete(self.draft_rect)
            self.draft_rect = self.canvas.create_rectangle(self.draft[0], self.draft[1], e.x, e.y, outline='#ff0000', width=2)

    def on_release(self, e):
        if not self.draft:
            return
        x0, y0 = self.draft
        x1, y1 = e.x, e.y
        self.draft = None
        if self.draft_rect:
            self.canvas.delete(self.draft_rect)
            self.draft_rect = None
        if abs(x1 - x0) < 5 or abs(y1 - y0) < 5:
            return
        sx, sy = min(x0, x1), min(y0, y1)
        sw, sh = abs(x1 - x0), abs(y1 - y0)
        if self.cur_index >= len(REGION_ORDER):
            self.show_status('所有区域已框完，按 S 保存退出')
            return
        name = REGION_ORDER[self.cur_index]
        self.regions[name] = self._to_norm(sx, sy, sw, sh)
        self.cur_index += 1
        self.draw_regions()
        self.update_info()
        self.show_status('已记录 [%s]，继续框 [%s]' % (name, self._next_label()))

    def _next_label(self):
        if self.cur_index < len(REGION_ORDER):
            return '%s（%s）' % (REGION_ORDER[self.cur_index], REGION_NAMES[self.cur_index][1])
        return '完成'

    def update_info(self):
        done = [n for n in REGION_ORDER if n in self.regions]
        pending = [n for n in REGION_ORDER if n not in self.regions]
        txt = '已完成: %d/%d\n' % (len(done), len(REGION_ORDER))
        txt += '✓ ' + ' '.join(done) + '\n\n'
        txt += '待框: ' + ' '.join(pending) + '\n\n'
        txt += '操作: 拖拽画框 | 松手即确认\nEsc重画 | S保存退出 | A保存继续\nR重新截图 | C清空全部'
        self.info.config(text=txt)

    def show_status(self, msg):
        self.root.title('框选工具 - ' + msg)

    def on_key(self, e):
        key = e.keysym.upper()
        if key == 'S':
            self.save_and_exit()
        elif key == 'A':
            self.save_only()
        elif key == 'R':
            self.re_screenshot()
        elif key == 'ESCAPE':
            self.clear_draft()
        elif key == 'C':
            self.clear_all()
        elif key == 'RETURN':
            # 完成当前未确认框？用最后一次拖拽
            pass

    def clear_draft(self):
        if self.regions and self.cur_index > 0:
            name = REGION_ORDER[self.cur_index - 1]
            if name in self.regions:
                del self.regions[name]
                self.cur_index -= 1
                self.draw_regions()
                self.update_info()
                self.show_status('已撤销 [%s]' % name)

    def clear_all(self):
        if messagebox.askyesno('确认', '清空所有已框区域？'):
            self.regions = {}
            self.cur_index = 0
            self.canvas.delete('region')
            self.update_info()
            self.show_status('已清空，从头开始')

    def save_only(self):
        with open(REGIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.regions, f, ensure_ascii=False, indent=2)
        self.show_status('已保存 %d 个区域' % len(self.regions))

    def save_and_exit(self):
        self.save_only()
        self._done = True
        self.root.destroy()

    def on_close(self):
        if messagebox.askyesno('确认', '退出？未保存的框选将丢失。'):
            self._done = True
            self.root.destroy()

    def run(self):
        print('框选窗口已打开（置顶）。如果看不到，请按 Alt+Tab 切换，或先最小化游戏窗口。')
        print('操作：拖拽画框（松手即确认）| Esc 撤销 | S 保存退出')
        self.root.mainloop()
        return self.regions if not self._done or os.path.exists(REGIONS_FILE) else {}


def ocr_test():
    """--test 模式：按 regions.json 对当前画面 OCR 并保存标注图"""
    set_dpi_aware()
    from PIL import ImageDraw
    hwnd = find_window()
    if not hwnd:
        print('未找到游戏窗口')
        return
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(REGIONS_FILE, encoding='utf-8') as f:
        regions = json.load(f)
    result = wait_window_ready(hwnd)
    if result is None:
        print('游戏窗口最小化，无法截图')
        return
    shot, w, h = result
    print('窗口客户区: %dx%d' % (w, h))

    from ocr_engine import ocr_region_lines
    img = Image.fromarray(shot).convert('RGB')
    draw = ImageDraw.Draw(img)
    results = {}
    for name, region in regions.items():
        txt = ocr_region_lines(shot, region, normalized=True, win_size=(w, h))
        lines = [(t, cy, cx) for t, cy, cx in txt]
        results[name] = ' | '.join(t for t, _, _ in lines)
        print('[%s] %s' % (name, results[name]))
        # 叠加标注
        x = int(region[0] * w)
        y = int(region[1] * h)
        rw = int(region[2] * w)
        rh = int(region[3] * h)
        draw.rectangle([x, y, x + rw, y + rh], outline='red', width=2)
        draw.text((x + 3, y + 2), name, fill='red')
    save_path = os.path.join(LOG_DIR, 'ocr_test_%s.png' % time.strftime('%H%M%S'))
    img.save(save_path)
    print('标注图已保存: %s' % save_path)


def main():
    set_dpi_aware()
    print('【本程序完全免费】如果你是通过付费购买的，请立即退款举报！谨防倒卖诈骗。')
    if '--test' in sys.argv:
        ocr_test()
        return
    hwnd = find_window()
    if not hwnd:
        print('未找到游戏窗口 %s，请先启动游戏' % PROC_NAME)
        return
    result = wait_window_ready(hwnd)
    if result is None:
        print('等待游戏窗口还原超时（120s），已退出')
        return
    shot, w, h = result
    print('窗口客户区: %dx%d' % (w, h))
    global ImageTk_PhotoImage
    from PIL import ImageTk
    ImageTk_PhotoImage = ImageTk.PhotoImage
    cal = Calibrator(hwnd, shot, w, h)
    regions = cal.run()
    print('最终保存: %d 个区域' % len(regions))


if __name__ == '__main__':
    main()
