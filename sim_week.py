# -*- coding: utf-8 -*-
"""周累计分布模拟：一周（每天 1 局、7 天、周一清零）达到各奖励档位的概率。

对比两种决策策略：
  single 每天用单局积分最大化决策（week_prior=0）
  week   每天用周效用决策（week_prior=当天已累计周积分，目标是周档位收益最大化）

档位：50/100/200/340/480/620。周效用 = 达到的最高档位值（阶梯奖励）。

用法：
  python sim_week.py [周数] [rollouts] [--single|--week|--both]
"""
import os
import random
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from solver import QuestionDB, predict_success
from mc_sim import build_stage_pools, QUES_PER_STAGE, mc_choose, _build_cache, WEEK_TIERS, week_utility

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
DAYS_PER_WEEK = 7


def play_day(db, pools, cache, init_attrs, seed, rollouts, week_prior, day=0):
    """模拟一天（一局 10 题），返回该局得分。seed 控制抽题随机数。
    day: 0=周一..6=周日（周效用模式需知道今天之后还剩几天）。"""
    random.seed(seed)
    attrs = dict(init_attrs)
    score = 0
    answered = set()
    for stage in range(5):
        n_ques = QUES_PER_STAGE[stage]
        cand = [q for q in pools[stage] if q['id'] not in answered]
        chosen = random.sample(cand, min(n_ques, len(cand)))
        for qi, q in enumerate(chosen):
            idx, _, _ = mc_choose(db, pools, q, attrs, score, stage,
                                  remaining_in_stage=n_ques - qi, rollouts=rollouts,
                                  exclude=answered, caches=cache, week_prior=week_prior,
                                  days_left=DAYS_PER_WEEK - 1 - day)
            answered.add(q['id'])
            c = cache[q['id']]
            success = predict_success(c['conds'][idx], attrs)
            rewards = c['suc'][idx] if success else c['fail'][idx]
            score += int(q.get('integralSuc', 0) if success else q.get('integralFail', 0))
            for at, d in rewards:
                attrs[at] = attrs.get(at, 0) + d
            if attrs.get('health', 10) <= 0:
                return score
    return score


def simulate_week(db, pools, cache, init_attrs, base_seed, rollouts, strategy):
    """模拟一周 7 天（同 seed 序列保证两策略公平对比）。strategy: 'single'|'week'"""
    w = 0
    for day in range(DAYS_PER_WEEK):
        prior = w if strategy == 'week' else 0
        s = play_day(db, pools, cache, init_attrs, base_seed + day, rollouts, prior, day=day)
        w += s
    return w


def report(name, totals):
    n = len(totals)
    print('%s（%d 周）' % (name, n))
    print('  周累计: 均 %.1f 中位 %d 最低 %d 最高 %d' % (
        sum(totals) / n, statistics.median(totals), min(totals), max(totals)))
    print('  周效用: 均 %.3f' % (sum(week_utility(t) for t in totals) / n))
    row = ['  档位达标率:']
    for t in WEEK_TIERS:
        p = sum(1 for x in totals if x >= t) / n * 100
        row.append('%d档 %.3f%%' % (t, p))
    print(' '.join(row))


def main():
    n_weeks = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    rollouts = int(sys.argv[2]) if len(sys.argv) > 2 else 150
    strategy = sys.argv[3] if len(sys.argv) > 3 else 'both'
    t0 = time.time()
    db = QuestionDB()
    pools = build_stage_pools(db)
    cache = _build_cache(db)
    import json
    init_attrs = json.load(open(os.path.join(DATA_DIR, 'characters.json'), encoding='utf-8'))['玉兔']

    print('周分布模拟（%d 周 x %d 天, rollouts=%d, 策略=%s）' % (n_weeks, DAYS_PER_WEEK, rollouts, strategy))
    if strategy in ('single', 'both'):
        t_single = [simulate_week(db, pools, cache, init_attrs, seed * 10, rollouts, 'single')
                    for seed in range(n_weeks)]
        report('单局模式', t_single)
    if strategy in ('week', 'both'):
        t_week = [simulate_week(db, pools, cache, init_attrs, seed * 10, rollouts, 'week')
                  for seed in range(n_weeks)]
        report('周效用模式', t_week)
    print('耗时 %.1fs' % (time.time() - t0))


if __name__ == '__main__':
    main()
