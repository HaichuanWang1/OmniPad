import argparse
import logging
import signal
import sys

import handlers  # noqa: F401  导入即完成全部消息处理器的注册
from tcp_server import TcpServer

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("OmniPad")


def main():
    parser = argparse.ArgumentParser(description="OmniPad Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5800)
    args = parser.parse_args()

    server = TcpServer(host=args.host, port=args.port)

    def shutdown(sig, frame):
        logger.info("shutting down...")
        server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        server.start()
    except OSError as e:
        logger.error(f"failed to start server: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
