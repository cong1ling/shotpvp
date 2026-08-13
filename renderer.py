"""ANSI 差分渲染：只重绘上次到现在变化了的格子，避免整屏闪烁。

游戏元素到字符的映射：
  墙(可破坏) = █    钢墙(不可破坏) = ▓   (本原型只用一种墙)
  坦克       = 根据方向 ^v<>  蓝色(玩家自己) / 红色(对手)
  子弹       = •    黄色
  空         = ' '
"""
import sys
from protocol import (DIR_DELTA, DIR_UP, DIR_DOWN, DIR_LEFT, DIR_RIGHT,
                      DIR_UP_LEFT, DIR_UP_RIGHT, DIR_DOWN_LEFT, DIR_DOWN_RIGHT)

# ANSI 转义
ESC = '\x1b['
HIDE_CURSOR = ESC + '?25l'
SHOW_CURSOR = ESC + '?25h'
CLEAR_SCREEN = ESC + '2J'
HOME = ESC + 'H'

# 颜色（前景/背景）
def fg(color): return ESC + color + 'm'
def reset(): return ESC + '0m'

# 前景色码
C_DEFAULT = '39'
C_RED = '31'
C_GREEN = '32'
C_YELLOW = '33'
C_BLUE = '34'
C_CYAN = '36'
C_WHITE = '97'
C_GRAY = '90'

# 背景色码（仅用于墙，让墙有质感）
BG_DARK = '48;5;238'

# 坦克方向到字符
TANK_CHAR = {
    DIR_UP: '▲',
    DIR_DOWN: '▼',
    DIR_LEFT: '◀',
    DIR_RIGHT: '▶',
    DIR_UP_LEFT: '◤', DIR_UP_RIGHT: '◥',
    DIR_DOWN_LEFT: '◣', DIR_DOWN_RIGHT: '◢',
}

# 子弹字符
BULLET_CHAR = '●'
ITEM_CHARS = {0: ('◆', C_GREEN), 1: ('✦', C_YELLOW), 2: ('⬢', C_CYAN)}
ITEM_BORDER = ('▣', C_WHITE)


# 渲染帧计数常量：每 N 帧强制全屏重绘一次，兜底差分渲染可能漏画的格子
FORCE_FULL_REFRESH = 90
# 单次 ANSI 输出分块大小（格数）：避免超长字符串在旧终端被截断
WRITE_CHUNK = 200


class Renderer:
    def __init__(self, width, height):
        self.w = width
        self.h = height
        # 当前屏上显示的字符（每个格子的内容）
        # 每个元素: (char, color_code) 或 None(空)
        self._buf = [[None] * width for _ in range(height)]
        self._started = False
        self._frames = 0

    def start(self):
        """初始化终端：清屏、隐藏光标。只在开始时调用一次。"""
        sys.stdout.write(HIDE_CURSOR + CLEAR_SCREEN + HOME)
        sys.stdout.flush()
        self._started = True

    def stop(self):
        sys.stdout.write(SHOW_CURSOR + reset())
        sys.stdout.flush()

    def build_buffer(self, grid, tanks, bullets, local_id, items=()):
        """差分重绘当前世界状态。

        grid: 地图
        tanks: list[Tank]
        bullets: list[Bullet]
        local_id: 本玩家 id（用于上色区分）
        """
        new_buf = [[None] * self.w for _ in range(self.h)]

        # 1. 地图（墙）
        for y in range(self.h):
            for x in range(self.w):
                if grid[y][x] != 0:
                    new_buf[y][x] = ('█', '37')

        # 2. 道具是视觉 3x3 标记，但只填空地；实体会在后续覆盖它。
        for item in items:
            for oy in (-1, 0, 1):
                for ox in (-1, 0, 1):
                    x, y = item.x + ox, item.y + oy
                    if 0 <= x < self.w and 0 <= y < self.h and new_buf[y][x] is None:
                        new_buf[y][x] = (ITEM_CHARS.get(item.kind, ('?', C_WHITE))
                                             if (ox, oy) == (0, 0) else ITEM_BORDER)

        # 3. 坦克：普通形态为 3x3 主体并带一格外伸炮管；缩小形态为单格。
        for t in tanks:
            if not t.alive:
                continue
            if t.x < 0 or t.x >= self.w or t.y < 0 or t.y >= self.h:
                continue
            color = C_CYAN if t.id == local_id else C_RED
            if getattr(t, 'shrink_ticks', 0) > 0:
                new_buf[t.y][t.x] = (TANK_CHAR.get(t.dir, '?'), color)
                continue
            body = {
                DIR_UP: ('╔▲╗', '║■║', '╚═╝'),
                DIR_DOWN: ('╔═╗', '║■║', '╚▼╝'),
                DIR_LEFT: ('╔═╗', '◀■║', '╚═╝'),
                DIR_RIGHT: ('╔═╗', '║■▶', '╚═╝'),
                DIR_UP_LEFT: ('◤═╗', '║■║', '╚═╝'),
                DIR_UP_RIGHT: ('╔═◥', '║■║', '╚═╝'),
                DIR_DOWN_LEFT: ('╔═╗', '║■║', '◣═╝'),
                DIR_DOWN_RIGHT: ('╔═╗', '║■║', '╚═◢'),
            }[t.dir]
            for oy, row in enumerate(body):
                for ox, ch in enumerate(row):
                    x, y = t.x + ox - 1, t.y + oy - 1
                    if 0 <= x < self.w and 0 <= y < self.h:
                        new_buf[y][x] = (ch, color)
            dx, dy = DIR_DELTA[t.dir]
            bx, by = t.x + dx * 2, t.y + dy * 2
            if 0 <= bx < self.w and 0 <= by < self.h:
                barrel = '┃' if dx == 0 else ('━' if dy == 0 else ('╲' if dx == dy else '╱'))
                new_buf[by][bx] = (barrel, color)

        # 4. 子弹始终位于道具视觉标记之上，确保弹道可读。
        for b in bullets:
            if b.x < 0 or b.x >= self.w or b.y < 0 or b.y >= self.h:
                continue
            new_buf[b.y][b.x] = (BULLET_CHAR, C_YELLOW)
        return new_buf

    def render(self, grid, tanks, bullets, local_id, items=()):
        if not self._started:
            self.start()
        new_buf = self.build_buffer(grid, tanks, bullets, local_id, items)

        # 差分输出：只画变化的格子
        out = []
        # 防御：若终端曾漏画首帧墙体（旧 conhost 对超长 ANSI/亮色码支持不稳），
        # 差分永远不再补画。每 FORCE_FULL_REFRESH 帧强制全屏重建一次兜底。
        force_full = self._frames % FORCE_FULL_REFRESH == 0
        for y in range(self.h):
            for x in range(self.w):
                old = self._buf[y][x]
                new = new_buf[y][x]
                if not force_full and old == new:
                    continue
                # 定位光标到 (y, x+1)（ANSI 行列从 1 开始）
                out.append(ESC + '%d;%dH' % (y + 1, x + 1))
                if new is None:
                    out.append(' ' + reset())
                else:
                    ch, color = new
                    out.append(fg(color) + ch + reset())
        if out:
            # 分块写入，避免单次超大字符串在部分终端/管道被截断
            for i in range(0, len(out), WRITE_CHUNK):
                sys.stdout.write(''.join(out[i:i + WRITE_CHUNK]))
            sys.stdout.flush()
        self._buf = new_buf
        self._frames += 1

    def render_hud(self, local_tank, remote_tank, ping_ms, connection='在线', phase=1,
                   phase_ticks=0, winner=255):
        """在画面底部画一行 HUD：分数、是否轮到操作、ping。

        简单实现：定位到最后一行下面一行。"""
        if phase == 0:
            status = '倒计时 %.1fs' % (phase_ticks / 30)
        elif phase == 2:
            status = ('你获胜' if winner == local_tank.id else '对手获胜') + ' | 双方按开火键重开'
        else:
            effects = []
            if local_tank.shrink_ticks: effects.append('缩小 %.1fs' % (local_tank.shrink_ticks / 30))
            if local_tank.shield: effects.append('护盾')
            if local_tank.triple_shot: effects.append('三连发')
            status = '/'.join(effects) or '战斗中'
        line = '你 %d : %d 对手 | %s | RTT %dms | %s' % (
            local_tank.score, remote_tank.score, status, ping_ms, connection,
        )
        out = [ESC + '%d;1H' % (self.h + 2), reset()]
        # 清掉该行旧内容
        out.append(' ' * self.w)
        out.append(ESC + '%d;1H' % (self.h + 2))
        out.append(line[:self.w])
        sys.stdout.write(''.join(out))
        sys.stdout.flush()


# 用于在文件外快速获取坦克字符
def tank_char(direction):
    return TANK_CHAR.get(direction, '?')
