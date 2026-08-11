# MyCode

一个基于 [DeepSeek API](https://api.deepseek.com) 的**本地编码代理桌面应用**（macOS）。三栏界面：左侧任务列表、中间对话框、右侧产物预览。支持文件读写、命令执行、会话存档续聊与思考模式。

> 定位：本地跑、直接操作你的项目，成本远低于云端编码代理；UI 为紫色科技风。

## 特性

- **本地运行**：直接读写你机器上的文件、执行终端命令（危险命令二次确认）。
- **三栏 UI**：任务列表 / 对话 / 产物预览，可拖拽调整宽度。
- **6 个工具**：`read_file` · `write_file` · `edit_file` · `list_dir` · `grep_files` · `run_command`。
- **会话存档与续聊**：每个任务一条会话，存于本地，可随时打开续聊。
- **思考模式**：关 / 自动 / 开 三档，自动档按任务复杂度启发式决定。
- **模型可选**：`deepseek-v4-flash`（默认）/ `deepseek-v4-pro`。

## 隐私

- **不收集任何数据**。你的对话内容仅发送给 DeepSeek API 以生成回复。
- **API Key 仅存于本地** `~/.config/mycode/config.json`（权限 600），也可通过环境变量提供。
- **本仓库不含任何密钥或个人标识**：图标、bundle id 等均已去除本机专属信息；提交前已扫描确认无 `sk-` 密钥、无 `/Users/...` 绝对路径、无用户名泄露。

## 安装与运行（开发模式）

需要 Python 3.10+。

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python3 macos/MyCode
```

## 配置

首次运行会在 `~/.config/mycode/config.json` 写入配置（自动创建）：

```json
{
  "api_key": "你的 DeepSeek API Key",
  "model": "deepseek-v4-flash",
  "base_url": "https://api.deepseek.com"
}
```

或通过环境变量：

```bash
export DEEPSEEK_API_KEY="你的 DeepSeek API Key"
```

获取 Key：<https://platform.deepseek.com>

## 构建 .app（macOS 原生应用）

参考 `macos/` 目录：`Info.plist` 与启动器 `MyCode`。图标由 `build_icon.py`（依赖 Pillow）生成：

```bash
python3 build_icon.py                 # 生成 /tmp/mycode_iconbuild/AppIcon.iconset
iconutil -c icns -o macos/AppIcon.icns /tmp/mycode_iconbuild/AppIcon.iconset
```

打包时把 `app/`、`macos/Info.plist`、`macos/AppIcon.icns` 按标准 `.app` 结构放置，启动器 shebang 指向你的 Python（含 pywebview 的 venv）。

## 许可证

[MIT](LICENSE)
