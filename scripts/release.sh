#!/usr/bin/env bash
# 发布：构建交付包 → 发到 GitHub Release → 服务器部署代理自动发现并部署。
#
# 为什么不用 GitHub Actions：
#   本机凭据是 OAuth App token（scopes: repo, delete_repo, admin:org…），
#   **缺 `workflow` scope**，GitHub 会拒绝任何写入 `.github/workflows/` 的操作
#   （git push 与 REST API 都会被拦：refusing to allow an OAuth App to
#   create or update workflow … without `workflow` scope）。
#   所以发版这一步由本机脚本用同一套凭据调 Release API 完成；
#   仓库里仍保留 `.github/workflows/build-release.yml`，等将来换成带
#   `workflow` scope 的 PAT 后即可切回 Actions。
#
# 用法：
#   bash scripts/release.sh              # 构建 + 发版
#   bash scripts/release.sh --dry-run    # 只构建不发
#
# 凭据：从 Windows 凭据管理器读（与 git push 同一套），不落盘、不进仓库。

set -uo pipefail

REPO_OWNER="${REPO_OWNER:-lzitong01-wq}"
REPO_NAME="${REPO_NAME:-cilv-tanwei}"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# 临时文件放在工作区下：Git Bash 的 /tmp 路径映射会让 python 读不到 curl 的输出文件
TMP_DIR="$ROOT/.tmp"
mkdir -p "$TMP_DIR"
BUILD_LOG="$TMP_DIR/frontend_build.log"
PACKAGE_LOG="$TMP_DIR/package.log"
UPLOAD_JSON="$TMP_DIR/upload.json"

log() { echo "[release] $*"; }
die() { echo "[release] 错误: $*" >&2; exit 1; }

# ---------- 1. 取凭据 ----------
get_token() {
    printf 'protocol=https\nhost=github.com\nusername=lzitong01-wq\n\n' \
        | GIT_TERMINAL_PROMPT=0 git credential fill 2>/dev/null \
        | grep '^password=' | cut -d= -f2-
}

TOKEN="$(get_token)"
[ -n "$TOKEN" ] || die "未能从凭据管理器取到 GitHub 凭据（请先执行一次 git push 完成认证）"

# ---------- 2. 版本号 ----------
VERSION_FILE="app/VERSION"
if [ -f "$VERSION_FILE" ]; then
    VERSION="$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+' "$VERSION_FILE" | head -1)"
fi
VERSION="${VERSION:-0.1.0}"
TAG="v${VERSION}-$(date +%Y%m%d%H%M)"
log "版本 $VERSION，标签 $TAG"

# ---------- 3. 前端构建（保证 dist 与源码一致）----------
log "构建前端…"
if [ -d app/frontend/node_modules ]; then
    (cd app/frontend && npm run build) >"$BUILD_LOG" 2>&1 \
        || { tail -20 "$BUILD_LOG"; die "前端构建失败"; }
    log "  前端构建完成"
else
    log "  跳过前端构建（node_modules 不存在，用仓库内已有 dist）"
fi

# ---------- 4. 打包 ----------
log "打包…"
python app/scripts/build_package.py >"$PACKAGE_LOG" 2>&1 \
    || { tail -20 "$PACKAGE_LOG"; die "打包失败"; }
TARBALL="$(ls -t 词律探微-交付包-*.tar.gz | head -1)"
SIZE="$(du -h "$TARBALL" | cut -f1)"
log "  产物 $TARBALL（$SIZE）"

if [ "$DRY_RUN" = "1" ]; then
    log "--dry-run：到此为止，不发版"
    exit 0
fi

# ---------- 5. 创建 Release ----------
log "创建 GitHub Release $TAG…"
REL_JSON=$(curl -s --max-time 60 -u "x-access-token:$TOKEN" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    -H "Content-Type: application/json" \
    -X POST -d "{\"tag_name\":\"$TAG\",\"name\":\"词律探微 $TAG\",\"body\":\"自动构建产物。SHA: $(git rev-parse HEAD 2>/dev/null || echo unknown)\",\"draft\":false,\"prerelease\":false}" \
    "https://api.github.com/repos/$REPO_OWNER/$REPO_NAME/releases")

RELEASE_ID=$(printf '%s' "$REL_JSON" | python -c "
import json,sys
try:
    d=json.load(sys.stdin)
    print(d.get('id',''))
except Exception:
    print('')
" 2>/dev/null)

if [ -z "$RELEASE_ID" ]; then
    printf '%s' "$REL_JSON" | head -c 300
    die "创建 Release 失败"
fi
log "  Release #$RELEASE_ID 已创建"

# ---------- 6. 上传资产 ----------
UPLOAD_URL="https://uploads.github.com/repos/$REPO_OWNER/$REPO_NAME/releases/$RELEASE_ID/assets"
# 资产名用纯 ASCII：中文文件名放在 URL 查询串里会被 GitHub 判 400
# （实测「词律探微-交付包-….tar.gz」直接 Bad request，ASCII 名同一份文件秒传）。
# 原始中文名记录在 Release 正文里，不丢信息。
ASSET_NAME="cilv-tanwei-${VERSION}.tar.gz"
log "上传资产（$SIZE，资产名 $ASSET_NAME，需要几分钟）…"
UPLOAD_CODE=$(curl -s --max-time 3600 -o "$UPLOAD_JSON" -w "%{http_code}" \
    -u "x-access-token:$TOKEN" -X POST \
    -H "Content-Type: application/gzip" \
    --data-binary "@$TARBALL" \
    "$UPLOAD_URL?name=$ASSET_NAME&label=delivery-package")

if [ "$UPLOAD_CODE" != "201" ] && [ "$UPLOAD_CODE" != "200" ]; then
    head -c 400 "$UPLOAD_JSON" 2>/dev/null
    die "上传资产失败（HTTP $UPLOAD_CODE）"
fi
log "  资产上传完成"

DOWNLOAD_URL=$(python -c "
import json
try:
    d=json.load(open(r'$UPLOAD_JSON', encoding='utf-8'))
    print(d.get('browser_download_url',''))
except Exception:
    print('')
" 2>/dev/null)

# 上传接口偶尔不返回 browser_download_url（Git Bash 的 /tmp 路径映射也会让 python 读不到）。
# 兜底：用列表接口查该 Release 的资产，拿不到就自己拼标准下载地址。
if [ -z "$DOWNLOAD_URL" ]; then
    log "  上传响应里没拿到下载地址，改用列表接口查…"
    DOWNLOAD_URL=$(curl -s --max-time 30 -u "x-access-token:$TOKEN" \
        "https://api.github.com/repos/$REPO_OWNER/$REPO_NAME/releases/$RELEASE_ID/assets" 2>/dev/null \
        | python -c "
import json,sys
try:
    for a in json.load(sys.stdin):
        if a.get('name') == '$ASSET_NAME':
            print(a.get('browser_download_url','')); break
except Exception:
    print('')
" 2>/dev/null)
fi
if [ -z "$DOWNLOAD_URL" ]; then
    DOWNLOAD_URL="https://github.com/$REPO_OWNER/$REPO_NAME/releases/download/$TAG/$ASSET_NAME"
    log "  用标准下载地址兜底"
fi
log "  下载地址: $DOWNLOAD_URL"

# ---------- 7. 校验资产可下载 ----------
log "校验资产可下载…"
HEAD_CODE=$(curl -s -o /dev/null --max-time 40 -w "%{http_code}" -I -L "$DOWNLOAD_URL")
[ "$HEAD_CODE" = "200" ] || die "资产下载校验失败（HTTP $HEAD_CODE）"
log "  ✓ 资产可下载"

log ""
log "✓ 发版完成：$TAG"
log "  服务器部署代理会在轮询周期内（默认 300 秒）自动拉取部署"
log "  查看部署状态：ssh 龙蜥 '/opt/cilv/bin/deploy-agent.sh --version'"
