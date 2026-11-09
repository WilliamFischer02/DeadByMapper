"""Pure parsing of Raw Input byte buffers (no Win32 calls - unit-tested on every OS).

Layouts are the x64 ones (the only target): every number below is asserted against the ctypes
structures in ``input/_win32.py`` by tests/test_input_raw_windows.py.

    RAWINPUTHEADER  24  dwType u32 @0, dwSize u32 @4, hDevice HANDLE @8, wParam WPARAM @16
    RAWMOUSE        24  usFlags u16 @0, (pad 2), {ulButtons u32 | usButtonFlags u16 @4, usButtonData u16 @6},
                        ulRawButtons u32 @8, lLastX i32 @12, lLastY i32 @16, ulExtraInformation u32 @20
    RAWKEYBOARD     16  MakeCode u16 @0, Flags u16 @2, Reserved u16 @4, VKey u16 @6, Message u32 @8,
                        ExtraInformation u32 @12
    RAWHID          8+  dwSizeHid u32 @0, dwCount u32 @4, bRawData[dwSizeHid * dwCount] @8
    RAWINPUT        header + data union: mouse message 48 bytes, keyboard message 40 bytes

GetRawInputBuffer quirk (x64): entries in the returned buffer are NOT packed back to back. The next
entry starts at ``align_up(entry + header.dwSize, 8)`` (the NEXTRAWINPUTBLOCK macro aligns to
sizeof(QWORD)). Mouse (48) and keyboard (40) entries are already multiples of 8; HID entries are not.
Under WOW64 (32-bit Python on 64-bit Windows) the buffer uses the 64-bit header, so the data would sit at
+24 even though sizeof(RAWINPUTHEADER) is 16 there - we only support 64-bit Python and refuse to batch
otherwise (raw_input.py).
"""

from __future__ import annotations

import struct

IS_64BIT = struct.calcsize("P") == 8

RIM_TYPEMOUSE = 0
RIM_TYPEKEYBOARD = 1
RIM_TYPEHID = 2

# RAWMOUSE.usFlags
MOUSE_MOVE_ABSOLUTE = 0x01
MOUSE_VIRTUAL_DESKTOP = 0x02

# RAWKEYBOARD.Flags
RI_KEY_BREAK = 0x01
RI_KEY_E0 = 0x02
RI_KEY_E1 = 0x04

VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU = 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5
VK_FAKE = 0xFF  # "no VK" - escaped/fake scan codes (e.g. the E0 2A shift that accompanies extended keys)
SC_LSHIFT, SC_RSHIFT = 0x2A, 0x36

# struct formats ("<" = little endian, no implicit padding - padding is spelled out with "x")
HEADER = struct.Struct("<IIQQ" if IS_64BIT else "<IIII")
HEADER_SIZE = HEADER.size  # 24 on x64
MOUSE = struct.Struct("<H2xHhIiiI")  # usFlags, pad, usButtonFlags, usButtonData(signed wheel), raw, x, y, extra
KEYBOARD = struct.Struct("<HHHHII")  # MakeCode, Flags, Reserved, VKey, Message, ExtraInformation
HID_HEAD = struct.Struct("<II")
MOUSE_MSG_SIZE = HEADER_SIZE + MOUSE.size  # 48
KEYBOARD_MSG_SIZE = HEADER_SIZE + KEYBOARD.size  # 40

# Mouse button transition bits (RAWMOUSE.usButtonFlags) -> (button, down)
BUTTON_BITS: tuple[tuple[int, str, bool], ...] = (
    (0x0001, "left", True),
    (0x0002, "left", False),
    (0x0004, "right", True),
    (0x0008, "right", False),
    (0x0010, "middle", True),
    (0x0020, "middle", False),
    (0x0040, "x1", True),
    (0x0080, "x1", False),
    (0x0100, "x2", True),
    (0x0200, "x2", False),
)
BUTTON_MASK = 0x03FF


def align_up(n: int, a: int = 8) -> int:
    return (n + a - 1) & ~(a - 1)


def next_block(offset: int, dw_size: int) -> int:
    """NEXTRAWINPUTBLOCK on x64: start of the next RAWINPUT in a GetRawInputBuffer buffer."""
    return align_up(offset + dw_size, 8)


def resolve_vk(vk: int, make_code: int, flags: int) -> int | None:
    """Raw Input reports generic VK_SHIFT/VK_CONTROL/VK_MENU; resolve them to the left/right codes.

    Returns ``None`` for messages that must be dropped:
      * VKey 0xFF (fake key) and VKey 0 (unmapped scan code),
      * anything flagged RI_KEY_E1 (the Pause/Break prefix sequence),
      * a VK_SHIFT whose scan code is neither 0x2A (left) nor 0x36 (right).
    Shift is told apart by scan code; Ctrl and Alt by the E0 prefix (right = E0).
    """
    if vk in (0, VK_FAKE) or flags & RI_KEY_E1:
        return None
    if vk == VK_SHIFT:
        if make_code == SC_LSHIFT:
            return VK_LSHIFT
        if make_code == SC_RSHIFT:
            return VK_RSHIFT
        return None
    if vk == VK_CONTROL:
        return VK_RCONTROL if flags & RI_KEY_E0 else VK_LCONTROL
    if vk == VK_MENU:
        return VK_RMENU if flags & RI_KEY_E0 else VK_LMENU
    return vk


def key_down(flags: int) -> bool:
    return not flags & RI_KEY_BREAK


# --------------------------------------------------------------------------- builders (tests + bench)
def pack_header(dw_type: int, dw_size: int, h_device: int = 0x1000, w_param: int = 1) -> bytes:
    return HEADER.pack(dw_type, dw_size, h_device, w_param)


def pack_mouse(
    dx: int = 0,
    dy: int = 0,
    *,
    us_flags: int = 0,
    button_flags: int = 0,
    button_data: int = 0,
    h_device: int = 0x1000,
) -> bytes:
    """A complete mouse RAWINPUT (48 bytes on x64), as GetRawInputData would return it."""
    return pack_header(RIM_TYPEMOUSE, MOUSE_MSG_SIZE, h_device) + MOUSE.pack(
        us_flags, button_flags, button_data, 0, dx, dy, 0
    )


def pack_keyboard(vk: int, make_code: int = 0, flags: int = 0, *, h_device: int = 0x2000) -> bytes:
    """A complete keyboard RAWINPUT (40 bytes on x64)."""
    message = 0x0101 if flags & RI_KEY_BREAK else 0x0100  # WM_KEYUP / WM_KEYDOWN
    return pack_header(RIM_TYPEKEYBOARD, KEYBOARD_MSG_SIZE, h_device) + KEYBOARD.pack(
        make_code, flags, 0, vk, message, 0
    )


def pack_hid(report: bytes, count: int = 1, *, h_device: int = 0x3000) -> bytes:
    size = HEADER_SIZE + HID_HEAD.size + len(report) * count
    return pack_header(RIM_TYPEHID, size, h_device) + HID_HEAD.pack(len(report), count) + report * count


def pack_buffer(messages: list[bytes]) -> bytes:
    """Lay messages out exactly like GetRawInputBuffer does on x64 (8-byte aligned entries)."""
    out = bytearray()
    for m in messages:
        out += m
        out += b"\0" * (align_up(len(out), 8) - len(out))
    return bytes(out)
