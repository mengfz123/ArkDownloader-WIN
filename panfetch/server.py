from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .engine import Manager, Task, new_id
from .fetch import resolve_task
from .paths import APP_NAME, VERSION, chunk_bytes_from_config, load_config, save_config, web_dir

_server: ThreadingHTTPServer | None = None
_server_lock = threading.Lock()


def _cors_headers(handler: BaseHTTPRequestHandler) -> None:
    """允许网页（含公网 HTTPS）通过 fetch 调本机 RPC；配合 Private Network Access。"""
    origin = (handler.headers.get("Origin") or "").strip() or "*"
    handler.send_header("Access-Control-Allow-Origin", origin)
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
    handler.send_header(
        "Access-Control-Allow-Headers",
        "Content-Type, Authorization, X-ArkDownloader-Token, X-PanFetch-Token",
    )
    handler.send_header("Access-Control-Allow-Private-Network", "true")
    handler.send_header("Access-Control-Max-Age", "86400")
    if origin != "*":
        handler.send_header("Vary", "Origin")


def _json_write(handler: BaseHTTPRequestHandler, code: int, data: Any, http: int = 200):
    body = json.dumps({"code": code, "msg": "", "data": data}, ensure_ascii=False).encode("utf-8")
    handler.send_response(http)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    _cors_headers(handler)
    handler.end_headers()
    handler.wfile.write(body)


def _json_err(handler: BaseHTTPRequestHandler, msg: str, http: int = 400, code: int = 1000):
    body = json.dumps({"code": code, "msg": msg, "data": None}, ensure_ascii=False).encode("utf-8")
    handler.send_response(http)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    _cors_headers(handler)
    handler.end_headers()
    handler.wfile.write(body)


_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _normalize_task_headers(raw) -> dict:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        if k is None or v is None:
            continue
        sk, sv = str(k).strip(), str(v).strip()
        if sk and sv:
            out[sk] = sv
    return out


def listen_config(cfg: dict | None = None) -> tuple[str, int]:
    cfg = cfg or load_config()
    host = "0.0.0.0" if cfg.get("rpcRemote") else "127.0.0.1"
    port = int(cfg.get("rpcPort") if cfg.get("rpcPort") is not None else cfg.get("port") or 0)
    return host, port


def server_status() -> dict[str, Any]:
    cfg = load_config()
    token = (cfg.get("rpcToken") or "").strip()
    remote = bool(cfg.get("rpcRemote"))
    if not _server:
        host, port = listen_config(cfg)
        return {
            "running": False,
            "remote": remote,
            "port": port,
            "bindHost": host,
            "localUrl": f"http://127.0.0.1:{port}" if port else "",
            "lanUrl": "",
            "tokenEnabled": bool(token),
        }
    bind_host, port = _server.server_address
    local_url = f"http://127.0.0.1:{port}"
    lan_url = f"http://{_local_ip()}:{port}" if remote else ""
    return {
        "running": True,
        "remote": remote,
        "port": port,
        "bindHost": bind_host,
        "localUrl": local_url,
        "lanUrl": lan_url,
        "tokenEnabled": bool(token),
    }


def _normalize_config(body: dict) -> dict:
    cfg = {**load_config(), **body}
    cfg["connections"] = max(1, min(int(cfg.get("connections") or 8), 32))
    cfg["maxRunning"] = max(1, min(int(cfg.get("maxRunning") or 3), 10))
    from .paths import clamp_chunk_size_mb

    cfg["chunkSizeMb"] = clamp_chunk_size_mb(cfg.get("chunkSizeMb"))
    cfg["rpcPort"] = max(0, min(int(cfg.get("rpcPort") or 0), 65535))
    cfg["userAgent"] = str(cfg.get("userAgent") or "").strip()
    cfg["httpUserAgent"] = str(cfg.get("httpUserAgent") or "").strip()
    cfg["rpcToken"] = str(cfg.get("rpcToken") or "").strip()
    cfg["rpcRemote"] = bool(cfg.get("rpcRemote"))
    return cfg


def _rpc_settings_changed(old: dict, new: dict) -> bool:
    return (
        bool(old.get("rpcRemote")) != bool(new.get("rpcRemote"))
        or int(old.get("rpcPort") or 0) != int(new.get("rpcPort") or 0)
    )


def _check_auth(handler: BaseHTTPRequestHandler) -> bool:
    token = (load_config().get("rpcToken") or "").strip()
    if not token:
        return True
    client = handler.client_address[0]
    if client in ("127.0.0.1", "::1"):
        return True
    auth = handler.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and auth[7:].strip() == token:
        return True
    if handler.headers.get("X-ArkDownloader-Token", "").strip() == token:
        return True
    if handler.headers.get("X-PanFetch-Token", "").strip() == token:
        return True
    return False


def _serve_static(handler: BaseHTTPRequestHandler, path: str) -> bool:
    root = web_dir()
    if path in ("/", "/index.html", "/ui"):
        fp = root / "index.html"
    elif path in ("/ingest", "/ingest.html"):
        fp = root / "ingest.html"
    elif path.startswith("/static/"):
        rel = path[len("/static/") :].lstrip("/")
        if ".." in rel.replace("\\", "/"):
            _json_err(handler, "forbidden", 403)
            return True
        fp = (root / rel).resolve()
        if not str(fp).startswith(str(root.resolve())):
            _json_err(handler, "forbidden", 403)
            return True
    else:
        return False
    if not fp.is_file():
        _json_err(handler, "not found", 404)
        return True
    data = fp.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", _MIME.get(fp.suffix.lower(), "application/octet-stream"))
    handler.send_header("Content-Length", str(len(data)))
    _cors_headers(handler)
    handler.end_headers()
    handler.wfile.write(data)
    return True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print(f"[{APP_NAME}] {fmt % args}")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return {}
        text = raw.decode("utf-8", errors="replace")
        ctype = (self.headers.get("Content-Type") or "").lower()

        def as_form() -> dict:
            qs = urllib.parse.parse_qs(text, keep_blank_values=True)
            return {k: (v[0] if len(v) == 1 else v) for k, v in qs.items()}

        # 网页表单推送（application/x-www-form-urlencoded）
        if "application/x-www-form-urlencoded" in ctype:
            return as_form()

        if "application/json" in ctype or text.lstrip().startswith(("{", "[")):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                form = as_form()
                if form:
                    return form
                raise

        form = as_form()
        if form:
            return form
        return json.loads(text)

    def _require_auth(self) -> bool:
        if _check_auth(self):
            return True
        _json_err(self, "unauthorized", 401, 401)
        return False

    def do_OPTIONS(self):
        self.send_response(204)
        _cors_headers(self)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"
        if path.startswith("/api/") and not self._require_auth():
            return
        mgr = Manager.instance()

        if path in ("/health", "/api/v1/info"):
            cfg = load_config()
            _json_write(
                self,
                0,
                {
                    "name": APP_NAME,
                    "version": VERSION,
                    "downloadDir": cfg.get("downloadDir"),
                    "rpc": server_status(),
                },
            )
            return
        if path == "/api/v1/config":
            _json_write(self, 0, load_config())
            return
        if path == "/api/v1/tasks":
            _json_write(self, 0, [t.to_dict() for t in mgr.list()])
            return
        m = re.match(r"^/api/v1/tasks/([^/]+)$", path)
        if m:
            t = mgr.get(m.group(1))
            if not t:
                _json_err(self, "task not found", 404)
                return
            _json_write(self, 0, t.to_dict())
            return
        if _serve_static(self, path):
            return
        _json_err(self, "not found", 404)

    def do_PUT(self):
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        if path.startswith("/api/") and not self._require_auth():
            return
        mgr = Manager.instance()
        if path == "/api/v1/config":
            try:
                body = self._body()
            except Exception:
                _json_err(self, "invalid json")
                return
            old = load_config()
            cfg = _normalize_config(body)
            save_config(cfg)
            restart = False
            port_changed = int(old.get("rpcPort") or 0) != int(cfg.get("rpcPort") or 0)
            if _rpc_settings_changed(old, cfg):
                try:
                    restart_server(cfg)
                    restart = True
                except Exception as e:
                    save_config(old)
                    _json_err(self, f"RPC 重启失败: {e}")
                    return
            _json_write(
                self,
                0,
                {
                    **cfg,
                    "rpcStatus": server_status(),
                    "rpcRestarted": restart,
                    "needsAppRestart": port_changed and restart,
                },
            )
            return
        if path == "/api/v1/tasks/pause":
            mgr.pause_all()
            _json_write(self, 0, True)
            return
        if path == "/api/v1/tasks/continue":
            mgr.resume_all()
            _json_write(self, 0, True)
            return
        m = re.match(r"^/api/v1/tasks/([^/]+)/(pause|continue)$", path)
        if m:
            t = mgr.get(m.group(1))
            if not t:
                _json_err(self, "task not found", 404)
                return
            if m.group(2) == "pause":
                t.pause()
            else:
                t.resume()
            mgr.persist()
            _json_write(self, 0, t.id)
            return
        _json_err(self, "not found", 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        if path.startswith("/api/") and not self._require_auth():
            return
        mgr = Manager.instance()
        try:
            body = self._body()
        except Exception as e:
            _json_err(self, f"invalid body: {e}")
            return

        if path == "/api/v1/resolve":
            url = (body.get("url") or "").strip()
            if not url:
                _json_err(self, "url required")
                return
            try:
                u, size, name, kind = resolve_task(url, body.get("name") or "")
            except Exception as e:
                _json_err(self, str(e))
                return
            _json_write(self, 0, {"url": u, "size": size, "name": name, "kind": kind})
            return

        if path == "/api/v1/tasks":
            url = (body.get("url") or body.get("req", {}).get("url") or "").strip()
            if not url:
                _json_err(self, "url required")
                return
            cfg = load_config()
            opts = body.get("opts") or body.get("opt") or {}
            # 兼容网页推送别名：save_path / file_name / threads
            dir_path = (
                opts.get("path")
                or body.get("dir")
                or body.get("save_path")
                or cfg.get("downloadDir")
            )
            name = opts.get("name") or body.get("name") or body.get("file_name") or ""
            conn = int(
                (opts.get("extra") or {}).get("connections")
                or body.get("connections")
                or body.get("threads")
                or cfg.get("connections", 8)
            )
            try:
                size_hint = int(body.get("size") or body.get("total_size") or 0)
            except Exception:
                size_hint = 0
            headers = _normalize_task_headers(
                body.get("headers") or (opts.get("extra") or {}).get("headers")
            )
            try:
                u, size, fname, kind = resolve_task(url, name, size_hint, headers)
            except Exception as e:
                _json_err(self, str(e))
                return
            task = Task(
                id=new_id(),
                url=u,
                dir=str(dir_path),
                name=fname,
                size=size,
                kind=kind,
                connections=max(1, min(conn, 32)),
                chunk_size=chunk_bytes_from_config(cfg.get("chunkSizeMb"), kind),
                headers=headers,
            )
            mgr.add(task)
            _json_write(self, 0, task.id)
            return

        if path == "/api/v1/tasks/batch":
            ids = []
            errors = []
            cfg = load_config()
            for item in body.get("reqs") or body.get("tasks") or []:
                url = (item.get("req") or {}).get("url") or item.get("url") or ""
                url = url.strip()
                if not url:
                    continue
                opts = item.get("opts") or {}
                dir_path = opts.get("path") or item.get("dir") or cfg.get("downloadDir")
                name = opts.get("name") or item.get("name") or ""
                conn = int(
                    (opts.get("extra") or {}).get("connections")
                    or item.get("connections")
                    or cfg.get("connections", 8)
                )
                try:
                    size_hint = int(item.get("size") or 0)
                except Exception:
                    size_hint = 0
                headers = _normalize_task_headers(
                    item.get("headers") or (opts.get("extra") or {}).get("headers")
                )
                try:
                    u, size, fname, kind = resolve_task(url, name, size_hint, headers)
                    task = Task(
                        id=new_id(),
                        url=u,
                        dir=str(dir_path),
                        name=fname,
                        size=size,
                        kind=kind,
                        connections=max(1, min(conn, 32)),
                        chunk_size=chunk_bytes_from_config(cfg.get("chunkSizeMb"), kind),
                        headers=headers,
                    )
                    mgr.add(task)
                    ids.append(task.id)
                except Exception as e:
                    errors.append({"url": url[:80], "error": str(e)})
            if not ids and errors:
                _json_err(self, errors[0]["error"])
                return
            _json_write(self, 0, {"ids": ids, "errors": errors})
            return

        if path == "/api/v1/open-folder":
            target = body.get("path") or body.get("dir") or ""
            if target and Path(target).exists():
                os.startfile(str(Path(target).parent if Path(target).is_file() else target))  # type: ignore
            _json_write(self, 0, True)
            return

        if path == "/api/v1/tasks/clear-completed":
            mgr.clear_completed()
            _json_write(self, 0, True)
            return

        _json_err(self, "not found", 404)

    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        if path.startswith("/api/") and not self._require_auth():
            return
        parsed = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(parsed.query)
        path = parsed.path.rstrip("/")
        mgr = Manager.instance()
        if path == "/api/v1/tasks":
            mgr.clear_completed()
            _json_write(self, 0, True)
            return
        tid = (q.get("id") or [None])[0]
        if not tid:
            m = re.match(r"^/api/v1/tasks/([^/]+)$", path)
            tid = m.group(1) if m else None
        if not tid:
            _json_err(self, "id required")
            return
        delete_file = (q.get("file") or ["false"])[0].lower() in ("1", "true", "yes")
        mgr.remove(tid, delete_file=delete_file)
        _json_write(self, 0, True)


def restart_server(cfg: dict | None = None) -> int:
    global _server
    host, port = listen_config(cfg)
    with _server_lock:
        if _server:
            old_host, old_port = _server.server_address
            if old_host == host and old_port == port and port != 0:
                return old_port
            _server.shutdown()
            _server.server_close()
            _server = None
        httpd = ThreadingHTTPServer((host, port), Handler)
        _server = httpd
        actual = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True, name="panfetch-http").start()
        print(f"[{APP_NAME}] RPC listening on {host}:{actual}")
        return actual


def start_server(host: str | None = None, port: int | None = None) -> int:
    global _server
    with _server_lock:
        if _server:
            return _server.server_address[1]
        cfg = load_config()
        if host is None or port is None:
            cfg_host, cfg_port = listen_config(cfg)
            host = host if host is not None else cfg_host
            port = port if port is not None else cfg_port
        try:
            httpd = ThreadingHTTPServer((host, port), Handler)
        except OSError as e:
            if port and port > 0:
                raise OSError(
                    f"端口 {port} 已被占用（请关闭其他 {APP_NAME} 实例或修改 RPC 端口）"
                ) from e
            raise
        _server = httpd
        actual = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True, name="panfetch-http").start()
        print(f"[{APP_NAME}] RPC listening on {host}:{actual}")
        return actual


def stop_server():
    global _server
    with _server_lock:
        if _server:
            _server.shutdown()
            _server.server_close()
            _server = None
