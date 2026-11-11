#!/usr/bin/env python3
"""Manage an isolated, read-only Nginx download service via a macOS LaunchAgent."""
import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener

from publishing import DEFAULT_STATE, atomic_text

LABEL = "app.shadowinggems.downloads"
PLIST = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
DOMAIN = f"gui/{os.getuid()}"


def nginx_quote(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


def nginx_config(state, port):
    root = nginx_quote(state / "current")
    runtime = state / "service"
    return f"""
worker_processes 1;
daemon off;
pid {nginx_quote(runtime / 'nginx.pid')};
error_log {nginx_quote(runtime / 'error.log')} warn;
events {{ worker_connections 256; }}
http {{
    types {{
        text/html html;
        text/plain txt;
        application/json json;
        image/svg+xml svg;
        application/vnd.android.package-archive apk;
    }}
    default_type application/octet-stream;
    charset utf-8;
    sendfile on;
    access_log off;
    server_tokens off;
    client_max_body_size 1k;
    client_body_temp_path {nginx_quote(runtime / 'client_temp')};
    proxy_temp_path {nginx_quote(runtime / 'proxy_temp')};
    fastcgi_temp_path {nginx_quote(runtime / 'fastcgi_temp')};
    uwsgi_temp_path {nginx_quote(runtime / 'uwsgi_temp')};
    scgi_temp_path {nginx_quote(runtime / 'scgi_temp')};
    server {{
        listen 0.0.0.0:{port};
        server_name _;
        root {root};
        autoindex off;
        # Edge Nginx or a tunnel must reach this origin through loopback/private LAN.
        allow 127.0.0.1;
        allow 10.0.0.0/8;
        allow 172.16.0.0/12;
        allow 192.168.0.0/16;
        deny all;
        if ($request_method !~ ^(GET|HEAD)$) {{ return 405; }}
        add_header X-Content-Type-Options nosniff always;
        add_header X-Frame-Options DENY always;
        add_header Referrer-Policy no-referrer always;
        add_header Cache-Control "no-store" always;
        add_header Content-Security-Policy "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'self'; frame-ancestors 'none'" always;
        location = / {{ try_files /index.html =404; }}
        location = /index.html {{ try_files $uri =404; }}
        location = /icon.svg {{ try_files $uri =404; }}
        location = /apps.json {{ try_files $uri =404; }}
        location ~ "^/apps/[a-z][a-z0-9-]{{0,39}}/$" {{ try_files $uri/index.html =404; }}
        location ~ "^/apps/[a-z][a-z0-9-]{{0,39}}/(index[.]html|releases[.]json|icon[.]svg)$" {{ try_files $uri =404; }}
        location ~ "^/apps/[a-z][a-z0-9-]{{0,39}}/(latest|releases/[a-zA-Z0-9.-]+)/(App[.]apk|release[.]json|download[.]json|SHA256SUMS[.]txt)$" {{ try_files $uri =404; }}
        location = /releases.json {{ try_files $uri =404; }}
        location ~ ^/(latest|releases/[a-zA-Z0-9.-]+)/([a-zA-Z0-9_-]+[.]apk|release[.]json|download[.]json|SHA256SUMS[.]txt)$ {{
            try_files $uri =404;
        }}
        location / {{ return 404; }}
    }}
}}
"""


def service_loaded():
    return subprocess.run(["launchctl", "print", f"{DOMAIN}/{LABEL}"],
                          capture_output=True).returncode == 0


def bootstrap():
    """launchd can briefly retain a just-booted-out job; retry only that exit code."""
    for attempt, delay in enumerate((0.2, 0.5, 1.0, 2.0, None)):
        try:
            subprocess.run(["launchctl", "bootstrap", DOMAIN, str(PLIST)], check=True,
                           capture_output=True, text=True)
            return
        except subprocess.CalledProcessError as error:
            if error.returncode != 5 or delay is None:
                detail = (error.stderr or '').strip()
                raise RuntimeError('launchctl bootstrap failed after ' + str(attempt + 1) +
                                   ' attempt(s)' + (': ' + detail if detail else '')) from None
            time.sleep(delay)


def check_port_available(port):
    import socket
    with socket.socket() as probe:
        probe.bind(("0.0.0.0", port))


def launch_definition(nginx, runtime, config):
    return {
        "Label": LABEL, "ProgramArguments": [nginx, "-p", str(runtime) + "/", "-c", str(config)],
        "WorkingDirectory": str(runtime), "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 10,
        "StandardOutPath": str(runtime / "launchd.stdout.log"),
        "StandardErrorPath": str(runtime / "launchd.stderr.log"), "ProcessType": "Background",
    }


def install(state, port):
    if not (state / "current/index.html").is_file():
        raise RuntimeError("Publish the first APK with scripts/publish-apk.py before installing the service.")
    nginx = shutil.which("nginx")
    if not nginx:
        raise RuntimeError("Nginx is required. Install a prebuilt package first.")
    # Pin the resolved executable, not a shell PATH that launchd will not inherit.
    nginx = str(Path(nginx).resolve())
    runtime = state / "service"
    runtime.mkdir(parents=True, exist_ok=True)
    config = runtime / "nginx.conf"
    candidate = runtime / "nginx.candidate.conf"
    atomic_text(candidate, nginx_config(state, port))
    prefix = str(runtime) + "/"
    subprocess.run([nginx, "-p", prefix, "-c", str(candidate), "-t"], check=True)
    loaded = service_loaded()
    previous_config = config.read_bytes() if config.is_file() else None
    previous_plist = PLIST.read_bytes() if PLIST.is_file() else None
    definition = launch_definition(nginx, runtime, config)
    previous_definition = plistlib.loads(previous_plist) if previous_plist is not None else None
    same_port = previous_config is not None and f'listen 0.0.0.0:{port};'.encode() in previous_config
    # Updating routes for the same managed instance needs no launchd restart.
    if loaded and previous_definition == definition and same_port:
        os.replace(candidate, config)
        try:
            subprocess.run([nginx, "-p", prefix, "-c", str(config), "-s", "reload"], check=True)
        except subprocess.CalledProcessError:
            atomic_text(config, previous_config.decode())
            raise
        print(f"Reloaded {LABEL}; kept the existing LaunchAgent and running Nginx master.")
        print(f"Origin: http://localhost:{port}/\nConfig: {config}\nLaunchAgent: {PLIST}")
        return
    # Detect conflicts before stopping an old instance on another port/path.
    if not loaded or not same_port:
        check_port_available(port)
    os.replace(candidate, config)
    PLIST.parent.mkdir(exist_ok=True)
    if loaded:
        subprocess.run(["launchctl", "bootout", f"{DOMAIN}/{LABEL}"], check=True)
    atomic_text(PLIST, plistlib.dumps(definition).decode())
    try:
        bootstrap()
    except RuntimeError as failed:
        # Restore a previously running service if a real reconfiguration cannot start.
        if loaded and previous_plist is not None and previous_config is not None:
            atomic_text(PLIST, plistlib.dumps(previous_definition).decode())
            atomic_text(config, previous_config.decode())
            try:
                bootstrap()
            except RuntimeError as restore_failed:
                raise RuntimeError(str(failed) + '; previous service restore also failed: ' + str(restore_failed)) from None
            raise RuntimeError(str(failed) + '; previous service configuration was restored') from None
        raise
    print(f"Installed {LABEL}; starts after this Mac user logs in.")
    print(f"Origin: http://localhost:{port}/\nConfig: {config}\nLaunchAgent: {PLIST}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "start", "stop", "status"])
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--port", type=int, default=8098)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a non-privileged port between 1024 and 65535")
    try:
        if args.action == "install":
            install(args.state_dir.expanduser().resolve(), args.port)
        elif args.action == "start":
            if not service_loaded():
                bootstrap()
            else:
                subprocess.run(["launchctl", "kickstart", f"{DOMAIN}/{LABEL}"], check=True)
        elif args.action == "stop":
            if service_loaded():
                subprocess.run(["launchctl", "bootout", f"{DOMAIN}/{LABEL}"], check=True)
            print("Stopped. Use start to resume; remove the plist to disable login autostart.")
        else:
            subprocess.run(["launchctl", "print", f"{DOMAIN}/{LABEL}"], check=True)
            # Ignore shell/system HTTP proxies for local health verification.
            opener = build_opener(ProxyHandler({}))
            with opener.open(f"http://127.0.0.1:{args.port}/latest/release.json", timeout=5) as result:
                print(result.read().decode())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"Download service: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
