"""UDP 二进制消息协议。

所有消息首字节为类型，后续为定长 struct 字段。
STATE 是变长的：header(tick, tank_count, bullet_count) + tanks[] + bullets[]。
"""
import struct

PROTOCOL_VERSION = 3
MAP_W = 64
MAP_H = 30
PHASES = {0, 1, 2}
POWERUP_KINDS = {0, 1, 2}

# ---- 消息类型 ----
MSG_JOIN = 1        # C2S: 请求加入
MSG_JOIN_ACK = 2    # S2C: 确认加入，分配 player_id
MSG_INPUT = 3        # C2S: 按键事件 (key, is_down)
MSG_STATE = 4       # S2C: 世界快照
MSG_LEAVE = 5       # C2S: 离开
MSG_PING = 6        # C2S: RTT 探测，携带客户端时间戳
MSG_PONG = 7        # S2C: 原样回显时间戳

# ---- 输入键 ----
KEY_UP = 0
KEY_DOWN = 1
KEY_LEFT = 2
KEY_RIGHT = 3
KEY_FIRE = 8

# ---- 方向（与 KEY 同值，方便复用）----
DIR_UP = 0
DIR_DOWN = 1
DIR_LEFT = 2
DIR_RIGHT = 3
DIR_UP_LEFT = 4
DIR_UP_RIGHT = 5
DIR_DOWN_LEFT = 6
DIR_DOWN_RIGHT = 7

# 方向位移：朝该方向移动一格的 (dx, dy)
DIR_DELTA = {
    DIR_UP: (0, -1),
    DIR_DOWN: (0, 1),
    DIR_LEFT: (-1, 0),
    DIR_RIGHT: (1, 0),
    DIR_UP_LEFT: (-1, -1),
    DIR_UP_RIGHT: (1, -1),
    DIR_DOWN_LEFT: (-1, 1),
    DIR_DOWN_RIGHT: (1, 1),
}
DELTA_DIR = {delta: direction for direction, delta in DIR_DELTA.items()}

# ---- struct 格式 ----
# JOIN: type(1) + ver(1)
JOIN_FMT = struct.Struct('!BB')
# JOIN_ACK: type(1) + player_id(1)
JOIN_ACK_FMT = struct.Struct('!BB')
# INPUT: type(1) + key(1) + is_down(1)
INPUT_FMT = struct.Struct('!BBB')
# LEAVE: type(1)
LEAVE_FMT = struct.Struct('!B')
PING_FMT = struct.Struct('!BQ')
# STATE header: type(1) + tick(4, unsigned) + tank_count(1) + bullet_count(1)
STATE_HEADER_FMT = struct.Struct('!BIBHBBBBBB')
# TANK: id(1) + x(1) + y(1) + dir(1) + alive(1) + score(1)
TANK_FMT = struct.Struct('!BBBBBB')
# BULLET: id(1) + x(1) + y(1) + dir(1) + owner(1)
BULLET_FMT = struct.Struct('!BBBBB')
TANK_V2_FMT = struct.Struct('!HBBBBBHBBB')
BULLET_V2_FMT = struct.Struct('!HBBBBB')
ITEM_FMT = struct.Struct('!HBBB')


# ---- 序列化 ----
def encode_join(version: int = PROTOCOL_VERSION) -> bytes:
    return JOIN_FMT.pack(MSG_JOIN, version)


def encode_join_ack(player_id: int) -> bytes:
    return JOIN_ACK_FMT.pack(MSG_JOIN_ACK, player_id)


def encode_input(key: int, is_down: bool) -> bytes:
    return INPUT_FMT.pack(MSG_INPUT, key, 1 if is_down else 0)


def encode_leave() -> bytes:
    return LEAVE_FMT.pack(MSG_LEAVE)


def encode_ping(timestamp_ns: int) -> bytes:
    return PING_FMT.pack(MSG_PING, timestamp_ns)


def encode_pong(timestamp_ns: int) -> bytes:
    return PING_FMT.pack(MSG_PONG, timestamp_ns)


def _pack_map(grid):
    flat = [tile for row in grid for tile in row]
    out = bytearray((len(flat) + 3) // 4)
    for i, tile in enumerate(flat):
        out[i // 4] |= (tile & 3) << ((i % 4) * 2)
    return out


def _unpack_map(data, width, height):
    return [[(data[(y * width + x) // 4] >> (((y * width + x) % 4) * 2)) & 3
             for x in range(width)] for y in range(height)]


def encode_state(tick: int, tanks: list, bullets: list, grid=None, items=None,
                 phase=1, phase_ticks=0, winner=255) -> bytes:
    grid = grid if grid is not None else [[0] * MAP_W for _ in range(MAP_H)]
    items = items or []
    buf = bytearray()
    h, w = len(grid), len(grid[0]) if grid else 0
    buf += STATE_HEADER_FMT.pack(MSG_STATE, tick & 0xFFFFFFFF, phase, phase_ticks,
                                 winner, w, h, len(tanks), len(bullets), len(items))
    buf += _pack_map(grid)
    for t in tanks:
        if len(t) == 6:
            t = (*t, 0, 0, 0, 0)
        buf += TANK_V2_FMT.pack(*t)
    for b in bullets:
        if len(b) == 5:
            b = (*b, 0)
        buf += BULLET_V2_FMT.pack(*b)
    for item in items:
        buf += ITEM_FMT.pack(*item)
    return bytes(buf)


def decode(data: bytes):
    """返回 (msg_type, parsed_dict_or_tuple)。未知/坏包返回 None。"""
    if not data:
        return None
    mtype = data[0]
    if mtype == MSG_JOIN:
        if len(data) != JOIN_FMT.size:
            return None
        _, ver = JOIN_FMT.unpack(data[:JOIN_FMT.size])
        return (MSG_JOIN, {'version': ver})
    if mtype == MSG_JOIN_ACK:
        if len(data) != JOIN_ACK_FMT.size:
            return None
        _, pid = JOIN_ACK_FMT.unpack(data[:JOIN_ACK_FMT.size])
        return (MSG_JOIN_ACK, {'player_id': pid})
    if mtype == MSG_INPUT:
        if len(data) != INPUT_FMT.size:
            return None
        _, key, is_down = INPUT_FMT.unpack(data[:INPUT_FMT.size])
        if key not in set(DIR_DELTA) | {KEY_FIRE} or is_down not in (0, 1):
            return None
        return (MSG_INPUT, {'key': key, 'is_down': bool(is_down)})
    if mtype == MSG_LEAVE:
        if len(data) != LEAVE_FMT.size:
            return None
        return (MSG_LEAVE, {})
    if mtype in (MSG_PING, MSG_PONG):
        if len(data) != PING_FMT.size:
            return None
        _, timestamp_ns = PING_FMT.unpack(data[:PING_FMT.size])
        return (mtype, {'timestamp_ns': timestamp_ns})
    if mtype == MSG_STATE:
        if len(data) < STATE_HEADER_FMT.size:
            return None
        _, tick, phase, phase_ticks, winner, width, height, tcount, bcount, icount = STATE_HEADER_FMT.unpack(data[:STATE_HEADER_FMT.size])
        if phase not in PHASES or width != MAP_W or height != MAP_H or tcount > 8 or bcount > 255 or icount > 32:
            return None
        off = STATE_HEADER_FMT.size
        map_size = (width * height + 3) // 4
        expected = off + map_size + tcount * TANK_V2_FMT.size + bcount * BULLET_V2_FMT.size + icount * ITEM_FMT.size
        if len(data) != expected:
            return None
        grid = _unpack_map(data[off:off + map_size], width, height)
        if any(tile > 2 for row in grid for tile in row):
            return None
        off += map_size
        tanks = []
        tank_ids = set()
        for _ in range(tcount):
            tank = TANK_V2_FMT.unpack(data[off:off + TANK_V2_FMT.size])
            tid, x, y, direction, alive, score, shrink, shield, triple, ready = tank
            if (tid in tank_ids or x >= width or y >= height or direction not in DIR_DELTA or
                    alive not in (0, 1) or shield not in (0, 1) or
                    triple not in (0, 1) or ready not in (0, 1)):
                return None
            tank_ids.add(tid)
            tanks.append(tank)
            off += TANK_V2_FMT.size
        bullets = []
        bullet_ids = set()
        for _ in range(bcount):
            bullet = BULLET_V2_FMT.unpack(data[off:off + BULLET_V2_FMT.size])
            bid, x, y, direction, owner, bounces = bullet
            if (bid in bullet_ids or x >= width or y >= height or
                    direction not in DIR_DELTA or bounces > 5):
                return None
            bullet_ids.add(bid)
            bullets.append(bullet)
            off += BULLET_V2_FMT.size
        items = []
        item_ids = set()
        for _ in range(icount):
            item = ITEM_FMT.unpack(data[off:off + ITEM_FMT.size])
            if (item[0] in item_ids or item[1] >= width or item[2] >= height or
                    item[3] not in POWERUP_KINDS):
                return None
            item_ids.add(item[0])
            items.append(item)
            off += ITEM_FMT.size
        if (phase == 2 and winner not in tank_ids) or (phase != 2 and winner != 255):
            return None
        return (MSG_STATE, {'tick': tick, 'phase': phase, 'phase_ticks': phase_ticks,
                            'winner': winner, 'width': width, 'height': height, 'grid': grid,
                            'tanks': tanks, 'bullets': bullets, 'items': items})
    return None
