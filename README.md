# ArkDownloader

面向 Windows 的轻量多线程下载器，专为 **百度网盘直链** 与通用 HTTP 下载设计。体积小、无 Electron / aria2 依赖，并提供本地 RPC，便于脚本与网盘工具联动。

**当前版本：** 1.0.6

---

## 特点

| 特色 | 说明 |
|------|------|
| **百度直链友好** | 自动识别常见 PCS / CDN 直链，强制 ≤5 MiB 分片，并使用网盘客户端风格 User-Agent |
| **真·断点续传** | 分片进度写在文件旁的 `.panfetch.json` 侧车文件，暂停、退出、重启后可继续 |
| **可中断暂停** | 下载过程中按块读取，暂停不必等整片读完 |
| **可配置分片** | 设置中可调 1–5 MB（默认 1 MB），新建任务按配置生效 |
| **轻量桌面壳** | Python 标准库 HTTP + 本地 Web UI（pywebview），一键打包单文件 exe |
| **RPC 可编程** | 默认端口 `18766`，支持批量建任务、局域网访问与可选令牌鉴权 |

---

## 功能一览

- 新建 / 暂停 / 继续 / 删除任务；暂停全部、继续全部、清空已完成
- 多连接分片下载；任务队列（最大同时下载数可配）
- 百度网盘直链与普通 HTTP 双通道（独立 UA）
- 链接探测、批量粘贴 URL、拖拽添加、搜索与排序
- 设置：保存目录、连接数、分片大小、最大同时下载、完成通知、自动开始
- 本地 RPC API（任务 / 配置 / resolve），可选绑定 `0.0.0.0` 供局域网调用
- `/ingest` 页面支持网页 `postMessage` 推送任务（便于浏览器扩展或网盘页对接）

---

## 普通用户使用

1. 运行 `build.bat`，生成 `dist\ArkDownloader.exe`
2. 双击 `ArkDownloader.exe` 即可使用  
   或开发模式双击 `start.bat`

系统要求：Windows 10+，桌面模式建议已安装 **WebView2**（多数 Win10/11 已自带）。

---

## 开发与运行

```powershell
pip install -r requirements.txt
python main.py              # 桌面窗口（WebView2）
python main.py --browser    # 浏览器调试
```

依赖见 `requirements.txt`（`pywebview`、`pyinstaller`）。下载逻辑本身仅使用 Python 标准库。

打包：

```text
build.bat  →  dist\ArkDownloader.exe
```

数据目录：`%APPDATA%\ArkDownloader\`（`config.json`、`tasks.json`）。

---

## 默认配置要点

| 项 | 默认 | 说明 |
|----|------|------|
| 连接数 | 8 | 单任务并发分片数（上限 32） |
| 分片大小 | 1 MB | 可设 1–5 MB；百度直链上限仍为 5 MiB |
| 最大同时下载 | 3 | 全局并行任务数 |
| RPC 端口 | 18766 | 可选令牌；默认可仅本机访问 |
| 百度 UA / HTTP UA | 内置默认值 | 可在设置中分别修改 |

---

## RPC 简要

服务随程序启动。常用路径示例：

- `GET /api/v1/info` — 版本与状态
- `GET/PUT /api/v1/config` — 读写配置
- `GET/POST /api/v1/tasks` — 列表 / 新建
- `POST /api/v1/tasks/batch` — 批量新建
- `PUT /api/v1/tasks/:id/pause|continue` — 暂停 / 继续
- `POST /api/v1/resolve` — 解析链接信息

若设置了 RPC 令牌，请求需携带：

```http
Authorization: Bearer <token>
```

或 `X-ArkDownloader-Token` / `X-PanFetch-Token`。程序内「设置 → RPC」可查看更完整的调用说明。

---

## 百度直链说明

本工具下载的是 **已拿到的直链**（含 `size=` 等参数的 PCS/CDN URL），不会登录网盘账号、也不会破解分享页。请自行通过官方客户端、第三方网盘工具等获取合法直链后导入。

---

## 相关项目

- [ArkDownloader-Mobile](https://github.com/mengfz123/ArkDownloader-Mobile) — 同系列 Android 端（uni-app），功能与 RPC 风格对齐

---

## 许可证

暂未指定开源许可证。发布或二次分发前请确认作者授权。
