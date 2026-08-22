# -*- coding: utf-8 -*-
"""事后 oracle 对比：bot 决策 vs 已知整局题序的精确最优解（金标准回归工具）。

原理：对一局实际抽到的 10 题序列做精确后向 DP（dp[i][属性] = 最优后续积分），
得到"开天眼"的理论最优分；与 bot 策略（事前决策）在同题序上对比。
gap 由三部分构成：抽题随机性的信息差 + survival-first 启发式 vs 最优策略 + rollouts 估计噪声。
改 mc_sim 决策逻辑后跑一遍本工具，可量化回归。

用法：
  python sim_oracle.py [局数] [rollouts]
"""
import json
import os
import random
import statistics
import sys
import time
from functools import lru_cache

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from solver import QuestionDB, predict_success
from mc_sim import build_stage_pools, QUES_PER_STAGE, mc_choose, _build_cache

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')


def draw_seq(pools, seed):
    """按真实抽题规则生成一局题序（阶段池不放回随机）"""
    random.seed(seed)
    seq, answered = [], set()
    for stage in range(5):
        cand = [q for q in pools[stage] if q['id'] not in answered]
        for q in random.sample(cand, QUES_PER_STAGE[stage]):
            seq.append(q)
            answered.add(q['id'])
    return seq


def oracle_score(seq, cache, init_attrs):
    """精确后向 DP：已知整局题序的最优积分（clairvoyant optimal）。

    属性状态以 dict 存储，lru_cache 按元组合并重复状态；实际可达状态远小于笛卡尔积。
    健康归 0 立即结算（该题得分后终止，后续题不得分）。
    """
    n = len(seq)
    info = [(q, cache[q['id']]) for q in seq]

    @lru_cache(maxsize=None)
    def rec(i, at_tuple):
        if i >= n:
            return 0
        attrs = dict(at_tuple)
        q, c = info[i]
        best = -1
        for idx in range(4):
            success = predict_success(c['conds'][idx], attrs)
            rewards = c['suc'][idx] if success else c['fail'][idx]
            gain = int(q.get('integralSuc', 0) if success else q.get('integralFail', 0))
            na = dict(attrs)
            for at, d in rewards:
                na[at] = na.get(at, 0) + d
            if na.get('health', 10) <= 0:
                val = gain
            else:
                val = gain + rec(i + 1, tuple(sorted(na.items())))
            best = max(best, val)
        return best

    init = tuple(sorted(dict(init_attrs).items()))
    return rec(0, init)


def bot_score(seq, db, pools, cache, init_attrs, rollouts=500):
    """bot 策略在同题序上的得分（与 bot.py 决策逻辑一致）"""
    attrs = dict(init_attrs)
    score = 0
    answered = set()
    for i, q in enumerate(seq):
        stage = int(q['id'][0]) - 1
        idx, _, _ = mc_choose(db, pools, q, attrs, score, stage,
                              remaining_in_stage=QUES_PER_STAGE[stage] - i % QUES_PER_STAGE[stage],
                              rollouts=rollouts, exclude=answered, caches=cache, week_prior=0)
        answered.add(q['id'])
        c = cache[q['id']]
        success = predict_success(c['conds'][idx], attrs)
        rewards = c['suc'][idx] if success else c['fail'][idx]
        score += int(q.get('integralSuc', 0) if success else q.get('integralFail', 0))
        for at, d in rewards:
            attrs[at] = attrs.get(at, 0) + d
        if attrs.get('health', 10) <= 0:
            break
    return score


def main():
    n_games = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    rollouts = int(sys.argv[2]) if len(sys.argv) > 2 else 500
    t0 = time.time()
    db = QuestionDB()
    pools = build_stage_pools(db)
    cache = _build_cache(db)
    chars = json.load(open(os.path.join(DATA_DIR, 'characters.json'), encoding='utf-8'))
    init_attrs = chars['玉兔']

    print('同题序对比（%d 局, rollouts=%d）' % (n_games, rollouts))
    bot_scores, orc_scores = [], []
    for seed in range(n_games):
        seq = draw_seq(pools, seed)
        bot_scores.append(bot_score(seq, db, pools, cache, init_attrs, rollouts))
        orc_scores.append(oracle_score(seq, cache, init_attrs))

    gaps = [o - b for o, b in zip(orc_scores, bot_scores)]
    tied = sum(1 for g in gaps if g == 0)
    print('bot 均分: %.1f | oracle 均分: %.1f | 平均 gap: %.1f (%.1f%%)' % (
        sum(bot_scores) / n_games, sum(orc_scores) / n_games,
        sum(gaps) / n_games, sum(gaps) / sum(orc_scores) * 100))
    q = statistics.quantiles(gaps, n=4)
    print('gap 分布: 中位 %.1f, p25 %.1f, p75 %.1f, 最大 %d' % (
        statistics.median(gaps), q[0], q[2], max(gaps)))
    print('bot 达到 oracle 最优分的比例: %.0f%%（%d/%d 局，耗时 %.1fs）' % (
        tied / n_games * 100, tied, n_games, time.time() - t0))


if __name__ == '__main__':
    main()
