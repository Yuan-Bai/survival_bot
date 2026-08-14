# -*- coding: utf-8 -*-
"""统一入口：一个 exe 同时提供框选与答题两个功能（减小打包体积）。
用法：
  main.py calibrate    -> 区域框选
  main.py run [args]   -> 自动答题（透传 --mc/--dry-run 等）
  main.py              -> 交互菜单（小白友好）
打包时 PyInstaller 只打这一个脚本。
"""
import sys


def main():
    args = sys.argv[1:]
    if 'calibrate' in args:
        import calibrate
        calibrate.main()
        return
    if 'run' in args or any(a.startswith('--') for a in args):
        import bot
        bot.main()
        return
    # 无参数：交互菜单
    print('=' * 44)
    print('  造梦西游4 生存大冒险 自动答题工具')
    print('  本程序完全免费，请勿付费购买！')
    print('=' * 44)
    print('  首次使用请先框选识别区域：')
    print('    [1] 区域框选工具')
    print('  然后开始自动答题：')
    print('    [2] 开始自动答题（推荐：蒙特卡洛模式）')
    print('    [3] 开始自动答题（简单模式）')
    print('    [q] 退出')
    while True:
        try:
            c = input('请选择: ').strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if c == '1':
            import calibrate
            calibrate.main()
            return
        if c in ('2', '3'):
            # 重启带 run 参数（提权后不会再回菜单）；3=简单模式（无 --mc）
            import os
            args = [sys.executable, os.path.abspath(__file__), 'run']
            if c == '2':
                args.append('--mc')
            os.execv(sys.executable, args)
            return
        if c == 'q':
            return
        print('无效选择，请输入 1/2/3/q')


if __name__ == '__main__':
    main()
