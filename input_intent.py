"""把客户端当前按住的四向意图组合为八方向移动。"""
from protocol import (KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, DIR_UP_LEFT,
                      DIR_UP_RIGHT, DIR_DOWN_LEFT, DIR_DOWN_RIGHT)


def compose_direction(held):
    vertical = 0 if (KEY_UP in held) == (KEY_DOWN in held) else (-1 if KEY_UP in held else 1)
    horizontal = 0 if (KEY_LEFT in held) == (KEY_RIGHT in held) else (-1 if KEY_LEFT in held else 1)
    directions = {
        (0, -1): KEY_UP, (0, 1): KEY_DOWN, (-1, 0): KEY_LEFT, (1, 0): KEY_RIGHT,
        (-1, -1): DIR_UP_LEFT, (1, -1): DIR_UP_RIGHT,
        (-1, 1): DIR_DOWN_LEFT, (1, 1): DIR_DOWN_RIGHT,
    }
    return directions.get((horizontal, vertical))
