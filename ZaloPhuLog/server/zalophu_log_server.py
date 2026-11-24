#!/usr/bin/env python3
"""Log server nhiều dịch vụ cho app ZaloPhu Log (SSE). Chỉ dùng thư viện chuẩn, chạy headless.

- Có file services.json cạnh script  -> dùng đúng danh sách đó:
    {"Bot chính": "tail -n 100 -F /home/userbot/ZaloPhu/bot.log",
     "Nhắc lịch": "journalctl -u nhaclich -f -n 100 --no-pager -o cat"}
- Không có services.json -> tự tìm mọi file *.log trong thư mục Home (mới sửa trong 3 ngày).
"""
import os, json, subprocess, threading, queue, collections, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

HOME = os.path.expanduser("~")
PORT = int(os.getenv("LOG_PORT", "8765"))
TOKEN = os.getenv("LOG_TOKEN", "doi-token-nay")
CONFIG = os.getenv("LOG_CONFIG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "services.json"))
SKIP = {"node_modules", ".cache", ".git", "venv", ".venv", "__pycache__", ".local", "snap", ".npm", ".config", ".vscode-server"}

svcs, glock = {}, threading.Lock()

class Svc:
    def __init__(self, name, cmd):
        self.name, self.cmd = name, cmd
        self.ring = collections.deque(maxlen=100)
        self.clients, self.lock = [], threading.Lock()
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while True:
            try:
                p = subprocess.Popen(self.cmd, shell=True, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, errors="replace")
                for line in p.stdout:
                    line = line.rstrip("\n")
                    if not line:
                        continue
                    self.ring.append(line)
                    with self.lock:
                        for q in self.clients:
                            q.put(line)
            except Exception:
                pass
            time.sleep(3)

def add(name, cmd):
    with glock:
        if name not in svcs:
            svcs[name] = Svc(name, cmd)
            print("+ service:", name, "->", cmd, flush=True)

def discover():
    now = time.time()
    for root, dirs, files in os.walk(HOME):
        dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith(".")]
        if root[len(HOME):].count(os.sep) >= 3:
            dirs[:] = []
        for f in files:
            if f.endswith(".log"):
                p = os.path.join(root, f)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if st.st_size > 0 and now - st.st_mtime < 3 * 86400:
                    add(os.path.relpath(p, HOME), f'tail -n 100 -F "{p}"')

def setup():
    if os.path.exists(CONFIG):
        for name, cmd in json.load(open(CONFIG, encoding="utf-8")).items():
            add(name, cmd)
    else:
        def loop():
            while True:
                discover()
                time.sleep(60)
        threading.Thread(target=loop, daemon=True).start()

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if qs.get("token", [""])[0] != TOKEN:
            self.send_response(401); self.end_headers(); return
        if u.path == "/services":
            body = json.dumps(sorted(svcs.keys()), ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers(); self.wfile.write(body); return
        s = svcs.get(qs.get("svc", [""])[0])
        if u.path != "/stream" or not s:
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        q = queue.Queue()
        with s.lock:
            for l in s.ring: q.put(l)
            s.clients.append(q)
        try:
            while True:
                try:
                    self.wfile.write(f"data: {q.get(timeout=15)}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with s.lock: s.clients.remove(q)

if __name__ == "__main__":
    setup()
    print(f"Log server :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
