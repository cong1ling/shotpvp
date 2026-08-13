"""客户端入口。

职责：
  - 启动 ConsoleInput 进入 raw 输入模式
  - 主循环：采集按键事件 -> 打包发 INPUT 包 -> 渲染当前已知状态
  - 副线程：收 STATE 包，更新本地状态副本
  - 本地预测：按键按下立即让本地坦克移动（不等服务器回包）
  - 远端插值：收到 STATE 后，远端坦克位置在两帧之间平滑过渡

连接：默认连 127.0.0.1:47000。
"""
import socket
import sys
import threading
import time
import argparse
import os

from protocol import (
    MSG_JOIN, MSG_JOIN_ACK, MSG_INPUT, MSG_STATE, MSG_LEAVE, MSG_PONG,
    decode, encode_join, encode_input, encode_leave, encode_ping,
    KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_FIRE,
    DIR_UP, DIR_DOWN, DIR_LEFT, DIR_RIGHT, DIR_DELTA,
)
from game import MAP_W, MAP_H, build_map, Tank, Bullet, PowerUp, is_wall, can_place_tank, PHASE_PLAYING
from renderer import Renderer
from input_win import (ConsoleInput, map_events, VK_ESCAPE, VK_W, VK_A, VK_S, VK_D,
                       VK_UP, VK_DOWN, VK_LEFT, VK_RIGHT)
from input_intent import compose_direction
from connectivity import recv_udp


SERVER_HOST = '127.0.0.1'
SERVER_PORT = 47000
TICK_HZ = 30
TICK_DT = 1.0 / TICK_HZ

# 渲染频率独立于逻辑帧
RENDER_HZ = 60
RENDER_DT = 1.0 / RENDER_HZ
INTERPOLATION_DELAY = 0.10
STATE_TIMEOUT = 3.0
PING_INTERVAL = 1.0


def make_local_grid():
    """客户端也保留一份地图，用于预测和渲染。"""
    return build_map()


def reconstruct_tanks(state_tanks):
    """把 STATE 里的 tank tuple 列表转成 Tank 对象。"""
    out = []
    for tid, x, y, d, alive, score, shrink, shield, triple, ready in state_tanks:
        t = Tank(tid, x, y, d)
        t.alive = bool(alive)
        t.score = score
        t.shrink_ticks, t.shield, t.triple_shot, t.restart_ready = shrink, shield, bool(triple), bool(ready)
        out.append(t)
    return out


def reconstruct_bullets(state_bullets):
    out = []
    for bid, x, y, d, owner, bounces in state_bullets:
        out.append(Bullet(bid, x, y, d, owner, bounces))
    return out


def ensure_socket_bound(sock):
    """Bind an unbound UDP socket, including Windows WSAEINVAL behavior."""
    try:
        local_port = sock.getsockname()[1]
    except OSError:
        local_port = 0
    if local_port == 0:
        sock.bind(('::' if sock.family == socket.AF_INET6 else '0.0.0.0', 0))
    return sock


def wait_for_join_ack(sock, server_addr, timeout=5.0, retry_interval=0.5):
    """Reliably complete JOIN over UDP by retransmitting until acknowledged."""
    deadline = time.monotonic() + timeout
    next_send = 0.0
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_send:
            sock.sendto(encode_join(), server_addr)
            next_send = now + retry_interval
        received = recv_udp(sock)
        if received is None:
            time.sleep(0.01)
            continue
        msg = decode(received[0])
        if msg and msg[0] == MSG_JOIN_ACK:
            return msg[1]['player_id']
    return None


def run_client(server_host=SERVER_HOST, server_port=SERVER_PORT, embedded_server=None, sock=None,
               keepalive=None):
    # 1. 建立 UDP socket
    sock = sock or socket.socket(socket.AF_INET6 if ':' in server_host else socket.AF_INET, socket.SOCK_DGRAM)
    ensure_socket_bound(sock)
    server_addr = (server_host, server_port)
    sock.setblocking(False)

    # 2. JOIN 是关键 UDP 握手，在截止时间内定期重发。
    print('正在连接 %s:%d ...' % server_addr)
    print('按 ESC 退出')

    # 3. 等 JOIN_ACK，拿 player_id
    player_id = wait_for_join_ack(sock, server_addr)
    if player_id is None:
        print('连接失败：未收到服务器响应')
        sys.exit(1)
    print('已连接，你的 player_id = %d' % player_id)
    print('（等另一个客户端加入后开始）')

    # 4. 启动输入与渲染
    con = ConsoleInput()
    renderer = Renderer(MAP_W, MAP_H)
    renderer.start()

    grid = make_local_grid()

    # 本地状态：最近两个 STATE 快照，用于远端插值
    prev_snapshot = None
    curr_snapshot = None
    snapshot_lock = threading.Lock()

    # ping 估算（原型阶段用固定值示意）
    ping_ms = 0
    last_state_at = time.perf_counter()

    # 客户端预测用的本地坦克
    local_tank = None

    running = True

    def recv_loop():
        nonlocal prev_snapshot, curr_snapshot, local_tank, ping_ms, last_state_at
        while running:
            try:
                received = recv_udp(sock)
                if received is None:
                    time.sleep(0.001)
                    continue
                data, _ = received
            except OSError:
                break
            msg = decode(data)
            if msg is None:
                continue
            if msg[0] == MSG_STATE:
                with snapshot_lock:
                    prev_snapshot = curr_snapshot
                    tanks = reconstruct_tanks(msg[1]['tanks'])
                    bullets = reconstruct_bullets(msg[1]['bullets'])
                    received_at = time.perf_counter()
                    curr_snapshot = (msg[1]['tick'], tanks, bullets, received_at, msg[1])
                    last_state_at = received_at
                    # 同步本地坦克（以服务器为准，纠正预测漂移）
                    for t in tanks:
                        if t.id == player_id:
                            local_tank = t
                            break
            elif msg[0] == MSG_PONG:
                elapsed_ns = time.perf_counter_ns() - msg[1]['timestamp_ns']
                ping_ms = max(0, round(elapsed_ns / 1_000_000))

    t = threading.Thread(target=recv_loop, daemon=True)
    t.start()

    # 6. 主循环
    last_render = time.perf_counter()
    last_input_tick = last_render
    last_ping = last_render - PING_INTERVAL
    held_directions = set()  # WASD only; arrows deliberately remain cardinal.
    held_arrows = set()
    arrow_order = []

    try:
        while running:
            now = time.perf_counter()

            # 6.1 采集输入
            raw_events = con.read_events()
            events = map_events(raw_events)
            wasd_keys = {VK_W: KEY_UP, VK_S: KEY_DOWN, VK_A: KEY_LEFT, VK_D: KEY_RIGHT}
            arrow_keys = {VK_UP: KEY_UP, VK_DOWN: KEY_DOWN, VK_LEFT: KEY_LEFT, VK_RIGHT: KEY_RIGHT}
            for vk, is_down in raw_events:
                if vk in wasd_keys:
                    key = wasd_keys[vk]
                    (held_directions.add if is_down else held_directions.discard)(key)
                elif vk in arrow_keys:
                    key = arrow_keys[vk]
                    if is_down:
                        held_arrows.add(key)
                        if key in arrow_order: arrow_order.remove(key)
                        arrow_order.append(key)
                    else:
                        held_arrows.discard(key)
                        if key in arrow_order: arrow_order.remove(key)
            for key, is_down in events:
                if key == KEY_FIRE and is_down:
                    try:
                        sock.sendto(encode_input(key, True), server_addr)
                    except OSError:
                        pass

            # 按住方向键时，每个逻辑 tick 发送一次移动意图并做本地预测。
            if now - last_input_tick >= TICK_DT:
                last_input_tick = now
                move_key = arrow_order[-1] if arrow_order else compose_direction(held_directions)
                if move_key is not None:
                    try:
                        sock.sendto(encode_input(move_key, True), server_addr)
                    except OSError:
                        pass
                    # 预测守卫：本地坦克存活以外，还需以服务器最新快照为准——
                    # local_tank.alive 可能滞后于服务器已判定的死亡，避免"幽灵预测"继续移动。
                    server_alive = True
                    snapshot_tanks = curr_snapshot[1] if curr_snapshot else []
                    for t in snapshot_tanks:
                        if t.id == player_id:
                            server_alive = t.alive
                            break
                    if (local_tank and local_tank.alive and server_alive
                            and curr_snapshot and curr_snapshot[4]['phase'] == PHASE_PLAYING):
                        local_tank.dir = move_key
                        dx, dy = DIR_DELTA[move_key]
                        nx, ny = local_tank.x + dx, local_tank.y + dy
                        remote_tanks = [t for t in snapshot_tanks if t.id != player_id]
                        if can_place_tank(grid, local_tank, nx, ny, remote_tanks):
                            local_tank.x, local_tank.y = nx, ny

            if now - last_ping >= PING_INTERVAL:
                last_ping = now
                try:
                    sock.sendto(encode_ping(time.perf_counter_ns()), server_addr)
                except OSError:
                    pass

            # ESC 退出
            if raw_events and any(vk == VK_ESCAPE and is_down for vk, is_down in raw_events):
                break

            # 6.2 渲染
            if now - last_render >= RENDER_DT:
                last_render = now
                with snapshot_lock:
                    if curr_snapshot is None:
                        # 还没收到任何状态：画空地图
                        renderer.render(grid, [], [], player_id)
                        continue
                    _, tanks, bullets, curr_at, state = curr_snapshot
                    grid = state['grid']
                    items = [PowerUp(*item) for item in state['items']]
                    prev = prev_snapshot

                # 用本地预测的 local_tank 覆盖远端给我们的那个坦克
                # 为渲染创建副本，避免插值结果反向污染网络快照。
                display_tanks = reconstruct_tanks([t.to_state_tuple() for t in tanks])
                if local_tank is not None:
                    display_tanks = [local_tank if t.id == player_id else t for t in display_tanks]

                # 远端坦克在前后快照间插值；字符网格最终取最近格。
                if prev is not None:
                    _, prev_tanks, _, prev_at, _ = prev
                    span = max(curr_at - prev_at, TICK_DT)
                    alpha = max(0.0, min(1.0, (now - INTERPOLATION_DELAY - prev_at) / span))
                    prev_by_id = {t.id: t for t in prev_tanks}
                    for tank in display_tanks:
                        old = prev_by_id.get(tank.id)
                        if tank.id != player_id and old and old.alive and tank.alive:
                            tank.x = round(old.x + (tank.x - old.x) * alpha)
                            tank.y = round(old.y + (tank.y - old.y) * alpha)

                renderer.render(grid, display_tanks, bullets, player_id, items)

                # HUD
                if display_tanks:
                    me = next((t for t in display_tanks if t.id == player_id), None)
                    other = next((t for t in display_tanks if t.id != player_id), None)
                    if me and other:
                        connection = '连接中断' if now - last_state_at > STATE_TIMEOUT else '在线'
                        renderer.render_hud(me, other, ping_ms, connection, state['phase'],
                                            state['phase_ticks'], state['winner'])
    except KeyboardInterrupt:
        pass
    finally:
        running = False
        try:
            sock.sendto(encode_leave(), server_addr)
        except OSError:
            pass
        con.close()
        renderer.stop()
        sock.close()
        if keepalive: keepalive.stop()
        if embedded_server: embedded_server.stop()
        t.join(timeout=0.5)
        print('\n已退出')


def main():
    parser = argparse.ArgumentParser(description='ShotPVP 双人直连')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--host', action='store_true', help='创建房间并嵌入服务端')
    modes.add_argument('--join-code', help='使用主机连接码加入')
    modes.add_argument('--connect', metavar='HOST[:PORT]', help='直接连接 IP/域名')
    args = parser.parse_args()
    embedded = None
    if args.host:
        from server import GameServer
        from connectivity import ProbeSession, gather_candidates
        from connection_code import new_session, encode_code, KIND_OFFER
        record = new_session(KIND_OFFER, [])
        probe = ProbeSession(record.session_id, record.secret)
        embedded = GameServer('0.0.0.0', 0, probe_session=probe)
        stun_hosts = []
        for value in os.environ.get('SHOTPVP_STUN', 'stun.cloudflare.com:3478,stun.l.google.com:19302').split(','):
            host, sep, port = value.strip().rpartition(':')
            if sep: stun_hosts.append((host, int(port)))
        candidates, resources, warnings = gather_candidates(embedded.sock, stun_hosts,
                                                             enable_upnp=True, diagnostic=False)
        for warning in warnings: print('[直连提示]', warning)
        embedded.start()
        record = type(record)(record.kind, record.created_minute, record.session_id, record.secret, tuple(candidates))
        print('将以下主机连接码发给对方（10 分钟有效，包含临时地址信息）：')
        print(encode_code(record))
        print('对方可回传应答码用于人工确认；当前直连由认证探测自动绑定。')
        try: run_client('127.0.0.1', embedded.address[1], embedded)
        finally:
            for resource in resources: resource.close()
    elif args.join_code:
        from connection_code import decode_code, make_answer, encode_code
        from connectivity import probe_candidates, Keepalive
        try: offer = decode_code(args.join_code)
        except ValueError as exc: parser.error(str(exc))
        if not offer.candidates:
            parser.error('主机连接码没有可探测地址')
        # A single UDP socket cannot portably probe mixed families on Windows.
        # Prefer IPv6 only when the offer contains no IPv4 path.
        family = socket.AF_INET if any(c.family == 4 for c in offer.candidates) else socket.AF_INET6
        sock = socket.socket(family, socket.SOCK_DGRAM)
        sock.bind(('::' if sock.family == socket.AF_INET6 else '0.0.0.0', 0))
        family_number = 6 if family == socket.AF_INET6 else 4
        compatible = [candidate for candidate in offer.candidates if candidate.family == family_number]
        endpoint = probe_candidates(sock, compatible, offer.session_id, offer.secret)
        local = sock.getsockname()
        answer_candidates = []
        try:
            from connection_code import Candidate, TYPE_LAN
            answer_candidates = [Candidate(6 if ':' in local[0] else 4, TYPE_LAN, local[0], local[1])]
        except ValueError: pass
        print('应答码（可发回主机确认本次会话）：')
        print(encode_code(make_answer(offer, answer_candidates)))
        if endpoint is None:
            parser.error('10 秒内未找到可用直连路径；请检查 Windows 防火墙、UPnP 与运营商 NAT，或使用 --connect IP:端口')
        keeper = Keepalive(sock, endpoint, offer.session_id, offer.secret).start()
        run_client(endpoint[0], endpoint[1], sock=sock, keepalive=keeper)
    elif args.connect:
        host, sep, port = args.connect.rpartition(':')
        run_client(host if sep else args.connect, int(port) if sep else SERVER_PORT)
    else:
        run_client()


if __name__ == '__main__':
    main()
