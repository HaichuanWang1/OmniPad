"""配对令牌：生成、持久化与归一化。

Server 默认监听 0.0.0.0，任何能连上端口的人都能完全控制鼠标键盘。
本模块为握手提供一道配对校验，见 fix.md 第 2 条。

令牌在首次启动时随机生成并写入数据目录下的 `pairing_token.txt`（不入库），
之后每次启动复用同一个值，因此客户端只需要配对一次。

**位置由 `runtime.resolve_data_dir()` 决定，不再按 `__file__` 走。** 打包成
onefile exe 后 `__file__` 指向启动时解包、退出即删的临时目录 —— 照旧写在那里
的话，每次启动都会重新生成令牌，用户每次都得重新配对。
"""
import os
import secrets

import runtime

# 剔除易混淆的 I / O / 0 / 1
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
TOKEN_LENGTH = 8


def default_token_file() -> str:
    """默认令牌路径。每次调用都重新解析，这样 `--data-dir` 才能生效。"""
    return runtime.token_file_path(runtime.resolve_data_dir())


def generate_token() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(TOKEN_LENGTH))


def normalize(token) -> str:
    """统一大小写与首尾空白，让令牌比较对用户友好。"""
    return str(token or "").strip().upper()


def load_or_create_token(path: str = None) -> str:
    """读取已有令牌；文件不存在或内容为空则生成并写回。

    读取失败（文件不存在）属正常首次启动；其它 OSError 直接抛出，
    宁可启动失败也不要静默地跑在没有配对校验的状态下。
    """
    path = path or default_token_file()
    try:
        with open(path, encoding="utf-8") as f:
            existing = normalize(f.read())
        if existing:
            return existing
    except FileNotFoundError:
        pass

    token = generate_token()
    runtime.ensure_dir(os.path.dirname(os.path.abspath(path)))
    with open(path, "w", encoding="utf-8") as f:
        f.write(token + "\n")
    return token
