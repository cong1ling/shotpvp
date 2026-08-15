"""纯游戏逻辑：地图、坦克、子弹、道具与对局状态。

服务端用它跑权威模拟，客户端用它做客户端预测。
状态用整数坐标，单位是"格"，无浮点。
"""
import random

from protocol import DIR_DELTA, DELTA_DIR, KEY_FIRE, DIR_UP, DIR_DOWN, DIR_LEFT, DIR_RIGHT

# ---- 常量 ----
MAP_W = 64
MAP_H = 30
TILE_EMPTY = 0
TILE_BRICK = 1
TILE_STEEL = 2
MAX_BOUNCES = 5

TANK_COOLDOWN = 8        # 开炮后多少 tick 才能再开
BULLET_SPEED = 1         # 每 tick 移动几格（这里固定 1）
TANK_SPEED = 1           # 每移动 tick 走几格
RESPAWN_TICKS = 15       # 被击杀后多少 tick 才重生
COUNTDOWN_TICKS = 90
WIN_SCORE = 5
POWERUP_SPAWN_TICKS = 240
SHRINK_TICKS = 300
MAX_POWERUPS = 2

PHASE_COUNTDOWN = 0
PHASE_PLAYING = 1
PHASE_GAME_OVER = 2
POWERUP_SHRINK = 0
POWERUP_TRIPLE = 1
POWERUP_SHIELD = 2

# 默认地图：0=空，1=墙（可破坏），2=钢墙（不可破坏）
# 用字符串模板便于编辑
_MAP_TEMPLATE = [
    "########################################",
    "#                                      #",
    "#  ##    ##    ##    ##    ##    ##  #",
    "#  ##    ##    ##    ##    ##    ##  #",
    "#                                      #",
    "#   ##        ##  ##        ##        #",
    "#   ##        ##  ##        ##        #",
    "#                                      #",
    "#        ##        ##        ##        #",
    "#        ##        ##        ##        #",
    "#                                      #",
    "#   ##        ##  ##        ##        #",
    "#   ##        ##  ##        ##        #",
    "#                                      #",
    "#  ##    ##    ##    ##    ##    ##  #",
    "#  ##    ##    ##    ##    ##    ##  #",
    "#                                      #",
    "#                                      #",
    "#                                      #",
    "########################################",
]


def build_map():
    """返回二维列表 grid[y][x]，值为格子类型。（固定双人模板，测试与预测依赖）"""
    grid = [[TILE_EMPTY] * MAP_W for _ in range(MAP_H)]
    for x in range(MAP_W):
        grid[0][x] = grid[MAP_H - 1][x] = TILE_STEEL
    for y in range(MAP_H):
        grid[y][0] = grid[y][MAP_W - 1] = TILE_STEEL
    # 对称的砖墙掩体，出生区域保持开阔。
    for y in (6, 7, 14, 15, 22, 23):
        for x in (12, 13, 24, 25):
            grid[y][x] = grid[y][MAP_W - 1 - x] = TILE_BRICK
    return grid


# 候选掩体锚点（2×2 砖墙块）：每局从左半场抽样存活。
# (y, x) 为左上角；右半场自动镜像，保证左右对称公平。
# 出生区（y<=4 或 y>=26 且靠边）刻意没有锚点，保持开阔。
_MASK_ANCHORS = [
    (6, 4), (6, 12), (6, 20), (7, 8), (7, 16),
    (10, 4), (10, 12), (10, 20), (11, 8), (11, 16),
    (14, 4), (14, 12), (14, 20), (15, 8), (15, 16),
    (18, 4), (18, 12), (18, 20), (19, 8), (19, 16),
    (22, 4), (22, 12), (22, 20), (23, 8), (23, 16),
]


def generate_map(rng=None):
    """生成随机对称竞技场。

    上下左右边界为钢墙；左半场按锚点随机放置 2×2 砖墙块，
    右半场镜像复制，保证双方出生区对称公平。
    每局从锚点中抽样约 60%，避免每次地图完全相同，也避免墙太密。
    """
    rng = rng or random.Random()
    grid = [[TILE_EMPTY] * MAP_W for _ in range(MAP_H)]
    for x in range(MAP_W):
        grid[0][x] = grid[MAP_H - 1][x] = TILE_STEEL
    for y in range(MAP_H):
        grid[y][0] = grid[y][MAP_W - 1] = TILE_STEEL
    # 左半场（x 从 2 到 MAP_W//2 - 4，预留镜像空间），抽样锚点
    half = MAP_W // 2
    for (ay, ax) in _MASK_ANCHORS:
        if ax < 2 or ax + 1 >= half - 2:
            continue  # 太靠中线的锚点跳过，避免左右镜像重叠
        if rng.random() < 0.6:
            for dy in range(2):
                for dx in range(2):
                    grid[ay + dy][ax + dx] = TILE_BRICK
    # 镜像到右半场：左半场 (y, x) -> 右半场 (y, MAP_W-1-x)
    for y in range(1, MAP_H - 1):
        for x in range(2, half - 2):
            if grid[y][x] == TILE_BRICK:
                grid[y][MAP_W - 1 - x] = TILE_BRICK
    return grid


def is_wall(grid, x, y):
    """坐标是否被墙占据。越界视为墙。"""
    if x < 0 or y < 0 or x >= MAP_W or y >= MAP_H:
        return True
    return grid[y][x] != 0


class Tank:
    __slots__ = ('id', 'x', 'y', 'dir', 'alive', 'score', 'cooldown', 'spawn_x', 'spawn_y', 'respawn_timer', 'shrink_ticks', 'shield', 'triple_shot', 'restart_ready')

    def __init__(self, tid, x, y, direction=DIR_UP):
        self.id = tid
        self.x = x
        self.y = y
        self.dir = direction
        self.alive = True
        self.score = 0
        self.cooldown = 0
        self.spawn_x = x
        self.spawn_y = y
        self.respawn_timer = 0
        self.shrink_ticks = 0
        self.shield = 0
        self.triple_shot = False
        self.restart_ready = False

    def reset(self):
        self.x = self.spawn_x
        self.y = self.spawn_y
        self.dir = DIR_UP
        self.alive = True
        self.cooldown = 0
        self.respawn_timer = 0

    def to_tuple(self):
        return (self.id, self.x, self.y, self.dir, 1 if self.alive else 0, self.score)

    def to_state_tuple(self):
        return (self.id, self.x, self.y, self.dir, int(self.alive), self.score,
                self.shrink_ticks, self.shield, int(self.triple_shot), int(self.restart_ready))

    def occupied_cells(self, x=None, y=None):
        x = self.x if x is None else x
        y = self.y if y is None else y
        if self.shrink_ticks > 0:
            return [(x, y)]
        return [(cx, cy) for cy in range(y - 1, y + 2) for cx in range(x - 1, x + 2)]

    def muzzle_position(self):
        dx, dy = DIR_DELTA[self.dir]
        distance = 1 if self.shrink_ticks > 0 else 2
        return self.x + dx * distance, self.y + dy * distance


class Bullet:
    __slots__ = ('id', 'x', 'y', 'dir', 'owner', 'bounces')

    def __init__(self, bid, x, y, direction, owner, bounces=0):
        self.id = bid
        self.x = x
        self.y = y
        self.dir = direction
        self.owner = owner
        self.bounces = bounces

    def to_tuple(self):
        return (self.id, self.x, self.y, self.dir, self.owner)

    def to_state_tuple(self):
        return (self.id, self.x, self.y, self.dir, self.owner, self.bounces)


class PowerUp:
    __slots__ = ('id', 'x', 'y', 'kind')

    def __init__(self, item_id, x, y, kind):
        self.id, self.x, self.y, self.kind = item_id, x, y, kind

    def to_tuple(self):
        return (self.id, self.x, self.y, self.kind)


# ---- 出生点（两个对称位置）----
SPAWN_POINTS = [
    (2, 2, DIR_DOWN),
    (MAP_W - 3, MAP_H - 3, DIR_UP),
]


def can_place_tank(grid, tank, x, y, tanks=()):
    cells = set(tank.occupied_cells(x, y))
    if any(is_wall(grid, cx, cy) for cx, cy in cells):
        return False
    return not any(other is not tank and other.alive and cells.intersection(other.occupied_cells()) for other in tanks)


def spawn_tank(tid):
    sx, sy, sd = SPAWN_POINTS[tid % len(SPAWN_POINTS)]
    return Tank(tid, sx, sy, sd)


# ---- 核心逻辑：服务端权威推进一个 tick ----
def step_world(grid, tanks, bullets, inputs, next_bullet_id):
    """推进一个逻辑 tick。

    grid: 地图
    tanks: list[Tank]
    bullets: list[Bullet]
    inputs: dict[tank_id -> list[(key, is_down)]]  本 tick 内累积的按键事件
    next_bullet_id: callable[[] -> int]  分配子弹 id

    返回: 新的 bullets 列表（含旧未消亡的+新发射的）
    """
    # 1. 处理每个坦克的输入
    new_bullets = []
    for tank in tanks:
        if not tank.alive:
            continue
        evs = inputs.get(tank.id, [])
        for key, is_down in evs:
            if not is_down:
                continue  # 原型阶段：只响应按下，忽略抬起
            if key in DIR_DELTA:
                tank.dir = key
                dx, dy = DIR_DELTA[key]
                nx, ny = tank.x + dx, tank.y + dy
                # 不能撞墙、不能撞别的活坦克
                blocked = not can_place_tank(grid, tank, nx, ny, tanks)
                if not blocked:
                    tank.x = nx
                    tank.y = ny
            elif key == KEY_FIRE:
                if tank.cooldown <= 0:
                    dx, dy = DIR_DELTA[tank.dir]
                    distance = 1 if tank.shrink_ticks > 0 else 2
                    muzzle_x, muzzle_y = tank.x + dx * distance, tank.y + dy * distance
                    offsets = (-1, 0, 1) if tank.triple_shot else (0,)
                    tank.triple_shot = False
                    for offset in offsets:
                        bx = muzzle_x + (-dy * offset)
                        by = muzzle_y + (dx * offset)
                        if not is_wall(grid, bx, by):
                            new_bullets.append(Bullet(next_bullet_id(), bx, by, tank.dir, tank.id))
                    tank.cooldown = TANK_COOLDOWN

    # 2. 子弹推进
    surviving_bullets = []
    killed_tanks = set()
    for b in bullets + new_bullets:
        dx, dy = DIR_DELTA[b.dir]
        nx, ny = b.x + dx, b.y + dy
        blocked_x = dx != 0 and is_wall(grid, b.x + dx, b.y)
        blocked_y = dy != 0 and is_wall(grid, b.x, b.y + dy)
        blocked_corner = is_wall(grid, nx, ny)
        if blocked_x or blocked_y or blocked_corner:
            contacts = set()
            if blocked_x: contacts.add((b.x + dx, b.y))
            if blocked_y: contacts.add((b.x, b.y + dy))
            if not blocked_x and not blocked_y and blocked_corner: contacts.add((nx, ny))
            for wx, wy in contacts:
                if 0 <= wx < MAP_W and 0 <= wy < MAP_H and grid[wy][wx] == TILE_BRICK:
                    grid[wy][wx] = TILE_EMPTY
            if not blocked_x and not blocked_y:  # destination-only diagonal corner
                dx, dy = -dx, -dy
            else:
                if blocked_x: dx = -dx
                if blocked_y: dy = -dy
            b.dir = DELTA_DIR[(dx, dy)]
            b.bounces += 1
            if b.bounces < MAX_BOUNCES:
                surviving_bullets.append(b)
            continue
        # 撞坦克
        hit = None
        for tank in tanks:
            if tank.alive and (nx, ny) in tank.occupied_cells():
                hit = tank
                break
        if hit:
            if hit.shield:
                hit.shield = 0
                continue
            hit.alive = False
            hit.respawn_timer = RESPAWN_TICKS
            killed_tanks.add(hit.id)
            if hit.id == b.owner:
                opponent = next((t for t in tanks if t.id != hit.id), None)
                if opponent:
                    opponent.score += 1
            else:
                killer = next((t for t in tanks if t.id == b.owner), None)
                if killer:
                    killer.score += 1
            continue
        b.x = nx
        b.y = ny
        surviving_bullets.append(b)

    # 3. 冷却递减
    for tank in tanks:
        if tank.cooldown > 0:
            tank.cooldown -= 1
        if tank.shrink_ticks > 1:
            tank.shrink_ticks -= 1
        elif tank.shrink_ticks == 1:
            tank.shrink_ticks = 0
            if not can_place_tank(grid, tank, tank.x, tank.y, tanks):
                tank.shrink_ticks = 1

    # 4. 重生倒计时
    for tank in tanks:
        if not tank.alive:
            if tank.respawn_timer > 0:
                tank.respawn_timer -= 1
                if tank.respawn_timer == 0:
                    # 倒计时归零，检查出生点是否被占用
                    sx, sy, _ = SPAWN_POINTS[tank.id % len(SPAWN_POINTS)]
                    if can_place_tank(grid, tank, sx, sy, tanks):
                        tank.reset()
                    else:
                        # 出生点被占，再延一个 tick
                        tank.respawn_timer = 1

    return surviving_bullets


class World:
    """服务端权威世界；网络和渲染层只消费其快照。"""
    def __init__(self, player_count=2, rng=None):
        self.player_count = player_count
        self.rng = rng or random.Random()
        self._next_bullet = 0
        self._next_item = 0
        self.reset_round()

    def reset_round(self):
        self.grid = generate_map(self.rng)
        self.tanks = [spawn_tank(i) for i in range(self.player_count)]
        self.bullets = []
        self.items = []
        self.tick = 0
        self.phase = PHASE_COUNTDOWN
        self.phase_ticks = COUNTDOWN_TICKS
        self.winner = 255
        self.item_spawn_timer = POWERUP_SPAWN_TICKS

    def next_bullet_id(self):
        self._next_bullet = (self._next_bullet + 1) & 0xffff
        return self._next_bullet

    def set_restart_ready(self, tank_id):
        if self.phase == PHASE_GAME_OVER and 0 <= tank_id < len(self.tanks):
            self.tanks[tank_id].restart_ready = True
            if self.tanks and all(t.restart_ready for t in self.tanks):
                self.reset_round()

    def _valid_item_center(self, x, y):
        cells = {(cx, cy) for cy in range(y - 1, y + 2) for cx in range(x - 1, x + 2)}
        if any(is_wall(self.grid, cx, cy) for cx, cy in cells):
            return False
        if any(t.alive and cells.intersection(t.occupied_cells()) for t in self.tanks):
            return False
        if any((i.x, i.y) in cells for i in self.items):
            return False
        if any((b.x, b.y) in cells for b in self.bullets):
            return False
        return True

    def spawn_powerup(self):
        candidates = [(x, y) for y in range(2, MAP_H - 2) for x in range(2, MAP_W - 2)
                      if self._valid_item_center(x, y)]
        if not candidates:
            return None
        x, y = self.rng.choice(candidates)
        self._next_item = (self._next_item + 1) & 0xffff
        item = PowerUp(self._next_item, x, y, self.rng.choice(
            (POWERUP_SHRINK, POWERUP_TRIPLE, POWERUP_SHIELD)))
        self.items.append(item)
        return item

    def _collect_items(self):
        remaining = []
        for item in self.items:
            tank = next((t for t in self.tanks if t.alive and (item.x, item.y) in t.occupied_cells()), None)
            if tank is None:
                remaining.append(item)
            elif item.kind == POWERUP_SHRINK:
                tank.shrink_ticks = SHRINK_TICKS
            elif item.kind == POWERUP_TRIPLE:
                tank.triple_shot = True
            elif item.kind == POWERUP_SHIELD:
                tank.shield = 1
        self.items = remaining

    def step(self, inputs):
        self.tick = (self.tick + 1) & 0xffffffff
        if self.phase == PHASE_COUNTDOWN:
            self.phase_ticks -= 1
            if self.phase_ticks <= 0:
                self.phase, self.phase_ticks = PHASE_PLAYING, 0
            return
        if self.phase == PHASE_GAME_OVER:
            for tid, events in inputs.items():
                if any(key == KEY_FIRE and down for key, down in events):
                    self.set_restart_ready(tid)
            return
        self.bullets = step_world(self.grid, self.tanks, self.bullets, inputs, self.next_bullet_id)
        self._collect_items()
        self.item_spawn_timer -= 1
        if self.item_spawn_timer <= 0:
            if len(self.items) < MAX_POWERUPS:
                self.spawn_powerup()
            self.item_spawn_timer = POWERUP_SPAWN_TICKS
        winner = next((t for t in self.tanks if t.score >= WIN_SCORE), None)
        if winner:
            self.phase, self.phase_ticks, self.winner = PHASE_GAME_OVER, 0, winner.id
            self.bullets = []

    def snapshot(self):
        return {'tick': self.tick, 'phase': self.phase, 'phase_ticks': self.phase_ticks,
                'winner': self.winner, 'grid': self.grid,
                'tanks': [t.to_state_tuple() for t in self.tanks],
                'bullets': [b.to_state_tuple() for b in self.bullets],
                'items': [i.to_tuple() for i in self.items]}
