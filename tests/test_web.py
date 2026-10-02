import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace as NS

from porsche_guru.data import ModelTable
from porsche_guru.web import App, make_handler
from tests.test_porsche_guru import DATA, fake_client


class WebTest(unittest.TestCase):
    def setUp(self):
        final = NS(stop_reason="end_turn", content=[
            NS(type="server_tool_use", name="web_search", input={"query": "992 GT3 for sale"}),
            NS(type="text", text="| Car | Price |\n|---|---|\n| GT3 | $220k |",
               citations=[NS(url="https://bringatrailer.com/x", title="BaT")]),
        ])
        client, self.messages = fake_client([final])
        self.app = App(ModelTable(DATA), client=client)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def request(self, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_serves_page_and_health(self):
        status, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Porsche <span>Guru</span>", body)
        for path in ("/health", "/healthz", "/ready"):
            status, body = self.request(path)
            self.assertEqual((status, json.loads(body)["models"]), (200, 10))
        req = urllib.request.Request(self.base + "/health", method="HEAD")
        with urllib.request.urlopen(req, timeout=5) as res:
            self.assertEqual(res.status, 200)

    def test_ask_runs_job_to_completion(self):
        status, body = self.request("/api/ask", {"session_id": "s1", "question": "Find GT3s"})
        self.assertEqual(status, 202)
        job_id = json.loads(body)["job_id"]

        for _ in range(50):
            job = json.loads(self.request(f"/api/jobs/{job_id}")[1])
            if job["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(job["status"], "done")
        self.assertIn("$220k", job["result"]["text"])
        self.assertEqual(job["result"]["sources"], [{"title": "BaT", "url": "https://bringatrailer.com/x"}])
        self.assertTrue(any("Searching the web" in a for a in job["activity"]))

    def test_api_error_is_reported_and_turn_dropped(self):
        def boom(**kwargs):
            raise RuntimeError("kaboom")
        self.messages.create = boom
        job_id = json.loads(self.request("/api/ask", {"session_id": "s2", "question": "q"})[1])["job_id"]
        for _ in range(50):
            job = json.loads(self.request(f"/api/jobs/{job_id}")[1])
            if job["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(job["status"], "error")
        self.assertIn("kaboom", job["error"])
        self.assertEqual(self.app.sessions["s2"].messages, [])

    def test_rejects_bad_requests(self):
        self.assertEqual(self.request("/api/ask", {"question": "q"})[0], 400)
        self.assertEqual(self.request("/api/ask", {"session_id": "s", "question": "  "})[0], 400)
        self.assertEqual(self.request("/api/jobs/nope")[0], 404)


if __name__ == "__main__":
    unittest.main()
