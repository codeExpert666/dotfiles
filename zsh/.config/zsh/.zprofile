# 登录 Zsh 在 macOS 和 Ubuntu 都会读取本文件；Ubuntu 主线未安装 Homebrew，因此两个条件均不成立。
if [[ -x /opt/homebrew/bin/brew ]]; then
	eval "$(/opt/homebrew/bin/brew shellenv)"
elif [[ -x /usr/local/bin/brew ]]; then
	eval "$(/usr/local/bin/brew shellenv)"
fi

# brew shellenv 会重排 PATH；保持 .zshenv 选中的完整 JDK 在最前。
[[ -d "$HOME/.local/bin" ]] && path=("$HOME/.local/bin" $path)
if [[ -n ${JAVA_HOME-} && -x "$JAVA_HOME/bin/java" && -x "$JAVA_HOME/bin/javac" ]]; then
	path=("$JAVA_HOME/bin" $path)
	export PATH
fi

# 本机可选文件不纳入仓库的静态分析。
# shuck: source=/dev/null
[[ -r "$ZDOTDIR/local.zprofile" ]] && source "$ZDOTDIR/local.zprofile"
