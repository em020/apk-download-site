#!/usr/bin/env python3
"""Manage an isolated, read-only Nginx download service via a macOS LaunchAgent."""
import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
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
    # Detect a conflicting server before touching any existing managed service.
    if not service_loaded():
        import socket
        with socket.socket() as probe:
            probe.bind(("0.0.0.0", port))
    os.replace(candidate, config)
    arguments = [nginx, "-p", prefix, "-c", str(config)]
    definition = {
        "Label": LABEL, "ProgramArguments": arguments, "WorkingDirectory": str(runtime),
        "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 10,
        "StandardOutPath": str(runtime / "launchd.stdout.log"),
        "StandardErrorPath": str(runtime / "launchd.stderr.log"),
        "ProcessType": "Background",
    }
    PLIST.parent.mkdir(exist_ok=True)
    if service_loaded():
        subprocess.run(["launchctl", "bootout", f"{DOMAIN}/{LABEL}"], check=True)
    atomic_text(PLIST, plistlib.dumps(definition).decode())
    subprocess.run(["launchctl", "bootstrap", DOMAIN, str(PLIST)], check=True)
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
                subprocess.run(["launchctl", "bootstrap", DOMAIN, str(PLIST)], check=True)
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
