"""Versioned Android APK publishing protocol and catalog activation."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid
from xml.etree import ElementTree

REPO = Path(__file__).resolve().parents[1]
DEFAULT_STATE = Path.home() / 'Library/Application Support/ShadowingGems/distribution'
DEFAULT_SDK = Path.home() / 'Library/Android/sdk'
MAX_APK_BYTES = 500 * 1024 * 1024
MAX_ICON_BYTES = 256 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
BUNDLE_FILES = frozenset(('App.apk', 'icon.svg', 'manifest.json'))
SLUG = re.compile(r'[a-z][a-z0-9-]{0,39}')
SHA256 = re.compile(r'[a-f0-9]{64}')
PACKAGE = re.compile(r'[A-Za-z][A-Za-z0-9_]*(?:[.][A-Za-z][A-Za-z0-9_]*)+')
APK_NAME = re.compile(r'[A-Za-z0-9_-]+[.]apk')
RELEASE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9.-]{0,127}')


def run(args, *, cwd=REPO, env=None):
    return subprocess.check_output(args, cwd=cwd, env=env, text=True).strip()

def atomic_text(path, text):
    path = Path(path)
    temp = path.with_name("." + path.name + "-" + uuid.uuid4().hex)
    try:
        with temp.open("x") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)

def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"

@contextmanager
def release_lock(state):
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / "release.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another release is running. Wait for it to finish.") from None
        yield


def inspect_apk(apk, sdk, certificate, package):
    tools = sdk / 'build-tools/35.0.0'
    signature = run([str(tools / 'apksigner'), 'verify', '--verbose', '--print-certs', str(apk)])
    digests = re.findall(r'Signer #\d+ certificate SHA-256 digest: ([0-9a-f]+)', signature)
    if digests != [certificate]:
        raise ValueError('APK signing certificate does not match the expected identity')
    badge = run([str(tools / 'aapt'), 'dump', 'badging', str(apk)])
    identity = re.search(r"package: name='([^']+)' versionCode='(\d+)' versionName='([^']+)'", badge)
    if not identity or identity[1] != package:
        raise ValueError('APK package does not match the expected app')
    return {'applicationId': package, 'versionCode': int(identity[2]), 'versionName': identity[3],
            'certificateSha256': certificate, 'sha256': digest(apk),
            'bytes': apk.stat().st_size, 'minSdk': int(re.search(r"sdkVersion:'(\d+)'", badge)[1]),
            'targetSdk': int(re.search(r"targetSdkVersion:'(\d+)'", badge)[1]),
            'permissions': re.findall(r"uses-permission: name='([^']+)'", badge),
            'channel': 'internal-debug' if 'application-debuggable' in badge.splitlines() else 'internal'}

def read_icon(path):
    with path.open('rb') as source:
        data = source.read(MAX_ICON_BYTES + 1)
    if len(data) > MAX_ICON_BYTES:
        raise ValueError('App icon SVG exceeds 256 KiB')
    svg = data.decode('utf-8')
    if '<!' in svg or '<?' in svg:
        raise ValueError('App icon SVG must not contain XML declarations or directives')
    try:
        root = ElementTree.fromstring(svg)
    except ElementTree.ParseError as error:
        raise ValueError('App icon is not valid SVG') from error
    namespace = '{http://www.w3.org/2000/svg}'
    elements = {namespace + name for name in ('svg', 'g', 'path', 'rect', 'circle', 'ellipse',
                                               'line', 'polygon', 'polyline', 'title', 'desc')}
    attributes = {'viewBox', 'width', 'height', 'd', 'fill', 'fill-rule', 'stroke', 'stroke-width',
                  'stroke-linecap', 'stroke-linejoin', 'transform', 'opacity', 'fill-opacity',
                  'stroke-opacity', 'x', 'y', 'x1', 'x2', 'y1', 'y2', 'rx', 'ry', 'cx', 'cy', 'r', 'points'}
    if root.tag != namespace + 'svg' or any(
            node.tag not in elements or not node.attrib.keys() <= attributes or
            any('url' in value.lower() for value in node.attrib.values()) for node in root.iter()):
        raise ValueError('App icon must be a self-contained SVG with static shapes')
    return data


def render_page(catalog, template, *, apk_name, navigation, app_name, description, icon, prefix=""):
    newest = catalog[0]
    escape = lambda value: html.escape(str(value), quote=True)
    entries = []
    for item in catalog:
        release_path = prefix + "releases/" + escape(item["id"]) + "/"
        entries.append(
            '<article class="release"><div><strong>' + escape(item["versionName"]) +
            '</strong><time>' + escape(item["publishedAt"]) + '</time></div><p>' +
            escape(item["notes"]).replace("\n", "<br>") + '</p><div class="links"><a href="' +
            release_path + apk_name + '">下载此版本</a><a href="' + release_path +
            'release.json">版本信息</a><a href="' + release_path +
            'SHA256SUMS.txt">SHA-256 校验</a></div><small>' +
            ('Commit ' if item.get('commit') else 'SHA-256 ') +
            escape(item.get("commit", item["sha256"])[:12]) + '</small></article>')
    values = {"APP_NAVIGATION": navigation,
              "APP_NAME": escape(app_name), "DESCRIPTION": escape(description),
              "APP_ICON": ('<img class="app-icon" src="' + escape(icon) + '" alt="" width="88" height="88">'
                           if icon else '<span class="app-icon icon-fallback" aria-hidden="true">' + escape(app_name[:1]) + '</span>'),
              "APK_NAME": escape(apk_name), "DISPLAY_VERSION": escape(newest["versionName"].split("-b")[0]),
              "SIZE": f'{newest["bytes"] / 1048576:.1f} MB',
              "DATE": escape(newest["publishedAt"][:10]),
              "RELEASES": "\n".join(entries),
              "SHA256": escape(newest["sha256"]),
              # Immutable link means an already-open page keeps its matching APK.
              "LATEST_APK": prefix + "releases/" + escape(newest["id"]) + "/" + apk_name}
    return re.sub(r"\{\{([A-Z_0-9]+)\}\}", lambda match: values[match[1]], template)


def digest(path):
    with path.open('rb') as handle:
        result = hashlib.sha256()
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            result.update(chunk)
        return result.hexdigest()


def validate_identity(app_id, record):
    if not SLUG.fullmatch(app_id):
        raise ValueError('Invalid app ID')
    if not PACKAGE.fullmatch(record['applicationId']):
        raise ValueError('Invalid registered Android package')
    if not SHA256.fullmatch(record['certificateSha256']):
        raise ValueError('Invalid registered certificate fingerprint')


def read_registry(state):
    path = state / 'registry.json'
    if not path.is_file():
        return {'schemaVersion': 1, 'defaultApp': None, 'apps': {}}
    registry = json.loads(path.read_text())
    if registry['schemaVersion'] != 1:
        raise ValueError('Unsupported registry schema version')
    for app_id, record in registry['apps'].items():
        validate_identity(app_id, record)
    default_app = registry['defaultApp']
    if default_app not in registry['apps']:
        raise ValueError('Default app is not registered')
    alias = registry.get('rootAlias')
    if alias and (alias['appId'] not in registry['apps'] or not APK_NAME.fullmatch(alias['apkName'])):
        raise ValueError('Invalid legacy download alias')
    return registry


def read_apps(state):
    path = state / 'current/apps.json'
    return json.loads(path.read_text()) if path.is_file() else []


def require_migrated(state):
    current = state / 'current'
    if (current / 'index.html').is_file() and not (current / '.protocol-v1').is_file():
        raise ValueError('Run the legacy catalog migration before publishing')


def app_info(state, app_id):
    require_migrated(state)
    record = read_registry(state)['apps'].get(app_id)
    if record is None:
        raise ValueError('App is not registered: ' + app_id)
    current = next((app for app in read_apps(state) if app['slug'] == app_id), None)
    return {'appId': app_id, 'applicationId': record['applicationId'],
            'certificateSha256': record['certificateSha256'],
            'latestVersionCode': current['releases'][0]['versionCode'] if current else 0}


def register_app(state, app_id, package, certificate, allowed_permissions=None):
    record = {'applicationId': package, 'certificateSha256': certificate}
    if allowed_permissions is not None:
        record['allowedPermissions'] = sorted(set(allowed_permissions))
    validate_identity(app_id, record)
    with release_lock(state):
        require_migrated(state)
        registry = read_registry(state)
        existing = registry['apps'].get(app_id)
        if existing and existing != record:
            raise ValueError('App ID is already registered with a different identity or permissions')
        if any(key != app_id and item['applicationId'] == package
               for key, item in registry['apps'].items()):
            raise ValueError('Android package is already registered to another app')
        registry['apps'][app_id] = record
        if not registry['defaultApp']:
            registry['defaultApp'] = app_id
        atomic_text(state / 'registry.json', json_text(registry))
    return {'status': 'registered', 'appId': app_id}


def navigation_links(apps, selected, prefix):
    return ''.join(
        '<a href="' + prefix + 'apps/' + app['slug'] + '/"'
        + (' aria-current="page"' if app['slug'] == selected else '') + '>'
        + html.escape(app['name']) + '</a>' for app in apps
    )


def activate_site(state, registry, apps):
    """Render the catalog and atomically switch the public tree."""
    template = (REPO / 'distribution/index.html').read_text()
    by_id = {app['slug']: app for app in apps}
    default = by_id.get(registry['defaultApp'], apps[0])
    sites = state / 'sites'
    sites.mkdir(exist_ok=True)
    site = sites / ('catalog-' + uuid.uuid4().hex)
    site.mkdir()
    (site / '.protocol-v1').touch()
    (site / 'apps.json').write_text(json_text(apps))

    for app in apps:
        slug = app['slug']
        if slug not in registry['apps']:
            raise ValueError('Catalog contains an unregistered app')
        directory = site / 'apps' / slug
        directory.mkdir(parents=True)
        artifacts = state / 'app-releases' / slug
        latest = artifacts / app['releases'][0]['id']
        (directory / 'latest').symlink_to(latest, target_is_directory=True)
        (directory / 'releases').symlink_to(artifacts, target_is_directory=True)
        (directory / 'releases.json').write_text(json_text(app['releases']))
        icon = latest / 'icon.svg'
        if not icon.is_file():
            icon = state / 'legacy-icons' / (slug + '.svg')
        if icon.is_file():
            shutil.copyfile(icon, directory / 'icon.svg')
        (directory / 'index.html').write_text(render_page(
            app['releases'], template, apk_name='App.apk',
            navigation=navigation_links(apps, slug, '../../'), app_name=app['name'],
            description=app['description'], icon='icon.svg' if icon.is_file() else None))

    # Legacy root URLs are a projection of one registered app, configured by the operator.
    alias = registry.get('rootAlias', {'appId': default['slug'], 'apkName': 'App.apk'})
    root_app = by_id.get(alias['appId'])
    if root_app:
        for release in root_app['releases']:
            release_id = release['id']
            artifacts = state / 'app-releases' / root_app['slug'] / release_id
            target = site / 'releases' / release_id
            target.mkdir(parents=True)
            (target / alias['apkName']).symlink_to(artifacts / 'App.apk')
            (target / 'release.json').symlink_to(artifacts / 'release.json')
            (target / 'SHA256SUMS.txt').write_text(release['sha256'] + '  ' + alias['apkName'] + '\n')
        (site / 'latest').symlink_to(Path('releases') / root_app['releases'][0]['id'], target_is_directory=True)
        (site / 'releases.json').write_text(json_text(root_app['releases']))

    default_slug = default['slug']
    default_icon = site / 'apps' / default_slug / 'icon.svg'
    if default_icon.is_file():
        shutil.copyfile(default_icon, site / 'icon.svg')
    uses_alias = default_slug == alias['appId']
    (site / 'index.html').write_text(render_page(
        default['releases'], template, apk_name=alias['apkName'] if uses_alias else 'App.apk',
        navigation=navigation_links(apps, default_slug, ''), app_name=default['name'],
        description=default['description'], icon='icon.svg' if default_icon.is_file() else None,
        prefix='' if uses_alias else 'apps/' + default_slug + '/'))
    pointer = state / ('.current-' + uuid.uuid4().hex)
    try:
        pointer.symlink_to(site, target_is_directory=True)
        os.replace(pointer, state / 'current')
    finally:
        pointer.unlink(missing_ok=True)


def refresh_site(state):
    with release_lock(state):
        require_migrated(state)
        apps = read_apps(state)
        if not apps:
            raise ValueError('Publish an APK before refreshing the download site')
        activate_site(state, read_registry(state), apps)


def validate_bundle(bundle):
    if {path.name for path in bundle.iterdir()} != BUNDLE_FILES:
        raise ValueError('A bundle must contain exactly App.apk, icon.svg and manifest.json')
    for name, limit in [('App.apk', MAX_APK_BYTES), ('icon.svg', MAX_ICON_BYTES),
                        ('manifest.json', MAX_MANIFEST_BYTES)]:
        path = bundle / name
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= limit:
            raise ValueError('Invalid or oversized bundle file: ' + name)
    manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
    required = {'schemaVersion', 'appId', 'name', 'description', 'notes'}
    if not isinstance(manifest, dict) or not required <= manifest.keys() <= required | {'commit'}:
        raise ValueError('Invalid manifest fields')
    if type(manifest['schemaVersion']) is not int or manifest['schemaVersion'] != 1:
        raise ValueError('Unsupported manifest schema version')
    for key, limit in [('appId', 40), ('name', 100), ('description', 2000), ('notes', 8000)]:
        value = manifest[key]
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError('Invalid manifest field: ' + key)
    if not SLUG.fullmatch(manifest['appId']):
        raise ValueError('Invalid app ID')
    if 'commit' in manifest and (not isinstance(manifest['commit'], str)
                                 or not re.fullmatch('[a-f0-9]{40}', manifest['commit'])):
        raise ValueError('Expected a full Git commit SHA')
    return manifest


def result(status, slug, release):
    prefix = 'apps/' + slug + '/'
    return {'status': status, 'appId': slug, 'releaseId': release['id'],
            'versionCode': release['versionCode'], 'versionName': release['versionName'],
            'sha256': release['sha256'], 'pagePath': prefix,
            'downloadPath': prefix + 'releases/' + release['id'] + '/App.apk'}


def bundle_matches(directory, manifest, icon, metadata):
    manifest_path = directory / 'manifest.json'
    return (manifest_path.is_file() and json.loads(manifest_path.read_text()) == manifest
            and (directory / 'icon.svg').read_bytes() == icon
            and digest(directory / 'App.apk') == metadata['sha256'])


def publish_bundle(bundle, state, sdk, *, allowed_app=None):
    manifest = validate_bundle(bundle)
    slug = manifest['appId']
    if allowed_app is not None and slug != allowed_app:
        raise ValueError('Publisher is not authorized for this app')
    icon = read_icon(bundle / 'icon.svg')
    with release_lock(state):
        require_migrated(state)
        registry = read_registry(state)
        identity = registry['apps'].get(slug)
        if identity is None:
            raise ValueError('App is not registered')
        apps = read_apps(state)
        old = next((app for app in apps if app['slug'] == slug), None)
        releases = old['releases'] if old else []
        work = state / 'work'
        work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='publish-', dir=work) as temp:
            staged = Path(temp) / 'artifact'
            staged.mkdir()
            apk = staged / 'App.apk'
            shutil.copyfile(bundle / 'App.apk', apk)
            metadata = inspect_apk(apk, sdk, identity['certificateSha256'], identity['applicationId'])
            permissions = identity.get('allowedPermissions')
            if permissions is not None and sorted(metadata['permissions']) != sorted(permissions):
                raise ValueError('APK permissions do not match the registered allowlist')
            if not 0 < metadata['versionCode'] <= 2100000000:
                raise ValueError('Invalid Android versionCode')
            if releases:
                latest = releases[0]
                latest_dir = state / 'app-releases' / slug / latest['id']
                if metadata['versionCode'] <= latest['versionCode']:
                    if (metadata['versionCode'] == latest['versionCode']
                            and bundle_matches(latest_dir, manifest, icon, metadata)):
                        return result('unchanged', slug, latest)
                    raise ValueError('Increase Android versionCode before publishing a changed bundle')
            metadata.update(id=f"v{metadata['versionCode']}-{metadata['sha256'][:12]}",
                            publishedAt=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                            notes=manifest['notes'],
                            sourceKind='git-snapshot' if 'commit' in manifest else 'prebuilt-apk',
                            checks=['apk-signature', 'package-version-sdk', 'apk-sha256'])
            if 'commit' in manifest:
                metadata['commit'] = manifest['commit']
            (staged / 'manifest.json').write_text(json_text(manifest))
            (staged / 'icon.svg').write_bytes(icon)
            (staged / 'release.json').write_text(json_text(metadata))
            (staged / 'SHA256SUMS.txt').write_text(metadata['sha256'] + '  App.apk\n')
            destination = state / 'app-releases' / slug / metadata['id']
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if not bundle_matches(destination, manifest, icon, metadata):
                    raise ValueError('An immutable release already exists with a different bundle')
                metadata = json.loads((destination / 'release.json').read_text())
            else:
                os.rename(staged, destination)
            app = {'slug': slug, 'name': manifest['name'], 'description': manifest['description'],
                   'releases': [metadata] + releases}
            updated = [app if item['slug'] == slug else item for item in apps] if old else apps + [app]
            activate_site(state, registry, updated)
            return result('published', slug, metadata)


def migrate_legacy(state, app_id, apk_name, name, description, sdk=DEFAULT_SDK):
    """Import the active legacy root and additional apps without rebuilding their APKs."""
    if not SLUG.fullmatch(app_id) or not APK_NAME.fullmatch(apk_name):
        raise ValueError('Invalid app ID or legacy APK name')
    if not name.strip() or not description.strip():
        raise ValueError('Legacy name and description must not be blank')
    with release_lock(state):
        current = state / 'current'
        if (current / '.protocol-v1').is_file():
            return {'status': 'unchanged', 'appIds': list(read_registry(state)['apps'])}
        releases = json.loads((current / 'releases.json').read_text())
        addons = read_apps(state)
        if any(app['slug'] == app_id for app in addons):
            raise ValueError('Legacy root app ID conflicts with an additional app')
        apps = [{'slug': app_id, 'name': name, 'description': description, 'releases': releases}, *addons]
        registry = {'schemaVersion': 1, 'defaultApp': app_id,
                    'rootAlias': {'appId': app_id, 'apkName': apk_name}, 'apps': {}}
        for app in apps:
            slug = app['slug']
            latest = app['releases'][0]
            identity = {key: latest[key] for key in ('applicationId', 'certificateSha256')}
            validate_identity(slug, identity)
            if any(record['applicationId'] == identity['applicationId']
                   for record in registry['apps'].values()):
                raise ValueError('Legacy catalog contains duplicate Android packages')
            registry['apps'][slug] = identity
            for release in app['releases']:
                identifier = release['id']
                if not RELEASE_ID.fullmatch(identifier):
                    raise ValueError('Invalid legacy release ID')
                if any(release[key] != identity[key] for key in ('applicationId', 'certificateSha256')):
                    raise ValueError('Legacy app contains incompatible signing identities')
                if not SHA256.fullmatch(release['sha256']):
                    raise ValueError('Invalid legacy APK digest')
                old_root = slug == app_id
                directory = state / 'app-releases' / slug / identifier
                apk = current / 'releases' / identifier / apk_name if old_root else directory / 'App.apk'
                if digest(apk) != release['sha256'] or apk.stat().st_size != release['bytes']:
                    raise ValueError('Legacy APK integrity check failed: ' + slug + '/' + identifier)
                if old_root:
                    directory.mkdir(parents=True, exist_ok=True)
                    if not (directory / 'App.apk').exists():
                        os.link(apk, directory / 'App.apk')
                    elif digest(directory / 'App.apk') != release['sha256']:
                        raise ValueError('Conflicting migrated APK: ' + slug + '/' + identifier)
                    atomic_text(directory / 'release.json', json_text(release))
                    atomic_text(directory / 'SHA256SUMS.txt', release['sha256'] + '  App.apk\n')
            latest_apk = state / 'app-releases' / slug / latest['id'] / 'App.apk'
            latest_info = inspect_apk(latest_apk, sdk, identity['certificateSha256'],
                                      identity['applicationId'])
            identity['allowedPermissions'] = sorted(latest_info['permissions'])
            previous_icon = current / ('icon.svg' if slug == app_id else 'apps/' + slug + '/icon.svg')
            if previous_icon.is_file():
                legacy_icons = state / 'legacy-icons'
                legacy_icons.mkdir(exist_ok=True)
                atomic_text(legacy_icons / (slug + '.svg'), read_icon(previous_icon).decode('utf-8'))
        registry_path = state / 'registry.json'
        existed = registry_path.exists()
        if existed and read_registry(state) != registry:
            raise ValueError('Registry already exists with different entries')
        atomic_text(registry_path, json_text(registry))
        try:
            activate_site(state, registry, apps)
        except Exception:
            if not existed:
                registry_path.unlink(missing_ok=True)
            raise
        return {'status': 'migrated', 'appIds': list(registry['apps'])}
