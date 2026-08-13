"""标准库 P2P 候选发现、认证探测、STUN 与尽力 UPnP。"""
import hashlib, hmac, ipaddress, os, socket, statistics, struct, time, threading
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from connection_code import Candidate, TYPE_LAN, TYPE_IPV6, TYPE_STUN, TYPE_UPNP

PROBE_MAGIC = b'SPVP'
PROBE_VERSION = 1
PROBE_CHALLENGE, PROBE_RESPONSE, PROBE_KEEPALIVE = 1, 2, 3
PROBE_HEAD = struct.Struct('!4sBB16sQIQ')
STUN_COOKIE = 0x2112A442
STUN_HEADER = struct.Struct('!HHI12s')


def recv_udp(sock, size=2048):
    """Return one datagram, or None for transient nonblocking/ICMP errors.

    Windows reports an ICMP Port Unreachable received by an unconnected UDP
    socket as WSAECONNRESET (10054). Candidate probing expects unreachable
    endpoints, so that signal must not abort the remaining candidates.
    """
    try:
        return sock.recvfrom(size)
    except BlockingIOError:
        return None
    except ConnectionResetError as exc:
        if getattr(exc, 'winerror', None) == 10054:
            return None
        raise


def endpoint_key(endpoint):
    """Normalize IPv4/IPv6 recvfrom tuples for comparison and sample ranking."""
    return endpoint[0], endpoint[1]


def encode_probe(kind, session_id, secret, nonce, sequence, timestamp_ns):
    head = PROBE_HEAD.pack(PROBE_MAGIC, PROBE_VERSION, kind, session_id, nonce, sequence, timestamp_ns)
    return head + hmac.new(secret, head, hashlib.sha256).digest()


def decode_probe(data, session_id, secret):
    if len(data) != PROBE_HEAD.size + 32: return None
    head, tag = data[:-32], data[-32:]
    if not hmac.compare_digest(tag, hmac.new(secret, head, hashlib.sha256).digest()): return None
    magic, version, kind, sid, nonce, sequence, stamp = PROBE_HEAD.unpack(head)
    if magic != PROBE_MAGIC or version != PROBE_VERSION or sid != session_id or kind not in (1, 2, 3): return None
    return {'kind': kind, 'nonce': nonce, 'sequence': sequence, 'timestamp_ns': stamp}


class ReplayWindow:
    def __init__(self, width=64): self.width, self.high, self.bits = width, -1, 0
    def accept(self, sequence):
        if sequence > self.high:
            shift = sequence - self.high
            self.bits = 1 if shift >= self.width else ((self.bits << shift) | 1) & ((1 << self.width)-1)
            self.high = sequence; return True
        delta = self.high - sequence
        if delta >= self.width or self.bits & (1 << delta): return False
        self.bits |= 1 << delta; return True


class ProbeSession:
    def __init__(self, session_id, secret):
        if len(session_id) != 16 or len(secret) != 32:
            raise ValueError('非法探测会话凭据')
        self.session_id, self.secret, self.replays, self.pending, self.samples = session_id, secret, {}, {}, {}
    def challenge(self, sequence, now_ns=None, endpoint=None):
        now_ns = time.monotonic_ns() if now_ns is None else now_ns; nonce = int.from_bytes(os.urandom(8), 'big')
        self.pending[nonce] = (now_ns, endpoint_key(endpoint) if endpoint is not None else None)
        return encode_probe(PROBE_CHALLENGE, self.session_id, self.secret, nonce, sequence, now_ns)
    def receive(self, data, endpoint, now_ns=None):
        endpoint = endpoint_key(endpoint)
        msg = decode_probe(data, self.session_id, self.secret)
        if not msg: return None
        replay = self.replays.setdefault(endpoint, ReplayWindow())
        if not replay.accept(msg['sequence']): return None
        if msg['kind'] == PROBE_CHALLENGE:
            return encode_probe(PROBE_RESPONSE, self.session_id, self.secret, msg['nonce'], msg['sequence'] + 1, msg['timestamp_ns'])
        if msg['kind'] == PROBE_RESPONSE and msg['nonce'] in self.pending:
            sent, expected = self.pending[msg['nonce']]
            if expected is not None and endpoint != expected:
                return None
            self.pending.pop(msg['nonce'])
            rtt = (time.monotonic_ns() if now_ns is None else now_ns) - sent
            if rtt < 0: return None
            self.samples.setdefault(endpoint, []).append(rtt / 1_000_000); return rtt / 1_000_000
        return True if msg['kind'] == PROBE_KEEPALIVE else None
    def best_endpoint(self, ipv6_tie_ms=2.0):
        ranked = [(statistics.median(v), ep) for ep, v in self.samples.items() if v]
        if not ranked: return None
        # 回环路径无条件最优先：本机/同机联机场景回环不受防火墙拦截且永远正确；
        # 跨网场景下回环地址探测不到，不会进入 samples，故无副作用。
        loopbacks = [(r, ep) for r, ep in ranked if ep[0] in ('127.0.0.1', '::1')]
        if loopbacks:
            return min(loopbacks)[1]
        ranked.sort(); best_rtt, best = ranked[0]
        ipv6 = [(r, ep) for r, ep in ranked if ':' in ep[0] and r <= best_rtt + ipv6_tie_ms]
        return min(ipv6)[1] if ipv6 else best
    def keepalive(self, sequence):
        return encode_probe(PROBE_KEEPALIVE, self.session_id, self.secret, 0, sequence, time.monotonic_ns())


def probe_candidates(sock, candidates, session_id, secret, timeout=10.0, retries=3):
    """并发轮询全部候选，返回最低中位 RTT 端点；总耗时严格受 timeout 限制。"""
    session = ProbeSession(session_id, secret); deadline = time.monotonic() + timeout
    endpoints = [(c.ip, c.port) for c in candidates
                 if (sock.family == socket.AF_INET and c.family == 4) or
                    (sock.family == socket.AF_INET6 and c.family == 6)]
    if not endpoints:
        return None
    sock.setblocking(False); sequence = 0
    for _ in range(retries):
        for endpoint in endpoints:
            sequence += 2
            try: sock.sendto(session.challenge(sequence, endpoint=endpoint), endpoint)
            except OSError: pass
        round_end = min(deadline, time.monotonic() + timeout / max(retries, 1))
        while time.monotonic() < round_end:
            received = recv_udp(sock)
            if received is None:
                time.sleep(0.005); continue
            data, endpoint = received
            session.receive(data, endpoint)
        if time.monotonic() >= deadline: break
    return session.best_endpoint()


class Keepalive:
    """后台发送认证保活，维持已选择路径的 NAT 映射。"""
    def __init__(self, sock, endpoint, session_id, secret, interval=2.0):
        self.sock, self.endpoint, self.interval = sock, endpoint, interval
        self.session = ProbeSession(session_id, secret); self.running = False; self.thread = None
    def start(self):
        self.running = True
        def loop():
            sequence = 1_000_000
            while self.running:
                try: self.sock.sendto(self.session.keepalive(sequence), self.endpoint)
                except OSError: pass
                sequence += 1; time.sleep(self.interval)
        self.thread = threading.Thread(target=loop, daemon=True); self.thread.start(); return self
    def stop(self):
        self.running = False
        if self.thread: self.thread.join(self.interval + .2)


def gather_lan_candidates(port, diagnostic=False, resolver=socket.getaddrinfo):
    found = set()
    try:
        infos = resolver(socket.gethostname(), port, socket.AF_UNSPEC, socket.SOCK_DGRAM)
    except OSError: infos = []
    for family, _, _, _, address in infos:
        ip = ipaddress.ip_address(address[0])
        if ip.is_loopback and not diagnostic or ip.version == 4 and ip.is_link_local and not diagnostic: continue
        if ip.version == 6 and (ip.is_link_local or ip.is_loopback) and not diagnostic: continue
        kind = TYPE_IPV6 if ip.version == 6 else TYPE_LAN
        found.add(Candidate(ip.version, kind, str(ip), port))
    if diagnostic: found.add(Candidate(4, TYPE_LAN, '127.0.0.1', port))
    return sorted(found, key=lambda c: (c.family, c.ip, c.port))


def make_stun_request(transaction_id=None):
    tx = transaction_id or os.urandom(12)
    return STUN_HEADER.pack(0x0001, 0, STUN_COOKIE, tx), tx


def parse_stun_response(data, transaction_id):
    if len(data) < 20 or len(data) > 2048: raise ValueError('STUN 长度非法')
    mtype, length, cookie, tx = STUN_HEADER.unpack(data[:20])
    if mtype != 0x0101 or cookie != STUN_COOKIE or tx != transaction_id or length != len(data)-20: raise ValueError('STUN 响应非法')
    off, mapped = 20, None
    while off + 4 <= len(data):
        atype, size = struct.unpack('!HH', data[off:off+4])
        padded = ((size + 3) // 4) * 4
        if off + 4 + padded > len(data): raise ValueError('STUN 属性截断')
        value = data[off+4:off+4+size]; off += 4 + padded
        if atype == 0x0020 and size in (8, 20):
            family = value[1]; port = struct.unpack('!H', value[2:4])[0] ^ (STUN_COOKIE >> 16)
            if (family, size) not in ((1, 8), (2, 20)) or port == 0:
                raise ValueError('STUN 映射地址非法')
            mask = struct.pack('!I', STUN_COOKIE) + transaction_id
            raw = bytes(a ^ b for a, b in zip(value[4:], mask))
            mapped = (str(ipaddress.ip_address(raw)), port)
    if off != len(data): raise ValueError('STUN 属性对齐非法')
    if mapped is not None: return mapped
    raise ValueError('STUN 缺少映射地址')


def stun_candidate(sock, endpoint, timeout=0.8):
    request, tx = make_stun_request(); old = sock.gettimeout()
    try:
        sock.settimeout(timeout); sock.sendto(request, endpoint); data, source = sock.recvfrom(2048)
        expected = socket.getaddrinfo(endpoint[0], endpoint[1], sock.family, socket.SOCK_DGRAM)
        allowed = {(entry[4][0], entry[4][1]) for entry in expected}
        if (source[0], source[1]) not in allowed: return None
        ip, port = parse_stun_response(data, tx); return Candidate(ipaddress.ip_address(ip).version, TYPE_STUN, ip, port)
    except (OSError, ValueError): return None
    finally: sock.settimeout(old)


def parse_upnp_description(xml_data, location):
    root = ET.fromstring(xml_data)
    for service in root.iter():
        if service.tag.endswith('service'):
            values = {child.tag.split('}')[-1]: child.text for child in service}
            stype = values.get('serviceType', '')
            if 'WANIPConnection' in stype or 'WANPPPConnection' in stype:
                from urllib.parse import urljoin
                return stype, urljoin(location, values['controlURL'])
    raise ValueError('未发现 UPnP WAN 服务')


class UPnPMapping:
    def __init__(self, service, control_url, external_port, opener=urllib.request.urlopen):
        self.service, self.control_url, self.external_port, self.opener = service, control_url, external_port, opener
    def _soap(self, action, fields, timeout=1.0):
        args = ''.join('<New%s>%s</New%s>' % (k, v, k) for k, v in fields.items())
        body = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><u:%s xmlns:u="%s">%s</u:%s></s:Body></s:Envelope>' % (action, self.service, args, action)).encode()
        req = urllib.request.Request(self.control_url, body, {'SOAPAction': '"%s#%s"' % (self.service, action), 'Content-Type':'text/xml'})
        return self.opener(req, timeout=timeout).read()
    def add(self, internal_ip, internal_port):
        self._soap('AddPortMapping', {'RemoteHost':'','ExternalPort':self.external_port,'Protocol':'UDP','InternalPort':internal_port,'InternalClient':internal_ip,'Enabled':1,'PortMappingDescription':'ShotPVP','LeaseDuration':600})
    def external_ip(self):
        data = self._soap('GetExternalIPAddress', {})
        root = ET.fromstring(data)
        value = next((node.text for node in root.iter() if node.tag.endswith('NewExternalIPAddress')), None)
        if not value: raise ValueError('UPnP 未返回公网地址')
        return value
    def close(self):
        try: self._soap('DeletePortMapping', {'RemoteHost':'','ExternalPort':self.external_port,'Protocol':'UDP'})
        except Exception: pass


def discover_upnp(timeout=1.0, transport=None, fetcher=urllib.request.urlopen):
    """尽力发现 IGD；transport 可在测试中注入，失败返回 None。"""
    request = ('M-SEARCH * HTTP/1.1\r\nHOST:239.255.255.250:1900\r\n'
               'MAN:"ssdp:discover"\r\nMX:1\r\nST:urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n\r\n').encode()
    own = transport is None
    sock = transport or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout); sock.sendto(request, ('239.255.255.250', 1900))
        data, responder = sock.recvfrom(8192)
        headers = {}
        for line in data.decode('iso-8859-1', 'replace').split('\r\n')[1:]:
            if ':' in line:
                key, value = line.split(':', 1); headers[key.lower().strip()] = value.strip()
        location = headers.get('location')
        parsed = urllib.parse.urlparse(location or '')
        if parsed.scheme != 'http' or not parsed.hostname or parsed.username or parsed.password: return None
        try:
            location_ip = ipaddress.ip_address(parsed.hostname)
            responder_ip = ipaddress.ip_address(responder[0])
        except ValueError:
            return None
        if (not location_ip.is_private or not responder_ip.is_private or
                location_ip != responder_ip):
            return None
        xml_data = fetcher(location, timeout=timeout).read()
        service, control = parse_upnp_description(xml_data, location)
        return service, control
    except (OSError, ValueError, ET.ParseError): return None
    finally:
        if own: sock.close()


def create_upnp_mapping(port, timeout=1.0):
    discovered = discover_upnp(timeout=timeout)
    return UPnPMapping(discovered[0], discovered[1], port) if discovered else None


def gather_candidates(sock, stun_endpoints=(), enable_upnp=False, diagnostic=False,
                      stun_func=stun_candidate, upnp_factory=None):
    """收集候选；STUN/UPnP 均是可选失败项，并返回 cleanup 资源与警告。"""
    port = sock.getsockname()[1]
    socket_version = 6 if sock.family == socket.AF_INET6 else 4
    candidates = [candidate for candidate in gather_lan_candidates(port, diagnostic=diagnostic)
                  if candidate.family == socket_version]
    warnings, resources = [], []
    for endpoint in stun_endpoints:
        candidate = stun_func(sock, endpoint)
        if candidate and candidate.family == socket_version:
            candidates.append(candidate); break
    else:
        if stun_endpoints: warnings.append('STUN 不可用，继续尝试局域网/IPv6')
    if enable_upnp:
        try:
            mapping = (upnp_factory or create_upnp_mapping)(port)
            if mapping:
                local_ip = next((c.ip for c in candidates if c.family == 4 and not ipaddress.ip_address(c.ip).is_loopback), '127.0.0.1')
                mapping.add(local_ip, port)
                candidates.append(Candidate(4, TYPE_UPNP, mapping.external_ip(), mapping.external_port))
                resources.append(mapping)
            else: warnings.append('UPnP 网关未发现')
        except Exception: warnings.append('UPnP 映射失败，继续使用其他候选')
    unique = {(c.family, c.kind, c.ip, c.port): c for c in candidates}
    # 方案 A：同机双终端兜底。host 连接码默认不包含回环地址（gather_lan_candidates 排除
    # loopback），而本机双终端场景下 join-code 端通过物理网卡 IP 探测会被 Windows 防火墙
    # 拦截入站 UDP。这里把回环地址作为最低优先候选补上，同机一定能连通；跨网场景下该
    # 候选探测不到，自然被对方忽略，无副作用。
    candidates = list(unique.values())[:12]
    if socket_version == 4 and not any(c.ip in ('127.0.0.1', '::1') for c in candidates):
        candidates.append(Candidate(4, TYPE_LAN, '127.0.0.1', port))
    return candidates, resources, warnings
