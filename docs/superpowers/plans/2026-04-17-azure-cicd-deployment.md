# Azure CI/CD Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy BOQ RateIQ to Azure Container Apps with a GitHub Actions CI/CD pipeline that runs tests on every PR and auto-deploys on merge to `main`.

**Architecture:** Bicep files declare all Azure resources (ACR, Container Apps Environment with 3 apps, PostgreSQL, Key Vault). GitHub Actions runs two workflows: `ci.yml` (tests on PRs) and `cd.yml` (build + deploy on main). The React frontend deploys free to Azure Static Web Apps. API secrets are stored as encrypted Container App secrets (injected from `@secure()` Bicep params).

**Tech Stack:** Azure Container Apps, Azure Container Registry, Azure Database for PostgreSQL Flexible Server, Azure Static Web Apps, Azure Key Vault, Azure Files, Bicep, GitHub Actions, Docker multi-stage build, uv.

---

## Files Created / Modified

```
Modified:
  .gitignore                                  — un-exclude boq_chunks.csv (needed in Docker)

Created:
  .env.example                                — template for local dev (never has real keys)
  .dockerignore                               — keeps Docker image lean
  Dockerfile.api                              — multi-stage build for FastAPI backend

  infra/main.bicep                            — master template, calls all modules
  infra/main.bicepparam                       — parameter values (names, region)
  infra/modules/registry.bicep               — Azure Container Registry
  infra/modules/keyVault.bicep               — Azure Key Vault
  infra/modules/postgres.bicep               — PostgreSQL Flexible Server + DB + firewall
  infra/modules/containerApps.bicep          — Log Analytics + Storage + CAE + 3 apps

  .github/workflows/ci.yml                    — PR gate: pytest tests/unit/
  .github/workflows/cd.yml                    — deploy: build image → push ACR → deploy apps

  frontend/staticwebapp.config.json           — SPA routing for Azure Static Web Apps
```

---

## Task 1: Security Foundation — .gitignore + .env.example + .dockerignore

**Why first:** Secrets must be protected before any other file touches the repo.
**Critical fix:** `data/processed/boq_chunks.csv` is currently gitignored — but the Docker image needs it at runtime for BM25. We must un-exclude it.

**Files:**
- Modify: `.gitignore`
- Create: `.env.example`
- Create: `.dockerignore`

- [ ] **Step 1: Un-exclude boq_chunks.csv from .gitignore**

Open `.gitignore` and change these lines:
```
# BEFORE (lines ~98-101):
data/processed/boq_chunks.csv
data/processed/boq_chunks.json
data/processed/boq_line_items.csv
data/processed/ingestion_checkpoint.json

# AFTER — only keep truly generated files gitignored:
# data/processed/boq_chunks.csv   ← REMOVED (needed in Docker image)
data/processed/boq_chunks.json
data/processed/boq_line_items.csv
data/processed/ingestion_checkpoint.json
```

> **Why:** `boq_chunks.csv` is not sensitive (processed construction rates, not client names). It's 1,429 rows that BM25 loads at startup. If it's not in Git, GitHub Actions can't copy it into the Docker image.

- [ ] **Step 2: Create .env.example**

Create `.env.example` at project root:
```bash
# Copy this file to .env and fill in your real values.
# NEVER commit .env — it's in .gitignore.

ANTHROPIC_API_KEY=sk-ant-...
TAVILY_API_KEY=tvly-...
OPENAI_API_KEY=sk-proj-...

QDRANT_URL=http://localhost:6333
POSTGRES_URL=postgresql://rateiq:rateiq123@localhost:5432/rateiq
REDIS_URL=redis://localhost:6379
```

> **Why .env.example exists:** New developers clone the repo, copy this file to `.env`, and fill in real keys. The `.env` file itself is never committed.

- [ ] **Step 3: Create .dockerignore**

Create `.dockerignore` at project root:
```
# Virtual environment — huge, not needed (we install inside Docker)
.venv/

# Tests — not needed in production image
tests/
notebooks/

# Frontend — built separately, not part of API image
frontend/

# Infrastructure files — not needed in image
infra/
docs/
.github/

# Secrets — NEVER in image
.env
.env.*

# Git history — not needed
.git/
.gitignore

# Python bytecode
__pycache__/
*.pyc
*.pyo

# Local data (raw client files)
data/raw/
data/embeddings/

# Editor files
.vscode/
.idea/
*.swp
```

> **What stays IN the image:** `src/rateiq/` (your code), `data/processed/boq_chunks.csv` (BM25 data), `pyproject.toml`, `uv.lock` (deps). Everything else is excluded to keep the image small.

- [ ] **Step 4: Commit**

```bash
git add .gitignore .env.example .dockerignore
git commit -m "chore: security foundation — fix gitignore, add .env.example, .dockerignore"
```

---

## Task 2: Dockerfile — Multi-Stage Build for FastAPI

**What is a multi-stage build?** Think of baking a cake: Stage 1 is the messy kitchen (mixers, flour everywhere, all the tools). Stage 2 is the finished cake on a clean plate. Docker copies only the cake into the final image — leaving all the kitchen mess behind. Result: a small, clean, secure image.

**Files:**
- Create: `Dockerfile.api`

- [ ] **Step 1: Create Dockerfile.api at project root**

```dockerfile
# ══════════════════════════════════════════════════════════
# Stage 1: builder — installs all Python dependencies
# This stage is messy (compilers, build tools) but temporary.
# ══════════════════════════════════════════════════════════
FROM python:3.11-slim AS builder

WORKDIR /app

# Install uv — the fast Python package manager this project uses
# '--mount=type=cache' caches the uv download between builds (speeds up rebuilds)
RUN pip install uv --no-cache-dir

# Copy dependency files first (before source code)
# WHY: Docker caches each step. If only source code changes (not deps),
# Docker reuses the cached dep install step — much faster rebuilds.
COPY pyproject.toml uv.lock ./

# Install all production dependencies into /app/.venv
# --frozen: use exact versions from uv.lock (reproducible builds)
# --no-dev:  skip test/dev tools (pytest etc.) — not needed in production
RUN uv sync --frozen --no-dev

# Pre-download the cross-encoder model used by the reranker
# WHY: Without this, the first API request downloads ~80MB. With this,
# the model is baked into the image and cold starts are fast.
RUN .venv/bin/python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"


# ══════════════════════════════════════════════════════════
# Stage 2: runtime — clean image with only what's needed
# Starts fresh from python:3.11-slim, no compilers or build tools.
# ══════════════════════════════════════════════════════════
FROM python:3.11-slim AS runtime

WORKDIR /app

# Copy the installed virtual environment from the builder stage
# This is the "cake" — all deps, none of the kitchen mess
COPY --from=builder /app/.venv /app/.venv
# Copy the pre-downloaded model from builder
COPY --from=builder /root/.cache /root/.cache

# Put the venv's bin directory first in PATH so 'python' and 'uvicorn'
# resolve to the venv versions, not the system Python
ENV PATH="/app/.venv/bin:$PATH"

# Copy application source code
COPY src/ ./src/

# Copy the BM25 data file — needed at startup to build the BM25 index
# (This is why we un-excluded it from .gitignore in Task 1)
COPY data/processed/boq_chunks.csv ./data/processed/boq_chunks.csv

# Tell Docker (and Azure) that this container listens on port 8000
EXPOSE 8000

# The command that runs when the container starts.
# --host 0.0.0.0: listen on all network interfaces (not just localhost)
# --workers 1: one worker process (0.5 CPU constraint)
CMD ["uvicorn", "rateiq.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

- [ ] **Step 2: Verify the image builds locally**

```bash
docker build -f Dockerfile.api -t rateiq-api:test .
```

Expected output: `Successfully built <hash>` — no errors.
This takes 3–5 minutes on first run (downloads Python + installs deps + downloads model).
Subsequent builds are much faster due to Docker layer caching.

- [ ] **Step 3: Verify the image size is reasonable**

```bash
docker images rateiq-api:test
```

Expected: image size between 1.5–3 GB (sentence-transformers model is large).
If you see 5+ GB, the `.dockerignore` isn't being applied correctly.

- [ ] **Step 4: Commit**

```bash
git add Dockerfile.api
git commit -m "feat: add multi-stage Dockerfile for FastAPI backend"
```

---

## Task 3: Bicep — registry.bicep (Azure Container Registry)

**What is Bicep?** A language that describes Azure resources. Azure reads your `.bicep` file and creates exactly what you described. If you run it again, Azure only changes what's different — it's *idempotent* (safe to run multiple times).

**What is Azure Container Registry?** Your private warehouse for Docker images. You push your built image here; Azure Container Apps pulls from here.

**Files:**
- Create: `infra/modules/registry.bicep`

- [ ] **Step 1: Create infra/modules/ directory and registry.bicep**

```bash
mkdir -p infra/modules
```

Create `infra/modules/registry.bicep`:
```bicep
// ── Bicep Concept: @description() ─────────────────────────────────────────
// Decorators add metadata to params/resources. @description() documents what
// a param is for. Azure portal and IDE tooling show this as a tooltip.

@description('Azure region where the registry will be created (e.g. eastus)')
param location string

@description('Name of the registry — must be globally unique, alphanumeric only, 5-50 chars')
param acrName string

// ── Bicep Concept: resource ────────────────────────────────────────────────
// Declares one Azure resource. Format:
//   resource <symbolic-name> '<resource-type>@<api-version>' = { ... }
// The symbolic name is used to reference this resource elsewhere in the file.
// The API version pins to a specific Azure API — prevents breaking changes.

resource acr 'Microsoft.ContainerRegistry/registries@2023-01-01-preview' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'   // Cheapest tier: ~$5/month, fine for small teams
  }
  properties: {
    adminUserEnabled: true
    // WHY adminUserEnabled: lets us use username/password auth from GitHub Actions.
    // Enterprise would use managed identity instead, but that's more complex.
  }
}

// ── Bicep Concept: output ─────────────────────────────────────────────────
// Outputs return values after deployment. main.bicep uses these to wire
// modules together — e.g. pass the ACR login server to containerApps.bicep.

output loginServer string = acr.properties.loginServer
// e.g. "rateiqacr.azurecr.io"

output acrName string = acr.name

// listCredentials() calls the Azure API to get ACR's admin username + password.
// These are used by the Container App to pull images, and by GitHub Actions to push.
output adminUsername string = acr.listCredentials().username
output adminPassword string = acr.listCredentials().passwords[0].value
```

- [ ] **Step 2: Validate Bicep syntax**

```bash
az bicep build --file infra/modules/registry.bicep
```

Expected: no output (silence = success). If you see errors, fix them.

> **Interview concept:** `az bicep build` compiles Bicep to ARM JSON (Azure's native format). Bicep is a higher-level language that compiles down — like TypeScript compiles to JavaScript.

- [ ] **Step 3: Commit**

```bash
git add infra/modules/registry.bicep
git commit -m "feat(infra): add ACR Bicep module"
```

---

## Task 4: Bicep — keyVault.bicep (Azure Key Vault)

**What is Key Vault?** A secure safe in Azure for secrets (API keys, passwords, certificates). Nothing sensitive ever touches your code or Git. In production, your app reads secrets from Key Vault at startup.

**Files:**
- Create: `infra/modules/keyVault.bicep`

- [ ] **Step 1: Create infra/modules/keyVault.bicep**

```bicep
@description('Azure region')
param location string

@description('Key Vault name — 3-24 chars, globally unique, alphanumeric + hyphens')
param keyVaultName string

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    sku: {
      family: 'A'
      name: 'standard'   // Standard tier covers secrets (keys/certs cost more)
    }
    // tenantId: which Azure AD tenant owns this vault.
    // subscription().tenantId reads the current subscription's tenant automatically.
    tenantId: subscription().tenantId

    // enableRbacAuthorization: use Azure RBAC roles for access control
    // (instead of the older "access policies" model — RBAC is the modern way)
    enableRbacAuthorization: true

    // Soft delete: secrets aren't permanently deleted for 7 days.
    // Protects against accidental deletion. Required by Azure since 2021.
    softDeleteRetentionInDays: 7
    enableSoftDelete: true

    // Do NOT enable public network access restriction for simplicity.
    // In production you'd add a private endpoint or VNet integration.
  }
}

output keyVaultUri string = kv.properties.vaultUri
// e.g. "https://kv-boq-rateiq.vault.azure.net/"

output keyVaultName string = kv.name
output keyVaultId string = kv.id
```

- [ ] **Step 2: Validate**

```bash
az bicep build --file infra/modules/keyVault.bicep
```

Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add infra/modules/keyVault.bicep
git commit -m "feat(infra): add Key Vault Bicep module"
```

---

## Task 5: Bicep — postgres.bicep (Managed PostgreSQL)

**What is Azure Database for PostgreSQL Flexible Server?** A fully managed Postgres service. Azure handles OS patching, backups, and high availability. You never SSH into the DB server — just connect to it like any Postgres.

**Files:**
- Create: `infra/modules/postgres.bicep`

- [ ] **Step 1: Create infra/modules/postgres.bicep**

```bicep
@description('Azure region')
param location string

@description('PostgreSQL server name — must be globally unique')
param serverName string

@description('Admin username for the database')
param adminUser string = 'rateiq'

// ── Bicep Concept: @secure() ───────────────────────────────────────────────
// Marks a param as sensitive. Azure will NOT log or display this value anywhere.
// In CI/CD, this comes from a GitHub Secret and never appears in pipeline logs.
@secure()
@description('Admin password — min 8 chars, must contain uppercase, lowercase, digit, special char')
param adminPassword string

resource pgServer 'Microsoft.DBforPostgreSQL/flexibleServers@2022-12-01' = {
  name: serverName
  location: location
  sku: {
    // Burstable B1ms = cheapest option: ~$12/month
    // "Burstable" means it can temporarily use more CPU when needed,
    // but averages low CPU — perfect for a low-traffic portfolio project.
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    administratorLogin: adminUser
    administratorLoginPassword: adminPassword
    version: '15'
    storage: {
      storageSizeGB: 32    // Minimum allowed = 32 GB
    }
    backup: {
      backupRetentionDays: 7     // Free automatic backups for 7 days
      geoRedundantBackup: 'Disabled'   // Disabled = cheaper (no cross-region replication)
    }
    highAvailability: {
      mode: 'Disabled'   // No HA = cheaper. Enable for production.
    }
    authConfig: {
      activeDirectoryAuth: 'Disabled'   // Use password auth only (simpler)
      passwordAuth: 'Enabled'
    }
  }
}

// Create the 'rateiq' database inside the server
resource pgDatabase 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2022-12-01' = {
  parent: pgServer    // 'parent' links this resource to pgServer above
  name: 'rateiq'
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

// Firewall rule: allow connections from other Azure services
// startIp = endIp = 0.0.0.0 is Azure's special "allow all Azure IPs" rule.
// This lets Container Apps (which have dynamic IPs) connect to Postgres.
resource pgFirewallAllowAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2022-12-01' = {
  parent: pgServer
  name: 'AllowAllAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

output serverFqdn string = pgServer.properties.fullyQualifiedDomainName
// e.g. "psql-boq-rateiq.postgres.database.azure.com"

output databaseName string = pgDatabase.name
// "rateiq"

output adminUser string = adminUser
```

- [ ] **Step 2: Validate**

```bash
az bicep build --file infra/modules/postgres.bicep
```

Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add infra/modules/postgres.bicep
git commit -m "feat(infra): add PostgreSQL Flexible Server Bicep module"
```

---

## Task 6: Bicep — containerApps.bicep (The Core Infrastructure)

**This is the largest module.** It creates:
1. Log Analytics workspace (monitoring)
2. Storage Account + File Share (Qdrant persistence)
3. Container Apps Environment (the shared "cluster")
4. Three Container Apps: qdrant, redis, rateiq-api

**Files:**
- Create: `infra/modules/containerApps.bicep`

- [ ] **Step 1: Create infra/modules/containerApps.bicep**

```bicep
@description('Azure region')
param location string

@description('Container Apps Environment name')
param environmentName string

@description('API Container App name')
param apiAppName string = 'rateiq-api'

@description('ACR login server URL (e.g. rateiqacr.azurecr.io)')
param acrLoginServer string

@description('ACR admin username')
@secure()
param acrUsername string

@description('ACR admin password')
@secure()
param acrPassword string

@description('Docker image tag to deploy (use git SHA for traceability)')
param imageTag string = 'latest'

@description('PostgreSQL server FQDN (from postgres module output)')
param postgresFqdn string

@description('PostgreSQL admin username')
param postgresUser string

@secure()
@description('PostgreSQL admin password')
param postgresPassword string

@description('PostgreSQL database name')
param postgresDatabaseName string = 'rateiq'

@secure()
@description('Anthropic API key')
param anthropicApiKey string

@secure()
@description('Tavily API key')
param tavilyApiKey string

@secure()
@description('OpenAI API key')
param openaiApiKey string


// ── 1. Log Analytics Workspace ─────────────────────────────────────────────
// Collects logs from all Container Apps. You can query logs in Azure portal.
// Free tier: first 5 GB/month included.
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: 'law-${environmentName}'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}


// ── 2. Storage Account for Qdrant persistence ─────────────────────────────
// Qdrant stores vector embeddings to disk. Without persistent storage,
// all vectors are lost when the container restarts.
// We mount an Azure Files share into the container at /qdrant/storage.
resource storage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  // Storage account names: 3-24 chars, lowercase alphanumeric only (no hyphens!)
  name: 'stqdrant${uniqueString(resourceGroup().id)}'
  location: location
  sku: { name: 'Standard_LRS' }    // LRS = Locally Redundant Storage (cheapest, 3 copies in one datacenter)
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-01-01' = {
  parent: storage
  name: 'default'
}

resource fileShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-01-01' = {
  parent: fileService
  name: 'qdrant-storage'
  properties: {
    shareQuota: 10    // 10 GB max. Qdrant data for 1,429 vectors is ~50 MB.
  }
}


// ── 3. Container Apps Environment ─────────────────────────────────────────
// The "cluster" all three Container Apps share. Provides:
// - Shared internal DNS (apps find each other by name: http://rateiq-qdrant)
// - Shared egress IP (one outbound IP for all apps)
// - Shared Log Analytics integration
resource cae 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        // listKeys() is a Bicep function that calls the Azure API to get credentials.
        // Here it gets the Log Analytics workspace key so Container Apps can send logs.
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// Register the Azure Files share with the Container Apps Environment.
// This creates a named storage reference ('qdrant-files') that Container Apps can mount.
resource caeStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: cae
  name: 'qdrant-files'
  properties: {
    azureFile: {
      accountName: storage.name
      accountKey: storage.listKeys().keys[0].value
      shareName: fileShare.name
      accessMode: 'ReadWrite'
    }
  }
}


// ── 4. Qdrant Container App (vector database) ──────────────────────────────
// Internal only (not reachable from internet), always-on (min 1 replica),
// with persistent Azure Files mount so vectors survive restarts.
resource qdrantApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'rateiq-qdrant'
  location: location
  properties: {
    environmentId: cae.id
    configuration: {
      ingress: {
        external: false          // Internal only — no public HTTPS endpoint
        targetPort: 6333
        transport: 'http'
      }
    }
    template: {
      containers: [
        {
          name: 'qdrant'
          image: 'qdrant/qdrant:latest'    // Public Docker Hub image
          resources: {
            cpu: json('0.5')    // json() needed because Bicep doesn't have a decimal literal
            memory: '1Gi'
          }
          volumeMounts: [
            {
              volumeName: 'qdrant-data'
              mountPath: '/qdrant/storage'    // Where Qdrant writes its data files
            }
          ]
        }
      ]
      volumes: [
        {
          name: 'qdrant-data'
          storageType: 'AzureFile'
          storageName: 'qdrant-files'    // References the caeStorage resource name above
        }
      ]
      scale: {
        minReplicas: 1    // Always at least 1 running — vector data must always be available
        maxReplicas: 1
      }
    }
  }
  dependsOn: [caeStorage]    // Wait for storage registration before creating app
}


// ── 5. Redis Container App (cache) ────────────────────────────────────────
// Internal only, scale-to-zero. Cache is ephemeral by design —
// market rates re-fetch after Redis restarts (7-day TTL anyway).
resource redisApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'rateiq-redis'
  location: location
  properties: {
    environmentId: cae.id
    configuration: {
      ingress: {
        external: false
        targetPort: 6379
        transport: 'tcp'
      }
    }
    template: {
      containers: [
        {
          name: 'redis'
          image: 'redis:7-alpine'
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
        }
      ]
      scale: {
        minReplicas: 0    // Scales to zero when idle (saves cost)
        maxReplicas: 1
      }
    }
  }
}


// ── 6. FastAPI Container App (the main API) ───────────────────────────────
// External HTTPS ingress. Scale-to-zero. Pulls image from ACR.
// API secrets are stored as encrypted Container App secrets
// (not in code, passed via @secure() Bicep params from CLI/GitHub Secrets).
resource apiApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: apiAppName
  location: location
  properties: {
    environmentId: cae.id
    configuration: {
      ingress: {
        external: true       // Public HTTPS endpoint created automatically
        targetPort: 8000
        transport: 'http'
      }
      // registries: tells Container Apps which ACR to authenticate with
      // so it can pull the private Docker image.
      registries: [
        {
          server: acrLoginServer
          username: acrUsername
          passwordSecretRef: 'acr-password'    // References the secret named below
        }
      ]
      // secrets: encrypted values stored in Azure (not in your code or Git).
      // The API container reads these as environment variables.
      secrets: [
        { name: 'acr-password',     value: acrPassword }
        { name: 'anthropic-key',    value: anthropicApiKey }
        { name: 'tavily-key',       value: tavilyApiKey }
        { name: 'openai-key',       value: openaiApiKey }
        { name: 'postgres-password', value: postgresPassword }
      ]
    }
    template: {
      containers: [
        {
          name: 'rateiq-api'
          // 'latest' for initial Bicep deploy; GitHub Actions overwrites with git SHA tag.
          image: '${acrLoginServer}/rateiq-api:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            // Internal DNS: containers in the same environment find each other by app name.
            // No ports needed — Container Apps handles internal routing.
            { name: 'QDRANT_URL',  value: 'http://rateiq-qdrant' }
            { name: 'REDIS_URL',   value: 'redis://rateiq-redis:6379' }
            {
              name: 'POSTGRES_URL'
              // sslmode=require: PostgreSQL Flexible Server requires SSL
              value: 'postgresql://${postgresUser}:$(postgres-password)@${postgresFqdn}/${postgresDatabaseName}?sslmode=require'
            }
            // secretRef: reads from the 'secrets' array above (encrypted, never logged)
            { name: 'ANTHROPIC_API_KEY', secretRef: 'anthropic-key' }
            { name: 'TAVILY_API_KEY',    secretRef: 'tavily-key' }
            { name: 'OPENAI_API_KEY',    secretRef: 'openai-key' }
            { name: 'ENVIRONMENT',       value: 'production' }
          ]
        }
      ]
      scale: {
        minReplicas: 0    // Scale to zero when nobody is using the app
        maxReplicas: 1
      }
    }
  }
  dependsOn: [qdrantApp, redisApp]
}

// Outputs used by main.bicep and by GitHub Actions deploy step
output apiUrl string = 'https://${apiApp.properties.configuration.ingress.fqdn}'
output apiAppName string = apiApp.name
output environmentName string = cae.name
```

- [ ] **Step 2: Fix the POSTGRES_URL secret reference**

The env var for POSTGRES_URL references the postgres password secret. Fix the value line:
```bicep
// In the env array, replace:
value: 'postgresql://${postgresUser}:$(postgres-password)@${postgresFqdn}/${postgresDatabaseName}?sslmode=require'
// With:
secretRef: 'postgres-password-url'
```

And add a new secret to the secrets array:
```bicep
{
  name: 'postgres-password-url'
  value: 'postgresql://${postgresUser}:${postgresPassword}@${postgresFqdn}/${postgresDatabaseName}?sslmode=require'
}
```

> **Why:** You can't mix `value:` (which is plain text, visible in Azure portal) with a `@secure()` param when it's embedded in a string. Store the full connection string as a secret instead.

- [ ] **Step 3: Validate**

```bash
az bicep build --file infra/modules/containerApps.bicep
```

Expected: no output (silence = success).

- [ ] **Step 4: Commit**

```bash
git add infra/modules/containerApps.bicep
git commit -m "feat(infra): add Container Apps Bicep module (Qdrant + Redis + API)"
```

---

## Task 7: Bicep — main.bicep + main.bicepparam (Orchestrator)

**What does main.bicep do?** It wires all modules together — calls each module, passes parameters, and threads outputs from one module as inputs to another. Think of it as the conductor calling each musician (module) in the right order.

**What is a .bicepparam file?** Separates *what* you're building (main.bicep) from *configuration values* (main.bicepparam). The `.bicepparam` file has non-secret config; secrets are passed via CLI flags.

**Files:**
- Create: `infra/main.bicep`
- Create: `infra/main.bicepparam`

- [ ] **Step 1: Create infra/main.bicep**

```bicep
// targetScope: this template deploys into an existing resource group.
// Alternative: 'subscription' (to also create the resource group).
targetScope = 'resourceGroup'

// ── Parameters ─────────────────────────────────────────────────────────────
@description('Azure region — defaults to the resource group region')
param location string = resourceGroup().location

@description('Azure Container Registry name (globally unique, alphanumeric only)')
param acrName string

@description('Key Vault name (globally unique, 3-24 chars)')
param keyVaultName string

@description('PostgreSQL server name (globally unique)')
param pgServerName string

@description('PostgreSQL admin username')
param pgAdminUser string = 'rateiq'

@description('Container Apps Environment name')
param environmentName string

@description('FastAPI Container App name')
param apiAppName string = 'rateiq-api'

@description('Docker image tag to deploy')
param imageTag string = 'latest'

// Secure params — passed via CLI, never in .bicepparam file
@secure()
param pgAdminPassword string

@secure()
param anthropicApiKey string

@secure()
param tavilyApiKey string

@secure()
param openaiApiKey string


// ── Modules ────────────────────────────────────────────────────────────────
// Each 'module' block calls a .bicep file and passes parameters to it.
// Bicep figures out the dependency order automatically from how outputs are used.

module registry 'modules/registry.bicep' = {
  name: 'registry-deployment'    // Deployment name shown in Azure portal history
  params: {
    location: location
    acrName: acrName
  }
}

module kv 'modules/keyVault.bicep' = {
  name: 'keyvault-deployment'
  params: {
    location: location
    keyVaultName: keyVaultName
  }
}

module postgres 'modules/postgres.bicep' = {
  name: 'postgres-deployment'
  params: {
    location: location
    serverName: pgServerName
    adminUser: pgAdminUser
    adminPassword: pgAdminPassword
  }
}

// containerApps depends on registry + postgres outputs, so Bicep deploys those first.
module apps 'modules/containerApps.bicep' = {
  name: 'containerapps-deployment'
  params: {
    location: location
    environmentName: environmentName
    apiAppName: apiAppName
    // Wire registry outputs → containerApps inputs
    acrLoginServer: registry.outputs.loginServer
    acrUsername: registry.outputs.adminUsername
    acrPassword: registry.outputs.adminPassword
    imageTag: imageTag
    // Wire postgres outputs → containerApps inputs
    postgresFqdn: postgres.outputs.serverFqdn
    postgresUser: pgAdminUser
    postgresPassword: pgAdminPassword
    postgresDatabaseName: postgres.outputs.databaseName
    // API secrets (passed through from CLI)
    anthropicApiKey: anthropicApiKey
    tavilyApiKey: tavilyApiKey
    openaiApiKey: openaiApiKey
  }
}


// ── Outputs ────────────────────────────────────────────────────────────────
// These print to terminal after deployment — useful for configuration.
output apiUrl string = apps.outputs.apiUrl
output acrLoginServer string = registry.outputs.loginServer
output keyVaultName string = kv.outputs.keyVaultName
output pgServerFqdn string = postgres.outputs.serverFqdn
```

- [ ] **Step 2: Create infra/main.bicepparam**

```bicep
// This file provides non-secret parameter values for main.bicep.
// Secret values (passwords, API keys) are passed via CLI --parameters flags.
// This file IS committed to Git — it contains no secrets.
using './main.bicep'

param acrName = 'rateiqacr'
// ↑ If this name is taken (ACR names are global), append 4 digits: 'rateiqacr2834'
// Check availability: az acr check-name --name rateiqacr

param keyVaultName = 'kv-boq-rateiq'
// ↑ If taken, use: 'kv-boq-rateiq-2834'
// Check: az keyvault check-name --name kv-boq-rateiq

param pgServerName = 'psql-boq-rateiq'
// ↑ Must be globally unique. Check: az postgres flexible-server list-skus --location eastus

param pgAdminUser = 'rateiq'

param environmentName = 'cae-boq-rateiq'

param apiAppName = 'rateiq-api'

param imageTag = 'latest'
// ↑ Overridden by GitHub Actions CD pipeline with the git commit SHA
```

- [ ] **Step 3: Validate the full template compiles**

```bash
az bicep build --file infra/main.bicep
```

Expected: no output. If you see "module not found" errors, check the relative paths in the `module` blocks.

- [ ] **Step 4: Commit**

```bash
git add infra/main.bicep infra/main.bicepparam
git commit -m "feat(infra): add main Bicep orchestrator and parameter file"
```

---

## Task 8: GitHub Actions — ci.yml (PR Gate)

**What is this?** Every time you open a Pull Request, GitHub automatically runs your unit tests. If tests fail, the PR is blocked — you can't merge broken code. This is **Continuous Integration**.

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Create .github/workflows/ directory**

```bash
mkdir -p .github/workflows
```

- [ ] **Step 2: Create .github/workflows/ci.yml**

```yaml
# ── What triggers this workflow ─────────────────────────────────────────────
# 'on:' defines events that cause this workflow to run.
on:
  pull_request:
    branches: [ main ]    # Only PRs targeting 'main' — not feature branches targeting each other
  push:
    branches: [ main ]    # Also run on direct pushes to main (though branch protection should prevent these)

# ── Workflow name (shown in GitHub Actions UI) ───────────────────────────────
name: CI — Unit Tests

# ── Jobs ────────────────────────────────────────────────────────────────────
# A workflow has one or more jobs. By default, jobs run in parallel.
# Each job gets a fresh virtual machine (runner) from GitHub.
jobs:

  test:
    name: Run Unit Tests
    # runs-on: which operating system the runner uses.
    # ubuntu-latest = Ubuntu Linux VM — fast and free for public repos.
    runs-on: ubuntu-latest

    # steps: a list of actions to run in sequence on this runner.
    steps:
      # ── Step 1: Checkout ─────────────────────────────────────────────────
      # 'uses:' runs a pre-built action from GitHub Marketplace.
      # actions/checkout downloads your repo code onto the runner machine.
      - name: Checkout code
        uses: actions/checkout@v4

      # ── Step 2: Install Python ────────────────────────────────────────────
      - name: Set up Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      # ── Step 3: Install uv ────────────────────────────────────────────────
      # astral-sh/setup-uv installs the 'uv' package manager.
      # 'cache: true' means uv's internal cache persists between runs —
      # same deps install in ~5s instead of ~60s on repeat runs.
      - name: Install uv
        uses: astral-sh/setup-uv@v4
        with:
          version: 'latest'
          cache: true

      # ── Step 4: Install dependencies ─────────────────────────────────────
      # 'run:' executes shell commands on the runner.
      # --frozen: use exact versions from uv.lock (reproducible)
      # --dev:    includes pytest and other dev tools
      - name: Install dependencies
        run: uv sync --frozen --dev

      # ── Step 5: Run tests ─────────────────────────────────────────────────
      # tests/unit/ only — no live services (Qdrant/Postgres) needed.
      # -v: verbose output (shows each test name)
      # --tb=short: short traceback on failure (readable in CI logs)
      - name: Run unit tests
        run: uv run pytest tests/unit/ -v --tb=short

      # ── What happens if tests fail? ───────────────────────────────────────
      # GitHub marks the PR check as "failed" in red.
      # The merge button is blocked (if you set up branch protection rules).
      # The developer sees exactly which test failed and why.
```

- [ ] **Step 3: Validate YAML syntax**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo "YAML valid"
```

Expected: `YAML valid`

- [ ] **Step 4: Commit and push to test the CI workflow**

```bash
git add .github/workflows/ci.yml
git commit -m "feat(ci): add PR unit test gate"
git push origin master
```

Then open GitHub → Actions tab. You should see the CI workflow run and pass.

---

## Task 9: GitHub Actions — cd.yml (Full Deploy Pipeline)

**What is this?** When code merges to `main`, this workflow builds the Docker image, pushes it to ACR, deploys the frontend to Static Web Apps, and updates the Container App. This is **Continuous Deployment**.

**Files:**
- Create: `.github/workflows/cd.yml`

- [ ] **Step 1: Create .github/workflows/cd.yml**

```yaml
name: CD — Build & Deploy to Azure

on:
  push:
    branches: [ main ]    # Triggers only when code lands on main (after PR merge)

# ── Permissions ──────────────────────────────────────────────────────────────
# 'permissions:' controls what the workflow token can do.
# id-token: write is needed for OIDC authentication with Azure (modern auth).
# contents: read allows checking out code.
permissions:
  id-token: write
  contents: read

# ── Environment variables shared across all jobs ─────────────────────────────
# 'env:' at workflow level = available in every job and step.
# These are non-secret config values — safe to hardcode here.
env:
  RESOURCE_GROUP: rg-boq-rateiq
  ACR_NAME: rateiqacr
  API_APP_NAME: rateiq-api
  AZURE_LOCATION: eastus

jobs:

  # ── Job 1: Run tests (must pass before anything deploys) ─────────────────
  test:
    name: Unit Tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - uses: astral-sh/setup-uv@v4
        with:
          version: 'latest'
          cache: true
      - run: uv sync --frozen --dev
      - run: uv run pytest tests/unit/ -v --tb=short


  # ── Job 2: Build Docker image and push to ACR ─────────────────────────────
  build-and-push:
    name: Build & Push Docker Image
    runs-on: ubuntu-latest
    needs: test    # 'needs:' means this job only starts AFTER 'test' passes
    outputs:
      # Pass the image tag to the deploy job
      # github.sha = the full git commit hash (e.g. a3f9b2c1...)
      # Using the SHA means every deploy is traceable to an exact commit.
      image-tag: ${{ github.sha }}

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      # Login to Azure using the Service Principal credentials.
      # 'secrets.AZURE_CREDENTIALS' reads the JSON blob you stored in GitHub Secrets.
      # This is like 'az login' but non-interactive — designed for automation.
      - name: Login to Azure
        uses: azure/login@v2
        with:
          creds: ${{ secrets.AZURE_CREDENTIALS }}

      # Login to ACR so Docker can push images there.
      # 'az acr login' is simpler than 'docker login' — handles token refresh automatically.
      - name: Login to Azure Container Registry
        run: az acr login --name ${{ env.ACR_NAME }}

      # Build the Docker image and push both tags in one step.
      # Tag with commit SHA: for traceability (which commit is deployed?)
      # Tag with 'latest': so Bicep's default imageTag='latest' still works.
      - name: Build and push Docker image
        run: |
          IMAGE="${{ secrets.ACR_LOGIN_SERVER }}/rateiq-api"
          docker build -f Dockerfile.api -t "${IMAGE}:${{ github.sha }}" -t "${IMAGE}:latest" .
          docker push "${IMAGE}:${{ github.sha }}"
          docker push "${IMAGE}:latest"


  # ── Job 3: Deploy frontend to Azure Static Web Apps ───────────────────────
  deploy-frontend:
    name: Deploy React Frontend
    runs-on: ubuntu-latest
    needs: test    # Runs in PARALLEL with build-and-push (both need 'test' only)
    steps:
      - uses: actions/checkout@v4

      # Get the API Container App URL so Vite can embed it at build time.
      # VITE_API_BASE_URL is read by frontend/src/api/boqApi.js at build time.
      - name: Login to Azure
        uses: azure/login@v2
        with:
          creds: ${{ secrets.AZURE_CREDENTIALS }}

      - name: Get API URL
        id: api-url
        run: |
          FQDN=$(az containerapp show \
            --name ${{ env.API_APP_NAME }} \
            --resource-group ${{ env.RESOURCE_GROUP }} \
            --query "properties.configuration.ingress.fqdn" \
            --output tsv 2>/dev/null || echo "")
          if [ -z "$FQDN" ]; then
            echo "api_url=" >> $GITHUB_OUTPUT
          else
            echo "api_url=https://$FQDN" >> $GITHUB_OUTPUT
          fi

      # This action handles: npm ci, npm run build, and upload to Static Web Apps.
      # 'app_location': where package.json lives
      # 'output_location': where Vite puts the built files (relative to app_location)
      - name: Deploy to Azure Static Web Apps
        uses: Azure/static-web-apps-deploy@v1
        with:
          azure_static_web_apps_api_token: ${{ secrets.AZURE_STATIC_WEB_APPS_API_TOKEN }}
          repo_token: ${{ secrets.GITHUB_TOKEN }}
          action: upload
          app_location: frontend
          output_location: dist
          app_build_command: npm run build
        env:
          VITE_API_BASE_URL: ${{ steps.api-url.outputs.api_url }}


  # ── Job 4: Update the Container App with the new image ────────────────────
  deploy-backend:
    name: Deploy FastAPI to Container Apps
    runs-on: ubuntu-latest
    needs: [build-and-push, deploy-frontend]    # Waits for BOTH jobs above
    steps:
      - name: Login to Azure
        uses: azure/login@v2
        with:
          creds: ${{ secrets.AZURE_CREDENTIALS }}

      # 'az containerapp update' changes the running image to the new SHA-tagged version.
      # Azure does a rolling restart: new container starts, old one shuts down.
      # Zero downtime — the API is never completely offline during deploy.
      - name: Deploy new image to Container App
        run: |
          az containerapp update \
            --name ${{ env.API_APP_NAME }} \
            --resource-group ${{ env.RESOURCE_GROUP }} \
            --image ${{ secrets.ACR_LOGIN_SERVER }}/rateiq-api:${{ needs.build-and-push.outputs.image-tag }}

      - name: Show deployed URL
        run: |
          az containerapp show \
            --name ${{ env.API_APP_NAME }} \
            --resource-group ${{ env.RESOURCE_GROUP }} \
            --query "properties.configuration.ingress.fqdn" \
            --output tsv
```

- [ ] **Step 2: Validate YAML syntax**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/cd.yml'))" && echo "YAML valid"
```

Expected: `YAML valid`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/cd.yml
git commit -m "feat(ci): add CD pipeline — build Docker, deploy frontend + backend"
```

---

## Task 10: Frontend Routing Config

**Why needed?** Azure Static Web Apps serves files from `dist/`. If a user visits `https://yourapp.com/dashboard` directly, Azure looks for a file at `dist/dashboard` — which doesn't exist (it's a React SPA). Without this config, they get a 404.

**Files:**
- Create: `frontend/staticwebapp.config.json`
- Modify: `frontend/vite.config.js` (if it exists) — or confirm no changes needed

- [ ] **Step 1: Create frontend/staticwebapp.config.json**

```json
{
  "navigationFallback": {
    "rewrite": "/index.html",
    "exclude": ["/assets/*", "/favicon.ico"]
  },
  "globalHeaders": {
    "Cache-Control": "no-cache"
  },
  "mimeTypes": {
    ".json": "text/json"
  }
}
```

> **What this does:**
> - `navigationFallback`: any URL that doesn't match a real file → serve `/index.html`. React Router then handles routing client-side.
> - `exclude`: don't fallback for `/assets/` — those ARE real files (JS, CSS bundles).
> - `globalHeaders`: prevent browsers from caching stale HTML (so new deploys take effect immediately).

- [ ] **Step 2: Commit**

```bash
git add frontend/staticwebapp.config.json
git commit -m "feat: add Azure Static Web Apps routing config for React SPA"
```

---

## Task 11: Manual Azure Provisioning (Run Once)

**This task is manual CLI steps** — not automated code. Run these once to create your Azure environment. After this, all future changes go through GitHub Actions automatically.

**Files:** none (Azure portal + CLI only)

- [ ] **Step 1: Install Azure CLI and Bicep (if not installed)**

```bash
# Check if installed
az --version
az bicep version

# Install Azure CLI on Ubuntu/WSL
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash

# Install Bicep
az bicep install
```

- [ ] **Step 2: Login and set subscription**

```bash
az login    # Opens browser — sign in with your Azure account

# List subscriptions
az account list --output table

# Set the subscription you want to deploy into
az account set --subscription "<YOUR_SUBSCRIPTION_ID>"

# Verify
az account show --query "{name:name, id:id}" --output table
```

- [ ] **Step 3: Create the Resource Group**

```bash
# A resource group is a logical container for all related Azure resources.
# Deleting the resource group deletes everything inside it — easy cleanup.
az group create \
  --name rg-boq-rateiq \
  --location eastus

# Expected output:
# {
#   "id": "/subscriptions/.../resourceGroups/rg-boq-rateiq",
#   "location": "eastus",
#   "provisioningState": "Succeeded",
#   ...
# }
```

- [ ] **Step 4: Create the Service Principal for GitHub Actions**

```bash
# Replace <SUBSCRIPTION_ID> with your subscription ID from Step 2
az ad sp create-for-rbac \
  --name "sp-boq-rateiq-github" \
  --role contributor \
  --scopes /subscriptions/<SUBSCRIPTION_ID>/resourceGroups/rg-boq-rateiq \
  --json-auth

# COPY THE ENTIRE JSON OUTPUT — it looks like:
# {
#   "clientId": "xxxxxxxx-...",
#   "clientSecret": "xxxxxxxxx...",
#   "subscriptionId": "xxxxxxxx-...",
#   "tenantId": "xxxxxxxx-..."
# }
# This goes into GitHub Secret: AZURE_CREDENTIALS
```

> **What is a Service Principal?** A non-human identity for automation. `--role contributor` means it can create and modify resources but cannot change permissions or billing. `--scopes` limits it to ONLY your resource group — if the key leaks, the blast radius is small.

- [ ] **Step 5: Deploy the Bicep infrastructure (first time)**

```bash
# This creates all Azure resources: ACR, Key Vault, PostgreSQL, Container Apps.
# Replace the placeholders with your real API keys.
# The --parameters flags pass @secure() params securely (not logged anywhere).

az deployment group create \
  --resource-group rg-boq-rateiq \
  --template-file infra/main.bicep \
  --parameters infra/main.bicepparam \
  --parameters pgAdminPassword="R@teIQ_Str0ng_2026!" \
  --parameters anthropicApiKey="sk-ant-..." \
  --parameters tavilyApiKey="tvly-..." \
  --parameters openaiApiKey="sk-proj-..." \
  --verbose

# This takes 8-15 minutes (PostgreSQL provisioning is slow).
# Expected final output:
# "provisioningState": "Succeeded"
# "outputs": { "apiUrl": "...", "acrLoginServer": "...", ... }
```

> **Password rules for PostgreSQL:** Must be 8+ chars, contain uppercase, lowercase, digit, and special character. Example: `R@teIQ_Str0ng_2026!`

- [ ] **Step 6: Get ACR credentials**

```bash
az acr show --name rateiqacr --resource-group rg-boq-rateiq \
  --query loginServer --output tsv
# Copy this → GitHub Secret: ACR_LOGIN_SERVER

az acr credential show --name rateiqacr --resource-group rg-boq-rateiq
# username → GitHub Secret: ACR_USERNAME
# passwords[0].value → GitHub Secret: ACR_PASSWORD
```

- [ ] **Step 7: Create Azure Static Web App and get deploy token**

```bash
az staticwebapp create \
  --name stapp-boq-rateiq \
  --resource-group rg-boq-rateiq \
  --location eastus2 \
  --sku Free \
  --source https://github.com/AliTheAnalyst01/boq_rateiq \
  --branch main \
  --app-location frontend \
  --output-location dist \
  --login-with-github
# This opens browser for GitHub auth. Follow the prompts.

# After creation, get the deploy token:
az staticwebapp secrets list \
  --name stapp-boq-rateiq \
  --resource-group rg-boq-rateiq \
  --query "properties.apiKey" --output tsv
# Copy this → GitHub Secret: AZURE_STATIC_WEB_APPS_API_TOKEN
```

- [ ] **Step 8: Load API keys into Key Vault**

```bash
# Replace with your real keys
az keyvault secret set --vault-name kv-boq-rateiq --name "ANTHROPIC-API-KEY" \
  --value "sk-ant-..."

az keyvault secret set --vault-name kv-boq-rateiq --name "TAVILY-API-KEY" \
  --value "tvly-..."

az keyvault secret set --vault-name kv-boq-rateiq --name "OPENAI-API-KEY" \
  --value "sk-proj-..."
```

- [ ] **Step 9: Add all 5 secrets to GitHub repository**

Go to: **GitHub → Your Repo → Settings → Secrets and variables → Actions → New repository secret**

Add these one by one:

| Secret Name | Value |
|---|---|
| `AZURE_CREDENTIALS` | The full JSON from Step 4 |
| `ACR_LOGIN_SERVER` | e.g. `rateiqacr.azurecr.io` |
| `ACR_USERNAME` | From Step 6 |
| `ACR_PASSWORD` | From Step 6 |
| `AZURE_STATIC_WEB_APPS_API_TOKEN` | From Step 7 |

- [ ] **Step 10: Push to trigger the first CD pipeline run**

```bash
git push origin master
```

Go to **GitHub → Actions tab**. Watch the `CD — Build & Deploy to Azure` workflow run. All 4 jobs should go green in ~8-12 minutes (Docker build is the slowest part).

- [ ] **Step 11: Verify the deployment**

```bash
# Get your API URL
az containerapp show \
  --name rateiq-api \
  --resource-group rg-boq-rateiq \
  --query "properties.configuration.ingress.fqdn" \
  --output tsv

# Test the health endpoint
curl https://<YOUR_FQDN>/health
# Expected: {"status":"ok","agent_ready":true,"version":"1.0.0"}

# Get your frontend URL
az staticwebapp show \
  --name stapp-boq-rateiq \
  --resource-group rg-boq-rateiq \
  --query "defaultHostname" --output tsv
# Visit this URL in your browser — the full app should load
```

---

## Self-Review

**Spec coverage check:**
- ✅ ACR (Basic SKU) — Task 3
- ✅ Container Apps Environment with Log Analytics — Task 6
- ✅ Qdrant (internal, always-on, Azure Files mount) — Task 6
- ✅ Redis (internal, scale-to-zero) — Task 6
- ✅ FastAPI (external HTTPS, scale-to-zero, secrets encrypted) — Task 6
- ✅ PostgreSQL Flexible Server B1ms — Task 5
- ✅ Static Web Apps (Free) — Task 10 + Task 11 Step 7
- ✅ Key Vault (RBAC, soft delete) — Task 4 + Task 11 Step 8
- ✅ Bicep orchestrator (main.bicep + params) — Task 7
- ✅ CI workflow (PR gate, pytest) — Task 8
- ✅ CD workflow (test → build → deploy frontend → deploy backend) — Task 9
- ✅ SPA routing config — Task 10
- ✅ Security (.gitignore, .env.example, .dockerignore) — Task 1
- ✅ Service Principal creation — Task 11
- ✅ boq_chunks.csv gitignore fix — Task 1

**Placeholder scan:** No TBDs. All commands have exact flags. All file contents are complete.

**Type consistency:** `apiAppName` used consistently as `rateiq-api` across all Bicep files and GitHub Actions env vars. ACR name `rateiqacr` consistent. Resource group `rg-boq-rateiq` consistent.
