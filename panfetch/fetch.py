from __future__ import annotations

import re
import urllib.parse
import urllib.request

from .paths import CHUNK_BAIDU, get_user_agent


def normalize_url(url: str) -> str:
    return url.strip().replace("htype=&randtype=", "htype&randtype")


def is_baidu_url(url: str) -> bool:
    u = url.lower()
    if not any(h in u for h in ("baidupcs.com", "antpcdn.com", "jomodns.com", "pcs.baidu.com")):
        return False
    return "size=" in u and "/file/" in u


def parse_fin(url: str) -> str:
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    fin = (q.get("fin") or [""])[0]
    if fin:
        return urllib.parse.unquote(fin).replace("+", " ")
    return ""


def parse_baidu_size(url: str) -> int:
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    try:
        return int((q.get("size") or ["0"])[0] or 0)
    except Exception:
        return 0


def baidu_fetch(url: str, start: int, end: int, headers: dict | None = None) -> bytes:
    hdr = {
        "User-Agent": get_user_agent("baidu"),
        "Connection": "Keep-Alive",
        "Accept-Encoding": "identity",
        "Accept-Language": "zh-CN",
        "Range": f"bytes={start}-{end}",
    }
    # 任务级 Cookie / UA（CloudDrive 推送的 cdnCookie/cdnUa）覆盖默认值
    if isinstance(headers, dict):
        for k, v in headers.items():
            if k and v is not None and str(v).strip():
                hdr[str(k)] = str(v)
    req = urllib.request.Request(
        url,
        headers=hdr,
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    if len(data) != end - start + 1:
        raise RuntimeError(f"百度分片长度不符 {len(data)} != {end - start + 1}")
    return data


def probe_http(url: str, headers: dict | None = None) -> tuple[int, str, bool]:
    """返回 (size, suggested_name, supports_range)。"""
    hdr = {"User-Agent": get_user_agent("http"), "Accept-Encoding": "identity"}
    if headers:
        hdr.update(headers)
    req = urllib.request.Request(url, headers={**hdr, "Range": "bytes=0-0"}, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        code = resp.status
        cr = resp.headers.get("Content-Range", "")
        cl = resp.headers.get("Content-Length")
        cd = resp.headers.get("Content-Disposition", "")
        name = ""
        if cd:
            m = re.search(r"filename\*=UTF-8''([^;]+)|filename=\"?([^\";]+)", cd, re.I)
            if m:
                name = urllib.parse.unquote(m.group(1) or m.group(2) or "")
        if not name:
            name = urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit("/", 1)[-1])
        size = 0
        supports = False
        if cr.startswith("bytes "):
            supports = True
            m = re.search(r"/(\d+)$", cr)
            if m:
                size = int(m.group(1))
        elif cl:
            size = int(cl)
        if code == 200 and size == 0 and cl:
            size = int(cl)
        return size, name or "download.bin", supports


def resolve_task(
    url: str, name: str = "", size_hint: int = 0, headers: dict | None = None
) -> tuple[str, int, str, str]:
    """返回 url, size, name, kind(baidu|http)。

    size_hint>0 且已提供文件名时跳过 HTTP 探测（避免一次性代理链被 Range 探测误杀/耗尽）。
    """
    url = normalize_url(url)
    if is_baidu_url(url):
        size = parse_baidu_size(url) or int(size_hint or 0)
        if size <= 0:
            raise ValueError("百度直链缺少 size= 参数")
        fname = name.strip() or parse_fin(url) or "baidu_download.bin"
        return url, size, fname, "baidu"
    hint = int(size_hint or 0)
    if hint > 0 and name.strip():
        return url, hint, name.strip(), "http"
    try:
        size, suggested, _ = probe_http(url, headers=headers)
    except Exception:
        raise
    if size <= 0:
        size = hint
    if size <= 0:
        raise ValueError("无法获取文件大小，请检查链接是否有效")
    fname = name.strip() or suggested
    return url, size, fname, "http"
