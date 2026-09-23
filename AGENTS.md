# Repository Guidelines

## Project Structure & Module Organization

- The core prepares, deploys, and checks a personal terminal environment on an
  existing macOS or Ubuntu machine. Multipass is an optional Ubuntu development
  machine extension that creates a guest and runs the core server bootstrap there.
- Top-level GNU Stow packages (`zsh/`, `git/`, `nvim/`, `ghostty/`, etc.) mirror
  paths beneath HOME, usually `<package>/.config/<app>/`.
- Neovim Lua lives in `nvim/.config/nvim/lua/`; Ghostty shader assets live in
  `ghostty-macos/.config/ghostty/shaders/`.
- AI skills live in `skills/src/`, excluded from deployment by the
  package's `.stow-local-ignore`; per-skill directory links in the same package
  expose them at the clients' discovery paths. `scripts/layout.bash` declares
  the required clients per skill; `astra-sol` has only an `.agents` entry.
- Codex custom agents live in `codex/agents/` and deploy as regular copies
  outside Stow because Codex rejects symlinked role files; personal Codex config,
  credentials, and runtime state remain outside version control.
- `scripts/` contains the core deploy, doctor, and bootstrap entrypoints plus the
  optional Multipass entrypoint. Their helpers stay in corresponding subdirectories;
  `layout.bash` centralizes core deployment paths and package lists.
- `environments/multipass/` contains VM defaults, cloud-init, and the installer
  manifest. It is not a Stow package; its [README](environments/multipass/README.md)
  owns the extension's setup, daily use, state, and recovery guidance.
- `tests/` contains five independent offline suites, a separate live Multipass
  acceptance entrypoint, and shared fixtures in `tests/support/`.
- The root README owns the terminal environment quick start and links to the
  extension. `scripts/README.md` documents core script behavior; `tests/README.md`
  navigates all suites. Historical plans and validation reports live in `docs/`.

## Build, Test, and Development Commands

There is no build step. Run these commands from the repository root:

- `bash scripts/deploy.sh --dry-run`: preview configuration links and conflicts.
- `bash scripts/bootstrap.sh --dry-run --profile server`: preview environment
  preparation; use `desktop` for GUI tools and fonts.
- `bash scripts/doctor.sh`: check dependencies, deployment, and configuration.
- `bash tests/all.sh`: run the five offline suites, including Multipass.
- `bash tests/multipass-live.sh --help`: review explicit two-image VM acceptance.
- `bash tests/config-loading.sh`: verify application configuration independently.

See [scripts/README.md](scripts/README.md) for maintenance and static checks.

## Coding Style & Naming Conventions

Keep Bash compatible with 3.2, using tabs and `shfmt -ci -sr`. Validate with
`bash -n`, `shellcheck -x`, and `shfmt -d -ci -sr`. Format Zsh with Shuck and
check syntax with `zsh -n`. Lua uses two spaces and StyLua; Neovim's
`stylua.toml` sets a 120-column width. Python uses four spaces and snake_case.
Use `.sh` for public shell entrypoints and `.bash` for sourced Bash helpers.
Sourced helpers declare functions or data; entrypoints own runtime state and traps.

## Testing Guidelines

Tests use custom Bash harnesses and Python's standard-library `unittest`, with
temporary HOME directories, repository copies, and offline fixtures. Name Python
cases `test_<behavior>` and give Bash cases descriptive labels. For example:

```sh
bash tests/doctor.sh DoctorTests.test_multiple_independent_link_faults
```

For behavior changes, cover relevant failure paths and run the affected suite;
use the full suite for changes spanning suites. No numeric coverage threshold
is configured. Report optional-application `SKIP` results and distinguish simulated
platform checks from native validation. See [tests/README.md](tests/README.md).

## Commit & Pull Request Guidelines

Use the `type(scope): summary` commit message convention; scope is optional. Types
include `feat`, `fix`, `docs`, `test`, `refactor`, and `chore`.
Example: `feat(nvim): configure Markdown editing environment`.
Keep PRs focused, describe behavior and affected platforms, list validation
results, link relevant issues, and include screenshots for visual changes.
Update applicable READMEs.

## Configuration Safety

Preview deployment before using `--apply`. Keep personal Git identity, credentials,
and machine-specific overrides outside tracked configuration. Preserve default
XDG paths and use `local.zsh` or `local.zprofile` for local Zsh settings.
