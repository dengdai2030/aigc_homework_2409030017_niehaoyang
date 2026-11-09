"""Prompt 策略库（作业1 的核心对比对象）。

四种策略，逐层加约束：
    S1 zero-shot       —— 只给任务和字段清单，观察模型裸能力
    S2 few-shot        —— 给一个缩微示例，观察格式学习效果
    S3 role-play+CoT   —— 先"想"再"写"，观察叙事质量
    S4 schema+selfcheck—— 字段级约束 + 自检清单（生产默认）

Prompt 与代码分离，便于版本化管理与 A/B 对比（对应第16周选题库第16项）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

StrategyName = Literal["zero_shot", "few_shot", "role_play_cot", "schema_selfcheck"]

SCHEMA_BLOCK = """{
  "title": "故事标题",
  "characters": [{"name": "角色名", "traits": "性格特征", "appearance": "外貌描述"}],
  "scenes": [
    {"scene_id": 1, "description": "场景描述", "text": "该场景故事文本", "mood": "情绪基调", "visual_prompt": "适合文生图的Prompt"}
  ],
  "moral": "故事寓意"
}"""

FEW_SHOT_EXAMPLE = """输入：主角=豆豆，年龄=5，场景=森林，主题=勇气，长度=短，受众=4-6岁
输出：
{
  "title": "豆豆的第一声吼",
  "characters": [
    {"name": "豆豆", "traits": "胆小但守信", "appearance": "圆脸短发的小男孩，穿黄色雨靴"}
  ],
  "scenes": [
    {"scene_id": 1, "description": "豆豆在森林入口不敢往里走", "text": "豆豆站在森林口，脚像粘在了地上。树叶沙沙响，他小声说：我不怕。", "mood": "紧张", "visual_prompt": "5岁圆脸短发男孩穿黄色雨靴，站在幽暗森林入口，回头张望，绘本插画，暖色调"}
  ],
  "moral": "勇气不是不害怕，是害怕的时候仍然往前走一步。"
}"""


@dataclass
class StoryRequest:
    protagonist: str = "小满"
    age: int = 7
    scene: str = "夜晚的城市屋顶"
    theme: str = "怕黑与分享"
    length: str = "中"          # 短 / 中 / 长
    audience_age: str = "6-8岁"

    def as_line(self) -> str:
        return (f"主角={self.protagonist}，年龄={self.age}，场景={self.scene}，"
                f"主题={self.theme}，长度={self.length}，受众={self.audience_age}")

    def as_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)


SCENE_COUNT = {"短": 4, "中": 6, "长": 8}


# --------------------------------------------------------------- 策略定义
def build_zero_shot(req: StoryRequest) -> tuple[str, str]:
    system = "你是一个儿童故事生成器，只输出 JSON。"
    user = f"""请生成一个儿童故事，参数如下：{req.as_line()}。

输出 JSON，字段如下：
{SCHEMA_BLOCK}

场景数量：{SCENE_COUNT.get(req.length, 6)} 个。"""
    return system, user


def build_few_shot(req: StoryRequest) -> tuple[str, str]:
    system = "你是一个儿童故事生成器，严格模仿示例的字段与颗粒度，只输出 JSON。"
    user = f"""示例：
{FEW_SHOT_EXAMPLE}

现在请按同样的结构，生成新的故事：
{req.as_line()}

要求：
1. 字段与示例完全一致，不要增删字段。
2. 场景数量 {SCENE_COUNT.get(req.length, 6)} 个，scene_id 从 1 连续编号。
3. visual_prompt 必须是中文，包含角色外貌 + 场景 + 光线 + 画风（绘本插画）。"""
    return system, user


def build_role_play_cot(req: StoryRequest) -> tuple[str, str]:
    system = (
        "你是一位有二十年经验的儿童绘本作家，擅长用具体的动作和感官细节代替抽象说教。"
        "你相信孩子能读懂情绪，但读不懂道理，所以道理必须藏在情节里。"
        "写作前你会先在心里搭好情绪曲线：起—承—转—合，转折处必须有一次'以为是这样，其实是那样'。"
    )
    user = f"""请为 {req.audience_age} 的孩子创作一个故事：{req.as_line()}

写作前先在脑内完成三步构思（不要输出构思过程）：
1. 主角最怕什么？这个恐惧在故事里必须被具体化成一个东西。
2. 转折点：主角以为的"敌人"，真实处境是什么？
3. 结尾：主角付出了什么，又因此得到了什么？

然后输出 JSON：
{SCHEMA_BLOCK}

额外要求：
- 场景 {SCENE_COUNT.get(req.length, 6)} 个，每个场景 text 不少于 80 字，要有对话和动作。
- mood 用两个字的情绪词。
- visual_prompt 中文，写清角色外貌、动作、环境、光线、画风。"""
    return system, user


def build_schema_selfcheck(req: StoryRequest) -> tuple[str, str]:
    system = (
        "你是一个严格遵循 JSON Schema 的儿童故事生成服务。"
        "你的输出会被程序直接 json.loads 解析，任何多余字符都会导致失败。"
    )
    user = f"""任务参数（JSON）：
{req.as_json()}

生成要求：
1. 只输出一个 JSON 对象，禁止 Markdown 代码块、禁止解释性文字。
2. 结构必须严格为：
{SCHEMA_BLOCK}
3. scenes 数量 = {SCENE_COUNT.get(req.length, 6)}，scene_id 从 1 开始连续递增。
4. 每个 scene 的 text 不少于 80 个汉字，包含至少一句对白。
5. characters 中必须包含主角，主角名与参数中的 protagonist 一致。
6. visual_prompt 为中文，必须包含：主角固定外貌描述（发型/服装/随身物品）+ 当前动作 + 环境 + 光线 + "儿童绘本插画风格"。

输出前自检（不要输出自检过程，只输出最终 JSON）：
- 是否是合法 JSON？有无尾逗号？
- scenes 是否够数、scene_id 是否连续？
- 每个 scene 是否 5 个字段齐全且非空？
- moral 是否是一句话而不是一段话？"""
    return system, user


BUILDERS = {
    "zero_shot": build_zero_shot,
    "few_shot": build_few_shot,
    "role_play_cot": build_role_play_cot,
    "schema_selfcheck": build_schema_selfcheck,
}

STRATEGY_LABELS = {
    "zero_shot": "S1 零样本",
    "few_shot": "S2 少样本",
    "role_play_cot": "S3 角色扮演+思维链",
    "schema_selfcheck": "S4 Schema约束+自检",
}


def build(name: StrategyName, req: StoryRequest) -> tuple[str, str]:
    if name not in BUILDERS:
        raise KeyError(f"未知策略 {name}，可选：{list(BUILDERS)}")
    return BUILDERS[name](req)
