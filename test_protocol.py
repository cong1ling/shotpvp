"""协议编解码往返测试。"""
from protocol import (
    MSG_PING, MSG_PONG, MSG_STATE, decode, encode_ping, encode_pong,
    encode_state, encode_join, encode_input, encode_leave, PROTOCOL_VERSION,
)
import struct


def test_ping_roundtrip():
    assert PROTOCOL_VERSION == 3
    stamp = 123456789012345
    assert decode(encode_ping(stamp)) == (MSG_PING, {'timestamp_ns': stamp})
    assert decode(encode_pong(stamp)) == (MSG_PONG, {'timestamp_ns': stamp})


def test_state_roundtrip():
    from game import build_map
    tanks = [(0, 2, 3, 1, 1, 4, 99, 1, 1, 0)]
    bullets = [(7, 5, 6, 3, 0, 2)]
    items = [(9, 20, 10, 1)]
    packet = encode_state(42, tanks, bullets, build_map(), items, 2, 0, 0)
    decoded = decode(packet)
    assert decoded[1]['tick'] == 42 and decoded[1]['phase'] == 2
    assert decoded[1]['tanks'] == tanks and decoded[1]['bullets'] == bullets
    assert decoded[1]['items'] == items and decoded[1]['grid'] == build_map()
    assert decode(packet[:-1]) is None
    assert decode(packet + b'junk') is None


def test_invalid_packets_are_rejected():
    for packet in (encode_join(), encode_input(0, True), encode_leave(), encode_ping(1)):
        assert decode(packet + b'junk') is None
    assert decode(struct.pack('!BBB', 3, 99, 1)) is None
    assert decode(struct.pack('!BBB', 3, 0, 2)) is None
    from game import build_map
    assert decode(encode_state(1, [(0, 2, 3, 9, 1, 0, 0, 0, 0, 0)], [], build_map())) is None
    assert decode(encode_state(1, [], [(1, 5, 6, 0, 0, 6)], build_map())) is None
    assert decode(encode_state(1, [], [], build_map(), [(1, 64, 10, 0)])) is None


def test_all_eight_directions_roundtrip():
    from game import build_map
    tanks = [(i, 2 + i, 3, i, 1, 0, 0, 0, 0, 0) for i in range(8)]
    bullets = [(i, 10 + i, 10, i, 0, 0) for i in range(8)]
    decoded = decode(encode_state(1, tanks, bullets, build_map()))
    assert decoded[1]['tanks'] == tanks and decoded[1]['bullets'] == bullets
    assert decode(encode_state(1, [(0, 2, 3, 8, 1, 0, 0, 0, 0, 0)], [], build_map())) is None


def test_state_identity_and_winner_validation():
    from game import build_map
    tank = (0, 2, 3, 0, 1, 0, 0, 0, 0, 0)
    assert decode(encode_state(1, [tank, tank], [], build_map())) is None
    assert decode(encode_state(1, [tank], [], build_map(), phase=1, winner=0)) is None
    assert decode(encode_state(1, [tank], [], build_map(), phase=2, winner=1)) is None


if __name__ == '__main__':
    test_ping_roundtrip()
    test_state_roundtrip()
    test_invalid_packets_are_rejected()
    test_all_eight_directions_roundtrip()
    test_state_identity_and_winner_validation()
    print('协议测试全部通过')
