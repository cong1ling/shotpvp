from input_intent import compose_direction
from protocol import *


def test_composition():
    assert compose_direction({KEY_UP, KEY_LEFT}) == DIR_UP_LEFT
    assert compose_direction({KEY_UP, KEY_RIGHT}) == DIR_UP_RIGHT
    assert compose_direction({KEY_DOWN, KEY_LEFT}) == DIR_DOWN_LEFT
    assert compose_direction({KEY_DOWN, KEY_RIGHT}) == DIR_DOWN_RIGHT
    assert compose_direction({KEY_UP, KEY_DOWN, KEY_LEFT}) == DIR_LEFT
    assert compose_direction({KEY_UP, KEY_LEFT} - {KEY_LEFT}) == DIR_UP
    assert compose_direction({KEY_LEFT, KEY_RIGHT}) is None


if __name__ == '__main__':
    test_composition()
    print('输入意图测试全部通过')
