# 龙蜥（Anolis OS）部署与迁移核查

本目录只放部署相关产物。**代码里没有任何 Windows 专有假设**，但迁移仍需逐项核对下面 10 条。

---

## 0. 部署前先自检（一条命令）

```bash
python3 app/deploy/check_env.py            # 检查 Python/SQLite/FTS5/快照/前端产物/索引产物/端口
```

12 项检查，逐项给 PASS/FAIL 与处理办法。**建议先跑这个再起服务**：
检索依赖 SQLite 的 FTS5 扩展，缺了会在服务起来之后才暴露。

| 检查 | 要求 | 不满足时 |
|---|---|---|
| Python | ≥ 3.11 | `dnf install -y python3.11 python3.11-pip` |
| SQLite | ≥ 3.34 且带 FTS5 | 换发行版或自编 sqlite3 |
| Tier 1 快照 | 可读、423 篇 | 从赛方数据导出 |
| 前端产物 | `dist/index.html` + `dist/fonts/*.woff2` | `cd app/frontend && npm ci && npm run build` |
| 构建期索引 | 检索/风格/校订/词谱/古音/Tier 2 | 报告里给了逐条补跑命令 |

## 1. 依赖与离线安装

运行时依赖三个纯 Python 包（无 C 扩展、无需编译）：

```
Flask==3.1.3
waitress==3.0.2
pypinyin==0.55.0
```

目标机不能联网时，用仓库里已备好的离线 wheel（**按平台分目录**）：

```
app/deploy/wheels/
  linux-x86_64-py311/    # 目标服务器（Anolis OS x86_64 + CPython 3.11）
  win-x86_64/           # 本机验证用
```

`markupsafe` 带 C 扩展，wheel 与平台绑定，所以必须分平台备；
其余依赖都是 `py3-none-any`，两边通用。

目标机安装：

```bash
python3 -m venv .venv
.venv/bin/pip install --no-index --find-links deploy/wheels/linux-x86_64-py311 \
  -r backend/requirements.txt
```

需要换平台/版本时，在联网机重新下载：

```bash
pip download -r backend/requirements.txt -d wheels/<平台>-<版本> \
  --only-binary=:all: --platform manylinux2014_x86_64 --python-version 3.11 \
  --implementation cp --abi cp311
```

`fonttools` / `brotli` **只在重新生成字体子集时需要**（`scripts/build_fonts.py`），
运行时不装也不影响任何功能——字体文件已经作为构建产物放在 `frontend/dist/fonts/` 里。

## 2. Python 版本

要求 **≥ 3.11**（用到 `X | None` 语法与 `match` 之外的新标准库行为）。Anolis 8 默认 3.6，需装 python3.11：

```bash
dnf install -y python3.11 python3.11-pip
```

`run.sh` 可用 `APP_PYTHON=python3.11` 指定解释器。

## 3. 前端产物

`frontend/dist/` 是构建产物，**在开发机构建好后随包带走**（目标机不必装 Node）：

```bash
cd frontend && npm ci && npm run build   # 产出 dist/
```

若目标机也要构建，需 Node 20 LTS（只在构建期需要，运行期不需要）。

`dist/` 里必须同时有 `index.html`、`assets/`、`fonts/`。后者是思源宋体子集
（Regular / Medium 各约 750 KB，另有 `OFL.txt` 授权原文），**不能只拷 `assets/`**。

## 4. 中文路径与编码

- 快照文件名含中文，Python 侧全部显式 `encoding="utf-8"`，路径用 `pathlib`；
- 系统 locale 建议设为 `C.UTF-8` 或 `zh_CN.UTF-8`，否则 `ls` 与日志里的中文可能显示为问号（不影响程序读写）；
- 检查：`locale` 输出含 `UTF-8`。

## 5. 监听地址与防火墙

`APP_HOST` 默认 `0.0.0.0`，评审从各自电脑访问同一台机器：

```bash
firewall-cmd --permanent --add-port=8080/tcp && firewall-cmd --reload
# SELinux 若为 enforcing，确认允许绑定端口
semanage port -l | grep 8080
```

只允许本机访问时设 `APP_HOST=127.0.0.1`。

## 6. 字体随产物分发，不依赖客户端

**旧口径已作废**：早期版本写「前端用系统字体栈，评审机缺字是客户端问题」。实测这会在
龙蜥 + 精简 Linux 桌面上造成整片中文缺字（本机 Windows 有楷体/宋体，完全看不出来）。

现在字体子集随 `dist/fonts/` 一起分发：

| 项 | 值 |
|---|---|
| 字体 | 思源宋体 Noto Serif SC（SIL OFL 1.1，`fonts/OFL.txt` 为授权原文） |
| 字重 | Regular / Medium 两档 woff2 |
| 字符集 | 3,226 字 = 423 篇语料 ∪ 前端源码 ∪ 风格索引词人名 ∪ 本地题库（含保密 300 题面）∪ 常用标点 |
| 体积 | 751 KB + 762 KB，全字库各 12 MB |
| 兜底 | 字形缺失时回落系统字体；已知 1 字 `𨓦`（U+284E6，CJK 扩展 B）源字体本身没有 |

后端在模块导入时注册 `font/woff2` MIME（Python 的 `mimetypes` 默认不认识 woff2，
否则会以 `application/octet-stream` 下发）；`/fonts` 与 `/assets` 走长缓存
`immutable`，`index.html` 走 `no-cache`。**服务器不需要装任何中文字体包**，
也不要为此 `dnf install wqy-zenhei`。

语料或题库扩容后，在开发机重跑一次并重新构建前端：

```bash
pip install fonttools brotli
APP_QUIZ_DIR=/path/to/quiz python app/scripts/build_fonts.py
cd app/frontend && npm run build
```

`tests/test_fonts.py` 会校验语料与题面的每个字都在子集里，忘了重跑会直接失败。

## 7. 数据与权限

- Tier 1 快照只读：`chmod 444 data/知识库审计快照V1.jsonl`；
- 运行用户不需要写权限，除 `data/results/`（评测产物）外；
- 不要以 root 运行；建专用账号：`useradd -r -s /sbin/nologin cilv`。

## 8. 启动与自启（systemd）

```ini
# /etc/systemd/system/cilv.service
[Unit]
Description=Cilv Tanwei research assistant
After=network.target

[Service]
Type=simple
User=cilv
WorkingDirectory=/opt/cilv/app
Environment=APP_HOST=0.0.0.0
Environment=APP_PORT=8080
Environment=APP_SNAPSHOT=/opt/cilv/data/知识库审计快照V1.jsonl
Environment=APP_DIST=/opt/cilv/app/frontend/dist
ExecStart=/opt/cilv/app/.venv/bin/python /opt/cilv/app/backend/server.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now cilv
journalctl -u cilv -f
```

## 9. 部署后必查（一条命令）

```bash
curl -s http://127.0.0.1:8080/api/health | python3 -m json.tool
```

要点：

| 字段 | 期望 | 不符合时看哪里 |
|---|---|---|
| `app` | `ok` | 进程是否起来、端口是否被占 |
| `db` | `ok` | `APP_SNAPSHOT` 路径与读权限 |
| `works` | `423` | 快照是否完整 |
| `platform` | `linux / Python 3.11+` | 是否误用了别的解释器 |
| `dist` | `built` | `frontend/dist/index.html` 是否存在 |

再验一次真实求解与字体下发：

```bash
curl -s -X POST http://127.0.0.1:8080/api/solve \
  -H 'Content-Type: application/json' \
  -d '{"question":"<从 /api/demo 取一道>"}' | python3 -m json.tool

# 字体 MIME 必须是 font/woff2；若为 octet-stream 说明用了旧版代码
curl -sI http://127.0.0.1:8080/fonts/NotoSerifSC-Regular.subset.woff2 | grep -i content-type
# 顺手看一眼 dist 是否拷全
ls -l /opt/cilv/app/frontend/dist/fonts/
```

## 10. 迁移时最容易踩的三处

| 坑 | 现象 | 处理 |
|---|---|---|
| 端口被旧进程占用 | 新进程启动无报错但访问到旧版本 | `ss -ltnp \| grep 8080` 后 kill 旧进程 |
| 快照路径写成相对路径 | 在 `/` 下启动时 `db=missing` | 用绝对路径，`run.sh` 已默认绝对化 |
| 题库未配置 | `/api/demo` 404 | 设 `APP_QUIZ_DIR`；不配也能求解，只是没有示例题 |
| `dist/` 只拷了 `assets/` | 中文全部掉字，但接口全绿 | `dist/fonts/` 必须一起拷；`curl -sI .../fonts/*.woff2` 应为 `font/woff2` |
| 语料扩容后忘了重跑字体子集 | 新字在评审机上显示豆腐块 | `python scripts/build_fonts.py && npm run build`；`tests/test_fonts.py` 会拦住 |
