#!/usr/bin/env python3
"""Publish immutable files independently of the business release catalog.

Only the operator controls this private configuration and command. Client-facing
descriptors contain HTTP locations and integrity information, never credentials.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def load_config(path):
    if not path.is_file():
        return {'schemaVersion': 1, 'mode': 'local'}
    config = json.loads(path.read_text())
    if (not isinstance(config, dict) or config.get('schemaVersion') != 1 or
            config.get('mode') not in ('local', 'external') or
            not config.keys() <= {'schemaVersion', 'mode', 'publicBaseUrl', 'publishCommand'}):
        raise ValueError('Invalid private file delivery configuration')
    if config['mode'] == 'external':
        base = config.get('publicBaseUrl', '')
        parsed = urlsplit(base)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None or
                parsed.password is not None or parsed.query or parsed.fragment or
                not base.endswith('/')):
            raise ValueError('External delivery requires a permanent HTTPS publicBaseUrl ending in /')
        parsed.port  # Validate malformed ports before constructing any URLs.
        command = config.get('publishCommand')
        if command is not None and (not isinstance(command, list) or not command or
                any(not isinstance(arg, str) or not arg or '\0' in arg for arg in command)):
            raise ValueError('publishCommand must be a nonempty JSON argv array')
    return config


class HttpsRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        target = urlsplit(new_url)
        if target.scheme != 'https' or not target.hostname or target.username is not None:
            raise ValueError('External download redirects must remain HTTPS')
        return super().redirect_request(request, response, code, message, headers, new_url)


def verify_remote(url, sha256, size):
    """Return False only for a missing object; existing mismatches must not be overwritten."""
    try:
        with build_opener(HttpsRedirects()).open(Request(url, headers={'Accept-Encoding': 'identity'}),
                                                timeout=30) as response:
            if response.status != 200:
                raise ValueError('External object did not return a complete file')
            length = response.headers.get('Content-Length')
            if length is not None and int(length) != size:
                raise ValueError('Existing external object has a different size')
            if response.headers.get('Content-Encoding', 'identity') != 'identity':
                raise ValueError('External object must be served without content transformation')
            result, total = hashlib.sha256(), 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > size:
                    raise ValueError('Existing external object exceeds its declared size')
                result.update(chunk)
            if total != size or result.hexdigest() != sha256:
                raise ValueError('Existing external object checksum differs from the immutable artifact')
            return True
    except HTTPError as error:
        if error.code == 404:
            return False
        raise RuntimeError('External object verification failed (HTTP ' + str(error.code) + ')') from None
    except URLError:
        raise RuntimeError('External object verification failed; check operator connectivity') from None


def deliver(config, source, object_key, sha256, size, *, cache=None):
    key = PurePosixPath(object_key)
    if (not object_key or key.is_absolute() or str(key) != object_key or
            any(part in ('.', '..') or not re.fullmatch(r'[A-Za-z0-9._-]+', part)
                for part in object_key.split('/'))):
        raise ValueError('Invalid immutable object key')
    if not isinstance(size, int) or size <= 0 or not isinstance(sha256, str) or len(sha256) != 64 or any(
            char not in '0123456789abcdef' for char in sha256):
        raise ValueError('Invalid file integrity metadata')
    if source.stat().st_size != size or digest(source) != sha256:
        raise ValueError('Local artifact integrity check failed')
    result = {'objectKey': object_key, 'bytes': size, 'sha256': sha256,
              'downloadUrl': None, 'expiresAt': None}
    if config['mode'] == 'local':
        return result
    url = config['publicBaseUrl'] + quote(object_key, safe='/')
    result['downloadUrl'] = url
    cached = None
    if cache is not None:
        cache.mkdir(parents=True, exist_ok=True)
        cached = cache / (hashlib.sha256((url + '\n' + sha256).encode()).hexdigest() + '.json')
        if cached.is_file() and json.loads(cached.read_text()) == result:
            return result
    if not verify_remote(url, sha256, size):
        command = config.get('publishCommand')
        if not command:
            raise ValueError('External artifact is not present; upload it first or configure publishCommand')
        # An operator-owned program may use any storage SDK/CLI and credentials
        # from its environment. Arguments and output are deliberately not logged.
        try:
            subprocess.run([*command, str(source.resolve()), object_key, sha256, str(size)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3600)
        except (OSError, subprocess.SubprocessError):
            raise RuntimeError('External artifact publication command failed; catalog was not activated') from None
        if not verify_remote(url, sha256, size):
            raise ValueError('External artifact is not visible after publication; retry when ready')
    if cached is not None:
        # Cache successful verification of immutable objects, outside the public tree.
        temporary = cached.with_suffix('.tmp')
        temporary.write_text(json.dumps(result))
        temporary.replace(cached)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--file', type=Path, required=True)
    parser.add_argument('--object-key', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--bytes', type=int, required=True)
    args = parser.parse_args()
    try:
        result = deliver(load_config(args.config), args.file, args.object_key, args.sha256, args.bytes)
    except (ValueError, RuntimeError, OSError) as error:
        parser.exit(1, str(error) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
