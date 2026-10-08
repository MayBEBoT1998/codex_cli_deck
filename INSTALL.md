# Codex Deck 安装与使用

## Ubuntu

需要 Ubuntu 22.04+ 图形桌面；在同一环境中安装并登录 [Codex CLI](https://learn.chatgpt.com/docs/codex/cli)。

```bash
sudo apt update
sudo apt install git python3-gi python3-cairo python3-gi-cairo \
  gir1.2-gtk-3.0 gir1.2-vte-2.91 fonts-noto-cjk

git clone https://github.com/MayBEBoT1998/codex_cli_deck.git
cd codex_cli_deck
bash launch.sh
```

已有项目文件可跳过克隆，直接进入项目目录启动。必须保留整个 `assets` 文件夹。

## macOS

需要已登录的 macOS 图形桌面、[Homebrew](https://brew.sh) 和本机可用的 Codex CLI。在终端里先确认 `codex --version` 能运行。

首次下载请选择 `mac` 分支：

```bash
git clone --branch mac https://github.com/MayBEBoT1998/codex_cli_deck.git
cd codex_cli_deck
```

进入项目目录执行：

```bash
bash scripts/setup_macos.sh
bash launch.sh
```

安装脚本通过 Homebrew 安装 Python 3、PyGObject、GTK 3、VTE、SVG 支持和新版 Bash，再创建能访问 Homebrew 图形库的 `.venv`，将 macOS 专用的 `psutil` 安装到其中。无需改动系统 Python，也不需要 XQuartz。

macOS 与 Ubuntu 共用同一套界面、终端、通知、头像和宠物功能。Mac 使用 `psutil` 跟踪目录及结束会话进程，Ubuntu 继续使用 `/proc`，无需安装 `psutil`。两边的内嵌终端均使用 Bash，并继承启动 Deck 时的 `PATH`；不会读取 `.zshrc`，也不会修改用户的 Shell 或 Codex 配置。从能运行 Codex 的终端启动 Deck 即可。

Mac 使用 Homebrew Bash，避免系统 Bash 3.2 在长中文路径提示符换行、窗口缩放时重绘错位；Ubuntu 仍使用 `/bin/bash`。

支持 Homebrew 的 Apple Silicon／Intel 路径自动识别；本机验证环境为 Apple Silicon，Intel Mac 尚未实测。

排查依赖问题：

```bash
bash launch.sh --check-deps
```

若提示找不到 `gi`、`cairo` 或 `psutil`，或升级 Homebrew Python 后无法启动，请重新执行 `bash scripts/setup_macos.sh`。不要用 macOS 的 `/usr/bin/python3` 启动。已有完整依赖环境时，可用 `CODEX_DECK_PYTHON=/绝对路径/python bash launch.sh` 指定解释器；不要将不同系统或架构的 `.venv` 互相复制。

### 中文输入法

macOS 启动时显式加载 GTK 的 `quartz` 原生输入法和 Homebrew 输入法缓存，支持系统拼音输入。修改后需重新打开 Deck，新设置才会生效。切换到系统拼音输入法后，在终端正常拼写、选词、按空格上屏即可；Linux 输入法配置不变。

### 打包 macOS 应用

先完成上面的依赖安装，然后执行：

```bash
.venv/bin/python -m pip install -r requirements-macos-build.txt
.venv/bin/python scripts/build_macos.py
```

输出为 `dist/Codex Deck.app`，可以双击运行、拖入“应用程序”或固定到 Dock。该应用包含 Python、GTK/VTE、Bash、图片资源和 Quartz 输入法模块，运行时不依赖项目目录或 `.venv`；Codex CLI 需单独安装并登录。输入法缓存在运行时生成，移动或重命名应用后仍可加载。

应用版设置及导入头像保存在 `~/Library/Application Support/Codex Deck`，源码版仍使用项目内的 `.state`。Finder 启动时会补齐常用命令路径及打包机器的 `PATH`，不会把登录信息或 API 密钥放进应用。

打包产物仅支持构建机器的架构。当前本机产物为 Apple Silicon；Intel 版本需在 Intel Mac 上重新打包。构建工具做本地 ad-hoc 签名，未做 Developer ID 签名或 Apple 公证，适合本机使用；向其他 Mac 分发时需要另行签名、公证。

应用版验证（不调用模型）：

```bash
"dist/Codex Deck.app/Contents/MacOS/Codex Deck" --smoke-test artifacts/macos-app
```

## Windows WSL

需要 Windows 11 或 Windows 10 19044+，使用 **WSL2＋WSLg**。

尚未安装 WSL：在管理员 PowerShell 执行，按提示重启并完成 Ubuntu 初始化。

```powershell
wsl --install -d Ubuntu
```

已有 WSL：用 `wsl -l -v` 确认 Ubuntu 的 VERSION 为 2；必要时执行 `wsl --update`。

之后在 **WSL 的 Ubuntu 终端**执行上面的 Ubuntu 安装步骤。Codex 也安装在 WSL 内；应用窗口显示在 Windows 桌面。

WSL 方案尚未实测。图形环境要求见 [微软说明](https://learn.microsoft.com/zh-cn/windows/wsl/tutorials/gui-apps)。

## 使用

1. 启动后选择工作目录，自动打开终端和 Codex。
2. 点击左侧 **＋**，为新终端选择项目目录。
3. 点击标签切换；右键标签可重命名、更换头像或关闭。
4. 顶部 **设置** 可调整主题、头像集、动画、提示音和桌面宠物。

```bash
bash launch.sh --shell      # 只打开普通终端
bash launch.sh --restore    # 按上次目录重新打开终端
```

关闭应用会结束其中的进程；恢复功能保存名称、目录和头像，不恢复运行中的任务。

## 主要功能

- 多项目、多终端独立运行，切换后后台任务继续执行。
- 左侧统一管理，堆叠层数随终端数量变化，支持平滑切换。
- 内置十款头像，可导入自己的图片或头像文件夹。
- Codex 一轮结束后，头像放大闪烁；点击确认，可选提示音。
- 日间／夜晚模式、字号调整、设置自动保存。
- 二次元女伴宠物：站立、眨眼、走路、攀爬；可换角色、改大小和数量，随时关闭。

## 快捷键

| 快捷键 | 功能 |
| --- | --- |
| Ctrl+↑ / ↓ | 上一个 / 下一个终端 |
| Alt+1～9 | 切换对应终端 |
| Ctrl+Tab / Ctrl+Shift+Tab | 向后 / 向前切换 |
| Ctrl+Shift+T / W | 新建 / 关闭终端 |
| Ctrl+Shift+C / V | 复制 / 粘贴 |
| Ctrl+加号 / 减号 | 调整字号 |

macOS 同时保留上述快捷键，并支持：

| 快捷键 | 功能 |
| --- | --- |
| ⌘T / ⌘W | 新建 / 关闭终端 |
| ⌘C / ⌘V | 复制 / 粘贴 |
| ⌘1～9 | 切换对应终端 |
| ⌘[ / ⌘] | 上一个 / 下一个终端 |
| ⌘加号 / 减号 | 调整字号 |

`Ctrl+C` 始终传给终端，用于中断程序。macOS 的 `Ctrl+↑/↓` 可能由系统的 Mission Control 占用，可使用 `⌘[/]`。

提示：完成提醒适用于工作台内启动的本机 Codex，SSH 远端 Codex 不会自动接入。
