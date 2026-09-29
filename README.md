# Aricoma Atlas – NetBox Bootstrap

Nástroje na automatizované nasadenie a provisioning NetBox prostredia pre projekt **Aricoma Atlas**.

Cieľom projektu je umožniť pripraviť novú čistú NetBox inštanciu do štandardného Atlas stavu reprodukovateľným a idempotentným spôsobom.

Projekt aktuálne zabezpečuje:

- automatizované nasadenie NetBoxu pomocou Docker Compose,
- PostgreSQL a Valkey backend služby,
- automatické vytvorenie NetBox superusera,
- automatické provisionovanie NetBox API tokenu,
- Atlas Custom Field Choice Sets,
- Atlas Custom Fields,
- výrobcov sieťových zariadení,
- Device Types,
- component templates jednotlivých Device Types,
- validáciu konfigurácie,
- kontrolu výsledného stavu po provisioningu.

Hlavný deployment workflow je možné spustiť jedným príkazom:

```bash
python scripts/deploy_netbox.py
```

Device Type katalóg sa generuje z upstream projektu **NetBox Community Device Type Library** na základe presne definovaného Git commitu a Atlas selection manifestu.

---

# Aktuálna verzia

**Atlas Device Catalog:** `0.1.0-beta`

Testované prostredie:

- NetBox `4.7.1`
- NetBox Docker `5.1.1`
- Docker image `docker.io/netboxcommunity/netbox:v4.7.1-5.1.1`

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

# Štruktúra repozitára

```text
atlas-netbox-bootstrap/
├── .github/
│   └── workflows/
│       └── validate.yml
│
├── catalog/
│   ├── manifest.yaml
│   └── baseline/
│       ├── custom_field_choice_sets.yaml
│       └── custom_fields.yaml
│
├── deployment/
│   └── netbox.yaml
│
├── scripts/
│   ├── deploy_netbox.py
│   ├── bootstrap_netbox.py
│   ├── build_catalog.py
│   ├── provision_baseline.py
│   ├── provision_netbox.py
│   ├── audit_catalog.py
│   └── audit_component_fields.py
│
├── requirements.txt
├── .gitignore
└── README.md
```

Lokálne, generované a runtime dáta sa zámerne neukladajú do Git repozitára:

```text
.venv/
build/
devicetype-library/
.runtime/
```

Adresár `.runtime/` obsahuje lokálny runtime deploymentu, napríklad:

```text
.runtime/
├── atlas-netbox-state.json
└── netbox-docker/
```

Súbor:

```text
.runtime/atlas-netbox-state.json
```

obsahuje lokálne generované credentials a NetBox API token.

Tento súbor musí zostať mimo Git repozitára.

---

# One-command NetBox deployment

Pre novú NetBox inštanciu je hlavný vstupný bod:

```bash
python scripts/deploy_netbox.py
```

Deployment vykoná celý proces:

```text
Docker preflight
        │
        ▼
pinned netbox-docker checkout
        │
        ▼
runtime secrets
        │
        ▼
PostgreSQL + Valkey
        │
        ▼
NetBox 4.7.1
        │
        ▼
NetBox healthcheck
        │
        ▼
superuser
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
final verification
        │
        ▼
ATLAS NETBOX READY
```

Aktuálne testovaná kombinácia:

```text
NetBox:          4.7.1
NetBox Docker:   5.1.1
Docker image:    v4.7.1-5.1.1
Compose project: atlas-netbox
```

---

# Deployment konfigurácia

Hlavná konfigurácia Docker deploymentu sa nachádza v:

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

Konfigurácia definuje:

- verziu NetBoxu,
- Docker image,
- upstream `netbox-docker` repository,
- pinned upstream ref,
- runtime adresár,
- Docker Compose project name,
- listen address,
- TCP port,
- meno NetBox superusera.

Používa sa konkrétna verzia Docker image namiesto pohyblivého `latest` tagu.

Cieľom je, aby rovnaký Atlas release používal vždy rovnakú kombináciu NetBoxu a NetBox Docker support files.

--

# Izolácia Docker Compose projektu

Atlas deployment používa explicitný Docker Compose project:

```text
atlas-netbox
```

To je dôležité najmä v prípade, že na rovnakom Docker hoste existuje iná NetBox inštancia.

Bez explicitného Compose project name by mohlo dôjsť ku kolízii:

- kontajnerov,
- networks,
- named volumes,
- konfigurácie existujúceho NetBox stacku.

Atlas resources preto používajú vlastný namespace.

Príklad kontajnerov:

```text
atlas-netbox-netbox-1
atlas-netbox-netbox-worker-1
atlas-netbox-postgres-1
atlas-netbox-redis-1
atlas-netbox-redis-cache-1
```

Príklad persistentných volumes:

```text
atlas-netbox_netbox-postgres
atlas-netbox_netbox-redis-data
atlas-netbox_netbox-redis-cache-data
atlas-netbox_netbox-media-files
atlas-netbox_netbox-reports-files
atlas-netbox_netbox-scripts-files
```

---

# Runtime secrets

Pri prvom deploymente sa automaticky generujú lokálne credentials.

Aktuálne sa generujú hodnoty pre:

- PostgreSQL password,
- Valkey password,
- Valkey cache password,
- NetBox `SECRET_KEY`,
- API token pepper,
- NetBox superuser password,
- NetBox API token.

Runtime stav sa uchováva v:

```text
.runtime/atlas-netbox-state.json
```

Súbor má byť dostupný iba lokálne a je ignorovaný cez `.gitignore`.

## Dôležité

Runtime credentials ani API token sa nesmú commitovať do Git repozitára.

Do Git repozitára nepatrí:

```text
.runtime/
```

---

# Požiadavky na deployment

Host musí mať minimálne:

- Git,
- Python,
- Python `venv`,
- Docker,
- Docker Compose plugin.

Overenie Dockeru:

```bash
docker --version
docker compose version
docker info
```

Python dependencies:

```bash
python -m pip install -r requirements.txt
```

Odporúčané je používať Python virtual environment.

---

# Python virtual environment

Vytvorenie:

```bash
python3 -m venv .venv
```

Aktivácia na macOS/Linux:

```bash
source .venv/bin/activate
```

Inštalácia dependencies:

```bash
pip install -r requirements.txt
```

Overenie:

```bash
python --version
```

---

# Spustenie deploymentu

Po aktivovaní virtual environment:

```bash
python scripts/deploy_netbox.py
```

Skript:

1. overí Docker,
2. overí Docker Compose,
3. pripraví pinned `netbox-docker`,
4. vygeneruje alebo načíta runtime secrets,
5. vytvorí Docker Compose override,
6. stiahne potrebné Docker images,
7. spustí NetBox stack,
8. počká na healthy NetBox,
9. overí alebo vytvorí API token,
10. spustí Atlas bootstrap,
11. vykoná finálnu kontrolu.

Pri úspešnom deploymente sa zobrazí napríklad:

```text
NETBOX DOCKER READY
==============================================================================
URL:      http://127.0.0.1:8000
Username: admin
```

---

# Prepare-only režim

Runtime je možné pripraviť bez spustenia Docker kontajnerov:

```bash
python scripts/deploy_netbox.py --prepare-only
```

Tento režim:

- overí Docker,
- naklonuje alebo aktualizuje pinned `netbox-docker`,
- overí požadovaný Git ref,
- pripraví runtime secrets,
- vytvorí Docker Compose override,
- nespustí kontajnery.

Použitie je vhodné napríklad pri kontrole deployment konfigurácie.

---

# Verbose režim

Štandardný deployment zámerne zobrazuje iba stručný priebeh.

Pre detailný výstup Atlas bootstrapu:

```bash
python scripts/deploy_netbox.py --verbose
```

Verbose režim je vhodný pri:

- troubleshootingu,
- vývoji,
- kontrole provisioningu,
- analýze chýb.

---

# Opakované spustenie deploymentu

Deployment je navrhnutý ako idempotentný.

Opakované spustenie:

```bash
python scripts/deploy_netbox.py
```

má:

- znovu overiť Docker prostredie,
- overiť pinned upstream,
- ponechať existujúce secrets,
- znovu použiť existujúce Docker volumes,
- overiť existujúci API token,
- zosúladiť Atlas baseline,
- zosúladiť Device Catalog,
- vykonať finálnu kontrolu.

Ak už všetko existuje, provisioning nemá vytvárať duplicity.

---

# Správa Docker stacku

Pre prácu priamo s Compose runtime:

```bash
cd .runtime/netbox-docker
```

## Stav

```bash
docker compose -p atlas-netbox ps
```

## Zastavenie

```bash
docker compose -p atlas-netbox stop
```

## Opätovné spustenie

```bash
docker compose -p atlas-netbox start
```

## Reštart

```bash
docker compose -p atlas-netbox restart
```

## Odstránenie kontajnerov bez zmazania dát

```bash
docker compose -p atlas-netbox down
```

Následne je možné stack znovu vytvoriť:

```bash
cd ~/atlas-netbox-bootstrap
python scripts/deploy_netbox.py
```

---

# Pozor na persistentné dáta

Pri bežnej správe deploymentu nepoužívaj:

```bash
docker compose -p atlas-netbox down -v
```

Parameter:

```text
-v
```

odstráni aj named volumes.

To môže znamenať odstránenie PostgreSQL databázy NetBoxu.

Rovnako je potrebné dávať pozor na:

```bash
docker volume prune
docker system prune --volumes
```

Pred zásahom do volumes je potrebné vedieť, ktoré persistentné dáta sa odstránia.

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

---

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

---

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

Ak deklarácia a existujúca konfigurácia NetBoxu nesedia, provisioning existujúce Custom Fieldy automaticky neprepíše.

Namiesto toho oznámi drift.

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

Aktuálny katalóg obsahuje:

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

Napríklad:

```text
Atlas Device Catalog 0.1.0-beta
```

musí vždy vytvoriť rovnaký katalóg.

Preto je upstream commit explicitne definovaný v:

```text
catalog/manifest.yaml
```

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

Tým zabezpečujeme reprodukovateľnosť jednotlivých Atlas release verzií.

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

Adresár:

```text
build/
```

nie je súčasťou Git repozitára.

Je to generovaný artefakt.

---

# Architektúra bootstrapu

Device Catalog časť funguje nasledovne:

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
                │
        ┌───────┴────────┐
        │                │
        ▼                ▼
 Atlas baseline     Device Catalog
        │                │
        └───────┬────────┘
                ▼
       bootstrap_netbox.py
                │
                ▼
             NetBox
```

Pri kompletnom Docker deploymente je nad touto vrstvou ešte:

```text
deploy_netbox.py
        │
        ├── Docker
        ├── PostgreSQL
        ├── Valkey
        ├── NetBox
        ├── API token
        │
        ▼
bootstrap_netbox.py
        │
        ▼
Atlas configuration
```

---

# `deploy_netbox.py` vs `bootstrap_netbox.py`

Tieto skripty majú rozdielnu úlohu.

## `deploy_netbox.py`

Použi, keď chceš pripraviť celý NetBox stack.

Zabezpečuje:

```text
Docker
  +
NetBox
  +
PostgreSQL
  +
Valkey
  +
superuser
  +
API token
  +
Atlas provisioning
```

Spustenie:

```bash
python scripts/deploy_netbox.py
```

---

## `bootstrap_netbox.py`

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

Kompletný remote preflight sa pred `--apply` zámerne neopakuje.

Jednotlivé provisionery vykonávajú vlastnú validáciu pred zápisom a po provisioningu nasleduje finálna kontrola.

---

# Dry-run existujúceho NetBoxu

Pre kontrolu existujúceho NetBoxu nastav:

```bash
export NETBOX_URL="http://localhost:8000"
export NETBOX_TOKEN="<API_TOKEN>"
```

Potom:

```bash
python scripts/bootstrap_netbox.py
```

Dry-run:

- nemení NetBox,
- kontroluje Atlas baseline,
- kontroluje Device Catalog,
- identifikuje missing objekty,
- identifikuje baseline drift,
- overuje katalóg.

---

# Apply na existujúci NetBox

Po úspešnom dry-rune:

```bash
python scripts/bootstrap_netbox.py --apply
```

Bootstrap následne:

1. pripraví Atlas Device Catalog,
2. aplikuje Atlas baseline,
3. aplikuje Device Catalog,
4. vykoná finálnu verifikáciu.

---

# Prístup k NetBox API

Pri manuálnom bootstrapovaní existujúceho NetBoxu sa používajú environment variables:

```bash
export NETBOX_URL="http://localhost:8000"
export NETBOX_TOKEN="<TOKEN>"
```

Bootstrap token neukladá do Git repozitára.

Pri `deploy_netbox.py` sa API token vytvorí automaticky a uloží sa do:

```text
.runtime/atlas-netbox-state.json
```

---

# Bezpečnosť

Do Git repozitára sa nesmú ukladať:

- NetBox API tokeny,
- používateľské heslá,
- PostgreSQL heslá,
- Redis/Valkey heslá,
- private keys,
- `.env` súbory s credentials,
- `.runtime/`.

Secrets musia zostať mimo Git repozitára.

Pred commitom je vhodné overiť:

```bash
git status
git diff --cached
```

---

# Idempotencia

Provisioning musí byť možné spustiť opakovane.

Ak objekt už existuje v požadovanom stave, bootstrap ho nevytvára znovu.

Príklad:

```text
Device Types
Existing: 690
Missing:  0
```

a:

```text
TOTAL COMPONENTS
catalog=26342
existing=26342
missing=0
```

znamená, že Device Catalog je už provisioned.

Rovnako pri baseline:

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

Ak objekt chýba:

```text
missing
```

je možné ho vytvoriť cez `--apply`.

Ak objekt existuje, ale jeho konfigurácia sa líši od Atlas deklarácie:

```text
drift
```

bootstrap ho automaticky neprepíše.

Dôvodom je ochrana existujúcej NetBox konfigurácie pred nechcenou modifikáciou.

---

# Rebuild Atlas Device Catalog

Katalóg je možné explicitne pregenerovať cez:

```bash
python scripts/bootstrap_netbox.py --rebuild-catalog
```

alebo samostatne:

```bash
python scripts/build_catalog.py
```

Bootstrap zároveň kontroluje, či existujúci build zodpovedá:

- manifestu,
- upstream repozitáru,
- upstream commitu,
- vendor selection pravidlám,
- počtu generovaných Device Types.

Ak nie, build sa označí ako stale.

---

# Audit katalógu

Základný audit:

```bash
python scripts/audit_catalog.py
```

Audit kontroluje napríklad:

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

Detailný audit:

```bash
python scripts/audit_component_fields.py
```

Používa sa pri vývoji provisionera na kontrolu, aké polia sa reálne nachádzajú v Device Type Library.

Napríklad interfaces aktuálne používajú aj:

```text
description
enabled
label
mgmt_only
poe_mode
poe_type
```

Provisioner má strict validáciu neznámych polí, aby sa nové upstream dáta nezačali potichu ignorovať.

---

# Jednotlivé skripty

## `deploy_netbox.py`

Hlavný deployment entrypoint.

Zabezpečuje:

- Docker preflight,
- prípravu pinned `netbox-docker`,
- runtime secrets,
- Docker Compose konfiguráciu,
- spustenie NetBox stacku,
- healthcheck,
- superusera,
- API token,
- Atlas bootstrap.

Bežné použitie:

```bash
python scripts/deploy_netbox.py
```

---

## `bootstrap_netbox.py`

Hlavný entrypoint pre provisioning existujúcej NetBox inštancie.

Dry-run:

```bash
python scripts/bootstrap_netbox.py
```

Apply:

```bash
python scripts/bootstrap_netbox.py --apply
```

---

## `build_catalog.py`

Generuje Atlas Device Catalog z pinned Device Type Library checkoutu.

```bash
python scripts/build_catalog.py
```

---

## `provision_baseline.py`

Provisionuje:

- Custom Field Choice Sets,
- Custom Fields.

Dry-run:

```bash
python scripts/provision_baseline.py
```

Apply:

```bash
python scripts/provision_baseline.py --apply
```

---

## `provision_netbox.py`

Provisionuje:

- Manufacturers,
- Device Types,
- component templates.

Dry-run:

```bash
python scripts/provision_netbox.py --all
```

Apply:

```bash
python scripts/provision_netbox.py --apply --all
```

Podporuje aj konkrétny Device Type.

---

## `audit_catalog.py`

Kontroluje štruktúru a obsah Device Catalogu.

```bash
python scripts/audit_catalog.py
```

---

## `audit_component_fields.py`

Analyzuje polia používané jednotlivými component templates.

```bash
python scripts/audit_component_fields.py
```

---

# Aktuálne podporované Device Type komponenty

Provisioner aktuálne podporuje:

- Console Port Templates,
- Console Server Port Templates,
- Power Port Templates,
- Power Outlet Templates,
- Interface Templates,
- Rear Port Templates,
- Front Port Templates,
- Module Bay Templates,
- Device Bay Templates.

Provisioner podporuje aj dependencies:

```text
Power Outlet
    ↓
Power Port
```

a:

```text
Front Port
    ↓
Rear Port
```

Aktuálny Atlas katalóg používa Power Outlet → Power Port dependency.

---

# Development workflow

Odporúčaný postup pri zmene projektu:

```text
upraviť manifest / baseline / skripty
        │
        ▼
lokálna validácia
        │
        ▼
dry-run
        │
        ▼
apply na test NetBox
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
python -m py_compile scripts/*.py
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

Kontroluje najmä:

- Python syntax,
- YAML konfiguráciu,
- Atlas manifest,
- baseline deklarácie,
- pinned Device Type Library checkout,
- build Atlas Device Catalogu,
- audit katalógu,
- audit component fields.

CI nepotrebuje prístup k produkčnému NetBoxu ani NetBox API token.

---

# Versioning

Projekt používa Git tagy na označenie release verzií.

Prvá plánovaná beta verzia:

```text
v0.1.0-beta
```

Release má reprezentovať konkrétnu kombináciu:

```text
Atlas bootstrap code
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

Cieľom je, aby bolo možné konkrétny release spätne reprodukovať.

---

# Aktuálny stav `0.1.0-beta`

Aktuálny beta scope:

- NetBox `4.7.1`
- NetBox Docker `5.1.1`
- Docker image `v4.7.1-5.1.1`
- one-command Docker deployment
- automatický superuser
- automatický API token
- Atlas baseline
- 1 Custom Field Choice Set
- 4 Custom Fields
- Cisco Device Types
- Fortinet Device Types
- Juniper Device Types
- 690 Device Types
- 26 342 component templates
- idempotentný provisioning
- final verification
- configuration drift detection pre baseline
- GitHub Actions validácia

Budúce verzie môžu rozšíriť napríklad:

- ďalších výrobcov,
- ďalšie Atlas baseline objekty,
- ďalšie integračné nastavenia,
- Checkmk provisioning,
- Oxidized provisioning,
- Atlas application deployment,
- reverse proxy,
- zákaznícke deployment profily.

---

# Cieľ projektu

Dlhodobý cieľ je dostať nasadenie Atlas infraštruktúry do podoby:

```text
nový server
    │
    ▼
Atlas deployment
    │
    ├── NetBox
    ├── Device Catalog
    ├── Atlas baseline
    ├── Checkmk
    ├── Oxidized
    ├── Atlas application
    └── integrations
```

Aktuálna verzia rieši prvú základnú časť tejto automatizácie:

```text
NetBox deployment
        +
Atlas NetBox bootstrap
```
