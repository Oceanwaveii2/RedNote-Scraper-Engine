"""Auto-Document Reader & Topic Grouper.

Analyzes ingested post captions, tags, and comment sentiment to suggest
topics and functional categories without overwriting user classifications.
"""
from __future__ import annotations
import re
from typing import List, Dict, Set
from .taxonomy import TOPIC_CATEGORIES, CLASSES

# Topic Keyword Patterns
TOPIC_RULES: Dict[str, List[str]] = {
    "Character": ["角色", "御姐", "修女", "少女", "辣妹", "舞娘", "芭比", "猫咪", "小猫", "主角", "原型"],
    "Transformation": ["变身", "膨胀", "换装", "反差", "复健", "穿搭", "体型", "蜕变", "变形"],
    "Outfit": ["穿搭", "修女服", "手套", "十字架", "长靴", "紧身裙", "涂装", "黑金", "靴子", "道具"],
    "Humor": ["搞怪", "滑稽", "比奇堡", "海绵宝宝", "梦回童年", "哈哈", "幽默", "魔性", "猎奇"],
    "Body language": ["眼神", "气场", "姿态", "摆拍", "摆造型", "侧脸", "卡点", "步伐", "站姿"],
    "Daily activity": ["清晨", "日常", "遛猫", "楼梯间", "出去玩", "记录", "散步", "跑腿"],
    "Object": ["皮质手套", "十字架", "猫包", "模型", "涂装", "道具", "月饼", "锁屏"],
    "Location": ["比奇堡", "楼梯间", "修道院", "街区", "沙龙", "理发店", "门厅"],
    "Relationship": ["邻居", "姐妹", "搭档", "刺杀队", "梦幻联动", "带小猫", "互动"],
    "Meme": ["梗图", "卡点", "比奇堡", "猎奇", "魔性", "表情包", "反差萌"],
    "Narrative premise": ["集结完毕", "美好的一天", "挑战", "刺杀", "三视图", "任务"],
    "Behavior": ["抱猫", "摆拍", "眼神杀", "转场", "扛起来", "调皮", "乖巧"],
    "Social reaction": ["气场全开", "太绝了", "好评", "梦回童年", "太魔性", "好飒", "评论区"],
}

def analyze_topics(text: str, tags: List[str] = None) -> List[Dict[str, any]]:
    """Analyzes text + tags and returns suggested topics with confidence scores."""
    combined = f"{text or ''} {' '.join(tags or [])}"
    suggestions = []

    for topic, keywords in TOPIC_RULES.items():
        matched_kw = [kw for kw in keywords if kw in combined]
        if matched_kw:
            score = min(1.0, 0.4 + (len(matched_kw) * 0.2))
            suggestions.append({
                "topic": topic,
                "confidence": round(score, 2),
                "matchedKeywords": matched_kw,
            })

    suggestions.sort(key=lambda s: s["confidence"], reverse=True)
    return suggestions

def suggest_class_and_character(text: str, tags: List[str] = None) -> Dict[str, str]:
    """Suggests primary taxonomy class and character association based on content."""
    combined = f"{text or ''} {' '.join(tags or [])}"
    
    # Class determination
    if any(k in combined for k in ["修女", "刺杀队", "御姐", "角色"]):
        suggested_class = "Costume" if "服" in combined or "手套" in combined else "Character"
    elif any(k in combined for k in ["比奇堡", "海绵宝宝", "猎奇", "搞怪"]):
        suggested_class = "Meme"
    elif any(k in combined for k in ["涂装", "模型", "三视图"]):
        suggested_class = "Reference"
    elif any(k in combined for k in ["抱猫", "遛猫", "摆拍"]):
        suggested_class = "Behavior"
    else:
        suggested_class = "Visual Style"

    # Character Association
    if any(k in combined for k in ["修女", "刺杀队", "黑金"]):
        char = "Rainee"
    elif any(k in combined for k in ["变身", "反差", "蜕变"]):
        char = "Transition Rainee"
    elif any(k in combined for k in ["搞怪", "滑稽", "魔性"]):
        char = "Shin-chan Rainee"
    elif any(k in combined for k in ["抱猫", "长靴", "遛猫"]):
        char = "Supporting Character"
    else:
        char = "Unassigned"

    return {
        "suggestedClass": suggested_class,
        "suggestedCharacter": char,
    }
