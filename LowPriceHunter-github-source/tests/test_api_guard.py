import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer

from ps5hunter.webui import (
    CSRF_TOKEN,
    Handler,
    _host_allowed,
    _json_content_type_ok,
    _origin_allowed,
)


class OriginHelperTest(unittest.TestCase):
    def test_host_allowlist(self):
        self.assertTrue(_host_allowed("127.0.0.1:8765", 8765))
        self.assertTrue(_host_allowed("localhost:8765", 8765))
        self.assertFalse(_host_allowed("evil.example:8765", 8765))
        self.assertFalse(_host_allowed("127.0.0.1:9999", 8765))
        self.assertFalse(_host_allowed(None, 8765))

    def test_missing_origin_allowed(self):
        self.assertTrue(_origin_allowed(None, "127.0.0.1:8765"))

    def test_same_origin_allowed(self):
        self.assertTrue(_origin_allowed("http://127.0.0.1:8765", "127.0.0.1:8765"))

    def test_cross_origin_rejected(self):
        self.assertFalse(_origin_allowed("https://evil.example", "127.0.0.1:8765"))
        self.assertFalse(_origin_allowed("http://127.0.0.1:9999", "127.0.0.1:8765"))
        self.assertFalse(_origin_allowed("null", "127.0.0.1:8765"))

    def test_content_type(self):
        self.assertTrue(_json_content_type_ok("application/json"))
        self.assertTrue(_json_content_type_ok("application/json; charset=utf-8"))
        self.assertFalse(_json_content_type_ok("text/plain"))
        self.assertFalse(_json_content_type_ok(None))


class HandlerGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def post(self, origin, content_type, token=CSRF_TOKEN):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": content_type}
        if origin:
            headers["Origin"] = origin
        if token is not None:
            headers["X-CSRF-Token"] = token
        payload = json.dumps({"text": "", "rules": []}).encode("utf-8")
        conn.request("POST", "/api/test-text", body=payload, headers=headers)
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status, body

    def test_cross_origin_post_rejected(self):
        status, body = self.post("https://evil.example", "application/json")
        self.assertEqual(status, 403)
        self.assertFalse(body["ok"])

    def test_text_plain_post_rejected(self):
        status, body = self.post(None, "text/plain")
        self.assertEqual(status, 403)
        self.assertFalse(body["ok"])

    def test_missing_csrf_token_rejected(self):
        status, body = self.post(None, "application/json", token=None)
        self.assertEqual(status, 403)
        self.assertFalse(body["ok"])

    def test_wrong_csrf_token_rejected(self):
        status, body = self.post(None, "application/json", token="wrong")
        self.assertEqual(status, 403)
        self.assertFalse(body["ok"])

    def test_same_origin_json_allowed(self):
        status, body = self.post(
            f"http://127.0.0.1:{self.port}", "application/json"
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def get_with_host(self, host):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.putrequest("GET", "/", skip_host=True)
        conn.putheader("Host", host)
        conn.endheaders()
        resp = conn.getresponse()
        resp.read()
        conn.close()
        return resp.status

    def test_bad_host_get_rejected(self):
        self.assertEqual(self.get_with_host("evil.example"), 403)

    def test_good_host_get_allowed(self):
        self.assertEqual(self.get_with_host(f"127.0.0.1:{self.port}"), 200)


if __name__ == "__main__":
    unittest.main()
