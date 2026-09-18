"""准备应用的受控运行会话，仅使用 Python 3 标准库。

Bash 调用方限制整个辅助程序的运行时间；内部子进程各有执行时限。
会话负责终止私有进程组、回收直接子进程，并关闭控制伪终端（PTY）。
此工具只提供应用级诊断，无法隔离任意本机代码的副作用。
"""

from contextlib import contextmanager
import errno
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import termios
import time


CHILDREN = []
DEADLINE = 0
DEFERRED_INTERRUPTS = 0
PENDING_SIGNAL = 0


def report(level, item, message, hint=""):
    print("\t".join(str(x).replace("\t", " ").replace("\n", " ")
                    for x in (level, item, message, hint)), flush=True)


def stop(child):
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait()
    for stream in (child.stdin, child.stdout, child.stderr):
        if stream:
            stream.close()
    if child in CHILDREN:
        CHILDREN.remove(child)


def interrupted(signum, _frame):
    global PENDING_SIGNAL
    if not PENDING_SIGNAL:
        PENDING_SIGNAL = signum
    if DEFERRED_INTERRUPTS:
        return
    # 进入清理阶段时若收到信号，仍以子进程登记表兜底。
    # 会话仍负责关闭句柄；延后退出以保留首个信号对应的退出状态。
    with defer_interrupts():
        for child in CHILDREN[:]:
            stop(child)


@contextmanager
def defer_interrupts():
    # 句柄交接或关闭期间只记录信号，待外层保护区结束后退出。
    # 不修改信号屏蔽字，避免子进程执行新程序后仍继承被屏蔽的信号。
    global DEFERRED_INTERRUPTS, PENDING_SIGNAL
    DEFERRED_INTERRUPTS += 1
    try:
        yield
    finally:
        DEFERRED_INTERRUPTS -= 1
        if not DEFERRED_INTERRUPTS and PENDING_SIGNAL:
            signum, PENDING_SIGNAL = PENDING_SIGNAL, 0
            raise SystemExit(128 + signum)


@contextmanager
def session(args, env, cwd, use_terminal=False):
    child = master = slave = None
    try:
        with defer_interrupts():
            if use_terminal:
                master, slave = pty.openpty()
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))

                def attach():
                    os.setsid()
                    fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

                child = subprocess.Popen(args, env=env, cwd=cwd, stdin=slave, stdout=slave,
                                         stderr=slave, preexec_fn=attach)
            else:
                child = subprocess.Popen(args, env=env, cwd=cwd, stdin=subprocess.DEVNULL,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         start_new_session=True)
            CHILDREN.append(child)
            if slave is not None:
                os.close(slave)
                slave = None
        yield child, master
    finally:
        # 分配资源前已建立清理保护，即使伪终端只准备了一部分也会释放。
        # 清理期间延后处理中断，避免第二个信号打断子进程回收或文件描述符关闭。
        with defer_interrupts():
            try:
                if child is not None and (child in CHILDREN or child.returncode is None):
                    stop(child)
            finally:
                try:
                    if slave is not None:
                        os.close(slave)
                finally:
                    if master is not None:
                        os.close(master)


def run(args, env, cwd, seconds=8):
    with session(args, env, cwd) as (child, _master):
        stdout, stderr = child.communicate(timeout=max(.1, min(seconds, DEADLINE - time.monotonic())))
        if child.returncode:
            sys.stderr.write(stderr.decode(errors="replace")[:5000])
            raise RuntimeError("{} exited {}".format(Path(args[0]).name, child.returncode))
        return stdout, stderr


def terminal(args, env, cwd, observe=None, seconds=8):
    output = bytearray()
    end = min(DEADLINE, time.monotonic() + seconds)
    with session(args, env, cwd, use_terminal=True) as (child, master):
        while time.monotonic() < end:
            if observe:
                observe(master)
            if select.select([master], [], [], .05)[0]:
                try:
                    data = os.read(master, 16384)
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
                    break
                if not data:
                    break
                output.extend(data)
                if len(output) > 1024 * 1024:
                    raise RuntimeError("terminal output exceeded 1 MiB")
            if child.poll() is not None:
                break
        child.wait(timeout=max(.1, end - time.monotonic()))
        if child.returncode:
            sys.stderr.write(output.decode(errors="replace")[-5000:])
            raise RuntimeError("terminal session exited {}".format(child.returncode))
        return bytes(output).replace(b"\r\n", b"\n")


def copy(source, target, ignore=None):
    if source.is_dir():
        shutil.copytree(str(source), str(target), ignore=ignore)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(source), str(target))


def managed(source, expected, item):
    """应用级探测无法约束个人可执行配置的副作用。"""
    if not source.is_file() or not expected.is_file():
        report("SKIP", item, "required managed configuration is missing: " + str(source))
        return False
    if source.read_bytes() != expected.read_bytes():
        report("SKIP", item, "configuration differs from the managed source: " + str(source),
               "Review executable overrides before manually starting this application.")
        return False
    return True


def nvim_config_files(root):
    """列出将复制到临时会话的启动代码和 JSON 配置，供核对文件清单。"""
    files = {Path("lazy-lock.json"), Path("lazyvim.json")}

    def unreadable(error):
        raise error

    for directory, directories, names in os.walk(str(root), onerror=unreadable, followlinks=False):
        for path in [Path(directory)] + [Path(directory) / name for name in directories]:
            if path.is_symlink():
                raise ValueError("configuration directory is a symbolic link: " + str(path))
        for name in names:
            path = Path(directory) / name
            if path.suffix in (".lua", ".vim"):
                files.add(path.relative_to(root))
    return files


def require_completion(env, name):
    # 进程正常退出不代表检查已经执行完毕，必须核对当前会话写入的完成标记。
    marker = Path(env["DOTFILES_DOCTOR_COMPLETE"])
    if not marker.is_file() or marker.read_text() != name + "\n":
        raise RuntimeError(name + " session did not complete its checks")


def setup(module, root, original):
    home = root / ("runtime-" + module)
    home.mkdir()
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", ""),
           "TERM": "xterm-256color", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1", "TMPDIR": str(home / "tmp")}
    (home / "tmp").mkdir()
    for variable, relative in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"),
                               ("XDG_STATE_HOME", ".local/state"), ("XDG_CACHE_HOME", ".cache")):
        (home / relative).mkdir(parents=True, exist_ok=True)
        env[variable] = str(home / relative)
    env["DOTFILES_DOCTOR_REPORT"] = str(home / "report.tsv")
    env["DOTFILES_DOCTOR_COMPLETE"] = str(home / "complete")
    for name in ("shuck", "atuin"):
        source = original / ".config" / name / ("shuck.toml" if name == "shuck" else "config.toml")
        if source.is_file():
            copy(source, home / ".config" / name / source.name)
    starship = original / ".config/starship.toml"
    if starship.is_file():
        copy(starship, home / ".config/starship.toml")
    return home, env


def zsh(repo, original, home, env):
    executable = shutil.which("zsh")
    if not executable:
        report("SKIP", "zsh.runtime", "Zsh is unavailable")
        return
    for name in ("local.zsh", "local.zprofile"):
        if (original / ".config/zsh" / name).exists():
            report("SKIP", "zsh.runtime", "local executable override exists: " + name,
                   "Default checks parse this override; controlled sessions cannot constrain its side effects.")
            return
    relative_files = [".zshenv", ".config/zsh/.zshrc", ".config/zsh/.zprofile",
                      ".config/zsh/common.zsh", ".config/zsh/linux.zsh", ".config/zsh/macos.zsh",
                      ".config/zsh/.zsh_plugins.txt"]
    if not all(managed(original / name, repo / "zsh" / name, "zsh.runtime") for name in relative_files):
        return
    manager = original / ".local/share/antidote"
    cache = original / ".cache/antidote"
    static = cache / "zsh_plugins.zsh"
    if not (manager / "antidote.zsh").is_file() or not static.is_file():
        report("SKIP", "zsh.runtime", "Antidote or its static cache is missing; no installation attempted")
        return
    for plugin in ("zsh-autosuggestions", "zsh-syntax-highlighting"):
        if not list(cache.glob("**/" + plugin + "/" + plugin + ".zsh")):
            report("SKIP", "zsh.runtime", "plugin source is missing: " + plugin)
            return
    for name in relative_files:
        copy(original / name, home / name)
    copy(manager, home / ".local/share/antidote")
    copy(cache, home / ".cache/antidote")
    staged = home / ".cache/antidote/zsh_plugins.zsh"
    staged.write_text(static.read_text().replace(str(original), str(home)))
    # 让静态脚本新于清单并补齐加载标记，使 Antidote 直接使用缓存，避免重新生成或下载。
    now = time.time() + 2
    os.utime(str(staged), (now, now))
    (home / ".cache/antidote/.antidote.load").touch()
    env["DOTFILES_DOCTOR_ZSH"] = str(repo / "scripts/doctor/zsh.zsh")
    for mode, flags in (("noninteractive", "-c"), ("login", "-lc"), ("interactive", "-ic")):
        args = [executable, flags, 'source "$DOTFILES_DOCTOR_ZSH" ' + mode]
        if mode == "interactive":
            terminal(args, env, home)
        else:
            stdout, stderr = run(args, env, home)
            if stdout or stderr:
                report("FAIL", "zsh.runtime.output", mode + " startup produced unexpected output",
                       "Review system startup files and initialization diagnostics.")
        require_completion(env, "zsh." + mode)
    report("PASS", "zsh.runtime.scope", "new login, noninteractive and PTY sessions used copied config/plugins and temporary state")


def nvim(repo, original, home, env):
    executable = shutil.which("nvim")
    if not executable:
        report("SKIP", "nvim.runtime", "Neovim is unavailable")
        return
    source = original / ".config/nvim"
    baseline = repo / "nvim/.config/nvim"
    if not source.is_dir():
        report("SKIP", "nvim.runtime", "Neovim config is missing")
        return
    try:
        files, expected = nvim_config_files(source), nvim_config_files(baseline)
    except ValueError as error:
        report("SKIP", "nvim.runtime", str(error), "Review directory links before starting a controlled session.")
        return
    unknown = sorted(files - expected)
    if unknown:
        report("SKIP", "nvim.runtime", "unmanaged startup configuration: " + str(source / unknown[0]),
               "Review executable overrides before manually starting Neovim.")
        return
    if not all(managed(source / name, baseline / name, "nvim.runtime") for name in sorted(expected)):
        return
    plugins = original / ".local/share/nvim/lazy"
    lock = json.loads((source / "lazy-lock.json").read_text())
    missing = [name for name in lock if not (plugins / name).is_dir()]
    if missing:
        report("SKIP", "nvim.runtime", "locked plugins are missing: " + ", ".join(missing),
               "Prepare plugins during bootstrap; doctor never installs them.")
        return
    # 只复制已核对的配置清单；Stow 的文件链接解引用为临时目录内的普通文件。
    # 无关配置文件和配置目录链接不进入会话。
    for name in sorted(expected):
        copy(source / name, home / ".config/nvim" / name)
    # 插件源码和编译结果也复制，避免 lazy.nvim 写入原插件目录或用户锁文件。
    copy(plugins, home / ".local/share/nvim/lazy", shutil.ignore_patterns(".git"))
    site = original / ".local/share/nvim/site"
    if site.is_dir():
        copy(site, home / ".local/share/nvim/site")
    env["PATH"] = str(original / ".local/share/nvim/mason/bin") + os.pathsep + env["PATH"]
    env["NVIM_LOG_FILE"] = str(home / "nvim.log")
    env["DOTFILES_DOCTOR_SETUP"] = str(repo / "scripts/doctor/nvim-setup.lua")
    env["DOTFILES_DOCTOR_CHECK"] = str(repo / "scripts/doctor/nvim-runtime.lua")
    env["SHUCK_EXPERIMENTAL"] = "1"
    (home / "project").mkdir()
    run(["git", "init", "--quiet", str(home / "project")], env, home)
    run([executable, "--headless", "-n", "-i", "NONE", "--cmd",
         "lua dofile(vim.env.DOTFILES_DOCTOR_SETUP)", "-c",
         "lua vim.api.nvim_create_autocmd('VimEnter', {once=true, callback=function() "
         "vim.schedule(function() dofile(vim.env.DOTFILES_DOCTOR_CHECK) end) end})"],
        env, home / "project", seconds=19)
    require_completion(env, "nvim")
    report("PASS", "nvim.runtime.scope", "copied plugins/config; lazy/Mason/Treesitter installers and update checks disabled")


def vim(repo, original, home, env):
    if not managed(original / ".vimrc", repo / "vim/.vimrc", "vim.runtime"):
        return
    copy(original / ".vimrc", home / ".vimrc")
    run(["vim", "-N", "-n", "-i", "NONE", "--not-a-term", "-c",
         'call writefile([$MYVIMRC, string(&number), &viminfofile, get(g:, "netrw_home", "")], "vim-settings")',
         "-c", "qa!"], env, home)
    expected = [str(home / ".vimrc"), "1", str(home / ".local/state/vim/viminfo"), str(home / ".local/state/vim")]
    if (home / "vim-settings").read_text().splitlines() != expected:
        raise RuntimeError("Vim selected different settings or state paths")
    report("PASS", "vim.runtime", "new Vim session discovered .vimrc and applied number/state settings")


def lazygit(repo, original, home, env, config):
    if not managed(config, repo / "lazygit/.config/lazygit/config.yml", "lazygit.runtime"):
        return
    delta = shutil.which("delta")
    if not delta:
        report("SKIP", "lazygit.runtime", "delta is unavailable")
        return
    copy(config, home / ".config/lazygit/config.yml")
    override = home / "runtime.yml"
    override.write_text("git:\n  autoFetch: false\nupdate:\n  method: never\n"
                        "disableStartupPopups: true\npromptToReturnFromSubprocess: false\n")
    bindir = home / "bin"
    bindir.mkdir()
    editor_args, delta_args = home / "editor.args", home / "delta.args"
    # 编辑器替身只记录交接参数，由 Neovim 模块验证编辑器本身。
    # delta 包装器记录参数后转发给真实命令，保留实际渲染验证。
    (bindir / "nvim").write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$DOTFILES_EDITOR_ARGS"\n')
    (bindir / "delta").write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$DOTFILES_DELTA_ARGS"\n'
                                   + "exec " + shlex.quote(delta) + ' "$@"\n')
    for path in bindir.iterdir():
        path.chmod(0o755)
    env.update(PATH=str(bindir) + os.pathsep + env["PATH"], DOTFILES_EDITOR_ARGS=str(editor_args),
               DOTFILES_DELTA_ARGS=str(delta_args))
    project = home / "project"
    run(["git", "init", "--quiet", str(project)], env, home)
    (project / "sample.txt").write_text("doctor renderer and editor handoff\n")
    state = {"edited": False, "quit": False}

    def observe(master):
        # 按证据文件推进按键交互：渲染器被调用后再编辑，编辑器参数落盘后再退出。
        # 避免用固定延时猜测终端界面是否就绪。
        if delta_args.exists() and not state["edited"]:
            os.write(master, b"2e")
            state["edited"] = True
        if editor_args.exists() and not state["quit"]:
            os.write(master, b"q")
            state["quit"] = True

    terminal(["lazygit", "--use-config-file", str(home / ".config/lazygit/config.yml") + "," + str(override)],
             env, project, observe=observe, seconds=12)
    if not delta_args.exists() or not editor_args.exists():
        raise RuntimeError("configured renderer/editor was not invoked")
    if not {"--dark", "--paging=never"}.issubset(delta_args.read_text().splitlines()):
        raise RuntimeError("unexpected delta options")
    if not any(Path(arg).name == "sample.txt" for arg in editor_args.read_text().splitlines()):
        raise RuntimeError("Neovim did not receive the selected file")
    report("PASS", "lazygit.runtime", "TUI loaded copied YAML, rendered with real delta and handed a file to the nvim command",
           "The editor boundary is recorded; Neovim itself is validated by the nvim module.")


def main():
    global DEADLINE
    DEADLINE = time.monotonic() + 26
    for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, interrupted)
    module, repo, root, original = sys.argv[1:5]
    repo, root, original = Path(repo), Path(root), Path(original)
    home, env = setup(module, root, original)
    try:
        functions = {"zsh": zsh, "nvim": nvim, "vim": vim}
        if module == "lazygit":
            lazygit(repo, original, home, env, Path(sys.argv[5]))
        else:
            functions[module](repo, original, home, env)
    finally:
        record = home / "report.tsv"
        if record.is_file():
            contents = record.read_text()
            print(contents, end="", flush=True)
            if module == "nvim" and any(line.startswith("FAIL\t") for line in contents.splitlines()):
                log = home / ".local/state/nvim/lsp.log"
                if log.is_file():
                    print("Temporary Neovim LSP log:\n" + log.read_text(errors="replace")[-4000:], file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    finally:
        with defer_interrupts():
            for process in CHILDREN[:]:
                stop(process)
