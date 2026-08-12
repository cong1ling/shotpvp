import time
from connection_code import *

def test_roundtrip_and_rejection():
    now = 1_800_000_000
    record = new_session(KIND_OFFER, [Candidate(4, TYPE_LAN, '192.168.1.2', 47000), Candidate(6, TYPE_IPV6, '2001:db8::1', 47000)], now, b'a'*16, b'b'*32)
    code = encode_code(record)
    assert decode_code(code, now) == record
    try: decode_code(code[:-2] + 'aa', now); assert False
    except ValueError: pass
    try: decode_code(code, now + 11*60); assert False
    except ValueError: pass
    try: encode_code(new_session(KIND_OFFER, [Candidate(4, TYPE_LAN, '10.0.0.1', i+1) for i in range(13)], now)); assert False
    except ValueError: pass
    answer = make_answer(record, [Candidate(4, TYPE_LAN, '192.168.1.3', 47001)], now)
    assert validate_answer(record, decode_code(encode_code(answer), now))
    try: decode_code(code + '=', now); assert False
    except ValueError: pass

if __name__ == '__main__': test_roundtrip_and_rejection(); print('连接码测试全部通过')
