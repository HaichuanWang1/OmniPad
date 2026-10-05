"""生成 OmniPad 的图标（纯标准库，无第三方依赖）。

    python scripts/make_icon.py            # 写入 server/assets/omnipad.ico

为什么自己画而不是塞一个二进制进仓库：图标只有几十行代码就能画出来，改配色、
改尺寸都是一行的事，而且评审时看得见它长什么样。生成结果仍然入库 ——
打包不该依赖「先跑一遍这个脚本」。

图标：圆角方块底 + 白色触控板 + 下方一个圆点（Home 键）。
在 16px 下细描边会糊成一团，所以全部用实心形状。
"""
import os
import struct
import sys

# 与 server_ui.py 的 PRIMARY 保持一致
BRAND = (0x0B, 0x5F, 0xD0)
WHITE = (0xFF, 0xFF, 0xFF)

SIZES = (16, 32, 48, 64)
SUPERSAMPLE = 4

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT = os.path.join(REPO_ROOT, "server", "assets", "omnipad.ico")


def in_round_rect(x, y, x0, y0, x1, y1, radius):
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    if x0 + radius <= x <= x1 - radius or y0 + radius <= y <= y1 - radius:
        return True
    cx = min(max(x, x0 + radius), x1 - radius)
    cy = min(max(y, y0 + radius), y1 - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius


def in_circle(x, y, cx, cy, radius):
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius


def sample(x, y, size):
    """返回该点的 RGBA。坐标是 0..size 的浮点数。"""
    unit = size
    # 圆角方块底（留一点点边距，避免在小尺寸下被切掉圆角）
    if not in_round_rect(x, y, 0.02 * unit, 0.02 * unit, 0.98 * unit, 0.98 * unit,
                         0.22 * unit):
        return (0, 0, 0, 0)
    # 触控板
    if in_round_rect(x, y, 0.24 * unit, 0.20 * unit, 0.76 * unit, 0.62 * unit,
                     0.08 * unit):
        return WHITE + (255,)
    # Home 圆点
    if in_circle(x, y, 0.5 * unit, 0.79 * unit, 0.085 * unit):
        return WHITE + (255,)
    return BRAND + (255,)


def render(size):
    """超采样后降采样，返回 size*size 的 RGBA 字节（自上而下）。"""
    n = size * SUPERSAMPLE
    rows = []
    for py in range(n):
        y = (py + 0.5) / SUPERSAMPLE
        row = []
        for px in range(n):
            x = (px + 0.5) / SUPERSAMPLE
            row.append(sample(x, y, size))
        rows.append(row)

    out = bytearray()
    block = SUPERSAMPLE * SUPERSAMPLE
    for oy in range(size):
        for ox in range(size):
            r = g = b = a = 0
            for dy in range(SUPERSAMPLE):
                row = rows[oy * SUPERSAMPLE + dy]
                for dx in range(SUPERSAMPLE):
                    pr, pg, pb, pa = row[ox * SUPERSAMPLE + dx]
                    # 按 alpha 预乘再加权，否则边缘会渗出黑色
                    r += pr * pa
                    g += pg * pa
                    b += pb * pa
                    a += pa
            if a:
                out += bytes((round(r / a), round(g / a), round(b / a),
                              round(a / block)))
            else:
                out += b"\x00\x00\x00\x00"
    return bytes(out)


def bmp_entry(size, rgba):
    """把 RGBA 打成 ICO 里的 BMP 条目（BITMAPINFOHEADER + XOR + AND 掩码）。"""
    # XOR 位图自下而上，BGRA
    xor = bytearray()
    for y in range(size - 1, -1, -1):
        for x in range(size):
            i = (y * size + x) * 4
            r, g, b, a = rgba[i:i + 4]
            xor += bytes((b, g, r, a))

    # AND 掩码：32 位图标其实不看它，但格式要求存在，每行按 4 字节对齐
    row_bytes = ((size + 31) // 32) * 4
    and_mask = bytes(row_bytes * size)

    header = struct.pack(
        "<IiiHHIIiiII",
        40, size, size * 2, 1, 32, 0, len(xor) + len(and_mask), 0, 0, 0, 0,
    )
    return header + bytes(xor) + and_mask


def build_ico(sizes=SIZES):
    images = [bmp_entry(size, render(size)) for size in sizes]

    directory = struct.pack("<HHH", 0, 1, len(images))
    offset = len(directory) + 16 * len(images)
    entries = b""
    for size, image in zip(sizes, images):
        entries += struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,     # 256 用 0 表示
            size if size < 256 else 0,
            0, 0, 1, 32, len(image), offset,
        )
        offset += len(image)
    return directory + entries + b"".join(images)


def main():
    data = build_ico()
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "wb") as f:
        f.write(data)
    print(f"已写入 {OUTPUT}（{len(data):,} 字节，{len(SIZES)} 个尺寸）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
