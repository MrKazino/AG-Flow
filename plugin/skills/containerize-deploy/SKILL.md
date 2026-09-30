---
name: containerize-deploy
description: Containerise an application and prepare safe deployments — production-grade multi-stage Dockerfiles, .dockerignore, compose files, health checks, config via environment, and deploy manifests (Cloud Run, Kubernetes, Fly, etc.) with dry-run validation. Use when asked to dockerise, deploy, set up environments, or harden an existing Dockerfile.
---

# Containerise & deploy

## Dockerfile standards
- **Multi-stage**: build stage with the toolchain; slim runtime stage (`-slim`, `distroless` or `alpine` when compatible). Pin base images to a version (and digest for production).
- **Layer caching**: copy lockfiles and install dependencies before copying the source.
- **Small context**: `.dockerignore` excluding `.git`, `node_modules`, virtualenvs, build output, `.env*`, tests if unneeded.
- **Security**: run as a non-root `USER`; no secrets in `ARG`/`ENV`/layers (use runtime env or secret mounts `RUN --mount=type=secret`); minimal packages; `HEALTHCHECK` or platform health probes.
- **Runtime**: one process per container, listen on `$PORT`, log to stdout/stderr, handle SIGTERM for graceful shutdown.

## Procedure
1. Detect the stack and the real start command (package scripts, Procfile, `main`). Identify required env vars and ports.
2. Write/refresh `Dockerfile` and `.dockerignore` following the standards; add `compose.yaml` for local dependencies (database, cache) if useful.
3. **Build and run locally**: `docker build -t app:dev .` then `docker run --rm -p 8080:8080 --env-file .env.example app:dev`; hit the health endpoint; check logs. Report image size (`docker images app:dev`).
4. Scan if available: `docker scout cves app:dev` or `trivy image app:dev`.
5. **Deploy config**: write the target's manifest (Cloud Run service YAML / `gcloud run deploy` command, Kubernetes Deployment+Service with resources, probes and securityContext, etc.). Validate with dry runs (`kubectl apply --dry-run=server -f`, `gcloud run services replace --dry-run` where supported, `terraform plan`).
6. **Stop before production.** Actually deploying, pushing images to a registry, or changing live infrastructure requires explicit user approval. Provide the exact commands instead.

## Exit criteria
Files created, local build/run evidence (commands, health check response, image size), scan summary, dry-run output, and the approval-gated deploy commands.
