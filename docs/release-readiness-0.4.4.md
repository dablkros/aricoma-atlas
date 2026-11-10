# Release readiness: v0.4.4

Audit date: 2026-11-10  
Prepared branch: `feat/multi-tenant-netbox`  
Prepared base HEAD: `0f108b7ba132dc3522bfd5eb0bc90243f13f8138`  
Previous release tag: `v0.4.3`

## Recommendation

**CONDITIONAL GO for a release pull request; NO-GO for the tag until the
resulting `main` commit passes the complete CI and required VM acceptance.**

This document records repository evidence available during release metadata
preparation. It does not claim that the current uncommitted documentation diff,
the future merge commit or a target VM has passed acceptance.

Confidence: **high** for repository metadata and static documentation checks;
**unverified** for CI, clean installation, upgrade and physical-device/VPN
acceptance of the final release commit.

## Candidate scope

**Fact:** compared with `v0.4.3`, prepared base HEAD `0f108b7` contains 44
commits and changes 126 files with 14,818 insertions and 340 deletions.

The principal release areas are:

- multi-tenant NetBox Tenant/Site/management-VRF inventory;
- inventory UI platform resolution and tenant-safe import;
- credential onboarding with purpose-scoped OpenBao storage;
- persistent VPN registry and Site-default/Device-override assignments;
- VPN profile import, isolated connectivity tests and OpenVPN prophylaxis;
- beta Fortinet IKEv2, Network ID, MFA and optional FCT compatibility input;
- Atlas local administrator authentication and protected Operations UI;
- Cisco platform expansion, including Catalyst 1200/1300 Cisco SMB handling;
- catalog expansion to 905 Device Types and 33,220 component templates.

## Release blockers

1. Commit the reviewed release metadata and obtain a green pull-request CI.
2. Merge into `main` without unresolved conflicts and obtain a green complete
   workflow for the exact merge commit.
3. Run the clean-install, redeploy and upgrade acceptance required by the
   project release gate.
4. Confirm the working tree is clean and create the annotated tag on the
   verified `main` commit, not on the feature branch.
5. Preserve the 10 November 2026 HBM-Ext1 A/B evidence and complete acceptance
   on every additional FortiGate profile represented as supported.

## Accepted beta limitations

- HBM-Ext1 established IKE SA and CHILD SA with FCT Notify 61696 both enabled
  and disabled. FCT is therefore optional for this tested gateway, but this
  does not establish the same behavior for other FortiGate configurations.
- Automated prophylaxis VPN sessions currently use OpenVPN. IPsec and
  openfortivpn remain connectivity-test paths.
- Catalyst 1200/1300 Cisco SMB behavior requires acceptance per intended
  hardware and firmware family.
- A declared platform identity does not enable a consumer automatically;
  prophylaxis, Oxidized and monitoring still require explicit mappings.

## Static release checks completed during preparation

- package, example environment and deployment image versions agree on `0.4.4`;
- README version examples and release commands target `v0.4.4`;
- README catalog totals agree with the tracked 905-file built catalog;
- Markdown/source diff contains no whitespace errors (`git diff --check`).

No unit, integration, Docker, deployment or live-device test was run during
this documentation-only preparation.

