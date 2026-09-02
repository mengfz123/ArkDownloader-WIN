from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "ArkDownloader"
VERSION = "1.0.6"
CHUNK_BAIDU = 5 * 1024 * 1024
CHUNK_SIZE_MB_MIN = 1
CHUNK_SIZE_MB_MAX = 5
CHUNK_SIZE_MB_DEFAULT = 1
BAIDU_UA = (
    "netdisk;P2SP;3.0.20.233;netdisk;8.7.9.102;"
    "PC;PC-Windows;10.0.19045;WindowsBaiduYunGuanJia"
)


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def web_dir() -> Path:
    return app_dir() / "web"


def data_dir() -> Path:
    base = Path(os.environ.get("APPDATA") or Path.home())
    new_p = base / APP_NAME
    legacy_p = base / "PanFetch"
    if new_p.exists():
        p = new_p
    elif legacy_p.exists():
        p = legacy_p
    else:
        p = new_p
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_path() -> Path:
    return data_dir() / "config.json"


def tasks_path() -> Path:
    return data_dir() / "tasks.json"


DEFAULT_CONFIG = {
    "downloadDir": str(Path.home() / "Downloads"),
    "connections": 8,
    "maxRunning": 3,
    "chunkSizeMb": CHUNK_SIZE_MB_DEFAULT,
    "autoStart": True,
    "notifyOnComplete": True,
    "port": 0,  # legacy; prefer rpcPort
    "userAgent": BAIDU_UA,
    "httpUserAgent": "ArkDownloader/1.0",
    "rpcPort": 18766,
    "rpcRemote": False,
    "rpcToken": "",
}


def clamp_chunk_size_mb(n) -> int:
    try:
        v = int(n)
    except Exception:
        v = CHUNK_SIZE_MB_DEFAULT
    return max(CHUNK_SIZE_MB_MIN, min(CHUNK_SIZE_MB_MAX, v))


def chunk_bytes_from_config(chunk_size_mb=None, kind: str = "http") -> int:
    mb = clamp_chunk_size_mb(
        chunk_size_mb if chunk_size_mb is not None else load_config().get("chunkSizeMb")
    )
    cs = mb * 1024 * 1024
    # 百度直链单片不超过 5MiB
    if kind == "baidu":
        cs = min(cs, CHUNK_BAIDU)
    return max(1024 * 1024, cs)


def get_user_agent(kind: str = "baidu") -> str:
    cfg = load_config()
    if kind == "baidu":
        return (cfg.get("userAgent") or BAIDU_UA).strip() or BAIDU_UA
    return (cfg.get("httpUserAgent") or "ArkDownloader/1.0").strip() or "ArkDownloader/1.0"


def load_config() -> dict:
    p = config_path()
    if p.exists():
        try:
            cfg = {**DEFAULT_CONFIG, **json.loads(p.read_text(encoding="utf-8"))}
            cfg["chunkSizeMb"] = clamp_chunk_size_mb(cfg.get("chunkSizeMb"))
            return cfg
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict) -> None:
    cfg = {**cfg}
    cfg["chunkSizeMb"] = clamp_chunk_size_mb(cfg.get("chunkSizeMb"))
    config_path().write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
