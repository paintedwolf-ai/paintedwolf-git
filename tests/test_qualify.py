import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
import urllib.request

import support  # noqa: F401
import lfs_server
import qualify


def call(method, url, body=None, content_type="application/vnd.git-lfs+json"):
    request = urllib.request.Request(url, data=body, method=method, headers={"Content-Type": content_type})
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read()


class LFSServerTest(unittest.TestCase):
    def test_upload_then_download_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            server = lfs_server.serve(Path(temporary))
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            base = f"http://127.0.0.1:{server.server_address[1]}/family"
            data = b"large file contents"
            oid = hashlib.sha256(data).hexdigest()
            request = {"operation": "upload", "objects": [{"oid": oid, "size": len(data)}]}
            batch = json.loads(call("POST", base + "/objects/batch", json.dumps(request).encode()))
            href = batch["objects"][0]["actions"]["upload"]["href"]
            with self.assertRaises(urllib.error.HTTPError):
                call("PUT", href, b"other bytes", "application/octet-stream")
            call("PUT", href, data, "application/octet-stream")
            request["operation"] = "download"
            batch = json.loads(call("POST", base + "/objects/batch", json.dumps(request).encode()))
            self.assertEqual(call("GET", batch["objects"][0]["actions"]["download"]["href"]), data)
            missing = {"operation": "download", "objects": [{"oid": "0" * 64, "size": 1}]}
            batch = json.loads(call("POST", base + "/objects/batch", json.dumps(missing).encode()))
            self.assertEqual(batch["objects"][0]["error"]["code"], 404)
            self.assertEqual(json.loads(call("GET", base + "/stats")), {"uploads": 1, "downloads": 1, "batches": 3})


class SmokeResultTest(unittest.TestCase):
    def test_exactly_one_result_line(self):
        self.assertEqual(qualify.parse_result('noise\nRESULT {"ok": true}\n'), {"ok": True})
        for output in ("no result\n", 'RESULT {}\nRESULT {}\n'):
            with self.subTest(output=output), self.assertRaises(ValueError):
                qualify.parse_result(output)


if __name__ == "__main__":
    unittest.main()
