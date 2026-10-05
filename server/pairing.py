"""配对令牌：生成、持久化与归一化。

Server 默认监听 0.0.0.0，任何能连上端口的人都能完全控制鼠标键盘。
本模块为握手提供一道配对校验，见 fix.md 第 2 条。

令牌在首次启动时随机生成并写入 pairing_token.txt（不入库），之后每次启动
复用同一个值，因此客户端只需要配对一次。
"""
import os
import secrets

# 剔除易混淆的 I / O / 0 / 1
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
TOKEN_LENGTH = 8

DEFAULT_TOKEN_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "pairing_token.txt"
)


def generate_token() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(TOKEN_LENGTH))


def normalize(token) -> str:
    """统一大小写与首尾空白，让令牌比较对用户友好。"""
    return str(token or "").strip().upper()


def load_or_create_token(path: str = DEFAULT_TOKEN_FILE) -> str:
    """读取已有令牌；文件不存在或内容为空则生成并写回。

    读取失败（文件不存在）属正常首次启动；其它 OSError 直接抛出，
    宁可启动失败也不要静默地跑在没有配对校验的状态下。
    """
    try:
        with open(path, encoding="utf-8") as f:
            existing = normalize(f.read())
        if existing:
            return existing
    except FileNotFoundError:
        pass

    token = generate_token()
    with open(path, "w", encoding="utf-8") as f:
        f.write(token + "\n")
    return token
