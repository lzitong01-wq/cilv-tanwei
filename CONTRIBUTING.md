# 贡献指南

感谢你愿意为「词律探微」出力。本文档说明这个项目**特有的**协作约定——通用开源流程请自行参照社区规范。

---

## 0. 先读这三条硬约束

这三条是项目的不变量，PR 里违反任何一条都不会被合并。

| 约束 | 原因 |
|---|---|
| **口径必须分层，不能混算** | Tier 1（423 篇官方快照）是唯一官方基准；Tier 2（同源语料 43,191 首）是非官方口径。两者绝不能混在一起给数字。 |
| **「未实现」要显式出口** | 宁可返回"没做/需要人工复核"，也不许静默降级、编数字、用估算冒充实测。 |
| **报告里的数字必须可复算** | 写了"命中 96.3%"就要能给出复现命令与产物路径。 |

第三条是本项目最容易翻车的地方：**报告写「已作废 N 条」但代码里只标记未作废＝虚报**。发现后要改的是**实现**，不是改说法。

---

## 1. 环境

```bash
# Python 3.11+（用到 X | None 语法）
python -m venv .venv && .venv/bin/pip install -r app/backend/requirements.txt

# 前端构建（仅构建期需要 Node 20）
cd app/frontend && npm ci && npm run build
```

**没有 pytest**，测试一律用标准库：

```bash
python -m unittest discover -s app/tests
```

其他常用校验：

```bash
python app/scripts/check_invariants.py    # 不变量认领表
python app/scripts/check_contract.py      # 文档 ↔ 实现 ↔ 前端 三方端点比对
python app/scripts/smoke_endpoints.py     # 接口冒烟
python app/scripts/build_package.py       # 打包（带完整性校验）
```

---

## 2. 分支与提交

```bash
git checkout -b feature/你的改动
# …改代码…
python -m unittest discover -s app/tests && python app/scripts/check_contract.py
git commit -m "简述改动做了什么、为什么"
git push origin feature/你的改动
```

提交信息用中文，一句话说清「做了什么」和「为什么」，不写「修复若干问题」这种。

---

## 3. 改代码前先确认口径

这个项目大量功能依赖"用哪一层的语料、哪个索引、哪个版本"。动手前先在 `词律探微-总体规划V6.1.md` 和 `分册A/B/C/D` 里查清楚：

- 我改的这个功能属于哪个板块？分册 A 是声律引擎、B 是交互与副功能、C 是数据管线与授权、D 是评测交付。
- 它依赖的语料层是 Tier 1 还是 Tier 2？
- 它有没有对应的**不变量**（`app/invariants.json`）？有的话，改完要一起更新认领用例。

**不确定口径就问，别猜。** 这个项目里"看起来对"的实现有一半是错的，因为口径混用了。

---

## 4. 数据与授权（重要）

| 类别 | 处理 |
|---|---|
| Tier 1 官方快照（423 篇） | 只读，随仓库分发 |
| Tier 2 同源语料 | 派生事实随仓库分发；**原始源文件不入库**（分册 C §C4） |
| 词谱体式数据 | 原始文件仅作构建期输入，**只分发派生事实**（`prosody_index.json` / `guxin_tune.json`） |
| 题库（公开 700 / 保密 300） | **不入库**，通过环境变量 `APP_QUIZ_DIR` 挂载 |
| 字体子集 | 思源宋体 SIL OFL 1.1，**随产物分发**，`OFL.txt` 与 `NOTICE.md` 必须一起带上 |

**不要把题库、词谱源文件或其他受限数据提交进仓库。** 构建期索引产物（`app/data/*.sqlite3` 等）体积大，走 Git LFS。

---

## 5. 大文件用 Git LFS

`tier2_locator.sqlite3` 约 165 MB，超过 GitHub 单文件 100 MB 硬限制。仓库已配置 LFS：

```bash
git lfs install          # 首次
git lfs ls-files         # 看哪些文件在 LFS 里
```

**不要试图绕过 LFS 直接提交大二进制**——会被 GitHub 直接拒绝 push。

---

## 6. 前端改动

- 主题走 CSS 变量（`styles.css` 的 token），**组件里不写硬编码色值**；
- 字体子集要覆盖新出现的字：改了语料或题库后必须重跑 `scripts/build_fonts.py`，否则新字显示豆腐块（`tests/test_fonts.py` 会拦住）；
- 改完跑 `npm run build`（含 `tsc --noEmit`）。

---

## 7. 提交前的自检清单

- [ ] `python -m unittest discover -s app/tests` 全过
- [ ] `python app/scripts/check_contract.py` 三方一致
- [ ] `python app/scripts/check_invariants.py` 通过
- [ ] 涉及语料/题库的改动，重跑了字体子集
- [ ] 改了实现，**同步更新了对应文档**（`/docs` 与根目录规划文档）
- [ ] 新增端点已登记进 `前后端一致性与接口契约V1.md` §3.2
- [ ] `python app/scripts/build_package.py` 通过（它会校验源码完整性）

---

## 8. 文档同步是硬要求

这个项目的文档与实现**必须一致**。发现不一致时选一个：改代码、改文档、或记录偏差原因——**不能放着不管**。

实现与文档偏离时，PR 里要写明：偏离点、原因、潜在影响。

---

## 9. 部署相关改动

改动 `app/deploy/` 下的东西会影响真实服务器，务必先读 `app/deploy/CICD说明.md`（含服务器环境实测数据与带宽限制）。几条容易踩的：

- 服务器系统 SQLite 是 3.26.0，**不支持 FTS5 trigram**，靠 `core/sqlite_compat.py` 自动切 `pysqlite3`；
- `pysqlite3-binary` 只发布 Linux wheel，**只能是可选依赖**（`requirements-optional.txt`），放进必装清单会让 Windows 离线安装失败；
- 单次 HTTP 下载超过约 3.9 MB 会截断，传输必须分片。

---

## 10. 提问

口径问题、实现思路、要不要做某个功能——先在 `项目一页纸.md` 和 `CHANGELOG.md` 里找有没有结论。确实没有，再开 issue 讨论。

**不要闷头做两天然后发现方向错了。**
