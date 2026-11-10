# Release readiness: v0.5.0-beta

Audit date: 2026-11-10  
Prepared branch: `main`  
Prepared base HEAD: `109ef8a30ec8761ef0862752ce885be8bb35c3ea`  
Previous release tag: `v0.4.3`

## Recommendation

**CONDITIONAL GO for the beta release; NO-GO for the tag until the resulting
release commit passes the complete CI and required VM acceptance.**

The multi-tenant feature pull request is merged into `main`. This document
records repository evidence available during release metadata preparation. It
does not claim that the current uncommitted documentation diff, its future
commit or a target VM has passed acceptance.

Confidence: **high** for repository metadata and static documentation checks;
**unverified** for CI, clean installation, upgrade and physical-device/VPN
acceptance of the final release commit.

## Candidate scope

**Fact:** compared with `v0.4.3`, the merged `main` base contains the
multi-tenant inventory, credential onboarding, VPN registry and isolated VPN
runtime changes summarized below.

The principal release areas are:

- multi-tenant NetBox Tenant/Site/management-VRF inventory;
- inventory UI platform resolution and tenant-safe import;
- credential onboarding with purpose-scoped OpenBao storage;
- persistent VPN registry and Site-default/Device-override assignments;
- VPN profile import, isolated connectivity tests and OpenVPN prophylaxis;
- beta Fortinet IKEv2, Network ID and multi-stage MFA compatibility;
- optional per-connection FCT compatibility input, not required by default;
- Atlas local administrator authentication and protected Operations UI;
- Cisco platform expansion, including Catalyst 1200/1300 Cisco SMB handling;
- catalog expansion to 905 Device Types and 33,220 component templates.

## Release blockers

1. Commit the reviewed release metadata and obtain green CI for the exact
   release commit on `main`.
2. Run the clean-install, redeploy and upgrade acceptance required by the
   project release gate.
3. Confirm the working tree is clean and create the annotated tag on the
   verified `main` commit.
4. Preserve the 10 November 2026 HBM-Ext1 A/B evidence and complete acceptance
   on every additional FortiGate profile represented as supported.

## Accepted beta limitations

- HBM-Ext1 established IKE SA and CHILD SA with FCT Notify 61696 both enabled
  and disabled. Atlas therefore does not require FCT and does not send Notify
  61696 when no payload is supplied. This does not establish the same behavior
  for every FortiGate configuration.
- Automated prophylaxis VPN sessions currently use OpenVPN. IPsec and
  openfortivpn remain connectivity-test paths.
- Catalyst 1200/1300 Cisco SMB behavior requires acceptance per intended
  hardware and firmware family.
- A declared platform identity does not enable a consumer automatically;
  prophylaxis, Oxidized and monitoring still require explicit mappings.

## Static release checks completed during preparation

- package, example environment and deployment image versions agree on
  `0.5.0-beta`;
- README version examples and release commands target `v0.5.0-beta`;
- README catalog totals agree with the tracked 905-file built catalog;
- Markdown/source diff contains no whitespace errors (`git diff --check`).

No unit, integration, Docker, deployment or live-device test was run during
this documentation-only preparation.
