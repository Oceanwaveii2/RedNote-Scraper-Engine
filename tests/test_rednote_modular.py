"""Unit tests for the modular Red Note sub-app ingestion engine."""
import unittest
import tempfile
from pathlib import Path

from rednote.taxonomy import CLASSES, CLASS_TYPES, CHARACTER_ASSOCIATIONS
from rednote.emoji_cache import replace_emojis_with_unicode, get_emoji_meta
from rednote.translation_engine import TranslationEngine
from rednote.media_manager import WorkspaceManager
from rednote.database import RedNoteDatabase
from rednote.parser import RedNoteParser
from rednote.crawler import RedNoteCrawler
from rednote.topic_grouper import analyze_topics, suggest_class_and_character
from rednote.ingestion_graph import IngestionGraph
from rednote.scatter_bridge import ScatterBridgeManager
from rednote.profile_reconstructor import ProfileReconstructor

class RedNoteModularTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp_dir.name)
        self.db = RedNoteDatabase(self.root / "test_noirdb.sqlite")
        self.wm = WorkspaceManager(self.root / "workspaces")
        self.crawler = RedNoteCrawler(self.wm, self.db)

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_taxonomy_definitions(self):
        self.assertEqual(len(CLASSES), 11)
        self.assertIn("Character", CLASSES)
        self.assertIn("Visual Style", CLASSES)
        self.assertIn("Rainee", CHARACTER_ASSOCIATIONS)
        self.assertIn("Transition Rainee", CHARACTER_ASSOCIATIONS)

    def test_emoji_replacement(self):
        raw = "太可爱了[害羞R]哈哈[笑哭R]"
        res = replace_emojis_with_unicode(raw)
        self.assertIn("😳", res)
        self.assertIn("😂", res)

    def test_translation_caching(self):
        t_file = self.root / "translations.json"
        engine = TranslationEngine(t_file)
        engine.put("你好", "Hello")
        self.assertEqual(engine.get("你好"), "Hello")
        # Ensure disk persistence
        engine2 = TranslationEngine(t_file)
        self.assertEqual(engine2.get("你好"), "Hello")

    def test_topic_grouper_and_suggestions(self):
        text = "全员暗黑御姐风！黑金修女刺杀队集结完毕，眼神杀拉满。"
        topics = analyze_topics(text, ["修女", "角色扮演"])
        self.assertTrue(len(topics) > 0)
        topic_names = [t["topic"] for t in topics]
        self.assertIn("Character", topic_names)
        
        class_char = suggest_class_and_character(text, ["修女"])
        self.assertEqual(class_char["suggestedCharacter"], "Rainee")

    def test_workspace_isolation(self):
        p_dir = self.wm.get_profile_workspace("test_profile_01")
        self.assertTrue((p_dir / "posts").exists())
        self.assertTrue((p_dir / "images").exists())
        self.assertTrue((p_dir / "scatter_assets").exists())

    def test_url_detection(self):
        res1 = RedNoteCrawler.detect_url_type("https://www.rednote.com/user/profile/62370bd2000000000102b6b4")
        self.assertEqual(res1["type"], "profile")
        self.assertEqual(res1["id"], "62370bd2000000000102b6b4")

        res2 = RedNoteCrawler.detect_url_type("https://www.rednote.com/discovery/item/6ab090fb0000000011031bfd")
        self.assertEqual(res2["type"], "post")
        self.assertEqual(res2["id"], "6ab090fb0000000011031bfd")

        res3 = RedNoteCrawler.detect_url_type("rednote://profile/ling_zi_123")
        self.assertEqual(res3["type"], "local_profile")
        self.assertEqual(res3["id"], "ling_zi_123")

    def test_ingestion_graph_and_scatter_bridge(self):
        post_payload = {
            "postId": "p_001",
            "profileId": "prof_001",
            "title": "今日角色：修女刺杀队",
            "description": "黑金修女刺杀队集结完毕",
            "mediaFiles": ["nun_1.jpg", "nun_2.jpg"],
            "likes": 1840,
            "comments": [{"text": "这套太绝了", "likes": 40}]
        }
        parsed = self.crawler.ingest_structured_post(post_payload, "prof_001")
        card = parsed["assetCard"]

        # Build Ingestion Graph
        graph = IngestionGraph()
        graph.build_post_pipeline(parsed, card, "scatter_node_nun_a")
        graph_dict = graph.to_dict()
        self.assertEqual(len(graph_dict["nodes"]), 7)
        self.assertEqual(len(graph_dict["edges"]), 6)

        # Build Scatter Bridge Package
        bridge = ScatterBridgeManager.create_bridge_package(
            card=card,
            parsed_post=parsed,
            target_scatter_node_id="scatter_node_nun_a"
        )
        rete_node = bridge.to_rete_node()
        self.assertEqual(rete_node["id"], "scatter_node_nun_a")
        self.assertIn("originalSourceEvidence", rete_node["data"]["layers"])
        self.assertIn("scatterInterpretation", rete_node["data"]["layers"])

    def test_offline_profile_reconstruction(self):
        prof = {
            "profile_id": "test_ling_zi",
            "author_name": "ling_zi",
            "avatar_url": "/avatars/ling_zi.jpg",
            "bio": "暗黑御姐风 / 角色扮演",
            "following_count": 12,
            "followers_count": 45000,
            "likes_count": 180000
        }
        posts = [
            {"title": "修女刺杀队", "coverImage": "/img/1.jpg", "likes": 1840}
        ]
        target = self.root / "test_site" / "index.html"
        ProfileReconstructor.render_offline_html(prof, posts, target)
        self.assertTrue(target.exists())
        content = target.read_text(encoding="utf-8")
        self.assertIn("ling_zi", content)
        self.assertIn("OFFLINE RECONSTRUCTION", content)

if __name__ == "__main__":
    unittest.main()
