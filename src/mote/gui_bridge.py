"""Browser HTTP/JSON bridge fallback for ChromeOS Crostini, headless Linux, and Termux."""

from __future__ import annotations

import importlib.util
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from pathlib import Path
from typing import Any

from mote.constants import is_termux
from mote.context import extract_context_from_file


def has_native_webview_backend(
    platform_str: str | None = None,
    find_spec_fn: Any = None,
) -> bool:
    """Return True if a native desktop pywebview backend is available without errors.

    >>> has_native_webview_backend(platform_str="linux", find_spec_fn=lambda _n: None)
    False
    """
    try:
        import webview as _webview
    except ImportError:
        return False
    if _webview is None:
        return False
    plat = platform_str if platform_str is not None else sys.platform
    if plat == "darwin" or plat.startswith("win") or os.name == "nt":
        return True
    if is_termux():
        return False
    if plat.startswith("linux") and find_spec_fn is None:
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            return False
    spec_fn = find_spec_fn if find_spec_fn is not None else importlib.util.find_spec
    return bool(spec_fn("gi") is not None or spec_fn("qtpy") is not None)


_BROWSER_BRIDGE_SCRIPT = """<script id="steno-browser-bridge">
(function() {
  const STENO_TOKEN = __STENO_BRIDGE_TOKEN__;
  function pickAndUploadBrowserFile(kind, multiple) {
    return new Promise((resolve) => {
      const inp = document.createElement('input');
      inp.type = 'file';
      inp.multiple = !!multiple;
      if (kind === 'audio') {
        inp.accept = '.wav,.m4a,.opus,.mp3,.ogg,.flac,.aac,.mp4,.webm,.html,.htm,.json,.txt,.md,.srt,.vtt';
      } else {
        inp.accept = '.pptx,.pdf,.odp,.docx,.odt,.md,.txt,.html,.json,.csv';
      }
      inp.onchange = async () => {
        if (!inp.files || inp.files.length === 0) {
          resolve(null);
          return;
        }
        try {
          if (kind === 'audio') {
            const f = inp.files[0];
            const buf = await f.arrayBuffer();
            const r = await fetch('/api/_upload_file', {
              method: 'POST',
              headers: {
                'Content-Type': 'application/octet-stream',
                'X-Steno-Token': STENO_TOKEN,
                'X-Steno-Filename': encodeURIComponent(f.name),
                'X-Steno-Kind': 'audio'
              },
              body: buf
            });
            const d = await r.json();
            resolve(d.ok ? d.result : null);
          } else {
            const chunks = [];
            for (const f of inp.files) {
              const buf = await f.arrayBuffer();
              const r = await fetch('/api/_upload_file', {
                method: 'POST',
                headers: {
                  'Content-Type': 'application/octet-stream',
                  'X-Steno-Token': STENO_TOKEN,
                  'X-Steno-Filename': encodeURIComponent(f.name),
                  'X-Steno-Kind': 'context'
                },
                body: buf
              });
              const d = await r.json();
              if (d.ok && d.result && d.result.extracted_text) {
                chunks.push(d.result.extracted_text);
              }
            }
            resolve(chunks.length > 0 ? chunks.join('\\n\\n') : null);
          }
        } catch (err) {
          console.error(err);
          resolve(null);
        }
      };
      inp.click();
    });
  }

  function invokeStructuredCall(code) {
    if (typeof window.__stenoDispatchJsCall === 'function') {
      window.__stenoDispatchJsCall(code);
      return;
    }
    const statements = String(code || '').trim().split(/;\\s*/).filter(Boolean);
    for (const stmt of statements) {
      const match = /^([A-Za-z_][A-Za-z0-9_]*)\\(([\\s\\S]*)\\)$/.exec(stmt);
      if (!match) continue;
      const fn = window[match[1]];
      if (typeof fn !== 'function') continue;
      const rawArgs = match[2].trim();
      if (!rawArgs) {
        fn();
        continue;
      }
      try {
        const parsed = JSON.parse('[' + rawArgs + ']');
        fn(...parsed);
      } catch (_err) {}
    }
  }

  window.pywebview = {
    api: new Proxy({}, {
      get: function(_target, prop) {
        if (prop === 'pick_audio_file') {
          return () => pickAndUploadBrowserFile('audio', false);
        }
        if (prop === 'pick_agenda_file') {
          return () => pickAndUploadBrowserFile('context', true);
        }
        if (prop === 'pick_output_dir') {
          return async (currentAudioPath) => {
            const cur = document.getElementById('out_dir') ? document.getElementById('out_dir').value : '';
            const entered = window.prompt('Output folder path / Mappe for utdatafiler:', cur || '');
            if (!entered) return null;
            const r = await fetch('/api/suggest_output_names', {
              method: 'POST',
              headers: {'Content-Type': 'application/json', 'X-Steno-Token': STENO_TOKEN},
              body: JSON.stringify({args: [currentAudioPath || '', entered]})
            });
            const d = await r.json();
            return d.ok ? d.result : {out_dir: entered};
          };
        }
        return async function(...args) {
          const resp = await fetch('/api/' + String(prop), {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-Steno-Token': STENO_TOKEN},
            body: JSON.stringify({args: args})
          });
          const data = await resp.json();
          if (!data.ok) throw new Error(data.error || 'API call failed');
          return data.result;
        };
      }
    })
  };

  setInterval(async () => {
    try {
      const r = await fetch('/api/_events', {
        headers: {'X-Steno-Token': STENO_TOKEN}
      });
      const d = await r.json();
      if (d && Array.isArray(d.ui_events) && typeof window.__stenoDispatchEvent === 'function') {
        for (const ev of d.ui_events) {
          try { window.__stenoDispatchEvent(ev.type, ev.payload); } catch (e) { console.error(e); }
        }
      }
      if (d && Array.isArray(d.events)) {
        for (const code of d.events) {
          try { invokeStructuredCall(code); } catch (e) { console.error(e); }
        }
      }
    } catch (_e) {}
  }, 100);

  window.addEventListener('DOMContentLoaded', () => {
    document.addEventListener('dragover', (e) => { e.preventDefault(); });
    document.addEventListener('drop', async (e) => {
      e.preventDefault();
      if (!e.dataTransfer || !e.dataTransfer.files || e.dataTransfer.files.length === 0) return;
      const f = e.dataTransfer.files[0];
      const buf = await f.arrayBuffer();
      const r = await fetch('/api/_upload_file', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/octet-stream',
          'X-Steno-Token': STENO_TOKEN,
          'X-Steno-Filename': encodeURIComponent(f.name),
          'X-Steno-Kind': 'drop'
        },
        body: buf
      });
      const d = await r.json();
      if (d.ok && d.result && d.result.local_path && typeof handleDroppedAudio === 'function') {
        handleDroppedAudio(d.result.local_path);
      }
    });
    setTimeout(() => {
      window.dispatchEvent(new Event('pywebviewready'));
    }, 50);
  });
})();
</script>"""


def inject_browser_bridge(html_content: str, auth_token: str = "") -> str:
    """Inject the browser pywebview.api bridge script into app.html."""
    script = _BROWSER_BRIDGE_SCRIPT.replace("__STENO_BRIDGE_TOKEN__", json.dumps(auth_token))
    if "</head>" in html_content:
        return html_content.replace("</head>", script + "\n</head>", 1)
    return script + "\n" + html_content


_ALLOWED_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})


def _extract_hostname_from_host_header(host_header: str) -> str:
    raw = host_header.strip().lower()
    if raw.startswith("["):
        end_idx = raw.find("]")
        return raw[: end_idx + 1] if end_idx != -1 else raw
    return raw.split(":", 1)[0]


class BrowserWindowBridge:
    """Window adapter that queues structured UI events and dispatches authenticated HTTP API requests."""

    def __init__(
        self,
        api: Any = None,
        html_content: str = "",
        auth_token: str | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._js_queue: list[str] = []
        self._ui_events: list[dict[str, Any]] = []
        self.server: Any = None
        self.api = api
        self.auth_token: str = auth_token or secrets.token_urlsafe(32)
        self.bridged_html_bytes = (
            inject_browser_bridge(html_content, auth_token=self.auth_token).encode("utf-8")
            if html_content
            else b""
        )
        self.upload_dir = Path(tempfile.gettempdir()) / "steno_browser_uploads"

    def evaluate_js(self, code: str) -> None:
        with self._lock:
            self._js_queue.append(code)

    def emit_event(self, event_type: str, payload: Any) -> None:
        with self._lock:
            self._ui_events.append({"type": event_type, "payload": payload})

    def drain_js_queue(self) -> list[str]:
        with self._lock:
            items = list(self._js_queue)
            self._js_queue.clear()
            return items

    def drain_ui_events(self) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._ui_events)
            self._ui_events.clear()
            return items

    def create_file_dialog(self, *_args: Any, **_kwargs: Any) -> Any:
        return None

    def destroy(self) -> None:
        srv = self.server
        if srv is not None:
            threading.Thread(target=srv.shutdown, daemon=True).start()

    def handle_http_request(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> tuple[int, str, bytes]:
        """Process a GET or POST request for the browser GUI bridge and return (status, content_type, body_bytes)."""
        hdrs = {k.lower(): v for k, v in (headers or {}).items()}
        if "host" in hdrs:
            host_only = _extract_hostname_from_host_header(hdrs["host"])
            if host_only not in _ALLOWED_LOCAL_HOSTS:
                return 403, "application/json; charset=utf-8", b'{"ok": false, "error": "Forbidden host"}'
        if "origin" in hdrs:
            origin_host = (urllib.parse.urlparse(hdrs["origin"]).hostname or "").lower()
            if origin_host not in {"127.0.0.1", "localhost", "::1"}:
                return 403, "application/json; charset=utf-8", b'{"ok": false, "error": "Forbidden origin"}'

        api = self.api
        req_path = path.split("?", 1)[0]
        if req_path.startswith("/api/"):
            supplied_tok = hdrs.get("x-steno-token", "")
            if not supplied_tok or not secrets.compare_digest(supplied_tok, self.auth_token):
                return (
                    403,
                    "application/json; charset=utf-8",
                    b'{"ok": false, "error": "Unauthorized bridge token"}',
                )

        if method.upper() == "GET":
            if req_path in ("/", "/index.html"):
                return 200, "text/html; charset=utf-8", self.bridged_html_bytes
            if req_path == "/api/_events":
                raw = json.dumps(
                    {
                        "ok": True,
                        "events": self.drain_js_queue(),
                        "ui_events": self.drain_ui_events(),
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
                return 200, "application/json; charset=utf-8", raw
            return 404, "text/plain; charset=utf-8", b"Not Found"

        if method.upper() == "POST" and api is not None:
            if req_path == "/api/_upload_file":
                raw_name = urllib.parse.unquote(hdrs.get("x-steno-filename", "uploaded_file.wav"))
                safe_name = Path(raw_name).name or "uploaded_file.wav"
                kind = hdrs.get("x-steno-kind", "audio")
                out_dir = self.upload_dir
                out_dir.mkdir(parents=True, exist_ok=True)
                dest_path = out_dir / safe_name
                dest_path.write_bytes(body)
                if kind == "context":
                    extracted = extract_context_from_file(dest_path)
                    api._log(f"==> Lastet inn kontekst fra {dest_path.name} ({len(extracted)} tegn)")
                    payload: dict[str, Any] = {
                        "ok": True,
                        "result": {"local_path": str(dest_path), "extracted_text": extracted},
                    }
                else:
                    suggestion = api.suggest_output_names(str(dest_path), str(out_dir))
                    suggestion["local_path"] = str(dest_path)
                    payload = {"ok": True, "result": suggestion}
                return 200, "application/json; charset=utf-8", json.dumps(payload, ensure_ascii=False).encode("utf-8")

            if req_path.startswith("/api/"):
                ctype_hdr = hdrs.get("content-type", "").lower()
                if not ctype_hdr.startswith("application/json"):
                    return (
                        415,
                        "application/json; charset=utf-8",
                        b'{"ok": false, "error": "Unsupported Content-Type"}',
                    )
                method_name = req_path[len("/api/") :].strip()
                if method_name.startswith("_") or not hasattr(api, method_name):
                    err_raw = json.dumps(
                        {"ok": False, "error": f"Unknown API method: {method_name}"}, ensure_ascii=False
                    ).encode("utf-8")
                    return 404, "application/json; charset=utf-8", err_raw
                try:
                    data = json.loads(body.decode("utf-8")) if body else {}
                    args = data.get("args", [])
                    fn = getattr(api, method_name)
                    result = fn(*args)
                    ok_raw = json.dumps({"ok": True, "result": result}, ensure_ascii=False).encode("utf-8")
                    return 200, "application/json; charset=utf-8", ok_raw
                except Exception as exc:
                    exc_raw = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False).encode("utf-8")
                    return 500, "application/json; charset=utf-8", exc_raw

        return 404, "text/plain; charset=utf-8", b"Not Found"


def create_browser_gui_server(
    api: Any,
    html_content: str,
    port: int | None = None,
) -> tuple[Any, BrowserWindowBridge, str]:
    """Create a local 127.0.0.1 HTTP server that serves the Steno GUI in any web browser."""
    import http.server

    bridge = BrowserWindowBridge(api=api, html_content=html_content)
    api.window = bridge

    class StenoBrowserHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def _dispatch(self, method: str) -> None:
            max_len = (
                512 * 1024 * 1024
                if self.path.split("?", 1)[0] == "/api/_upload_file"
                else 10 * 1024 * 1024
            )
            try:
                length = int(self.headers.get("Content-Length", "0") or "0") if method == "POST" else 0
            except ValueError:
                length = 0
            if length < 0 or length > max_len:
                err_b = b'{"ok": false, "error": "Payload Too Large"}'
                self.send_response(413)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(err_b)))
                self.end_headers()
                self.wfile.write(err_b)
                return
            body = self.rfile.read(length) if length > 0 else b""
            hdr_dict = {k: v for k, v in self.headers.items()}
            status, ctype, resp_bytes = bridge.handle_http_request(method, self.path, hdr_dict, body)
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(resp_bytes)

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

    candidate_ports = [port] if port is not None else [8080, 8000, 8888, 3000, 5000, 0]
    srv: Any = None
    for p in candidate_ports:
        try:
            srv = http.server.ThreadingHTTPServer(("127.0.0.1", int(p)), StenoBrowserHandler)
            break
        except OSError:
            continue

    bridge.server = srv
    actual_port = srv.server_address[1] if srv is not None else 8080
    url = f"http://127.0.0.1:{actual_port}"
    return srv, bridge, url


def run_browser_gui(api: Any, html_content: str) -> None:
    """Start the local browser GUI server and open it in the default system/ChromeOS browser."""
    import webbrowser

    srv, _bridge, url = create_browser_gui_server(api, html_content, port=None)
    print(
        f"==> Starting Steno Web UI (ChromeOS / Linux browser mode): {url}\n"
        f"    Open {url} in your browser if it does not open automatically. Press Ctrl+C to quit.",
        flush=True,
    )
    opened = False
    for opener in ("garcon-url-handler", "termux-open-url", "xdg-open"):
        if shutil.which(opener):
            try:
                subprocess.Popen(
                    [opener, url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                opened = True
                break
            except Exception:
                pass
    if not opened:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n==> Shutting down Steno Web UI...", flush=True)
    finally:
        api.shutdown_children()
        srv.server_close()
