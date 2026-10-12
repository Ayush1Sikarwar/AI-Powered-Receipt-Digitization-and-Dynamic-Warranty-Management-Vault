# infrastructure/deployment

Reserved for Phase 6 (Production Hardening) per PRD Phase 6 / TSD §51:

- Staging/production deployment manifests (SDD §11 environments)
- CI/CD deploy jobs (current CI in `.github/workflows/ci.yml` runs tests
  and builds only; it does not deploy)
- Secret-management wiring (secrets exist only in environment/secret
  management, never in the repo — TSD §52)

Intentionally empty until those deliverables are implemented.
