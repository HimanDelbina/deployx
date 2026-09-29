# Changelog

All notable changes to DeployX are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.4] - 2026-09-29

### Added
- **Production-Grade Streaming Command Runner (`run_command_streaming`)**: Real-time line-by-line streaming of stdout and stderr via non-blocking worker threads, preserving exit codes, enforcing timeouts, and redacting sensitive strings on the fly.
- **Docker BuildKit Live Output & Progress Parsing**: Integrates `BUILDKIT_PROGRESS=plain` and `--progress=plain` into `docker compose build`. Streamed output is parsed in real time to extract step counters (`5/10 steps`), approximate build percentages (`~50%`), and current operations without freezing the terminal.
- **Overall Deployment Progress Tracking**: Clear stage progression across all 12 deployment stages (`Overall: 58% [███████████░░░░░░░░]`), clearly separating overall stage progress from Docker build step progress.
- **Subprocess Activity Heartbeat**: Automatically emits lightweight progress heartbeats every 25 seconds for silent operations, keeping operators informed of elapsed time and activity without flooding the terminal.
- **Build Stall Warnings & Contextual Network Hints**: Detects long-running silent operations exceeding threshold (120s) with actionable diagnostic hints, specifically highlighting potential registry, PyPI, apt, or network constraints if stalled during network-bound operations.
- **Verbose & Plain Modes**:
  - `deployx deploy <project> --verbose` / `deployx update <project> --verbose`: Displays full underlying command output in real time for detailed debugging.
  - `deployx deploy <project> --plain` (and `--no-progress`): Clean text-only output without Rich Live redraws, ideal for CI/CD pipelines, log aggregators, and script automation.
- **Synchronous Build & Deployment Logging**: All streamed build output is recorded to `/opt/deployx/logs/<project>/deploy.log` with secret redaction applied.
- **Structured Failure Diagnostics**: On build failure, displays the exact failing stage, last build operation, exit code, and recent output snippet alongside troubleshooting instructions.
- **Graceful Ctrl+C Handling**: Captures `KeyboardInterrupt` cleanly, terminates child subprocess trees to prevent orphaned processes, marks deployment state as cancelled, and exits without raw tracebacks.

---

## [0.1.3] - 2026-09-29

### Fixed
- **Invalid Project Discovery**: Fixed `deployx project list` silently dropping invalid or legacy projects and printing "No projects registered yet." when directories existed. Invalid projects are now prominently displayed with `INVALID_CONFIG` status.
- **Raw Pydantic Tracebacks on Config Inspection**: Replaced raw `ValidationError` crashes in `deployx project info` with a structured diagnostic panel reporting validation problems and recovery actions.
- **Unprivileged Permission Error Stack Traces**: Caught `PermissionError` when running commands without sudo on `/opt/deployx`, displaying clean elevated privilege advice (`sudo deployx <command>`) and eliminating raw tracebacks.
- **Broken Project Removal**: Enabled `deployx project remove` to cleanly unregister projects with malformed or invalid configurations while continuing to preserve Docker volumes, backups, and SSH deploy keys.
- **Deployment & Key Guarding**: Preflight checks in `deploy`, `update`, `key create`, and `key verify` now cleanly intercept invalid configs with repair guidance and zero tracebacks.

### Added
- **Legacy Configuration Repair Flow**: Decoupled raw YAML loading (`load_project_raw`) from schema validation (`load_project_config`) in `deployx project edit`, allowing users to fix invalid fields on existing legacy configs without upfront validation failures.
- **Unified CLI Exception Handling (`@handle_cli_exceptions`)**: Robust decorator across all Typer commands catching `PermissionError`, `ValidationError`, `SecurityError`, `CommandError`, and `ValueError` to ensure human-readable error output and consistent non-zero exit codes.

---

## [0.1.2] - 2026-09-29

### Added
- **Project Rename (`deployx project rename <old> <new>`)**: Safe pre-deployment project renaming across filesystem, `deployx.yml`, persistent state, and SSH deploy keys. Strictly guards against renaming deployed projects to avoid container and volume drift.
- **Interactive & Flag-Based Project Editing (`deployx project edit`)**: Supports interactive editor fallback when no flags are given, displays a side-by-side configuration diff preview panel, re-validates all fields before committing, and provides deployed project redeployment guidance.
- **Repository Verification Invalidation & Transition**: Changing repository URL, branch, or privacy mode automatically invalidates prior verification. Seamlessly handles Public-to-Private transitions (instructions to generate key) and Private-to-Public transitions (re-verifies via `git ls-remote` while safely preserving existing SSH keys).
- **Three-Tier Project Removal (`deployx project remove`)**:
  - *Tier A (Safe unregister, default)*: Removes project registration and state; completely preserves running containers, Docker volumes, backups, and SSH keys.
  - *Tier B (Runtime purge, `--purge`)*: Stops and removes project containers and networks; preserves Docker volumes, database data, backups, and SSH keys.
  - *Tier C (Destructive volume deletion, `--purge --delete-volumes`)*: Destructive volume removal requiring project name typing in interactive mode or `--yes` in non-interactive mode. Backups and SSH keys remain preserved.
- **Explicit Deploy Key Removal (`deployx key remove <project>`)**: Project removal preserves SSH deploy keys by default. Keys can only be deleted via explicit confirmation with `deployx key remove`.
- **Duplicate Project & Conflict Protection**: `deployx project add` prevents duplicate project registrations and conflicting filesystem directories, directing users to `deployx project edit`.
- **Project Name Security & Path Traversal Guards**: Strictly rejects path traversal characters (`..`, `/`, `\`, `~`), spaces, shell metacharacters, and reserved system directory names (`keys`, `backups`, `logs`, `state`, `system`, etc.).
- **Expanded Project Info**: `deployx project info` displays `Created At`, `Updated At`, `Deploy Key Status`, and complete deployment state.
- **Audit Logging**: Logs all project additions, edits, renames, removals, and verification state transitions.

---

## [0.1.1] - 2026-09-29

### Fixed
- **Placeholder Repository Acceptance**: Prevent registration of obvious placeholder Git URLs and project names (e.g., `REAL_REPOSITORY`, `USER`, `REPO`, `OWNER`, `example.com`).
- **Missing Repository Verification**: Enforce remote reachability and branch existence check (`git ls-remote`) before registering public repositories.
- **Private Repository Verification State**: Implemented clear verification tracking (`git.verified`) preventing unverified private repository deployments.
- **Missing Safe Project Removal**: Implemented `deployx project remove` with confirmation prompts and optional `--purge` flag.
- **Installer PyPI Hard Dependency**: Removed forced dependency on `files.pythonhosted.org` and default PyPI by honoring `PIP_INDEX_URL` throughout the installation pipeline.
- **Forced Pip Upgrade**: Removed mandatory pip upgrade during virtual environment creation, making it opt-in via `DEPLOYX_UPGRADE_PIP=1`.
- **Weak Network Error Messaging**: Replaced uninformative pip stack traces with actionable diagnostics, timeout parameters, and retry guidance.
- **Ubuntu Compatibility Messaging**: Improved Doctor and installer diagnostic messages for newer, unvalidated releases (e.g. Ubuntu 26.04) running in compatibility mode.

### Added
- **Project Removal**: Added `deployx project remove <project>` command with safe metadata cleanup and conservative `--purge` capabilities.
- **Project Edit**: Added `deployx project edit <project>` command for updating repository, branch, domain, framework, and database with strict re-validation.
- **Repository Verified State**: Added persistent `verified` attribute in `deployx.yml` and visible verification indicators in `project list` and `project info`.
- **Non-Interactive Registration**: Added `--non-interactive` flag to `deployx project add` for automated CI/CD and scripted deployments without terminal prompts.
- **Custom PIP Index Support**: Official support for `PIP_INDEX_URL` in root and internal installers with preflight reachability checks.
- **Doctor Network Diagnostics**: Added lightweight reachability checks for GitHub, package index, and DNS resolution to detect partial or restricted connectivity.

---

## [0.1.0] - 2026-09-28

### Added
- Initial public release of DeployX.
- Zero-host-bloat Docker Compose v2 deployment pipeline.
- Automatic framework detection for Django.
- Per-project isolated ED25519 SSH deploy key generation and verification.
- Production environment generator (`.env.production`) with high-entropy cryptographic secrets.
- Diagnostic suite (`deployx doctor`).
- System lifecycle commands: `deploy`, `update`, `status`, `logs`, `restart`, `stop`, `start`.
