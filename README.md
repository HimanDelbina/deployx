# DeployX (v0.2.0) — Zero-Touch Deployment

> **Production-Grade Autonomous Deployment Manager for Ubuntu Linux servers (22.04 / 24.04 LTS).**  
> Effortlessly deploy, monitor, update, and rollback public and private GitHub projects with zero host bloat. DeployX automatically inspects your repository, detects frameworks (FastAPI, Flask, Django, Generic Python), selects Python runtimes, configures Docker and databases, resolves port conflicts, and manages reverse proxy routing with zero manual configuration.

---

## 🚀 Key Highlights & Philosophy

- **Zero-Touch Autonomous Deployment**: Run `deployx deploy <git-url>` directly. DeployX automatically infers project name, verifies access, detects default branch (`main`/`master`), classifies framework, resolves port conflicts, configures Docker Compose, and provisions services without editing YAML files.
- **Deep Application Detection**: Out-of-the-box detection and containerization for **FastAPI**, **Flask**, **Django**, and **Generic Python** (WSGI/ASGI/entrypoints).
- **Smart Runtime & Dependency Resolution**: Automatically detects Python versions from PEP 621, `pyproject.toml`, `runtime.txt`, or `.python-version`, and supports `uv`, `poetry`, and standard `pip`.
- **Dynamic Port Conflict Auto-Resolution**: Proactively scans host network interfaces and registered DeployX applications; shifts conflicting ports automatically (`8000 -> 8001 -> ...`).
- **Resilient PyPI Mirror Failover**: Actively tests latency to global and regional PyPI mirrors, automatically retrying Docker builds on failover mirrors if timeouts or connection errors occur.
- **Preflight Inspection & Explain Modes**: `--dry-run` validates configurations without mutating the system; `deployx explain <project>` visualizes detection rationale, entrypoint, database, and generated compose services.
- **Managed Reverse Proxy & Automatic HTTPS (Caddy)**: Built-in `deployx domain add <project> <domain>` with DNS resolution checks and automated TLS certificates.
- **Automated Rollback & Audit Timeline**: `--auto-rollback` reverts to previous known-good commit upon health check failure; `deployx events <project>` and `deployx inspect <project>` provide full visibility into deployment lifecycle.
- **Zero Host Python Dependencies**: Application code and dependencies run exclusively inside isolated Docker containers. No pollution of host system packages.
- **SSH Deploy Keys per Project**: Cryptographically generated ED25519 deploy keys stored with strict `0600` permissions.
- **System Cleanup & Self-Update**: `deployx cleanup` removes unreferenced DeployX resources; `deployx self-update` keeps DeployX up to date.

---

## 🖥️ Operating System Compatibility Matrix

| Operating System | Support Level | Status | Notes |
| :--- | :--- | :--- | :--- |
| **Ubuntu 24.04 LTS** (Noble Numbat) | **Validated** | Supported | Recommended production tier |
| **Ubuntu 22.04 LTS** (Jammy Jellyfish) | **Validated** | Supported | Fully tested LTS release |
| **Ubuntu 26.04+** | **Compatibility Mode** | Supported with Warning | Automatically executes in compatibility mode with an advisory warning |
| **Debian 12+** | Experimental | Community | Docker Engine & Compose v2 required |
| Non-Debian / RHEL / Alpine | Unsupported | Untested | Not supported by automated installer |

---

## 📁 System Directory Hierarchy

```
/opt/deployx/
├── projects/       # Projects storage (/opt/deployx/projects/<name>/)
│   └── <project>/
│       ├── repo/                       # Cloned Git repository
│       ├── deployx.yml                 # Versioned project config (0640)
│       ├── .env.production             # Generated production secrets (0600)
│       ├── Dockerfile.deployx          # Auto-generated Dockerfile (if missing in repo)
│       └── docker-compose.deployx.yml  # DeployX Compose manifest
├── keys/           # Per-project ED25519 deploy keys (0700)
│   ├── <project>                       # Private key (chmod 0600)
│   └── <project>.pub                   # Public key (chmod 0644)
├── backups/        # Backup archives (chmod 0700)
├── logs/           # Per-project execution & deployment logs (chmod 0750)
│   └── <project>/
│       └── deploy.log                  # Redacted deployment transcript
├── state/          # Deployment state tracking (JSON / YAML)
│   └── <project>.state.json            # Persistent commit, image, & health status
└── generated/      # Staging & template renders

/etc/deployx/
└── config.yml      # Global DeployX system settings
```

---

## 🛠️ Automated Installation

### One-Line Production Installer
Run the official production bootstrap installer with `sudo`:

```bash
curl -fsSL https://raw.githubusercontent.com/HimanDelbina/deployx/main/install.sh | sudo bash
```

### Installation with Custom Mirror / Restricted Network
If operating behind an enterprise proxy or in a restricted network with an internal PyPI mirror, pass `PIP_INDEX_URL`:

```bash
# Example with a hypothetical mirror:
curl -fsSL https://raw.githubusercontent.com/HimanDelbina/deployx/main/install.sh | sudo env PIP_INDEX_URL=https://mirror.example.com/simple/ bash
```

### Or Install from Local Clone:

```bash
git clone https://github.com/HimanDelbina/deployx.git
cd deployx
sudo bash scripts/install.sh

# With custom mirror:
sudo env PIP_INDEX_URL=https://mirror.example.com/simple/ bash scripts/install.sh
```

### What the installer does (Idempotent & Safe):
1. **Compatibility Check**: Validates Ubuntu release (officially supported on 22.04 and 24.04 LTS; runs in compatibility mode with an advisory warning on newer releases like 26.04).
2. **Preflight Network Diagnostics**: Checks connectivity to GitHub and PyPI/mirror. If connectivity fails, outputs actionable mirror and firewall hints without aborting blindly.
3. **DNS Protection**: **Never** modifies `/etc/resolv.conf`, `systemd-resolved`, or Netplan. All network settings remain untouched.
4. **Installs System Dependencies**: Installs core packages (`curl`, `ca-certificates`, `git`, `openssh-client`, `python3-venv`).
5. **Configures Docker Engine**: Sets up the official Docker repository with secure GPG keyrings and installs Docker Engine with the Compose v2 plugin (`docker compose`).
6. **Isolated Python Venv**: Provisions a virtual environment at `/opt/deployx/venv`. Honors `PIP_INDEX_URL` and preserves the distribution's stable `pip` without forcing an upgrade (upgrade can be enabled via `DEPLOYX_UPGRADE_PIP=1`).
7. **Security Permissions**: Configures `/opt/deployx/` and `/etc/deployx/` with strict least-privilege permissions.
8. **Installs CLI**: Installs `deployx` and symlinks `/usr/local/bin/deployx`.
9. **Automated Diagnostic**: Executes `deployx doctor` to verify system readiness.

---

## 🩺 System Diagnostic: `deployx doctor`

Run the diagnostic suite at any time to verify system health, Docker state, and network connectivity:

```bash
deployx doctor
```

**Diagnostic checks include:**
- **Operating System**: Ubuntu version compatibility (22.04 / 24.04 LTS validated; compatibility mode on newer versions).
- **Python Runtime**: Python 3.10+ runtime check.
- **Git & OpenSSH**: Availability and binary versions.
- **Docker Engine & Compose**: Docker daemon socket connectivity, engine version, and Compose v2 plugin.
- **Disk Space**: Root partition availability (requires `>= 2 GB`, recommends `>= 5 GB`).
- **Filesystem Permissions**: Verification of `/opt/deployx` hierarchy and write permissions.
- **Network & DNS Diagnostics**:
  - DNS resolution of `github.com` (with strict 2-second timeout to prevent hangs).
  - GitHub HTTPS & SSH reachability.
  - Package index reachability (checks official PyPI or custom `PIP_INDEX_URL` configured on the system).

Outputs clear `[ OK ]`, `[ WARN ]`, and `[ ERROR ]` badges alongside actionable recovery instructions.

---

## 📖 Quickstart Guide

### 0. Zero-Touch One-Command Deployment (Recommended)

Deploy a supported application directly from its Git URL in a single command:

```bash
# Public repository
deployx deploy https://github.com/myorg/my-fastapi-app.git

# Or with custom domain, automated rollback, and preflight dry-run:
deployx deploy https://github.com/myorg/my-fastapi-app.git \
  --domain api.example.com \
  --auto-rollback \
  --dry-run
```

**What DeployX does autonomously:**
1. **Repository & Branch Intelligence**: Extracts project name (`my-fastapi-app`), checks connectivity, and queries remote `HEAD` for default branch (`main`/`master`).
2. **Deep Application Detection**: Identifies whether the project is **FastAPI**, **Flask**, **Django**, or **Generic Python**; derives the entrypoint and database backend.
3. **Smart Runtime Selection**: Detects Python version constraints from PEP 621, `pyproject.toml`, or `.python-version` (e.g. `<3.13` -> Python 3.12-slim).
4. **Package Manager Detection**: Automatically configures `uv`, Poetry, or pip based on project lockfiles.
5. **Environment Contract**: Parses required secrets from `.env.example`, `.env.template`, or settings files, securely prompting with masked input or honoring `--env-secret KEY=VALUE`.
6. **Dynamic Port Conflict Resolution**: Automatically scans host ports; if `8000` is occupied, assigns `8001`, `8002`, etc., avoiding port collisions.
7. **Resilient Mirror Failover**: Probes PyPI mirrors; if network errors occur during Docker build, retries automatically with failover mirrors.
8. **Automated Rollback**: If `--auto-rollback` is specified and health checks fail, safely restores previous known-good containers and images.

---

### 1. Deploying a Public GitHub Repository (Two-Step Flow)

DeployX automatically detects placeholders, checks remote connectivity via `git ls-remote`, and verifies that the specified branch exists before saving the project.

#### Step A: Register the project
```bash
deployx project add \
  --name my-django-app \
  --git https://github.com/myorg/my-django-app.git \
  --branch main \
  --framework django \
  --database postgres
```

> **Non-Interactive Mode**: For automated scripts or CI/CD pipelines, pass `--non-interactive` to bypass interactive prompts:
> ```bash
> deployx project add --name my-django-app --git https://github.com/myorg/my-django-app.git --non-interactive
> ```

#### Step B: Trigger the deployment
```bash
deployx deploy my-django-app
```

DeployX will clone the repository, detect dependencies, generate missing configs (`.env.production`, `docker-compose.deployx.yml`), build the Docker image, run migrations in-container, collect static assets, start the web service, and verify the healthcheck.

---

### 2. Deploying a Private GitHub Repository

For private repositories, DeployX enforces a secure verification workflow to ensure authentication succeeds before any deployment is attempted.

```
[project add --private]  ──>  [key create]  ──>  [Add key to GitHub]  ──>  [key verify]  ──>  [deploy]
   (verified: false)                                                        (verified: true)
```

#### Step A: Register the private project
```bash
deployx project add \
  --name client-portal \
  --git git@github.com:myorg/client-portal.git \
  --branch main \
  --private
```
*The project is registered with `verified: false`. Deployments are safely guarded until access is verified.*

#### Step B: Generate the dedicated SSH Deploy Key
```bash
deployx key create client-portal
```
*Creates `/opt/deployx/keys/client-portal` (private, `0600`) and `/opt/deployx/keys/client-portal.pub` (public).*  
*If a key already exists, DeployX prevents accidental overwriting unless `--force` is passed.*

#### Step C: Display and copy the public key
```bash
deployx key show client-portal
```

#### Step D: Add the key to GitHub
1. Open your repository on GitHub.
2. Go to **Settings** -> **Deploy keys** -> **Add deploy key**.
3. **Title**: `DeployX (client-portal)`
4. Paste the public key into the **Key** field (Leave *Allow write access* unchecked).
5. Click **Add key**.

#### Step E: Verify GitHub access without cloning
```bash
deployx key verify client-portal
```
*Tests remote authentication via SSH, verifies that the configured branch exists, retrieves the latest remote commit SHA, and marks the project as verified (`git.verified = true`).*

#### Step F: Deploy
```bash
deployx deploy client-portal
```
*If a user attempts to deploy an unverified private project, DeployX blocks execution and provides immediate instructions to create and verify the deploy key.*

---

## 🛠️ Project Management Commands

### List Projects
```bash
deployx project list
```
Displays registered projects, framework, branch, latest deployed commit, health status, and repository verification status (`Verified: yes / no`). Long repository URLs are gracefully formatted for clean terminal display.

### Inspect Project Details
```bash
deployx project info <project>
```
Displays full project configuration, directory paths, environment state, and whether the repository and deploy key are verified.

### Edit Project Configuration
Update project parameters without manually editing files. DeployX re-validates all fields and re-verifies public repository connectivity:
```bash
deployx project edit my-django-app --branch staging --port 8080 --domain staging.example.com
```

Supported edit flags:
- `--git <url>`: Update repository URL (re-validated and placeholder-checked).
- `--branch <branch>`: Change deployment branch.
- `--domain <domain>`: Update domain name.
- `--port <port>`: Change external host port mapping.
- `--database <postgres|sqlite|mysql|none>`: Change database backend.
- `--redis / --no-redis`: Toggle Redis container.
- `--worker / --no-worker`: Toggle background Celery/RQ worker.
- `--pip-index-url <url>`: Set custom Python PyPI package index URL for container builds.
- `--pip-extra-index-url <url>`: Set secondary/extra package index URL.
- `--pip-trusted-host <host>`: Mark package index hostname as trusted.
- `--clear-pip-index`: Reset Python build mirror back to official PyPI defaults.

### Safe Project Removal
DeployX provides granular project removal flags, prioritizing data safety with preview plans and status tables:

```bash
# Default Safe Unregister
# Removes project configuration and state from DeployX.
# Containers, Docker volumes, backups, and SSH deploy keys are completely preserved!
deployx project remove my-django-app

# Granular Runtime Cleanup:
# Stop and remove containers and images:
deployx project remove my-django-app --remove-containers --remove-images

# Full Runtime Purge (containers + images):
deployx project remove my-django-app --purge

# Remove project deploy key as well:
deployx project remove my-django-app --remove-key

# Destructive Volume Deletion
# Stops containers AND permanently deletes project Docker volumes and database data.
# In interactive mode, prompts to type project name for confirmation.
# Backups and SSH deploy keys are STILL preserved!
deployx project remove my-django-app --purge --remove-volumes

# Scripting / Non-Interactive Removal:
# Safe unregister without interactive prompts:
deployx project remove my-django-app --force
# Purge runtime containers without prompts:
deployx project remove my-django-app --purge --force
# Destructive volume deletion in non-interactive mode (requires explicit --yes):
deployx project remove my-django-app --purge --remove-volumes --non-interactive --yes
```

### Pre-Deployment Project Renaming
Safely rename an undeployed project across filesystem directory paths, `deployx.yml`, persistent state, and SSH deploy keys:

```bash
deployx project rename old-app-name new-app-name
```
- **Safety Rule**: If a project has already been deployed, DeployX halts and prevents renaming (`Project rename is currently supported only before first deployment`) to prevent container, network, and persistent volume drift.
- **Key Material Protection**: Renaming migrates keys to `/opt/deployx/keys/<new-name>` and preserves permissions (`0600`/`0644`) without altering key contents.

### Dedicated SSH Deploy Key Removal
Deploy keys are never deleted automatically during `deployx project remove`. To explicitly delete an SSH key pair:

```bash
deployx key remove my-django-app
# Skip confirmation prompt in automated scripts:
deployx key remove my-django-app --force
```

---

## 🔄 Incremental Updates: `deployx update`

When you push new code to GitHub, simply run:

```bash
deployx update my-django-app
```

**How update intelligence works:**
1. Checks the configured remote branch without pulling blindly.
2. Resolves the latest remote commit SHA.
3. Compares remote SHA with the current deployed commit SHA stored in `/opt/deployx/state/<project>.state.json`.
4. If identical, returns:
   ```
   Already up to date.
   Current Deployed Commit: f022238
   ```
5. If new commits exist:
   ```
   New update available!
   Current:   f022238
   Remote:    4ba128c
   Deploying: f022238 -> 4ba128c
   ```
6. Pulls exact commit, builds image `my-django-app:4ba128c`, executes in-container migrations and collectstatic, updates containers gracefully, verifies healthcheck, and persists new state.

---

## 📊 Command Reference

| Command | Purpose |
| :--- | :--- |
| `deployx deploy <url\|project>` | Deploys directly via Git URL or project name (supports `--dry-run`, `--explain`, `--auto-rollback`, `--domain`) |
| `deployx explain <project>` | Explains framework detection confidence, entrypoint, runtime, and proposed compose configuration |
| `deployx events <project>` | Displays deployment timeline audit trail with stage durations and status (`--json`) |
| `deployx inspect <project>` | Displays comprehensive diagnostic payload across state, containers, and config (`--json`) |
| `deployx update <project>` | Performs zero-drift, SHA-checked incremental update (supports `--build-timeout`, `--auto-rollback`) |
| `deployx rollback <project>` | Safely rolls back project to previously deployed commit and Docker image |
| `deployx status <project>` | Inspects live container statuses and healthcheck state (`--json`) |
| `deployx logs <project>` | Views streaming or tailed Docker container logs (`--tail 100`, `--follow`) |
| `deployx domain add <p> <dom>` | Configures reverse proxy routing and automated HTTPS via Caddy |
| `deployx domain remove <p>` | Removes reverse proxy route for a project |
| `deployx config <cmd>` | Global configuration manager (`show`, `set`, `unset`, `reset`) |
| `deployx project config <cmd>` | Per-project configuration manager (`show`, `set`, `unset`) |
| `deployx project add` | Registers a new project with placeholder detection & verification |
| `deployx project list` | Lists all registered projects with branch, commit, and verification status (`--json`) |
| `deployx project info <project>` | Displays complete project metadata, paths, and deployment state (`--json`) |
| `deployx project edit <project>` | Safely updates project configuration parameters with re-validation |
| `deployx project rename <old> <new>` | Safely renames an undeployed project across filesystem and keys |
| `deployx project remove <project>` | Removes project metadata safely; supports `--purge` and `--remove-volumes` |
| `deployx key create <project>` | Generates dedicated ED25519 deploy key (preserves existing unless `--force`) |
| `deployx key show <project>` | Displays public SSH deploy key |
| `deployx key verify <project>` | Tests SSH authentication against remote repository & verifies branch |
| `deployx key remove <project>` | Explicitly removes SSH deploy key pair with confirmation |
| `deployx generate <project>` | Safely regenerates DeployX-owned files (`Dockerfile.deployx`, compose manifest) |
| `deployx init` | System initialization wizard (configures directories, mirrors, proxy defaults) |
| `deployx cleanup` | Cleans orphaned DeployX containers, builder cache, dangling images, and unused volumes |
| `deployx doctor` | Runs full system, Docker, permission, and network diagnostics (`--orphans`, `--docker-network`) |
| `deployx project doctor <project>` | Runs targeted container, volume, network, and database checks for a project |
| `deployx self-update` | Checks for and executes autonomous in-place updates from GitHub releases |

---

## ⚙️ Configuration Reference: `deployx.yml`

Located at `/opt/deployx/projects/<project>/deployx.yml`:

```yaml
version: 1
project:
  name: store-portal
git:
  repository: git@github.com:myorg/store-portal.git
  branch: main
  private: true
  verified: true        # Automatically maintained by DeployX
build:
  python:
    index_url: https://mirrors.aliyun.com/pypi/simple/   # Optional custom PyPI mirror
    extra_index_url: null
    trusted_host: mirrors.aliyun.com
deployment:
  framework: django
  domain: portal.example.com
  database: postgres    # postgres | sqlite | mysql | none
  redis: true           # Enable standalone Redis container
  docker:
    compose_file: docker-compose.deployx.yml
    service_name: web
    port: 8000
  healthcheck:
    enabled: true
    path: /
    port: 8000
    timeout: 10
    retries: 6
    interval: 5
```

---

## 🔒 Security Architecture

1. **Placeholder & Input Validation**: Strict validation rejects placeholder names, example domains, and invalid git URLs before any action is taken.
2. **Subprocess Isolation**: All OS calls use `run_command` with explicit string lists and `shell=False`. Prevents command injection attacks.
3. **Path Traversal Guards**: Project names, branches, and custom paths are resolved and strictly validated against directory traversal (`..`, absolute path overrides).
4. **Secret Masking & Redaction**: The core logging engine redacts all SSH private keys, `SECRET_KEY`, `POSTGRES_PASSWORD`, and sensitive variable patterns before writing to disk or terminal.
5. **Dedicated Per-Project Keys**: No single shared SSH key across repositories; each project has its own ED25519 keypair.
6. **Key Overwrite Protection**: `deployx key create` refuses to overwrite an existing key unless `--force` is explicitly provided.
7. **No Host Dependency Leaks**: Python libraries, Gunicorn, psycopg2, etc. are installed inside Docker images, preserving the stability of the Ubuntu host.
8. **Isolated Docker Networks & Volumes**: Each project resides on `deployx_<project>_net` with named volumes `deployx_<project>_pgdata` and `deployx_<project>_static`. Projects cannot interfere with one another.
9. **DNS Configuration Integrity**: The installer and CLI never alter system DNS resolvers or systemd-resolved.

---

## ❓ Troubleshooting & Actionable Guidance

| Problem | Root Cause | Actionable Fix |
| :--- | :--- | :--- |
| `Repository is not verified` | Private repository was registered but key was not verified | Run `deployx key create <project>`, add public key to GitHub, then run `deployx key verify <project>`. |
| `Git URL contains placeholder values` | URL includes `REAL_REPOSITORY`, `USER`, `REPO`, or `example.com` | Provide your real GitHub repository URL (e.g. `https://github.com/myorg/myapp.git`). |
| `Public repository verification failed` | Repository does not exist, is private, or network is down | Check spelling, ensure the repo is public (or use `--private`), or check internet connectivity. |
| `Git authentication failed (publickey)` | SSH key missing or not added to GitHub | Run `deployx key show <project>`, copy public key to GitHub repository -> Settings -> Deploy keys, then run `deployx key verify <project>`. |
| `PyPI / mirror connection failed` | Restricted network or custom mirror unreachable | Pass `PIP_INDEX_URL` during installation, e.g. `sudo env PIP_INDEX_URL=https://mirror.example.com/simple/ bash scripts/install.sh`. |
| `Docker daemon is not running` | Docker service inactive | Start the Docker service with `sudo systemctl start docker`. |
| `Port is already allocated` | Another service is using port 8000 | Check listening ports with `sudo ss -tulpn` and assign a distinct port using `deployx project edit <project> --port <new_port>`. |
| `Healthcheck failed or timed out` | Application crashed on startup or wrong health path | Inspect container logs with `deployx logs <project>` or adjust healthcheck path in `deployx.yml`. |

---

## 🗺️ Roadmap (Future Versions)

- **v0.2**: Webhook listener for automated GitHub push deployments (`deployx webhook listen`).
- **v0.3**: Automated atomic rollbacks to previous commit image (`deployx rollback <project>`).
- **v0.4**: Web Dashboard UI with real-time SSE deployment log streaming.
- **v0.5**: Automated database backups and S3 snapshots.
- **v0.6**: Multi-Server cluster management.

