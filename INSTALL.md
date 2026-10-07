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

提示：完成提醒适用于工作台内启动的本机 Codex，SSH 远端 Codex 不会自动接入。
