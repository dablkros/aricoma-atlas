# Device credential onboarding

Atlas Operations UI provides an explicit plan/apply flow for device credentials.
One plan targets one or more active devices from one tenant and the same
manufacturer. The operator first selects the tenant, then one explicit platform
and one shared credential profile for the whole batch. Atlas blocks devices
without a tenant, cross-tenant batches and a device that already has a different
platform instead of silently replacing it.
It stores only `credential_profile`, the selected platform and enablement flags
in NetBox. Passwords, API tokens and SNMP communities are written only to the
OpenBao KV v2 mount.

## Paths and consumers

```text
devices/credentials/<manufacturer.slug>/<credential_profile>/prophylaxis
devices/credentials/<manufacturer.slug>/<credential_profile>/oxidized
devices/credentials/<manufacturer.slug>/<credential_profile>/snmp
```

- `prophylaxis`: SSH `username`, `password`, optional `enable_password`; or a
  FortiOS `api_token` when the exact `fortios-api` NetBox service is selected.
- `oxidized`: dedicated SSH `username` and `password`.
- `snmp`: SNMPv2c `community` in the first implementation.

There is no fallback between purpose leaves. The browser sends secret values
only in the authorized plan/apply requests. Atlas never returns them in the
response, logs them, or stores them in NetBox.

## Safety contract

1. Select one tenant and one or more active NetBox devices from that tenant and
   the same manufacturer, an explicit compatible platform and a shared
   credential profile. A plan accepts at most 100 unique devices.
2. Select the consumers and enter their purpose-specific values.
3. Create a dry-run plan. An existing different value is a conflict unless
   explicit replacement was selected.
4. Confirm and apply the exact plan. The plan fingerprint is HMAC-bound to the
   operator token and the complete request, including the secret values.
5. OpenBao writes use KV v2 compare-and-set. New values use `cas=0`; replacements
   use the version observed during planning.

The dedicated `atlas-credential-writer` AppRole can create, read and update only
the three exact device credential leaves. It cannot list profiles, delete
credentials or access unrelated OpenBao paths.

FortiOS transport remains deterministic: an exact `fortios-api` service selects
HTTPS API; absence selects SSH. Atlas blocks ambiguous or conflicting service
state and does not silently fall back.
