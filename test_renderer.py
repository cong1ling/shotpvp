"""渲染缓冲区测试，不操作真实终端。"""
from game import build_map, Tank, Bullet, PowerUp, POWERUP_SHIELD
from renderer import Renderer, ITEM_CHARS, ITEM_BORDER, BULLET_CHAR
from protocol import DIR_UP_LEFT, DIR_UP_RIGHT, DIR_DOWN_LEFT, DIR_DOWN_RIGHT


def test_world_glyphs():
    grid = build_map()
    tank = Tank(0, 10, 10)
    bullet = Bullet(1, 15, 10, 0, 0)
    item = PowerUp(1, 20, 10, POWERUP_SHIELD)
    buf = Renderer(64, 30).build_buffer(grid, [tank], [bullet], 0, [item])
    assert buf[0][0] is not None and buf[10][10] is not None
    assert buf[10][15][0] == BULLET_CHAR
    assert buf[10][20] == ITEM_CHARS[POWERUP_SHIELD]
    assert all(buf[10 + oy][20 + ox] == ITEM_BORDER
               for oy in (-1, 0, 1) for ox in (-1, 0, 1) if (ox, oy) != (0, 0))


def test_entities_draw_above_visual_item_footprint():
    grid = build_map()
    item = PowerUp(1, 20, 10, POWERUP_SHIELD)
    tank = Tank(0, 19, 10)
    bullet = Bullet(1, 20, 10, 0, 0)
    buf = Renderer(64, 30).build_buffer(grid, [tank], [bullet], 0, [item])
    assert buf[10][19] != ITEM_BORDER
    assert buf[10][20][0] == BULLET_CHAR


def test_diagonal_tanks_have_barrels():
    for direction in (DIR_UP_LEFT, DIR_UP_RIGHT, DIR_DOWN_LEFT, DIR_DOWN_RIGHT):
        tank = Tank(0, 20, 15, direction)
        buf = Renderer(64, 30).build_buffer(build_map(), [tank], [], 0)
        from protocol import DIR_DELTA
        dx, dy = DIR_DELTA[direction]
        assert buf[15 + dy * 2][20 + dx * 2][0] in ('╲', '╱')


if __name__ == '__main__':
    test_world_glyphs()
    test_entities_draw_above_visual_item_footprint()
    test_diagonal_tanks_have_barrels()
    print('渲染测试全部通过')
