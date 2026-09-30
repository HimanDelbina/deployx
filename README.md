# DeployX (v0.1.6)

> **Production-Grade Autonomous Deployment Manager for Ubuntu Linux servers (22.04 / 24.04 LTS).**  
> Effortlessly register, verify, deploy, and incrementally update public and private GitHub projects using Docker and Docker Compose v2 with zero host bloat.

---

## 🚀 Key Highlights & Philosophy

- **Production-Ready & Hardened**: Engineered specifically for bare-metal / VPS Ubuntu Linux. Officially validated on **Ubuntu 22.04 LTS** and **24.04 LTS**, with automated compatibility mode for newer releases (such as Ubuntu 26.04).
- **Zero Host Python Dependencies**: Application dependencies run exclusively inside isolated Docker containers. No pollution of host system packages.
- **Strict Registration Validation**: Prevents invalid configurations upfront. Detects and rejects placeholder URLs (e.g. `REAL_REPOSITORY`, `USER`, `example.com`), verifies public repositories before saving, and enforces deploy key verification before private deployments.
- **SSH Deploy Keys per Project**: Cryptographically generated ED25519 deploy keys stored with strict `0600` permissions. Existing keys are protected from accidental overwrite unless `--force` is specified. Never exposes or logs private keys.
- **Safe Lifecycle Management**: Supports non-destructive project removal (`deployx project remove`) with optional `--purge`, configuration edits (`deployx project edit`), and non-interactive scripting (`--non-interactive`).
- **Network & Mirror Friendly**: Fully supports custom package mirrors via `PIP_INDEX_URL`, includes preflight network diagnostics, preserves system DNS configurations untouched, and avoids forced pip upgrades.
- **Existing Configuration Protection**: Respects and validates existing repository `Dockerfile`, `docker-compose.yml`, and `.env.example`. DeployX writes non-intrusive auxiliary files (e.g. `docker-compose.deployx.yml`) so your Git tree is never dirtied or overwritten.
- **Zero Insecure Defaults**: High-entropy cryptographic secrets generated automatically using Python's `secrets` module (`SECRET_KEY`, `POSTGRES_PASSWORD`).
- **Strict Version Tracking**: Docker images are automatically tagged with Git commit SHAs (`myproject:4ba128c`), laying the foundation for instant rollbacks and deterministic state reproduction.
- **Automated Update Intelligence**: `deployx update` inspects remote repository HEADs before building, preventing unnecessary rebuilds if the codebase is already up to date.
- **Robust Security Perimeter**: All subprocesses run with `shell=False`, strict argument arrays, regex-enforced project and URL sanitization, path traversal prevention, and automated secret redaction across all logs.

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

### 1. Deploying a Public GitHub Repository

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
| `deployx project add` | Registers a new project with placeholder detection & verification |
| `deployx project list` | Lists all registered projects with branch, commit, and verification status |
| `deployx project info <project>` | Displays complete project metadata, paths, and deployment state |
| `deployx project edit <project>` | Safely updates project configuration parameters with re-validation |
| `deployx project rename <old> <new>` | Safely renames an undeployed project across filesystem and keys |
| `deployx project remove <project>` | Removes project metadata safely; supports `--purge` and `--delete-volumes` |
| `deployx key create <project>` | Generates dedicated ED25519 deploy key (preserves existing unless `--force`) |
| `deployx key show <project>` | Displays public SSH deploy key |
| `deployx key verify <project>` | Tests SSH authentication against remote repository & verifies branch |
| `deployx key remove <project>` | Explicitly removes SSH deploy key pair with confirmation |
| `deployx deploy <project>` | Builds, provisions, and deploys the project (supports `--build-timeout`, `--regenerate`) |
| `deployx update <project>` | Performs zero-drift, SHA-checked incremental update (supports `--build-timeout`) |
| `deployx rollback <project>` | Safely rolls back project to previously deployed commit and Docker image |
| `deployx generate <project>` | Safely regenerates DeployX-owned files (`Dockerfile.deployx`, compose manifest) |
| `deployx status <project>` | Inspects live container statuses and healthcheck state |
| `deployx logs <project>` | Views streaming or tailed Docker container logs (`--tail 100`, `--follow`) |
| `deployx restart <project>` | Restarts project containers safely |
| `deployx stop <project>` | Stops running containers |
| `deployx start <project>` | Starts stopped containers |
| `deployx doctor` | Runs full system, Docker, permission, and network diagnostics (`--orphans`, `--docker-network`) |
| `deployx project doctor <project>` | Runs targeted container, volume, network, and database checks for a project |

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

