"""有界、带校验和且会过期的 P2P 邀请码。"""
from dataclasses import dataclass
import base64, binascii, hmac, ipaddress, os, struct, time, zlib

FORMAT_VERSION = 1
GAME_PROTOCOL = 3
KIND_OFFER = 1
KIND_ANSWER = 2
TYPE_LAN = 1
TYPE_IPV6 = 2
TYPE_STUN = 3
TYPE_UPNP = 4
MAX_CANDIDATES = 12
MAX_CODE = 4096
MAX_RAW = 2048
EXPIRY_MINUTES = 10
HEADER = struct.Struct('!BBBBI16s32sB')


@dataclass(frozen=True)
class Candidate:
    family: int
    kind: int
    ip: str
    port: int

    def __post_init__(self):
        addr = ipaddress.ip_address(self.ip)
        expected = 4 if self.family == 4 else 6 if self.family == 6 else None
        if expected != addr.version or self.kind not in (TYPE_LAN, TYPE_IPV6, TYPE_STUN, TYPE_UPNP):
            raise ValueError('非法候选地址')
        if not 1 <= self.port <= 65535:
            raise ValueError('非法端口')


@dataclass(frozen=True)
class SessionCode:
    kind: int
    created_minute: int
    session_id: bytes
    secret: bytes
    candidates: tuple


def new_session(kind, candidates, now=None, session_id=None, secret=None):
    return SessionCode(kind, int((time.time() if now is None else now) // 60),
                       session_id or os.urandom(16), secret or os.urandom(32), tuple(candidates))


def encode_code(record):
    if record.kind not in (KIND_OFFER, KIND_ANSWER) or len(record.session_id) != 16 or len(record.secret) != 32:
        raise ValueError('非法会话')
    if len(record.candidates) > MAX_CANDIDATES:
        raise ValueError('候选地址过多')
    if not 0 <= record.created_minute <= 0xffffffff:
        raise ValueError('非法创建时间')
    raw = bytearray(HEADER.pack(FORMAT_VERSION, record.kind, GAME_PROTOCOL, 0,
                                record.created_minute, record.session_id, record.secret,
                                len(record.candidates)))
    for candidate in record.candidates:
        packed = ipaddress.ip_address(candidate.ip).packed
        raw += struct.pack('!BBBBH', candidate.family, candidate.kind, len(packed), 0, candidate.port) + packed
    raw += struct.pack('!I', binascii.crc32(raw) & 0xffffffff)
    compressed = zlib.compress(bytes(raw), 9)
    payload = (b'Z' + compressed) if len(compressed) < len(raw) else (b'R' + bytes(raw))
    return base64.urlsafe_b64encode(payload).rstrip(b'=').decode('ascii')


def decode_code(code, now=None):
    if (not isinstance(code, str) or not code or len(code) > MAX_CODE or
            '=' in code or any(ch.isspace() for ch in code)):
        raise ValueError('连接码为空或过长')
    try:
        payload = base64.b64decode(code + '=' * (-len(code) % 4), altchars=b'-_', validate=True)
        if base64.urlsafe_b64encode(payload).rstrip(b'=').decode('ascii') != code:
            raise ValueError('连接码不是规范编码')
        if payload[:1] == b'Z':
            inflater = zlib.decompressobj()
            raw = inflater.decompress(payload[1:], MAX_RAW + 1)
            if inflater.unconsumed_tail or not inflater.eof:
                raise ValueError('连接码解压载荷过大或截断')
            raw += inflater.flush()
        else:
            raw = payload[1:] if payload[:1] == b'R' else None
    except (binascii.Error, zlib.error) as exc:
        raise ValueError('连接码损坏') from exc
    if raw is None or len(raw) > MAX_RAW or len(raw) < HEADER.size + 4:
        raise ValueError('连接码载荷非法')
    body, crc = raw[:-4], struct.unpack('!I', raw[-4:])[0]
    if binascii.crc32(body) & 0xffffffff != crc:
        raise ValueError('连接码校验失败')
    version, kind, game, flags, created, sid, secret, count = HEADER.unpack(body[:HEADER.size])
    if version != FORMAT_VERSION or game != GAME_PROTOCOL or flags or kind not in (KIND_OFFER, KIND_ANSWER) or count > MAX_CANDIDATES:
        raise ValueError('连接码版本或字段不兼容')
    current = int((time.time() if now is None else now) // 60)
    if created > current + 1 or current - created > EXPIRY_MINUTES:
        raise ValueError('连接码已过期')
    off, candidates = HEADER.size, []
    for _ in range(count):
        if off + 6 > len(body): raise ValueError('连接码截断')
        family, ckind, size, reserved, port = struct.unpack('!BBBBH', body[off:off + 6]); off += 6
        if reserved or size not in (4, 16) or off + size > len(body): raise ValueError('候选字段非法')
        ip = str(ipaddress.ip_address(body[off:off + size])); off += size
        candidates.append(Candidate(family, ckind, ip, port))
    if off != len(body): raise ValueError('连接码包含尾随数据')
    return SessionCode(kind, created, sid, secret, tuple(candidates))


def make_answer(offer, candidates, now=None):
    if offer.kind != KIND_OFFER: raise ValueError('应答必须引用主机 offer')
    return new_session(KIND_ANSWER, candidates, now=now, session_id=offer.session_id, secret=offer.secret)


def validate_answer(offer, answer):
    if (offer.kind != KIND_OFFER or answer.kind != KIND_ANSWER or
            not hmac.compare_digest(offer.session_id, answer.session_id) or
            not hmac.compare_digest(offer.secret, answer.secret)):
        raise ValueError('应答与主机连接码不匹配')
    return True
