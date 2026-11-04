"""运行时配置：只从环境变量读，绝不硬编码路径、密钥或端点。"""

import os
from dataclasses import dataclass
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent.parent
WORKSPACE = APP_ROOT.parent

CONTRACT_VERSION = "v1"
APP_VERSION = "0.1.0"


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    threads: int
    snapshot_path: Path
    quiz_dir: Path | None
    index_path: Path
    style_index_path: Path
    revision_index_path: Path
    tier2_locator_path: Path
    demo_cases_path: Path
    sentiment_benchmark_path: Path
    polyphone_rules_path: Path
    holdout_report_path: Path
    reasoning_eval_path: Path
    rules_path: Path
    dist_dir: Path
    trace_sample_ratio: float

    @property
    def snapshot_exists(self) -> bool:
        return self.snapshot_path.is_file()

    @property
    def dist_exists(self) -> bool:
        return (self.dist_dir / "index.html").is_file()


def load_settings() -> Settings:
    return Settings(
        # 默认监听所有网卡：评审从各自电脑访问，绑 127.0.0.1 会导致外部不可达
        host=os.environ.get("APP_HOST", "0.0.0.0"),
        port=int(os.environ.get("APP_PORT", "8080")),
        threads=int(os.environ.get("APP_THREADS", "16")),
        snapshot_path=_env_path(
            "APP_SNAPSHOT",
            WORKSPACE / "data" / "知识库审计快照V1.jsonl",
        ),
        # 题库不随交付分发；未配置时 /api/demo 明确报错，不猜测 Windows 用户目录
        quiz_dir=(Path(os.environ["APP_QUIZ_DIR"]).expanduser().resolve()
                  if os.environ.get("APP_QUIZ_DIR") else None),
        index_path=_env_path("APP_INDEX", APP_ROOT / "data" / "index.sqlite3"),
        style_index_path=_env_path("APP_STYLE_INDEX", APP_ROOT / "data" / "style.sqlite3"),
        revision_index_path=_env_path("APP_REVISION_INDEX", APP_ROOT / "data" / "revisions.sqlite3"),
        tier2_locator_path=_env_path(
            "APP_TIER2_LOCATOR", APP_ROOT / "data" / "tier2_locator.sqlite3"
        ),
        demo_cases_path=_env_path("APP_DEMO_CASES", APP_ROOT / "data" / "demo_cases.json"),
        sentiment_benchmark_path=_env_path(
            "APP_SENTIMENT_BENCHMARK", APP_ROOT / "data" / "sentiment_benchmark_v1.json"
        ),
        polyphone_rules_path=_env_path(
            "APP_POLYPHONE_RULES", APP_ROOT / "data" / "polyphone_rules.json"
        ),
        holdout_report_path=_env_path(
            "APP_HOLDOUT_REPORT", APP_ROOT / "data" / "holdout_report.json"
        ),
        reasoning_eval_path=_env_path(
            "APP_REASONING_EVAL", APP_ROOT / "data" / "reasoning_eval.json"
        ),
        rules_path=_env_path("APP_RULES_FILE", APP_ROOT / "backend" / "rules.json"),
        dist_dir=_env_path("APP_DIST", APP_ROOT / "frontend" / "dist"),
        trace_sample_ratio=float(os.environ.get("APP_TRACE_SAMPLE", "1.0")),
    )
