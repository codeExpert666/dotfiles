"""Real ncurses tools with an isolated default database, independent of the host."""

from pathlib import Path
import shlex
import shutil

INFOCMP = shutil.which("infocmp")
TIC = shutil.which("tic")


def tools(directory, database=None):
    infocmp, tic = INFOCMP, TIC
    if not infocmp or not tic:
        raise RuntimeError("native tic and infocmp are required for terminfo regression tests")
    directory = Path(directory)
    for name in ("tic", "infocmp"):
        (directory / name).unlink(missing_ok=True)
    (directory / "tic").symlink_to(tic)
    # Explicit -A staging checks pass through. Default-path checks still use the
    # caller's HOME; -A prevents a host-installed entry from hiding missing data.
    target = shlex.quote(str(database)) if database is not None else '"$HOME/.terminfo"'
    wrapper = directory / "infocmp"
    wrapper.write_text('#!/bin/sh\n'
                       'if test "${TERMINFO+x}" = x || test "${TERMINFO_DIRS+x}" = x; then\n'
                       '  echo "terminfo probe must clear search overrides" >&2; exit 97\nfi\n'
                       'case " $* " in\n'
                       f'  *" -A "*) exec {shlex.quote(infocmp)} "$@" ;;\n'
                       f'  *) exec {shlex.quote(infocmp)} -A {target} "$@" ;;\nesac\n')
    wrapper.chmod(0o755)
