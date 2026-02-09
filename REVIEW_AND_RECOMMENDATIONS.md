# Vegetation Prime Module – Review and Recommendations

**Date:** 2026-02-08  
**Scope:** Full review vs `nekazari-public/module-template`, security, workflow, i18n, and production readiness.

---

## 1. Alignment with `nekazari-public/module-template`

### 1.1 What Matches

| Area | Status |
|------|--------|
| K8s backend/frontend deployments | OK: `imagePullSecrets: ghcr-secret`, `imagePullPolicy: Always`, health probes |
| Frontend Dockerfile | Multi-stage (Node build + nginx), CORS and `/modules/vegetation-prime/` in nginx |
| Manifest slots | `layer-toggle`, `context-panel`, `bottom-panel`, `map-layer` defined; IDs and priorities consistent |
| Module Federation | `vegetation_prime_module`, exposes `./App` and `./viewerSlots` |
| Registration SQL | Present; `ON CONFLICT` update; remote_entry_url and scope match |

### 1.2 Gaps vs Template (Addressed or Recommended)

| Gap | Template | Vegetation | Action taken / recommendation |
|-----|----------|------------|-------------------------------|
| **Backend config** | Central `config.py` with pydantic-settings, `jwt_issuer_url`/`jwks_url` from KEYCLOAK_* | Scattered `os.getenv`; auth uses own JWT_* | **Done:** JWT defaults fixed to `auth.artotxiki.com`. Optional: introduce a single `config.py` (pydantic-settings) and use it in auth. |
| **Auth middleware** | `get_tenant_id`: FIWARE-Service header **or** token; `TokenPayload`; `require_roles()` | Requires `X-Tenant-ID`; no FIWARE-Service fallback | **Recommendation:** Support `Fiware-Service` header as alternative to `X-Tenant-ID` for gateway compatibility. |
| **Health at root** | `/health` at app root | Present | OK |
| **API prefix** | `settings.api_prefix` (e.g. `/api/MODULE_NAME`) | Hardcoded `/api/vegetation` | OK for this module (single backend). |
| **Frontend export** | `export { viewerSlots } from './slots/index'` in App | Only exposed via Vite `./viewerSlots` | **Done:** Re-exported `viewerSlots` from `App.tsx` for consistency with template. |
| **env.example** | Full (KEYCLOAK, CORS, DB, Redis) | Minimal | **Done:** Expanded with backend/frontend, JWT, CORS, DB, Redis, storage placeholders. |

### 1.3 Bug Fixes Applied

- **`JobCreateRequest`**: Backend used `request.ha_to_process` but the model had no such field → **Added** `ha_to_process: Optional[float]` to the Pydantic model.
- **Auth defaults**: `JWT_ISSUER` default was `auth.nekazari.com` → **Updated** to `https://auth.artotxiki.com/auth/realms/nekazari`; `JWKS_URL` to `/protocol/openid-connect/certs`.
- **Logging**: Issuer was logged at `logger.info` → **Changed** to `logger.debug` to avoid sensitive patterns in production.
- **Frontend `getRecentJobs()`**: Backend returns `{ jobs, total }` but client was typing as `VegetationJob[]` and returning full response → **Fixed** to return `data.jobs` with fallback to `[]`.
- **`.gitignore`**: `.dockerignore` was ignored → **Removed** so `.dockerignore` is tracked and Docker build context is correct.

---

## 2. Security

### 2.1 Good Practices

- No hardcoded secrets in repo; K8s uses `secretKeyRef` / `configMapKeyRef`.
- JWT validation uses JWKS and **exact** issuer match (no suffix/prefix).
- CORS restricted to known origins via `CORS_ALLOWED_ORIGINS`.
- Tenant isolation: queries filter by `current_user['tenant_id']`.
- Service-only endpoint `sync-limits` protected by `require_service_auth`.

### 2.2 Recommendations

1. **Copernicus secret**: Config update stores `copernicus_client_secret` with a TODO to encrypt. Prefer encryption at rest (e.g. platform-managed secret or KMS) before persisting.
2. **Credentials status**: Avoid logging full `client_id`; current `client_id_preview` (truncated) is fine.
3. **Webhook URLs** (alerts, N8N): Validate URL scheme (e.g. allow only `https` in production) and consider rate limiting to avoid SSRF/abuse.
4. **Debug**: Ensure `DEBUG` and `SQL_ECHO` are false in production (already set in K8s).

---

## 3. Internationalisation (i18n)

**Current state:** UI strings are hardcoded in Spanish (e.g. "Cargando...", "Gestión de Cultivos", "Volver al listado", tab labels). Platform is international and expects modules to be translatable.

**Recommendations:**

1. Add a minimal i18n layer (e.g. `react-i18next` + JSON per language, or use host-provided i18n if the SDK exposes it).
2. Replace all user-facing strings with keys (e.g. `t('vegetation.loading')`, `t('vegetation.tabs.analytics')`).
3. Ship at least `en` and `es` namespaces; align language codes with platform (es, en, ca, eu, fr, pt) if needed.
4. Keep API messages and backend-only logs in English; translate only what the user sees in the UI.

This can be done incrementally (e.g. one page or one tab at a time) so the module stays “translation-ready” without over-engineering.

---

## 4. Workflow and Operations

### 4.1 GitOps / Build

- Each module is its own repo; Git commands must be run **inside** the module folder.
- Frontend: image `ghcr.io/k8-benetis/vegetation-health-nkz/vegetation-prime-frontend:latest`; backend analogous. Use `imagePullPolicy: Always` and rollout restart after push.
- Frontend Dockerfile replaces local SDK path with npm `@nekazari/sdk` for CI; ensure `package.json` has a publishable version (e.g. `^1.0.3`) for builds outside the monorepo.

### 4.2 API client vs backend contract

- **`testAlert(entityId)`**: Backend expects `webhook_url` as query param; frontend currently does not pass it. Either add `webhook_url` to the client method or read it from alert config.
- **`getWeather(entityId)`**: Backend expects `latitude`, `longitude`, `days` as query params; frontend only passes `entityId`. Callers should pass centroid and days (or derive from parcel geometry).
- **`/capabilities`**: Not implemented in backend; frontend degrades gracefully. See below.

### 4.3 Optional: Backend `/capabilities`

Frontend `getCapabilities()` calls `GET /api/vegetation/capabilities`. That endpoint does not exist; the client falls back to default capabilities. Options:

- **A)** Add a lightweight `/capabilities` endpoint returning e.g. `{ n8n_available, intelligence_available, isobus_available, features: {...} }` from env or feature flags.
- **B)** Leave as-is and rely on graceful degradation (current behaviour).

Recommendation: **A** if you want to drive UI/features from backend config; otherwise **B** is fine.

### 4.4 Backend Structure

- Template keeps a thin `main.py` and routes in `app.api`. Vegetation has a large `main.py` with many inline routers and Pydantic models. For maintainability, consider:
  - Moving job/config/timeseries/export/weather/alerts/carbon/zoning into dedicated router modules under `app.api`.
  - Keeping only app creation, global middleware, and `include_router` in `main.py`.

---

## 5. Aesthetics and UX

- Tabs and cards are clear; emerald/green theme fits “vegetation”.
- Consider: loading skeletons instead of only spinners; consistent empty states (e.g. “No parcelas” already present); and ARIA labels for icon-only actions for accessibility.
- If the host provides a design token or theme (e.g. via SDK/UI-kit), align colours and spacing for a consistent look across modules.

---

## 6. Scope and Future Modules

The module is feature-rich (jobs, indices, timeseries, config, prescription, alerts, weather, zoning, carbon, exports). To keep it maintainable and avoid over-dimensioning:

- **Keep:** Core vegetation (scenes, indices, jobs, config, timeline, map layer, context panel). This is the “vegetation health” core.
- **Consider splitting later:** e.g. “Prescription / VRA” (exports, zoning, ISOBUS) or “Alerts / Notifications” into separate addons that depend on vegetation-prime, if they grow or have different release cycles.
- New modules can complement (e.g. soil, machinery, intelligence) and call this module’s API where needed.

---

## 7. Checklist Summary

| Item | Status |
|------|--------|
| `ha_to_process` in `JobCreateRequest` | Fixed |
| JWT issuer / JWKS defaults (artotxiki.com) | Fixed |
| Auth logging (issuer at debug level) | Fixed |
| `viewerSlots` exported from App | Fixed |
| `getRecentJobs` returns array | Fixed |
| `env.example` expanded | Done |
| `.gitignore` (.dockerignore tracked) | Fixed |
| No hardcoded secrets | OK |
| Tenant isolation in queries | OK |
| i18n | Recommended (incremental) |
| Optional `/capabilities` endpoint | Documented |
| Optional FIWARE-Service in auth | Recommended |
| Optional refactor: routers out of main.py | Recommended |

---

## 8. References

- Platform: `nekazari-public` (CLAUDE.md, GitOps, services).
- Module template: `nekazari-public/module-template` (backend config, auth middleware, frontend App export, env.example, K8s, CI).
- External module install: `docs/modules/EXTERNAL_MODULE_INSTALLATION.md` (ingress, routing, imagePullSecrets).

This document should be updated when aligning with future template or platform changes.
