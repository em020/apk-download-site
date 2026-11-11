import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import artifact_delivery as delivery
import publishing


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'App.apk'
        self.source.write_bytes(b'example signed APK')
        self.sha = delivery.digest(self.source)
        self.size = self.source.stat().st_size
        self.key = 'apps/reader/releases/v2-123456789abc/App.apk'
        self.config = {'schemaVersion': 1, 'mode': 'external', 'publicBaseUrl': 'https://cdn.example.test/files/'}

    def test_rejects_unsafe_config_keys_and_object_paths(self):
        config = self.root / 'delivery.json'
        self.assertEqual(delivery.load_config(config)['mode'], 'local')
        for base in ('http://cdn.example.test/', 'https://key:secret@cdn.test/', 'https://cdn.test/?token=secret',
                     'https://cdn.test/no-slash'):
            config.write_text(json.dumps(dict(self.config, publicBaseUrl=base)))
            with self.assertRaises(ValueError):
                delivery.load_config(config)
        for key in ('../x', '/absolute', 'a//b', 'a/./b', 'a/../b', 'a/%2e%2e/b', 'a/\\b', 'a/\0'):
            with self.assertRaises(ValueError):
                delivery.deliver(self.config, self.source, key, self.sha, self.size)

    def test_preuploaded_immutable_object_is_verified_then_cached(self):
        with patch.object(delivery, 'verify_remote', return_value=True) as verify:
            first = delivery.deliver(self.config, self.source, self.key, self.sha, self.size, cache=self.root/'cache')
            second = delivery.deliver(self.config, self.source, self.key, self.sha, self.size, cache=self.root/'cache')
        self.assertEqual(first, second)
        verify.assert_called_once_with('https://cdn.example.test/files/' + self.key, self.sha, self.size)
        self.assertEqual(first['downloadUrl'], 'https://cdn.example.test/files/' + self.key)
        self.assertIsNone(first['expiresAt'])

    def test_real_publisher_hook_receives_arguments_and_failure_never_activates(self):
        objects = self.root / 'objects'
        hook = self.root / 'upload.py'
        hook.write_text('import sys, pathlib, shutil\n'
                        'root, source, key, sha, size = sys.argv[1:]\n'
                        'target=pathlib.Path(root)/key;target.parent.mkdir(parents=True,exist_ok=True)\n'
                        'assert not target.exists();shutil.copyfile(source,target)\n')
        config = dict(self.config, publishCommand=[sys.executable, str(hook), str(objects)])

        def verify(url, sha, size):
            target = objects / self.key
            if not target.exists():
                return False
            self.assertEqual(delivery.digest(target), sha)
            self.assertEqual(target.stat().st_size, size)
            return True
        with patch.object(delivery, 'verify_remote', side_effect=verify):
            result = delivery.deliver(config, self.source, self.key, self.sha, self.size)
        self.assertTrue(result['downloadUrl'].startswith('https://'))
        self.assertEqual((objects / self.key).read_bytes(), self.source.read_bytes())
        bad = dict(self.config, publishCommand=[sys.executable, '-c', 'raise SystemExit(2)'])
        with patch.object(delivery, 'verify_remote', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'catalog was not activated'):
                delivery.deliver(bad, self.source, self.key, self.sha, self.size)

    def test_remote_body_must_match_length_and_full_sha(self):
        class Response(io.BytesIO):
            status = 200
            headers = {'Content-Length': str(self.size)}
        for body in (self.source.read_bytes(), b'x' * self.size, b'x' * (self.size-1)):
            with patch.object(delivery, 'build_opener') as opener:
                opener.return_value.open.return_value = Response(body)
                if body == self.source.read_bytes():
                    self.assertTrue(delivery.verify_remote('https://cdn.test/App.apk', self.sha, self.size))
                else:
                    with self.assertRaisesRegex(ValueError, 'checksum'):
                        delivery.verify_remote('https://cdn.test/App.apk', self.sha, self.size)

    def test_existing_external_mismatch_is_never_overwritten(self):
        with patch.object(delivery, 'verify_remote', side_effect=ValueError('checksum mismatch')), \
                patch.object(delivery.subprocess, 'run') as publish:
            with self.assertRaisesRegex(ValueError, 'checksum'):
                delivery.deliver(dict(self.config, publishCommand=['upload']), self.source, self.key, self.sha, self.size)
            publish.assert_not_called()

    def test_local_and_external_catalog_share_identity_and_keep_legacy_files(self):
        state = self.root / 'state'
        directory = state / 'app-releases/reader/v2-123456789abc'
        directory.mkdir(parents=True)
        (directory / 'App.apk').write_bytes(self.source.read_bytes())
        release = dict(id='v2-123456789abc', versionName='1.0', versionCode=2, notes='New',
                       publishedAt='2026-11-11T00:00:00Z', bytes=self.size, sha256=self.sha)
        (directory/'release.json').write_text(json.dumps(release))
        (directory/'SHA256SUMS.txt').write_text(self.sha+'  App.apk\n')
        apps = [dict(slug='reader', name='Reader', description='Read', releases=[release])]
        registry = {'defaultApp': 'reader', 'apps': {'reader': {}}}
        publishing.activate_site(state, registry, apps)
        current = state/'current'
        local = json.loads((current/'apps/reader/latest/download.json').read_text())
        self.assertEqual(local['downloadUrl'], 'App.apk')
        catalog = json.loads((current/'apps/reader/releases.json').read_text())
        self.assertEqual(catalog[0]['downloadInfoUrl'], 'releases/v2-123456789abc/download.json')
        before = current.resolve()
        (state/'delivery.json').write_text(json.dumps(self.config))
        with patch.object(delivery, 'verify_remote', side_effect=RuntimeError('upload unavailable')):
            with self.assertRaises(RuntimeError):
                publishing.activate_site(state, registry, apps)
        self.assertEqual(current.resolve(), before)
        with patch.object(delivery, 'verify_remote', return_value=True):
            publishing.activate_site(state, registry, apps)
        remote = json.loads((current/'apps/reader/latest/download.json').read_text())
        self.assertEqual({k:v for k,v in local.items() if k!='downloadUrl'},
                         {k:v for k,v in remote.items() if k!='downloadUrl'})
        self.assertEqual(remote['downloadUrl'], 'https://cdn.example.test/files/'+self.key)
        page = (current/'apps/reader/index.html').read_text()
        self.assertIn('href="'+remote['downloadUrl']+'"', page)
        self.assertEqual((current/'apps/reader/latest/App.apk').read_bytes(), self.source.read_bytes())
        self.assertFalse((current/'delivery.json').exists())
        self.assertNotIn('publishCommand', (current/'apps.json').read_text())

    def test_checker_accepts_only_https_external_page_links(self):
        spec = importlib.util.spec_from_file_location('verify_downloads', Path(__file__).parents[1]/'verify-downloads.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for link in ('releases/v1/App.apk', 'https://cdn.example.test/App.apk'):
            module.validate_page_link(link)
        for link in ('http://cdn.test/App.apk', '//cdn.test/App.apk', '/absolute', 'https://secret@cdn.test/App.apk'):
            with self.assertRaises(AssertionError):
                module.validate_page_link(link)
