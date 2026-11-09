"""核心逻辑单元测试（只用标准库 unittest，不引入额外依赖）。

运行：
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.common.llm import extract_json  # noqa: E402
from src.common.schema import Story, StorySchemaError  # noqa: E402
from src.week3_tts.tts import compose_params  # noqa: E402
from src.week4_video.make_video import _wrap, scene_ranges  # noqa: E402
from src.week6_agent.agent import StoryAgent  # noqa: E402


GOOD = {
    "title": "测试故事",
    "characters": [{"name": "小满", "traits": "好奇", "appearance": "短发女孩"}],
    "scenes": [
        {"scene_id": i, "description": f"场景{i}", "text": "这是一段足够长的场景文本，用来通过最短长度校验。" * 2,
         "mood": "平静", "visual_prompt": "短发女孩站在屋顶，暖光，儿童绘本插画风格"}
        for i in range(1, 5)
    ],
    "moral": "把光分给别人。",
}


class TestJsonRepair(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})

    def test_fenced(self):
        self.assertEqual(extract_json('好的：\n```json\n{"a": 2}\n```\n以上'), {"a": 2})

    def test_noise_around(self):
        self.assertEqual(extract_json('模型说了一堆话 {"a": 3} 后面还有'), {"a": 3})

    def test_trailing_comma(self):
        self.assertEqual(extract_json('{"a": 4, "b": [1,2,],}'), {"a": 4, "b": [1, 2]})

    def test_nested_braces(self):
        raw = '{"a": {"b": {"c": 1}}, "d": 2}'
        self.assertEqual(extract_json(raw)["d"], 2)

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            extract_json("完全没有 JSON")


class TestStorySchema(unittest.TestCase):
    def test_ok(self):
        s = Story.from_dict(GOOD)
        self.assertEqual(len(s.scenes), 4)
        self.assertEqual(s.char_count, sum(len(x.text) for x in s.scenes))

    def test_missing_field(self):
        bad = dict(GOOD)
        bad.pop("moral")
        with self.assertRaises(StorySchemaError):
            Story.from_dict(bad)

    def test_too_few_scenes(self):
        bad = dict(GOOD)
        bad["scenes"] = GOOD["scenes"][:2]
        with self.assertRaises(StorySchemaError):
            Story.from_dict(bad, min_scenes=4)

    def test_duplicate_scene_id(self):
        bad = dict(GOOD)
        bad["scenes"] = [dict(s) for s in GOOD["scenes"]]
        bad["scenes"][1]["scene_id"] = 1
        with self.assertRaises(StorySchemaError):
            Story.from_dict(bad)

    def test_short_text(self):
        bad = dict(GOOD)
        bad["scenes"] = [dict(s) for s in GOOD["scenes"]]
        bad["scenes"][0]["text"] = "太短"
        with self.assertRaises(StorySchemaError):
            Story.from_dict(bad)

    def test_roundtrip(self):
        s = Story.from_dict(GOOD)
        self.assertEqual(Story.from_dict(s.to_dict()).title, s.title)


class TestTtsParams(unittest.TestCase):
    def test_emotion_adds_up(self):
        voice = {"base_rate": "-14%", "base_pitch": "-6Hz"}
        emo = {"rate": "-15%", "pitch": "-7Hz", "volume": "-10%"}
        p = compose_params(voice, emo)
        self.assertEqual(p["rate"], "-29%")
        self.assertEqual(p["pitch"], "-13Hz")
        self.assertEqual(p["volume"], "-10%")

    def test_positive_offsets(self):
        voice = {"base_rate": "-2%", "base_pitch": "+4Hz"}
        emo = {"rate": "+11%", "pitch": "+5Hz"}
        p = compose_params(voice, emo)
        self.assertEqual(p["rate"], "+9%")
        self.assertEqual(p["pitch"], "+9Hz")


class TestSubtitleWrap(unittest.TestCase):
    def test_short_no_break(self):
        self.assertNotIn("\\N", _wrap("晚上九点。", 20))

    def test_break_on_punctuation(self):
        t = "衣柜的影子像一只蹲着的猫，窗帘被风吹得鼓起来，又瘪下去。"
        out = _wrap(t, 20)
        self.assertIn("\\N", out)
        first = out.split("\\N")[0]
        self.assertTrue(first.endswith("，") or first.endswith("。"))

    def test_no_content_lost(self):
        t = "阿木就讲了。讲楼下的面馆，讲妈妈端面时烫红的手，讲那只总在窗台上晒太阳的猫。"
        self.assertEqual(_wrap(t, 20).replace("\\N", ""), t)


class TestSceneRanges(unittest.TestCase):
    def test_ranges_cover_all(self):
        timeline = {"segments": [
            {"scene": 1, "start": 0.0, "end": 5.0},
            {"scene": 1, "start": 5.3, "end": 9.0},
            {"scene": 2, "start": 10.0, "end": 15.0},
        ]}
        r = scene_ranges(timeline)
        self.assertEqual(len(r), 2)
        self.assertEqual(r[0][0], 1)
        self.assertAlmostEqual(r[0][1], 0.0, places=2)
        self.assertGreater(r[0][2], 9.0)

    def test_last_scene_has_tail(self):
        timeline = {"segments": [{"scene": 1, "start": 0.0, "end": 5.0}]}
        _, _, dur = scene_ranges(timeline)[0]
        self.assertGreater(dur, 5.0)


class TestAgentIntervention(unittest.TestCase):
    def test_parse_rename(self):
        req, changed = StoryAgent._parse("把主角的名字改成小树", {"protagonist": "阿木"})
        self.assertEqual(req["protagonist"], "小树")
        self.assertEqual(changed, {"protagonist": "小树"})

    def test_parse_scene(self):
        req, changed = StoryAgent._parse("场景换成海底", {"scene": "太空站"})
        self.assertEqual(req["scene"], "海底")

    def test_parse_unmatched(self):
        _, changed = StoryAgent._parse("让它有个更开心的结尾", {"protagonist": "阿木"})
        self.assertEqual(changed, {})

    def test_affected_only_voice_when_rename(self):
        a = {"protagonist": "阿木", "scene": "太空站"}
        b = {"protagonist": "小树", "scene": "太空站"}
        self.assertEqual(StoryAgent._affected(a, b), ["voice", "compose_video", "build_book"])

    def test_affected_full_when_scene_changes(self):
        a = {"protagonist": "阿木", "scene": "太空站"}
        b = {"protagonist": "阿木", "scene": "海底"}
        self.assertIn("illustrate", StoryAgent._affected(a, b))


if __name__ == "__main__":
    unittest.main()
