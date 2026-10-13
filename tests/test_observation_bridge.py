import tempfile
import unittest
from pathlib import Path
from observation_bridge import accept_observation, development_pack

class BridgeTests(unittest.TestCase):
    def row(self):
        return {'schema':'noir.browser-observation/1','sourceUrl':'https://www.rednote.com/discovery/item/abc?xsec_token=secret','title':'soft tissue','coverage':'profile-card','observedAt':'2026-10-03T00:00:00Z'}
    def test_redaction_and_deduplication(self):
        with tempfile.TemporaryDirectory() as root:
            a=accept_observation(self.row(),root)
            b=accept_observation(self.row(),root)
            self.assertEqual(a,b)
            self.assertNotIn('secret',str(a))
            self.assertEqual(len(list(Path(root).glob('*.json'))),1)
            self.assertFalse(development_pack([a])['runtimeAuthority'])
    def test_bad_source_and_coverage(self):
        with tempfile.TemporaryDirectory() as root:
            for change in [{'sourceUrl':'https://evil.example/a'},{'coverage':'all-watched'}]:
                with self.assertRaises(ValueError): accept_observation(self.row()|change,root)
