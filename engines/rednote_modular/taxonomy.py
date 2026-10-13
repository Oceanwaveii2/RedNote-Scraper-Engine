"""Red Note Taxonomy System.

Defines the 11 functional classes, subtypes, and character associations
required by the Codex Implementation Directive.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Optional

# The 11 Core Functional Classes specified on page 6 of the Directive
CLASSES = [
    "Character",
    "Object",
    "Environment",
    "Costume",
    "Behavior",
    "Transformation",
    "Reference",
    "Dialogue",
    "Meme",
    "Social Interaction",
    "Visual Style",
]

# Standard Types per Class
CLASS_TYPES: Dict[str, List[str]] = {
    "Character": ["Protagonist", "Antagonist", "Supporting", "Mascot", "Background Extra", "Archetype"],
    "Object": ["Prop", "Handheld Accessory", "Vehicle", "Relic", "Food/Drink", "Container", "Tool"],
    "Environment": ["Convent/Sanctuary", "Apartment/Room", "Urban Street", "Salon/Barbershop", "Postal Station", "Stage"],
    "Costume": ["Veil/Habit", "Leather Gloves", "Boots/Footwear", "Gown/Robe", "Accessory/Jewelry", "Sportswear"],
    "Behavior": ["Posing/Stance", "Banter/Teasing", "Grooming", "Combat/Defense", "Gait/Walking", "Reaction Take"],
    "Transformation": ["Cartoon Metamorphosis", "Inflation/Scale", "Gait Shift", "Costume Alteration", "Morphological Shift"],
    "Reference": ["Anime Archetype", "Cosplay Craft", "Figurine/Collectible", "Game Model", "Meme Origin"],
    "Dialogue": ["Catchphrase", "Banter", "Internal Monologue", "Crowd Reaction", "Audience Quip"],
    "Meme": ["Slapstick Trope", "Reaction Face", "Soundtrack Cadence", "Sight Gag", "Pop Culture Echo"],
    "Social Interaction": ["Audience Banter", "Peer Teasing", "Clerk Negotiation", "Compliment Exchange", "Disagreement"],
    "Visual Style": ["Dark Gold/Nun Gothic", "Cartoon Slapstick", "Pastel Pop", "Cinematic Film Still", "High-Contrast Noir"]
}

# Standard Character Associations specified on page 7 of the Directive
CHARACTER_ASSOCIATIONS = [
    "Rainee",
    "Transition Rainee",
    "Shin-chan Rainee",
    "Super Butt",
    "Rainee Horse",
    "Horse Rainee",
    "Supporting Character",
    "Unassigned",
]

# Topic Grouping Categories (Page 8)
TOPIC_CATEGORIES = [
    "Character",
    "Transformation",
    "Outfit",
    "Humor",
    "Body language",
    "Daily activity",
    "Object",
    "Location",
    "Relationship",
    "Meme",
    "Narrative premise",
    "Behavior",
    "Social reaction",
]

@dataclass
class ClassificationRecord:
    primary_class: str
    subtype: str
    character_association: str
    suggested_topics: List[str] = field(default_factory=list)
    confidence: float = 1.0
    user_approved: bool = False
    notes: str = ""

    def validate(self) -> bool:
        if self.primary_class not in CLASSES:
            return False
        return True
