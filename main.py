"""ArkDownloader 桌面入口 — pywebview 窗口 + 内置服务"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import threading
import time
import webbrowser

from panfetch.paths import APP_NAME, VERSION, load_config
from panfetch.server import start_server, stop_server


def run_desktop(port: int = 0):
    try:
        import webview
    except ImportError:
        print("请先安装: pip install pywebview")
        sys.exit(1)

    try:
        if port:
            actual = start_server(None, port)
        else:
            actual = start_server()
    except OSError as e:
        cfg = load_config()
        rpc_port = cfg.get("rpcPort", 18766)
        print(f"[ERROR] 无法绑定 RPC 端口 {rpc_port}: {e}")
        print(f"请关闭已运行的 {APP_NAME}，或在设置中修改 RPC 端口后重试。")
        sys.exit(1)

    url = f"http://127.0.0.1:{actual}/"
    print(f"{APP_NAME} v{VERSION} → {url}")

    window = webview.create_window(
        APP_NAME,
        url,
        width=1100,
        height=720,
        min_size=(900, 560),
        text_select=True,
    )

    def on_closed():
        stop_server()

    window.events.closed += on_closed

    errors: list[str] = []
    for gui in ("edgechromium", "mshtml", None):
        try:
            if gui:
                webview.start(gui=gui)
            else:
                webview.start()
            return
        except Exception as ex:
            errors.append(f"{gui or 'default'}: {ex}")

    print("[ERROR] 无法创建桌面窗口:")
    for line in errors:
        print(f"  - {line}")
    print("可改用浏览器模式: python main.py --browser")
    stop_server()
    sys.exit(1)


def run_browser(port: int = 0):
    if port:
        actual = start_server(None, port)
    else:
        actual = start_server()
    url = f"http://127.0.0.1:{actual}/"
    print(f"{APP_NAME} → {url}")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop_server()


def main():
    p = argparse.ArgumentParser(description=f"{APP_NAME} 下载器")
    p.add_argument("--browser", action="store_true", help="用系统浏览器打开（调试）")
    p.add_argument("--port", type=int, default=0)
    args = p.parse_args()
    if args.browser:
        run_browser(args.port)
    else:
        run_desktop(args.port)


if __name__ == "__main__":
    main()
