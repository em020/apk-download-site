import contextlib
import importlib.util
import io
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile


SCRIPT = Path(__file__).parents[1] / "publish-ssh.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("publish_ssh", SCRIPT)
transport = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport)


def zipped(entries, *, comment=b""):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries:
            archive.writestr(name, body)
        archive.comment = comment
    stream.seek(0)
    return stream


class SshPublishTests(unittest.TestCase):
    entries = [("App.apk", b"signed artifact"), ("icon.svg", b"static icon"),
               ("manifest.json", b'{"schemaVersion":1,"appId":"other-app"}')]

    def test_forced_command_preserves_operator_app_scope(self):
        output = io.StringIO()

        def publish(bundle, state, sdk, *, allowed_app):
            self.assertEqual(allowed_app, "example-app")
            self.assertEqual(state, Path("/private/example"))
            self.assertEqual({file.name: file.read_bytes() for file in bundle.iterdir()}, dict(self.entries))
            return {"status": "published", "appId": allowed_app}

        with (patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": "publish-v1"}),
              patch.object(sys, "argv", [str(SCRIPT), "--app-id", "example-app", "--state-dir", "/private/example"]),
              patch.object(sys, "stdin", SimpleNamespace(buffer=zipped(self.entries))),
              patch.object(transport, "publish_bundle", side_effect=publish),
              contextlib.redirect_stdout(output)):
            transport.main()
        self.assertEqual(output.getvalue(), '{"status": "published", "appId": "example-app"}\n')

    def test_unexpected_command_is_rejected_before_reading(self):
        with (patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": "publish-v1; ls"}),
              patch.object(sys, "argv", [str(SCRIPT), "--app-id", "example-app"]),
              patch.object(transport, "receive_bundle") as receive,
              contextlib.redirect_stderr(io.StringIO())):
            with self.assertRaises(SystemExit) as error:
                transport.main()
            self.assertEqual(error.exception.code, 1)
            receive.assert_not_called()

    def test_zip_validation_rejects_unsafe_bundle_before_extraction(self):
        symlink = zipfile.ZipInfo("App.apk")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        oversized_manifest = b"x" * (transport.MAX_MANIFEST_BYTES + 1)
        cases = {
            "path traversal": zipped([("../App.apk", b"x"), *self.entries[1:]]),
            "symlink": zipped([(symlink, b"/private/file"), *self.entries[1:]]),
            "unexpected entry": zipped([*self.entries, ("other", b"x")]),
            "oversized member": zipped([*self.entries[:2], ("manifest.json", oversized_manifest)]),
            "archive comment": zipped(self.entries, comment=b"surprise"),
        }
        for case, source in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as work:
                bundle = Path(work)
                with self.assertRaises(ValueError):
                    transport.receive_bundle(source, bundle)
                self.assertFalse(list(bundle.iterdir()))

    def test_upload_cap_is_enforced_before_opening_zip(self):
        with (tempfile.TemporaryDirectory() as work,
              patch.object(transport, "MAX_ZIP_BYTES", 4)):
            with self.assertRaisesRegex(ValueError, "upload size limit"):
                transport.receive_bundle(io.BytesIO(b"12345"), Path(work))
