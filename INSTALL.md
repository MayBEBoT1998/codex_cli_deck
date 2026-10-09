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

### 安装为桌面程序和全局命令

在源码目录执行一次，无需 `sudo`：

```bash
bash install.sh
```

之后在 Ubuntu 应用菜单搜索 **Codex Deck**，或在任意目录运行：

```bash
codex-deck                 # 打开目录选择窗口
codex-deck --cwd .         # 直接打开当前项目
codex-deck --restore       # 恢复上次的终端列表
codex-deck --shell         # 仅打开普通终端
```

首次提示找不到命令时，重新登录，或执行 `export PATH="$HOME/.local/bin:$PATH"`。

程序安装到 `~/.local/share/codex-deck`，不再依赖源码目录。首次安装复制现有设置和自定义头像到 `~/.local/state/codex-deck`（支持 `XDG_STATE_HOME`），更新时保留。更新源码后再次运行 `bash install.sh`；卸载运行 `codex-deck --uninstall`，设置和头像仍保留。

## Windows WSL

需要 Windows 11 或 Windows 10 19044+，使用 **WSL2＋WSLg**。

尚未安装 WSL：在管理员 PowerShell 执行，按提示重启并完成 Ubuntu 初始化。

```powershell
wsl --install -d Ubuntu
```

已有 WSL：用 `wsl -l -v` 确认 Ubuntu 的 VERSION 为 2；必要时执行 `wsl --update`。

之后在 **WSL 的 Ubuntu 终端**执行上面的 Ubuntu 安装步骤。Codex 也安装在 WSL 内；应用窗口显示在 Windows 桌面。

同样可运行 `bash install.sh`，之后在 WSL 终端的任意目录使用 `codex-deck`。

WSL 方案尚未实测。图形环境要求见 [微软说明](https://learn.microsoft.com/zh-cn/windows/wsl/tutorials/gui-apps)。

## 使用

1. 启动后选择工作目录，自动打开终端和 Codex。
2. 点击左侧 **＋**，为新终端选择项目目录。
3. 点击标签切换；右键标签可重命名、更换头像或关闭。
4. 顶部 **设置** 可调整主题、头像集、动画、提示音和桌面宠物。
5. 顶部 **Agent 协作**：将一个 Agent 卡片拖到另一个上，建立 **发起者 → 执行者** 的关系，再在发起者终端中描述工作。也可使用选择器连接，点击关系右侧 **×** 解除。

协作为实验功能，已验证真实双终端派发与回传。派发任务沿用执行者当前对话历史；双方的完整上下文不会自动互传。

```bash
bash launch.sh --shell      # 只打开普通终端
bash launch.sh --restore    # 按上次目录重新打开终端
```

关闭应用会结束其中的进程；恢复功能保存名称、目录和头像，不恢复运行中的任务或 Codex 对话。继续原对话可在终端中运行 `codex resume`。

## 主要功能

- 多项目、多终端独立运行，切换后后台任务继续执行。
- 左侧统一管理，堆叠层数随终端数量变化，支持平滑切换。
- 内置十款头像，可导入自己的图片或头像文件夹。
- Codex 一轮结束后，头像放大闪烁；点击确认，可选提示音。
- 日间／夜晚模式、字号调整、设置自动保存。日间使用浅色外框与深色终端，保证 Codex 输入框和状态文字清晰。
- 二次元女伴宠物：站立、眨眼、走路、攀爬；可换角色、改大小和数量，随时关闭。
- Agent 间协作：每个发起者单独选择控制范围，任务排队执行、结果回传、状态查看与取消。需要 Codex CLI 的 App Server 支持；普通模式使用 `codex --no-daemon`。

## 快捷键

| 快捷键 | 功能 |
| --- | --- |
| Ctrl+↑ / ↓ | 上一个 / 下一个终端 |
| Alt+1～9 | 切换对应终端 |
| Ctrl+Tab / Ctrl+Shift+Tab | 向后 / 向前切换 |
| Ctrl+Shift+T / W | 新建 / 关闭终端 |
| Ctrl+Shift+C / V | 复制 / 粘贴 |
| Ctrl+加号 / 减号 | 调整字号 |

提示：完成提醒适用于工作台内启动的本机 Codex，SSH 远端 Codex 不会自动接入。
