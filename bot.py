# -*- coding: utf-8 -*-
"""生存大冒险自动答题 bot
用法：
  python bot.py            运行（需要先手动开始冒险并处于题目界面）
  python bot.py --dry-run  只 OCR+决策，不点击（验证用）
"""
import ctypes
import json
import os
import random
import sys
import threading
import time
import traceback

import numpy as np

import win32api
import win32con
import win32gui
import win32process
from PIL import Image, ImageGrab

from solver import QuestionDB, parse_cond, parse_reward_4, predict_success, attrs_from_text, ATTR_CN
from ocr_engine import ocr_region_lines

PROC_NAME = 'zmxy_online.exe'
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
REGIONS_FILE = os.path.join(DATA_DIR, 'regions.json')
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'log')

MAX_MISS = 3          # 连续 OCR/匹配失败上限，超过则停止
CLICK_JITTER = 3      # 点击坐标抖动 ±px
MIN_DELAY = 0.8       # 动作间最小延迟
MAX_DELAY = 1.8
ATTR_ORDER = ['health', 'wisdom', 'charm', 'luck', 'smart', 'power']


def set_dpi_aware():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()


def find_window():
    """按进程名定位窗口：QueryFullProcessImageNameW 拿 exe 名 → 匹配 PID → 枚举窗口"""
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
        h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
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
    max_pids = 8192
    pids = (wintypes.DWORD * max_pids)()
    count = wintypes.DWORD()
    if psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(count)):
        n = count.value
        for i in range(n):
            pid = pids[i]
            try:
                if proc_name(pid) == PROC_NAME:
                    pid_match = pid
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
        return True  # 不能返回 False：pywin32 会因 GetLastError 残留抛'拒绝访问'异常

    win32gui.EnumWindows(enum_cb, None)
    return hwnd


def capture_client(hwnd, retries=3):
    """截取窗口客户区，返回 (np.ndarray BGR, 客户区宽, 客户区高)。
    ImageGrab 在提权/显卡切换时偶发失败，重试 + 退避。"""
    import time
    for attempt in range(retries):
        l, t, r, b = win32gui.GetClientRect(hwnd)
        w, h = r - l, b - t
        if w <= 0 or h <= 0:
            return None
        try:
            tl = win32gui.ClientToScreen(hwnd, (0, 0))
        except Exception:
            tl = (0, 0)
        try:
            img = ImageGrab.grab(bbox=(tl[0], tl[1], tl[0] + w, tl[1] + h))
            return np.array(img.convert('RGB')), w, h
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))
            else:
                raise
    return None


def click_client(hwnd, cx, cy, win_w, win_h):
    """在窗口客户区内点击 (cx, cy)（客户区像素坐标）"""
    try:
        tl = win32gui.ClientToScreen(hwnd, (0, 0))
    except Exception:
        tl = (0, 0)
    sx = tl[0] + int(cx) + random.randint(-CLICK_JITTER, CLICK_JITTER)
    sy = tl[1] + int(cy) + random.randint(-CLICK_JITTER, CLICK_JITTER)
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.25)
    win32api.SetCursorPos((sx, sy))
    time.sleep(0.15 + random.random() * 0.2)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.08 + random.random() * 0.1)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def region_center(region, win_w, win_h):
    return region[0] * win_w + region[2] * win_w / 2, region[1] * win_h + region[3] * win_h / 2


def human_delay():
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


class Bot:
    def __init__(self, dry_run=False, use_mc=False, role_name=None):
        self.dry_run = dry_run
        self.use_mc = use_mc
        self.role_name = role_name  # 角色名（影子价格用，如 --role 小唐）
        self.db = QuestionDB()
        with open(REGIONS_FILE, encoding='utf-8') as f:
            self.regions = json.load(f)
        self.score = 0
        self.attrs = None
        self.hwnd = None
        self.win_w = 0
        self.win_h = 0
        self.last_q_id = None
        self.last_idx = None
        self.last_outcome = None
        self.answered = 0          # 已实际答题数（用于推断当前阶段）
        self.pools = None          # 蒙特卡洛阶段池缓存
        self.rem_answers = 1       # 当前阶段剩余题数（含当前题）
        self.mc_caches = None      # 蒙特卡洛题目缓存（41题条件/奖励解析）
        self.answered_ids = set()  # 本局已答题 id（模拟时同阶段排除，防重复抽题）

    def _match_text(self, a, b):
        from rapidfuzz import fuzz
        return fuzz.partial_ratio(a, b) >= 45

    def _last_ref_text(self):
        """上一题的结果文本（成功或失败），用于残留检测"""
        if self.last_q_id is None or self.last_idx is None or self.last_outcome is None:
            return None
        q = self.db.by_id.get(self.last_q_id)
        if not q:
            return None
        key = 'suc' if self.last_outcome == 'success' else 'fail'
        texts = q.get(key) or []
        return texts[self.last_idx] if self.last_idx < len(texts) else None

    # ---------- 识别 ----------
    def shot(self):
        # 截图前确保游戏在前台，避免遮挡污染截图
        self._prep_game()
        shot, w, h = capture_client(self.hwnd)
        self.win_w, self.win_h = w, h
        return shot

    def _prep_game(self):
        """把游戏窗口拉到前台（截图/点击前调用）。
        用 AttachThreadInput 技巧绕过 Windows 的前台切换限制（SetForegroundWindow 从后台进程调用会被拒）。"""
        try:
            win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
            time.sleep(0.15)
            # 前台切换技巧：把自己的输入线程挂到目标窗口线程，再 SetForegroundWindow
            try:
                fg = win32gui.GetForegroundWindow()
                fg_tid = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
                target_tid = win32process.GetWindowThreadProcessId(self.hwnd)[0]
                cur_tid = win32api.GetCurrentThreadId()
                attached = False
                if fg_tid and fg_tid != target_tid and fg_tid != cur_tid:
                    if win32process.AttachThreadInput(cur_tid, fg_tid, True):
                        attached = True
                if not win32gui.SetForegroundWindow(self.hwnd):
                    win32gui.BringWindowToTop(self.hwnd)
                if attached:
                    win32process.AttachThreadInput(cur_tid, fg_tid, False)
            except Exception:
                try:
                    win32gui.SetForegroundWindow(self.hwnd)
                except Exception:
                    pass
            time.sleep(0.25)
        except Exception:
            pass

    def _restore_ui(self):
        pass

    def ocr_region(self, shot, name, scale=2):
        if name not in self.regions:
            return []
        return ocr_region_lines(shot, self.regions[name], normalized=True, win_size=(self.win_w, self.win_h), scale=scale)

    def read_question(self, shot):
        items = self.ocr_region(shot, 'question')
        text = ''.join(t for t, _, _ in items)
        return text.strip()

    def read_options(self, shot):
        """返回 4 个选项的 [(text, click_x, click_y), ...]，
        click_x/y 为窗口客户区坐标（OCR 文本行中心，失败时 None）"""
        opts = []
        for i in range(1, 5):
            name = 'opt%d' % i
            items = self.ocr_region(shot, name)
            text = ''.join(t for t, _, _ in items).strip()
            click = None
            if items:
                r = self.regions[name]
                cx = sum(cx for _, _, cx in items) / len(items) + r[0] * self.win_w
                cy = sum(cy for _, cy, _ in items) / len(items) + r[1] * self.win_h
                click = (cx, cy)
            opts.append((text, click))
        return opts

    def read_attrs(self, shot):
        import re
        attrs = {}
        for en in ATTR_ORDER:
            name = 'attr_' + en
            items = self.ocr_region(shot, name)
            text = ''.join(t for t, _, _ in items).strip()
            nums = [int(x) for x in re.findall(r'\d+', text)]
            if nums:
                attrs[en] = nums[0]
        return attrs if len(attrs) >= 3 else None

    def read_result(self, shot):
        items = self.ocr_region(shot, 'result')
        text = ''.join(t for t, _, _ in items).strip()
        return text

    def read_result_anywhere(self, shot):
        """整图 OCR 拼接文本（按行序），结果页弹窗位置未知时兜底用"""
        from ocr_engine import ocr_image
        items = ocr_image(shot, scale=1)
        lines = sorted(items, key=lambda it: (it[0][0][1], it[0][0][0]))
        return ' '.join(t for _, t, _ in lines)

    # ---------- 决策 ----------
    def current_stage(self):
        """按已答题数推断当前阶段 0-4（10 题一局，每阶段 [1,2,3,3,1] 题）"""
        from mc_sim import QUES_PER_STAGE
        cum = 0
        for s, n in enumerate(QUES_PER_STAGE):
            if self.answered < cum + n:
                return s
            cum += n
        return 4

    def decide(self, q, opt_texts):
        """返回 (选项索引0-3, reasons/evals)"""
        if self.use_mc:
            from mc_sim import mc_choose, build_stage_pools, _build_cache
            if self.pools is None:
                self.pools = build_stage_pools(self.db)
            if self.mc_caches is None:
                self.mc_caches = _build_cache(self.db)
            stage = self.current_stage()
            idx, evals = mc_choose(self.db, self.pools, q, self.attrs or {}, self.score,
                                   stage, remaining_in_stage=self.rem_answers, rollouts=1000,
                                   exclude=self.answered_ids, caches=self.mc_caches)
            return idx, evals
        best, reasons = choose_with_opts(self.db, q, self.attrs, opt_texts)
        return best, reasons

    # ---------- 结果判定 ----------
    def judge_result(self, q, idx, result_text):
        """用结果文本匹配 suc/fail，返回 'success'/'fail'/None"""
        from rapidfuzz import fuzz
        suc_text = q['suc'][idx]
        fail_text = q['fail'][idx]
        if not result_text:
            return None
        s_score = fuzz.partial_ratio(result_text, suc_text)
        f_score = fuzz.partial_ratio(result_text, fail_text)
        if s_score >= 45 and s_score > f_score:
            return 'success'
        if f_score >= 45 and f_score > s_score:
            return 'fail'
        return None

    def judge_by_attr_diff(self, q, idx, attrs_before, attrs_after):
        """按属性面板数字变化判定成败：结果页弹出后属性已变化，
        与题库奖励对照，匹配成功或失败奖励组的返回对应结果。"""
        if not attrs_before or not attrs_after:
            return None
        diff = {}
        for k, v in attrs_after.items():
            if k in attrs_before:
                d = v - attrs_before[k]
                if d:
                    diff[k] = d
        if not diff:
            return None
        suc_rewards = parse_reward_4(q.get('successRewardT', ''), q.get('successRewardV', ''))
        fail_rewards = parse_reward_4(q.get('failRewardT', ''), q.get('failRewardV', ''))

        def matches(rewards):
            r = {}
            for a, d in rewards[idx]:
                if d:
                    r[a] = d
            return r == diff

        if matches(suc_rewards):
            return 'success'
        if matches(fail_rewards):
            return 'fail'
        return None

    def apply_reward(self, q, idx, outcome):
        rewards = parse_reward_4(q.get('successRewardT', ''), q.get('successRewardV', ''))
        if outcome == 'fail':
            rewards = parse_reward_4(q.get('failRewardT', ''), q.get('failRewardV', ''))
            self.score += int(q.get('integralFail', 0))
        else:
            self.score += int(q.get('integralSuc', 0))
        if self.attrs is None:
            self.attrs = {k: 0 for k in ATTR_ORDER}
        for attr, delta in rewards[idx]:
            self.attrs[attr] = self.attrs.get(attr, 0) + delta

    # ---------- 主循环 ----------
    def run(self):
        hwnd = find_window()
        if not hwnd:
            print('未找到游戏窗口，请先启动游戏')
            return
        self.hwnd = hwnd
        print('窗口已找到，开始运行（Ctrl+C 停止）')

        self._settlement_done = False

        miss = 0
        round_no = 0
        err_rounds = 0
        try:
            while True:
                round_no += 1
                try:
                    miss = self._one_round(round_no)
                    if self._settlement_done:
                        print('已到结算页，冒险结束，程序退出。')
                        break
                    if miss >= MAX_MISS:
                        print('连续 %d 次未识别到有效题目，判定冒险结束或界面异常，退出。' % MAX_MISS)
                        break
                except Exception:
                    err_rounds += 1
                    traceback.print_exc()
                    print('[%d] 本轮异常，已跳过 (连续异常 %d/3)' % (round_no, err_rounds))
                    if err_rounds >= 3:
                        print('连续 3 轮异常，停止。')
                        break
                    time.sleep(2)
                    continue
                err_rounds = 0
        except KeyboardInterrupt:
            print('\n用户中断')
        except Exception:
            traceback.print_exc()
            print('发生异常，已停止。详见上方 traceback。')
        finally:
            self.save_log()
            print('退出。最终积分=%s 属性=%s' % (self.score, self.attrs))

    def check_settlement(self, shot):
        """结算页检测：整图 OCR 找结算页关键词。
        实测结算页文本形如：'冒险失败为0，生存大冒险中获得62积分，高于或等于上轮的0积分'
        返回 True 表示已到结算页（冒险结束），调用方应停止。"""
        import re
        from ocr_engine import ocr_image
        items = ocr_image(shot, scale=1)
        text = ' '.join(t for _, t, _ in items)
        # 精准特征：获得X积分 / 高于或等于 / 冒险失败为 / 领取奖励 / 上轮
        if re.search(r'获得\s*\d+\s*积分', text) or '高于或等于' in text or '冒险失败' in text or '领取奖励' in text or '上轮' in text:
            print('[结算] 检测到结算页文本: %r，冒险结束' % text[:60])
            return True
        return False

    def _one_round(self, round_no):
        """单轮完整流程：识别题目 → 匹配 → 读选项/属性 → 决策 → 点击 → 判结果 → 点继续。
        任何异常由调用方 run() 捕获。"""
        shot = self.shot()
        # 结算页检测（中途运行或健康归零触发结算）
        if self.check_settlement(shot):
            self._settlement_done = True
            return 1
        q_text = self.read_question(shot)
        if not q_text:
            print('[%d] 题目区无文字 (miss)' % round_no)
            time.sleep(2)
            return 1

        # 残留结果页清理：上一轮点继续可能未生效，结果页还开着。
        # 此时题目区文字不变但选项区会读到结果文本，先点继续关掉它。
        if self.last_q_id is not None and not self.dry_run:
            same_q = False
            try:
                m = self.db.match_title(q_text)
                same_q = m is not None and m[0]['id'] == self.last_q_id
            except Exception:
                pass
            if same_q and self.last_outcome is not None:
                ref = self._last_ref_text()
                opt_items = [t for t, _ in self.read_options(shot)]
                res_txt = self.read_result(shot)
                stale = False
                if res_txt and ref and self._match_text(res_txt, ref):
                    stale = True
                if not stale:
                    # 选项区出现上一题结果文本也视为残留
                    for t in opt_items:
                        if ref and t and self._match_text(t, ref):
                            stale = True
                            break
                if stale:
                    print('[%d] 检测到残留结果页，先点击继续清理' % round_no)
                    if 'btn_next' in self.regions:
                        cx, cy = region_center(self.regions['btn_next'], self.win_w, self.win_h)
                        self._prep_game()
                        click_client(self.hwnd, cx, cy, self.win_w, self.win_h)
                        self._restore_ui()
                    time.sleep(2.5)
                    shot = self.shot()
                    q_text = self.read_question(shot)
                    if not q_text:
                        print('[%d] 清理后题目区无文字，等待下一轮' % round_no)
                        return 1

        matched = self.db.match_title(q_text)
        if not matched:
            print('[%d] 题目无法匹配: %r' % (round_no, q_text))
            time.sleep(2)
            return 1
        q, score_q = matched
        print('[%d] 题目: %s (匹配度 %d)' % (round_no, q['title'], score_q))

        # 读选项（校验顺序）
        opt_texts = self.read_options(shot)
        print('[%d] 选项OCR: %s' % (round_no, [t for t, _ in opt_texts]))

        # 首轮读属性
        if self.attrs is None:
            self.attrs = self.read_attrs(shot)
            if self.attrs:
                print('[%d] 属性OCR: %s' % (round_no, self.attrs))
            else:
                print('[%d] 警告: 属性区识别失败，使用推算值 %s' % (round_no, self.attrs))

        idx, reasons = self.decide(q, [t for t, _ in opt_texts])
        choice = q['results'][idx] if idx < 4 else '?'
        if self.use_mc:
            evl = ' | '.join('opt%d:%.0f' % (i + 1, e) for i, e, _ in reasons)
            print('[%d] 决策(MC): 选项%d %s  (期望积分: %s)' % (round_no, idx + 1, choice, evl))
        else:
            print('[%d] 决策: 选项%d %s  (打分明细: %s)' % (round_no, idx + 1, choice, reasons))

        if self.dry_run:
            print('[DRY] 不点击，等待 5 秒继续...')
            time.sleep(5)
            return 0

        # 点击选项（自动前置游戏+隐藏状态窗）
        if idx < 4:
            name = 'opt%d' % (idx + 1)
            # 优先用 OCR 文本行中心（框选偏大也不怕），失败回退区域中心
            ocr_click = opt_texts[idx][1]
            if ocr_click:
                cx, cy = ocr_click
            else:
                cx, cy = region_center(self.regions[name], self.win_w, self.win_h)
            self._prep_game()
            click_client(self.hwnd, cx, cy, self.win_w, self.win_h)
            self._restore_ui()
            print('[%d] 已点击选项%d' % (round_no, idx + 1))
        human_delay()

        # 等待结果页并判定（三层判定：区域文本 → 属性面板变化 → 整图文本 → 预测兜底）
        outcome = None
        result_seen = False
        attrs_before = dict(self.attrs) if self.attrs else None
        for _ in range(10):
            time.sleep(1.5)
            try:
                shot = self.shot()
            except Exception as e:
                print('[%d] 截图失败(重试中): %s' % (round_no, e))
                time.sleep(1)
                continue
            # 0) 结算页检测（最后一题后可能直接进结算）
            if self.check_settlement(shot):
                self._settlement_done = True
                outcome = 'success' if predict_success(parse_cond(q)[idx], self.attrs or {}) else 'fail'
                self.apply_reward(q, idx, outcome)
                self.last_q_id = q['id']
                self.last_idx = idx
                self.last_outcome = outcome
                print('[%d] 结果: %s (积分+%s)，随后进入结算页' % (round_no, outcome, q.get('integralSuc' if outcome == 'success' else 'integralFail')))
                print('[%d] 当前积分=%s 属性=%s' % (round_no, self.score, self.attrs))
                return 0
            # 1) 区域文本
            res_text = self.read_result(shot)
            if res_text:
                result_seen = True
                outcome = self.judge_result(q, idx, res_text)
                if outcome:
                    break
            # 2) 属性面板变化（结果页弹出后属性数字已变）
            if not outcome:
                attrs_now = self.read_attrs(shot)
                if attrs_now and attrs_before:
                    diff_outcome = self.judge_by_attr_diff(q, idx, attrs_before, attrs_now)
                    if diff_outcome:
                        result_seen = True
                        outcome = diff_outcome
                        break
            # 3) 整图文本兜底
            if not outcome:
                whole = self.read_result_anywhere(shot)
                if whole:
                    result_seen = True
                    outcome = self.judge_result(q, idx, whole)
                    if outcome:
                        break
        if outcome is None:
            if not result_seen:
                print('[%d] 警告: 结果页始终未出现，按预测处理（可能点击未生效）' % round_no)
            else:
                print('[%d] 警告: 结果文本/属性变化无法判定成败，按预测处理' % round_no)
            outcome = 'success' if predict_success(parse_cond(q)[idx], self.attrs or {}) else 'fail'
        print('[%d] 结果: %s (积分+%s)' % (round_no, outcome, q.get('integralSuc' if outcome == 'success' else 'integralFail')))
        self.apply_reward(q, idx, outcome)
        self.answered += 1
        self.answered_ids.add(q['id'])
        self.rem_answers -= 1
        if self.rem_answers <= 0:
            from mc_sim import QUES_PER_STAGE
            self.rem_answers = QUES_PER_STAGE[self.current_stage()] if self.current_stage() < 5 else 1
        self.last_q_id = q['id']
        self.last_idx = idx
        self.last_outcome = outcome
        print('[%d] 当前积分=%s 属性=%s' % (round_no, self.score, self.attrs))

        # 点击继续按钮
        if 'btn_next' in self.regions:
            cx, cy = region_center(self.regions['btn_next'], self.win_w, self.win_h)
            self._prep_game()
            click_client(self.hwnd, cx, cy, self.win_w, self.win_h)
            self._restore_ui()
            print('[%d] 已点击继续' % round_no)
        human_delay()
        return 0

    def save_log(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        path = os.path.join(LOG_DIR, 'run_%s.log' % time.strftime('%Y%m%d_%H%M%S'))
        with open(path, 'w', encoding='utf-8') as f:
            f.write('score=%s attrs=%s\n' % (self.score, self.attrs))
        print('日志: %s' % path)


def choose_with_opts(db, q, attrs, opt_texts):
    """带选项文本校验的决策：若 OCR 选项与题库选项能对齐则用题库决策，
    否则按题库 1-4 顺序决策"""
    conds = parse_cond(q)
    suc_rewards = parse_reward_4(q.get('successRewardT', ''), q.get('successRewardV', ''))
    fail_rewards = parse_reward_4(q.get('failRewardT', ''), q.get('failRewardV', ''))
    attrs = attrs or {}

    def attr_sum(pairs):
        return sum(v for _, v in pairs)

    # 选项文本对齐：OCR 第 i 个区域实际是题库哪个选项？
    mapping = []
    for i, txt in enumerate(opt_texts):
        if txt:
            m = db.match_option(q, txt)
            mapping.append(m[0] if m else i)
        else:
            mapping.append(i)

    best_i = 0
    best_score = None
    reasons = []
    for i in range(4):
        # 使用 OCR 对齐后的题库索引计算打分
        db_idx = mapping[i] if i < len(mapping) else i
        success = predict_success(conds[db_idx], attrs)
        score = 0
        if success:
            score += int(q.get('integralSuc', 0)) * 10 + attr_sum(suc_rewards[db_idx])
        else:
            score += int(q.get('integralFail', 0)) * 10 + attr_sum(fail_rewards[db_idx])
        health_delta = 0
        for a, v in (suc_rewards[db_idx] if success else fail_rewards[db_idx]):
            if a == 'health' and v < 0:
                health_delta += v
        health = attrs.get('health', 10)
        if health_delta < 0 and health + health_delta <= 0:
            score -= 500
        elif health_delta < 0 and health <= 3:
            score -= 100
        if best_score is None or score > best_score:
            best_score = score
            best_i = i
        reasons.append((i + 1, db_idx + 1, success, score))
    return best_i, reasons


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def ensure_admin():
    """游戏以管理员权限运行，bot 必须同级才能注入鼠标（UIPI 限制）。
    非管理员时用 runas 提权重启自己。"""
    if is_admin():
        return True
    print('需要管理员权限（游戏以管理员运行），正在请求提升，请在弹出的 UAC 对话框中点"是"...')
    try:
        script = os.path.abspath(sys.argv[0])
        args = ' '.join('"%s"' % a if ' ' in a else a for a in sys.argv[1:])
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, 'runas', sys.executable, '"%s" %s' % (script, args), os.path.dirname(script), 1)
        if ret > 32:
            return False  # 已启动提权副本，本进程退出
    except Exception as e:
        print('提权失败: %s' % e)
    return False


def main():
    set_dpi_aware()
    if not ensure_admin():
        print('请以管理员身份重新运行（UAC 弹窗点"是"后自动继续）。')
        return
    dry_run = '--dry-run' in sys.argv
    use_mc = '--mc' in sys.argv
    role_name = None
    if '--role' in sys.argv:
        i = sys.argv.index('--role')
        if i + 1 < len(sys.argv):
            role_name = sys.argv[i + 1]

    # 运行日志：stdout/stderr 同时写文件（cmd 最小化也能查）
    class Tee:
        def __init__(self, *streams):
            self.streams = streams

        def write(self, s):
            for st in self.streams:
                try:
                    st.write(s)
                except Exception:
                    pass

        def flush(self):
            for st in self.streams:
                try:
                    st.flush()
                except Exception:
                    pass

    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, 'console_%s.log' % time.strftime('%Y%m%d_%H%M%S'))
    logf = open(log_path, 'w', encoding='utf-8')
    sys.stdout = Tee(sys.stdout, logf)
    sys.stderr = Tee(sys.stderr, logf)

    bot = Bot(dry_run=dry_run, use_mc=use_mc, role_name=role_name)
    print('=' * 50)
    print('生存大冒险自动答题 bot (%s)' % ('DRY-RUN 验证模式' if dry_run else '真实运行模式'))
    if role_name:
        print('角色: %s（启用属性影子价格）' % role_name)
    print('请确保: 已手动进入答题界面、窗口在前台、regions.json 已配置')
    print('运行日志: %s' % log_path)
    print('=' * 50)
    bot.run()
    logf.close()


if __name__ == '__main__':
    main()
