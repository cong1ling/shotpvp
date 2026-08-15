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

# ---- 终端 VT 支持（Windows 关键）----
# 现代终端（Windows Terminal）默认解析 ANSI，但经典 conhost（PowerShell 5.1
# 自带窗口、旧版 Windows 10 控制台）默认不处理 VT 转义序列，会把 \x1b[37m、
# \x1b[H 这类控制码当普通文本直接打印——界面会变成大段乱码/阶梯状文字。
# 必须在启动时用 SetConsoleMode 显式开启 ENABLE_VIRTUAL_TERMINAL_PROCESSING。
def enable_vt():
    """让 stdout 所在终端处理 ANSI 转义。非 Windows 或失败时静默跳过。"""
    if sys.platform == 'win32':
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_uint32()
            if (kernel32.GetConsoleMode(handle, ctypes.byref(mode)) and
                    not (mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING)):
                kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
        except Exception:
            pass

# ANSI 转义
ESC = '\x1b['
HIDE_CURSOR = ESC + '?25l'
SHOW_CURSOR = ESC + '?25h'
CLEAR_SCREEN = ESC + '2J'
HOME = ESC + 'H'

# 颜色（前景/背景）
def fg(color): return ESC + color + ';1m'
def reset(): return ESC + '0m'

# 真彩色（24-bit）配色板 —— 坦克大战主题
# 格式: 38;2;R;G;B   (前景)  /  48;2;R;G;B  (背景)
C_DEFAULT = '38;2;200;200;200'       # 默认文字：浅灰
C_RED = '38;2;220;60;60'             # 敌方坦克：暖红
C_GREEN = '38;2;80;200;80'           # 缩小道具：绿色
C_YELLOW = '38;2;255;200;40'         # 子弹：金黄
C_BLUE = '38;2;60;140;220'           # 我方坦克：蓝白
C_CYAN = '38;2;60;180;220'           # 护盾/三连发：青色
C_WHITE = '38;2;240;240;235'         # 亮白
C_GRAY = '38;2;120;100;80'           # 不可破坏墙体：灰褐
C_SAND = '38;2;190;160;110'          # 可破坏砖墙：暖沙色
C_SHADOW = '38;2;30;20;10'            # 墙体阴影：深褐
C_GROUND = '38;2;35;30;25'            # 背景纹理：极暗棕
C_EXPLOSION = '38;2;255;140;30'       # 爆炸粒子：橙红

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
        self._particles = []  # [(x, y, tick, max_tick)]

    def start(self):
        """初始化终端：开启 VT 处理、清屏、隐藏光标。只在开始时调用一次。"""
        enable_vt()
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

        # 1. 地图（墙）—— 砖墙=暖沙色▓，钢墙=灰褐█
        for y in range(self.h):
            for x in range(self.w):
                t = grid[y][x]
                if t == 1:
                    new_buf[y][x] = ('▓', C_SAND)
                elif t == 2:
                    new_buf[y][x] = ('█', C_GRAY)

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

        # 5. 墙体阴影：砖墙下方空地画深色投影，增强立体感
        for y in range(self.h):
            for x in range(self.w):
                if grid[y][x] == 1:  # 砖墙
                    sx, sy = x, y + 1
                    if 0 <= sx < self.w and 0 <= sy < self.h and new_buf[sy][sx] is None:
                        new_buf[sy][sx] = ('░', C_SHADOW)

        # 6. 背景纹理：剩余空地铺极暗点，消除纯黑"空洞感"
        for y in range(self.h):
            for x in range(self.w):
                if new_buf[y][x] is None:
                    new_buf[y][x] = ('·', C_GROUND)

        return new_buf

    def spawn_explosion(self, x, y):
        """在指定位置生成 3×3 爆炸粒子（扩散动画）。"""
        import random
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                px, py = x + dx, y + dy
                if 0 <= px < self.w and 0 <= py < self.h:
                    life = random.randint(3, 6)
                    self._particles.append((px, py, 0, life))

    def _advance_particles(self, buf):
        """推进粒子生命周期并覆盖到 buffer。"""
        surviving = []
        for (px, py, tick, life) in self._particles:
            tick += 1
            if tick >= life:
                continue
            surviving.append((px, py, tick, life))
            # 粒子字符：█(火) → ▓(爆) → ▒(烟) → ░(弥散)
            phase = tick / life
            if phase < 0.25:
                ch = '█'
            elif phase < 0.5:
                ch = '▓'
            elif phase < 0.75:
                ch = '▒'
            else:
                ch = '░'
            # 颜色：橙红 → 暗灰
            r = int(255 - 200 * phase)
            g = int(140 - 100 * phase)
            b = int(30 + 50 * phase)
            color = '38;2;%d;%d;%d' % (r, g, b)
            buf[py][px] = (ch, color)
        self._particles = surviving

    def render(self, grid, tanks, bullets, local_id, items=()):
        if not self._started:
            self.start()
        new_buf = self.build_buffer(grid, tanks, bullets, local_id, items)

        # 爆炸粒子（在实体之上叠加，确保视觉可见）
        self._advance_particles(new_buf)

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
        """在画面底部画三行面板 HUD：上边框 / 内容 / 下边框。"""
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
        line = '你 %d : %d 对手 │ %s │ RTT %dms │ %s' % (
            local_tank.score, remote_tank.score, status, ping_ms, connection,
        )
        # 内宽 = 地图宽度 - 2（左右边框各占一列）
        inner_w = self.w - 2
        # 如果内容太长，截断
        content = line[:inner_w]
        content = content + ' ' * (inner_w - len(content))
        top = '┌' + '─' * inner_w + '┐'
        mid = '│' + content + '│'
        bot = '└' + '─' * inner_w + '┘'
        out = [ESC + '%d;1H' % (self.h + 1), reset()]
        out.append(top)
        out.append(ESC + '%d;1H' % (self.h + 2))
        out.append(mid)
        out.append(ESC + '%d;1H' % (self.h + 3))
        out.append(bot)
        sys.stdout.write(''.join(out))
        sys.stdout.flush()


# 用于在文件外快速获取坦克字符
def tank_char(direction):
    return TANK_CHAR.get(direction, '?')
