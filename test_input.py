"""按键读取测试：跑这个程序，按方向键/空格/回车，看能否正确识别。

用法：python test_input.py
按 ESC 退出。
"""
from input_win import ConsoleInput, map_events, VK_ESCAPE
from protocol import KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_FIRE

KEY_NAMES = {
    KEY_UP: 'UP', KEY_DOWN: 'DOWN', KEY_LEFT: 'LEFT',
    KEY_RIGHT: 'RIGHT', KEY_FIRE: 'FIRE',
}

def main():
    con = ConsoleInput()
    print('按键测试：按 方向键 / 空格 / 回车，ESC 退出')
    print('-' * 40)
    try:
        while True:
            events = con.read_events()
            if not events:
                continue
            for vk, is_down in events:
                # 显示原始 VK 和映射后的游戏键
                game = map_events([(vk, is_down)])
                tag = '按下' if is_down else '抬起'
                if game:
                    gname = KEY_NAMES[game[0][0]]
                    print(f'VK=0x{vk:02X} {tag} -> 游戏键 {gname}')
                else:
                    print(f'VK=0x{vk:02X} {tag} (未映射)')
                if vk == VK_ESCAPE and is_down:
                    print('收到 ESC，退出')
                    return
    except KeyboardInterrupt:
        pass
    finally:
        con.close()

if __name__ == '__main__':
    main()
