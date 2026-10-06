"""qr.py 的单元测试：载荷契约 + 二维码编码器。

这里能测的只有「自洽」：字段顺序、百分号编码规则、错误分类、矩阵结构、容量边界。
**真正的正确性验证在客户端**：`client/app/src/test/java/com/omnipad/client/network/
PairingQrTest.kt` 用 ZXing 把本模块生成的 `pairing-qr-v1.png` 解码回来 ——
编码器写错了，那个用例就红。两侧的分工是：

  本文件  保证「生成的图没有漂移」（fixture 与当前编码器逐字节一致）
  客户端  保证「生成的图能被真实解码器读出来」

fixture 需要重生成时（改了编码器或协议版本）：

    cd server && python test_qr.py --write-fixture

运行：
    cd server && python test_qr.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import qr

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE_PATH = os.path.join(
    REPO_ROOT, "client", "app", "src", "test", "resources", "pairing-qr-v1.png"
)

# fixture 里那张二维码承载的连接参数。客户端测试里有一份相同的期望值，
# 两边对不上就说明 fixture 或解析器漂移了。
FIXTURE_HOST = "192.168.1.5"
FIXTURE_PORT = 5800
FIXTURE_TOKEN = "K7M2P9QR"
FIXTURE_NAME = "DESKTOP-ABC"


def protocol_version():
    """协议版本从 handlers 取（与文档、客户端三处一致的那份常量）。"""
    import handlers
    return handlers.PROTOCOL_VERSION


def fixture_payload():
    return qr.build_payload(
        protocol_version(), FIXTURE_HOST, FIXTURE_PORT, FIXTURE_TOKEN, FIXTURE_NAME
    )


def fixture_png():
    code = qr.encode_text(fixture_payload())
    return qr.png_bytes(code.modules, scale=8)


def decode_png(png):
    """解出 `qr.png_bytes` 那种 PNG 的像素，返回 `(width, height, RGB 字节)`。

    只认我们自己的产出：8 位真彩色、每行 filter 一律 0。

    **为什么要解像素而不是比字节**：`zlib.compress` 的输出跟 zlib 的实现有关 ——
    本机的 Python 3.14 自带 zlib-ng（`1.3.1.zlib-ng`），CI 的 3.11 是标准 zlib
    （`1.2.12`），同一份像素压出来的 IDAT 不一样。这条用例要守的是「提交进仓库的
    那张图就是编码器画的那张」，不是「压缩器实现没换过」。
    这个坑是 CI 抓到的：本地绿、CI 红。
    """
    import struct
    import zlib

    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG")
    offset = 8
    width = height = color_type = None
    idat = bytearray()
    while offset + 8 <= len(png):
        length = struct.unpack(">I", png[offset:offset + 4])[0]
        tag = png[offset + 4:offset + 8]
        body = png[offset + 8:offset + 8 + length]
        if tag == b"IHDR":
            width, height, depth, color_type = struct.unpack(">IIBB", body[:10])
            if depth != 8 or color_type != 2:
                raise ValueError(f"只支持 8 位真彩色，实际 depth={depth} type={color_type}")
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
        offset += 12 + length

    raw = zlib.decompress(bytes(idat))
    row_bytes = width * 3 + 1
    pixels = bytearray()
    for y in range(height):
        start = y * row_bytes
        if raw[start] != 0:
            raise ValueError(f"第 {y} 行的 filter 不是 0")
        pixels += raw[start + 1:start + row_bytes]
    return width, height, bytes(pixels)


# --------------------------------------------------------------------------
# 载荷：构造
# --------------------------------------------------------------------------

class PayloadBuildTest(unittest.TestCase):
    def test_canonical_field_order(self):
        """字段顺序固定，同样的参数必须产出逐字节相同的载荷。"""
        text = qr.build_payload("1.1", "192.168.1.5", 5800, "K7M2P9QR", "DESKTOP-ABC")
        self.assertEqual(
            text,
            "omnipad://pair?v=1.1&host=192.168.1.5&port=5800"
            "&token=K7M2P9QR&name=DESKTOP-ABC",
        )
        self.assertEqual(
            text, qr.build_payload("1.1", "192.168.1.5", 5800, "K7M2P9QR", "DESKTOP-ABC")
        )

    def test_name_is_optional(self):
        text = qr.build_payload("1.1", "10.0.0.1", 5800, "K7M2P9QR")
        self.assertNotIn("name=", text)

    def test_space_becomes_percent20_not_plus(self):
        """空格必须编码成 %20。用 `+` 的话，解码方一还原就会把令牌里的 `+` 改掉。"""
        text = qr.build_payload("1.1", "10.0.0.1", 5800, "K7M2P9QR", "HAICHUAN PC")
        self.assertIn("name=HAICHUAN%20PC", text)
        self.assertNotIn("+", text)

    def test_non_ascii_name_is_utf8_percent_encoded(self):
        text = qr.build_payload("1.1", "10.0.0.1", 5800, "K7M2P9QR", "电脑")
        self.assertIn("name=%E7%94%B5%E8%84%91", text)

    def test_overlong_name_is_truncated_to_the_field_limit(self):
        """name 是显示用的，超长按字符边界截到 64 字节（不劈开字符）。"""
        text = qr.build_payload("1.1", "10.0.0.1", 5800, "K7M2P9QR", "x" * 500)
        self.assertEqual(qr.parse_payload(text)["name"], "x" * qr.MAX_NAME_BYTES)

    def test_name_is_dropped_whole_when_the_uri_would_be_too_long(self):
        """整条 URI 装不下时丢掉 name，而不是把 URI 截断成无效载荷。"""
        text = qr.build_payload("1.1", "a" * 200, 5800, "K7M2P9QR", "x" * 500)
        self.assertNotIn("name=", text)
        self.assertLessEqual(len(text.encode("utf-8")), qr.MAX_PAYLOAD_BYTES)
        self.assertEqual(qr.parse_payload(text)["name"], None)

    def test_name_is_truncated_on_a_character_boundary(self):
        text = qr.build_payload("1.1", "10.0.0.1", 5800, "K7M2P9QR", "电" * 100)
        name = qr.parse_payload(text)["name"]
        self.assertLessEqual(len(name.encode("utf-8")), qr.MAX_NAME_BYTES)
        self.assertTrue(set(name) == {"电"})

    def test_token_is_normalized(self):
        text = qr.build_payload("1.1", "10.0.0.1", 5800, "  k7m2p9qr  ")
        self.assertIn("token=K7M2P9QR", text)

    def test_rejects_bad_host(self):
        for host in ("", "  ", "http://x", "a/b", "x y", "[::1", "中文主机"):
            with self.subTest(host=host):
                with self.assertRaises(qr.PayloadError) as ctx:
                    qr.build_payload("1.1", host, 5800, "K7M2P9QR")
                self.assertEqual(ctx.exception.kind, qr.ERR_BAD_HOST)

    def test_accepts_ipv6_literal(self):
        text = qr.build_payload("1.1", "[fe80::1]", 5800, "K7M2P9QR")
        self.assertIn("host=%5Bfe80%3A%3A1%5D", text)
        self.assertEqual(qr.parse_payload(text)["host"], "[fe80::1]")

    def test_rejects_bad_port(self):
        for port in (0, -1, 65536, "abc", None):
            with self.subTest(port=port):
                with self.assertRaises(qr.PayloadError) as ctx:
                    qr.build_payload("1.1", "10.0.0.1", port, "K7M2P9QR")
                self.assertEqual(ctx.exception.kind, qr.ERR_BAD_PORT)

    def test_rejects_bad_token(self):
        for token in ("", "   ", "A B", "A\tB"):
            with self.subTest(token=token):
                with self.assertRaises(qr.PayloadError) as ctx:
                    qr.build_payload("1.1", "10.0.0.1", 5800, token)
                self.assertEqual(ctx.exception.kind, qr.ERR_BAD_TOKEN)


# --------------------------------------------------------------------------
# 载荷：解析
# --------------------------------------------------------------------------

class PayloadParseTest(unittest.TestCase):
    def test_round_trip(self):
        text = qr.build_payload("1.1", "100.101.102.103", 65535, "abcd2345", "我的电脑")
        parsed = qr.parse_payload(text, expected_version="1.1")
        self.assertEqual(parsed["version"], "1.1")
        self.assertEqual(parsed["host"], "100.101.102.103")
        self.assertEqual(parsed["port"], 65535)
        self.assertEqual(parsed["token"], "ABCD2345")
        self.assertEqual(parsed["name"], "我的电脑")

    def test_scheme_and_action_are_case_insensitive(self):
        parsed = qr.parse_payload("OMNIPAD://PAIR?v=1.1&host=a&port=1&token=B")
        self.assertEqual(parsed["host"], "a")

    def test_unknown_fields_are_ignored(self):
        """新服务端 + 旧 App 仍要能用，所以多出来的字段不报错。"""
        parsed = qr.parse_payload(
            "omnipad://pair?v=1.1&host=a&port=1&token=B&future=1&alt=c"
        )
        self.assertEqual(parsed["host"], "a")
        self.assertNotIn("future", parsed)

    def test_fragment_is_ignored(self):
        parsed = qr.parse_payload("omnipad://pair?v=1.1&host=a&port=1&token=B#x=1")
        self.assertEqual(parsed["token"], "B")

    def test_plus_is_not_a_space(self):
        """本项目刻意偏离 x-www-form-urlencoded：`+` 就是 `+`。"""
        parsed = qr.parse_payload("omnipad://pair?v=1.1&host=a&port=1&token=A%2BB")
        self.assertEqual(parsed["token"], "A+B")

    def test_equals_inside_a_value_survives(self):
        parsed = qr.parse_payload("omnipad://pair?v=1.1&host=a&port=1&token=A=B")
        self.assertEqual(parsed["token"], "A=B")

    def test_duplicate_field_is_rejected(self):
        with self.assertRaises(qr.PayloadError) as ctx:
            qr.parse_payload("omnipad://pair?v=1.1&host=a&port=1&token=B&token=C")
        self.assertEqual(ctx.exception.kind, qr.ERR_DUPLICATE)

    def test_missing_fields_are_rejected_one_by_one(self):
        full = {"v": "1.1", "host": "a", "port": "1", "token": "B"}
        for field in full:
            with self.subTest(field=field):
                query = "&".join(f"{k}={v}" for k, v in full.items() if k != field)
                with self.assertRaises(qr.PayloadError) as ctx:
                    qr.parse_payload(f"omnipad://pair?{query}")
                self.assertEqual(ctx.exception.kind, qr.ERR_MISSING)
                self.assertEqual(ctx.exception.detail, field)

    def test_empty_value_counts_as_missing(self):
        with self.assertRaises(qr.PayloadError) as ctx:
            qr.parse_payload("omnipad://pair?v=1.1&host=&port=1&token=B")
        self.assertEqual(ctx.exception.kind, qr.ERR_MISSING)

    def test_not_omni_pad_links(self):
        for text in ("", "   ", "https://example.com", "hello world",
                     "omnipad://connect?v=1.1", "omnipad://pair"):
            with self.subTest(text=text):
                with self.assertRaises(qr.PayloadError) as ctx:
                    qr.parse_payload(text)
                self.assertIn(ctx.exception.kind, (qr.ERR_NOT_OMNIPAD, qr.ERR_MISSING))

    def test_version_mismatch_is_its_own_kind(self):
        """旧 App 连新服务端时，用户该看到「请更新」，而不是「令牌错了」。"""
        with self.assertRaises(qr.PayloadError) as ctx:
            qr.parse_payload("omnipad://pair?v=1.0&host=a&port=1&token=B",
                             expected_version="1.1")
        self.assertEqual(ctx.exception.kind, qr.ERR_VERSION)

    def test_bad_port_and_host_kinds(self):
        with self.assertRaises(qr.PayloadError) as ctx:
            qr.parse_payload("omnipad://pair?v=1.1&host=a&port=abc&token=B")
        self.assertEqual(ctx.exception.kind, qr.ERR_BAD_PORT)

        with self.assertRaises(qr.PayloadError) as ctx:
            qr.parse_payload("omnipad://pair?v=1.1&host=a%2Fb&port=1&token=B")
        self.assertEqual(ctx.exception.kind, qr.ERR_BAD_HOST)

    def test_overlong_scan_is_rejected(self):
        with self.assertRaises(qr.PayloadError) as ctx:
            qr.parse_payload("omnipad://pair?x=" + "A" * qr.MAX_SCAN_BYTES)
        self.assertEqual(ctx.exception.kind, qr.ERR_TOO_LONG)

    def test_broken_escape_is_not_omni_pad(self):
        with self.assertRaises(qr.PayloadError) as ctx:
            qr.parse_payload("omnipad://pair?v=1.1&host=a&port=1&token=%ZZ")
        self.assertEqual(ctx.exception.kind, qr.ERR_NOT_OMNIPAD)


# --------------------------------------------------------------------------
# 编码器：容量表
# --------------------------------------------------------------------------

class CapacityTest(unittest.TestCase):
    def test_total_codewords_known_versions(self):
        # 26 / 44 / 3706 是 ISO/IEC 18004 表 1 里的公开值
        self.assertEqual(qr.total_codewords(1), 26)
        self.assertEqual(qr.total_codewords(2), 44)
        self.assertEqual(qr.total_codewords(40), 3706)

    def test_data_codewords_known_versions(self):
        expected = {
            (1, "L"): 19, (1, "M"): 16, (1, "Q"): 13, (1, "H"): 9,
            (2, "L"): 34, (2, "M"): 28, (2, "Q"): 22, (2, "H"): 16,
            (40, "L"): 2956, (40, "M"): 2334, (40, "Q"): 1666, (40, "H"): 1276,
        }
        for (version, ecc), count in expected.items():
            with self.subTest(version=version, ecc=ecc):
                self.assertEqual(qr.data_codewords(version, ecc), count)

    def test_byte_capacity_is_monotonic(self):
        for ecc in ("L", "M", "Q", "H"):
            capacities = [qr.byte_mode_capacity(v, ecc) for v in range(1, 41)]
            self.assertEqual(capacities, sorted(capacities))


# --------------------------------------------------------------------------
# 编码器：矩阵
# --------------------------------------------------------------------------

class EncoderTest(unittest.TestCase):
    def test_size_and_version(self):
        code = qr.encode_text("hello")
        self.assertEqual(code.size, code.version * 4 + 17)
        self.assertEqual(len(code.modules), code.size)
        self.assertTrue(all(len(row) == code.size for row in code.modules))

    def test_picks_the_smallest_fitting_version(self):
        for ecc in ("L", "M", "Q", "H"):
            for version in (1, 2, 5, 9, 10, 26, 27):
                capacity = qr.byte_mode_capacity(version, ecc)
                if capacity < 1:
                    continue
                with self.subTest(ecc=ecc, version=version):
                    exact = qr.encode_bytes(b"A" * capacity, ecc=ecc)
                    self.assertEqual(exact.version, version)
                    if capacity + 1 <= qr.byte_mode_capacity(version + 1, ecc):
                        bigger = qr.encode_bytes(b"A" * (capacity + 1), ecc=ecc)
                        self.assertEqual(bigger.version, version + 1)

    def test_too_long_content_is_rejected(self):
        with self.assertRaises(ValueError):
            qr.encode_bytes(b"A" * 3000, ecc="H")

    def test_encoding_is_deterministic(self):
        first = qr.encode_text("omnipad://pair?v=1.1&host=a&port=1&token=B")
        second = qr.encode_text("omnipad://pair?v=1.1&host=a&port=1&token=B")
        self.assertEqual(first.modules, second.modules)
        self.assertEqual(first.mask, second.mask)

    def test_explicit_mask_is_respected(self):
        code = qr.encode_text("masked", mask=3)
        self.assertEqual(code.mask, 3)
        self.assertEqual(self.read_format_bits(code), (qr.ECC_FORMAT_BITS[code.ecc], 3))

    def test_auto_mask_matches_the_format_bits(self):
        code = qr.encode_text("omnipad://pair?v=1.1&host=192.168.1.5&port=5800&token=K")
        ecc_bits, mask = self.read_format_bits(code)
        self.assertEqual(ecc_bits, qr.ECC_FORMAT_BITS[code.ecc])
        self.assertEqual(mask, code.mask)

    def test_finder_patterns_are_intact(self):
        """定时图形画在定位图形之前，顺序反了会啃掉定位图形的边缘。"""
        code = qr.encode_text("finder")
        size = code.size
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if not (0 <= x < size and 0 <= y < size):
                        continue          # 越界部分被 setFunctionModule 裁掉了
                    distance = max(abs(dx), abs(dy))
                    self.assertEqual(
                        code.modules[y][x],
                        distance != 2 and distance != 4,
                        f"定位图形在 ({x},{y}) 不对",
                    )

    def test_timing_pattern_alternates(self):
        code = qr.encode_text("timing")
        for i in range(8, code.size - 8):
            self.assertEqual(code.modules[6][i], i % 2 == 0)
            self.assertEqual(code.modules[i][6], i % 2 == 0)

    def test_dark_module_is_set(self):
        code = qr.encode_text("dark")
        self.assertTrue(code.modules[code.size - 8][8])

    def test_interleave_produces_exactly_the_expected_codewords(self):
        for version, ecc in ((1, "L"), (2, "H"), (7, "M"), (10, "Q"), (40, "H")):
            with self.subTest(version=version, ecc=ecc):
                data = list(range(qr.data_codewords(version, ecc)))
                result = qr._add_ecc_and_interleave(data, version, ecc)
                self.assertEqual(len(result), qr.total_codewords(version))

    def test_every_mask_can_be_used(self):
        for mask in range(8):
            with self.subTest(mask=mask):
                code = qr.encode_text("omnipad://pair?v=1.1", mask=mask)
                self.assertEqual(self.read_format_bits(code)[1], mask)

    def test_rejects_unknown_ecc(self):
        with self.assertRaises(ValueError):
            qr.encode_text("x", ecc="Z")

    @staticmethod
    def read_format_bits(code):
        """从左上角那份格式信息里把 (纠错等级, 掩码) 读回来，顺带校验 BCH 校验位。"""
        bits = 0
        for i in range(6):
            bits |= int(code.modules[i][8]) << i
        bits |= int(code.modules[7][8]) << 6
        bits |= int(code.modules[8][8]) << 7
        bits |= int(code.modules[8][7]) << 8
        for i in range(9, 15):
            bits |= int(code.modules[8][14 - i]) << i

        bits ^= 0x5412
        data = bits >> 10
        remainder = bits & 0x3FF
        check = data
        for _ in range(10):
            check = (check << 1) ^ ((check >> 9) * 0x537)
        if check != remainder:
            raise AssertionError("格式信息的 BCH 校验位不对")
        return data >> 3, data & 0x07


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------

class RenderTest(unittest.TestCase):
    def test_ascii_art_uses_half_blocks(self):
        code = qr.encode_text("hello")
        art = qr.ascii_art(code.modules, border=4)
        lines = art.split("\n")
        self.assertEqual(len(lines), (code.size + 8 + 1) // 2)
        self.assertTrue(all(len(line) == code.size + 8 for line in lines))
        self.assertTrue(set(art) - {"\n"} <= {"█", "▀", "▄", " "})

    def test_png_header_and_size(self):
        code = qr.encode_text("hello")
        png = qr.png_bytes(code.modules, scale=4, border=2)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        import struct
        width, height = struct.unpack(">II", png[16:24])
        expected = (code.size + 4) * 4
        self.assertEqual((width, height), (expected, expected))

    def test_png_is_deterministic(self):
        code = qr.encode_text("hello")
        self.assertEqual(qr.png_bytes(code.modules), qr.png_bytes(code.modules))


class FixtureTest(unittest.TestCase):
    """fixture 是「客户端用 ZXing 解码」那条交叉验证的输入，不能漂移。"""

    def test_fixture_matches_current_encoder(self):
        with open(FIXTURE_PATH, "rb") as f:
            committed = f.read()

        expected_width, expected_height, expected_pixels = decode_png(fixture_png())
        width, height, pixels = decode_png(committed)

        if (width, height, pixels) != (expected_width, expected_height, expected_pixels):
            self.fail(
                "pairing-qr-v1.png 与当前编码器画出来的图不一致"
                f"（提交的是 {width}x{height}，编码器给的是 "
                f"{expected_width}x{expected_height}）。\n"
                "如果这是有意的（改了编码器或协议版本），重新生成：\n"
                "    cd server && python test_qr.py --write-fixture\n"
                "然后跑一遍客户端测试确认 ZXing 仍能解码。"
            )

    def test_fixture_is_a_well_formed_png(self):
        with open(FIXTURE_PATH, "rb") as f:
            committed = f.read()
        self.assertEqual(committed[:8], b"\x89PNG\r\n\x1a\n")
        width, height, pixels = decode_png(committed)
        self.assertEqual(width, height, "二维码图应该是正方形")
        self.assertEqual(len(pixels), width * height * 3)
        # 白底 + 黑码点，两种颜色都不该缺
        self.assertIn(0x00, pixels)
        self.assertIn(0xFF, pixels)

    def test_fixture_payload_parses_back(self):
        parsed = qr.parse_payload(fixture_payload(), expected_version=protocol_version())
        self.assertEqual(parsed["host"], FIXTURE_HOST)
        self.assertEqual(parsed["port"], FIXTURE_PORT)
        self.assertEqual(parsed["token"], FIXTURE_TOKEN)
        self.assertEqual(parsed["name"], FIXTURE_NAME)


def write_fixture():
    os.makedirs(os.path.dirname(FIXTURE_PATH), exist_ok=True)
    with open(FIXTURE_PATH, "wb") as f:
        f.write(fixture_png())
    print(f"已写入 {FIXTURE_PATH}")
    print(f"载荷：{fixture_payload()}")
    return 0

if __name__ == "__main__":
    if "--write-fixture" in sys.argv:
        sys.exit(write_fixture())
    unittest.main(verbosity=2)
