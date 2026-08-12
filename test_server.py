import socket, time
from server import GameServer
from client import ensure_socket_bound
from protocol import encode_join, decode, MSG_JOIN_ACK
from connectivity import ProbeSession, probe_candidates
from connection_code import Candidate, TYPE_LAN

def test_lifecycle_localhost():
    server = GameServer('127.0.0.1', 0).start()
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); client.settimeout(1)
    client.sendto(encode_join(), server.address)
    assert decode(client.recvfrom(1024)[0])[0] == MSG_JOIN_ACK
    client.close(); server.stop(); assert not server.running

def test_authenticated_local_probe_then_game_join():
    sid, secret = b's'*16, b'k'*32
    server = GameServer('127.0.0.1', 0, probe_session=ProbeSession(sid, secret)).start()
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); client.bind(('127.0.0.1', 0))
    endpoint = probe_candidates(client, [Candidate(4, TYPE_LAN, '127.0.0.1', server.address[1])], sid, secret, timeout=.15, retries=2)
    assert endpoint == ('127.0.0.1', server.address[1])
    client.settimeout(1); client.sendto(encode_join(), endpoint)
    assert decode(client.recvfrom(2048)[0])[0] == MSG_JOIN_ACK
    client.close(); server.stop()


def test_unbound_client_socket_is_bound_before_use():
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    ensure_socket_bound(client)
    assert client.getsockname()[1] != 0
    client.close()

if __name__=='__main__': test_lifecycle_localhost(); test_authenticated_local_probe_then_game_join(); print('服务端生命周期测试全部通过')
