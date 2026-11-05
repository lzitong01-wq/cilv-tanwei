"""生产入口：waitress 单进程多线程。禁止用 `flask run` 上生产。"""

import sys

from waitress import serve

from app import create_app
from core import edge as edge_core
from core.config import load_settings


def main() -> int:
    settings = load_settings()
    application = create_app(settings)

    # 校园网边缘入口自举：在 443 上建 HTTPS 入口反代到业务端口。
    # 校园网 ACL 只放行 80/443，服务本身监听 8080，校园网访问不到。
    # 失败不影响主服务（8080 照常服务），原因会写进 /api/health 的 edge 字段。
    edge_state = edge_core.bootstrap(settings.port)
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
    print(
        "边缘入口 | 状态=%s | %s"
        % (edge_state.get("state"), edge_state.get("detail", "")),
        flush=True,
    )
    serve(application, host=settings.host, port=settings.port, threads=settings.threads)
    return 0


if __name__ == "__main__":
    sys.exit(main())
