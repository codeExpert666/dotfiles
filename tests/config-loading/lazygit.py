"""在 config-loading.sh 提供的 HOME 中测试真实 Lazygit 终端界面。"""

import os
from pathlib import Path
import shlex
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'support'))
from harness import cleanup_on_exit, run, terminal


def main():
    lazygit, delta = sys.argv[1:]
    root = Path.cwd() / "lazygit-test"
    root.mkdir()
    work = root / "repo"
    work.mkdir()
    run(["git", "init", "--quiet", str(work)], check=True)
    (work / "sample.txt").write_text("configuration loading test\n")

    config_dir = run([lazygit, "--print-config-dir"], check=True).stdout.strip()
    expected = Path(os.environ["HOME"]) / ".config/lazygit"
    assert Path(config_dir) == expected, f"unexpected config directory: {config_dir}"

    # 在夹具中关闭网络活动和首次启动提示。
    overrides = root / "runtime.yml"
    overrides.write_text(
        "git:\n  autoFetch: false\n"
        "update:\n  method: never\n"
        "disableStartupPopups: true\n"
        "promptToReturnFromSubprocess: false\n"
    )
    bin_dir = root / "bin"
    bin_dir.mkdir()
    editor_probe, delta_probe = root / "editor.args", root / "delta.args"
    # 在编辑器调用处记录参数，避免启动编辑器或触发插件安装。
    (bin_dir / "nvim").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$DOTFILES_EDITOR_PROBE"\n'
    )
    # 每次渲染完成后写入退出状态，仅记录参数无法证明渲染成功。
    # 通过原子重命名发布状态文件，避免读取到尚未写完的内容。
    delta_status = root / "delta-status"
    delta_status.mkdir()
    (bin_dir / "delta").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$DOTFILES_DELTA_PROBE"\n'
        f'{shlex.quote(delta)} "$@"\n'
        'status=$?\n'
        'printf "%s\\n" "$status" > "$DOTFILES_DELTA_STATUS/$$.tmp"\n'
        'mv "$DOTFILES_DELTA_STATUS/$$.tmp" "$DOTFILES_DELTA_STATUS/$$.status"\n'
        'exit "$status"\n'
    )
    for path in bin_dir.iterdir():
        path.chmod(0o755)
    env = dict(os.environ)
    env.update(
        PATH=str(bin_dir) + os.pathsep + env["PATH"],
        LG_CONFIG_FILE=f"{expected / 'config.yml'},{overrides}",
        DOTFILES_EDITOR_PROBE=str(editor_probe),
        DOTFILES_DELTA_PROBE=str(delta_probe),
        DOTFILES_DELTA_STATUS=str(delta_status),
    )

    edited = False
    next_quit = 0

    def completed_renders():
        receipts = list(delta_status.glob('*.status'))
        for receipt in receipts:
            status = receipt.read_text().strip()
            assert status == '0', f"configured delta renderer exited {status}"
        return receipts

    def advance(master):
        nonlocal edited, next_quit
        if completed_renders() and not edited:
            os.write(master, b"2e")
            edited = True
        if editor_probe.exists() and time.monotonic() >= next_quit:
            # 编辑器参数文件可能先于终端界面恢复输入模式出现；
            # 在会话总时限内重复发送 q，直到 Lazygit 退出。
            os.write(master, b"q")
            next_quit = time.monotonic() + .2

    output = terminal([lazygit], env=env, cwd=work, timeout=25, observe=advance)
    try:
        assert completed_renders(), "Lazygit did not complete the configured delta renderer"
        assert editor_probe.exists(), "Lazygit did not invoke the configured Neovim editor"
        assert "--dark" in delta_probe.read_text().splitlines()
        assert "--paging=never" in delta_probe.read_text().splitlines()
        assert any(
            Path(arg).name == "sample.txt" for arg in editor_probe.read_text().splitlines()
        ), "Neovim did not receive the selected file"
    except BaseException:
        print("Terminal output (tail):", repr(output[-8000:]), file=sys.stderr)
        raise


if __name__ == "__main__":
    with cleanup_on_exit():
        main()
