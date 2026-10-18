#!/usr/bin/env python3
"""Verify SmartEdu's signed S3 -> WebP contract with Docker and Python stdlib.

The S3 fixture runs in python:3.12-alpine on an isolated Docker network. It uses
synthetic pixels and test credentials; no host networking or real storage.
"""

import base64
import hashlib
import hmac
import http.server
import json
from pathlib import Path
import secrets
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def serve_fixture():
    header = struct.pack(">IIBBBBB", 800, 400, 8, 2, 0, 0, 0)
    rows = (b"\x00" + b"\x30\x80\xd0" * 800) * 400
    fixture = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")

    class S3Fixture(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if urllib.parse.urlsplit(self.path).path != "/smoke/source.png" or not self.headers.get("Authorization", "").startswith("AWS4-HMAC-SHA256 "):
                print("FIXTURE_REJECTED", self.path, self.headers.get("Authorization", "").split(" ")[0], flush=True)
                self.send_error(403)
                return
            print("SOURCE_READ", flush=True)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(fixture)))
            self.send_header("ETag", '"smoke-fixture"')
            self.end_headers()
            self.wfile.write(fixture)

        def log_message(self, *_):
            pass

    http.server.ThreadingHTTPServer(("0.0.0.0", 9000), S3Fixture).serve_forever()


def webp_dimensions(data):
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP", "Not a WebP image"
    pos = 12
    while pos + 8 <= len(data):
        kind, size = data[pos:pos + 4], int.from_bytes(data[pos + 4:pos + 8], "little")
        body = data[pos + 8:pos + 8 + size]
        if kind == b"VP8 ":
            assert body[3:6] == b"\x9d\x01\x2a"
            width, height = struct.unpack("<HH", body[6:10])
            return width & 0x3fff, height & 0x3fff
        if kind == b"VP8L":
            assert body[0] == 0x2f
            bits = int.from_bytes(body[1:5], "little")
            return (bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1
        if kind == b"VP8X":
            return int.from_bytes(body[4:7], "little") + 1, int.from_bytes(body[7:10], "little") + 1
        pos += 8 + size + size % 2
    raise AssertionError("Missing WebP dimensions")


def main(image):
    container = "imgproxy-smoke-" + secrets.token_hex(6)
    fixture = container + "-s3"
    network = container + "-net"
    key, salt = secrets.token_bytes(32), secrets.token_bytes(32)
    env = {
        "IMGPROXY_KEY": key.hex(),
        "IMGPROXY_SALT": salt.hex(),
        "IMGPROXY_BIND": ":8080",
        "IMGPROXY_USE_S3": "true",
        "IMGPROXY_S3_ENDPOINT": f"http://{fixture}:9000",
        "IMGPROXY_S3_REGION": "us-east-1",
        "IMGPROXY_S3_ENDPOINT_USE_PATH_STYLE": "true",
        "IMGPROXY_ALLOWED_SOURCES": "s3://smoke/",
        "IMGPROXY_S3_ALLOWED_BUCKETS": "smoke",
        "AWS_ACCESS_KEY_ID": "smoke-only",
        "AWS_SECRET_ACCESS_KEY": "smoke-only",
        "AWS_EC2_METADATA_DISABLED": "true",
        "HTTP_PROXY": "",
        "HTTPS_PROXY": "",
        "ALL_PROXY": "",
        "NO_PROXY": "*",
        "IMGPROXY_WORKERS": "2",
        "IMGPROXY_REQUESTS_QUEUE_SIZE": "16",
        "IMGPROXY_MAX_SRC_FILE_SIZE": "20971520",
        "IMGPROXY_MAX_SRC_RESOLUTION": "50",
        "IMGPROXY_MAX_ANIMATION_FRAMES": "1",
        "IMGPROXY_MAX_RESULT_DIMENSION": "2048",
        "IMGPROXY_DOWNLOAD_TIMEOUT": "10",
        "IMGPROXY_TIMEOUT": "20",
    }
    args = [
        "run", "-d", "--name", container, "--network", network,
        "-p", "127.0.0.1::8080", "--read-only",
        "--tmpfs", "/tmp:size=256m,mode=1777",
        "--tmpfs", "/var/cache/fontconfig:size=16m,mode=1777",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--memory", "2g", "--cpus", "2",
    ]
    for name, value in env.items():
        args.extend(["-e", f"{name}={value}"])
    args.append(image)

    try:
        docker("network", "create", network)
        docker("run", "-d", "--name", fixture, "--network", network,
               "-v", f"{Path(__file__).resolve()}:/smoke.py:ro",
               "python:3.12-alpine", "python", "/smoke.py", "--serve-fixture")
        docker(*args)
        bindings = json.loads(docker("inspect", "--format", "{{json .NetworkSettings.Ports}}", container))
        base = "http://127.0.0.1:" + bindings["8080/tcp"][0]["HostPort"]
        for attempt in range(60):
            try:
                with urllib.request.urlopen(base + "/health", timeout=2) as response:
                    assert response.status == 200
                break
            except (OSError, AssertionError):
                if attempt == 59:
                    raise
                time.sleep(1)
        docker("exec", container, "imgproxy", "health")
        encoded = base64.urlsafe_b64encode(b"s3://smoke/source.png").rstrip(b"=").decode()
        path = "/rs:fit:480:480:0/sh:0.5/q:92/" + encoded + ".webp"
        signature = base64.urlsafe_b64encode(hmac.new(key, salt + path.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
        with urllib.request.urlopen(base + "/" + signature + path, timeout=25) as response:
            assert response.status == 200
            assert response.headers.get_content_type() == "image/webp"
            assert webp_dimensions(response.read()) == (480, 240)
        before = docker("logs", fixture).count("SOURCE_READ")
        assert before > 0, "Image was not read through S3"
        try:
            urllib.request.urlopen(base + "/invalid-signature" + path, timeout=5)
            raise AssertionError("Invalid signature was accepted")
        except urllib.error.HTTPError as error:
            assert error.code == 403, error.code
        assert docker("logs", fixture).count("SOURCE_READ") == before, "Invalid signature reached storage"
        print("PASS: health, signed S3 read, 480x240 WebP, invalid signature rejected before storage")
    except BaseException:
        subprocess.run(["docker", "logs", container], check=False)
        subprocess.run(["docker", "logs", fixture], check=False)
        raise
    finally:
        subprocess.run(["docker", "rm", "-f", container, fixture], stdout=subprocess.DEVNULL, check=False)
        subprocess.run(["docker", "network", "rm", network], stdout=subprocess.DEVNULL, check=False)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 scripts/smoke-image.py IMAGE")
    if sys.argv[1] == "--serve-fixture":
        serve_fixture()
    else:
        main(sys.argv[1])
