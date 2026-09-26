# xterm-ghostty 来源

[xterm-ghostty.terminfo](xterm-ghostty.terminfo) 来自 Ghostty 官方 **1.3.1**，
tag 对应提交 `332b2aefc6e72d363aa93ab6ecfc86eeeeb5ed28`。
上游声明为 [src/terminfo/ghostty.zig](https://github.com/ghostty-org/ghostty/blob/332b2aefc6e72d363aa93ab6ecfc86eeeeb5ed28/src/terminfo/ghostty.zig)，
保留其 [MIT 许可证](LICENSE)。

本文件由官方 macOS 1.3.1 应用随附的数据库导出（导出工具为 macOS 的 ncurses 6.0.20150808）：

```sh
/Applications/Ghostty.app/Contents/MacOS/ghostty +version
infocmp -x -A /Applications/Ghostty.app/Contents/Resources/terminfo xterm-ghostty > exported.terminfo
```

去掉导出文件首行的本机路径注释，补上本文件的来源注释；将名称行
`xterm-ghostty|ghostty|Ghostty,` 改为 `xterm-ghostty|Ghostty terminal emulator,`。
能力字段保持导出结果不变。删除 `ghostty` 别名可防止遮蔽用户或系统的同名定义；
带空格的末字段明确表示描述，避免旧版 tic 把它当作另一个别名。
更新来源时须重新核实官方版本、提交、许可及能力差异，再运行真实 tic/infocmp 回归。

以上导出仅用于维护仓库资源。安装时不需要 Ghostty、网络或 Zig：核心
[terminfo.py](../terminfo.py) 使用当前机器的 `tic -x` 暂存编译，再用 `infocmp -x`
验证，只发布一个条目到执行用户的 `~/.terminfo`，并用默认搜索路径再次验收。
仓库不保存编译后的平台二进制。完整准备流程见[bootstrap 说明](../../README.md#bootstrap-准备完整环境)。
