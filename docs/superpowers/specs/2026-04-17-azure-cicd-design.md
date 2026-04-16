# BOQ RateIQ — Azure Deployment & CI/CD Design

> **For agentic workers:** Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this spec task-by-task.

**Goal:** Deploy BOQ RateIQ to Azure using Container Apps + managed PostgreSQL, with a GitHub Actions CI/CD pipeline that tests on every PR and deploys automatically on merge to `main`. All infrastructure defined in Bicep.

**Architecture:** Azure Container Apps hosts the FastAPI backend, Qdrant, and Redis as three separate containers in one environment. PostgreSQL runs as a managed Azure Flexible Server. The React frontend is served free via Azure Static Web Apps. All secrets live in Azure Key Vault — never in code or Git.

**Tech Stack:** Azure Container Apps, Azure Container Registry, Azure Database for PostgreSQL Flexible Server, Azure Static Web Apps, Azure Key Vault, Bicep (IaC), GitHub Actions (CI/CD), Docker multi-stage build, `uv` for Python deps.

---

## 1. Azure Resource Architecture

### Resource Group
- Name: `rg-boq-rateiq`
- Region: `eastus` (cheapest for this stack)
- All resources live in this single group — easy to delete everything at once

### Azure Container Registry (ACR)
- Name: `rateiqacr` (must be globally unique — append 4 random digits if taken)
- SKU: **Basic** (~$5/month)
- Admin account: enabled (allows simple username/password auth from GitHub Actions)
- Stores: `rateiq-api:latest` Docker image

### Azure Container Apps Environment
- Name: `cae-boq-rateiq`
- One shared environment for all three container apps
- Internal networking: containers talk to each other via internal DNS (`http://rateiq-qdrant`, `http://rateiq-redis`)
- Log Analytics workspace attached for basic monitoring

### Container App: `rateiq-api` (FastAPI backend)
- Image: `rateiqacr.azurecr.io/rateiq-api:latest`
- CPU: 0.5 cores, Memory: 1Gi
- Scale: min 0, max 1 (scale-to-zero when idle = free)
- Ingress: external HTTPS, port 8000
- Env vars injected from Key Vault: `ANTHROPIC_API_KEY`, `TAVILY_API_KEY`, `OPENAI_API_KEY`, `POSTGRES_URL`
- Env vars set directly: `QDRANT_URL`, `REDIS_URL`, `ENVIRONMENT=production`

### Container App: `rateiq-qdrant` (Vector DB)
- Image: `qdrant/qdrant:latest` (public Docker Hub image)
- CPU: 0.5 cores, Memory: 1Gi
- Scale: min 1, max 1 (always on — holds vector data)
- Ingress: **internal only** (not exposed to internet)
- Volume: Azure Files mount at `/qdrant/storage` (persistent — survives restarts)

### Container App: `rateiq-redis` (Cache)
- Image: `redis:7-alpine` (public Docker Hub image)
- CPU: 0.25 cores, Memory: 0.5Gi
- Scale: min 0, max 1
- Ingress: **internal only**
- No persistent volume (cache is ephemeral by design — acceptable for market rate cache)

### Azure Database for PostgreSQL Flexible Server
- Name: `psql-boq-rateiq`
- SKU: `Standard_B1ms` (Burstable, 1 vCore, 2GiB) — ~$12/month
- Version: PostgreSQL 15
- Database name: `rateiq`
- Admin user: `rateiq`
- Password: stored in Key Vault as `POSTGRES-PASSWORD`
- Connection string stored in Key Vault as `POSTGRES-URL`
- SSL: required
- Backup: 7 days (default, free)

### Azure Static Web Apps
- Name: `stapp-boq-rateiq`
- SKU: **Free**
- Source: `frontend/` directory
- Build output: `dist/`
- Framework: React (Vite)
- Custom routes: `staticwebapp.config.json` handles SPA routing (all 404s → `/index.html`)
- `VITE_API_BASE_URL` set to the Container App HTTPS URL

### Azure Key Vault
- Name: `kv-boq-rateiq` (must be globally unique)
- SKU: Standard
- Secrets stored:
  - `ANTHROPIC-API-KEY`
  - `TAVILY-API-KEY`
  - `OPENAI-API-KEY`
  - `POSTGRES-URL` (full connection string)
  - `POSTGRES-PASSWORD`
- Access: Container Apps use system-assigned managed identity to read secrets

---

## 2. Bicep File Structure

```
infra/
├── main.bicep            — master template, calls all modules, wires outputs
├── main.bicepparam       — parameter values (location, names, SKUs)
└── modules/
    ├── registry.bicep    — ACR resource
    ├── postgres.bicep    — PostgreSQL Flexible Server + database
    ├── keyVault.bicep    — Key Vault + secret stubs (values loaded via CLI)
    └── containerApps.bicep — Log Analytics + CAE + 3 Container Apps
```

### Key Bicep concepts used
- `param` with `@description()` decorator — documents every input
- `@secure()` on password params — Azure masks them in logs
- `module` — each file is a reusable module called from `main.bicep`
- `output` — passes values between modules (e.g. ACR login server → Container App)
- `existing` — references Key Vault secrets by name without re-declaring them
- `dependsOn` — explicit ordering where Bicep can't infer it

### Deploy command (one command creates everything)
```bash
az deployment group create \
  --resource-group rg-boq-rateiq \
  --template-file infra/main.bicep \
  --parameters infra/main.bicepparam \
  --parameters postgresPassword="<STRONG_PASSWORD>"
```

---

## 3. Dockerfile (Multi-Stage Build)

**File:** `Dockerfile.api` at project root.

### Stage 1 — builder
- Base: `python:3.11-slim`
- Install `uv`
- Copy `pyproject.toml` + `uv.lock`
- Run `uv sync --frozen --no-dev` → installs deps into `/app/.venv`

### Stage 2 — runtime
- Base: `python:3.11-slim` (fresh, no build tools)
- Copy installed packages from builder stage
- Copy `src/` application code
- Copy `data/processed/boq_chunks.csv` (needed at runtime for BM25 index)
- Set `ENV PATH="/app/.venv/bin:$PATH"`
- `EXPOSE 8000`
- `CMD ["uvicorn", "rateiq.api:app", "--host", "0.0.0.0", "--port", "8000"]`

**Why multi-stage?** The builder stage installs compilers and build tools (needed to compile some Python packages). The runtime stage starts fresh and only copies the final output — no compilers, no build cache, ~60% smaller image.

### `.dockerignore`
Excludes: `.venv/`, `tests/`, `notebooks/`, `*.pyc`, `__pycache__/`, `.env`, `frontend/`, `infra/`, `.git/`

---

## 4. GitHub Actions Pipelines

### File: `.github/workflows/ci.yml` — PR Checks

**Trigger:** `pull_request` on any branch targeting `main`

**Jobs:**
1. `test` — runs `pytest tests/unit/ -v` with Python 3.11
   - Uses `uv` for fast dependency install (~15s vs 90s with pip)
   - No Azure services needed — unit tests only
   - Fails PR if any test fails (branch protection rule)

### File: `.github/workflows/cd.yml` — Deploy Pipeline

**Trigger:** `push` to `main` branch only

**Jobs (in order):**

```
test ──► build-and-push ──► deploy-frontend ──► deploy-backend
```

1. `test` — same as CI, must pass before anything deploys

2. `build-and-push`
   - `needs: test`
   - Login to ACR using `ACR_LOGIN_SERVER`, `ACR_USERNAME`, `ACR_PASSWORD` secrets
   - `docker build -f Dockerfile.api -t rateiqacr.azurecr.io/rateiq-api:${{ github.sha }} .`
   - Also tag as `:latest`
   - `docker push` both tags
   - **Why `github.sha`?** Every commit gets a unique image tag — you can always roll back to an exact commit

3. `deploy-frontend`
   - `needs: test`
   - Runs in parallel with `build-and-push` (independent)
   - Uses `Azure/static-web-apps-deploy@v1` action
   - Builds `frontend/` with `npm ci && npm run build`
   - Deploys `dist/` to Azure Static Web Apps

4. `deploy-backend`
   - `needs: [build-and-push, deploy-frontend]`
   - Login to Azure using `AZURE_CREDENTIALS` secret
   - `az containerapp update --name rateiq-api --image rateiqacr.azurecr.io/rateiq-api:${{ github.sha }}`
   - Azure pulls new image and does a **rolling restart** (zero-downtime)

### GitHub Secrets Required

| Secret | How to get it |
|---|---|
| `AZURE_CREDENTIALS` | `az ad sp create-for-rbac --json-auth` output |
| `ACR_LOGIN_SERVER` | `az acr show --query loginServer` |
| `ACR_USERNAME` | `az acr credential show --query username` |
| `ACR_PASSWORD` | `az acr credential show --query passwords[0].value` |
| `AZURE_STATIC_WEB_APPS_API_TOKEN` | Azure portal → Static Web App → Manage deployment token |

---

## 5. Frontend Routing Config

**File:** `frontend/staticwebapp.config.json`

Azure Static Web Apps needs to know that all URL paths (like `/dashboard`) should serve `index.html` — otherwise a direct URL visit returns 404.

```json
{
  "navigationFallback": {
    "rewrite": "/index.html",
    "exclude": ["/assets/*"]
  }
}
```

---

## 6. Security Decisions

- `.env` added to `.gitignore` (enforced — pipeline fails if `.env` is detected in repo)
- All API keys in Azure Key Vault, injected as env vars at runtime
- Container Apps use **managed identity** to read Key Vault — no client secrets for the app itself
- ACR access uses admin credentials (acceptable for solo/small-team project; enterprise would use managed identity for ACR too)
- PostgreSQL enforces SSL (`sslmode=require` in connection string)
- Qdrant and Redis have **internal-only** ingress — not reachable from the internet
- Service Principal scoped to single resource group (principle of least privilege)

---

## 7. Cost Breakdown (Estimated Monthly)

| Resource | SKU | Est. Cost |
|---|---|---|
| Azure Container Registry | Basic | ~$5 |
| Container Apps (API + Redis, scale-to-zero) | Consumption | ~$2–5 (usage-based) |
| Container Apps (Qdrant, always-on min 1) | Consumption | ~$8–12 |
| Azure Database for PostgreSQL | Standard_B1ms | ~$12 |
| Azure Static Web Apps | Free | $0 |
| Azure Key Vault | Standard | ~$0.03 |
| Log Analytics | Pay-per-use | ~$1–2 |
| Azure Files (Qdrant storage, ~5GB) | LRS | ~$0.50 |
| **Total** | | **~$29–37/month** |

---

## 8. What Each File Does (Interview Reference)

| File | Purpose | Key concept |
|---|---|---|
| `Dockerfile.api` | Packages FastAPI backend into a container image | Multi-stage build |
| `.dockerignore` | Keeps image lean by excluding unnecessary files | Build context optimization |
| `infra/main.bicep` | Orchestrates all Azure resources | IaC, idempotent deployment |
| `infra/main.bicepparam` | Separates config values from resource definitions | Parameter files |
| `infra/modules/registry.bicep` | Declares ACR | Modular Bicep |
| `infra/modules/postgres.bicep` | Declares managed PostgreSQL | Managed services |
| `infra/modules/keyVault.bicep` | Declares Key Vault + access policies | Secrets management |
| `infra/modules/containerApps.bicep` | Declares all 3 Container Apps + environment | Container orchestration |
| `.github/workflows/ci.yml` | Runs tests on every PR | Continuous Integration |
| `.github/workflows/cd.yml` | Builds + deploys on merge to main | Continuous Deployment |
| `frontend/staticwebapp.config.json` | SPA routing for Azure Static Web Apps | Client-side routing |

---

## 9. Interview Talking Points

**"Walk me through your CI/CD pipeline"**
> "On every pull request, GitHub Actions runs our unit test suite using pytest. If tests pass, the PR can be merged. On merge to main, a second workflow triggers in parallel — one job builds a Docker image and pushes it to Azure Container Registry tagged with the git commit SHA, another deploys the React frontend to Azure Static Web Apps. Once both finish, a final job updates the Container App to use the new image via a rolling restart — zero downtime."

**"Why Container Apps over Kubernetes?"**
> "For this project, Container Apps gives us the same container isolation and auto-scaling as AKS without the operational overhead of managing a Kubernetes control plane. Scale-to-zero means we pay nothing when idle, which is important for a dev environment. The trade-off is less control over networking and scheduling, which we don't need here."

**"How do you manage secrets?"**
> "Nothing sensitive is in code or Git. API keys are stored in Azure Key Vault. The Container Apps use system-assigned managed identities to pull secrets at startup — no credentials are ever stored in the app itself. GitHub Actions gets a scoped Service Principal that only has contributor rights on our resource group."

**"What is Infrastructure as Code and why use it?"**
> "IaC means our Azure environment is described in version-controlled Bicep files. If I delete a resource by accident, I run one command and Azure rebuilds it identically. It also means the staging and production environments are provably identical — no manual portal clicks that are easy to forget or mis-configure."
