# 字体来源与授权

本目录的两个 woff2 是 **思源宋体（Noto Serif SC）** 的子集，只保留本项目界面与语料
实际用到的 3,226 个字符，供龙蜥等无中文字体的环境使用。

| 项 | 内容 |
|---|---|
| 上游 | `googlefonts/noto-cjk` → `Serif/SubsetOTF/SC/NotoSerifSC-{Regular,Medium}.otf` |
| 授权 | SIL Open Font License 1.1（本目录 `OFL.txt` 为原文），允许子集化与随产品分发 |
| 子集化 | `python app/scripts/build_fonts.py`（fonttools + brotli；字符集来自 Tier 1 快照、前端源码、风格索引词人名、本地题库题面与答案、常用标点） |
| 体积 | Regular 751 KB / Medium 762 KB（woff2，合计 1.5 MB） |
| 未包含 | 生僻字与未入语料的字；缺失字符由系统字体兜底，界面不会因此缺行 |

重新生成字体（例如语料扩容后）：

```bash
pip install fonttools brotli
# 源字体放在 app/frontend/.fontsrc/（12 MB/字重，来自 noto-cjk 的 SubsetOTF/SC）
# 题库在位时一并扫描，字符集才完整
APP_QUIZ_DIR=/path/to/quiz python app/scripts/build_fonts.py
cd app/frontend && npm run build
```
