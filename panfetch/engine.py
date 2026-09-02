from __future__ import annotations

import json
import math
import os
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .fetch import is_baidu_url, normalize_url
from .paths import CHUNK_BAIDU, get_user_agent, load_config, tasks_path


class FetchAborted(Exception):
    """暂停/停止时中断正在读取的分片。"""


def new_id() -> str:
    return f"{int(time.time() * 1000):x}{os.getpid():x}"[-16:]


def sidecar_path(out_file: Path) -> Path:
    return out_file.with_suffix(out_file.suffix + ".panfetch.json")


# 仅持久化可 JSON 序列化的字段（排除线程锁等运行时状态）
_PERSIST_FIELDS = (
    "id", "url", "dir", "name", "size", "kind", "connections", "chunk_size", "headers",
    "status", "error", "completed_chunks", "downloaded", "created_at", "updated_at",
)


@dataclass
class Task:
    id: str
    url: str
    dir: str
    name: str
    size: int
    kind: str = "baidu"  # baidu | http
    connections: int = 8
    chunk_size: int = 0  # 字节；0 表示按旧规则推导（兼容历史任务）
    headers: dict = field(default_factory=dict)
    status: str = "pending"
    error: str = ""
    completed_chunks: list[int] = field(default_factory=list)
    downloaded: int = 0
    speed: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    _pause: threading.Event = field(default_factory=threading.Event, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)
    _speed_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _speed_bytes: int = field(default=0, repr=False)
    _speed_ts: float = field(default_factory=time.time, repr=False)

    @property
    def out_file(self) -> Path:
        return Path(self.dir) / self.name

    def resolved_chunk_size(self) -> int:
        if self.chunk_size and self.chunk_size > 0:
            return int(self.chunk_size)
        # 历史任务：百度固定 5MiB；HTTP 按连接数估算
        if self.kind == "baidu":
            return CHUNK_BAIDU
        return max(
            1024 * 1024,
            min(8 * 1024 * 1024, self.size // max(self.connections * 2, 1) or 1),
        )

    @property
    def chunk_count(self) -> int:
        cs = self.resolved_chunk_size()
        return math.ceil(self.size / cs) if self.size and cs else 0

    def chunk_range(self, index: int) -> tuple[int, int]:
        cs = self.resolved_chunk_size()
        start = index * cs
        end = min(start + cs - 1, self.size - 1)
        return start, end

    def chunk_len(self, index: int) -> int:
        s, e = self.chunk_range(index)
        return e - s + 1

    @property
    def completed_set(self) -> set[int]:
        return set(self.completed_chunks)

    def touch(self):
        self.updated_at = time.time()

    def add_bytes(self, n: int):
        with self._speed_lock:
            self.downloaded += n
            self._speed_bytes += n
            now = time.time()
            if now - self._speed_ts >= 1.0:
                self.speed = int(self._speed_bytes / (now - self._speed_ts))
                self._speed_bytes = 0
                self._speed_ts = now
        self.touch()

    def save_sidecar(self):
        sc = sidecar_path(self.out_file)
        sc.write_text(
            json.dumps(
                {
                    "id": self.id,
                    "url": self.url,
                    "name": self.name,
                    "size": self.size,
                    "kind": self.kind,
                    "completed_chunks": sorted(self.completed_set),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def load_sidecar(self) -> bool:
        sc = sidecar_path(self.out_file)
        if not sc.exists():
            return False
        try:
            data = json.loads(sc.read_text(encoding="utf-8"))
            if data.get("size") != self.size or data.get("url") != self.url:
                return False
            self.completed_chunks = list(data.get("completed_chunks") or [])
            self.downloaded = sum(self.chunk_len(i) for i in self.completed_set)
            return True
        except Exception:
            return False

    def to_dict(self) -> dict[str, Any]:
        pct = round(self.downloaded / self.size * 100, 2) if self.size else 0
        return {
            "id": self.id,
            "name": self.name,
            "path": self.dir,
            "url": self.url,
            "size": self.size,
            "kind": self.kind,
            "status": self.status,
            "connections": self.connections,
            "chunkSize": self.resolved_chunk_size(),
            "downloaded": self.downloaded,
            "speed": self.speed if self.status == "running" else 0,
            "progress": pct,
            "chunks": {"total": self.chunk_count, "done": len(self.completed_set)},
            "error": self.error,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "out": str(self.out_file),
        }

    def _fetch_range(self, start: int, end: int) -> bytes:
        expect = end - start + 1
        if self.kind == "baidu" or is_baidu_url(self.url):
            hdr = {
                "User-Agent": get_user_agent("baidu"),
                "Connection": "Keep-Alive",
                "Accept-Encoding": "identity",
                "Accept-Language": "zh-CN",
                "Range": f"bytes={start}-{end}",
            }
        else:
            hdr = {
                "User-Agent": get_user_agent("http"),
                "Accept-Encoding": "identity",
                "Range": f"bytes={start}-{end}",
            }
        if isinstance(self.headers, dict):
            for k, v in self.headers.items():
                if k and v is not None and str(v).strip():
                    hdr[str(k)] = str(v)
        req = urllib.request.Request(self.url, headers=hdr, method="GET")
        buf = bytearray()
        with urllib.request.urlopen(req, timeout=120) as resp:
            while len(buf) < expect:
                if self._stop.is_set() or self._pause.is_set():
                    raise FetchAborted()
                block = resp.read(min(256 * 1024, expect - len(buf)))
                if not block:
                    break
                buf.extend(block)
        if len(buf) != expect:
            raise RuntimeError(f"分片长度不符 {len(buf)} != {expect}")
        return bytes(buf)

    def _ensure_outfile(self):
        """创建/对齐输出文件长度，不截断已有内容（修复暂停后重开任务清空文件）。"""
        path = self.out_file
        if path.exists():
            with open(path, "r+b") as f:
                f.seek(0, os.SEEK_END)
                cur = f.tell()
                if cur != self.size:
                    f.truncate(self.size)
        else:
            with open(path, "wb") as f:
                f.truncate(self.size)

    def run(self):
        try:
            self.status = "running"
            self.touch()
            Path(self.dir).mkdir(parents=True, exist_ok=True)
            self.load_sidecar()
            self._ensure_outfile()

            pending = [i for i in range(self.chunk_count) if i not in self.completed_set]
            if not pending:
                self.status = "done"
                self.save_sidecar()
                return

            lock = threading.Lock()
            err: list[Exception] = []

            def worker():
                while True:
                    if self._stop.is_set():
                        return
                    if self._pause.is_set():
                        time.sleep(0.15)
                        continue
                    with lock:
                        if err or not pending:
                            return
                        idx = pending.pop(0)
                    s, e = self.chunk_range(idx)
                    try:
                        data = self._fetch_range(s, e)
                        if self._stop.is_set() or self._pause.is_set():
                            # 读完但已暂停：仍写入本片，避免浪费；下一轮停住
                            pass
                        with open(self.out_file, "r+b") as f:
                            f.seek(s)
                            f.write(data)
                        with lock:
                            if idx not in self.completed_chunks:
                                self.completed_chunks.append(idx)
                            self.add_bytes(len(data))
                            self.save_sidecar()
                    except FetchAborted:
                        with lock:
                            pending.insert(0, idx)
                        continue
                    except Exception as ex:
                        with lock:
                            if not err:
                                err.append(ex)
                            pending.insert(0, idx)
                        return

            threads = [
                threading.Thread(target=worker, daemon=True, name=f"w-{self.id}-{n}")
                for n in range(max(1, self.connections))
            ]
            for t in threads:
                t.start()
            while any(t.is_alive() for t in threads):
                if self._stop.is_set():
                    self.status = "paused"
                    self.speed = 0
                    self.touch()
                    return
                if self._pause.is_set():
                    self.status = "paused"
                    self.speed = 0
                time.sleep(0.25)
            if self._stop.is_set() or self._pause.is_set():
                self.status = "paused"
                self.speed = 0
                self.touch()
                return
            if err:
                raise err[0]
            if self.out_file.stat().st_size != self.size:
                raise RuntimeError("文件大小校验失败")
            self.status = "done"
            self.speed = 0
            self.save_sidecar()
        except Exception as ex:
            self.status = "error"
            self.error = str(ex)
            self.speed = 0
            if self.downloaded == 0 and self.out_file.exists():
                try:
                    self.out_file.unlink()
                except Exception:
                    pass
        finally:
            self.touch()
            Manager.instance().on_task_finished(self)

    def start_async(self):
        if self._thread and self._thread.is_alive():
            # 暂停后恢复：线程仍在，只需清除暂停标志
            self._pause.clear()
            self._stop.clear()
            if self.status == "paused":
                self.status = "running"
                self.touch()
            return
        self._pause.clear()
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, daemon=True, name=f"task-{self.id}")
        self._thread.start()

    def pause(self):
        self._pause.set()
        self.speed = 0
        with self._speed_lock:
            self._speed_bytes = 0
            self._speed_ts = time.time()
        if self.status == "running":
            self.status = "paused"
            self.touch()

    def resume(self):
        if self.status == "done":
            return
        self._pause.clear()
        self._stop.clear()
        self.error = ""
        if self._thread and self._thread.is_alive():
            self.status = "running"
            self.touch()
            return
        self.status = "pending"
        Manager.instance().schedule(self)

    def cancel(self):
        self._stop.set()
        self._pause.set()


class Manager:
    _inst: Manager | None = None
    _lock = threading.Lock()

    def __init__(self):
        self.tasks: dict[str, Task] = {}
        self.mu = threading.Lock()
        self._load_tasks()

    @classmethod
    def instance(cls) -> Manager:
        with cls._lock:
            if cls._inst is None:
                cls._inst = Manager()
            return cls._inst

    def _load_tasks(self):
        p = tasks_path()
        if not p.exists():
            return
        try:
            rows = json.loads(p.read_text(encoding="utf-8"))
            for row in rows:
                t = Task(**{k: v for k, v in row.items() if k in _PERSIST_FIELDS})
                if t.status in ("running", "pending"):
                    t.status = "paused"
                self.tasks[t.id] = t
        except Exception:
            pass

    def persist(self):
        rows = [{k: getattr(t, k) for k in _PERSIST_FIELDS} for t in self.tasks.values()]
        tasks_path().write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, task: Task, auto_start: bool | None = None) -> Task:
        cfg = load_config()
        if auto_start is None:
            auto_start = cfg.get("autoStart", True)
        with self.mu:
            self.tasks[task.id] = task
            self.persist()
        if auto_start:
            self.schedule(task)
        return task

    def schedule(self, task: Task):
        cfg = load_config()
        max_run = int(cfg.get("maxRunning", 3))
        with self.mu:
            running = sum(1 for t in self.tasks.values() if t.status == "running")
        if running >= max_run:
            task.status = "pending"
            task.touch()
            self.persist()
            return
        task.start_async()

    def on_task_finished(self, task: Task):
        self.persist()
        cfg = load_config()
        max_run = int(cfg.get("maxRunning", 3))
        with self.mu:
            running = sum(1 for t in self.tasks.values() if t.status == "running")
            waiting = [t for t in self.tasks.values() if t.status == "pending"]
        for t in waiting:
            if running >= max_run:
                break
            self.schedule(t)
            running += 1

    def get(self, tid: str) -> Task | None:
        return self.tasks.get(tid)

    def list(self) -> list[Task]:
        return sorted(self.tasks.values(), key=lambda t: t.created_at, reverse=True)

    def remove(self, tid: str, delete_file: bool = False) -> bool:
        t = self.tasks.pop(tid, None)
        if not t:
            return False
        t.cancel()
        if delete_file and t.out_file.exists():
            try:
                t.out_file.unlink()
            except Exception:
                pass
        sc = sidecar_path(t.out_file)
        if sc.exists():
            try:
                sc.unlink()
            except Exception:
                pass
        self.persist()
        return True

    def pause_all(self):
        for t in self.tasks.values():
            if t.status == "running":
                t.pause()
        self.persist()

    def resume_all(self):
        for t in self.tasks.values():
            if t.status in ("paused", "error", "pending"):
                t.resume()
        self.persist()

    def clear_completed(self):
        done = [tid for tid, t in self.tasks.items() if t.status == "done"]
        for tid in done:
            self.remove(tid)
