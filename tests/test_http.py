import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from yeno.server import make_server
from .helpers import observation


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server(port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()

    def test_actual_http_endpoint(self):
        request = Request(self.base + "/decide", json.dumps(observation()).encode(), {"Content-Type": "application/json"})
        with urlopen(request, timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.load(response), {"action": "HOLD"})

    def test_health(self):
        with urlopen(self.base + "/health", timeout=2) as response:
            self.assertEqual(json.load(response)["status"], "ok")

    def test_bad_route_and_nonfinite_json_rejected(self):
        for route, body, status in (("/nope", b"{}", 404), ("/decide", b"{bad", 400),
                                    ("/decide", b'{"timestamp": NaN}', 400)):
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(self.base + route, body), timeout=2)
            self.assertEqual(error.exception.code, status)


if __name__ == "__main__":
    unittest.main()
