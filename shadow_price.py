# -*- coding: utf-8 -*-
"""属性影子价格：每个属性 +1 对最终期望积分的边际价值（离线计算，决策时查表）。

用途：MC 决策时把『选项的属性变化』折算成等值积分计入期望，
把"属性投资的未来回报"显式化（如战力+1 对小猪值 ~4 分，运气+1 只值 ~0.2 分）。

用法：
  python shadow_price.py [局数] [rollouts]   # 计算并保存 data/shadow_prices.json
"""
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from solver import QuestionDB, predict_success
from mc_sim import build_stage_pools, QUES_PER_STAGE, mc_choose, _build_cache

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
PRICES_FILE = os.path.join(DATA_DIR, 'shadow_prices.json')
ATTRS = ['health', 'power', 'wisdom', 'charm', 'luck', 'smart']
CN = {'health': '健康', 'power': '战力', 'wisdom': '智慧', 'charm': '魅力', 'luck': '运气', 'smart': '灵巧'}


def simulate_full_game(db, pools, cache, char_attrs, n_games=50, rollouts=500):
    """按真实规则完整模拟 n_games 局（与 sim_full.py 相同逻辑，独立实现避免依赖）"""
    scores = []
    for _ in range(n_games):
        attrs = dict(char_attrs)
        score = 0
        answered_ids = set()
        for stage in range(5):
            n_ques = QUES_PER_STAGE[stage]
            cand = [q for q in pools[stage] if q['id'] not in answered_ids]
            chosen = random.sample(cand, min(n_ques, len(cand)))
            for qi, q in enumerate(chosen):
                idx, _ = mc_choose(db, pools, q, attrs, score, stage,
                                   remaining_in_stage=n_ques - qi, rollouts=rollouts,
                                   exclude=answered_ids, caches=cache)
                answered_ids.add(q['id'])
                c = cache[q['id']]
                success = predict_success(c['conds'][idx], attrs)
                rewards = c['suc'][idx] if success else c['fail'][idx]
                score += int(q.get('integralSuc', 0) if success else q.get('integralFail', 0))
                for at, d in rewards:
                    attrs[at] = attrs.get(at, 0) + d
                if attrs.get('health', 10) <= 0:
                    break
            if attrs.get('health', 10) <= 0:
                break
        scores.append(score)
    return scores


def compute_shadow_prices(n_games=50, rollouts=500):
    """对每个角色算六属性影子价格：基准期望 vs 各属性 +1 的期望"""
    db = QuestionDB()
    pools = build_stage_pools(db)
    cache = _build_cache(db)
    chars = json.load(open(os.path.join(DATA_DIR, 'characters.json'), encoding='utf-8'))
    prices = {}
    for name, attrs in chars.items():
        base = sum(simulate_full_game(db, pools, cache, attrs, n_games, rollouts)) / n_games
        prices[name] = {'base': base}
        for a in ATTRS:
            up = dict(attrs)
            up[a] += 1
            v = sum(simulate_full_game(db, pools, cache, up, n_games, rollouts)) / n_games
            prices[name][a] = v - base
        print('%s: 基准 %.1f' % (name, base))
        for a in ATTRS:
            print('  %s+1: %+.1f' % (CN[a], prices[name][a]))
    return prices


def main():
    n_games = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    rollouts = int(sys.argv[2]) if len(sys.argv) > 2 else 500
    t0 = time.time()
    prices = compute_shadow_prices(n_games=n_games, rollouts=rollouts)
    with open(PRICES_FILE, 'w', encoding='utf-8') as f:
        json.dump(prices, f, ensure_ascii=False, indent=2)
    print('已保存 %s（耗时 %.1fs）' % (PRICES_FILE, time.time() - t0))


if __name__ == '__main__':
    main()
