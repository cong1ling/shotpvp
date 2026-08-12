"""Windows 控制台输入：用 Win32 ReadConsoleInput 拿真实的 keydown/keyup 事件。

这绕过了 Python 标准库 msvcrt.kbhit/getch 只能拿"按键字符"的限制，
能区分按下与抬起、能拿到方向键、不会丢事件。

只在 Windows 上跑，其他平台需要另写实现。
"""
import ctypes
from ctypes import wintypes
import sys
from protocol import KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_FIRE

if not sys.platform.startswith('win'):
    raise RuntimeError("input_win.py 仅支持 Windows")

kernel32 = ctypes.windll.kernel32

# ---- 常量 ----
STD_INPUT_HANDLE = -10
ENABLE_PROCESSED_INPUT = 0x0001
ENABLE_ECHO_INPUT = 0x0004
ENABLE_WINDOW_INPUT = 0x0008
ENABLE_MOUSE_INPUT = 0x0010
ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200

# 事件类型
KEY_EVENT = 0x0001

# 控制键状态位
SHIFT_PRESSED = 0x0010

# 虚拟键码
VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
# 字母键（VK 码与 ASCII 大写值相同）
VK_A = 0x41
VK_D = 0x44
VK_S = 0x53
VK_W = 0x57


class MOUSE_BUTTONS(ctypes.Structure):
    _fields_ = [
        ('ButtonState', wintypes.DWORD),
        ('ControlKeyState', wintypes.DWORD),
        ('EventFlags', wintypes.DWORD),
        ('Fill', wintypes.BYTE * 4),
    ]


class COORD(ctypes.Structure):
    _fields_ = [('X', wintypes.SHORT), ('Y', wintypes.SHORT)]


class KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [
        ('bKeyDown', wintypes.BOOL),
        ('wRepeatCount', wintypes.WORD),
        ('wVirtualKeyCode', wintypes.WORD),
        ('wVirtualScanCode', wintypes.WORD),
        ('uChar', wintypes.WCHAR),
        ('dwControlKeyState', wintypes.DWORD),
    ]


class _EVENT_UNION(ctypes.Union):
    _fields_ = [
        ('KeyEvent', KEY_EVENT_RECORD),
        ('Fill', wintypes.BYTE * 16),
    ]


class INPUT_RECORD(ctypes.Structure):
    _fields_ = [
        ('EventType', wintypes.WORD),
        ('Event', _EVENT_UNION),
    ]


class CONSOLE_CURSOR_INFO(ctypes.Structure):
    _fields_ = [
        ('dwSize', wintypes.DWORD),
        ('bVisible', wintypes.BOOL),
    ]


class ConsoleInput:
    """封装控制台为 raw 输入模式，读取按键事件。"""

    def __init__(self):
        self._stdin = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        if not self._stdin or self._stdin == -1:
            raise RuntimeError("GetStdHandle 失败")
        self._saved_mode = wintypes.DWORD(0)
        if not kernel32.GetConsoleMode(self._stdin, ctypes.byref(self._saved_mode)):
            raise RuntimeError("GetConsoleMode 失败")
        # 关闭行模式和回显，关闭鼠标/窗口事件，保留按键
        new_mode = ENABLE_WINDOW_INPUT  # 只要按键事件
        if not kernel32.SetConsoleMode(self._stdin, new_mode):
            raise RuntimeError("SetConsoleMode 失败")
        # 隐藏光标
        so = kernel32.GetStdHandle(-11)
        ci = CONSOLE_CURSOR_INFO(dwSize=1, bVisible=0)
        kernel32.SetConsoleCursorInfo(so, ctypes.byref(ci))

    def close(self):
        kernel32.SetConsoleMode(self._stdin, self._saved_mode)
        so = kernel32.GetStdHandle(-11)
        ci = CONSOLE_CURSOR_INFO(dwSize=1, bVisible=1)
        kernel32.SetConsoleCursorInfo(so, ctypes.byref(ci))

    def read_events(self):
        """读取并消费所有待处理控制台事件。

        返回 list of (vk_code, is_down)，已过滤非按键事件。
        事件计数为 0 时立即返回空列表。
        """
        # 先 PeekAvailable
        available = wintypes.DWORD(0)
        if not kernel32.GetNumberOfConsoleInputEvents(self._stdin, ctypes.byref(available)):
            return []
        if available.value == 0:
            return []

        # 批量读
        n_to_read = min(available.value, 64)
        buf = (INPUT_RECORD * n_to_read)()
        read = wintypes.DWORD(0)
        ok = kernel32.ReadConsoleInputW(
            self._stdin, buf, n_to_read, ctypes.byref(read)
        )
        if not ok or read.value == 0:
            return []

        out = []
        for i in range(read.value):
            rec = buf[i]
            if rec.EventType != KEY_EVENT:
                continue
            ke = rec.Event.KeyEvent
            if ke.uChar in ('\x00', '\r') and ke.wVirtualKeyCode in (0,):
                continue
            out.append((ke.wVirtualKeyCode, bool(ke.bKeyDown)))
        return out

    def flush(self):
        """清空输入缓冲。"""
        kernel32.FlushConsoleInputBuffer(self._stdin)


# ---- 映射到游戏按键 ----
VK_TO_GAME_KEY = {
    VK_UP: KEY_UP, VK_DOWN: KEY_DOWN, VK_LEFT: KEY_LEFT, VK_RIGHT: KEY_RIGHT,
    VK_W: KEY_UP, VK_S: KEY_DOWN, VK_A: KEY_LEFT, VK_D: KEY_RIGHT,
    VK_SPACE: KEY_FIRE, VK_RETURN: KEY_FIRE,
}


def map_events(vk_events):
    """把 Win32 事件流转成游戏按键事件流，返回 list[(game_key, is_down)]。"""
    out = []
    for vk, is_down in vk_events:
        gk = VK_TO_GAME_KEY.get(vk)
        if gk is not None:
            out.append((gk, is_down))
    return out
