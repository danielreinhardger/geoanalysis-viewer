"""Open a page in a real (windowed) Chrome with a throwaway profile and no network, then read its
state and console through the DevTools protocol. Standard library only.

  python -I tools/check_page.py file:///<repo>/exports/G2_3d.html [screenshot.png] [js-expression]

Meant for file:// pages; an http page makes Chrome swap processes on navigation and the
connection drops (check those in a normal browser instead). Prints STATE (viewer ready, title,
loading notice), REQUESTS (should be empty for the single file), and any exception, console error
or warning. Use it for the three.js viewer: headless Chrome cannot run it (its software GPU fails
on the scene and the page stays at "loading"). The throwaway profile lives in
%TEMP%\\geoanalysis_viewer_cdp_profile. Set CHROME to the browser executable if it is elsewhere."""
import base64, json, os, socket, struct, subprocess, sys, time, urllib.request

URL = sys.argv[1]
SHOT = sys.argv[2] if len(sys.argv) > 2 else None
EXPR = sys.argv[3] if len(sys.argv) > 3 else None   # optional JS expression, result printed as EVAL
PORT = 9333
prof = os.path.join(os.environ.get("TEMP", os.path.dirname(__file__)), "geoanalysis_viewer_cdp_profile")
chrome = os.environ.get("CHROME", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
p = subprocess.Popen([chrome, f"--remote-debugging-port={PORT}", f"--user-data-dir={prof}", "--no-first-run",
                      "--no-default-browser-check", "--host-resolver-rules=MAP * ~NOTFOUND", "--window-size=1400,900",
                      "about:blank"])
try:
    for _ in range(50):
        try:
            tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list"))
            page = next(t for t in tabs if t["type"] == "page")
            break
        except Exception:
            time.sleep(0.2)
    ws_url = page["webSocketDebuggerUrl"]
    host, path = ws_url[len("ws://"):].split("/", 1)
    h, pt = host.split(":")
    s = socket.create_connection((h, int(pt)))
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall(f"GET /{path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
              f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += s.recv(4096)
    rest = buf.split(b"\r\n\r\n", 1)[1]

    def send(obj):
        data = json.dumps(obj).encode()
        mask = os.urandom(4)
        hdr = bytes([0x81])
        n = len(data)
        hdr += bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 else bytes([0x80 | 127]) + struct.pack(">Q", n)
        s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv_exact(n):
        nonlocal_buf = recv_exact.buf
        while len(nonlocal_buf) < n:
            chunk = s.recv(1 << 20)
            if not chunk:
                raise EOFError
            nonlocal_buf += chunk
        out, recv_exact.buf = nonlocal_buf[:n], nonlocal_buf[n:]
        return out
    recv_exact.buf = rest

    def recv():
        b0, b1 = recv_exact(2)
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", recv_exact(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", recv_exact(8))[0]
        return json.loads(recv_exact(n))

    events, mid = [], [0]

    def call(method, params=None, timeout=60):
        mid[0] += 1
        send({"id": mid[0], "method": method, "params": params or {}})
        t0 = time.time()
        while time.time() - t0 < timeout:
            m = recv()
            if m.get("id") == mid[0]:
                return m
            events.append(m)
        raise TimeoutError(method)

    call("Runtime.enable"); call("Log.enable"); call("Page.enable")
    call("Page.navigate", {"url": URL})
    t0 = time.time()
    state = None
    while time.time() - t0 < 40:
        time.sleep(1)
        r = call("Runtime.evaluate", {"expression": "JSON.stringify({viewer: !!window.viewer, title: (document.getElementById('title')||{}).textContent, loading: (document.getElementById('loading')||{}).textContent || null, ready: window.viewer && window.viewer.marks})", "returnByValue": True})
        state = json.loads(r["result"]["result"]["value"])
        if state["viewer"] or (state["loading"] and state["loading"].startswith("Fehler")):
            break
    print("STATE", json.dumps(state, ensure_ascii=False))
    if EXPR:   # before the screenshot, so the expression can set up the view
        r = call("Runtime.evaluate", {"expression": f"JSON.stringify({EXPR})", "returnByValue": True})
        print("EVAL", r["result"]["result"].get("value"))
    if SHOT and state and state["viewer"]:
        time.sleep(2)
        r = call("Page.captureScreenshot", {"format": "png"})
        open(SHOT, "wb").write(base64.b64decode(r["result"]["data"]))
    r = call("Runtime.evaluate", {"expression": "JSON.stringify(performance.getEntriesByType('resource').map(e => e.name.slice(0, 60)))", "returnByValue": True})
    print("REQUESTS", r["result"]["result"]["value"][:400])
    for e in events:
        m = e.get("method")
        if m == "Runtime.exceptionThrown":
            d = e["params"]["exceptionDetails"]
            print("EXCEPTION", d.get("text"), (d.get("exception") or {}).get("description", "")[:300])
        elif m == "Runtime.consoleAPICalled" and e["params"]["type"] in ("error", "warning"):
            print("CONSOLE", e["params"]["type"], " ".join(str(a.get("value", a.get("description", "")))[:200] for a in e["params"]["args"]))
        elif m == "Log.entryAdded" and e["params"]["entry"]["level"] in ("error", "warning"):
            print("LOG", e["params"]["entry"]["level"], e["params"]["entry"]["text"][:300])
finally:
    p.terminate()
