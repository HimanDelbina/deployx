# DeployX (v0.1)

> **Production-Grade Autonomous Deployment Manager for Ubuntu Linux servers (22.04 / 24.04 LTS).**  
> Effortlessly register, deploy, and incrementally update public and private GitHub projects using Docker and Docker Compose v2 with zero host bloat.

---

## 🚀 Key Highlights & Philosophy

- **Production-Ready by Design**: Engineered strictly for bare-metal / VPS Ubuntu Linux (22.04 & 24.04 LTS).
- **Zero Host Python Dependencies**: Application dependencies run exclusively inside isolated Docker containers. No pollution of host system packages.
- **SSH Deploy Keys per Project**: Cryptographically generated ED25519 deploy keys stored with strict `0600` permissions. Never exposes or logs private keys.
- **Existing Configuration Protection**: Respects and validates existing repository `Dockerfile`, `docker-compose.yml`, and `.env.example`. DeployX writes non-intrusive auxiliary files (e.g. `docker-compose.deployx.yml`) so your Git tree is never dirtied or overwritten.
- **Zero Insecure Defaults**: High-entropy cryptographic secrets generated automatically using Python's `secrets` module (`SECRET_KEY`, `POSTGRES_PASSWORD`).
- **Strict Version Tracking**: Docker images are automatically tagged with Git commit SHAs (`myproject:4ba128c`), laying the foundation for instant rollbacks and deterministic state reproduction.
- **Automated Update Intelligence**: `deployx update` inspects remote repository HEADs before building, preventing unnecessary rebuilds if the codebase is already up to date.
- **Robust Security Perimeter**: All subprocesses run with `shell=False`, strict argument arrays, regex-enforced project and URL sanitization, path traversal prevention, and automated secret redaction across all logs.

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

## 🛠️ Automated Installation (Ubuntu 22.04 / 24.04 LTS)

### One-Line Production Installer
Run the official production bootstrap installer with `sudo`:

```bash
curl -fsSL https://raw.githubusercontent.com/HimanDelbina/deployx/main/install.sh | sudo bash
```

### Or Install from Local Clone:

```bash
git clone https://github.com/HimanDelbina/deployx.git
cd deployx
sudo bash scripts/install.sh
```

### What the installer does (Idempotent):
1. Verifies Ubuntu 22.04 or 24.04 LTS.
2. Installs core dependencies (`curl`, `ca-certificates`, `git`, `openssh-client`, `python3-venv`).
3. Configures the official Docker repository with secure GPG keyrings.
4. Installs Docker Engine and Docker Compose v2 plugin (`docker compose`).
5. Configures `/opt/deployx/` and `/etc/deployx/` with strict security permissions.
6. Provisions an isolated Python virtual environment at `/opt/deployx/venv`.
7. Installs the `deployx` CLI and links `/usr/local/bin/deployx`.
8. Executes `deployx doctor` to verify system readiness.

---

## 🩺 System Diagnostic: `deployx doctor`

Run the diagnostic suite at any time to verify system health:

```bash
deployx doctor
```

**Diagnostic checks include:**
- Operating System & Ubuntu version (22.04 / 24.04)
- Python runtime (`>= 3.10`)
- Git version & binary availability
- SSH client (`OpenSSH`)
- Docker CLI & Compose v2 plugin
- Docker daemon socket connectivity & engine version
- Root disk space availability (requires `>= 2 GB`, recommends `>= 5 GB`)
- DeployX directory presence and filesystem writability

Outputs clear `[ OK ]`, `[ WARN ]`, and `[ ERROR ]` badges alongside actionable recovery instructions.

---

## 📖 Quickstart Guide

### 1. Deploying a Public GitHub Repository

#### Step A: Register the project
```bash
deployx project add \
  --name my-django-app \
  --git https://github.com/example/my-django-app.git \
  --branch main \
  --framework django \
  --database postgres
```

#### Step B: Trigger the deployment
```bash
deployx deploy my-django-app
```

DeployX will clone the repository, detect dependencies, generate missing configs (`.env.production`, `docker-compose.deployx.yml`), build the Docker image, run migrations in-container, collect static assets, start the web service, and verify the healthcheck.

---

### 2. Deploying a Private GitHub Repository

For private repositories, DeployX uses a dedicated, isolated ED25519 SSH deploy key.

#### Step A: Register the private project
```bash
deployx project add \
  --name client-portal \
  --git git@github.com:myorg/client-portal.git \
  --branch main \
  --private
```

#### Step B: Generate the dedicated SSH Deploy Key
```bash
deployx key create client-portal
```
*Creates `/opt/deployx/keys/client-portal` (private, `0600`) and `/opt/deployx/keys/client-portal.pub` (public).*

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
*Tests remote authentication and resolves the latest remote commit SHA.*

#### Step F: Deploy
```bash
deployx deploy client-portal
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

## 📊 Monitoring & Lifecycle Commands

| Command | Purpose |
| :--- | :--- |
| `deployx project list` | Lists all registered projects, frameworks, branches, commits, and statuses |
| `deployx project info <project>` | Displays complete project metadata, paths, and deployment state |
| `deployx status <project>` | Inspects live container statuses and healthcheck state |
| `deployx logs <project>` | Views streaming or tailed Docker container logs (`--tail 100`, `--follow`) |
| `deployx restart <project>` | Restarts project containers safely |
| `deployx stop <project>` | Stops running containers |
| `deployx start <project>` | Starts stopped containers |
| `deployx key show <project>` | Displays public SSH deploy key |
| `deployx key verify <project>` | Tests SSH authentication against remote repository |

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

1. **Subprocess Isolation**: All OS calls use `run_command` with explicit string lists and `shell=False`. Prevents command injection attacks.
2. **Path Traversal Guards**: Project names, branches, and custom paths are resolved and strictly validated against directory traversal (`..`, absolute path overrides).
3. **Secret Masking & Redaction**: The core logging engine redacts all SSH private keys, `SECRET_KEY`, `POSTGRES_PASSWORD`, and sensitive variable patterns before writing to disk or terminal.
4. **Dedicated Per-Project Keys**: No single shared SSH key across repositories; each project has its own ED25519 keypair.
5. **No Host Dependency Leaks**: Python libraries, Gunicorn, psycopg2, etc. are installed inside Docker images, preserving the stability of the Ubuntu host.
6. **Isolated Docker Networks & Volumes**: Each project resides on `deployx_<project>_net` with named volumes `deployx_<project>_pgdata` and `deployx_<project>_static`. Projects cannot interfere with one another.

---

## ❓ Troubleshooting & Actionable Guidance

| Problem | Root Cause | Actionable Fix |
| :--- | :--- | :--- |
| `Git authentication failed (publickey)` | SSH key missing or not added to GitHub | Run `deployx key show <project>`, copy public key to GitHub repository -> Settings -> Deploy keys, then run `deployx key verify <project>`. |
| `Docker daemon is not running` | Docker service inactive | Start the Docker service with `sudo systemctl start docker`. |
| `Port is already allocated` | Another service is using port 8000 | Check listening ports with `sudo ss -tulpn` and assign a distinct port in `deployx.yml`. |
| `Healthcheck failed or timed out` | Application crashed on startup or wrong health path | Inspect container logs with `deployx logs <project>` or adjust healthcheck path in `deployx.yml`. |
| `Deploy key verification failed` | Host key verification or wrong repo URL | Check repository URL in `deployx.yml` and verify GitHub host key with `ssh-keyscan github.com >> ~/.ssh/known_hosts`. |

---

## 🗺️ Roadmap (Future Versions)

- **v0.2**: Webhook listener for automated GitHub push deployments (`deployx webhook listen`).
- **v0.3**: Automated atomic rollbacks to previous commit image (`deployx rollback <project>`).
- **v0.4**: Web Dashboard UI with real-time SSE deployment log streaming.
- **v0.5**: Automated database backups and S3 snapshots.
- **v0.6**: Multi-Server cluster management.
