# -*- coding: utf-8 -*-
"""蒙特卡洛前瞻搜索：不只当前题贪心，而是模拟后续所有可能题目路径，
按期望积分选最优选项。

抽题规则（实测校准）：10 题一局，每阶段抽题数 [1,2,3,3,1]
  - 第1题: 10xx 池(4题)   第2-3题: 20xx 池(10题)
  - 第4-6题: 30xx 池(15题) 第7-9题: 40xx 池(7题)  第10题: 50xx 池(5题)
  同阶段池内不放回随机抽题。
"""
import itertools
import json
import os
import random
from solver import QuestionDB, parse_cond, parse_reward_4, predict_success, choose_best

STAGE_POOLS = {'10': '10', '20': '20', '30': '30', '40': '40', '50': '50'}
QUES_PER_STAGE = [1, 2, 3, 3, 1]  # 每阶段抽题数（10题一局，实测校准：10xx×1 20xx×2 30xx×3 40xx×3 50xx×1）

_SHADOW_CACHE = None


def load_shadow_prices():
    """加载各角色属性影子价格（{角色: {base, health, power, ...}}），失败返回 None"""
    global _SHADOW_CACHE
    if _SHADOW_CACHE is not None:
        return _SHADOW_CACHE
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'shadow_prices.json')
    try:
        with open(path, encoding='utf-8') as f:
            _SHADOW_CACHE = json.load(f)
    except Exception:
        _SHADOW_CACHE = None
    return _SHADOW_CACHE


def attr_value_factor(role_name, attrs):
    """把『当前属性』按影子价格折算成等价积分（用于衡量属性现值）。
    属性现值 = 各属性相对基准的超出值 × 影子价格。
    返回 (等价积分加成, 是否可用)"""
    prices = load_shadow_prices()
    if not prices or role_name not in prices:
        return 0.0, False
    p = prices[role_name]
    base = p.get('base', 0.0)
    # 基准属性来自 characters.json，这里只算相对增值
    chars_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'characters.json')
    try:
        with open(chars_path, encoding='utf-8') as f:
            chars = json.load(f)
        base_attrs = chars.get(role_name, {})
    except Exception:
        base_attrs = {}
    val = 0.0
    for a, v in attrs.items():
        if a in base_attrs and a in p:
            val += (v - base_attrs[a]) * p[a]
    return val, True


def stage_of(q):
    """题目 id 前两位 → 阶段序号 0-4"""
    prefix = str(q['id'])[:2]
    return {'10': 0, '20': 1, '30': 2, '40': 3, '50': 4}.get(prefix, 4)


def build_stage_pools(db):
    pools = {}
    for q in db.questions:
        s = stage_of(q)
        pools.setdefault(s, []).append(q)
    return pools


def apply_choice(q, idx, attrs, score, rewards_cache=None):
    """按题库判定选项成败并更新属性/积分，返回 (新attrs, 新score, outcome)"""
    conds = rewards_cache['conds'] if rewards_cache else parse_cond(q)
    suc_rewards = rewards_cache['suc'] if rewards_cache else parse_reward_4(q.get('successRewardT', ''), q.get('successRewardV', ''))
    fail_rewards = rewards_cache['fail'] if rewards_cache else parse_reward_4(q.get('failRewardT', ''), q.get('failRewardV', ''))
    success = predict_success(conds[idx], attrs)
    rewards = suc_rewards[idx] if success else fail_rewards[idx]
    score += int(q.get('integralSuc', 0) if success else q.get('integralFail', 0))
    attrs = dict(attrs)
    for a, d in rewards:
        attrs[a] = attrs.get(a, 0) + d
    return attrs, score, success


def simulate_from(db, pools, start_stage, attrs, score, first_choice=None, first_q=None,
                  rollouts=1000, policy='random', stage_ques=None, start_remaining=None):
    """从 start_stage 起模拟完整剩余局。
    first_choice 指定第一题的选项（外部预选），后续题目按 policy 决策。
    policy: 'random' 后续选项随机（属性总值高不代表合适，随机更客观）；
            'survival' 优先选能成功的选项，全失败选扣健康最少（推荐）；
            'greedy' 按贪心打分。
    start_remaining: start_stage 阶段还剩下的题数（该阶段已答过部分题时用）。
    返回所有 rollout 的最终积分列表（可用于求均值/分布）"""
    stage_ques = stage_ques or QUES_PER_STAGE
    results = []
    for _ in range(rollouts):
        a = dict(attrs)
        s = score
        dead = False
        for st in range(start_stage, 5):
            n_ques = stage_ques[st] if st > start_stage else (start_remaining if start_remaining is not None else stage_ques[st])
            # 该阶段抽 n_ques 题：池内不放回随机（实测：同阶段不会抽到重复题）
            # 中途阶段（start_remaining>0）无法知道已答哪些题，近似从池中随机抽 start_remaining 题
            pool = list(pools[st])
            chosen = random.sample(pool, min(n_ques, len(pool)))
            for _q, q in enumerate(chosen):
                if first_choice is not None and st == start_stage and _q == 0 and first_q is not None:
                    q = first_q
                    idx = first_choice
                else:
                    conds = parse_cond(q)
                    suc = parse_reward_4(q.get('successRewardT', ''), q.get('successRewardV', ''))
                    fail = parse_reward_4(q.get('failRewardT', ''), q.get('failRewardV', ''))
                    cache = {'conds': conds, 'suc': suc, 'fail': fail}
                    if policy == 'greedy':
                        idx, _ = choose_best(q, a)
                    else:
                        idx = pick_rollout_idx(cache, q, a, policy)
                    a, s, _ = apply_choice(q, idx, a, s, cache)
                if a.get('health', 10) <= 0:
                    dead = True
                    break
            if dead:
                break
        results.append(s)
    return results


def generate_seqs(pools, stage, remaining_in_stage, rollouts, exclude=None):
    """生成 rollouts 条『后续题目序列』（每条 = 当前阶段剩余题 + 后续所有阶段题）。
    公共随机数：4 个选项评估时共用这一批序列，把抽题随机性变成共同因子，
    只保留选项不同带来的差异 → 方差大幅降低。

    分层枚举（方差缩减）：41 题全部已知，阶段池固定且每阶段组合数有限
    （C(4,1)=4 / C(10,2)=45 / C(15,3)=455 / C(7,3)=35 / C(5,1)=5），
    每个阶段都枚举『全部可能组合』后均匀抽样拼接——每道题出现频率
    严格等于真实概率，比纯 random.sample 的抽样噪声更低。

    exclude: 本局已答过的题目 id 集合（同阶段真实不抽重复题，模拟时也要排除）。"""
    exclude = exclude or set()
    seqs = []
    # 预计算各阶段全部组合（组合长度 = 该阶段抽题数）
    stage_combos = {}
    rem = remaining_in_stage - 1  # 当前阶段还剩几题（当前题已答）
    if rem > 0 and pools.get(stage):
        cand = [q for q in pools[stage] if q['id'] not in exclude]
        n = min(rem, len(cand))
        stage_combos[stage] = list(itertools.combinations(cand, n)) if n > 0 else []
    for st in range(stage + 1, 5):
        pool = pools.get(st)
        if pool:
            n = min(QUES_PER_STAGE[st], len(pool))
            stage_combos[st] = list(itertools.combinations(pool, n)) if n > 0 else []
    for _ in range(rollouts):
        seq = []
        for st, combos in stage_combos.items():
            if combos:
                seq += list(random.choice(combos))
        seqs.append(seq)
    return seqs


def _build_cache(db):
    """预计算全部 41 题的条件/奖励解析缓存，模拟时直接查表"""
    cache = {}
    for q in db.questions:
        cache[q['id']] = {
            'conds': parse_cond(q),
            'suc': parse_reward_4(q.get('successRewardT', ''), q.get('successRewardV', '')),
            'fail': parse_reward_4(q.get('failRewardT', ''), q.get('failRewardV', '')),
        }
    return cache


def pick_rollout_idx(cache, qq, attrs, policy):
    """rollout 中后续题目的选项选择策略。
    'random'   : 4 选项完全随机（用户偏好：不按属性总值贪心）
    'survival' : 存在能成功的选项时在其中随机挑一个（保持多样性，不贪积分）；
                 全部失败时选失败扣健康最少的选项（保命优先）。
                 理由：成败是确定性判定，随机模拟会把必败项 25% 概率注入，
                 低估『需要后续精细运营才值钱』的选项（专家审查建议）。
    （曾尝试 'survival_w' 属性加权增强，A/B 配对检验 200 局无显著提升，已回退）"""
    conds = cache['conds']
    if policy == 'survival':
        ok = [i for i in range(4) if predict_success(conds[i], attrs)]
        if ok:
            return random.choice(ok)
        fail = cache['fail']
        costs = [sum(d for at, d in fail[i] if at == 'health' and d < 0) for i in range(4)]
        mx = max(costs)  # 负数取 max = 扣健康最少
        return random.choice([i for i in range(4) if costs[i] == mx])
    return random.randrange(4)


def mc_choose(db, pools, q, attrs, score, stage, remaining_in_stage=1, rollouts=1000,
              exclude=None, caches=None, policy='survival'):
    """对当前题 q 的 4 个选项做蒙特卡洛评估（公共随机数版本）：
    预生成 rollouts 条题目序列，每个选项都在同一批序列上模拟到冒险结束，
    取平均最终积分作为期望。返回 (最优选项索引, [(选项, 期望积分, 成功率), ...])
    stage: 当前阶段 0-4；remaining_in_stage: 该阶段还剩的题数（含当前题）
    exclude: 本局已答题 id 集合（同阶段不抽重复题）；caches: 预计算缓存
    policy: 后续题目的模拟策略（'random'/'survival'，默认 survival）"""
    cache = caches if caches is not None else _build_cache(db)
    cur = cache[q['id']]
    # 当前题尚未入 answered_ids，但同阶段不放回，模拟后续题时必须排除当前题
    exclude = (exclude or set()) | {q['id']}

    def apply(qq, idx, a, s):
        c = cache[qq['id']]
        success = predict_success(c['conds'][idx], a)
        rewards = c['suc'][idx] if success else c['fail'][idx]
        s += int(qq.get('integralSuc', 0) if success else qq.get('integralFail', 0))
        a = dict(a)
        for at, d in rewards:
            a[at] = a.get(at, 0) + d
        return a, s, success

    # 公共随机数：预生成一批题目序列（只与 stage/remaining 有关，与选项无关）
    seqs = generate_seqs(pools, stage, remaining_in_stage, rollouts, exclude=exclude)

    evals = []
    for i in range(4):
        a2, s2, success = apply(q, i, dict(attrs), score)
        if a2.get('health', 10) <= 0:
            evals.append((i, s2, 0.0))  # 直接死亡，期望=当前积分
            continue
        if stage + 1 >= 5:
            evals.append((i, s2, 1.0 if success else 0.0))
            continue
        # 同一批序列评估：每条序列内按 policy 选后续选项，到结算页取最终积分
        total = 0
        for seq in seqs:
            a, s = a2, s2
            for qq in seq:
                idx_r = pick_rollout_idx(cache[qq['id']], qq, a, policy)
                a, s, _ = apply(qq, idx_r, a, s)
                if a.get('health', 10) <= 0:
                    break  # 死亡，结算
            total += s
        evals.append((i, total / len(seqs), 1.0 if success else 0.0))
    best = max(evals, key=lambda x: x[1])
    # 平局保护：期望接近时（差 < 0.5）偏好失败不扣健康/健康扣得少的选项
    top_exp = best[1]
    tied = [i for i, e, r in evals if top_exp - e < 0.5]
    if len(tied) > 1:
        health_cost = {}
        for i in tied:
            hc = sum(d for a, d in cache[q['id']]['fail'][i] if a == 'health' and d < 0)
            health_cost[i] = hc
        min_cost = max(health_cost.values())  # 注意 hc 为负数，max = 扣健康最少
        best_tie = [i for i in tied if health_cost[i] == min_cost]
        if best_tie:
            best = (best_tie[0], evals[best_tie[0]][1])
    return best[0], evals


def main():
    db = QuestionDB()
    pools = build_stage_pools(db)
    print('阶段池:', {k: len(v) for k, v in pools.items()})
    # 用玉兔初始属性测一道题
    import json, os
    chars = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'characters.json'), encoding='utf-8'))
    attrs = chars['玉兔']
    print('玉兔属性:', attrs)
    q = db.by_id['1002']
    idx, evals = mc_choose(db, pools, q, attrs, 0, 0, rollouts=1000)
    print('题1002 蒙特卡洛:', idx + 1)
    for i, e, r in evals:
        print('  选项%d %s: 期望积分=%.1f' % (i + 1, q['results'][i], e))
    g, _ = choose_best(q, attrs)
    print('贪心决策: 选项%d' % (g + 1))


if __name__ == '__main__':
    main()
