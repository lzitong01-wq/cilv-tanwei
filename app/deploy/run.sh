#!/usr/bin/env bash
# 龙蜥（Anolis OS）一键启动：先自检环境，再建 venv 装依赖，最后起服务。
#
# 设计要点：
# - **先自检再启动**：Python 版本、SQLite FTS5、快照、前端产物、索引产物一次查清，
#   免得服务起来才发现检索不能用（check_env.py）。
# - **离线可装**：优先用 deploy/wheels 里与目标平台匹配的 wheel，不联网。
# - 只用纯 Python 依赖，无 C 扩展编译。
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${APP_VENV:-$APP_DIR/.venv}"
PYTHON="${APP_PYTHON:-python3}"

export APP_HOST="${APP_HOST:-0.0.0.0}"
export APP_PORT="${APP_PORT:-8080}"
export APP_THREADS="${APP_THREADS:-16}"
export APP_SNAPSHOT="${APP_SNAPSHOT:-$APP_DIR/../data/知识库审计快照V1.jsonl}"
export APP_DIST="${APP_DIST:-$APP_DIR/frontend/dist}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "未找到 $PYTHON。请先安装 Python 3.11+（dnf install python3.11）。" >&2
  exit 1
fi

# ---- 1. 环境自检（缺项只提示、不阻塞；关键项失败才退出）----
echo "== 环境自检 =="
if ! "$PYTHON" "$APP_DIR/deploy/check_env.py" --snapshot "$APP_SNAPSHOT" --dist "$APP_DIST" --port "$APP_PORT"; then
  echo "自检有未通过项：上面标 FAIL 的会影响对应功能；服务仍可启动（核心解析与解题不受影响）。" >&2
fi

# ---- 2. 虚拟环境与依赖（优先离线 wheels）----
if [ ! -d "$VENV" ]; then
  echo "== 创建虚拟环境：$VENV =="
  "$PYTHON" -m venv "$VENV"
  "$VENV/bin/pip" install --upgrade pip --quiet || true
  WHEELS="$APP_DIR/deploy/wheels/linux-x86_64-py311"
  if [ -d "$WHEELS" ]; then
    echo "离线安装依赖（$WHEELS）"
    "$VENV/bin/pip" install --no-index --find-links "$WHEELS" -r "$APP_DIR/backend/requirements.txt"
    # 可选依赖：系统 SQLite 过旧时补 FTS5 trigram 分词器。
    # 装不上不阻塞——应用层会退回内存子串匹配（功能可用、较慢）。
    if "$VENV/bin/pip" install --no-index --find-links "$WHEELS" \
         -r "$APP_DIR/backend/requirements-optional.txt" 2>/dev/null; then
      echo "可选依赖 pysqlite3 已装（≥3 字检索走 FTS5 trigram）"
    else
      echo "未装可选依赖 pysqlite3：≥3 字检索退回内存子串匹配（功能可用、较慢）"
    fi
  else
    echo "未找到离线 wheels，改为联网安装"
    "$VENV/bin/pip" install -r "$APP_DIR/backend/requirements.txt"
    "$VENV/bin/pip" install -r "$APP_DIR/backend/requirements-optional.txt" || true
  fi
fi

# ---- 3. 起服务 ----
echo "== 启动 =="
echo "地址：http://$APP_HOST:$APP_PORT ｜ 快照 $APP_SNAPSHOT"
exec "$VENV/bin/python" "$APP_DIR/backend/server.py"
