# Phase 7: Production deployment and operations

## Start prompt (paste into a new chat)
> Read CLAUDE.md and docs/phases/PROGRESS.md, then execute docs/phases/phase-7-deploy-ops.md.
> Use the Azure CLI (already installed). Never print secrets. Update README with the live URL, PROGRESS.md, commit.

## Goal
A public, monitored, secure deployment that stays free on Azure for Students.

## Owner prerequisites
- An **Azure for Students** subscription (college email, no card); run `az login` when asked.

## Tasks
1. **Images**: build with GitHub Actions and push to **GitHub Container Registry** (free). Separate
   `api`, `worker` and `indexer` images (or one image with different commands). CPU torch, CLIP + SSCD baked in.
2. **Azure resources** (Bicep or az CLI script in `deploy/azure/`, idempotent):
   - Resource group; **Container Apps** environment: `api` (scale 0–2), `worker` (1), `indexer` (1).
   - **Azure Database for PostgreSQL Flexible Server** (Burstable B1ms, free-tier eligible for students) + the pgvector extension.
   - Redis: Azure Cache (Basic C0) or a Redis container app (cheaper). Document the choice.
   - **Key Vault** for VERIFIER key, REVIEWER key, PINATA_JWT and ALCHEMY_API_KEY; managed identity, no secrets in env files.
   - Blob storage for evidence thumbnails if needed.
3. **Cost guard**: budget alert at $10/month; scale-to-zero for the API; document the expected monthly cost against the $100 credit.
4. **Security**: HTTPS only, CORS locked, CSP kept, dependency scanning (Dependabot), container
   scanning (Trivy in CI), secret rotation runbook.
5. **Observability**: structured JSON logs, Application Insights (or OpenTelemetry), `/healthz` +
   `/readyz`, alerts on error rate, queue depth and verifier wallet balance < 0.01 ETH.
6. **Load test**: k6 or Locust; report the throughput and p95 latency of the async analyse flow.
7. **Docs and showcase**: README with the live URL, architecture diagram, contract addresses
   (Sepolia + Base Sepolia), a 2-minute demo video/GIF, and resume bullets in `docs/resume.md`.

## Acceptance criteria
- The public URL serves the app over HTTPS; analyse → attest → artist mint works on a testnet.
- Monitoring alerts fire in a test; the budget alert is configured.
- The README has a live link, badges, addresses and a demo GIF.
