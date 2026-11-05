import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import publishing


class ImportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state = self.root / 'state'
        self.bundle = self.root / 'bundle'
        self.bundle.mkdir()
        self.manifest = {'schemaVersion': 1, 'appId': 'third-app', 'name': 'Third App',
                         'description': 'Sample text <tag>', 'notes': 'First release'}
        self.icon = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0H1V1Z"/></svg>'
        self.package = 'dev.example.third'
        self.certificate = 'a' * 64
        self.make_bundle(1)

    def make_bundle(self, version):
        (self.bundle / 'manifest.json').write_text(json.dumps(self.manifest))
        (self.bundle / 'icon.svg').write_bytes(self.icon)
        (self.bundle / 'App.apk').write_bytes(str(version).encode())

    def inspect(self, apk, sdk, certificate, package):
        self.assertEqual((certificate, package), (self.certificate, self.package))
        return dict(applicationId=package, certificateSha256=certificate,
                    versionCode=int(apk.read_bytes()), versionName='1.0',
                    sha256=publishing.digest(apk), bytes=apk.stat().st_size,
                    permissions=['android.permission.INTERNET'])

    def publish(self, allowed_app=None):
        return publishing.publish_bundle(self.bundle, self.state, self.root, allowed_app=allowed_app)

    def register(self):
        return publishing.register_app(self.state, 'third-app', self.package, self.certificate,
                                       ['android.permission.INTERNET'])

    def test_first_app_uses_same_catalog_and_protocol_as_update(self):
        with patch.object(publishing, 'inspect_apk', side_effect=self.inspect):
            with self.assertRaisesRegex(ValueError, 'not registered'):
                self.publish()
            self.register()
            self.assertEqual(publishing.app_info(self.state, 'third-app'), {
                'appId': 'third-app', 'applicationId': self.package,
                'certificateSha256': self.certificate, 'latestVersionCode': 0,
            })
            with self.assertRaisesRegex(ValueError, 'not authorized'):
                self.publish('different')
            first = self.publish('third-app')
            self.assertEqual(first['status'], 'published')
            current = self.state / 'current'
            initial_site = current.resolve()
            self.assertEqual(self.publish()['status'], 'unchanged')
            self.assertEqual(current.resolve(), initial_site)
            self.manifest['notes'] = 'Different notes'
            self.make_bundle(1)
            with self.assertRaisesRegex(ValueError, 'versionCode'):
                self.publish()
            self.icon = self.icon.replace(b'H1V1', b'H2V2')
            self.make_bundle(2)
            second = self.publish()
            self.assertEqual(publishing.app_info(self.state, 'third-app')['latestVersionCode'], 2)
            self.assertEqual(second['downloadPath'], 'apps/third-app/releases/' + second['releaseId'] + '/App.apk')
            self.assertEqual([release['versionCode'] for release in publishing.read_apps(self.state)[0]['releases']], [2, 1])
            publishing.refresh_site(self.state)
            self.assertEqual((current / 'apps/third-app/icon.svg').read_bytes(), self.icon)
            self.assertEqual((current / 'latest/App.apk').read_bytes(), b'2')
            self.assertEqual((current / first['downloadPath']).read_bytes(), b'1')
            self.make_bundle(1)
            with self.assertRaisesRegex(ValueError, 'versionCode'):
                self.publish()

    def test_reject_invalid_svg_and_permissions_without_activating(self):
        self.register()
        icon = self.bundle / 'icon.svg'
        icon.write_text('<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>')
        with self.assertRaisesRegex(ValueError, 'static shapes'):
            self.publish()
        self.make_bundle(1)
        wrong_permissions = self.inspect(self.bundle / 'App.apk', self.root, self.certificate, self.package)
        wrong_permissions['permissions'] = ['android.permission.CAMERA']
        with patch.object(publishing, 'inspect_apk', return_value=wrong_permissions):
            with self.assertRaisesRegex(ValueError, 'permissions'):
                self.publish()
        self.assertFalse((self.state / 'current').exists())

    def test_failed_activation_can_retry_same_bundle(self):
        self.register()
        with patch.object(publishing, 'inspect_apk', side_effect=self.inspect):
            first = self.publish()
            self.make_bundle(2)
            with patch.object(publishing.os, 'replace', side_effect=OSError('simulated failure')):
                with self.assertRaises(OSError):
                    self.publish()
            self.assertEqual(publishing.read_apps(self.state)[0]['releases'][0]['id'], first['releaseId'])
            self.assertEqual(self.publish()['versionCode'], 2)

    def test_legacy_migration_preserves_ids_apks_and_root_links(self):
        old_apk = b'legacy-root-apk'
        current = self.state / 'current'
        (current / 'releases/legacy-1').mkdir(parents=True)
        (current / 'releases/legacy-1/Legacy.apk').write_bytes(old_apk)
        (current / 'icon.svg').write_bytes(self.icon)
        (current / 'index.html').write_text('<!doctype html>')
        release = dict(id='legacy-1', versionCode=1, versionName='1.0',
                       applicationId='dev.example.legacy', certificateSha256='b' * 64,
                       sha256=hashlib.sha256(old_apk).hexdigest(), bytes=len(old_apk),
                       notes='Old', publishedAt='2026-01-01T00:00:00Z')
        (current / 'releases.json').write_text(json.dumps([release]))
        archived = self.state / 'legacy-site'
        current.rename(archived)
        current.symlink_to(archived, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'migration'):
            self.register()
        with patch.object(publishing, 'inspect_apk', return_value={'permissions': []}):
            result = publishing.migrate_legacy(self.state, 'legacy', 'Legacy.apk', 'Legacy', 'Old app')
        self.assertEqual(publishing.read_registry(self.state)['apps']['legacy']['allowedPermissions'], [])
        self.assertEqual(result['appIds'], ['legacy'])
        self.assertEqual(publishing.read_apps(self.state)[0]['releases'], [release])
        self.assertEqual((current / 'latest/Legacy.apk').read_bytes(), old_apk)
        self.assertEqual((current / 'apps/legacy/latest/App.apk').read_bytes(), old_apk)
        self.assertEqual((current / 'apps/legacy/icon.svg').read_bytes(), self.icon)
        self.assertEqual((archived / 'releases/legacy-1/Legacy.apk').read_bytes(), old_apk)
        self.assertEqual(publishing.migrate_legacy(self.state, 'legacy', 'Legacy.apk', 'Legacy', 'Old app')['status'], 'unchanged')
