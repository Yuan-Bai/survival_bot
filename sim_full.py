# -*- coding: utf-8 -*-
"""离线模拟：不识别游戏，按真实规则（阶段池不放回抽题 + MC 决策）完整跑局，
统计各角色最终积分分布（期望/中位数/分位数/存活率）。"""
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from solver import QuestionDB, predict_success
from mc_sim import build_stage_pools, QUES_PER_STAGE, mc_choose, _build_cache, generate_seqs


def simulate_full_game(db, pools, cache, char_attrs, n_games=100, rollouts=1000, verbose=False):
    """按真实规则完整模拟 n_games 局：每阶段池内不放回抽题，
    每题用 mc_choose（与 bot 相同）决策。返回最终积分列表。"""
    scores = []
    for g in range(n_games):
        attrs = dict(char_attrs)
        score = 0
        answered_ids = set()
        for stage in range(5):
            n_ques = QUES_PER_STAGE[stage]
            cand = [q for q in pools[stage] if q['id'] not in answered_ids]
            chosen = random.sample(cand, min(n_ques, len(cand)))
            for qi, q in enumerate(chosen):
                remaining = n_ques - qi
                idx, evals, _ = mc_choose(db, pools, q, attrs, score, stage,
                                       remaining_in_stage=remaining, rollouts=rollouts,
                                       exclude=answered_ids, caches=cache)
                answered_ids.add(q['id'])
                c = cache[q['id']]
                success = predict_success(c['conds'][idx], attrs)
                rewards = c['suc'][idx] if success else c['fail'][idx]
                score += int(q.get('integralSuc', 0) if success else q.get('integralFail', 0))
                for at, d in rewards:
                    attrs[at] = attrs.get(at, 0) + d
                if verbose:
                    print('  [局%d 阶段%d 题%d] %s → 选%d %s (积分%d 健康%d)' % (
                        g + 1, stage + 1, qi + 1, q['title'][:16], idx + 1,
                        '成功' if success else '失败', score, attrs.get('health', 0)))
                if attrs.get('health', 10) <= 0:
                    break
            if attrs.get('health', 10) <= 0:
                break
        scores.append(score)
    return scores


def report(name, scores):
    import statistics
    n = len(scores)
    mean = sum(scores) / n
    med = statistics.median(scores)
    p25 = sorted(scores)[n // 4]
    p75 = sorted(scores)[3 * n // 4]
    print('=' * 56)
    print('角色 %s  (%d 局)' % (name, n))
    print('  期望(均值): %.1f' % mean)
    print('  中位数:     %.1f' % med)
    print('  P25~P75:    %d ~ %d' % (p25, p75))
    print('  最小/最大:  %d / %d' % (min(scores), max(scores)))
    return mean, med


def main():
    n_games = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    rollouts = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
    db = QuestionDB()
    pools = build_stage_pools(db)
    cache = _build_cache(db)
    chars = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'characters.json'), encoding='utf-8'))
    for name in ['玉兔', '小猪', '小白', '小唐', '小猴', '小沙', '小吒']:
        t0 = time.time()
        scores = simulate_full_game(db, pools, cache, chars[name], n_games=n_games, rollouts=rollouts)
        print('耗时 %.1fs' % (time.time() - t0))
        report(name, scores)
        print('得分分布:', sorted(scores))


if __name__ == '__main__':
    main()
