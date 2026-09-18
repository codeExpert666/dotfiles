# 不导出 ZDOTDIR；子 Zsh 必须重新读取根 .zshenv，才能获得同一组早期启动开关。
# 仓库采用默认目录布局；部署时拒绝不匹配的 XDG 路径。
ZDOTDIR="$HOME/.config/zsh" # shuck: ignore=C001 # 由 Zsh 用于定位启动文件。

# Ubuntu 的系统级 zshrc 可能先调用 compinit；本配置在用户 .zshrc 中统一初始化并把转储写入 XDG cache。
# 该开关不需要导出；未使用它的平台会忽略这个普通 Shell 参数。
skip_global_compinit=1 # shuck: ignore=C001 # 由 Ubuntu 的系统级 zshrc 读取。

# 所有 Zsh 都读取 .zshenv；PATH 和编辑器选择也供非交互命令使用。
typeset -U path PATH
[[ -d /usr/local/bin ]] && path=(/usr/local/bin $path)
[[ -d /opt/homebrew/bin ]] && path=(/opt/homebrew/bin $path)
[[ -d "$HOME/.local/bin" ]] && path=("$HOME/.local/bin" $path)
export PATH

# nvim 从默认 ~/.config/nvim 加载 LazyVim；子进程和 sudoedit 使用同一编辑入口。
export EDITOR=nvim
export VISUAL=nvim
export SUDO_EDITOR=nvim
