# -*- coding: utf-8 -*-
"""实时状态小窗：显示当前积分 + 六属性 + 最近决策，供与游戏内核对。
整个 tkinter 生命周期在独立线程内运行（tkinter 不允许跨线程操作），
主程序通过 update() 往队列投递消息，UI 线程消费刷新。"""
import queue
import threading
import tkinter as tk

ATTR_CN = {'health': '健康', 'wisdom': '智慧', 'charm': '魅力', 'luck': '运气', 'smart': '灵巧', 'power': '战力'}


class StatusUI:
    def __init__(self):
        self._q = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready = threading.Event()
        # 等待 UI 就绪
        self._ready.wait(timeout=5)

    def _run(self):
        root = tk.Tk()
        root.title('生存大冒险 bot 状态')
        root.attributes('-topmost', True)
        root.geometry('+40+40')
        labels = {}
        for key in ['score', 'attrs', 'last_q', 'decision', 'pred', 'actual']:
            lbl = tk.Label(root, text='', justify=tk.LEFT, font=('Microsoft YaHei', 11), anchor='w')
            lbl.pack(fill=tk.X, padx=10, pady=1)
            labels[key] = lbl
        self._labels = labels
        self._root = root
        self._ready.set()
        while True:
            try:
                item = self._q.get(timeout=0.2)
            except queue.Empty:
                try:
                    root.update()
                except Exception:
                    return
                continue
            if item == 'STOP':
                try:
                    root.destroy()
                except Exception:
                    pass
                return
            if item == 'HIDE':
                try:
                    root.withdraw()
                except Exception:
                    pass
                try:
                    root.update()
                except Exception:
                    pass
                continue
            if item == 'SHOW':
                try:
                    root.deiconify()
                    root.attributes('-topmost', True)
                except Exception:
                    pass
                try:
                    root.update()
                except Exception:
                    pass
                continue
            score, attrs, last_q, decision, pred, actual = item
            labels['score'].config(text='积分: %s' % score)
            if attrs:
                txt = ' '.join('%s%s' % (ATTR_CN.get(k, k), v) for k, v in attrs.items())
            else:
                txt = '(未读取)'
            labels['attrs'].config(text='属性: ' + txt)
            labels['last_q'].config(text='题目: ' + last_q)
            labels['decision'].config(text='选择: ' + decision)
            labels['pred'].config(text='预测: ' + pred)
            labels['actual'].config(text='实际: ' + actual)
            try:
                root.update()
            except Exception:
                return

    def update(self, score, attrs, last_q, decision, pred, actual):
        self._q.put((score, attrs, last_q, decision, pred, actual))

    def hide(self):
        """隐藏窗口（截图/点击前调用，避免遮挡游戏）"""
        self._q.put('HIDE')

    def show(self):
        self._q.put('SHOW')

    def stop(self):
        self._q.put('STOP')
