# -*- coding: utf-8 -*-
"""题库加载、文本模糊匹配、最优选项决策"""
import json
import os
import re

from rapidfuzz import fuzz

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
QUESTIONS_FILE = os.path.join(DATA_DIR, 'questions.json')

ATTR_CN = {'health': '健康', 'wisdom': '智慧', 'charm': '魅力', 'luck': '运气', 'smart': '灵巧', 'power': '战力'}


class QuestionDB:
    def __init__(self, path=QUESTIONS_FILE):
        with open(path, encoding='utf-8') as f:
            self.questions = json.load(f)
        self.by_id = {q['id']: q for q in self.questions}

    def match_title(self, text):
        """模糊匹配题目标题，返回 (题目, 分数) 或 None"""
        if not text:
            return None
        best = None
        best_score = 0
        for q in self.questions:
            score = fuzz.partial_ratio(text, q['title'])
            if score > best_score:
                best_score = score
                best = q
        return (best, best_score) if best_score >= 60 else None

    def match_option(self, q, text):
        """匹配题目 q 的选项文本，返回 (索引0-3, 分数) 或 None"""
        if not text:
            return None
        best_i = -1
        best_score = 0
        for i in range(4):
            opt = q['results'][i]
            score = fuzz.partial_ratio(text, opt)
            if score > best_score:
                best_score = score
                best_i = i
        return (best_i, best_score) if best_score >= 55 else None


def parse_cond(q):
    """把 cond 解析成 4 个选项各自的判定条件列表 [(attr, val), ...]
    成功判定：属性 >= 阈值。health=1 视为必成功，health=99 视为必失败。"""
    conds = []
    raw = q.get('cond') or []
    for pair in raw:
        ats = pair[0].split(';')
        vs = pair[1].split(';')
        cond = []
        for i, at in enumerate(ats):
            val = int(vs[i]) if i < len(vs) else int(vs[-1])
            cond.append((at, val))
        conds.append(cond)
    while len(conds) < 4:
        conds.append([])
    return conds


def parse_reward_4(t_str, v_str):
    """successRewardT/V 分号分隔 4 组，返回 [(attr, delta), ...] x4"""
    groups = t_str.split(';') if t_str else [''] * 4
    vals = v_str.split(';') if v_str else ['0'] * 4
    out = []
    for i in range(4):
        ts = groups[i].split(',') if i < len(groups) else []
        vs = vals[i].split(',') if i < len(vals) else []
        pairs = []
        for j, t in enumerate(ts):
            if t == '-1' or t == '':
                continue
            try:
                v = int(vs[j]) if j < len(vs) else 0
            except ValueError:
                v = 0
            pairs.append((t, v))
        out.append(pairs)
    return out


def predict_success(cond, attrs):
    """判断选项在给定六属性下是否成功。
    规则：所有 (attr, val) 条件中，健康=1 恒真（必成功占位），健康=99 恒假（必失败占位），
    其余要求 attrs[attr] >= val。条件为空视为恒真。"""
    for attr, val in cond:
        if val == 1 and attr == 'health':
            continue
        if val == 99 and attr == 'health':
            return False
        if attr in ('health', 'wisdom', 'charm', 'luck', 'smart', 'power'):
            cur = attrs.get(attr, 0)
            if cur < val:
                return False
    return True


def choose_best(q, attrs):
    """给定题目和六属性，返回最优选项索引 (0-3)。
    打分 = 成功积分*10 + 成功属性正收益 - 失败扣健康惩罚（健康低时加重）"""
    conds = parse_cond(q)
    suc_rewards = parse_reward_4(q.get('successRewardT', ''), q.get('successRewardV', ''))
    fail_rewards = parse_reward_4(q.get('failRewardT', ''), q.get('failRewardV', ''))

    def attr_sum(pairs):
        return sum(v for _, v in pairs)

    best_i = 0
    best_score = None
    reasons = []
    for i in range(4):
        success = predict_success(conds[i], attrs)
        score = 0
        if success:
            score += int(q.get('integralSuc', 0)) * 10 + attr_sum(suc_rewards[i])
        else:
            score += int(q.get('integralFail', 0)) * 10 + attr_sum(fail_rewards[i])
        # 健康风险惩罚：若失败会扣健康，且健康已很低
        health_delta = 0
        for _, v in (suc_rewards[i] if success else fail_rewards[i]):
            if v < 0:
                health_delta += v
        health = attrs.get('health', 10)
        if health_delta < 0 and health + health_delta <= 0:
            score -= 500  # 会死，强烈避免
        elif health_delta < 0 and health <= 3:
            score -= 100  # 健康低，谨慎
        if best_score is None or score > best_score:
            best_score = score
            best_i = i
        reasons.append((i + 1, success, score))
    return best_i, reasons


def attrs_from_text(text):
    """从属性面板 OCR 文本解析六属性 {attr: int}
    支持格式：'健康5 智慧3 ...' 或 '5 3 4 2 ...'（按顺序）"""
    attrs = {}
    order = ['health', 'wisdom', 'charm', 'luck', 'smart', 'power']
    label_pat = {cn: en for en, cn in ATTR_CN.items()}
    # 带标签格式：健康5 / 健康：5 / 健康 5
    for cn, en in label_pat.items():
        m = re.search(cn + r'[：: ]*(\d+)', text)
        if m:
            attrs[en] = int(m.group(1))
    if len(attrs) >= 6:
        return attrs
    # 纯数字格式：按顺序取 6 个数字
    nums = re.findall(r'\d+', text)
    if len(nums) >= 6:
        for i, en in enumerate(order):
            if en not in attrs:
                attrs[en] = int(nums[i])
    return attrs


if __name__ == '__main__':
    db = QuestionDB()
    print('题库:', len(db.questions), '题')
    m = db.match_title('冒险前，你决定到商店买些必需品，但是钱不太够，你会_')
    print('匹配:', m[0]['id'], m[0]['title'], 'score=', m[1])
    attrs = {'health': 5, 'wisdom': 4, 'charm': 3, 'luck': 4, 'smart': 4, 'power': 4}
    best, reasons = choose_best(m[0], attrs)
    print('最优选项:', best + 1, reasons)
    print('attrs_from_text:', attrs_from_text('健康5 智慧3 魅力4 运气2 灵巧5 战力3'))
