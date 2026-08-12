import struct
from connectivity import *


class ResettingSocket:
    def recvfrom(self, _size):
        error = ConnectionResetError('UDP destination unreachable')
        error.winerror = 10054
        raise error


def test_windows_udp_reset_is_transient():
    assert recv_udp(ResettingSocket()) is None

def test_probe_replay_rtt():
    s = ProbeSession(b'a'*16, b'b'*32); packet = s.challenge(1, 100, ('127.0.0.1', 1))
    peer = ProbeSession(b'a'*16, b'b'*32); response = peer.receive(packet, ('x',1), 120)
    assert s.receive(response, ('127.0.0.1', 1), 200) == 0.0001
    assert s.receive(response, ('127.0.0.1', 1), 210) is None
    bad = bytearray(packet); bad[-1] ^= 1; assert decode_probe(bytes(bad), b'a'*16, b'b'*32) is None

def test_probe_replay_and_nonce_are_endpoint_bound():
    sid, secret = b'a'*16, b'b'*32
    sender, peer = ProbeSession(sid, secret), ProbeSession(sid, secret)
    a, b = ('127.0.0.1', 1), ('127.0.0.1', 2)
    challenge = sender.challenge(5, 100, a)
    response = peer.receive(challenge, a, 110)
    assert sender.receive(response, b, 200) is None
    assert sender.receive(response, a, 200) == 0.0001
    # The same sequence from a distinct endpoint has an independent replay window.
    assert peer.receive(challenge, b, 120) is not None

def test_stun_ipv4_fixture():
    tx=b't'*12; ip=b'\xcb\x00\x71\x07'; mask=struct.pack('!I',STUN_COOKIE)
    value=b'\x00\x01'+struct.pack('!H',50000^(STUN_COOKIE>>16))+bytes(a^b for a,b in zip(ip,mask))
    attr=struct.pack('!HH',0x0020,len(value))+value
    data=STUN_HEADER.pack(0x0101,len(attr),STUN_COOKIE,tx)+attr
    assert parse_stun_response(data,tx)==('203.0.113.7',50000)

def test_stun_ipv6_and_upnp_xml():
    tx=b'u'*12; raw=bytes.fromhex('20010db8000000000000000000000001'); mask=struct.pack('!I',STUN_COOKIE)+tx
    value=b'\x00\x02'+struct.pack('!H',40000^(STUN_COOKIE>>16))+bytes(a^b for a,b in zip(raw,mask))
    attr=struct.pack('!HH',0x0020,len(value))+value
    data=STUN_HEADER.pack(0x0101,len(attr),STUN_COOKIE,tx)+attr
    assert parse_stun_response(data,tx)==('2001:db8::1',40000)
    xml=b'<root xmlns="urn:x"><device><serviceList><service><serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType><controlURL>/ctl</controlURL></service></serviceList></device></root>'
    assert parse_upnp_description(xml,'http://192.0.2.1/root.xml')[1]=='http://192.0.2.1/ctl'

def test_candidate_gathering_matches_socket_family():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('127.0.0.1', 0))
    try:
        candidates, resources, warnings = gather_candidates(sock, diagnostic=True)
        assert candidates and all(candidate.family == 4 for candidate in candidates)
        assert resources == [] and warnings == []
    finally:
        sock.close()


def test_public_room_candidates_exclude_loopback_and_apipa():
    def resolver(*_args):
        return [
            (socket.AF_INET, socket.SOCK_DGRAM, 0, '', ('127.0.0.1', 47000)),
            (socket.AF_INET, socket.SOCK_DGRAM, 0, '', ('169.254.83.107', 47000)),
            (socket.AF_INET, socket.SOCK_DGRAM, 0, '', ('192.168.1.8', 47000)),
        ]
    assert gather_lan_candidates(47000, diagnostic=False, resolver=resolver) == [
        Candidate(4, TYPE_LAN, '192.168.1.8', 47000)
    ]

if __name__=='__main__': test_windows_udp_reset_is_transient(); test_probe_replay_rtt(); test_probe_replay_and_nonce_are_endpoint_bound(); test_stun_ipv4_fixture(); test_stun_ipv6_and_upnp_xml(); test_candidate_gathering_matches_socket_family(); test_public_room_candidates_exclude_loopback_and_apipa(); print('连接探测测试全部通过')
