# 把光借给你 · 多模态儿童故事生成流水线

> AIGC 应用开发实践 · 第 1–8 周作业合集
> 作者：聂浩洋　|　课程：AIGC 应用开发实践（大模型工程实践课）

同一个项目贯穿八周：从「一个网页」长成「一条能把创意跑成绘本 + 有声书 + 短视频的流水线」，
最后一周收束为可现场演示、可复现、可讲清楚每个技术取舍的完整工程。

---

## 一、成品长什么样

| 产物 | 位置 | 规格 |
| --- | --- | --- |
| 绘本 PDF | `outputs/book/把光借给你.pdf` | A4 竖版，11 页，5.4 MB |
| 网页绘本 | `outputs/book/index.html` | 点击/键盘翻页，含封面与角色介绍 |
| 有声书 | `outputs/audio/把光借给你_有声书.mp3` | 4 分 52 秒，4 音色，54 句，含 BGM |
| 同步高亮网页 | `outputs/web/audiobook.html` | 逐句高亮 + 配图随场景切换 |
| 故事短片 | `outputs/video/把光借给你_故事短片.mp4` | 4 分 53 秒，1080p，硬字幕 |
| 故事网页（作业0） | `src/week0_web/index.html` | 4 个世界，输入姓名/年龄/兴趣即生成 |
| 素材库检索 | `src/week5_rag/` | 35 条素材，文本 + 图像双通道 |

> 直接双击 HTML 就能看；PDF / MP3 / MP4 均为本地文件，无需联网。
> 界面截图在 `docs/screenshots/`（故事网页首页/阅读页、网页绘本、有声书同步页）。

---

## 二、八周与作业的对应关系

| 周次 | 作业 | 我交的东西 | 代码 |
| --- | --- | --- | --- |
| 第1周 | 作业0 Trae 个性化故事网页 | 4 个世界的交互式故事页 | `src/week0_web/` |
| 第2周 | 作业1 结构化故事生成器 | 4 种 Prompt 策略 + 自动评估 + 对比报告 | `src/week1_story/` |
| 第3周 | 作业2 故事配图 / 绘本 | 角色一致性方案 + PDF/网页绘本 | `src/week2_illustration/` |
| 第4周 | 作业3 多角色有声书 | 4 音色 + 16 种情绪参数 + 同步高亮 | `src/week3_tts/` |
| 第5周 | 作业4 故事短视频 | Ken Burns 自动剪辑 + ASS 字幕烧录 | `src/week4_video/` |
| 第6周 | 作业5 素材库与多模态检索 | 10 模板 / 20 图 / 5 音效 + CLIP 检索 | `src/week5_rag/` |
| 第7周 | 作业6 多模态故事 Agent | 6 个工具 + 依赖图 + 中途干预 | `src/week6_agent/` |
| 第8周 | 综合考核 | 项目报告 + 答辩稿 + 演示视频脚本 | `docs/` |

---

## 三、目录结构

```
第01-08周_基础作业/
├── README.md                  # 本文件
├── requirements.txt
├── .env.example               # 密钥模板（真实 .env 不进仓库）
├── ai_log.md                  # AI 协作日志（第1-8周）
├── docs/                      # 每周的方案说明与报告
│   ├── 作业0_故事网页说明.md
│   ├── 作业1_Prompt策略对比.md
│   ├── 作业2_角色一致性方案.md
│   ├── 作业3_音色映射表.md
│   ├── 作业4_视频生成流程.md
│   ├── 作业5_素材库与检索评估.md
│   ├── 作业6_Agent架构与案例.md
│   ├── 第8周_项目报告.md
│   └── 第8周_答辩稿.md
├── src/
│   ├── common/                # 公共能力：LLM 客户端 / Schema / Prompt / 音频工具
│   ├── week0_web/             # 作业0
│   ├── week1_story/           # 作业1
│   ├── week2_illustration/    # 作业2
│   ├── week3_tts/             # 作业3
│   ├── week4_video/           # 作业4
│   ├── week5_rag/             # 作业5
│   └── week6_agent/           # 作业6
├── data/
│   ├── stories/               # 故事 JSON + 配音脚本
│   ├── raw_outputs/           # 4 种 Prompt 策略的原始输出
│   └── material_library/      # 素材库（模板/图片/音效/索引）
├── outputs/                   # 所有多模态产物
├── scripts/                   # 一键复现脚本 run_weekX.py
└── tests/                     # 单元测试
```

---

## 四、跑起来

```bash
# 1. 环境（Python 3.10+）
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt

# 2. 配置密钥（可选，不配也能跑：默认 replay 模式）
copy .env.example .env

# 3. 一键复现
python scripts/run_week2.py       # 绘本 PDF + 网页
python scripts/run_week3.py       # 有声书 + 同步高亮页
python scripts/run_week4.py       # 故事短片
python scripts/run_week6.py --all # Agent 两个案例 + 中途干预

# 4. 单独跑某一周
python -m src.week1_story.generate --compare
python -m src.week5_rag.cli "太空冒险" --recommend
```

### 关于「没有 API Key 也能跑」

课程环境里我不一定有商用大模型额度，所以每个环节都做了**降级设计**，
保证交上去的东西在老师电脑上双击就能看、能跑：

| 环节 | 在线（有 Key） | 离线（默认） |
| --- | --- | --- |
| 文本生成 | OpenAI 兼容接口 / DeepSeek | `replay`：回放 `data/stories/*.json` |
| 文生图 | 通义万相 / DALL·E 3 | `manifest`：取 `outputs/images` 已生成成品 |
| 语音合成 | Edge TTS（免费，无需 Key） | 同左，联网即可 |
| 视频 | FFmpeg | 同左 |
| 检索 | Chinese-CLIP | TF-IDF + HSV 分块直方图 |

降级不是"假装跑通"：接口、参数、产物格式完全一致，只是把"模型调用"换成了"读缓存"。
换 Key 只需改 `.env`，代码零改动。

---

## 五、我在这个项目里真正解决的三个工程问题

**1. 角色一致性（作业2）**
同一角色出现在 8 个场景里，不能每个场景都长一张新脸。
做法是把外貌写成不可变的「角色特征卡」，每次生成原样拼进 prompt，再固定 seed，
最后以角色设定图作参考图做条件生成。8 张图肉眼可辨是同一个孩子。

**2. 模态间的时间对齐（作业3/4）**
字幕、配音、画面三者必须对齐到同一根时间轴。
做法是先合成逐句音频、解码成 PCM 精确量出每句时长，再累加得到 `timeline.json`；
视频剪辑和字幕烧录都只读这一份时间轴。改一句台词，全链路自动跟着变。

**3. 成本与可复现（全局）**
多模态调用很贵，所以：LLM 响应按 prompt 哈希缓存；同一 seed 保证同图；
Agent 有依赖图，改名字只重跑配音/剪辑/排版，不重做已生成的 8 张图。

---

## 六、代码质量说明

- **分层**：`common`（能力）→ `weekN_*`（业务）→ `scripts`（入口），业务之间不互相 import。
- **错误处理**：LLM 调用 3 次指数退避重试；JSON 解析失败自动二次追问；单句 TTS 失败不拖垮整本。
- **配置管理**：所有密钥走 `.env`，`.gitignore` 已排除；代码里没有任何硬编码 key。
- **可测试**：`tests/` 覆盖 JSON 修复、Schema 校验、时间轴计算、字幕断行等纯逻辑。
- **可观测**：每次运行输出 token 用量与估算成本；Agent 全程留 trace。

---

## 七、AI 协作说明

本项目全程使用 AI 辅助（WorkBuddy / Claude / DeepSeek），
每一周的交互记录、Prompt 原文、AI 输出与人工修改都写在 [`ai_log.md`](ai_log.md)，
代码与文档的 AI 生成占比也在其中逐项估算。

简要说：**结构、取舍、验收标准由我定，实现细节大量借助 AI，每一处 AI 产出我都读过并改过。**
