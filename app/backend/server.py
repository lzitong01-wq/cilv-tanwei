"""生产入口：waitress 单进程多线程。禁止用 `flask run` 上生产。"""

import sys

from waitress import serve

from app import create_app
from core.config import load_settings


def main() -> int:
    settings = load_settings()
    application = create_app(settings)
    print(
        "词律探微 API 启动 | http://%s:%d | threads=%d | 快照=%s | 前端=%s"
        % (
            settings.host,
            settings.port,
            settings.threads,
            settings.snapshot_path,
            "已构建" if settings.dist_exists else "未构建（仅 API）",
        ),
        flush=True,
    )
    serve(application, host=settings.host, port=settings.port, threads=settings.threads)
    return 0


if __name__ == "__main__":
    sys.exit(main())
