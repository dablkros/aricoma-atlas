# VPN Connection Registry (P1)

## Účel a hranice

VPN Connection Registry je perzistentný register netajných metadát, podľa
ktorých Atlas určí spojenie priradené konkrétnemu NetBox Device. P1 nevytvára
tunel, nemení routing hostiteľa, nekontroluje dostupnosť gateway a nespúšťa VPN
klienta. Preto je `runtime_status` vždy `not_deployed`.

## Dátový model

| Pole | Význam |
|---|---|
| `id` | Interné UUID vytvorené Atlasom. |
| `connection_id` | Nemenný verejný identifikátor, globálne unique. |
| `name` | Zobrazovaný názov. |
| `tenant_id` | NetBox Tenant ID; zdroj pravdy pre vlastníctvo. |
| `tenant_slug` | Pri čítaní odvodený z NetBoxu, neukladá sa ako zdroj pravdy. |
| `vendor` | `fortinet`, `checkpoint` alebo `generic`. |
| `protocol` | `ipsec-ikev2`, `ssl-vpn`, `wireguard` alebo `openvpn`. |
| `driver` | `strongswan`, `forticlient`, `checkpoint-snx`, `wireguard` alebo `openvpn`. |
| `gateway_host`, `gateway_port` | Netajný cieľ VPN gateway. |
| `auth_method` | `username_password`, `certificate`, `psk` alebo `external_mfa`. |
| `secret_ref` | Presná referencia `vpn/connections/<connection_id>`. |
| `management_vrf_id` | Tenant-owned NetBox management VRF. |
| `allowed_prefixes` | Neprázdny zoznam normalizovaných CIDR sietí. |
| `administrative_status` | `planned`, `enabled` alebo `disabled`. |
| `created_at`, `updated_at` | UTC čas vytvorenia a poslednej zmeny. |

Povolené vendor/protocol/driver hodnoty sú katalóg, nie tvrdenie o funkčnom
VPN klientovi. Kombinácie sa v P1 zámerne neblokujú bez overenej kompatibility.

## Perzistencia a migrácia

Register používa samostatný SQLite súbor
`/run/atlas/vpn/connections.sqlite3`, bind-mountovaný z chránenej
`.runtime/vpn/` s adresárom `0700` a databázou `0600`. Tabuľka
`vpn_registry_migrations` eviduje aplikovanú schému; migrácia `1` vytvorí
`vpn_connections`, unique constraint pre `connection_id` a tenant index.
Migrácia je dopredná a nie je deštruktívna.

`DELETE` nevykonáva fyzické mazanie: nastaví `administrative_status=disabled`.
Staré `inventory.connection_profiles` nie sú druhým zdrojom pravdy. Ak sa
objavia v site YAML, validácia ich explicitne odmietne s pokynom vytvoriť
plnohodnotné záznamy cez `/api/v1/vpn/connections`; neúplné P0 profily sa
automaticky a potichu nemigrujú.

## NetBox mapovanie a resolver

Custom field `atlas_connection_id` podporuje `dcim.site` aj `dcim.device`.
Resolver postupuje striktne:

1. načíta Device podľa NetBox ID;
2. overí jeho Tenant a Site;
3. načíta `primary_ip4`, jej Tenant a VRF;
4. použije Device override, inak Site hodnotu;
5. vyhľadá presné `connection_id` v SQLite registri;
6. overí rovnaký Tenant a `management_vrf_id`;
7. overí management IP voči `allowed_prefixes`;
8. vráti iba netajné metadata a `runtime_status=not_deployed`.

Nepriradené zariadenie zostáva platným NetBox objektom a resolver vráti
`resolution_status=unmapped`. Neexistuje automatický fallback na iné spojenie,
Tenant ani VRF. Prekrývajúce sa IP adresy sú bezpečne oddelené Tenantom a VRF.

### Operations UI assignment flow

Operations UI používa Site ako predvolenú úroveň priradenia. Operátor vyberie
Tenant, jedno tenant-owned VPN spojenie a jednu alebo viac Sites. Voľba všetkých
Sites je pohodlný hromadný zápis, nie nový tenantový fallback. Nové Sites preto
nie sú automaticky priradené bez samostatného potvrdeného plánu.

Device assignment je explicitný override. Jeho odstránenie zapíše do
`atlas_connection_id` hodnotu `null`, čím sa zariadenie vráti k Site dedičnosti.
Dry-run a Apply znovu kontrolujú Tenant všetkých cieľov aj VPN spojenia,
zachovávajú ostatné NetBox custom fields a Apply je viazaný na HMAC odtlačok
presného plánu. OpenBao secret sa počas priraďovania nenačítava.

Apply zapisuje jednotlivé NetBox objekty postupne. Nie je to distribuovaná
transakcia: ak NetBox zlyhá uprostred hromadného zápisu, operátor vytvorí nový
dry-run, ktorý už úspešné zápisy označí ako `UNCHANGED` a ponúkne iba zostávajúce
operácie. Resolver následne pri každom zariadení samostatne overí zhodu Tenant,
VRF a `allowed_prefixes`; samotné Site priradenie tieto device-level podmienky
neobchádza.

## OpenBao secret reference

Atlas databáza uchováva iba `secret_ref`. Skutočné hodnoty sa vkladajú mimo
registry API do OpenBao KV v2 mountu `atlas/`:

```text
atlas/vpn/connections/customer-a-primary
```

Registry hodnota je preto:

```text
vpn/connections/customer-a-primary
```

Backend policy má iba read prístup k
`atlas/metadata/vpn/connections/*`. Endpoint `validate` preto kontroluje KV v2
metadata bez načítania secret hodnoty. Obsah secretu sa nevracia cez API ani CLI
a neloguje sa. Budúci VPN Worker musí dostať vlastnú, užšiu machine identity s
prístupom k potrebným data cestám; P1 worker ani zápis secretov neimplementuje.

## REST API

Všetky endpointy vyžadujú existujúcu Atlas administrátorskú session alebo
operator bearer token. `tenant_id` filter len obmedzuje zoznam; nenahrádza
autorizáciu.

| Metóda a cesta | Operácia |
|---|---|
| `GET /api/v1/vpn/connections` | Stránkovaný zoznam; `tenant_id`, `limit`, `offset`. |
| `POST /api/v1/vpn/connections` | Vytvorenie po NetBox validácii. |
| `GET /api/v1/vpn/connections/{connection_id}` | Detail bez secret obsahu. |
| `PATCH /api/v1/vpn/connections/{connection_id}` | Konzistentná čiastočná aktualizácia. |
| `DELETE /api/v1/vpn/connections/{connection_id}` | Kontrolovaná deaktivácia. |
| `POST /api/v1/vpn/connections/{connection_id}/validate` | NetBox a OpenBao referencia. |
| `GET /api/v1/vpn/devices/{device_id}/resolve` | Tenant-safe rozlíšenie zariadenia. |
| `GET /api/v1/vpn/assignments/options` | Tenant-scoped spojenia, Sites a aktívne Devices pre GUI. |
| `POST /api/v1/vpn/assignments/plan` | Secret-free dry-run Site alebo Device priradenia. |
| `POST /api/v1/vpn/assignments/apply` | Aplikuje iba potvrdený a nezmenený assignment plán. |

Príklad create payloadu:

```json
{
  "connection_id": "customer-a-primary",
  "name": "Customer A primary VPN",
  "tenant_id": 10,
  "vendor": "fortinet",
  "protocol": "ssl-vpn",
  "driver": "forticlient",
  "gateway_host": "vpn.customer-a.example",
  "gateway_port": 443,
  "auth_method": "username_password",
  "secret_ref": "vpn/connections/customer-a-primary",
  "management_vrf_id": 30,
  "allowed_prefixes": ["10.10.0.0/16"],
  "administrative_status": "planned"
}
```

## CLI

```bash
sudo atlasctl vpn list --tenant-id 10
sudo atlasctl vpn show customer-a-primary
sudo atlasctl vpn resolve --device-id 101 --tenant-id 10
sudo atlasctl vpn validate customer-a-primary
```

`resolve` iba overuje mapovanie. `validate` kontroluje NetBox vlastníctvo a
dostupnosť OpenBao referencie; nekontroluje VPN gateway ani tunel.

## Kontrolované chyby

Medzi stabilné reason codes patria `connection_not_found`,
`connection_id_conflict`, `connection_tenant_mismatch`,
`connection_vrf_mismatch`, `management_ip_outside_allowed_prefixes`,
`management_vrf_tenant_mismatch`, `vpn_secret_not_found`,
`vpn_secret_validation_unavailable`, `netbox_unavailable` a
`vpn_registry_unavailable`. Validačné chyby vstupu používajú spoločný API kód
`validation_error`.

## Manuálna akceptácia na Ubuntu VM

1. Nasaďte build a overte, že backend compose obsahuje mount
   `/run/atlas/vpn` a premennú `ATLAS_VPN_REGISTRY_FILE`.
2. V NetBoxe vytvorte dvoch Tenantov, každý s vlastnou management VRF a Site.
   V oboch VRF možno použiť rovnakú management IP, napríklad
   `192.168.1.10/24`.
3. Autorizovaným `POST /api/v1/vpn/connections` vytvorte jedno spojenie pre
   každý Tenant. Použite odlišné `connection_id`, Tenant ID a VRF ID.
4. Do OpenBao KV v2 mountu `atlas/` manuálne zapíšte testovací secret na
   `vpn/connections/<connection_id>`. Produkčný secret nevkladajte do shell
   histórie ani JSON fixture.
5. Na každom Site nastavte jeho `atlas_connection_id`; na jednom Device
   voliteľne nastavte iný, tenant-owned override.
6. Spustite `sudo atlasctl vpn list`, `show`, `validate` a `resolve`. Výstupy
   nesmú obsahovať hodnotu hesla, PSK ani privátneho kľúča.
7. Zmeňte Site mapping na connection druhého Tenanta. Resolver musí skončiť
   `connection_tenant_mismatch`, nie fallbackom.
8. Obnovte Tenant, ale nastavte nesprávnu VRF alebo prefix mimo management IP.
   Resolver musí vrátiť `connection_vrf_mismatch`, respektíve
   `management_ip_outside_allowed_prefixes`.
9. Odstavte OpenBao a spustite `validate`. Registry záznam musí zostať čitateľný
   a validácia musí skončiť kontrolovaným
   `vpn_secret_validation_unavailable`.
10. Zavolajte `DELETE` a overte, že záznam zostal zachovaný so stavom
    `disabled`. Na hostiteľovi sa nesmie objaviť nové rozhranie, route ani VPN
    proces.

## Rozšírenie o vendora a P2

Pridanie vendora v P1 znamená rozšíriť validované katalógové hodnoty a testy;
nesmie automaticky aktivovať klienta. P2 VPN Worker musí samostatne doplniť:

- explicitnú maticu podporovaných vendor/protocol/driver kombinácií;
- vlastnú OpenBao AppRole s prístupom iba k potrebným secretom;
- izoláciu procesov/siete a routing model schválený pre multi-tenant prevádzku;
- stavový automat `connecting/connected/degraded/error`;
- retry, reconnect, audit a health kontrakty;
- integráciu konzumentov až po potvrdenom spojení.
