# Changelog

All notable changes to DeployX are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
