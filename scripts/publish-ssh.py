#!/usr/bin/env python3
"""Receive one v1 APK release bundle from an app-scoped SSH key."""
import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import zipfile
import zlib

from publishing import (DEFAULT_SDK, DEFAULT_STATE, MAX_APK_BYTES, MAX_ICON_BYTES,
                        MAX_MANIFEST_BYTES, publish_bundle)


MEMBER_LIMITS = {
    "App.apk": MAX_APK_BYTES,
    "icon.svg": MAX_ICON_BYTES,
    "manifest.json": MAX_MANIFEST_BYTES,
}
MAX_ZIP_BYTES = sum(MEMBER_LIMITS.values()) + 1024 * 1024
MAX_INDEX_BYTES = 64 * 1024
ZIP_END = struct.Struct("<4s4H2LH")


def check_zip_index(archive, size):
    """Bound the ZIP index before zipfile creates a ZipInfo for each entry."""
    if size < ZIP_END.size:
        raise ValueError("Expected a v1 ZIP archive")
    archive.seek(size - ZIP_END.size)
    signature, disk, index_disk, disk_entries, entries, index_size, offset, comment = (
        ZIP_END.unpack(archive.read(ZIP_END.size))
    )
    if (signature != b"PK\x05\x06" or disk or index_disk or comment or
            disk_entries != len(MEMBER_LIMITS) or entries != len(MEMBER_LIMITS) or
            index_size > MAX_INDEX_BYTES or offset + index_size != size - ZIP_END.size):
        raise ValueError("Expected a v1 ZIP with exactly three files and no archive comment")
    if size >= ZIP_END.size + 20:
        archive.seek(size - ZIP_END.size - 20)
        if archive.read(4) == b"PK\x06\x07":
            raise ValueError("ZIP64 archives are not supported")


def receive_bundle(source, bundle):
    with tempfile.TemporaryFile() as incoming:
        size = 0
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_ZIP_BYTES:
                raise ValueError("Release ZIP exceeds the upload size limit")
            incoming.write(chunk)
        check_zip_index(incoming, size)
        with zipfile.ZipFile(incoming) as archive:
            members = archive.infolist()
            if (len(members) != len(MEMBER_LIMITS) or
                    {item.orig_filename for item in members} != MEMBER_LIMITS.keys()):
                raise ValueError("Release ZIP must contain exactly App.apk, icon.svg and manifest.json")
            for item in members:
                mode = stat.S_IFMT(item.external_attr >> 16)
                if (item.is_dir() or mode not in (0, stat.S_IFREG) or item.flag_bits & 1 or
                        item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) or
                        not 0 < item.file_size <= MEMBER_LIMITS[item.filename]):
                    raise ValueError(f"Invalid ZIP member: {item.filename}")
            for item in members:
                with archive.open(item) as content, (bundle / item.filename).open("xb") as output:
                    shutil.copyfileobj(content, output, length=1024 * 1024)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-id", required=True, help="App authorized by this SSH key")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--sdk", type=Path, default=DEFAULT_SDK)
    args = parser.parse_args()
    if os.environ.get("SSH_ORIGINAL_COMMAND") != "publish-v1":
        parser.exit(1, "Expected the publish-v1 SSH command.\n")
    try:
        with tempfile.TemporaryDirectory(prefix="apk-publish-ssh-") as work:
            bundle = Path(work)
            receive_bundle(sys.stdin.buffer, bundle)
            result = publish_bundle(bundle, args.state_dir, args.sdk, allowed_app=args.app_id)
    except (OSError, EOFError, ValueError, RuntimeError, zipfile.BadZipFile,
            zlib.error, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Publish failed: {error}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
