# 作业6 · 交互式多模态故事 Agent

> 代码：`src/week6_agent/`　｜　运行：`python scripts/run_week6.py --all`
> 日志：`outputs/traces/log_案例1.md`、`log_案例2.md`、`log_案例2_干预后.md`

---

## 一、Agent 要解决的真问题

前六周我已经有一条能跑的流水线了——但它是六个脚本，我得按顺序手动敲六次命令，
中间还得记住"上一步的输出文件叫什么"。

Agent 要做的不是"更聪明"，是**把这六步串起来，并且允许我在中途改主意**。
这两个诉求对应两件事：**工具编排** 和 **可干预**。

---

## 二、架构

```
                    ┌──────────────────────────────┐
   创意 ──→ Planner ─│  RulePlanner（离线，固定 DAG） │
                    │  LLMPlanner（在线，模型拆步）   │
                    └──────────────┬───────────────┘
                                   │ steps[]
                                   ▼
                    ┌──────────────────────────────┐
                    │  StoryAgent                  │
                    │  ├ state{}   上一步产物 → 下一步入参
                    │  ├ trace[]   每步工具/入参/耗时/产物
                    │  └ intervene(指令) → 只重跑下游
                    └──────────────┬───────────────┘
                                   │
        ┌──────────────┬───────────┼───────────┬──────────────┐
        ▼              ▼           ▼           ▼              ▼
  search_materials  write_story  illustrate   voice      compose_video   build_book
   （RAG 检索）      （结构化）    （配图）    （配音）      （剪辑）        （排版）
```

### 六个工具（满足"至少调用 4 个工具"）

| # | 工具 | 入参 | 产出 | 依赖 |
| --- | --- | --- | --- | --- |
| 1 | `search_materials` | query, top_k | 模板/配图/音效 + 结构化建议 | — |
| 2 | `write_story` | request, story_file | story.json | request |
| 3 | `illustrate` | story_file, source_dir, src_prefix, out_prefix | 8 张场景图 | story |
| 4 | `voice` | script_file, tag | mp3 + timeline.json | story |
| 5 | `compose_video` | story_file, timeline_file, tag, img_prefix | mp4 | story + voice |
| 6 | `build_book` | story_file, tag | pdf | story + illustrate |

### 为什么规划器要可插拔

`RulePlanner` 是固定 DAG——因为这个任务的步骤本来就是确定的，让模型重新"想"一遍是浪费。
但 `LLMPlanner` 我留着了：如果以后要支持"只做图不做视频""先出三幕看看效果"这种
非常规需求，就需要模型来拆步骤。两者接口一致，`StoryAgent(planner=...)` 一行切换。

**没有 LLM 时自动回退**：`LLMPlanner.plan()` 检测到 `LLM.provider == "replay"` 会直接
`super().plan(case)`，不会抛异常。

---

## 三、中途干预：这次作业的核心

### 3.1 怎么解析

正则，不是 LLM。因为四类高频指令是封闭集合，正则又快又稳：

```python
patterns = [
    (r"主角(?:的名字)?(?:改成|叫|换成)[「“\"]?([^\s，,。「」”\"]{1,8})", "protagonist"),
    (r"(?:年龄|岁数)(?:改成|换成)\s*(\d{1,2})", "age"),
    (r"(?:场景|地点|世界)(?:改成|换成|换到)[：:]?[「“\"]?([^\s，,。「」”\"]{1,12})", "scene"),
    (r"主题(?:改成|换成)[「“\"]?([^\s，,。「」”\"]{1,12})", "theme"),
]
```

匹配不上就**明确拒绝**，并说明"这条需要 LLM 在线"。不假装能做到。

### 3.2 怎么判断影响范围

```python
@staticmethod
def _affected(before, after):
    # 改了年龄/场景/主题 → 故事要重写 → 全链路重跑
    if any(before[k] != after[k] for k in before if k != "protagonist"):
        return ["write_story", "illustrate", "voice", "compose_video", "build_book"]
    # 只改名字 → 图片里主角的脸没变，复用
    if before["protagonist"] != after["protagonist"]:
        return ["voice", "compose_video", "build_book"]
    return []
```

**"只改名字就只重跑三步"不是省事，是正确性。**
图片里主角的长相、服装、动作一个像素都没变，重画一遍只会引入新的不一致。

### 3.3 改名怎么落地

不做"重新创作"，做**确定性全文替换**：

```python
dst = src.with_name(f"{src.stem}_干预后.json")   # 写副本，不污染原故事
dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
self._rename_in_story(dst, "阿木", "小树")
```

然后把 state 里的 `story_file` 指向副本，下游三步自动读到新文件。
**原始故事保持干净**——这一点很重要，否则演示一次就把素材改坏了。

---

## 四、两个完整案例

### 案例 1 · 《把光借给你》

| 项 | 值 |
| --- | --- |
| 输入创意 | 一个怕黑的小女孩在阁楼点亮旧灯笼，遇见一只靠吞灯光取暖的乌云小怪 |
| 请求参数 | 小满 / 7 岁 / 夜晚的城市屋顶 / 怕黑与分享 / 长 / 6-8 岁 |
| 步骤 | 6 步全部成功 |
| 总耗时 | 101.3 秒（跳过视频） |
| 产物 | story.json → 8 张图 → 4 分 52 秒有声书 → 11 页绘本 |

### 案例 2 · 《第六颗星》

| 项 | 值 |
| --- | --- |
| 输入创意 | 在太空站过夜的小男孩，想念家里的灯，遇见一颗很久没人跟它说话的星星 |
| 请求参数 | 阿木 / 6 岁 / 太空站 / 想念家人 / 短 / 5-7 岁 |
| 步骤 | 6 步全部成功 |
| 总耗时 | 204.0 秒（含视频） |
| 产物 | story.json → 4 张图 → 1 分 52 秒有声书 → 1 分 52 秒短片 → 6 页绘本 |

**案例 2 的意义**：它不是"再跑一遍案例 1"。
素材来自素材库（`src_prefix="space"`），故事是另一份 JSON，配音是另一份脚本，
但**六个工具的代码一行没改**。这是"流水线"和"一次性脚本"的分界线。

### 案例 2 的中途干预

```
用户：把主角的名字改成小树

Agent:
  intervene  用户：把主角的名字改成小树
  replan     变更字段：{'protagonist': '小树'}　受影响步骤：['voice','compose_video','build_book']
  rewrite    全文替换主角名：阿木 → 小树（写入副本 space_story_干预后.json）
  act        #99 voice(script_file=space_script.json, tag=案例2干预后)
  observe    #99 voice done
  act        #99 compose_video(...)
  observe    #99 compose_video done
  act        #99 build_book(...)
  observe    #99 build_book done
```

**配图一步没跑**，因为 `_affected()` 判定它不受影响。

---

## 五、创作流程日志

每次运行会落两份 trace：

- `outputs/traces/trace_案例X.json`：机器可读，含每一步的完整入参、产物路径、耗时、状态。
- `outputs/traces/log_案例X.md`：人类可读，一张表 + 一段"决策与观察"。

日志里能看到的不只是"成功了"，还有"为什么这么走"：

```
| # | 步骤 | 工具 | 耗时 | 状态 | 产物 |
| 0 | search_materials | search_materials | 2.7s | done |  |
| 1 | write_story | write_story | 0.0s | done | space_story.json |
| 2 | illustrate | illustrate | 0.1s | done | 4 张 |
| 3 | voice | voice | 42.8s | done | 案例2_第六颗星_有声书.mp3 |
| 4 | compose_video | compose_video | 154.4s | done | 案例2_故事短片.mp4 |
| 5 | build_book | build_book | 1.8s | done | 案例2_第六颗星.pdf |
```

---

## 六、工程细节

| 细节 | 做法 | 为什么 |
| --- | --- | --- |
| 异常兜底 | `_exec()` 里 `try/except Exception`，失败写入 trace 并终止后续 | Agent 不能因为一个工具挂了就整体崩溃，得能说清"挂在哪" |
| 状态传递 | `state` 字典，上一步结果的 key 自动成为下一步的入参来源 | 这就是"记忆"，也是它能串起来的原因 |
| 幂等 | 图片/音频按 tag 命名，重跑覆盖自己的产物，不影响别的案例 | 演示可以反复跑 |
| 干预后隔离 | 干预产物带 `案例2干预后_` 前缀，故事写副本 | 一次演示不会毁掉原始素材 |

---

## 七、已知局限

1. **只支持四类干预**（主角名/年龄/场景/主题）。"换个更开心的结尾"这类需要 LLM 在线。
2. **规划是固定 DAG**，不支持"跳过某一步"或"重试某一步"的运行时决策
   （`Step.status` 已经留了 `skipped` 状态，但还没接指令）。
3. **没有多轮对话**：一次 `intervene()` 改一个需求，不是连续对话式修改。

这三条都在 `docs/第8周_项目报告.md` 的"改进方向"里。

---

## 八、复现

```bash
# 只跑案例1（复用已有产物，约 100 秒）
python scripts/run_week6.py

# 案例1 + 案例2 + 干预演示（约 6 分钟，含两次视频编码）
python scripts/run_week6.py --all
```
