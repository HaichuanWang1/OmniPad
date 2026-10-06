"""连接二维码：载荷构造/解析 + 纯标准库 QR 编码器。

两件事放在同一个模块里，是因为它们的边界就是「一段文本」：上面一半把连接参数
变成 `omnipad://pair?...`，下面一半把这段文本变成码点矩阵。中间没有别的消费者。

**为什么自己写 QR 编码器**：服务端刻意只用标准库（发布包里不含 `docs/`，也不带
第三方依赖），而 PyPI 上的 `qrcode` / `segno` 都是额外依赖。QR 的字节模式编码是
个封闭的算法 —— 版本容量、GF(256) 上的 Reed-Solomon、掩码评分，全部有确定答案，
而且**可验证**：客户端测试用 ZXing 把服务端生成的图解码回来，编错了就红。
所以这里的正确性不靠「我读懂了规范」，靠那条交叉验证。

算法依据 ISO/IEC 18004，实现结构参考了 Nayuki 的 QR Code generator（MIT）的
分块与掩码评分做法 —— 那套做法在「短块填充字节」这类细节上比裸读规范可靠。

与 `docs/qr-payload.md` 严格对应：那份文档是契约，本模块是它的一份实现。
"""
from __future__ import annotations

import re
import struct
import zlib

# --------------------------------------------------------------------------
# 载荷：常量与错误分类
# --------------------------------------------------------------------------

SCHEME = "omnipad"
ACTION = "pair"

# 整个 URI 的字节上限。正常载荷只有几十字节，300 字节对应 QR version 10（纠错 M），
# 再长码点就密到不好扫了。
MAX_PAYLOAD_BYTES = 300

# name 字段的上限。超长时整个丢掉而不是截断 —— 截断会把 UTF-8 字符劈成半个。
MAX_NAME_BYTES = 64

# 解析时的防御性上限。扫到 2 KB 以上的内容说明对面根本不是 OmniPad。
MAX_SCAN_BYTES = 2048

# 解析失败分类。取值与 docs/qr-payload.md 的表格一一对应，客户端那边有一份同名的。
ERR_NOT_OMNIPAD = "NOT_OMNIPAD_LINK"
ERR_MISSING = "MISSING_FIELD"
ERR_DUPLICATE = "DUPLICATE_FIELD"
ERR_BAD_PORT = "BAD_PORT"
ERR_BAD_HOST = "BAD_HOST"
ERR_BAD_TOKEN = "BAD_TOKEN"
ERR_VERSION = "VERSION_MISMATCH"
ERR_TOO_LONG = "TOO_LONG"

REQUIRED_FIELDS = ("v", "host", "port", "token")

# 允许出现在主机名里的字符。与客户端 EndpointValidator 的 ALLOWED_HOST 保持一致。
_HOST_ALLOWED = re.compile(r"^[A-Za-z0-9.\-_:%\[\]]+$")

_UNRESERVED = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)


class PayloadError(ValueError):
    """载荷不合法。`kind` 是机器可读的分类，`detail` 只用于日志与测试。"""

    def __init__(self, kind, detail=""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail


# --------------------------------------------------------------------------
# 百分号编码
# --------------------------------------------------------------------------

def percent_encode(text) -> str:
    """按 RFC 3986 的 unreserved 集合转义，十六进制用大写。

    刻意**不**采用 `application/x-www-form-urlencoded` 那套（空格 → `+`）：
    解码方一旦把 `+` 还原成空格，令牌里合法的 `+` 就会被静默改写。
    """
    out = []
    for byte in str(text).encode("utf-8"):
        if byte in _UNRESERVED:
            out.append(chr(byte))
        else:
            out.append("%%%02X" % byte)
    return "".join(out)


def percent_decode(text) -> str:
    """percent_encode 的逆运算。非法转义与非法 UTF-8 都当成「这不是我们的链接」。"""
    raw = bytearray()
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "%":
            escape = text[index + 1:index + 3]
            if len(escape) != 2:
                raise PayloadError(ERR_NOT_OMNIPAD, "截断的百分号转义")
            try:
                raw.append(int(escape, 16))
            except ValueError:
                raise PayloadError(ERR_NOT_OMNIPAD, f"非法转义 %{escape}") from None
            index += 3
            continue
        # 字面量非 ASCII 字符也按 UTF-8 收下：生成方本不该这么写，但收下比报错友好
        raw += char.encode("utf-8")
        index += 1
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise PayloadError(ERR_NOT_OMNIPAD, "载荷不是合法 UTF-8") from None


# --------------------------------------------------------------------------
# 载荷：构造与解析
# --------------------------------------------------------------------------

def is_valid_host(host: str) -> bool:
    """主机名/IP 字面量的粗校验，与客户端的手输校验同源。"""
    if not host:
        return False
    if any(char.isspace() for char in host):
        return False
    if "://" in host or "/" in host:
        return False
    if not _HOST_ALLOWED.match(host):
        return False
    # 方括号必须成对，单独一个 '[' 是打错了
    return host.startswith("[") == host.endswith("]")


def _truncate_utf8(text: str, limit: int) -> str:
    """按 UTF-8 字节数截断，且不劈开字符。"""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", errors="ignore")


def build_payload(version, host, port, token, name=None) -> str:
    """连接参数 → 二维码文本。

    字段顺序固定为 v / host / port / token / name，同样的输入必须产出逐字节
    相同的载荷（测试靠这条比对，界面重绘也不会让二维码跳变）。
    """
    host = (host or "").strip()
    if not is_valid_host(host):
        raise PayloadError(ERR_BAD_HOST, host)

    try:
        port = int(port)
    except (TypeError, ValueError):
        raise PayloadError(ERR_BAD_PORT, repr(port)) from None
    if not 1 <= port <= 65535:
        raise PayloadError(ERR_BAD_PORT, str(port))

    token = (token or "").strip().upper()
    if not token or any(char.isspace() for char in token):
        raise PayloadError(ERR_BAD_TOKEN, token)

    text = "{}://{}?{}".format(
        SCHEME,
        ACTION,
        "&".join((
            "v=" + percent_encode(version),
            "host=" + percent_encode(host),
            "port=" + str(port),
            "token=" + percent_encode(token),
        )),
    )

    if name:
        candidate = text + "&name=" + percent_encode(
            _truncate_utf8(str(name).strip(), MAX_NAME_BYTES)
        )
        # 装不下就整条丢掉 name，而不是截断 URI —— 截断后的载荷是无效的
        if len(candidate.encode("utf-8")) <= MAX_PAYLOAD_BYTES:
            text = candidate

    if len(text.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise PayloadError(ERR_TOO_LONG, f"{len(text.encode('utf-8'))} 字节")
    return text


def parse_payload(text, expected_version=None) -> dict:
    """二维码文本 → 连接参数。

    返回 `{"version", "host", "port", "token", "name"}`；`name` 可能为 None。
    未知字段被忽略（新服务端 + 旧 App 仍能用），但**重复字段报错**：
    同一个键出现两次是歧义，不猜。
    """
    text = (text or "").strip()
    if len(text.encode("utf-8", "replace")) > MAX_SCAN_BYTES:
        raise PayloadError(ERR_TOO_LONG, f"{len(text.encode('utf-8'))} 字节")

    scheme, separator, rest = text.partition("://")
    if not separator or scheme.strip().lower() != SCHEME:
        raise PayloadError(ERR_NOT_OMNIPAD, text[:40])

    action, _, query = rest.partition("?")
    if action.strip().strip("/").lower() != ACTION:
        raise PayloadError(ERR_NOT_OMNIPAD, action[:40])

    # '#' 之后是片段，按 RFC 3986 由客户端处理，这里直接丢掉
    query = query.split("#", 1)[0]

    params = {}
    for chunk in query.split("&"):
        if not chunk:
            continue
        key, _, value = chunk.partition("=")
        if key in params:
            raise PayloadError(ERR_DUPLICATE, key)
        params[key] = percent_decode(value)

    for field in REQUIRED_FIELDS:
        if field not in params:
            raise PayloadError(ERR_MISSING, field)
        if not params[field]:
            raise PayloadError(ERR_MISSING, f"{field} 为空")

    version = params["v"]
    if expected_version is not None and version != expected_version:
        raise PayloadError(ERR_VERSION, f"载荷 {version} != 本机 {expected_version}")

    host = params["host"]
    if not is_valid_host(host):
        raise PayloadError(ERR_BAD_HOST, host)

    try:
        port = int(params["port"])
    except ValueError:
        raise PayloadError(ERR_BAD_PORT, params["port"]) from None
    if not 1 <= port <= 65535:
        raise PayloadError(ERR_BAD_PORT, params["port"])

    token = params["token"]
    if any(char.isspace() for char in token):
        raise PayloadError(ERR_BAD_TOKEN, token)

    name = params.get("name") or None
    return {
        "version": version,
        "host": host,
        "port": port,
        "token": token.upper(),
        "name": name,
    }


# --------------------------------------------------------------------------
# QR 编码器：容量表
# --------------------------------------------------------------------------

# 纠错等级 → 格式信息里的两位取值（L=01 M=00 Q=11 H=10）
ECC_FORMAT_BITS = {"L": 1, "M": 0, "Q": 3, "H": 2}

# 表里的行序固定为 L / M / Q / H，索引 0 是占位（QR 没有 version 0）。
ECC_ORDER = ("L", "M", "Q", "H")

# 每块纠错码字数（ISO/IEC 18004 表 13-22）
ECC_CODEWORDS_PER_BLOCK = (
    # 0   1   2   3   4   5   6   7   8   9  10  11  12  13  14  15  16  17  18  19  20  21  22  23  24  25  26  27  28  29  30  31  32  33  34  35  36  37  38  39  40
    (-1,  7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),  # L
    (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28),  # M
    (-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),  # Q
    (-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),  # H
)

# 分块数（同一张表）
NUM_ERROR_CORRECTION_BLOCKS = (
    (-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25),  # L
    (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49),  # M
    (-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68),  # Q
    (-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81),  # H
)

PENALTY_N1 = 3
PENALTY_N2 = 3
PENALTY_N3 = 40
PENALTY_N4 = 10


def _ecc_row(ecc: str):
    index = ECC_ORDER.index(ecc.upper())
    return index


def num_raw_data_modules(version: int) -> int:
    """该版本除掉功能图形后，能放数据的模块总数（ISO/IEC 18004 的公式）。"""
    result = (16 * version + 128) * version + 64
    if version >= 2:
        num_align = version // 7 + 2
        result -= (25 * num_align - 10) * num_align - 55
        if version >= 7:
            result -= 36
    return result


def total_codewords(version: int) -> int:
    return num_raw_data_modules(version) // 8


def data_codewords(version: int, ecc: str) -> int:
    row = _ecc_row(ecc)
    return (
        total_codewords(version)
        - ECC_CODEWORDS_PER_BLOCK[row][version] * NUM_ERROR_CORRECTION_BLOCKS[row][version]
    )


def byte_mode_capacity(version: int, ecc: str) -> int:
    """该版本/纠错等级下，字节模式能装多少个字节。"""
    bits = data_codewords(version, ecc) * 8
    overhead = 4 + (8 if version <= 9 else 16)
    return max(0, (bits - overhead) // 8)


# --------------------------------------------------------------------------
# QR 编码器：GF(256) 与 Reed-Solomon
# --------------------------------------------------------------------------

def _gf_multiply(x: int, y: int) -> int:
    """GF(256) 乘法，本原多项式 0x11D（x^8+x^4+x^3+x^2+1）。"""
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int):
    """生成多项式 (x-a^0)(x-a^1)...(x-a^(degree-1)) 的系数。"""
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_multiply(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_multiply(root, 0x02)
    return result


def _rs_remainder(data, divisor):
    """多项式除法取余，就是这一块的纠错码字。"""
    result = [0] * len(divisor)
    for byte in data:
        factor = byte ^ result[0]
        result = result[1:] + [0]
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_multiply(coef, factor)
    return result


# --------------------------------------------------------------------------
# QR 编码器：矩阵
# --------------------------------------------------------------------------

class QRCode:
    """编码结果。`modules[y][x]` 为 True 表示该点是深色。"""

    __slots__ = ("version", "ecc", "mask", "size", "modules")

    def __init__(self, version, ecc, mask, modules):
        self.version = version
        self.ecc = ecc
        self.mask = mask
        self.size = len(modules)
        self.modules = modules

    def __repr__(self):
        return f"<QRCode v{self.version} {self.ecc} mask={self.mask} {self.size}x{self.size}>"


class _Builder:
    def __init__(self, version, ecc):
        self.version = version
        self.ecc = ecc
        self.size = version * 4 + 17
        self.modules = [[False] * self.size for _ in range(self.size)]
        self.is_function = [[False] * self.size for _ in range(self.size)]

    # ---- 基本操作 ----

    def _set_function(self, x, y, dark):
        if 0 <= x < self.size and 0 <= y < self.size:
            self.modules[y][x] = bool(dark)
            self.is_function[y][x] = True

    # ---- 功能图形 ----

    def draw_function_patterns(self):
        size = self.size

        # 定时图形先画：第 6 行与第 6 列的黑白交替。它会被随后画上去的定位图形
        # 覆盖掉交叠部分 —— 顺序反了会把定位图形的边缘啃掉，码就废了。
        for i in range(size):
            self._set_function(6, i, i % 2 == 0)
            self._set_function(i, 6, i % 2 == 0)

        # 定位图形（三个角）与分隔带
        self._draw_finder(3, 3)
        self._draw_finder(size - 4, 3)
        self._draw_finder(3, size - 4)

        # 校正图形
        positions = self.alignment_positions()
        count = len(positions)
        for i in range(count):
            for j in range(count):
                # 三个角上已经有定位图形，跳过
                if (
                    (i == 0 and j == 0)
                    or (i == 0 and j == count - 1)
                    or (i == count - 1 and j == 0)
                ):
                    continue
                self._draw_alignment(positions[i], positions[j])

        # 格式信息先占位（真实比特在掩码确定后写入），版本信息此时就能定下来
        self.draw_format_bits(0)
        self.draw_version_bits()

    def _draw_finder(self, x, y):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                distance = max(abs(dx), abs(dy))
                # distance==2 是白色内圈，==4 是白色分隔带，其余为深色
                self._set_function(x + dx, y + dy, distance != 2 and distance != 4)

    def _draw_alignment(self, x, y):
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                self._set_function(x + dx, y + dy, max(abs(dx), abs(dy)) != 1)

    def alignment_positions(self):
        if self.version == 1:
            return []
        num_align = self.version // 7 + 2
        step = 26 if self.version == 32 else (
            (self.version * 4 + num_align * 2 + 1) // (num_align * 2 - 2) * 2
        )
        result = [6] * num_align
        pos = self.size - 7
        for i in range(num_align - 1, 0, -1):
            result[i] = pos
            pos -= step
        return result

    def draw_format_bits(self, mask):
        """格式信息写两遍：左上角一份，右上/左下各半份。"""
        size = self.size
        data = ECC_FORMAT_BITS[self.ecc] << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412

        def bit(index):
            return (bits >> index) & 1

        for i in range(6):
            self._set_function(8, i, bit(i))
        self._set_function(8, 7, bit(6))
        self._set_function(8, 8, bit(7))
        self._set_function(7, 8, bit(8))
        for i in range(9, 15):
            self._set_function(14 - i, 8, bit(i))

        for i in range(8):
            self._set_function(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self._set_function(8, size - 15 + i, bit(i))
        self._set_function(8, size - 8, True)      # 恒为深色的固定模块

    def draw_version_bits(self):
        if self.version < 7:
            return
        rem = self.version
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = self.version << 12 | rem
        for i in range(18):
            bit = (bits >> i) & 1
            a = self.size - 11 + i % 3
            b = i // 3
            self._set_function(a, b, bit)
            self._set_function(b, a, bit)

    # ---- 数据 ----

    def draw_codewords(self, codewords):
        """从右下角起，两列一组蛇形填充。功能模块位置全部跳过。"""
        size = self.size
        total_bits = len(codewords) * 8
        index = 0
        right = size - 1
        while right >= 1:
            if right == 6:
                # 第 6 列是定时图形，整体左移一格（列对仍然是 (5,4)）
                right = 5
            for vert in range(size):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = (size - 1 - vert) if upward else vert
                    if not self.is_function[y][x] and index < total_bits:
                        self.modules[y][x] = (codewords[index >> 3] >> (7 - (index & 7))) & 1 == 1
                        index += 1
            right -= 2
        # 剩余的 0~7 个「剩余位」本来就该是浅色，不填即可
        if index != total_bits:
            raise AssertionError(f"码字没有全部写入：{index}/{total_bits}")

    def apply_mask(self, mask):
        for y in range(self.size):
            for x in range(self.size):
                if self.is_function[y][x]:
                    continue
                if mask == 0:
                    invert = (x + y) % 2 == 0
                elif mask == 1:
                    invert = y % 2 == 0
                elif mask == 2:
                    invert = x % 3 == 0
                elif mask == 3:
                    invert = (x + y) % 3 == 0
                elif mask == 4:
                    invert = (x // 3 + y // 2) % 2 == 0
                elif mask == 5:
                    invert = x * y % 2 + x * y % 3 == 0
                elif mask == 6:
                    invert = (x * y % 2 + x * y % 3) % 2 == 0
                elif mask == 7:
                    invert = ((x + y) % 2 + x * y % 3) % 2 == 0
                else:
                    raise ValueError(f"非法掩码 {mask}")
                if invert:
                    self.modules[y][x] = not self.modules[y][x]

    # ---- 掩码评分 ----

    def penalty_score(self):
        """ISO/IEC 18004 的四条罚分规则，分数越低越好。"""
        size = self.size
        result = 0

        # 规则 1：同色连续 5 个以上；规则 3：出现类似定位图形的 1:1:3:1:1
        for y in range(size):
            run_color = False
            run_length = 0
            history = [0] * 7
            for x in range(size):
                if self.modules[y][x] == run_color:
                    run_length += 1
                    if run_length == 5:
                        result += PENALTY_N1
                    elif run_length > 5:
                        result += 1
                else:
                    self._add_history(run_length, history)
                    if not run_color:
                        result += self._count_patterns(history) * PENALTY_N3
                    run_color = self.modules[y][x]
                    run_length = 1
            result += self._terminate_and_count(run_color, run_length, history) * PENALTY_N3

        for x in range(size):
            run_color = False
            run_length = 0
            history = [0] * 7
            for y in range(size):
                if self.modules[y][x] == run_color:
                    run_length += 1
                    if run_length == 5:
                        result += PENALTY_N1
                    elif run_length > 5:
                        result += 1
                else:
                    self._add_history(run_length, history)
                    if not run_color:
                        result += self._count_patterns(history) * PENALTY_N3
                    run_color = self.modules[y][x]
                    run_length = 1
            result += self._terminate_and_count(run_color, run_length, history) * PENALTY_N3

        # 规则 2：2x2 同色块
        for y in range(size - 1):
            for x in range(size - 1):
                color = self.modules[y][x]
                if (
                    color == self.modules[y][x + 1]
                    and color == self.modules[y + 1][x]
                    and color == self.modules[y + 1][x + 1]
                ):
                    result += PENALTY_N2

        # 规则 4：深色模块占比偏离 50% 的程度，每 5% 罚 10 分
        dark = sum(1 for row in self.modules for cell in row if cell)
        total = size * size
        steps = (abs(dark * 20 - total * 10) + total - 1) // total - 1
        result += steps * PENALTY_N4
        return result

    def _add_history(self, run_length, history):
        # 首段（还没出现过异色）要把码外的浅色边也算进去
        if history[0] == 0:
            run_length += self.size
        history[1:] = history[:-1]
        history[0] = run_length

    def _count_patterns(self, history):
        n = history[1]
        core = (
            n > 0
            and history[2] == n
            and history[3] == n * 3
            and history[4] == n
            and history[5] == n
        )
        return (
            (1 if core and history[0] >= n * 4 and history[6] >= n else 0)
            + (1 if core and history[6] >= n * 4 and history[0] >= n else 0)
        )

    def _terminate_and_count(self, run_color, run_length, history):
        if run_color:                     # 收尾的深色段先落进历史
            self._add_history(run_length, history)
            run_length = 0
        run_length += self.size           # 末尾的浅色边
        self._add_history(run_length, history)
        return self._count_patterns(history)


def _add_ecc_and_interleave(data, version, ecc):
    """数据码字 → 分块加纠错 → 交错成一串（ISO/IEC 18004 的步骤）。"""
    row = _ecc_row(ecc)
    num_blocks = NUM_ERROR_CORRECTION_BLOCKS[row][version]
    block_ecc_len = ECC_CODEWORDS_PER_BLOCK[row][version]
    raw_codewords = total_codewords(version)

    num_short_blocks = num_blocks - raw_codewords % num_blocks
    short_block_len = raw_codewords // num_blocks

    divisor = _rs_divisor(block_ecc_len)
    blocks = []
    k = 0
    for i in range(num_blocks):
        length = short_block_len - block_ecc_len + (0 if i < num_short_blocks else 1)
        dat = list(data[k:k + length])
        k += length
        # 短块补一个 0 占位，交错时跳过它，保证长块与短块的纠错码字对齐
        block = dat + [0] * (short_block_len + 1 - len(dat))
        ecc_bytes = _rs_remainder(dat, divisor)
        block[len(block) - block_ecc_len:] = ecc_bytes
        blocks.append(block)

    result = []
    for i in range(len(blocks[0])):
        for j, block in enumerate(blocks):
            if i != short_block_len - block_ecc_len or j >= num_short_blocks:
                result.append(block[i])
    if len(result) != raw_codewords:
        raise AssertionError(f"交错后码字数不对：{len(result)} != {raw_codewords}")
    return result


def encode_bytes(data: bytes, ecc="M", min_version=1, mask=None) -> QRCode:
    """字节模式编码。版本按内容自动选最小的，掩码默认按罚分挑最好的。"""
    ecc = ecc.upper()
    if ecc not in ECC_FORMAT_BITS:
        raise ValueError(f"未知纠错等级 {ecc}")
    data = bytes(data)

    version = None
    for candidate in range(max(1, int(min_version)), 41):
        bits_needed = 4 + (8 if candidate <= 9 else 16) + 8 * len(data)
        if bits_needed <= data_codewords(candidate, ecc) * 8:
            version = candidate
            break
    if version is None:
        raise ValueError(f"内容太长，QR 最高版本也装不下（{len(data)} 字节）")

    # ---- 数据码字 ----
    capacity = data_codewords(version, ecc)
    bits = []

    def append(value, length):
        for i in range(length - 1, -1, -1):
            bits.append((value >> i) & 1)

    append(0b0100, 4)                                  # 字节模式
    append(len(data), 8 if version <= 9 else 16)       # 字符计数
    for byte in data:
        append(byte, 8)
    append(0, min(4, capacity * 8 - len(bits)))        # 终止符
    append(0, (-len(bits)) % 8)                        # 补到字节边界

    pad = (0xEC, 0x11)
    index = 0
    while len(bits) < capacity * 8:
        append(pad[index % 2], 8)
        index += 1

    codewords = [
        int("".join(str(b) for b in bits[i:i + 8]), 2)
        for i in range(0, len(bits), 8)
    ]

    all_codewords = _add_ecc_and_interleave(codewords, version, ecc)

    # ---- 矩阵 ----
    builder = _Builder(version, ecc)
    builder.draw_function_patterns()
    builder.draw_codewords(all_codewords)

    if mask is None:
        best_mask = 0
        best_penalty = None
        for candidate in range(8):
            builder.apply_mask(candidate)
            builder.draw_format_bits(candidate)
            penalty = builder.penalty_score()
            if best_penalty is None or penalty < best_penalty:
                best_mask, best_penalty = candidate, penalty
            builder.apply_mask(candidate)              # 撤销，XOR 自逆
        mask = best_mask

    builder.apply_mask(mask)
    builder.draw_format_bits(mask)
    return QRCode(version, ecc, mask, builder.modules)


def encode_text(text, ecc="M", min_version=1, mask=None) -> QRCode:
    return encode_bytes(str(text).encode("utf-8"), ecc=ecc,
                        min_version=min_version, mask=mask)


# --------------------------------------------------------------------------
# 输出：PNG / 终端
# --------------------------------------------------------------------------

def png_bytes(matrix, scale=8, border=4, dark=(0, 0, 0), light=(255, 255, 255)) -> bytes:
    """把矩阵画成 PNG（纯标准库，真彩色 8 位）。

    浅色底 + 深色码点是扫描器的要求，所以图形界面那块深色主题里的二维码必须
    自带白底 —— 深底浅码有些摄像头认不出来。
    """
    size = len(matrix)
    dim = (size + border * 2) * scale
    raw = bytearray()
    dark_bytes = bytes(dark)
    light_bytes = bytes(light)
    for py in range(dim):
        raw.append(0)                                  # 每行的 filter type
        my = py // scale - border
        row = matrix[my] if 0 <= my < size else None
        for px in range(dim):
            mx = px // scale - border
            if row is not None and 0 <= mx < size and row[mx]:
                raw += dark_bytes
            else:
                raw += light_bytes

    def chunk(tag, payload):
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", dim, dim, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def ascii_art(matrix, border=4, quiet=" ") -> str:
    """终端里的二维码，用半块字符两行并一行。

    一格字符在终端里大致是 1:2（宽:高），所以「一个字符盖两个模块」正好把
    宽高比掰回方的 —— 直接一个模块一个字符会被拉成竖长条，摄像头很难认。
    """
    size = len(matrix)
    dim = size + border * 2

    def is_dark(x, y):
        mx, my = x - border, y - border
        return 0 <= mx < size and 0 <= my < size and matrix[my][mx]

    lines = []
    for y in range(0, dim, 2):
        row = []
        for x in range(dim):
            top = is_dark(x, y)
            bottom = is_dark(x, y + 1) if y + 1 < dim else False
            if top and bottom:
                row.append("█")
            elif top:
                row.append("▀")
            elif bottom:
                row.append("▄")
            else:
                row.append(quiet)
        lines.append("".join(row))
    return "\n".join(lines)
