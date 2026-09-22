#!/usr/bin/env python3
"""ESC/POS command builder for 58mm / 80mm thermal receipt printers.

Pure standard library, no third-party runtime dependency.  Text is emitted in
the printer's native code page (GBK by default for Chinese printers) so it
stays crisp and does not depend on any font being installed on the host;
bitmaps are emitted as ``GS v 0`` raster graphics.
"""

from __future__ import annotations

import unicodedata

ESC = b"\x1b"
GS = b"\x1d"
FS = b"\x1c"

ALIGN = {"left": 0, "center": 1, "right": 2}
FONT = {"a": 0, "b": 1}
SIZES = {
    "normal": (1, 1),
    "double": (2, 2),
    "large": (3, 3),
    "double-width": (2, 1),
    "double-height": (1, 2),
}
CUT_MODES = {"none": None, "full": 0, "partial": 1}
ECC_LEVELS = {"L": 48, "M": 49, "Q": 50, "H": 51}
BARCODE_SYMBOLOGIES = {
    "upca": 65,
    "upce": 66,
    "ean13": 67,
    "ean8": 68,
    "code39": 69,
    "itf": 70,
    "codabar": 71,
    "code93": 72,
    "code128": 73,
}
HRI_POSITION = {"none": 0, "above": 1, "below": 2, "both": 3}


def char_width(char: str) -> int:
    """Printer columns occupied by one character (CJK is double width)."""
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def display_width(text: str) -> int:
    return sum(char_width(char) for char in str(text))


def fit(text: str, columns: int) -> str:
    """Truncate `text` so it never exceeds `columns` printer columns."""
    out, used = [], 0
    for char in str(text):
        width = char_width(char)
        if used + width > columns:
            break
        out.append(char)
        used += width
    return "".join(out)


def pad(text, columns: int, align: str = "left", filler: str = " ") -> str:
    """Pad `text` to exactly `columns` printer columns."""
    text = "" if text is None else str(text)
    space = max(0, columns - display_width(text))
    if align == "right":
        return filler * space + text
    if align == "center":
        left = space // 2
        return filler * left + text + filler * (space - left)
    return text + filler * space


class Escpos:
    """Builds an ESC/POS byte stream.  Every method returns ``self``."""

    def __init__(self, width_dots: int = 576, encoding: str = "gbk",
                 columns: int = None, cut: str = "partial", feed: int = 4,
                 chinese: bool = True):
        self.width_dots = int(width_dots)
        self.encoding = encoding or "gbk"
        self.columns = int(columns) if columns else max(1, self.width_dots // 12)
        self.cut_mode = cut
        self.feed_lines = int(feed)
        self.chinese = bool(chinese)
        self._buf = bytearray()

    # ------------------------------------------------------------------ core
    def raw(self, data: bytes):
        self._buf.extend(data)
        return self

    def text(self, value: str):
        """Write text using the configured code page, ``\\n`` becomes LF."""
        return self.raw(str(value).replace("\r\n", "\n").encode(self.encoding, "replace"))

    def bytes(self) -> bytes:
        return bytes(self._buf)

    def clear(self):
        self._buf.clear()
        return self

    def __bytes__(self) -> bytes:
        return self.bytes()

    # ------------------------------------------------------------- commands
    def init(self):
        return self.raw(ESC + b"@")

    def chinese_mode(self, on: bool = True):
        """FS & enables the double byte (GBK) character mode on most printers."""
        return self.raw(FS + (b"&" if on else b"."))

    def codepage(self, page: int):
        return self.raw(ESC + b"t" + bytes([int(page) & 0xFF]))

    def align(self, mode: str = "left"):
        return self.raw(ESC + b"a" + bytes([ALIGN.get(mode, 0)]))

    def font(self, name: str = "a"):
        return self.raw(ESC + b"M" + bytes([FONT.get(name, 0)]))

    def size(self, name: str = "normal"):
        width, height = SIZES.get(name, (1, 1))
        return self.raw(GS + b"!" + bytes([((width - 1) << 4) | (height - 1)]))

    def size_wh(self, width: int = 1, height: int = 1):
        width = min(max(int(width), 1), 8)
        height = min(max(int(height), 1), 8)
        return self.raw(GS + b"!" + bytes([((width - 1) << 4) | (height - 1)]))

    def bold(self, on: bool = True):
        return self.raw(ESC + b"E" + bytes([1 if on else 0]))

    def underline(self, thickness: int = 1):
        return self.raw(ESC + b"-" + bytes([int(thickness) & 0xFF]))

    def invert(self, on: bool = True):
        return self.raw(GS + b"B" + bytes([1 if on else 0]))

    def upside_down(self, on: bool = True):
        return self.raw(ESC + b"{" + bytes([1 if on else 0]))

    def line_spacing(self, dots: int = 30):
        dots = max(0, min(int(dots), 255))
        return self.raw(ESC + b"3" + bytes([dots]))

    def left_margin(self, dots: int = 0):
        dots = max(0, int(dots))
        return self.raw(GS + b"L" + bytes([dots & 0xFF, (dots >> 8) & 0xFF]))

    def feed(self, lines: int = 1):
        return self.raw(ESC + b"d" + bytes([max(0, min(int(lines), 255))]))

    def feed_dots(self, dots: int = 0):
        return self.raw(ESC + b"J" + bytes([max(0, min(int(dots), 255))]))

    def cut(self, mode: str = None):
        """``None`` uses the instance default; ``none`` disables the cut."""
        mode = self.cut_mode if mode is None else mode
        value = CUT_MODES.get(mode, 1)
        if value is None:
            return self
        return self.raw(GS + b"V" + bytes([value]))

    def drawer(self, pin: int = 2, on_ms: int = 50, off_ms: int = 500):
        """Kick the cash drawer on pin 2 (m=0) or pin 5 (m=1)."""
        pulse = 0 if int(pin) == 2 else 1
        return self.raw(ESC + b"p" + bytes([pulse, max(1, on_ms // 2) & 0xFF,
                                           max(1, off_ms // 2) & 0xFF]))

    def beep(self, times: int = 1, duration: int = 3):
        """Non standard buzzer command, supported by many Chinese printers."""
        return self.raw(ESC + b"B" + bytes([max(1, int(times)) & 0xFF,
                                            max(1, min(int(duration), 9))]))

    # ---------------------------------------------------------------- layout
    def line(self, value: str = "", align: str = None, size: str = None,
             bold: bool = None, underline: int = None, invert: bool = None,
             font: str = None, feed: int = 1):
        """Write one formatted line followed by `feed` line feeds."""
        if align:
            self.align(align)
        if font:
            self.font(font)
        if size:
            self.size(size)
        if bold is not None:
            self.bold(bold)
        if underline is not None:
            self.underline(underline)
        if invert is not None:
            self.invert(invert)
        self.text(value)
        self.feed(feed)
        return self

    def divider(self, char: str = "-", align: str = "left"):
        return self.line(char * self.columns, align=align, bold=False,
                         underline=0, invert=False, size="normal")

    def kv(self, key: str, value: str, filler: str = " ", bold: bool = False):
        """`key` left aligned, `value` right aligned, filler in between."""
        key, value = str(key), str(value)
        space = self.columns - display_width(key) - display_width(value)
        if space < 1:
            key = fit(key, max(1, self.columns - display_width(value) - 1))
            space = max(1, self.columns - display_width(key) - display_width(value))
        return self.line(key + filler * space + value, align="left", size="normal",
                         bold=bold, underline=0, invert=False)

    def row(self, cells, widths, aligns=None, bold: bool = False):
        """Format a table row from cells and the column widths (printer columns)."""
        aligns = aligns or ["left"] * len(cells)
        parts = []
        for index, width in enumerate(widths):
            cell = cells[index] if index < len(cells) else ""
            mode = aligns[index] if index < len(aligns) else "left"
            parts.append(pad(fit(cell, width), width, mode))
        return self.line("".join(parts), align="left", size="normal", bold=bold,
                         underline=0, invert=False)

    # ------------------------------------------------------------- graphics
    def qr(self, data: str, module: int = 6, ecc: str = "M", align: str = "center"):
        payload = str(data).encode("utf-8")
        length = len(payload) + 3
        self.align(align)
        self.raw(GS + b"(k" + bytes([4, 0, 49, 65, 50, 0]))
        self.raw(GS + b"(k" + bytes([3, 0, 49, 67, max(1, min(int(module), 16))]))
        self.raw(GS + b"(k" + bytes([3, 0, 49, 69, ECC_LEVELS.get(ecc.upper(), 49)]))
        self.raw(GS + b"(k" + bytes([length & 0xFF, (length >> 8) & 0xFF, 49, 80, 48]))
        self.raw(payload)
        self.raw(GS + b"(k" + bytes([3, 0, 49, 81, 48]))
        return self.feed(1)

    def barcode(self, data: str, symbology: str = "code128", height: int = 80,
                width: int = 2, hri: str = "below", font: str = "a",
                align: str = "center"):
        code = BARCODE_SYMBOLOGIES.get(str(symbology).lower(), 73)
        payload = str(data).encode("ascii", "replace")
        self.align(align)
        self.raw(GS + b"h" + bytes([max(1, min(int(height), 255))]))
        self.raw(GS + b"w" + bytes([max(2, min(int(width), 6))]))
        self.raw(GS + b"H" + bytes([HRI_POSITION.get(hri, 2)]))
        self.raw(GS + b"f" + bytes([FONT.get(font, 0)]))
        self.raw(GS + b"k" + bytes([code, len(payload) & 0xFF]))
        self.raw(payload)
        return self.feed(1)

    def raster(self, packed: bytes, width: int, height: int):
        """``GS v 0`` raster of a 1 bit bitmap, MSB first, bit set = black."""
        if width % 8:
            raise ValueError("raster width must be a multiple of 8")
        bytes_per_line = width // 8
        expected = bytes_per_line * height
        if len(packed) < expected:
            raise ValueError("bitmap is %d bytes, expected %d" % (len(packed), expected))
        for top in range(0, height, 255):
            rows = min(255, height - top)
            offset = top * bytes_per_line
            self.raw(GS + b"v" + b"0" + bytes([0, bytes_per_line & 0xFF,
                                               (bytes_per_line >> 8) & 0xFF,
                                               rows & 0xFF, (rows >> 8) & 0xFF]))
            self.raw(packed[offset:offset + bytes_per_line * rows])
        return self

    def raster_from_image(self, image, mode: str = "threshold", threshold: int = 160,
                          trim: bool = True):
        """Convert a PIL image to a raster block (requires Pillow)."""
        packed, width, height = image_to_bitmap(image, self.width_dots, mode,
                                                threshold, trim)
        return self.raster(packed, width, height)


# ---------------------------------------------------------------- utilities
def _dither(grey, width, height, mode: str, threshold: int):
    """Return a bytearray of rows x width, 0 = no dot, 255 = dot."""
    pixels = bytearray(grey)
    if mode == "threshold":
        return bytearray(255 if value < threshold else 0 for value in pixels)

    if mode == "atkinson":
        div, taps = 8, ((1, 0, 1), (2, 0, 1), (-1, 1, 1), (0, 1, 1), (1, 1, 1), (0, 2, 1))
    else:  # floyd-steinberg
        div, taps = 16, ((1, 0, 7), (-1, 1, 3), (0, 1, 5), (1, 1, 1))

    out = bytearray(width * height)
    errors = [0] * (width + 2)
    for y in range(height):
        line = pixels[y * width:(y + 1) * width]
        nxt = [0] * (width + 2)
        for x in range(width):
            value = line[x] + errors[x + 1]
            value = 0 if value < 0 else (255 if value > 255 else value)
            dot = value < threshold
            out[y * width + x] = 255 if dot else 0
            error = value - (0 if dot else 255)
            for dx, dy, weight in taps:
                target = nxt if dy else errors
                index = x + 1 + dx
                if 0 <= index < width + 2:
                    target[index] += error * weight // div
        errors = nxt
    return out


def _pack(mono, width, height):
    """Pack a 0/255 byte per pixel map into 1 bit per pixel rows."""
    bytes_per_line = width // 8
    packed = bytearray(bytes_per_line * height)
    for y in range(height):
        base = y * width
        row = y * bytes_per_line
        for x in range(width):
            if mono[base + x]:
                packed[row + (x >> 3)] |= 0x80 >> (x & 7)
    return packed


def image_to_bitmap(image, width_dots: int = 576, mode: str = "threshold",
                    threshold: int = 160, trim: bool = True):
    """Scale/dither a PIL image and return ``(packed, width, height)``."""
    width_dots -= width_dots % 8
    grey = image.convert("L")
    if grey.width != width_dots:
        height = max(1, round(grey.height * width_dots / grey.width))
        grey = grey.resize((width_dots, height))
    width, height = grey.size
    mono = _dither(grey.tobytes(), width, height, mode, threshold)
    if trim:
        rows = [y for y in range(height) if any(mono[y * width:(y + 1) * width])]
        if rows and (rows[0] > 0 or rows[-1] < height - 1):
            top, bottom = rows[0], rows[-1] + 1
            mono = mono[top * width:bottom * width]
            height = bottom - top
    return _pack(mono, width, height), width, height


def render_receipt(blocks, width_dots: int = 576, encoding: str = "gbk",
                   columns: int = None, cut: str = "partial", feed: int = 4,
                   chinese: bool = True) -> bytes:
    """Render a list of block dictionaries into an ESC/POS byte stream."""
    printer = Escpos(width_dots, encoding, columns, cut, feed, chinese)
    printer.init()
    if chinese and str(encoding).lower().startswith("gb"):
        printer.chinese_mode(True)

    for block in blocks or []:
        kind = str(block.get("type", "text")).lower()
        if kind == "text":
            printer.line(block.get("text", ""), align=block.get("align", "left"),
                         size=block.get("size", "normal"), bold=block.get("bold", False),
                         underline=block.get("underline", 0),
                         invert=block.get("invert", False), font=block.get("font", "a"))
        elif kind in ("kv", "keyvalue"):
            printer.kv(block.get("key", ""), block.get("value", ""),
                       bold=block.get("bold", False))
        elif kind in ("row", "columns"):
            printer.row(block.get("cells", []), block.get("widths", []),
                        block.get("aligns"), block.get("bold", False))
        elif kind == "divider":
            printer.divider(block.get("char", "-"))
        elif kind == "qr":
            printer.qr(block.get("data", ""), block.get("module", 6),
                       block.get("ecc", "M"), block.get("align", "center"))
        elif kind == "barcode":
            printer.barcode(block.get("data", ""), block.get("symbology", "code128"),
                            block.get("height", 80), block.get("width", 2),
                            block.get("hri", "below"), block.get("font", "a"),
                            block.get("align", "center"))
        elif kind == "image":
            printer.raster(base64_bytes(block.get("bitmap")), int(block.get("width", width_dots)),
                           int(block.get("height", 0)))
            printer.feed(1)
        elif kind == "feed":
            printer.feed(block.get("lines", 1))
        elif kind == "feed_dots":
            printer.feed_dots(block.get("dots", 0))
        elif kind == "cut":
            printer.cut(block.get("mode"))
        elif kind == "drawer":
            printer.drawer(block.get("pin", 2))
        elif kind == "beep":
            printer.beep(block.get("times", 1), block.get("duration", 3))

    if not any(str(b.get("type", "")).lower() == "cut" for b in blocks or []):
        printer.feed(printer.feed_lines)
        printer.cut()
    return printer.bytes()


def base64_bytes(value) -> bytes:
    import base64
    if not value:
        return b""
    if isinstance(value, bytes):
        return value
    return base64.b64decode(str(value))
