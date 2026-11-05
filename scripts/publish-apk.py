#!/usr/bin/env python3
"""Manage app identities and import versioned APK publication bundles."""
import argparse
import json
from pathlib import Path
import subprocess

from publishing import (DEFAULT_SDK, DEFAULT_STATE, app_info, inspect_apk, migrate_legacy,
                        publish_bundle, refresh_site, register_app)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--bundle', type=Path, help='Directory with manifest.json, App.apk and icon.svg')
    action.add_argument('--register', metavar='APP_ID', help='Register an app identity as the site operator')
    action.add_argument('--app-info', metavar='APP_ID', help='Read a registered identity and latest version code')
    action.add_argument('--inspect', type=Path, metavar='APK', help='Verify an APK against its registered identity')
    action.add_argument('--migrate-legacy', action='store_true', help='Import the active pre-v1 download site')
    action.add_argument('--refresh-site', action='store_true', help='Render the active catalog again')
    parser.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    parser.add_argument('--sdk', type=Path, default=DEFAULT_SDK)
    parser.add_argument('--package')
    parser.add_argument('--certificate', help='Registered signing certificate SHA-256')
    parser.add_argument('--allow-permission', action='append', help='Allowed Android permission, repeat as needed')
    parser.add_argument('--app-id', help='App ID for APK inspection or legacy migration')
    parser.add_argument('--legacy-apk-name', help='APK filename used by the legacy root URLs')
    parser.add_argument('--name', help='Display name for the legacy site root')
    parser.add_argument('--description', help='Description for the legacy site root')
    args = parser.parse_args()
    state = args.state_dir.expanduser().resolve()
    sdk = args.sdk.expanduser().resolve()
    try:
        if args.bundle is not None:
            result = publish_bundle(args.bundle.expanduser().resolve(), state, sdk)
        elif args.app_info:
            result = app_info(state, args.app_info)
        elif args.inspect is not None:
            if not args.app_id:
                parser.error('--inspect requires --app-id')
            identity = app_info(state, args.app_id)
            result = inspect_apk(args.inspect.expanduser().resolve(), sdk,
                                 identity['certificateSha256'], identity['applicationId'])
        elif args.register:
            if not args.package or not args.certificate:
                parser.error('--register requires --package and --certificate')
            result = register_app(state, args.register, args.package, args.certificate, args.allow_permission)
        elif args.migrate_legacy:
            if not all((args.app_id, args.legacy_apk_name, args.name, args.description)):
                parser.error('--migrate-legacy requires --app-id, --legacy-apk-name, --name and --description')
            result = migrate_legacy(state, args.app_id, args.legacy_apk_name, args.name, args.description, sdk)
        else:
            refresh_site(state)
            result = {'status': 'refreshed'}
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Publish operation failed: {error}\n')


if __name__ == '__main__':
    main()
