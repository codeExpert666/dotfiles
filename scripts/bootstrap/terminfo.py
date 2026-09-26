#!/usr/bin/env python3
"""Prepare only xterm-ghostty for the bootstrap user; no network or GUI needed."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


SOURCE = Path(__file__).with_name("terminfo") / "xterm-ghostty.terminfo"
NAME = "xterm-ghostty"


def default_env():
    # Keep the real target HOME. A Ghostty bundle in TERMINFO must not mask a
    # missing entry in the user's default search path (including ~/.terminfo).
    env = dict(os.environ)
    for key in ("TERMINFO", "TERMINFO_DIRS"):
        env.pop(key, None)
    return env


def lookup(database=None):
    argv = ["infocmp", "-x"]
    if database is not None:
        argv.extend(("-A", str(database)))
    return subprocess.run([*argv, NAME], env=default_env(), capture_output=True,
                          text=True, timeout=30)


def require_lookup(database=None):
    result = lookup(database)
    if result.returncode:
        sys.stderr.write(result.stderr)
        raise RuntimeError(f"infocmp cannot resolve {NAME} in {database or 'the default search path'} "
                           f"(exit {result.returncode})")


def real_directory(path):
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError(f"expected a real directory; preserved {path}")


def prepare():
    existing = lookup()
    if existing.returncode == 0:
        print(f"REUSE: {NAME} resolves in the default search path")
        return
    sys.stderr.write(existing.stderr)
    print(f"PREPARE: {NAME} from bundled Ghostty 1.3.1 source ({SOURCE})", flush=True)
    home = Path(os.environ["HOME"])
    if not home.is_absolute() or not home.is_dir():
        raise RuntimeError(f"expected an existing absolute HOME: {home}")
    # Compile away from the live database; -x preserves extended capabilities.
    with tempfile.TemporaryDirectory(prefix="dotfiles-terminfo-") as scratch:
        database = Path(scratch) / "database"
        database.mkdir()
        subprocess.run(["tic", "-x", "-o", str(database), str(SOURCE)],
                       env=default_env(), check=True, timeout=30)
        require_lookup(database)
        entries = [path for path in database.rglob("*") if not path.is_dir()]
        if len(entries) != 1 or entries[0].relative_to(database).as_posix() not in (
                f"x/{NAME}", f"78/{NAME}") or entries[0].is_symlink():
            raise RuntimeError("tic must produce only xterm-ghostty (no aliases or database files)")
        # Recheck before publication in case another installer prepared it.
        if lookup().returncode == 0:
            print(f"REUSE: {NAME} became available during preparation")
            return
        root = home / ".terminfo"
        destination = root / entries[0].relative_to(database)
        for directory in (root, destination.parent):
            directory.mkdir(mode=0o700, exist_ok=True)
            real_directory(directory)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"existing entry is not replaced; inspect {destination}")
        # Publish without replacement, on the destination filesystem. Existing
        # files, hard links and symlinks (even broken ones) are never overwritten.
        with tempfile.NamedTemporaryFile(prefix=".xterm-ghostty-", dir=destination.parent) as staged:
            shutil.copyfile(entries[0], staged.name)
            os.link(staged.name, destination)
            try:
                require_lookup()
            except BaseException:
                if destination.exists() and os.path.samefile(staged.name, destination):
                    destination.unlink()
                raise
    print(f"READY: {NAME} installed at {destination}; default-path infocmp passed")


if __name__ == "__main__":
    try:
        prepare()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"FAIL terminfo: {exc}", file=sys.stderr)
        raise SystemExit(1)
