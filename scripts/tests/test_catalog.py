import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import publishing


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)

    def metadata(self, identifier):
        return dict(id=identifier, versionName="0.1.0-debug", versionCode=2,
                    notes='<script>alert("notes")</script>', publishedAt="2026-10-28T00:00:00Z",
                    bytes=100, sha256="a" * 64, commit="1" * 40)

    def test_page_escapes_notes_and_uses_prefix_portable_immutable_links(self):
        template = (publishing.REPO / "distribution/index.html").read_text()
        page = publishing.render_page([self.metadata("v2")], template, apk_name="App.apk",
                                      navigation="", app_name="Test", description="Test", icon=None)
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn('href="releases/v2/App.apk"', page)
        self.assertNotIn('href="/', page)
        self.assertNotIn("{{", page)

    def add_release(self, slug, release_id, apk=b"app"):
        artifact = self.state / "app-releases" / slug / release_id
        artifact.mkdir(parents=True)
        (artifact / "App.apk").write_bytes(apk)
        metadata = self.metadata(release_id)
        (artifact / "release.json").write_text(json.dumps(metadata))
        return metadata

    def test_catalog_activation_keeps_other_apps_and_legacy_links(self):
        default_id = "reader"
        legacy_name = "Reader.apk"
        registry = {
            "schemaVersion": 1,
            "defaultApp": default_id,
            "rootAlias": {"appId": default_id, "apkName": legacy_name},
            "apps": {default_id: {}, "example": {}},
        }
        v1 = self.add_release(default_id, "v1", apk=b"old")
        v2 = self.add_release(default_id, "v2", apk=b"new")
        q1 = self.add_release("example", "q1")
        default = dict(slug=default_id, name="Reader", description="Read", releases=[v1])
        other = dict(slug="example", name="Example", description="选择并复制文本 <安全>", releases=[q1])
        publishing.activate_site(self.state, registry, [default, other])
        current = self.state / "current"
        before = current.resolve()
        page = (current / "apps/example/index.html").read_text()
        self.assertIn('&lt;安全&gt;', page)
        self.assertIn('href="../../apps/example/" aria-current="page">Example</a>', page)
        self.assertIn('href="../../apps/reader/">Reader</a>', page)
        self.assertNotIn('{{', page)

        updated = dict(default, releases=[v2, v1])
        with patch.object(publishing.os, "replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                publishing.activate_site(self.state, registry, [updated, other])
        self.assertEqual(current.resolve(), before)
        self.assertEqual((current / f"latest/{legacy_name}").read_bytes(), b"old")
        publishing.activate_site(self.state, registry, [updated, other])
        self.assertEqual(publishing.read_apps(self.state), [updated, other])
        self.assertEqual((current / f"latest/{legacy_name}").read_bytes(), b"new")
        self.assertEqual((current / f"releases/v1/{legacy_name}").read_bytes(), b"old")
        self.assertEqual((current / "apps/reader/latest/App.apk").read_bytes(), b"new")
        self.assertEqual((current / "apps/example/latest/App.apk").read_bytes(), b"app")
