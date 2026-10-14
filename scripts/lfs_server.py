#!/usr/bin/env python3
"""A minimal Git LFS batch server for qualification round trips on 127.0.0.1.

Every repository is a path prefix: POST <repo>/objects/batch, PUT and GET
<repo>/objects/<oid>, and GET <repo>/stats for the transfers it has seen.
"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sys
import threading

LFS_JSON = "application/vnd.git-lfs+json"
OBJECT = re.compile(r"^/([A-Za-z0-9._-]+)/objects/([0-9a-f]{64})$")
BATCH = re.compile(r"^/([A-Za-z0-9._-]+)/objects/batch$")
STATS = re.compile(r"^/([A-Za-z0-9._-]+)/stats$")


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.Lock()
        self.stats = {}

    def path(self, repository, oid):
        return self.root / repository / oid

    def count(self, repository, key):
        with self.lock:
            entry = self.stats.setdefault(repository, {"uploads": 0, "downloads": 0, "batches": 0})
            entry[key] += 1


def handler(store):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            sys.stderr.write("lfs-server: " + format % args + "\n")

        def reply(self, status, body=b"", content_type=LFS_JSON):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def body(self):
            return self.rfile.read(int(self.headers.get("Content-Length", "0")))

        def do_POST(self):
            match = BATCH.match(self.path)
            if not match:
                return self.reply(404, b'{"message":"not found"}')
            repository = match.group(1)
            request = json.loads(self.body())
            store.count(repository, "batches")
            base = f"http://{self.headers['Host']}/{repository}/objects/"
            objects = []
            for item in request.get("objects", []):
                oid, size = item["oid"], item["size"]
                present = store.path(repository, oid).is_file()
                entry = {"oid": oid, "size": size, "authenticated": True}
                if request.get("operation") == "upload":
                    if not present:
                        entry["actions"] = {"upload": {"href": base + oid}}
                elif present:
                    entry["actions"] = {"download": {"href": base + oid}}
                else:
                    entry["error"] = {"code": 404, "message": "Object does not exist"}
                objects.append(entry)
            self.reply(200, json.dumps({"transfer": "basic", "objects": objects}).encode())

        def do_PUT(self):
            match = OBJECT.match(self.path)
            if not match:
                return self.reply(404)
            repository, oid = match.groups()
            data = self.body()
            if hashlib.sha256(data).hexdigest() != oid:
                return self.reply(422, b'{"message":"digest mismatch"}')
            target = store.path(repository, oid)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            store.count(repository, "uploads")
            self.reply(200)

        def do_GET(self):
            match = STATS.match(self.path)
            if match:
                with store.lock:
                    stats = dict(store.stats.get(match.group(1), {"uploads": 0, "downloads": 0, "batches": 0}))
                return self.reply(200, json.dumps(stats).encode(), "application/json")
            match = OBJECT.match(self.path)
            if not match or not store.path(*match.groups()).is_file():
                return self.reply(404)
            store.count(match.group(1), "downloads")
            self.reply(200, store.path(*match.groups()).read_bytes(), "application/octet-stream")

    return Handler


def serve(root, port=0):
    server = ThreadingHTTPServer(("127.0.0.1", port), handler(Store(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(Store(args.root)))
    print(server.server_address[1], flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
