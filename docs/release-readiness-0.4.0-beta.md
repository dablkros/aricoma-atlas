# Release readiness: 0.4.0-beta

Audit date: 2026-10-16  
Audited branch: `atlas/monitoring`  
Audited base HEAD: `f031b4747f9988112ccb013f6bb5e9b2722dab9f`  
Latest existing local and remote tag: `v0.3.0-beta`

## Recommendation

**NO-GO until the local stabilization changes pass the complete GitHub release
workflow.** The base HEAD is green, but the Cisco SMB mapping, version bump and
documentation changes exist only in the working tree and therefore have no CI
evidence yet. After a green run on the committed candidate, the remaining
items below are acceptable beta limitations if they are retained in the
release notes.

Confidence: **High** for repository and base-HEAD CI findings; **medium** for
the secret scan because no dedicated history scanner was available locally.

## 1. Latest existing tag

**Fact:** `v0.3.0-beta` is the latest local and `origin` tag. It points to merge
commit `7520ea2` dated 2026-10-08. The audited base HEAD descends from it.

## 2. Changes since the latest tag

**Fact:** the committed comparison `v0.3.0-beta..f031b47` contains 19 commits,
79 changed paths, 7,515 insertions and 220 deletions. The principal changes are
Zabbix deployment/API/reconciliation, monitoring schema v2, FortiGate
provisioning, Sophos and Cisco CBS templates, HPE Aruba catalog/platform
support, new OpenBao policy boundaries, Operations UI updates and tests.

**Fact:** the release worktree additionally maps `cisco-cbs` and
`cisco-cbs250-350` to `ciscosmb`, refreshes the catalog audit snapshot, adds a
regression test, updates version metadata to `0.4.0-beta`, and updates release
documentation. It also removes the false Ubuntu 22.04 support claim: the pinned
`ansible-core 2.21.5` requires Python 3.12+, while Ubuntu 22.04 provides an
older system Python. The installer now supports Ubuntu 24.04/26.04 and fails
early below Python 3.12.

## 3. Completed functionality

- **Fact:** NetBox remains the source of device identity, platform, primary IP,
  enable flags and `credential_profile`.
- **Fact:** OpenBao remains the source of credentials. Runtime identity and
  materialized credential files are ignored by Git.
- **Fact:** Oxidized uses JSONFile inventory, keeps configuration history in a
  persistent Git volume and enforces SSH host-key verification against its
  persistent administrator-approved `known_hosts` file.
- **Fact:** Zabbix 7.0.31 is the monitoring backend. Host provisioning is
  declarative and tested for repeat execution.
- **Fact:** monitoring configuration files use schema v2. The prophylaxis
  result schema v1 is a separate data contract and is not a monitoring-config
  fallback.
- **Fact:** `monitoring_enabled` is canonical. `checkmk_enabled` remains only in
  migration code, migration tests and historical documentation.
- **Fact:** active CheckMK deployment policy/runtime files are removed.

## 4. Experimental functionality

- **Fact:** Cisco CBS platforms now map explicitly to Oxidized `ciscosmb`.
- **Fact:** Oxidized 0.37.0 contains `ciscosmb`, but its source identifies older
  Small Business 300, 500 and ESW2 families, not CBS220/250/350 specifically.
- **Inference:** the explicit mapping is suitable for beta testing but does not
  establish physical-device compatibility. Accept a real backup, redaction and
  restore/diff workflow before production enablement.
- **Fact:** FortiGate monitoring provisioning is guarded and tested statically;
  its documented live FortiOS 7.0.9 acceptance remains outstanding.

Primary Oxidized evidence: [ciscosmb model at 0.37.0](https://github.com/ytti/oxidized/blob/0.37.0/lib/oxidized/model/ciscosmb.rb) and [supported OS types at 0.37.0](https://github.com/ytti/oxidized/blob/0.37.0/docs/Supported-OS-Types.md).

## 5. Known limitations

- **SHOULD FIX:** OpenBao still runs with server TLS disabled; compensate with
  host/network isolation until end-to-end TLS is implemented.
- **SHOULD FIX:** production backend configuration has legacy FortiOS
  certificate validation disabled for direct API checks. The newer monitoring
  schema defaults to verification and permits an explicit lab exception only.
- **NON-BLOCKING FOR BETA:** production SSO/RBAC for Atlas/Oxidized remains
  incomplete. Oxidized ingress uses HTTPS Basic Auth and a management CIDR
  allowlist; the backend API is bound to loopback.
- **NON-BLOCKING FOR BETA:** 195 imported device types remain intentionally
  unmapped rather than receiving guessed platforms.

## 6. Security findings

- **Fact:** no `.env`, `.runtime`, PEM/private-key, P12 or PFX file is tracked.
  A repository-history signature check found no private-key PEM header.
- **Fact:** `.runtime/` and `.env` are ignored. Existing local runtime identity
  files were not opened or modified during this audit.
- **Fact:** OpenBao policies separate deployer, operator, backend, monitoring,
  NetBox and Oxidized identities; policy unit tests passed in the base-HEAD CI.
- **Fact:** source contains expected password/token identifiers in secret
  handling code and fixtures. No committed live credential was identified by
  the manual scan.
- **Limitation:** gitleaks/trufflehog was not available, so the secret finding
  is not equivalent to a dedicated entropy and full-history scan.

## 7. Test results

- **Fact:** GitHub Actions run
  [38876630159](https://github.com/dablkros/aricoma-atlas/actions/runs/38876630159)
  passed all five jobs on base HEAD `f031b47`: validation/unit tests, full Atlas
  clean installation and second deployment, OpenBao integration, Oxidized
  HTTPS ingress/persistence, and NetBox upgrade/data preservation.
- **Fact:** 76 focused local backend/installer, device-model, Oxidized runtime
  and Oxidized-sync tests pass with the stabilization changes. Python syntax
  compilation, catalog build, catalog audit and component-field audit also pass.
- **Fact:** the catalog contains 820 device types; 625 are platform-mapped and
  195 intentionally unmapped. The generated report matches the refreshed
  documentation snapshot.
- **Environment limitation:** the complete local discovery executed 144 tests:
  136 passed and 8 modules failed to import because the pre-existing venv uses
  Python 3.9 and lacks FastAPI. A Python 3.14 dependency install was blocked by
  missing local `libssh` headers for `ansible-pylibssh`; installing a reduced
  test dependency set was not authorized. These are test-environment failures,
  not observed assertion failures.

## 8. Clean install and redeploy status

- **Fact:** base-HEAD CI completed a fresh Atlas deployment, a second complete
  deployment, health verification, device-model idempotency and preserved
  Oxidized state.
- **Fact:** the upgrade job deployed `v0.3.0-beta`, upgraded to base HEAD,
  verified persistent data survival, and passed post-upgrade idempotency.
- **Not verified:** no local or production deployment was performed, and the
  uncommitted Cisco/version/documentation changes have not run in Docker CI.

## 9. Remaining blockers

1. **BLOCKER:** commit the reviewed worktree changes and obtain a complete green
   workflow for that exact commit.
2. **BLOCKER:** confirm `docs/platform-audit.json` equality in that workflow.
3. **BLOCKER:** do not create the tag until the candidate commit is clean,
   reviewed and green.
4. **SHOULD FIX / explicitly waive for beta:** validate `ciscosmb` on each CBS
   family intended for collection; otherwise retain the experimental label.
5. **SHOULD FIX:** run a dedicated secret scanner before publishing the tag.

## 10. Recommended next version

**Inference:** `v0.4.0-beta` is appropriate because the release adds a new
monitoring backend and multiple integration/platform contracts rather than a
patch-only correction. Create the annotated tag only after the blockers above
are closed; do not tag the current uncommitted worktree.
