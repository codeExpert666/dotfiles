"""Opt-in, offline LazyVim icon integration using an installed plugin cache.

Only a temporary HOME/tmux server is used. Plugin checks/downloads, parser
installation, LSP servers and adapter provisioning are disabled. No AI runs.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--plugins', type=Path, required=True)
    parser.add_argument('--overlay', type=Path)
    parser.add_argument('--probe', type=Path, default=Path(__file__).with_suffix('.lua'))
    parser.add_argument('--nvim', default=shutil.which('nvim'))
    parser.add_argument('--tmux', default=shutil.which('tmux'))
    parser.add_argument('--preview-seconds', type=int, default=0,
                        help='keep the synthetic UI available for visual inspection (0-120 seconds)')
    args = parser.parse_args()
    assert 0 <= args.preview_seconds <= 120
    assert args.nvim and args.tmux, 'nvim and tmux are required'
    assert (args.plugins / 'lazy.nvim').is_dir(), 'installed lazy.nvim cache required'
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='nvim-icons-', dir='/tmp') as directory:
        root = Path(directory)
        config = root / '.config/nvim'
        shutil.copytree(args.config, config)
        if args.overlay:
            for source in args.overlay.rglob('*.lua'):
                dest = config / source.relative_to(args.overlay)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, dest)
        data = root / '.local/share/nvim'
        data.mkdir(parents=True)
        (data / 'lazy').symlink_to(args.plugins.resolve(), target_is_directory=True)
        (root / 'init.lua').write_text('''vim.opt.rtp:prepend(vim.fn.stdpath("config"))
vim.opt.rtp:prepend(vim.fn.stdpath("data") .. "/lazy/lazy.nvim")
local lazy = require("lazy")
local setup = lazy.setup
lazy.setup = function(opts)
  opts.install = vim.tbl_extend("force", opts.install or {}, { missing = false })
  opts.checker = { enabled = false }
  opts.change_detection = { enabled = false }
  opts.rocks = { enabled = false }
  for _, name in ipairs({ "neovim/nvim-lspconfig", "mason-org/mason-lspconfig.nvim",
    "jay-babu/mason-nvim-dap.nvim" }) do
    table.insert(opts.spec, { name, enabled = false })
  end
  table.insert(opts.spec, { "mason-org/mason.nvim", opts = function(_, o) o.ensure_installed = {} end })
  table.insert(opts.spec, { "nvim-treesitter/nvim-treesitter", opts = function(_, o)
    o.ensure_installed = {}; o.auto_install = false
  end })
  return setup(opts)
end
dofile(vim.fn.stdpath("config") .. "/init.lua")
''')
        shutil.copyfile(args.probe, root / 'probe.lua')
        report = root / 'report.json'
        env = {
            'HOME': str(root), 'XDG_CONFIG_HOME': str(root / '.config'),
            'XDG_DATA_HOME': str(root / '.local/share'), 'XDG_STATE_HOME': str(root / '.local/state'),
            'XDG_CACHE_HOME': str(root / '.cache'), 'TMPDIR': str(root),
            'PATH': '/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin',
            'TERM': 'xterm-256color', 'LANG': 'en_US.UTF-8', 'ICON_REPORT': str(report),
            'ICON_PREVIEW': '1' if args.preview_seconds else '',
        }
        tmux = [args.tmux, '-S', str(root / 'tmux.sock')]

        def run(*arguments):
            return subprocess.run(tmux + list(arguments), env=env, cwd=root,
                                  capture_output=True, text=True, timeout=8)

        result = None
        try:
            start = run('-f', '/dev/null', 'new-session', '-d', '-x', '200', '-y', '55',
                        '-s', 'icons', args.nvim, '-u', str(root / 'init.lua'),
                        '-i', 'NONE', '-n', '-c', 'luafile ' + str(root / 'probe.lua'))
            assert start.returncode == 0, start.stderr
            deadline = time.monotonic() + 30
            while not report.exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            if report.exists():
                result = json.loads(report.read_text())
                print(json.dumps(result, ensure_ascii=False), flush=True)
                if result['result'] == 'PASS' and args.preview_seconds:
                    print('PREVIEW_SOCKET=' + str(root / 'tmux.sock'), flush=True)
                    time.sleep(args.preview_seconds)
            else:
                screen = run('capture-pane', '-p', '-S', '-150', '-t', 'icons')
                print(screen.stdout, flush=True)
                raise RuntimeError('icon probe timed out without a report')
        finally:
            run('kill-server')
            assert run('list-sessions').returncode != 0, 'test tmux server survived'
            deadline = time.monotonic() + 8
            while True:
                processes = subprocess.check_output(['/bin/ps', '-axo', 'command='], text=True)
                remaining = [line for line in processes.splitlines() if str(root) in line]
                if not remaining:
                    break
                assert time.monotonic() < deadline, 'test processes survived: ' + repr(remaining)
                time.sleep(0.1)
        assert result and result['result'] == 'PASS', 'icon integration failed'
    print('PASS: isolated editor/server cleanup')


if __name__ == '__main__':
    main()
