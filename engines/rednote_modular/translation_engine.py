"""Red Note Dual-Language Translation Engine.

Caches Chinese-to-English translations locally to ensure repeatable,
offline presentation without duplicate processing.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, Optional

class TranslationEngine:
    def __init__(self, cache_file: Optional[Path] = None):
        self.cache_file = cache_file or (Path(__file__).parent / "translation_cache.json")
        self._cache: Dict[str, str] = {}
        self._load_cache()

    def _load_cache(self) -> None:
        if self.cache_file.exists():
            try:
                self._cache = json.loads(self.cache_file.read_text(encoding="utf-8"))
            except Exception:
                self._cache = {}

    def save_cache(self) -> None:
        try:
            self.cache_file.write_text(
                json.dumps(self._cache, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception:
            pass

    def get(self, text: str) -> Optional[str]:
        return self._cache.get(text.strip())

    def put(self, text: str, translation: str) -> None:
        cleaned = text.strip()
        if cleaned and translation:
            self._cache[cleaned] = translation.strip()
            self.save_cache()

    def translate_or_lookup(self, text: str, fallback_translation: Optional[str] = None) -> str:
        """Returns existing translation, fallback if provided, or empty."""
        cleaned = text.strip()
        if not cleaned:
            return ""
        if cleaned in self._cache:
            return self._cache[cleaned]
        if fallback_translation:
            self.put(cleaned, fallback_translation)
            return fallback_translation
        return ""

# Common domain translations seeded for our harvested corpus
SEEDED_TRANSLATIONS = {
    "今日角色：修女刺杀队": "Today's Role: Sister Assassination Squad",
    "全员暗黑御姐风！黑金修女刺杀队集结完毕，眼神杀拉满。造型道具与姿态呈现。": "Full dark domineering sister aesthetic! The black-gold nun assassination squad is fully assembled, gaze intensity at max. Costume props and posture presentation.",
    "这套暗黑修女太绝了，气场全开！": "This dark nun set is incredible, total aura powerhouse!",
    "眼神好飒，皮质手套和十字架细节好评！": "Such a fierce look, the leather gloves and crucifix details are top notch!",
    "第4张侧脸构图有电影感，反差萌拉满。": "The side profile in photo 4 has real cinematic framing, contrast charm at maximum.",
    "美好的一天从比奇堡开始✌🏻": "A wonderful day starts from Bikini Bottom ✌🏻",
    "清晨的比奇堡日常，滑稽动作与搞怪表情包合成实录。": "Morning Bikini Bottom daily routine, slapstick antics and goofy meme expression compilation.",
    "配乐一响直接梦回童年哈哈哈哈！": "As soon as the soundtrack starts it brings me right back to childhood hahaha!",
    "这个转场节奏太舒服了，卡点好魔性。": "This transition rhythm is so satisfying, the beat drop is hypnotic.",
    "春丽涂装": "Chun-Li Custom Livery",
    "泼拉克丝（斯）": "Lux Splatter Art",
    "王者荣耀貂蝉异域舞娘模型三视图存": "Honor of Kings Diaochan Exotic Dancer 3D Model Three-View Archive",
    "美少女芭比锁屏，氛围感拿捏！💖💖💖💖💖": "Pretty Girl Barbie Lock Screen, vibe nailed! 💖💖💖💖💖",
    "芭比甜美秘籍，一秒get同款穿搭": "Barbie Sweet Secrets, get the matching outfit in one second",
    "这会不会有点太猎奇了": "Is this a bit too bizarre / grotesque?",
    "挑战超长靴抱猫｜小猫的快乐，就是这么简单！": "Challenging Over-the-Knee Boots While Holding Cat | A kitten's joy is just that simple!",
    "谁家小猫这么会摆造型啊？": "Whose kitty knows how to strike a pose like this?",
    "小猫的快乐，就是这么简单！": "A kitten's joy is just that simple!",
    "谁家小猫不调皮": "Whose little cat isn't mischievous?",
    "日常遛猫系列": "Daily Cat Walking Series",
    "今日份的楼梯间摆拍": "Today's Stairwell Posing Session",
    "职业装也可以这样穿": "Office Wear Can Also Look Like This",
    "国庆带小猫出去玩": "National Day Holiday: Taking Kitten Out to Play",
    "谁家猫猫这么乖呀，是我家的🐈‍": "Whose kitty is so well-behaved, it's mine 🐈‍",
    "记录小猫的一天｜谁家小猫这么会摆？": "Recording A Day in the Life of a Kitten | Whose kitten poses this well?",
    "小猫的重量，我扛起来！": "The Weight of a Cat, I Can Carry It!",
    "少女紧身裙穿搭，辣妹的神！": "Girl Tight Dress Outfit, Queen of Hot-Girl Aesthetics!",
    "甜甜的月饼，甜甜的小猫": "Sweet Mooncake, Sweet Little Kitty",
    "🩰芭蕾舞和小猫咪的梦幻联动！": "🩰 Ballet and Little Kitten's Dream Collaboration!"
}

default_translator = TranslationEngine()
for k, v in SEEDED_TRANSLATIONS.items():
    if not default_translator.get(k):
        default_translator.put(k, v)
