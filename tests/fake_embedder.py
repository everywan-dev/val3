"""Stand-in for the embedding service in tests: Ollama's POST /api/embed with bag-of-words vectors.

Each word (lowercase, \\w+) adds 1 to one of DIMENSIONS buckets chosen by its SHA-256, so texts that
share words score a high cosine and texts that share none score about 0. A text without words maps
to the first axis, never to a zero vector.
"""
import hashlib
import json
import math
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIMENSIONS = 256


def vector(text: str) -> list[float]:
    values = [0.0] * DIMENSIONS
    for word in re.findall(r"\w+", text.lower()):
        values[int(hashlib.sha256(word.encode()).hexdigest()[:8], 16) % DIMENSIONS] += 1
    if not any(values):
        values[0] = 1.0
    norm = math.sqrt(sum(x * x for x in values))
    return [x / norm for x in values]


class FakeEmbedder:
    def __init__(self):
        self.requests = []  # request bodies, in order
        self.inputs = []    # every text received, in order
        self.delay = 0.0    # seconds to wait before answering
        self.tags_delay = 0.0   # seconds to wait before answering GET /api/tags
        self.status = 200   # answer with this HTTP status when not 200
        self.zero = False   # answer with zero vectors
        self.digest = "a" * 64  # digest of bge-m3 listed by GET /api/tags
        self.hold = None    # a threading.Event: the next POST waits until it is set
        self.trickle = False  # POST sends its headers, then one body byte a second for a minute: no socket timeout
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def _json(self, payload):
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # /api/tags
                time.sleep(owner.tags_delay)
                self._json({"models": [{"name": "bge-m3:latest", "model": "bge-m3:latest", "digest": owner.digest}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                texts = body["input"] if isinstance(body["input"], list) else [body["input"]]
                owner.requests.append(body)
                owner.inputs.extend(texts)
                hold, owner.hold = owner.hold, None
                if hold is not None:
                    hold.wait(60)
                time.sleep(owner.delay)
                if owner.trickle:
                    self.send_response(200)
                    self.send_header("Content-Length", "100")
                    self.end_headers()
                    try:
                        for _ in range(60):
                            self.wfile.write(b" ")
                            self.wfile.flush()
                            time.sleep(1)
                    except OSError:
                        pass
                    return
                if owner.status != 200:
                    self.send_response(owner.status)
                    self.end_headers()
                    return
                vectors = [[0.0] * DIMENSIONS if owner.zero else vector(text) for text in texts]
                self._json({"model": body["model"], "embeddings": vectors})

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
