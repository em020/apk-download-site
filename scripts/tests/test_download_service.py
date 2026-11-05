import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).parents[1]))
from download_service import nginx_config


class NginxConfigTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('nginx'), 'Nginx is not installed')
    def test_generated_multi_app_routes_pass_real_nginx_config_check(self):
        with tempfile.TemporaryDirectory(prefix='apk-download-config-') as directory:
            state = Path(directory)
            (state / 'service').mkdir()
            (state / 'current').mkdir()
            config = state / 'service/nginx.conf'
            config.write_text(nginx_config(state, 8098))
            result = subprocess.run([shutil.which('nginx'), '-p', str(state / 'service') + '/',
                                     '-c', str(config), '-t'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
