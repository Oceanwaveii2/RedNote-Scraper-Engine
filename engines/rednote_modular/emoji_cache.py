"""Xiaohongshu Emoji Cache and Mapping Library.

Preserves Xiaohongshu-specific emojis and standard emoticons for offline rendering.
"""
from __future__ import annotations
from typing import Dict, Optional

# Standard Xiaohongshu Emoji Mappings
# Maps bracketed codes like [害羞R], [偷笑R], [比心R] to unicode equivalents and local SVG asset paths
REDNOTE_EMOJI_MAP: Dict[str, Dict[str, str]] = {
    "[害羞R]": {"unicode": "😳", "name": "shy", "pinyin": "haixiu"},
    "[偷笑R]": {"unicode": "🤭", "name": "smirk", "pinyin": "touxiao"},
    "[比心R]": {"unicode": "🫰", "name": "finger_heart", "pinyin": "bixin"},
    "[赞R]": {"unicode": "👍", "name": "thumbs_up", "pinyin": "zan"},
    "[飞吻R]": {"unicode": "😘", "name": "blow_kiss", "pinyin": "feiwen"},
    "[色R]": {"unicode": "😍", "name": "heart_eyes", "pinyin": "se"},
    "[大笑R]": {"unicode": "😄", "name": "big_smile", "pinyin": "daxiao"},
    "[斜眼R]": {"unicode": "🙄", "name": "side_eye", "pinyin": "xieyan"},
    "[哭惹R]": {"unicode": "😭", "name": "crying", "pinyin": "kure"},
    "[笑哭R]": {"unicode": "😂", "name": "joy", "pinyin": "xiaoku"},
    "[派对R]": {"unicode": "🥳", "name": "party", "pinyin": "paidui"},
    "[心碎R]": {"unicode": "💔", "name": "broken_heart", "pinyin": "xinsui"},
    "[生病R]": {"unicode": "🤒", "name": "sick", "pinyin": "shengbing"},
    "[失望R]": {"unicode": "😞", "name": "disappointed", "pinyin": "shiwang"},
    "[汗颜R]": {"unicode": "😅", "name": "sweat_smile", "pinyin": "hanyan"},
    "[得意R]": {"unicode": "😎", "name": "proud", "pinyin": "deyi"},
    "[思考R]": {"unicode": "🤔", "name": "thinking", "pinyin": "sikao"},
    "[看R]": {"unicode": "👀", "name": "eyes", "pinyin": "kan"},
    "[火R]": {"unicode": "🔥", "name": "fire", "pinyin": "huo"},
    "[哇R]": {"unicode": "🤩", "name": "star_struck", "pinyin": "wa"},
    "[吃瓜R]": {"unicode": "🍉", "name": "eating_melon", "pinyin": "chigua"},
    "[萌萌哒R]": {"unicode": "🥺", "name": "cute", "pinyin": "mengmengda"},
    "[合十R]": {"unicode": "🙏", "name": "folded_hands", "pinyin": "heshi"},
}

def replace_emojis_with_unicode(text: str) -> str:
    """Replace [xxR] bracketed Red Note emojis with their unicode equivalents."""
    if not text:
        return ""
    result = text
    for tag, meta in REDNOTE_EMOJI_MAP.items():
        if tag in result:
            result = result.replace(tag, meta["unicode"])
    return result

def get_emoji_meta(tag: str) -> Optional[Dict[str, str]]:
    """Retrieve metadata for a specific Red Note emoji tag."""
    return REDNOTE_EMOJI_MAP.get(tag)
