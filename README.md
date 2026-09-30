# Aricoma Atlas

Automatizované nasadenie a provisioning infraštruktúry pre projekt **Aricoma Atlas**.

Aktuálna beta verzia nasadzuje a pripravuje najmä:

- **OpenBao** ako centrálny secrets backend,
- **NetBox 4.7.1** cez Docker Compose,
- **Oxidized 0.37.0** s lokálnou Git históriou konfigurácií,
- spoločný **Nginx HTTPS proxy** s certifikátmi internej CA a autentifikáciou Oxidized,
- PostgreSQL a Valkey backend služby pre NetBox,
- Atlas NetBox baseline,
- Atlas Device Catalog,
- AppRole identity pre ďalšie Atlas služby,
- audit logging OpenBao.

Hlavný deployment workflow sa spúšťa jedným príkazom:

```bash
python scripts/deploy_atlas.py
```

`deploy_atlas.py` je nadradený orchestrátor: overí konfiguráciu zákazníckeho HTTPS vstupu, pripraví OpenBao a overí jeho readiness, potom nasadí NetBox, Oxidized a Nginx. Pred prvým spustením je potrebné pripraviť `.runtime/proxy.yaml`, DNS a certifikáty podľa sekcie nižšie.

---

# Aktuálny stav

**Release:** `0.1.0-beta`

Deklarovaná kombinácia pre tento deployment (výsledok integračných testov je v GitHub Actions):

- OpenBao `2.7.0`
- NetBox `4.7.1`
- NetBox Docker `5.1.1`
- NetBox image `docker.io/netboxcommunity/netbox:v4.7.1-5.1.1`
- Oxidized image `docker.io/oxidized/oxidized:0.37.0`
- Nginx image `docker.io/library/nginx:1.30.5`
- Docker Compose
- Python virtual environment

## Device Type katalóg

| Výrobca | Device Types |
|---|---:|
| Cisco | 238 |
| Fortinet | 162 |
| Juniper | 290 |
| **Spolu** | **690** |

## Component templates

| Typ komponentu | Počet |
|---|---:|
| Console ports | 860 |
| Console server ports | 0 |
| Power ports | 331 |
| Interfaces | 23 456 |
| Rear ports | 2 |
| Front ports | 0 |
| Module bays | 1 687 |
| Device bays | 2 |
| Power outlets | 4 |
| **Spolu** | **26 342** |

---

# Beta security status

Táto verzia je určená ako **beta baseline** a ešte nie je finálnym produkčným hardeningom.

## HTTPS vstup a zostávajúce TLS obmedzenia

V aktuálnom beta prostredí:

- OpenBao API používa lokálne HTTP,
- OpenBao listener má `tls_disable = true`,
- lokálne OpenBao API je bindnuté na loopback adresu,
- NetBox a Oxidized sú dostupné cez spoločný Nginx a HTTPS s certifikátmi zákazníckej internej CA,
- NetBox bootstrap HTTP port ostáva iba na `127.0.0.1:8000`,
- Oxidized web/API nemá publikovaný host port; prístup vedie cez Nginx Basic Auth,
- Nginx vyžaduje management IP a explicitný zoznam povolených podsietí,
- komunikácia Nginx → backend prebieha cez HTTP v oddelených lokálnych Docker sieťach.

Pred produkčným nasadením je potrebné doplniť minimálne:

1. TLS pre OpenBao,
2. dôveryhodný CA trust a certificate verification v Python klientoch,
3. odstránenie `tls_disable`,
4. overiť HTTPS vstup, DNS, povolené podsiete a izoláciu portov na zákazníckej VM,
5. lifecycle certifikátov a ich obnovu.

Produkčná verzia nesmie používať `verify=False` ako náhradu za správne overenie certifikátu.

---

# Architektúra beta deploymentu

```text
scripts/deploy_atlas.py
        │
        ├── 1. OpenBao
        │      │
        │      ├── Docker deploy
        │      ├── init pri fresh deployi
        │      ├── Shamir unseal 5/3
        │      ├── Raft readiness
        │      ├── KV v2: atlas/
        │      ├── policies
        │      ├── AppRoles
        │      ├── audit logging
        │      └── revoke initial root token
        │
        ├── VERIFY OPENBAO
        │      ├── initialized
        │      ├── unsealed
        │      └── atlas-deployer login
        │
        ├── 2. NetBox
               │
               ├── secrets z OpenBao
               ├── pinned netbox-docker
               ├── PostgreSQL + Valkey
               ├── NetBox
               ├── API token
               ├── Atlas baseline
               └── Atlas Device Catalog
        │
        ├── 3. Oxidized
        │      ├── prázdny inventár pri prvom nasadení
        │      ├── persistentná Git história
        │      └── web/API len v internej sieti
        │
        └── 4. Nginx
               ├── samostatné DNS názvy služieb
               ├── HTTPS s certifikátmi internej CA
               ├── management subnet allowlist
               └── Oxidized Basic Auth: secret v OpenBao
```

Ak OpenBao stage zlyhá, NetBox stage sa nespustí.

Nginx sa spustí až po dokončení backendov. Chýbajúca alebo neplatná proxy konfigurácia zastaví celý deploy už pri preflight kontrole.

---

# Štruktúra repozitára

```text
aricoma-atlas/
├── .github/
│   └── workflows/
│       └── validate.yml
│
├── atlas/
│   ├── openbao_client.py
│   ├── deployment.py
│   └── proxy.py
│
├── catalog/
│   ├── manifest.yaml
│   └── baseline/
│       ├── custom_field_choice_sets.yaml
│       └── custom_fields.yaml
│
├── deployment/
│   ├── netbox.yaml
│   ├── oxidized.yaml
│   ├── proxy.example.yaml
│   └── openbao/
│       ├── docker-compose.yml
│       ├── openbao.hcl
│       └── policies/
│           ├── atlas-deployer.hcl
│           ├── atlas-operator.hcl
│           ├── device-credentials-read.hcl
│           ├── netbox-runtime.hcl
│           ├── checkmk-runtime.hcl
│           └── oxidized-runtime.hcl
│
├── scripts/
│   ├── deploy_atlas.py
│   ├── deploy_openbao.py
│   ├── deploy_netbox.py
│   ├── deploy_oxidized.py
│   ├── deploy_proxy.py
│   ├── ci_ingress.py
│   ├── openbao_access.py
│   ├── bootstrap_netbox.py
│   ├── build_catalog.py
│   ├── provision_baseline.py
│   ├── provision_netbox.py
│   ├── audit_catalog.py
│   └── audit_component_fields.py
│
├── requirements.txt
├── tests/
│   └── test_ingress.py
├── .gitignore
└── README.md
```

Lokálne a generované dáta sa do Git repozitára neukladajú:

```text
.venv/
build/
devicetype-library/
.runtime/
.env
.env.*
```

---

# `.runtime/`

Adresár `.runtime/` obsahuje lokálny runtime konkrétnej Atlas inštancie a **nesmie byť commitovaný**.

Príklady:

```text
.runtime/
├── openbao-approle.json
├── openbao-operator.json
├── openbao-netbox.json
├── openbao-checkmk.json
├── openbao-oxidized.json
└── netbox-docker/
```

OpenBao unseal keys sa do `.runtime/` neukladajú.

Každý `openbao-*.json` obsahuje machine identity pre konkrétnu AppRole a musí zostať lokálny na danom serveri.

---

# Požiadavky na host

Host musí mať minimálne:

- Git,
- Python,
- Python `venv`,
- Docker,
- Docker Compose plugin.
- OpenSSL CLI pre kontrolu CA certifikátov a generovanie hashov.

Pre loopback port isolation používaj Docker Engine 28 alebo novší. Staršie verzie majú známe obmedzenie izolácie portov publikovaných na loopback adresu. [Docker port publishing](https://docs.docker.com/engine/network/port-publishing/) (overené 30. 9. 2026).

Overenie Dockeru:

```bash
docker --version
docker compose version
docker info
```

Odporúčané je používať samostatný Python virtual environment.

---

# Python virtual environment

Vytvorenie:

```bash
python3 -m venv .venv
```

Aktivácia:

```bash
source .venv/bin/activate
```

Inštalácia dependencies:

```bash
python -m pip install -r requirements.txt
```

Overenie:

```bash
python --version
```

Poznámka: na staršom systémovom Pythone v macOS môže `urllib3` zobrazovať `NotOpenSSLWarning` kvôli LibreSSL. Beta flow tým nie je blokovaný, ale pred produkčným nasadením sa má použiť podporovaný Python/OpenSSL stack.

---

# One-command Atlas deployment

## Konfigurácia pre konkrétnu zákaznícku VM

DNS názvy, management IP, povolené podsiete a certifikáty sa líšia podľa zákazníka. Nie sú pevne zapísané v repozitári.

```bash
mkdir -p .runtime
chmod 700 .runtime
cp deployment/proxy.example.yaml .runtime/proxy.yaml
chmod 600 .runtime/proxy.yaml
```

Vyplň všetky prázdne hodnoty v `.runtime/proxy.yaml`:

| Položka | Význam |
|---|---|
| `proxy.listen_address` | Konkrétna management IP tejto VM, nie `0.0.0.0`. |
| `proxy.https_port` | HTTPS port, štandardne `443`. |
| `proxy.allowed_networks` | Neprázdny zoznam povolených správcovských CIDR podsietí/VPN; `0.0.0.0/0` a `::/0` sa odmietnu. |
| `tls.ca_certificate` | Absolútna cesta k PEM trust bundle zákazníckej internej CA. |
| `services.netbox.hostname` | DNS názov NetBoxu smerujúci na management IP VM. |
| `services.oxidized.hostname` | Samostatný DNS názov Oxidized smerujúci na rovnakú management IP. |
| `services.*.certificate` | Absolútna cesta k PEM serverovému certifikátu, vrátane intermediate chain. |
| `services.*.private_key` | Absolútna cesta k príslušnému nešifrovanému PEM privátnemu kľúču. |

Certifikát musí pokrývať príslušný DNS názov. Jeden SAN certifikát môže pokrývať oba názvy, alebo možno použiť dva samostatné certifikáty. Klienti musia dôverovať internej CA. Deploy overuje zhodu certifikátu s kľúčom, platnosť, DNS meno a reťazec voči nastavenej CA; nevydáva certifikáty ani nemení zákaznícke DNS.

Iný lokálny konfiguračný súbor možno zvoliť cez `ATLAS_PROXY_CONFIG`. Hodnoty `.runtime/`, certifikáty ani kľúče sa necommitujú. Výstupný runtime obsahuje lokálne kópie TLS súborov; chráň a zálohuj ho ako citlivé dáta.

Na firewalli povoľ HTTPS na management IP iba zo správcovských sietí/VPN. Proxy zároveň uplatňuje CIDR allowlist. Pred proxy sa v tejto verzii nepredpokladá ďalší load balancer; Nginx používa skutočnú adresu spojenia a nedôveruje klientskému `X-Forwarded-For`.

## Prihlasovanie a hranice prístupu

Oxidized nemá vlastný používateľský login. Nginx chráni celý jeho web aj API pomocou Basic Auth cez HTTPS. [Nginx Basic Auth](https://nginx.org/en/docs/http/ngx_http_auth_basic_module.html), [oxidized-web](https://github.com/ytti/oxidized-web) (overené 30. 9. 2026).

Pri prvom deployi `deploy_proxy.py` vygeneruje účet a uloží ho do KV v2 na `atlas/oxidized/web` (`username`, `password`). Pri opakovanom deployi sa hodnoty používajú znova. Chybný existujúci secret alebo chyba oprávnení zastaví deploy; heslo sa potichu nezmení. Nginx dostane iba SHA-512 crypt hash do `.runtime/proxy/oxidized.htpasswd`, nie plaintext heslo ani OpenBao token.

Prihlásený administrátor s príslušným oprávnením môže secret načítať cez OpenBao CLI:

```bash
bao kv get -mount=atlas oxidized/web
```

Príkaz predpokladá už nastavenú bezpečnú autentifikáciu CLI a správnu adresu OpenBao. Výstup obsahuje heslo; neukladaj ho do zdieľaných logov.

NetBox používa svoje vlastné používateľské účty a API tokeny. Nginx pred neho nepridáva Basic Auth. OpenBao zostáva dostupné iba lokálne na loopback; proxy ho nesprístupňuje.

Basic Auth účet pre Oxidized má prístup k celému jeho rozhraniu, vrátane API. Táto verzia nerieši individuálne roly, read-only používateľov ani SSO. Prístup majú dostať iba poverení správcovia.

Hlavný entrypoint:

```bash
python scripts/deploy_atlas.py
```

Deployment vykoná:

```text
Docker preflight
        │
        ▼
OpenBao container
        │
        ▼
OpenBao init / unseal
        │
        ▼
Raft leader readiness
        │
        ▼
OpenBao bootstrap
        │
        ▼
OpenBao readiness gate
        │
        ▼
NetBox Docker runtime
        │
        ▼
OpenBao-backed NetBox secrets
        │
        ▼
PostgreSQL + Valkey
        │
        ▼
NetBox healthcheck
        │
        ▼
NetBox API token
        │
        ▼
Atlas baseline
        │
        ▼
Atlas Device Catalog
        │
        ▼
Oxidized + Git volume
        │
        ▼
Nginx HTTPS + Oxidized Basic Auth
        │
        ▼
ARICOMA ATLAS READY
```

---

# Fresh deployment

Pri úplne novom OpenBao storage sa automaticky vykoná inicializácia.

Používa sa Shamir model:

```text
Shares:     5
Threshold:  3
```

OpenBao vygeneruje:

- 5 unseal keys,
- initial root token.

## Unseal keys

Unseal keys sa:

- zobrazia adminovi okamžite po inicializácii,
- pri spustení cez `deploy_atlas.py` sa držia iba v pamäti procesu,
- po úspešnom dokončení celého fresh deploymentu sa ešte raz zobrazia na konci,
- neukladajú sa do súboru,
- neukladajú sa do Git repozitára,
- nemajú byť uložené na tom istom Atlas serveri.

Admin ich musí uložiť mimo servera podľa interného recovery procesu.

Initial root token sa používa iba počas prvotného bootstrapu a následne sa automaticky revokuje.

---

# OpenBao po reštarte

Bez auto-unseal sa OpenBao po reštarte vráti do stavu `sealed`.

Opätovné spustenie:

```bash
python scripts/deploy_atlas.py
```

Deployment zistí sealed stav a interaktívne vyžiada 3 z 5 unseal keys:

```text
Unseal key 1/3:
Unseal key 2/3:
Unseal key 3/3:
```

Po úspešnom unseal-e pokračuje deployment automaticky.

---

# OpenBao storage

OpenBao používa integrovaný **Raft storage**.

Persistentné dáta sú uložené v Docker volume a pretrvávajú medzi reštartmi kontajnera.

Fresh deploy flow po inicializácii explicitne čaká na Raft leader election pred vykonaním bootstrap operácií. Tým sa predchádza race condition, pri ktorej môže byť storage krátko po unseal-e read-only.

---

# OpenBao KV v2

Atlas používa KV v2 mount:

```text
atlas/
```

Beta secret model:

```text
atlas/
├── devices/
│   ├── credentials/
│   │   ├── default
│   │   ├── cisco
│   │   ├── aruba
│   │   └── ...
│   └── snmp/
│       └── ...
│
├── netbox/
│   └── ...
├── checkmk/
│   └── ...
└── oxidized/
    └── ...
```

Význam:

- `atlas/devices/*` – spoločné credentials pre spravované zariadenia,
- `atlas/netbox/*` – interné NetBox secrets,
- `atlas/checkmk/*` – interné Checkmk secrets,
- `atlas/oxidized/*` – interné Oxidized secrets.

Prázdne paths sa nevytvárajú dopredu. V KV vzniknú až pri prvom zápise konkrétneho secretu.

---

# OpenBao AppRoles

Beta verzia vytvára samostatné machine identities.

## `atlas-deployer`

Používa sa pre deployment a bootstrap.

Má oprávnenia potrebné na čítanie a správu Atlas secrets.

## `atlas-operator`

Je oddelená operator identita určená pre autentifikovaný `generate-root` workflow.

Nepoužíva sa ako bežná aplikačná identita.

## `netbox-runtime`

Runtime identita pripravená pre NetBox.

Policies:

```text
netbox-runtime
device-credentials-read
```

## `checkmk-runtime`

Runtime identita pripravená pre Checkmk.

Policies:

```text
checkmk-runtime
device-credentials-read
```

## `oxidized-runtime`

Runtime identita pripravená pre Oxidized.

Policies:

```text
oxidized-runtime
device-credentials-read
```

Runtime policies sú navrhnuté ako read-only. Zápis a správa secrets zostáva deployment vrstve.

---

# Shared device credentials policy

Policy:

```text
device-credentials-read
```

umožňuje runtime službám čítať spoločný strom:

```text
atlas/devices/*
```

Používa sa preto, aby sa rovnaké credentials sieťových zariadení nemuseli duplikovať pre Checkmk, Oxidized a ďalšie Atlas aplikácie.

App-specific secrets zostávajú oddelené.

---

# OpenBao audit logging

OpenBao audit device je v beta verzii zapnutý a smeruje na `stdout` kontajnera.

Audit log je dostupný cez Docker:

```bash
docker compose \
  -f deployment/openbao/docker-compose.yml \
  logs openbao
```

Posledných 100 riadkov:

```bash
docker compose \
  -f deployment/openbao/docker-compose.yml \
  logs --tail=100 openbao
```

Live sledovanie:

```bash
docker compose \
  -f deployment/openbao/docker-compose.yml \
  logs -f openbao
```

Audit udalosti sú JSON záznamy a obsahujú napríklad:

- request/response typ,
- API path,
- operation,
- auth metadata,
- policy context.

Pre produkciu sa počíta s oddeleným persistentným audit logom, rotáciou a prípadným forwardingom do centrálneho log managementu/SIEM.

---

# OpenBao access helper

Pre servisné administrátorské operácie je určený:

```bash
python scripts/openbao_access.py
```

Aktuálne podporuje napríklad:

- vytvorenie krátkodobého Atlas deployer tokenu,
- quorum-based temporary root workflow,
- revoke tokenu,
- inspect tokenu.

Temporary root token je vytvorený iba na zvolený čas a quorum-generated bootstrap root je po výmene okamžite revokovaný.

Root prístup je break-glass mechanizmus, nie bežný deployment credential.

---

# NetBox secrets

OpenBao je jediný source of truth pre NetBox credentials.

NetBox deployment používa paths približne:

```text
atlas/netbox/
├── postgres
│   └── password
├── redis
│   └── password
├── redis-cache
│   └── password
├── application
│   ├── secret_key
│   └── api_token_pepper
├── admin
│   ├── username
│   └── password
└── api
    └── token
```

Pri deploymente:

```text
secret existuje?
    ├── áno → použiť existujúci
    └── nie → vygenerovať a uložiť do OpenBao
```

Tým sa pri opakovanom deploymente neregenerujú databázové heslá ani NetBox `SECRET_KEY`.


---

# NetBox Docker Compose secrets

Generovaný:

```text
.runtime/netbox-docker/docker-compose.override.yml
```

neobsahuje plaintext hodnoty NetBox secrets.

Používa environment placeholders a deployment proces doplní hodnoty z OpenBao do prostredia Docker Compose procesu.

Beta obmedzenie: secrets sú následne dostupné ako container environment variables. Pre budúci production hardening je možné zvážiť OpenBao Agent/file-based runtime secret delivery.

---

# NetBox deployment konfigurácia

Hlavná konfigurácia NetBox deploymentu:

```text
deployment/netbox.yaml
```

Aktuálna konfigurácia:

```yaml
netbox:
  version: "4.7.1"
  docker_image: "docker.io/netboxcommunity/netbox:v4.7.1-5.1.1"

netbox_docker:
  repository: "https://github.com/netbox-community/netbox-docker.git"
  ref: "5.1.1"

runtime:
  directory: ".runtime/netbox-docker"
  compose_project: "atlas-netbox"

network:
  listen_address: "0.0.0.0"
  port: 8000

bootstrap:
  superuser_name: "admin"
```

Používa sa konkrétna verzia Docker image namiesto pohyblivého `latest` tagu.

Cieľom je reprodukovateľnosť rovnakého Atlas release.

---

# Izolácia Docker Compose projektu

NetBox používa explicitný Compose project:

```text
atlas-netbox
```

Príklad kontajnerov:

```text
atlas-netbox-netbox-1
atlas-netbox-netbox-worker-1
atlas-netbox-postgres-1
atlas-netbox-redis-1
atlas-netbox-redis-cache-1
```

Príklad volumes:

```text
atlas-netbox_netbox-postgres
atlas-netbox_netbox-redis-data
atlas-netbox_netbox-redis-cache-data
atlas-netbox_netbox-media-files
atlas-netbox_netbox-reports-files
atlas-netbox_netbox-scripts-files
```

Tým sa Atlas stack oddeľuje od prípadných iných NetBox inštancií na rovnakom Docker hoste.

---

# Prepare-only režim

Atlas orchestrátor podporuje:

```bash
python scripts/deploy_atlas.py --prepare-only
```

OpenBao sa spustí a overí. NetBox, Oxidized a Nginx pripravia runtime, Compose konfiguráciu a potrebné secrets bez štartu svojich kontajnerov. Aj tento režim vyžaduje platnú zákaznícku proxy konfiguráciu a certifikáty.

Samostatne je možné použiť aj:

```bash
python scripts/deploy_netbox.py --prepare-only
```

---

# Verbose režim

Detailnejší výstup NetBox bootstrapu:

```bash
python scripts/deploy_atlas.py --verbose
```

alebo priamo:

```bash
python scripts/deploy_netbox.py --verbose
```

---

# Opakované spustenie

Deployment je navrhnutý ako idempotentný.

```bash
python scripts/deploy_atlas.py
```

Pri existujúcom deploymente má:

- znovu použiť existujúci OpenBao Raft storage,
- vyžiadať unseal shares iba ak je OpenBao sealed,
- overiť AppRole identities,
- ponechať existujúce secrets,
- znovu použiť NetBox volumes,
- overiť existujúci NetBox API token,
- zosúladiť Atlas baseline,
- zosúladiť Device Catalog.

Secrets sa pri bežnom opakovanom deploymente neregenerujú.

---

# Správa NetBox stacku

Pre prácu priamo s runtime:

```bash
cd .runtime/netbox-docker
```

Stav:

```bash
docker compose -p atlas-netbox ps
```

Zastavenie:

```bash
docker compose -p atlas-netbox stop
```

Spustenie:

```bash
docker compose -p atlas-netbox start
```

Reštart:

```bash
docker compose -p atlas-netbox restart
```

Odstránenie kontajnerov bez zmazania volumes:

```bash
docker compose -p atlas-netbox down
```

Následný deployment:

```bash
cd ~/aricoma-atlas
python scripts/deploy_atlas.py
```

---

# Pozor na persistentné dáta

Pri bežnej správe deploymentu nepoužívaj bez jasného dôvodu:

```bash
docker compose -p atlas-netbox down -v
```

`-v` odstráni named volumes, vrátane PostgreSQL dát.

Rovnako opatrne používaj:

```bash
docker volume prune
docker system prune --volumes
```

OpenBao Raft volume je rovnako kritický persistentný stav a jeho odstránenie znamená stratu OpenBao databázy secrets a konfigurácie.

---

# Atlas NetBox baseline

Atlas baseline predstavuje konfiguráciu, ktorá má existovať na každej Atlas NetBox inštancii bez ohľadu na konkrétneho zákazníka.

Aktuálne obsahuje:

- 1× Custom Field Choice Set,
- 4× Custom Fields.

Konfigurácia baseline je deklaratívne uložená v:

```text
catalog/baseline/
```

## Custom Field Choice Set

### Profylaxia checks

Aktuálne možnosti:

- CPU utilization
- Memory utilization
- Interface utilization
- interface errors
- Routing table
- Routing protocol neighbors
- HA status
- Temperature

Hodnoty sa zámerne zachovávajú presne v deklarovanej forme.

Bootstrap ich automaticky nenormalizuje ani neprepisuje.

## Custom Fields

| Name | Type | Group |
|---|---|---|
| `checkmk_enabled` | Boolean | Monitoring |
| `oxidized_enabled` | Boolean | backuping |
| `profylaxia_enabled` | Boolean | Profylaxia |
| `profylaxia_checks` | Multiple selection | Profylaxia |

Všetky aktuálne Custom Fields sú priradené k objektu:

```text
dcim.device
```

Baseline provisioner kontroluje aj configuration drift.

Ak deklarácia a existujúca konfigurácia NetBoxu nesedia, provisioning existujúce Custom Fieldy automaticky neprepíše. Namiesto toho oznámi drift.

---

# Atlas Device Catalog

Atlas Device Catalog sa generuje z upstream projektu:

```text
https://github.com/netbox-community/devicetype-library.git
```

Presná verzia upstream repozitára je definovaná v:

```text
catalog/manifest.yaml
```

Aktuálny upstream commit:

```text
517549215455824be5e3b89965353fd2210cc20c
```

Manifest obsahuje:

- URL upstream repozitára,
- presný Git commit,
- testovanú verziu NetBoxu,
- zoznam výrobcov,
- pravidlá výberu Device Types.

Aktuálny katalóg:

```text
Cisco       238
Fortinet    162
Juniper     290
----------------
Spolu       690
```

---

# Cisco selection

Cisco Device Types sa neimportujú všetky.

Atlas manifest aktuálne vyberá najmä tieto produktové rady:

```text
CBS*
SF*
SG*
SX*
RV*

WS-C2960X-*
WS-C2960XR-*
WS-C3650-*
WS-C3850-*

C9200-*
C9200L-*
C9200CX-*
Catalyst-9200CX-*

C9300-*
C9300L-*
C9300LM-*
C9300X-*

C9500-*
C9500X-*
Catalyst-9500X-*

ISR4*
C8200-*
C8300-*
```

Fortinet a Juniper sa v aktuálnej beta verzii vyberajú celé.

---

# Prečo používame pinned commit

Atlas katalóg nesmie závisieť od aktuálneho stavu upstream repozitára.

Konkrétny Atlas release musí vždy vytvoriť rovnaký katalóg.

Bootstrap kontroluje:

```text
manifest
   │
   ├── repository
   ├── commit
   └── selection rules
   │
   ▼
local Device Type Library
   │
   ▼
generated Atlas catalog
```

Ak lokálny upstream checkout alebo build nezodpovedá manifestu, katalóg sa považuje za stale a znovu sa pripraví.

---

# Build Atlas Device Catalog

Samostatný build:

```bash
python scripts/build_catalog.py
```

Výstup sa generuje do:

```text
build/
```

Napríklad:

```text
build/
├── catalog-report.json
└── device-types/
```

`build/` nie je súčasťou Git repozitára.

---

# Architektúra NetBox bootstrapu

```text
NetBox Community Device Type Library
                │
                │ pinned Git commit
                ▼
        catalog/manifest.yaml
                │
                ▼
        build_catalog.py
                │
                ▼
       Atlas Device Catalog
          690 Device Types
                │
         ┌──────┴──────┐
         │             │
         ▼             ▼
 Atlas baseline   Device Catalog
         │             │
         └──────┬──────┘
                ▼
       bootstrap_netbox.py
                │
                ▼
              NetBox
```

V kompletnom Atlas deploymente je táto vrstva spúšťaná cez:

```text
deploy_atlas.py
    │
    └── deploy_netbox.py
            │
            └── bootstrap_netbox.py
```

---

# `deploy_atlas.py`

Hlavný beta deployment entrypoint.

Použitie:

```bash
python scripts/deploy_atlas.py
```

Zodpovedá za správne poradie:

```text
OpenBao
  ↓
readiness gate
  ↓
NetBox
  ↓
Oxidized
  ↓
Nginx
```

---

# `deploy_openbao.py`

Samostatný OpenBao deployment/bootstrap entrypoint.

```bash
python scripts/deploy_openbao.py
```

Zabezpečuje:

- Docker preflight,
- štart OpenBao,
- init 5/3,
- interaktívny unseal,
- Raft readiness,
- KV v2 mount,
- policies,
- AppRoles,
- machine identities,
- initial root revocation.

---

# `deploy_netbox.py`

Samostatný NetBox deployment entrypoint.

Vyžaduje pripravený a unsealed OpenBao.

```bash
python scripts/deploy_netbox.py
```

Zabezpečuje:

- Docker preflight,
- pinned `netbox-docker`,
- NetBox secrets z OpenBao,
- Docker Compose konfiguráciu,
- PostgreSQL,
- Valkey,
- NetBox,
- healthcheck,
- API token,
- Atlas bootstrap.

---

# `deploy_oxidized.py`

```bash
python scripts/deploy_oxidized.py
python scripts/deploy_oxidized.py --prepare-only
```

Pripravuje pripnutý kontajner podľa `deployment/oxidized.yaml`, internú sieť `atlas-oxidized-web` a named volume `atlas-oxidized-data`. Volume obsahuje Git históriu a zapisovateľný runtime; opakovaný deploy ho nemaže. Zálohuj ho mimo VM spolu s OpenBao a NetBox dátami. Nepoužívaj `down -v` pri bežnom upgrade.

Prvé nasadenie vytvorí prázdny `.runtime/oxidized/router.db`. Existujúci inventár a `.runtime/oxidized/config` sa zachovávajú. Deployment týmto krokom **nezálohuje žiadne reálne zariadenie** a neimplementuje synchronizáciu NetBox → Oxidized.

Budúca Atlas aplikácia bude čítať schválené zariadenia, management IP a platformy z NetBoxu, prekladať platformu na Oxidized model a získavať zariadeniové credentials z OpenBao. Oxidized dostane pripravený zdroj cez interné API. Tieto účty sa odlišujú od Basic Auth účtu webového rozhrania. SSH overovanie host kľúčov je zapnuté; pred zapojením zariadení treba pripraviť dôveryhodné host kľúče a otestovať jeden reálny backup.

# `deploy_proxy.py`

```bash
python scripts/deploy_proxy.py
python scripts/deploy_proxy.py --prepare-only
```

Vyžaduje zákaznícku proxy konfiguráciu a pripravené OpenBao. Vygeneruje/použije secret `atlas/oxidized/web`, vytvorí Nginx runtime a overí konfiguráciu pred štartom. Proxy je pripojený na webové siete oboch backendov, nie na NetBox databázovú sieť. Pri redeployi sa proxy kontajner obnoví, aby načítal certifikáty a hash; počas tohto kroku môže krátko vypadnúť webový prístup.

Obnova certifikátu: obnov zdrojové PEM súbory na cestách uvedených v `.runtime/proxy.yaml` a spusti `deploy_proxy.py`. Rotácia webového hesla: oprávnený správca aktualizuje `password` na `atlas/oxidized/web` (minimálne 20 znakov), potom spustí `deploy_proxy.py`. Automatické vydávanie/obnova certifikátov nie sú implementované.

# `bootstrap_netbox.py`

Použi, keď NetBox už existuje a chceš iba aplikovať Atlas konfiguráciu.

Dry-run:

```bash
python scripts/bootstrap_netbox.py
```

Apply:

```bash
python scripts/bootstrap_netbox.py --apply
```

Pri `--apply` sa vykoná:

```text
prepare / validate catalog
        │
        ▼
Atlas baseline
        │
        ▼
Atlas Device Catalog
        │
        ▼
Final verification
```

---

# Manuálny bootstrap existujúceho NetBoxu

Nastav:

```bash
export NETBOX_URL="http://localhost:8000"
export NETBOX_TOKEN="<API_TOKEN>"
```

Dry-run:

```bash
python scripts/bootstrap_netbox.py
```

Apply:

```bash
python scripts/bootstrap_netbox.py --apply
```

Pri štandardnom `deploy_atlas.py` sa NetBox API token automaticky vytvára, validuje a ukladá do OpenBao na:

```text
atlas/netbox/api
```

---

# Idempotencia

Provisioning a deployment musia byť bezpečne opakovateľné.

Ak objekt alebo secret už existuje v požadovanom stave, nemá sa vytvárať znovu.

Príklad katalógu:

```text
Device Types
Existing: 690
Missing:  0
```

Component templates:

```text
TOTAL COMPONENTS
catalog=26342
existing=26342
missing=0
```

Baseline:

```text
Choice Sets:
  existing: 1
  missing:  0
  drift:    0

Custom Fields:
  existing: 4
  missing:  0
  drift:    0
```

---

# Configuration drift

Baseline provisioner rozlišuje:

```text
existing
missing
drift
```

Ak objekt chýba, môže ho `--apply` vytvoriť.

Ak objekt existuje, ale jeho konfigurácia sa líši od Atlas deklarácie, bootstrap ho automaticky neprepíše. Namiesto toho oznámi drift.

Cieľom je zabrániť nechcenej modifikácii existujúcej zákazníckej konfigurácie.

---

# Rebuild Atlas Device Catalog

Explicitný rebuild:

```bash
python scripts/bootstrap_netbox.py --rebuild-catalog
```

alebo:

```bash
python scripts/build_catalog.py
```

Bootstrap kontroluje, či existujúci build zodpovedá:

- manifestu,
- upstream repozitáru,
- upstream commitu,
- vendor selection pravidlám,
- počtu generovaných Device Types.

---

# Audit katalógu

Základný audit:

```bash
python scripts/audit_catalog.py
```

Kontroluje napríklad:

- počet Device Types,
- počty podľa výrobcov,
- podporované component templates,
- dependencies,
- neznáme top-level polia.

Aktuálne overený stav:

```text
Device Types: 690
```

Component coverage:

```text
console-ports          860
power-ports            331
interfaces           23456
rear-ports               2
module-bays            1687
device-bays               2
power-outlets             4
```

---

# Audit polí component templates

```bash
python scripts/audit_component_fields.py
```

Používa sa pri vývoji provisionera na kontrolu polí, ktoré sa reálne nachádzajú v Device Type Library.

Provisioner používa strict validáciu neznámych polí, aby sa nové upstream dáta nezačali potichu ignorovať.

---

# Bezpečnosť a Git

Do Git repozitára sa nesmú ukladať:

- unseal keys,
- root tokeny,
- AppRole SecretID,
- NetBox API tokeny,
- používateľské heslá,
- PostgreSQL heslá,
- Redis/Valkey heslá,
- private keys,
- `.env` súbory s credentials,
- `.runtime/`,
- OpenBao Raft data.

Pred commitom:

```bash
git status
git diff --cached
```

Odporúčaná kontrola názvov citlivých súborov:

```bash
git ls-files | grep -E '(\.runtime|openbao-.*\.json|\.env|secret|password|token)'
```

Výsledky treba manuálne vyhodnotiť – názvy `secret`, `password` alebo `token` sa môžu legitímne nachádzať v source code a dokumentácii, ale nie ako reálne credentials.

---

# Development workflow

Odporúčaný postup:

```text
upraviť konfiguráciu / manifest / baseline / skripty
        │
        ▼
lokálna validácia
        │
        ▼
fresh test alebo dry-run
        │
        ▼
apply na test prostredí
        │
        ▼
opakovaný run / idempotencia
        │
        ▼
git diff
        │
        ▼
commit
        │
        ▼
push
        │
        ▼
GitHub Actions
```

Pred commitom:

```bash
python -m py_compile scripts/*.py atlas/*.py
```

Stav repozitára:

```bash
git status
```

Kontrola staged zmien:

```bash
git diff --cached --check
git diff --cached --stat
```

---

# GitHub Actions

Repozitár obsahuje:

```text
.github/workflows/validate.yml
```

CI sa spúšťa pri pushi a pull requeste.

Aktuálna validácia môže zahŕňať:

- Python syntax,
- YAML konfiguráciu,
- Atlas manifest,
- baseline deklarácie,
- pinned Device Type Library checkout,
- build Atlas Device Catalogu,
- audit katalógu,
- audit component fields.

CI nesmie vyžadovať produkčné secrets.

Nové ingress testy pokrývajú:

- validáciu zákazníckej konfigurácie, certifikátu, názvu a privátneho kľúča,
- zachovanie hesla pri redeployi a zastavenie pri chybách OpenBao,
- HTTPS s dočasnou CI CA, bez vypínania certificate verification,
- odmietnutie chýbajúceho/zlého hesla pre Oxidized web/API,
- skutočný NetBox login POST cez HTTPS vrátane CSRF,
- neprítomnosť publikovaných portov Oxidized,
- zachovanie Oxidized Git commit-u a credentials pri opakovanom deployi,
- upgrade pôvodného Atlasu na verziu s proxy/Oxidized a zachovanie NetBox dát.

`scripts/ci_ingress.py` vytvára certifikáty iba pre izolovaný CI runner. Jeho DNS mená, širšie lokálne CIDR rozsahy a krátka platnosť certifikátov sa nepoužívajú pri zákazníckom nasadení. Lokálne testy bez Dockeru:

```bash
python -m unittest discover -s tests -v
```

---

# Versioning

Projekt používa Git tagy na release verzie.

Prvá beta verzia:

```text
v0.1.0-beta
```

Release má reprezentovať konkrétnu kombináciu:

```text
Atlas deployment code
        +
OpenBao configuration
        +
OpenBao policies
        +
Atlas baseline
        +
Atlas Device Catalog manifest
        +
pinned Device Type Library
        +
pinned NetBox Docker
        +
pinned NetBox image
```

Cieľom je reprodukovateľnosť konkrétneho release.

---

# Aktuálny beta scope

Aktuálna `0.1.0-beta` zahŕňa:

- one-command `deploy_atlas.py`,
- OpenBao deployment,
- OpenBao Raft storage,
- Shamir 5/3 unseal,
- initial root revocation,
- OpenBao KV v2 `atlas/`,
- OpenBao audit logging,
- `atlas-deployer` AppRole,
- `atlas-operator` AppRole,
- `netbox-runtime` AppRole,
- `checkmk-runtime` AppRole,
- `oxidized-runtime` AppRole,
- shared `device-credentials-read` policy,
- NetBox `4.7.1`,
- NetBox Docker `5.1.1`,
- PostgreSQL + Valkey,
- OpenBao-backed NetBox secrets,
- Oxidized deployment s persistentnou Git históriou a prázdnym počiatočným inventárom,
- Nginx HTTPS proxy s internou CA a CIDR allowlistom,
- OpenBao-backed Basic Auth pre Oxidized web/API,
- automatický NetBox superuser,
- automatický NetBox API token,
- Atlas baseline,
- 1 Custom Field Choice Set,
- 4 Custom Fields,
- Cisco Device Types,
- Fortinet Device Types,
- Juniper Device Types,
- 690 Device Types,
- 26 342 component templates,
- idempotentný provisioning,
- configuration drift detection,
- final verification.

---

# Pred produkciou

Pred označením Atlas deploymentu ako produkčného je potrebné minimálne doriešiť:

- TLS pre OpenBao,
- CA trust a certificate verification,
- lifecycle certifikátov a overenie proxy/firewall izolácie na zákazníckej VM,
- produkčný audit-log destination a rotáciu,
- backup/restore OpenBao Raft storage,
- backup/restore NetBox PostgreSQL,
- deployment test na cieľovej Linux VM,
- dependency/version pinning pre produkčný Python runtime,
- hardening Docker hosta a filesystem permissions.

Auto-unseal nie je súčasťou beta verzie. Aktuálny recovery model zámerne používa manuálny 3-of-5 unseal.

---

# Ďalší plánovaný rozvoj

Ďalšie fázy projektu môžu zahŕňať:

- Checkmk deployment a provisioning,
- automatické napĺňanie Oxidized inventára z NetBoxu cez Atlas aplikáciu,
- napojenie oboch služieb na spoločné device credentials v OpenBao,
- Atlas aplikačnú vrstvu,
- individuálne prístupy/SSO pre proxy,
- centrálne logging/monitoring,
- zákaznícke deployment profily,
- produkčný TLS hardening.

---

# Cieľ projektu

Dlhodobý cieľ:

```text
nový Linux server
        │
        ▼
Git clone aricoma-atlas
        │
        ▼
python scripts/deploy_atlas.py
        │
        ├── OpenBao
        │     └── secrets / identities / audit
        │
        ├── NetBox
        │     ├── Atlas baseline
        │     └── Device Catalog
        │
        ├── Checkmk
        ├── Oxidized
        ├── Atlas application
        └── integrations
```

Aktuálna beta verzia už pokrýva základ tejto architektúry:

```text
OpenBao
   +
NetBox deployment
   +
Atlas NetBox bootstrap
   +
Oxidized deployment a Git história
   +
Nginx HTTPS a chránený Oxidized web/API
   +
pripravené runtime identity pre ďalšie služby
```
