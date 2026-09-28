# Aricoma Atlas – NetBox Bootstrap

Nástroje na automatizované vytvorenie a provisioning základnej NetBox konfigurácie pre projekt **Aricoma Atlas**.

Cieľom projektu je umožniť pripraviť novú čistú NetBox inštanciu do štandardného Atlas stavu reprodukovateľným a idempotentným spôsobom.

Bootstrap aktuálne zabezpečuje:

- Atlas Custom Field Choice Sets,
- Atlas Custom Fields,
- výrobcov sieťových zariadení,
- Device Types,
- component templates jednotlivých Device Types,
- validáciu konfigurácie pred zápisom,
- kontrolu výsledného stavu po provisioningu.

Device Type katalóg sa generuje z upstream projektu **NetBox Community Device Type Library** na základe presne definovaného Git commitu a Atlas selection manifestu.

---

# Aktuálna verzia

**Atlas Device Catalog:** `0.1.0-beta`

Testované prostredie:

- NetBox `4.7.1`
- NetBox Docker `5.1.1`

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
| Power ports | 331 |
| Interfaces | 23 456 |
| Rear ports | 2 |
| Module bays | 1 687 |
| Device bays | 2 |
| Power outlets | 4 |
| **Spolu** | **26 342** |

---

# Štruktúra repozitára

```text
atlas-netbox-bootstrap/
├── catalog/
│   ├── manifest.yaml
│   └── baseline/
│       ├── custom_field_choice_sets.yaml
│       └── custom_fields.yaml
│
├── scripts/
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

Lokálne a generované dáta sa zámerne neukladajú do Git repozitára:

```text
.venv/
build/
devicetype-library/
```

---

# Atlas NetBox baseline

Atlas baseline predstavuje konfiguráciu, ktorá má existovať na každej Atlas NetBox inštancii bez ohľadu na konkrétneho zákazníka.

Aktuálne obsahuje:

- 1× Custom Field Choice Set
- 4× Custom Fields

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

---

# Atlas Device Catalog

Atlas Device Catalog sa generuje z upstream projektu:

```text
netbox-community/devicetype-library
```

Presná verzia upstream repozitára je definovaná v:

```text
catalog/manifest.yaml
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

## Prečo používame pinned commit

Atlas katalóg nesmie závisieť od aktuálneho stavu upstream repozitára.

Napríklad:

```text
Atlas Device Catalog 0.1.0-beta
```

musí vždy vytvoriť rovnakých 690 Device Types.

Preto `build_catalog.py` pred buildom kontroluje:

```text
Expected upstream commit
        =
Actual local upstream commit
```

Ak sa commity nezhodujú, build sa zastaví.

Tým zabezpečujeme reprodukovateľnosť jednotlivých Atlas release verzií.

---

# Architektúra bootstrapu

Celý proces vyzerá nasledovne:

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
 Atlas baseline    Device Catalog
        │                │
        └───────┬────────┘
                ▼
        bootstrap_netbox.py
                │
                ▼
             NetBox
```

---

# Inštalácia development prostredia

## 1. Python virtual environment

Vytvorenie virtual environment:

```bash
python3 -m venv .venv
```

Aktivácia:

```bash
source .venv/bin/activate
```

Inštalácia dependencies:

```bash
pip install -r requirements.txt
```

---

# Device Type Library

Upstream Device Type Library nie je súčasťou tohto Git repozitára.

Naklonuje sa samostatne:

```bash
git clone https://github.com/netbox-community/devicetype-library.git
```

Následne je potrebné checkoutnúť commit definovaný v:

```text
catalog/manifest.yaml
```

Napríklad:

```bash
cd devicetype-library

git checkout <COMMIT_Z_MANIFESTU>
```

`build_catalog.py` automaticky overí, či je použitý správny commit.

---

# Prístup k NetBox API

Bootstrap komunikuje s NetBoxom cez REST API.

URL NetBoxu sa nastavuje pomocou environment variable:

```bash
export NETBOX_URL="http://localhost:8000"
```

API token:

```bash
export NETBOX_TOKEN="..."
```

## Bezpečnosť

Do Git repozitára sa nikdy nesmú ukladať:

- NetBox API tokeny,
- heslá,
- privátne kľúče,
- zákaznícke credentials,
- `.env` súbory obsahujúce secrets.

Credentials musia byť poskytované cez environment variables alebo externý secrets management.

---

# Hlavný bootstrap

Primárny entrypoint projektu je:

```text
scripts/bootstrap_netbox.py
```

Pri bežnom použití nie je potrebné spúšťať jednotlivé provisioning skripty manuálne.

---

# Dry-run

Pred každým provisioningom je odporúčané spustiť:

```bash
python scripts/bootstrap_netbox.py
```

Dry-run kontroluje:

1. Atlas baseline,
2. Custom Field Choice Sets,
3. Custom Fields,
4. výrobcov,
5. Device Types,
6. component templates,
7. prípadný configuration drift.

Dry-run nevykonáva žiadne zmeny v NetBoxe.

Kompletne provisionovaný Atlas NetBox má aktuálne skončiť približne takto:

```text
Choice Sets:
  existing: 1
  missing:  0
  drift:    0

Custom Fields:
  existing: 4
  missing:  0
  drift:    0

Device Types:
  Existing: 690
  Missing:  0

TOTAL COMPONENTS:
  catalog=26342
  existing=26342
  missing=0
```

---

# Provisioning novej NetBox inštancie

Provisioning sa spustí:

```bash
python scripts/bootstrap_netbox.py --apply
```

Bootstrap najprv automaticky vykoná kompletný preflight dry-run.

Ak preflight zlyhá, provisioning sa nespustí.

## Poradie provisioningu

```text
Preflight
    │
    ▼
Custom Field Choice Sets
    │
    ▼
Custom Fields
    │
    ▼
Manufacturers
    │
    ▼
Device Types
    │
    ▼
Component Templates
    │
    ▼
Final Verification
```

Po úspešnom provisioningu bootstrap vykoná záverečný dry-run, ktorým overí výsledný stav.

---

# Idempotencia

Provisioning je navrhnutý ako idempotentný.

To znamená, že opakované spustenie bootstrapu nemá vytvárať duplicity.

Prvý run:

```text
missing object
    ↓
CREATE
```

Ďalšie runy:

```text
object exists
    ↓
EXISTS
    ↓
bez zmeny
```

Výsledný stav kompletne provisionovanej inštancie má byť:

```text
missing = 0
drift   = 0
```

---

# Configuration drift

Atlas baseline nekontroluje iba existenciu Custom Fields.

Kontroluje aj ich deklarovanú konfiguráciu.

Ak napríklad NetBox obsahuje:

```text
checkmk_enabled
```

ale jeho konfigurácia sa líši od:

```text
catalog/baseline/custom_fields.yaml
```

bootstrap vypíše:

```text
[DRIFT]
```

Existujúci objekt sa zámerne automaticky neupravuje.

Dôvodom je ochrana už používaných Custom Fields a prípadných existujúcich dát.

Drift sa musí vyhodnotiť a vyriešiť vedomou zmenou baseline alebo NetBox konfigurácie.

---

# Rebuild Atlas Device Catalog

Vygenerovaný katalóg sa nachádza v:

```text
build/device-types/
```

Adresár `build/` nie je verzovaný v Git repozitári.

Rebuild je možné vykonať:

```bash
python scripts/bootstrap_netbox.py --rebuild-catalog
```

Alebo rebuild a následný provisioning:

```bash
python scripts/bootstrap_netbox.py \
  --rebuild-catalog \
  --apply
```

Pri rebuilde sa vždy kontroluje pinned upstream Git commit.

---

# Audit katalógu

## Základný audit

```bash
python scripts/audit_catalog.py
```

Audit kontroluje napríklad:

- počet Device Types,
- distribúciu podľa výrobcu,
- počet component templates,
- použité component types,
- závislosti medzi komponentmi,
- neznáme top-level YAML polia.

---

# Audit polí component templates

```bash
python scripts/audit_component_fields.py
```

Audit vypíše všetky polia používané jednotlivými component templates.

Používa sa najmä pri:

- aktualizácii Device Type Library,
- zmene pinned upstream commitu,
- pridávaní nového výrobcu,
- zmene NetBox verzie.

Cieľom je zabrániť tomu, aby importer potichu zahodil nové alebo nepodporované YAML atribúty.

---

# Jednotlivé skripty

## `bootstrap_netbox.py`

Hlavný entrypoint.

Riadi:

- build katalógu,
- preflight,
- provisioning baseline,
- provisioning Device Catalogu,
- final verification.

## `build_catalog.py`

Vytvára Atlas Device Catalog z upstream Device Type Library podľa:

```text
catalog/manifest.yaml
```

## `provision_baseline.py`

Spravuje:

- Custom Field Choice Sets,
- Custom Fields,
- configuration drift.

## `provision_netbox.py`

Spravuje:

- Manufacturers,
- Device Types,
- Device Type component templates.

## `audit_catalog.py`

Analyzuje obsah výsledného Atlas katalógu.

## `audit_component_fields.py`

Analyzuje YAML schému component templates používanú v katalógu.

---

# Aktuálne podporované Device Type komponenty

Atlas Device Catalog `0.1.0-beta` aktuálne používa:

```text
console-ports
power-ports
power-outlets
interfaces
rear-ports
module-bays
device-bays
```

Aktuálne nepoužíva:

```text
console-server-ports
front-ports
inventory-items
cooling-intakes
cooling-outflows
```

Pri pridaní nových modelov alebo výrobcov musí audit overiť, či sa tento stav nezmenil.

---

# Development workflow

Odporúčaný spôsob práce:

```text
feature branch
      │
      ▼
lokálna validácia
      │
      ▼
GitHub Actions
      │
      ▼
Pull Request
      │
      ▼
main
      │
      ▼
release / version tag
```

Príklady branchov:

```text
feature/netbox-platforms
feature/netbox-tags
feature/add-aruba
fix/device-type-import
```

---

# Versioning

Projekt používa semantic versioning.

Príklady:

```text
v0.1.0-beta
v0.2.0-beta
v1.0.0
```

Verzia Atlas Device Catalogu je nezávislá od upstream Device Type Library.

Presný upstream commit je vždy definovaný v:

```text
catalog/manifest.yaml
```

---

# Aktuálny stav `0.1.0-beta`

Funkčne overené:

```text
Atlas baseline
├── Choice Sets       1
└── Custom Fields     4

Atlas Device Catalog
├── Manufacturers     3
├── Device Types      690
└── Components        26 342
```

Na kompletne provisionovanej testovacej NetBox inštancii:

```text
missing = 0
drift   = 0
```

Bootstrap bol testovaný proti:

```text
NetBox 4.7.1
NetBox Docker 5.1.1
```
