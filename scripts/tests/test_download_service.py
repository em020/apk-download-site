import shutil
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parents[1]))
from download_service import nginx_config
import download_service as service


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


class ServiceInstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='apk-service-install-')
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name)
        (self.state/'current').mkdir()
        (self.state/'current/index.html').write_text('<!doctype html>')
        self.runtime = self.state/'service'
        self.runtime.mkdir()
        self.config = self.runtime/'nginx.conf'
        self.old_config = nginx_config(self.state, 8098) + '\n# previous routes\n'
        self.config.write_text(self.old_config)
        self.plist = self.state/'launchagent.plist'
        self.nginx = str(Path('/test/bin/nginx').resolve())
        self.definition = service.launch_definition(self.nginx, self.runtime, self.config)
        self.plist.write_bytes(plistlib.dumps(self.definition))

    def test_same_instance_reloads_without_bootout_or_bootstrap(self):
        before = self.plist.read_bytes()
        with patch.object(service, 'PLIST', self.plist), \
                patch.object(service.shutil, 'which', return_value=self.nginx), \
                patch.object(service, 'service_loaded', return_value=True), \
                patch.object(service, 'check_port_available') as probe, \
                patch.object(service.subprocess, 'run') as run:
            service.install(self.state, 8098)
        self.assertEqual(len(run.call_args_list), 2)
        self.assertEqual(run.call_args_list[0].args[0][-1], '-t')
        self.assertEqual(run.call_args_list[1].args[0][-2:], ['-s', 'reload'])
        probe.assert_not_called()
        self.assertEqual(self.plist.read_bytes(), before)
        self.assertEqual(self.config.read_text(), nginx_config(self.state, 8098))

    def test_invalid_candidate_keeps_running_configuration_and_plist(self):
        before = self.plist.read_bytes()
        with patch.object(service, 'PLIST', self.plist), \
                patch.object(service.shutil, 'which', return_value=self.nginx), \
                patch.object(service, 'service_loaded') as loaded, \
                patch.object(service.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['nginx', '-t'])):
            with self.assertRaises(subprocess.CalledProcessError):
                service.install(self.state, 8098)
        loaded.assert_not_called()
        self.assertEqual(self.config.read_text(), self.old_config)
        self.assertEqual(self.plist.read_bytes(), before)

    def test_failed_reload_restores_old_disk_configuration(self):
        with patch.object(service, 'PLIST', self.plist), \
                patch.object(service.shutil, 'which', return_value=self.nginx), \
                patch.object(service, 'service_loaded', return_value=True), \
                patch.object(service.subprocess, 'run', side_effect=[None, subprocess.CalledProcessError(1, ['nginx', '-s', 'reload'])]):
            with self.assertRaises(subprocess.CalledProcessError):
                service.install(self.state, 8098)
        self.assertEqual(self.config.read_text(), self.old_config)

    def test_bootstrap_retries_transient_launchd_exit_5(self):
        failed = subprocess.CalledProcessError(5, ['launchctl', 'bootstrap'], stderr='Bootstrap failed: 5')
        with patch.object(service.subprocess, 'run', side_effect=[failed, None]) as run, \
                patch.object(service.time, 'sleep') as sleep:
            service.bootstrap()
        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once_with(0.2)

    def test_persistent_bootstrap_errors_are_bounded_and_reported(self):
        for code, attempts in ((5, 5), (78, 1)):
            with self.subTest(code=code), \
                    patch.object(service.subprocess, 'run', side_effect=subprocess.CalledProcessError(code, ['launchctl'], stderr='configuration rejected')) as run, \
                    patch.object(service.time, 'sleep') as sleep:
                with self.assertRaisesRegex(RuntimeError, 'configuration rejected'):
                    service.bootstrap()
                self.assertEqual(run.call_count, attempts)
                self.assertEqual(sleep.call_count, attempts-1)

    def test_real_reconfiguration_failure_restores_previous_service_definition(self):
        old_definition = dict(self.definition, ProgramArguments=['/old/nginx', '-p', str(self.runtime)+'/', '-c', str(self.config)])
        self.plist.write_bytes(plistlib.dumps(old_definition))
        with patch.object(service, 'PLIST', self.plist), \
                patch.object(service.shutil, 'which', return_value=self.nginx), \
                patch.object(service, 'service_loaded', return_value=True), \
                patch.object(service.subprocess, 'run') as run, \
                patch.object(service, 'bootstrap', side_effect=[RuntimeError('persistent launchd error'), None]) as bootstrap:
            with self.assertRaisesRegex(RuntimeError, 'previous service configuration was restored'):
                service.install(self.state, 8098)
        self.assertEqual(bootstrap.call_count, 2)
        self.assertEqual(plistlib.loads(self.plist.read_bytes()), old_definition)
        self.assertEqual(self.config.read_text(), self.old_config)
        self.assertEqual(run.call_args_list[-1].args[0][:2], ['launchctl', 'bootout'])
