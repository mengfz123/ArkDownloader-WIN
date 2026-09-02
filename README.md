# PanFetch

Motrix 风格桌面下载器，支持 **百度网盘直链**（自动 5MiB 分片 + UA）与普通 **HTTP** 多线程下载。

## 普通用户使用

1. 运行 `build.bat` 生成 `dist\PanFetch.exe`（只需构建一次）
2. 双击 `PanFetch.exe` 即可使用
3. 或开发模式：双击 `start.bat`

## 功能

- 新建 / 暂停 / 继续 / 删除任务
- 暂停全部、继续全部、清空已完成
- 多连接并发、断点续传（`.panfetch.json` 侧车文件）
- 百度直链自动识别
- 设置：保存目录、连接数、最大同时下载
- Gopeed 兼容 API（`/api/v1/tasks` 等）

## 开发

```powershell
cd C:\Users\Ivan\Projects\pan-fetch
pip install -r requirements.txt
python main.py --browser   # 浏览器调试
python main.py           # 桌面窗口（Edge WebView2）
```

配置与任务记录保存在 `%APPDATA%\PanFetch\`。
# ArkDownloader-WIN
