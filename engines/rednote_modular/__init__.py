"""Red Note / Xiaohongshu Sub-App & Content Ingestion Engine.

Modular, structured social-media research environment and Scatter Game Engine
ingestion pipeline following the Codex Implementation Directive.
"""
from .taxonomy import CLASSES, CLASS_TYPES, CHARACTER_ASSOCIATIONS, TOPIC_CATEGORIES, ClassificationRecord
from .emoji_cache import REDNOTE_EMOJI_MAP, replace_emojis_with_unicode, get_emoji_meta
from .translation_engine import TranslationEngine, default_translator
from .media_manager import WorkspaceManager
from .database import RedNoteDatabase
from .parser import RedNoteParser
from .crawler import RedNoteCrawler
from .topic_grouper import analyze_topics, suggest_class_and_character
from .ingestion_graph import IngestionGraph, GraphNode, GraphEdge
from .scatter_bridge import ScatterBridgeManager, ScatterBridgePayload
from .profile_reconstructor import ProfileReconstructor

__all__ = [
    "CLASSES",
    "CLASS_TYPES",
    "CHARACTER_ASSOCIATIONS",
    "TOPIC_CATEGORIES",
    "ClassificationRecord",
    "REDNOTE_EMOJI_MAP",
    "replace_emojis_with_unicode",
    "get_emoji_meta",
    "TranslationEngine",
    "default_translator",
    "WorkspaceManager",
    "RedNoteDatabase",
    "RedNoteParser",
    "RedNoteCrawler",
    "analyze_topics",
    "suggest_class_and_character",
    "IngestionGraph",
    "GraphNode",
    "GraphEdge",
    "ScatterBridgeManager",
    "ScatterBridgePayload",
    "ProfileReconstructor",
]
