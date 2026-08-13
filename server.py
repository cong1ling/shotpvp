"""权威服务端。

单进程 + 线程：
  - 主线程：游戏逻辑循环，30Hz tick
  - 副线程：接收 UDP 包，把消息塞进队列

约定端口 47000。两个客户端连接。
"""
import socket
import threading
import time

from protocol import (
    MSG_JOIN, MSG_INPUT, MSG_LEAVE, MSG_PING,
    decode, encode_join_ack, encode_state, encode_pong, PROTOCOL_VERSION,
)
from game import World

SERVER_PORT = 47000
TICK_HZ = 30
TICK_DT = 1.0 / TICK_HZ
MAX_CLIENTS = 2


class GameServer:
    """可嵌入客户端或独立运行的权威 UDP 服务端。"""
    def __init__(self, host='0.0.0.0', port=SERVER_PORT, sock=None, tick_hz=TICK_HZ,
                 probe_session=None):
        self.world = World(MAX_CLIENTS)
        self.clients = {}
        self.pending_inputs = {0: [], 1: []}
        self.lock = threading.Lock()
        self.sock = sock or socket.socket(socket.AF_INET6 if ':' in host else socket.AF_INET, socket.SOCK_DGRAM)
        if sock is None:
            self.sock.bind((host, port))
        self.sock.setblocking(False)
        self.tick_dt = 1.0 / tick_hz
        self.running = False
        self.thread = None
        self.probe_session = probe_session
        self.authenticated_endpoint = None

    @property
    def address(self):
        return self.sock.getsockname()

    def start(self):
        if self.running: return self
        self.running = True
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        if not self.running and getattr(self.sock, '_closed', True):
            return
        self.running = False
        try: self.sock.close()
        except OSError: pass
        if self.thread and self.thread is not threading.current_thread(): self.thread.join(1.0)

    def _tid(self, addr):
        return next((tid for tid, (known, _) in self.clients.items() if known == addr), None)

    def _receive(self):
        try: data, addr = self.sock.recvfrom(65535)
        except BlockingIOError: return
        except OSError: return
        if self.probe_session:
            response = self.probe_session.receive(data, addr)
            if isinstance(response, bytes):
                if self.authenticated_endpoint is None:
                    self.authenticated_endpoint = addr
                try: self.sock.sendto(response, addr)
                except OSError: pass
                return
        if self.authenticated_endpoint and addr != self.authenticated_endpoint and addr[0] not in ('127.0.0.1', '::1'):
            return
        msg = decode(data)
        if not msg: return
        mtype, payload = msg
        with self.lock:
            if mtype == MSG_JOIN:
                if payload.get('version') != PROTOCOL_VERSION: return
                existing = self._tid(addr)
                if existing is not None:
                    self.sock.sendto(encode_join_ack(existing), addr); return
                if len(self.clients) >= MAX_CLIENTS: return
                tid = len(self.clients); self.clients[tid] = (addr, PROTOCOL_VERSION)
                self.sock.sendto(encode_join_ack(tid), addr)
            elif mtype == MSG_INPUT:
                tid = self._tid(addr)
                if tid is not None: self.pending_inputs[tid].append((payload['key'], payload['is_down']))
            elif mtype == MSG_PING: self.sock.sendto(encode_pong(payload['timestamp_ns']), addr)
            elif mtype == MSG_LEAVE:
                tid = self._tid(addr)
                if tid is not None: self.world.tanks[tid].alive = False

    def tick(self):
        with self.lock:
            inputs = {tid: list(events) for tid, events in self.pending_inputs.items()}
            for events in self.pending_inputs.values(): events.clear()
            if len(self.clients) == MAX_CLIENTS: self.world.step(inputs)
            snap = self.world.snapshot()
            packet = encode_state(snap['tick'], snap['tanks'], snap['bullets'], snap['grid'], snap['items'], snap['phase'], snap['phase_ticks'], snap['winner'])
            for addr, _ in self.clients.values():
                try: self.sock.sendto(packet, addr)
                except OSError: pass

    def run(self):
        next_tick = time.perf_counter()
        while self.running:
            self._receive()
            now = time.perf_counter()
            if now >= next_tick:
                self.tick(); next_tick = now + self.tick_dt
            time.sleep(0.001)


def main():
    server = GameServer()
    print('[server] 监听 %s:%d' % server.address[:2])
    server.running = True
    try: server.run()
    except KeyboardInterrupt: print('\n[server] 收到 Ctrl+C，关闭')
    finally: server.stop()


if __name__ == '__main__': main()
