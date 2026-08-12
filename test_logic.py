"""无副作用的游戏逻辑自测：不启网络，直接调 game.py 验证核心规则。

跑法：python test_logic.py
预期：打印 OK，退出码 0。
"""
from game import build_map, spawn_tank, step_world, Tank, Bullet, MAP_W, MAP_H
from protocol import (DIR_UP, DIR_DOWN, DIR_LEFT, DIR_RIGHT, DIR_UP_LEFT,
                      DIR_UP_RIGHT, DIR_DOWN_LEFT, DIR_DOWN_RIGHT,
                      KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_FIRE)


def test_large_tank_footprint_and_muzzle():
    t = Tank(0, 10, 10, DIR_UP)
    assert set(t.occupied_cells()) == {(x, y) for y in range(9, 12) for x in range(9, 12)}
    assert t.muzzle_position() == (10, 8)
    t.dir = DIR_RIGHT
    assert t.muzzle_position() == (12, 10)


def test_small_tank_occupies_one_cell():
    t = Tank(0, 10, 10)
    t.shrink_ticks = 10
    assert t.occupied_cells() == [(10, 10)]


def test_brick_breaks_and_bullet_reflects():
    from game import TILE_BRICK
    grid = build_map()
    grid[10][12] = TILE_BRICK
    t0 = Tank(0, 7, 10, DIR_RIGHT)
    t1 = Tank(1, 30, 20)
    bullet = Bullet(1, 11, 10, DIR_RIGHT, 0)
    bullets = step_world(grid, [t0, t1], [bullet], {}, lambda: 2)
    assert grid[10][12] == 0
    assert len(bullets) == 1
    assert bullets[0].dir == DIR_LEFT and bullets[0].bounces == 1


def test_own_ricochet_can_kill_owner():
    t0 = Tank(0, 10, 10)
    bullet = Bullet(1, 10, 8, DIR_DOWN, 0)
    bullets = step_world(build_map(), [t0], [bullet], {}, lambda: 2)
    assert not t0.alive
    assert bullets == []


def test_self_kill_scores_for_opponent():
    t0, t1 = Tank(0, 10, 10), Tank(1, 30, 20)
    step_world(build_map(), [t0, t1], [Bullet(1, 10, 8, DIR_DOWN, 0)], {}, lambda: 2)
    assert not t0.alive and t0.score == 0 and t1.score == 1


def test_self_kill_can_end_match_for_opponent():
    from game import World, PHASE_PLAYING, PHASE_GAME_OVER, WIN_SCORE
    world = World()
    world.phase = PHASE_PLAYING
    world.tanks[1].score = WIN_SCORE - 1
    victim = world.tanks[0]
    victim.x = victim.y = 10
    world.bullets = [Bullet(1, 10, 8, DIR_DOWN, victim.id)]
    world.step({})
    assert world.phase == PHASE_GAME_OVER and world.winner == 1
    assert world.tanks[1].score == WIN_SCORE


def test_shield_absorbs_one_hit():
    t0 = Tank(0, 10, 10)
    t0.shield = 1
    bullet = Bullet(1, 10, 8, DIR_DOWN, 1)
    step_world(build_map(), [t0], [bullet], {}, lambda: 2)
    assert t0.alive and t0.shield == 0


def test_triple_shot_is_consumed():
    t0 = Tank(0, 20, 20, DIR_UP)
    t0.triple_shot = True
    bullets = step_world(build_map(), [t0], [], {0: [(KEY_FIRE, True)]}, iter(range(1, 10)).__next__)
    assert len(bullets) == 3
    assert not t0.triple_shot


def test_parallel_triple_shot_all_directions():
    for direction in range(8):
        tank = Tank(0, 30, 15, direction)
        tank.triple_shot = True
        bullets = step_world(build_map(), [tank], [], {0: [(KEY_FIRE, True)]}, iter(range(1, 20)).__next__)
        assert len(bullets) == 3 and {b.dir for b in bullets} == {direction}
        assert len({(b.x, b.y) for b in bullets}) == 3


def test_diagonal_move_muzzle_and_component_ricochet():
    from game import TILE_BRICK
    tank = Tank(0, 20, 20, DIR_UP_RIGHT)
    assert tank.muzzle_position() == (22, 18)
    step_world(build_map(), [tank], [], {0: [(DIR_UP_RIGHT, True)]}, lambda: 1)
    assert (tank.x, tank.y) == (21, 19)
    grid = build_map()
    grid[10][11] = TILE_BRICK  # x-only contact from (10,10)
    bullets = step_world(grid, [], [Bullet(1, 10, 10, DIR_DOWN_RIGHT, 0)], {}, lambda: 2)
    assert bullets[0].dir == DIR_DOWN_LEFT and grid[10][11] == 0
    grid = build_map()
    grid[11][10] = TILE_BRICK  # y-only contact
    bullets = step_world(grid, [], [Bullet(1, 10, 10, DIR_DOWN_RIGHT, 0)], {}, lambda: 2)
    assert bullets[0].dir == DIR_UP_RIGHT and grid[11][10] == 0
    grid = build_map()
    grid[11][11] = TILE_BRICK  # destination-only corner
    bullets = step_world(grid, [], [Bullet(1, 10, 10, DIR_DOWN_RIGHT, 0)], {}, lambda: 2)
    assert bullets[0].dir == DIR_UP_LEFT and grid[11][11] == 0


def test_diagonal_corner_counts_one_bounce_and_expires_at_limit():
    from game import TILE_STEEL, MAX_BOUNCES
    grid = build_map()
    grid[11][11] = TILE_STEEL
    bullet = Bullet(1, 10, 10, DIR_DOWN_RIGHT, 0, MAX_BOUNCES - 2)
    bullets = step_world(grid, [], [bullet], {}, lambda: 2)
    assert len(bullets) == 1 and bullets[0].bounces == MAX_BOUNCES - 1
    bullets[0].x, bullets[0].y, bullets[0].dir = 10, 10, DIR_DOWN_RIGHT
    assert step_world(grid, [], bullets, {}, lambda: 2) == []


def test_shrink_timer_expires():
    t0 = Tank(0, 20, 20)
    t0.shrink_ticks = 1
    step_world(build_map(), [t0], [], {}, lambda: 1)
    assert t0.shrink_ticks == 0


def test_world_countdown_powerups_win_and_restart():
    import random
    from game import (World, PowerUp, PHASE_PLAYING, PHASE_GAME_OVER,
                      POWERUP_SHRINK, POWERUP_TRIPLE, POWERUP_SHIELD,
                      SHRINK_TICKS, WIN_SCORE)
    world = World(rng=random.Random(7))
    for _ in range(90):
        world.step({})
    assert world.phase == PHASE_PLAYING
    tank = world.tanks[0]
    for kind in (POWERUP_SHRINK, POWERUP_TRIPLE, POWERUP_SHIELD):
        world.items = [PowerUp(kind + 1, tank.x, tank.y, kind)]
        world._collect_items()
    assert tank.shrink_ticks == SHRINK_TICKS and tank.triple_shot and tank.shield == 1
    tank.score = WIN_SCORE
    world.step({})
    assert world.phase == PHASE_GAME_OVER and world.winner == 0
    world.step({0: [(KEY_FIRE, True)]})
    assert world.tanks[0].restart_ready and world.phase == PHASE_GAME_OVER
    world.step({1: [(KEY_FIRE, True)]})
    assert world.phase != PHASE_GAME_OVER and all(t.score == 0 for t in world.tanks)


def test_shrink_recovery_defers_when_blocked():
    from game import TILE_BRICK
    grid = build_map()
    tank = Tank(0, 10, 10)
    tank.shrink_ticks = 1
    grid[9][9] = TILE_BRICK
    step_world(grid, [tank], [], {}, lambda: 1)
    assert tank.shrink_ticks == 1


def test_respawn_requires_full_footprint():
    from game import TILE_BRICK
    grid = build_map()
    tank = spawn_tank(0)
    tank.alive = False
    tank.respawn_timer = 1
    grid[tank.spawn_y + 1][tank.spawn_x + 1] = TILE_BRICK
    step_world(grid, [tank], [], {}, lambda: 1)
    assert not tank.alive and tank.respawn_timer == 1


def test_basic_move():
    """坦克按方向键能移动一格。"""
    grid = build_map()
    t0 = spawn_tank(0)
    t1 = spawn_tank(1)
    tanks = [t0, t1]
    bullets = []
    bid = [0]

    def next_id():
        bid[0] += 1
        return bid[0]

    # t0 朝下移动
    inputs = {0: [(KEY_DOWN, True)], 1: []}
    new_bullets = step_world(grid, tanks, bullets, inputs, next_id)
    assert t0.y == 3, f"t0 应该移动到 y=3，实际 y={t0.y}"
    assert t0.dir == DIR_DOWN
    assert not new_bullets
    print('  test_basic_move OK')


def test_fire_and_kill():
    """坦克开火，子弹沿方向飞，命中对手使其死亡，开火者加分。"""
    grid = build_map()
    # 手工放置：t0 在 (10,10) 朝右，t1 在 (13,10)
    t0 = Tank(0, 10, 10, DIR_RIGHT)
    t1 = Tank(1, 13, 10, DIR_LEFT)
    tanks = [t0, t1]
    bullets = []
    bid = [0]

    def next_id():
        bid[0] += 1
        return bid[0]

    # 大坦克炮口在 (12,10)，子弹同 tick 推进并命中 (13,10)
    inputs = {0: [(KEY_FIRE, True)], 1: []}
    bullets = step_world(grid, tanks, bullets, inputs, next_id)
    assert not t1.alive, "t1 应被击杀"
    assert t0.score == 1, f"t0 应得 1 分，实际 {t0.score}"
    assert len(bullets) == 0, "命中后子弹应消失"
    print('  test_fire_and_kill OK')


def test_wall_block_move():
    """坦克不能穿墙。"""
    grid = build_map()
    # 找一个墙旁边的位置
    # 地图第 0 行全是墙，放在 (1,1) 朝上
    t0 = Tank(0, 1, 1, DIR_UP)
    t1 = spawn_tank(1)
    tanks = [t0, t1]
    bullets = []
    bid = [0]

    def next_id():
        bid[0] += 1
        return bid[0]

    # t0 朝上，上面是墙
    inputs = {0: [(KEY_UP, True)], 1: []}
    step_world(grid, tanks, bullets, inputs, next_id)
    assert t0.x == 1 and t0.y == 1, "t0 被墙挡住不应移动"
    print('  test_wall_block_move OK')


def test_cooldown():
    """开火后冷却期内不能再开火。"""
    grid = build_map()
    t0 = Tank(0, 10, 10, DIR_RIGHT)
    t1 = spawn_tank(1)
    tanks = [t0, t1]
    bullets = []
    bid = [0]

    def next_id():
        bid[0] += 1
        return bid[0]

    # 第一次开火
    inputs = {0: [(KEY_FIRE, True)], 1: []}
    bullets = step_world(grid, tanks, bullets, inputs, next_id)
    assert len(bullets) == 1
    # 立刻再开火（同一 tick 再发一次）
    bullets = step_world(grid, tanks, bullets, inputs, next_id)
    assert len(bullets) == 1, "冷却期内不应产生新子弹"
    print('  test_cooldown OK')


def test_respawn():
    """被击杀后保持死亡一段时间，倒计时归零后在出生点重生。"""
    from game import RESPAWN_TICKS
    grid = build_map()
    t0 = Tank(0, 10, 10, DIR_RIGHT)
    # t1 用 spawn_tank 创建，保证 spawn 坐标正确，但手动移到 (13,10) 便于被击中
    t1 = spawn_tank(1)
    t1.x = 13
    t1.y = 10
    t1.dir = DIR_LEFT
    tanks = [t0, t1]
    bullets = []
    bid = [0]

    def next_id():
        bid[0] += 1
        return bid[0]

    # 开火击杀 t1
    inputs = {0: [(KEY_FIRE, True)], 1: []}
    bullets = step_world(grid, tanks, bullets, inputs, next_id)
    assert not t1.alive, "t1 应被击杀"
    # 立刻检查，t1 仍应死亡
    bullets = step_world(grid, tanks, bullets, {}, next_id)
    assert not t1.alive, "重生倒计时内 t1 应保持死亡"
    # 推进到倒计时结束
    for _ in range(RESPAWN_TICKS - 1):
        bullets = step_world(grid, tanks, bullets, {}, next_id)
    assert t1.alive, "t1 应在出生点重生"
    from game import SPAWN_POINTS
    sx, sy, _ = SPAWN_POINTS[1]
    assert t1.x == sx and t1.y == sy, f"t1 应重生到出生点 ({sx},{sy})，实际 ({t1.x},{t1.y})"
    print('  test_respawn OK')


def test_bullet_hit_wall():
    """子弹撞墙后消失。"""
    grid = build_map()
    t0 = Tank(0, 1, 1, DIR_UP)  # 朝上就是墙
    t1 = spawn_tank(1)
    tanks = [t0, t1]
    bullets = []
    bid = [0]

    def next_id():
        bid[0] += 1
        return bid[0]

    inputs = {0: [(KEY_FIRE, True)], 1: []}
    bullets = step_world(grid, tanks, bullets, inputs, next_id)
    # 子弹生成在 (1,0) 即墙位置，应直接消失
    assert len(bullets) == 0, "子弹撞墙应消失"
    print('  test_bullet_hit_wall OK')


if __name__ == '__main__':
    print('运行游戏逻辑自测...')
    test_basic_move()
    test_fire_and_kill()
    test_wall_block_move()
    test_cooldown()
    test_respawn()
    test_bullet_hit_wall()
    test_large_tank_footprint_and_muzzle()
    test_small_tank_occupies_one_cell()
    test_brick_breaks_and_bullet_reflects()
    test_own_ricochet_can_kill_owner()
    test_self_kill_scores_for_opponent()
    test_self_kill_can_end_match_for_opponent()
    test_shield_absorbs_one_hit()
    test_triple_shot_is_consumed()
    test_parallel_triple_shot_all_directions()
    test_diagonal_move_muzzle_and_component_ricochet()
    test_diagonal_corner_counts_one_bounce_and_expires_at_limit()
    test_shrink_timer_expires()
    test_world_countdown_powerups_win_and_restart()
    test_shrink_recovery_defers_when_blocked()
    test_respawn_requires_full_footprint()
    print('全部通过')
