# Aricoma Atlas

Automatizované nasadenie a provisioning infraštruktúry pre projekt **Aricoma Atlas**.

Aktuálna beta baseline nasadzuje a pripravuje:

- **OpenBao 2.7.0** ako centrálny secrets backend,
- **NetBox 4.7.1** cez pripnutý `netbox-docker` runtime,
- **Oxidized 0.37.0** s persistentnou Git históriou konfigurácií,
- **Atlas FastAPI backend 0.2.0-beta** zostavený a spustený v samostatnom kontajneri,
- bezpečný počiatočný stav Oxidized **WAITING_FOR_INVENTORY**,
- spoločný **Nginx HTTPS proxy** s certifikátmi internej CA,
- Nginx Basic Auth pre celý Oxidized web/API,
- PostgreSQL a Valkey backend služby pre NetBox,
- Atlas NetBox baseline a Atlas Device Catalog,
- OpenBao policies a AppRole identity pre ďalšie Atlas služby,
- OpenBao audit logging,
- systémový administračný wrapper `atlasctl`.

Odporúčaný clean-install workflow na novej Linux VM je:

```bash
sudo ./install.sh
sudo ./deploy.sh
```

`install.sh` pripraví nainštalovaný strom v `/opt/aricoma-atlas`, privátny runtime a administračný wrapper. `deploy.sh` následne spustí deployment z nainštalovaného stromu. Po inštalácii sa bežná prevádzka vykonáva cez:

```bash
sudo atlasctl deploy
sudo atlasctl status
sudo atlasctl openbao
sudo atlasctl oxidized reconcile
sudo atlasctl oxidized logs -f
sudo atlasctl backend logs -f
sudo atlasctl proxy logs -f
```

Pred prvým spustením je potrebné pripraviť lokálnu `.runtime/proxy.yaml`, DNS a TLS súbory podľa sekcie **Inštalácia a deployment na čistej VM**. Priame `python3 scripts/*.py` entrypointy zostávajú dostupné najmä pre vývoj a diagnostiku.

---

# Aktuálny stav

**Posledný vydaný release:** `v0.2.0-beta`

Táto dokumentácia opisuje aktuálny pracovný strom. Historický scope už vydaného tagu `v0.1.2-beta` je uvedený samostatne nižšie.

Aktuálne overený clean-install flow zahŕňa:

- prípravu zákazníckeho alebo LAB TLS vstupu,
- import lokálnej `.runtime/` konfigurácie cez `install.sh`,
- deployment z `/opt/aricoma-atlas` cez `deploy.sh`/`atlasctl`,
- OpenBao init, Shamir 5/3 unseal, Raft readiness a bootstrap,
- zdravý NetBox stack a idempotentný NetBox bootstrap,
- zdravý Oxidized backend s prázdnym inventárom,
- waiting stránku namiesto chyby backendu pri nulovom inventári,
- reload inventára cez `atlasctl oxidized reconcile`,
- strict SSH host-key verification,
- persistentný Oxidized `known_hosts` a Git runtime,
- polling interval `3600` sekúnd.

Release gate vyžaduje na cieľovej VM aspoň jeden úspešný reálny backup zariadenia, vznik Git verzie a zachovanie stavu po redeployi/reštarte.

Deklarovaná kombinácia pre tento deployment:

- OpenBao `2.7.0`
- NetBox `4.7.1`
- NetBox Docker `5.1.1`
- NetBox image `docker.io/netboxcommunity/netbox:v4.7.1-5.1.1`
- Oxidized image `docker.io/oxidized/oxidized:0.37.0`
- Atlas backend image `aricoma-atlas-backend:0.2.0-beta`
- Atlas backend base image `docker.io/library/python:3.13.16-slim-bookworm`
- Nginx image `docker.io/library/nginx:1.30.5`
- Docker Compose plugin
- Python 3 virtual environment

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

Táto verzia je **beta baseline**, nie finálny produkčný hardening.

## HTTPS vstup a zostávajúce TLS obmedzenia

V aktuálnom beta prostredí:

- OpenBao API používa lokálne HTTP,
- OpenBao listener má `tls_disable = true`,
- OpenBao API je bindnuté na loopback adresu,
- NetBox a Oxidized sú publikované cez spoločný Nginx a HTTPS,
- Atlas backend je publikovaný iba na loopback adrese `127.0.0.1:8081`,
- Atlas Operations UI nie je pripojené do Nginx, pretože používateľská autentifikácia je rozsah nasledujúceho milestone,
- Oxidized web/API nemá publikovaný host port,
- Nginx vyžaduje konkrétnu management IP a explicitný CIDR allowlist,
- Nginx → backend komunikácia prebieha cez HTTP v oddelených lokálnych Docker sieťach,
- Oxidized web/API je chránený OpenBao-backed Basic Auth,
- Oxidized má v tejto beta konfigurácii vedome nastavené `secure: false`; SSH host key sa preto neoveruje,
- návrat na `secure: true` vyžaduje naplnenie a správu persistentného `known_hosts`.

Oxidized inventory je JSON pole generované Atlasom z NetBoxu a OpenBao. Zariadeniové credentials sa materializujú iba do ephemeral súboru `/run/atlas/oxidized/router.json`, ktorý zostáva mimo Git a je do kontajnera pripojený read-only.

Pred produkčným nasadením je potrebné doplniť minimálne:

1. TLS pre OpenBao,
2. dôveryhodný CA trust a certificate verification v Python klientoch,
3. odstránenie `tls_disable`,
4. lifecycle a obnovu certifikátov,
5. backup/restore OpenBao, NetBox a Oxidized Git dát,
6. definovaný host-key rotation proces so samostatným schválením,
7. individuálne roly alebo SSO pre administratívny web/API prístup.

Produkčná verzia nesmie používať `verify=False` ako náhradu za správne overenie certifikátu a nesmie vypnúť SSH host-key verification ako náhradu za správu trustu.

---

# Architektúra beta deploymentu

```text
source checkout
    │
    ├── lokálna .runtime/proxy.yaml + TLS
    │
    ▼
install.sh
    │
    ├── atlas service account
    ├── /opt/aricoma-atlas
    ├── Python virtual environment
    ├── privátny /opt/aricoma-atlas/.runtime
    └── /usr/local/bin/atlasctl
    │
    ▼
deploy.sh / atlasctl deploy
    │
    ├── 1. OpenBao
    │      ├── Docker deploy
    │      ├── init pri fresh deployi
    │      ├── Shamir unseal 5/3
    │      ├── Raft leader readiness
    │      ├── KV v2: atlas/
    │      ├── policies + AppRoles
    │      ├── audit logging
    │      └── revoke initial root token
    │
    ├── VERIFY OPENBAO
    │      ├── initialized
    │      ├── unsealed
    │      └── atlas-deployer login
    │
    ├── 2. NetBox
    │      ├── secrets z OpenBao
    │      ├── pinned netbox-docker
    │      ├── PostgreSQL + Valkey
    │      ├── NetBox
    │      ├── API token
    │      ├── Atlas baseline
    │      └── Atlas Device Catalog
    │
    ├── 3. Oxidized
    │      ├── persistentný runtime a Git volume
    │      ├── WAITING_FOR_INVENTORY pri 0 zariadeniach
    │      ├── ephemeral JSONFile inventory + hodinový polling
    │      ├── strict SSH host-key verification
    │      └── web/API iba v internej Docker sieti
    │
    ├── 4. Atlas backend
    │      ├── lokálny build z pripnutého checkoutu
    │      ├── non-root kontajner s read-only root filesystemom
    │      ├── healthcheck GET /api/health
    │      └── host bind iba 127.0.0.1:8081
    │
    └── 5. Nginx
           ├── samostatné DNS názvy služieb
           ├── HTTPS s certifikátmi internej CA
           ├── management subnet allowlist
           └── Oxidized Basic Auth z OpenBao
```

Ak OpenBao stage zlyhá, ďalšie stage sa nespustia. Nginx sa spustí až po príprave backendov. Chýbajúca alebo neplatná proxy konfigurácia zastaví deployment pri preflight kontrole.

Prázdny Oxidized inventár nie je chyba deploymentu. Backend zostane zdravý a zobrazí waiting stav; po doplnení inventára sa zber aktivuje cez `sudo atlasctl oxidized reconcile`.

---

# Štruktúra repozitára

```text
aricoma-atlas/
├── .github/
│   └── workflows/
│       └── validate.yml
│
├── atlas/
│   ├── api/
│   │   ├── main.py
│   │   ├── routes/
│   │   │   ├── health.py
│   │   │   └── oxidized.py
│   │   └── schemas/
│   ├── services/
│   │   ├── netbox.py
│   │   ├── openbao.py
│   │   └── oxidized.py
│   ├── devices/
│   ├── prophylaxis/
│   ├── config.py
│   ├── logging.py
│   ├── device_credentials.py
│   ├── openbao_client.py
│   ├── deployment.py
│   ├── platforms.py
│   └── proxy.py
│
├── bin/
│   └── atlasctl
│
├── catalog/
│   ├── manifest.yaml
│   └── baseline/
│       ├── custom_field_choice_sets.yaml
│       └── custom_fields.yaml
│
├── deployment/
│   ├── backend.yaml
│   ├── backend/
│   │   └── Dockerfile
│   ├── netbox.yaml
│   ├── oxidized.yaml
│   ├── proxy.example.yaml
│   └── openbao/
│       ├── docker-compose.yml
│       ├── openbao.hcl
│       └── policies/
│           ├── atlas-backend.hcl
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
│   ├── deploy_backend.py
│   ├── deploy_proxy.py
│   ├── openbao_access.py
│   ├── bootstrap_netbox.py
│   ├── build_catalog.py
│   ├── provision_baseline.py
│   ├── provision_netbox.py
│   ├── audit_catalog.py
│   └── audit_component_fields.py
│
├── tests/
├── install.sh
├── deploy.sh
├── requirements.txt
├── .dockerignore
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

`install.sh` nasadzuje konkrétny checkout do `/opt/aricoma-atlas`. Prevádzkový runtime sa preto nesmie zamieňať so source checkoutom v domovskom adresári.

---

# `.runtime/`

Atlas používa dva súvisiace kontexty:

1. **bootstrap input v source checkoute** – `~/aricoma-atlas/.runtime/`,
2. **kanonický nainštalovaný runtime** – `/opt/aricoma-atlas/.runtime/`.

Pred `install.sh` sa v source checkoute pripravujú site-specific vstupy, najmä `proxy.yaml` a TLS súbory. Installer ich importuje do nainštalovaného runtime. Bežná prevádzka a `atlasctl` následne pracujú s `/opt/aricoma-atlas/.runtime/`.

Typický nainštalovaný runtime:

```text
/opt/aricoma-atlas/.runtime/
├── proxy.yaml
├── tls/
│   ├── ca.crt
│   ├── ca.key                 # iba LAB CA, ak bola vytvorená lokálne
│   ├── server.crt
│   ├── server.key
│   ├── server.csr
│   └── server.ext
├── proxy/
│   └── oxidized.htpasswd
├── openbao-approle.json
├── openbao-operator.json
├── openbao-backend.json
├── openbao-netbox.json
├── openbao-checkmk.json
├── openbao-oxidized.json
├── netbox-docker/
├── backend/
│   └── docker-compose.yml
└── oxidized/
    ├── config
    └── ssh/
        └── known_hosts
```

Ephemeral inventár je oddelený od persistentného runtime:

```text
/run/atlas/oxidized/router.json
```

OpenBao unseal keys sa do `.runtime/` neukladajú. Každý `openbao-*.json` obsahuje machine identity pre konkrétnu AppRole a musí zostať lokálny na danom serveri.

Oxidized Git repository nie je v Git repozitári Atlasu. Je uložený v persistentnom Docker volume. `router.json` obsahuje dočasnú materializáciu credentials; po reštarte hosta ho vytvorí `systemd-tmpfiles` ako prázdne JSON pole a Atlas ho znovu zostaví z NetBoxu a OpenBao.

Odporúčané minimum:

```bash
sudo chmod 700 /opt/aricoma-atlas/.runtime
sudo chmod 600 /opt/aricoma-atlas/.runtime/proxy.yaml
```

Konkrétne práva súborov a vlastníctvo runtime mountov spravuje installer/deployment. Manuálne ich nerozširuj len kvôli riešeniu chyby; najprv použi `sudo atlasctl status` a `sudo atlasctl oxidized reconcile`.

---

# Požiadavky na host

Cieľový host musí mať:

- podporovaný Linux so `sudo`,
- Git,
- Python 3.10 alebo novší a modul `venv`,
- OpenSSL CLI,
- prístup k Docker registru a potrebným Git repozitárom,
- pripravenú management IP, DNS a firewall pravidlá,
- zákaznícke certifikáty alebo explicitne LAB certifikáty.

`install.sh` pripravuje nainštalovaný Atlas runtime, Python virtual environment, služobný účet a administračný wrapper. Docker a Docker Compose musia byť po install stage dostupné a funkčné.

Overenie:

```bash
docker --version
docker compose version
docker info
python3 --version
openssl version
```

Pre loopback port isolation používaj Docker Engine 28 alebo novší. Staršie verzie majú známe obmedzenie izolácie portov publikovaných na loopback adresu. [Docker port publishing](https://docs.docker.com/engine/network/port-publishing/) (overené 30. 9. 2026).

Deploy explicitne normalizuje práva bind-mount konfigurácie NetBoxu: adresáre `configuration/` na `0755`, súbory na `0644`. Konfigurácia nesmie obsahovať plaintext heslá; Atlas ich dodáva z OpenBao cez prostredie kontajnera. Flow bol overený aj pri `umask 077`.

Pri chybe `PermissionError: /etc/netbox/config/` skontroluj práva tohto mountu. NetBox Docker môže chybu zobrazovať spolu s „Waiting on DB“, pretože opakovaný Django príkaz načítava aj konfiguráciu. Zvýšenie timeoutu nerieši nesprávne filesystem permissions.

---

# Python virtual environment

Táto sekcia je určená najmä pre vývoj v source checkoute. Produkčný/nainštalovaný flow vytvára vlastné prostredie pod `/opt/aricoma-atlas`.

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
python3 -m pip install -r requirements.txt
```

Overenie:

```bash
python3 --version
```

FastAPI 0.142.2 vyžaduje Python 3.10 alebo novší. V dokumentácii a skriptoch používaj explicitne `python3`. Na staršom systémovom Pythone v macOS môže `urllib3` zobrazovať `NotOpenSSLWarning` kvôli LibreSSL; beta flow tým nemusí byť blokovaný, ale produkcia má používať podporovaný Python/OpenSSL stack.

## Atlas backend API — Milestone 1

Pri štandardnom nasadení `sudo atlasctl deploy` zostaví image z aktuálneho nainštalovaného Git checkoutu, spustí backend ako štvrtú z piatich fáz a počká na stav `healthy`. Konfigurácia image, portu a timeoutu je v `deployment/backend.yaml`; generovaný Compose runtime je v `.runtime/backend/docker-compose.yml`.

Backend je na hoste zámerne dostupný iba lokálne:

```bash
curl -i http://127.0.0.1:8081/api/health
sudo atlasctl backend logs -f
```

Verejné sprístupnenie cez Nginx je odložené do momentu, keď bude definovaná autentifikácia a autorizácia Atlas API.

Lokálne spustenie po aktivácii virtual environmentu:

```bash
uvicorn atlas.api.main:app --host 127.0.0.1 --port 8081
```

Liveness kontrola:

```bash
curl -i http://127.0.0.1:8081/api/health
```

Očakávané telo odpovede:

```json
{"status":"ok","service":"Aricoma Atlas","version":"0.2.0-beta","environment":"development"}
```

Konfigurácia používa environment premenné s prefixom `ATLAS_`. Bezpečné lokálne hodnoty sú zdokumentované v `.env.example`; reálny `.env` zostáva mimo Git. Premenné Milestone 1 sú `ATLAS_APP_NAME`, `ATLAS_APP_VERSION`, `ATLAS_ENVIRONMENT`, `ATLAS_API_PREFIX` a `ATLAS_LOG_LEVEL`.

`GET /api/health` zostáva iba liveness kontrola procesu. Nekontroluje NetBox, OpenBao ani Oxidized.

## Atlas backend API — Milestone 2

Milestone 2 pridáva bezpečnú read-only dependency vrstvu a samostatný readiness endpoint. Kontrakty endpointov sú zámerne oddelené:

| Endpoint | Význam | HTTP odpoveď |
| --- | --- | --- |
| `GET /api/health` | proces FastAPI beží | `200` bez ohľadu na stav externých služieb |
| `GET /api/ready` | OpenBao, NetBox a Oxidized sú použiteľné | `200` pri úspechu, inak `503` |

Readiness overuje, že OpenBao je dostupné, inicializované a odomknuté a že funguje AppRole login identity `atlas-backend`. Následne cez token uložený výhradne v OpenBao vykoná lacný read-only NetBox request a načíta Oxidized `nodes.json`. Prázdne Oxidized pole `[]` je pripravený stav, nie chyba. Kontrola nevykonáva žiadny zápis do OpenBao, NetBoxu ani Oxidized a nevolá Oxidized `/reload`.

Bezpečné dependency nastavenia:

```text
ATLAS_OPENBAO_URL
ATLAS_OPENBAO_IDENTITY_FILE
ATLAS_NETBOX_URL
ATLAS_OXIDIZED_URL
ATLAS_HTTP_CONNECT_TIMEOUT
ATLAS_HTTP_READ_TIMEOUT
```

Identity súbor obsahujúci `role_id` a `secret_id` zostáva mimo Git. OpenBao token sa cacheuje iba v pamäti procesu; po odmietnutí už cacheovaného tokenu sa vykoná najviac jeden nový AppRole login a jeden opakovaný request. NetBox token zostáva iba na `atlas/netbox/api`, key `token`, a nekopíruje sa do `.env` ani do iného konfiguračného súboru.

Lokálne manuálne overenie po nastavení URL a dostupnosti `.runtime/openbao-backend.json`:

```bash
python3 -m uvicorn atlas.api.main:app --host 127.0.0.1 --port 8081
curl -i http://127.0.0.1:8081/api/health
curl -i http://127.0.0.1:8081/api/ready
```

Chybová odpoveď readiness obsahuje iba stav dependency a bezpečný dôvod, napríklad `connection_failed`, `authentication_failed`, `sealed` alebo `unavailable`. Neobsahuje raw exception, token ani heslo.

### Kontajnerové zapojenie

Od Milestone 4 má backend kontajner read-only mount AppRole identity, read-write mount výhradne na ephemeral Oxidized inventory adresár a pripojenie do troch interných aplikačných sietí. Neobsahuje Docker socket a beží pod numerickým UID/GID neprivilegovaného Atlas service accountu. Kontajnerový healthcheck naďalej používa lacný `/api/health`; dependency readiness je samostatný endpoint.

## Atlas backend API — Milestone 3

Milestone 3 pridáva prvú zápisovú orchestration operáciu:

```http
POST /api/oxidized/sync
```

Atlas načíta cez paginovaný NetBox API iba zariadenia so stavom `active` a s `oxidized_enabled=true`, striktne overí meno, primary IP, výrobcu, platform mapping a `credential_profile`, a cez AppRole `atlas-backend` načíta príslušné admin credentials z OpenBao. Zariadenie v inom stave (napríklad `offline`) sa do desired-state inventára nezaradí. IPv4 aj IPv6 prefix sa pred odovzdaním Oxidized odstráni. Neexistuje fallback platformy podľa výrobcu ani fallback credentials.

Spracovanie používa jedno spoločné jadro pre API aj `sudo atlasctl oxidized reconcile`:

```text
NetBox desired state
        +
OpenBao credentials
        ↓
canonical inventory zoradený podľa mena
        ↓
diff added / updated / removed / unchanged
        ↓
atomický zápis router.json iba pri zmene
        ↓
Oxidized GET /reload po zápise alebo pending retry
```

Zmena ľubovoľného runtime poľa vrátane hesla alebo enable secretu znamená `updated`, ale odpoveď a logy obsahujú iba názov zariadenia a bezpečný reason code. Plaintext credentials, tokeny a AppRole `secret_id` sa nevracajú.

Safe reconciliation rozlišuje tieto situácie:

- zariadenie odstránené z NetBoxu alebo s `oxidized_enabled=false` sa odstráni iba z runtime inventára,
- chybné enabled zariadenie, ktoré už v inventári existuje, sa zachová a uvedie v `issues`,
- chýbajúci secret alebo chybná secret schéma je per-device `error`; ostatné zariadenia pokračujú,
- nedostupný NetBox alebo OpenBao, nefunkčná AppRole identita, poškodený current inventory alebo chyba zápisu ukončí celý sync bez vytvorenia inventára z neúplných dát,
- súbežný sync dostane `409 sync_already_running`.

Úspech a partial success vracajú `200`. Ak zápis prebehol, ale Oxidized reload zlyhal, odpoveď má `503`, `status=error`, `inventory_updated=true` a `oxidized_reloaded=false`; automatický rollback sa nevykonáva. Bezpečný marker v ephemeral runtime zabezpečí, že ďalší sync reload zopakuje aj bez ďalšieho prepisu inventára. Ak sa desired a current inventory zhodujú a žiadny reload nečaká, Atlas súbor neprepisuje a `/reload` nevolá.

Príklad lokálneho volania backendu spusteného mimo kontajnera:

```bash
export ATLAS_OPENBAO_IDENTITY_FILE="$PWD/.runtime/openbao-backend.json"
export ATLAS_OXIDIZED_INVENTORY_FILE=/run/atlas/oxidized/router.json
python3 -m uvicorn atlas.api.main:app --host 127.0.0.1 --port 8081
curl -sS -X POST http://127.0.0.1:8081/api/oxidized/sync
curl -sS -X POST http://127.0.0.1:8081/api/oxidized/sync
```

Druhé volanie pri nezmenenom NetBoxe a OpenBao má vrátiť `inventory_changed=false`, nulové add/update/remove a nemá vyvolať ďalší zápis ani reload.

Atlas touto operáciou synchronizuje iba desired runtime inventory. Configuration backup, polling, porovnanie konfigurácií a Git commit vykonáva Oxidized; sync nemaže zariadeniové secrets, NetBox objekty, uložené konfigurácie ani Oxidized Git históriu.

### Kontajnerová dostupnosť

Milestone 4 dopĺňa backend kontajneru AppRole identity, Oxidized runtime inventory a interné siete OpenBao, NetBoxu a Oxidized. `POST /api/oxidized/sync` preto používa v kontajnerovom deploymente tú istú `OxidizedSyncService` ako `sudo atlasctl oxidized reconcile`. Atlas API a UI zostávajú dostupné iba cez host loopback; Nginx ingress sa nemení.

## Atlas Operations UI — Milestone 4

FastAPI servuje jednoduchú single-page Operations UI na `GET /`. Frontend je čisté HTML, CSS a vanilla JavaScript bez CDN, npm alebo Node build procesu. Poskytuje:

- aplikačný stav Atlasu, OpenBao, NetBoxu a Oxidized,
- porovnanie NetBox-enabled zariadení s aktuálnym runtime inventárom,
- bezpečný zoznam zariadení obsahujúci iba `name`, `ip` a `model`,
- explicitnú operáciu **Sync inventory**,
- výsledok posledného syncu v aktuálnej browser session,
- explicitnú operáciu **Queue backup** iba pre zariadenie z runtime inventára,
- samostatný zoznam aktívnych zariadení s `profylaxia_enabled=true` a zvolenou
  kontrolou `CPU utilization`,
- explicitné tlačidlo **Run CPU check** a posledný lokálne uložený výsledok.

Každé tlačidlo volá konkrétny Atlas API endpoint a následne shared Python service. UI nespúšťa shell, `sudo` ani `atlasctl`; backend nemá Docker socket a neposkytuje deploy, OpenBao admin helper ani raw log viewer.

### API kontrakty pre Operations UI

| Endpoint | Význam |
| --- | --- |
| `GET /api/health` | Lacná liveness kontrola procesu Atlas API. |
| `GET /api/ready` | Použiteľnosť kritických dependencies; pri chybe vracia `503`. |
| `GET /api/status` | Používateľský aggregate stav `healthy`, `degraded` alebo `unhealthy`. |
| `GET /api/oxidized/status` | Počty NetBox-enabled a runtime zariadení plus počet inventory issues. |
| `GET /api/oxidized/devices` | Verejný pohľad `name`, `ip`, `model`; credentials sa nevracajú. |
| `POST /api/oxidized/sync` | Reconciliation cez rovnakú `OxidizedSyncService` ako CLI cesta. |
| `POST /api/oxidized/devices/{name}/backup` | Zaradí existujúci runtime node na prioritné spracovanie v Oxidized; `202 accepted` nepotvrdzuje dokončený backup. |
| `GET /api/prophylaxis/devices` | Vráti bezpečný zoznam aktívnych zariadení oprávnených na CPU kontrolu. |
| `POST /api/prophylaxis/devices/{id}/checks/cpu` | Vykoná jednu CPU kontrolu a uloží normalizovaný výsledok lokálne. |
| `GET /api/prophylaxis/results?limit=50` | Vráti najnovšie lokálne uložené výsledky bez credentials a raw CLI výstupu. |

Profylaxia história je SQLite databáza v kontajneri na
`/run/atlas/prophylaxis/results.sqlite3`. Deployment pripája persistentný host
adresár `/opt/aricoma-atlas/.runtime/prophylaxis/`, takže výsledky prežijú
reštart alebo výmenu backend kontajnera. Databáza má režim `0600`, adresár
`0700` a predvolený retention limit 10 000 výsledkov. Najstaršie záznamy sa po
prekročení limitu odstránia. Neukladá sa username, password, OpenBao token ani
raw príkazový výstup.

`GET /api/oxidized/status` zámerne nevykonáva credential resolution ani plný sync. `inventory_issues` je lacný stavový údaj: počet názvov prítomných iba v jednej z množín NetBox-enabled/runtime plus chýbajúce alebo duplicitné mená z NetBox odpovede. Zmena credentials alebo ostatných polí sa ukáže až vo výsledku explicitného syncu.

### CLI a API hranice

| CLI operácia | UI/API ekvivalent | Rozdiel |
| --- | --- | --- |
| `atlasctl status` | `GET /api/status` | CLI môže navyše kontrolovať Docker, filesystem a host runtime; API zostáva bez host privileges. |
| `atlasctl oxidized reconcile` | `POST /api/oxidized/sync` | Obe cesty používajú shared reconciliation service. |
| prioritný Oxidized node | `POST /api/oxidized/devices/{name}/backup` | Požiadavka node iba zaradí; výsledok backupu spravuje Oxidized. |

`atlasctl deploy` a `atlasctl openbao` nemajú UI ani API ekvivalent.

### Bezpečný manuálny prístup

Backend zostáva publikovaný iba na `127.0.0.1:8081`; Atlas hostname ani route sa do Nginx nepridáva. Na VM možno overiť liveness:

```bash
curl http://127.0.0.1:8081/api/health
```

Z administračnej workstation vytvor SSH tunnel:

```bash
ssh -L 8081:127.0.0.1:8081 user@atlas-vm
```

Potom otvor:

```text
http://127.0.0.1:8081/
```

Tunnel sprístupní loopback port iba lokálnemu prehliadaču na workstation. Používateľská autentifikácia, RBAC a Nginx HTTPS ingress pre Atlas UI patria do Milestone 5.

---

# Inštalácia a deployment na čistej VM

## 1. Clone repozitára

```bash
git clone <REPOSITORY_URL> aricoma-atlas
cd aricoma-atlas
```

## 2. Priprav site-specific TLS a proxy konfiguráciu

DNS názvy, management IP, povolené podsiete a certifikáty sa líšia podľa zákazníka. Nie sú pevne zapísané v repozitári.

```bash
umask 077
mkdir -p .runtime/tls
chmod 700 .runtime .runtime/tls
cp deployment/proxy.example.yaml .runtime/proxy.yaml
chmod 600 .runtime/proxy.yaml
```

Vyplň všetky povinné hodnoty v `.runtime/proxy.yaml`:

| Položka | Význam |
|---|---|
| `proxy.listen_address` | Konkrétna management IP tejto VM, nie `0.0.0.0`. |
| `proxy.https_port` | HTTPS port, štandardne `443`. |
| `proxy.allowed_networks` | Neprázdny zoznam správcovských CIDR podsietí/VPN; `0.0.0.0/0` a `::/0` sa odmietnu. |
| `tls.ca_certificate` | Absolútna cesta k PEM trust bundle internej CA. |
| `services.netbox.hostname` | DNS názov NetBoxu smerujúci na management IP VM. |
| `services.oxidized.hostname` | Samostatný DNS názov Oxidized smerujúci na rovnakú management IP. |
| `services.*.certificate` | Absolútna cesta k PEM server certifikátu vrátane intermediate chain. |
| `services.*.private_key` | Absolútna cesta k príslušnému nešifrovanému PEM private key. |

Certifikát musí pokrývať príslušný DNS názov. Jeden SAN certifikát môže pokrývať oba názvy. Deploy overuje zhodu certifikátu s kľúčom, platnosť, DNS meno a reťazec voči nastavenej CA; nevydáva certifikáty ani nemení DNS.

### LAB-only CA a SAN certifikát

Nasledujúci príklad je určený iba pre izolovaný LAB. Privátny CA kľúč nesmie zostať na produkčnom Atlas serveri.

```bash
export ATLAS_IP="10.99.98.114"
export ATLAS_ALLOWED_NETWORK="10.99.98.0/23"
export NETBOX_HOSTNAME="netbox.atlas.test"
export OXIDIZED_HOSTNAME="oxidized.atlas.test"

umask 077
mkdir -p .runtime/tls
chmod 700 .runtime .runtime/tls

openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 30 \
  -subj "/CN=Atlas Lab CA" \
  -keyout .runtime/tls/ca.key \
  -out .runtime/tls/ca.crt \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -addext "subjectKeyIdentifier=hash"

openssl req -new -newkey rsa:2048 -nodes -sha256 \
  -subj "/CN=${NETBOX_HOSTNAME}" \
  -keyout .runtime/tls/server.key \
  -out .runtime/tls/server.csr

cat > .runtime/tls/server.ext <<EOF
subjectAltName=DNS:${NETBOX_HOSTNAME},DNS:${OXIDIZED_HOSTNAME}
extendedKeyUsage=serverAuth
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
EOF

openssl x509 -req -sha256 -days 30 \
  -in .runtime/tls/server.csr \
  -CA .runtime/tls/ca.crt \
  -CAkey .runtime/tls/ca.key \
  -CAcreateserial \
  -extfile .runtime/tls/server.ext \
  -out .runtime/tls/server.crt
```

Ak je v bootstrap prostredí dostupný PyYAML, `.runtime/proxy.yaml` možno pripraviť aj skriptom:

```bash
python3 - <<'PY'
import os
from pathlib import Path
import yaml

root = Path.cwd()
runtime = root / ".runtime"
runtime.mkdir(mode=0o700, exist_ok=True)

config = yaml.safe_load(
    (root / "deployment/proxy.example.yaml").read_text()
)

config["proxy"]["listen_address"] = os.environ["ATLAS_IP"]
config["proxy"]["https_port"] = 443
config["proxy"]["allowed_networks"] = [
    os.environ["ATLAS_ALLOWED_NETWORK"]
]
config["tls"]["ca_certificate"] = str(
    root / ".runtime/tls/ca.crt"
)

for service, hostname in (
    ("netbox", os.environ["NETBOX_HOSTNAME"]),
    ("oxidized", os.environ["OXIDIZED_HOSTNAME"]),
):
    config["services"][service]["hostname"] = hostname
    config["services"][service]["certificate"] = str(
        root / ".runtime/tls/server.crt"
    )
    config["services"][service]["private_key"] = str(
        root / ".runtime/tls/server.key"
    )

target = runtime / "proxy.yaml"
target.write_text(yaml.safe_dump(config, sort_keys=False))
target.chmod(0o600)
PY
```

## 3. Nainštaluj Atlas do `/opt`

```bash
sudo ./install.sh
```

Po installeri over import runtime vstupov:

```bash
sudo test -f /opt/aricoma-atlas/.runtime/proxy.yaml
sudo test -f /opt/aricoma-atlas/.runtime/tls/ca.crt
sudo test -f /opt/aricoma-atlas/.runtime/tls/server.crt
sudo test -f /opt/aricoma-atlas/.runtime/tls/server.key
```

Súbory nekopíruj ručne do `/opt`, pokiaľ installer zlyhá. Chýbajúci import je chyba install flow, nie štandardný post-install krok.

## 4. Spusti deployment

```bash
sudo ./deploy.sh
```

Správny názov je `deploy.sh`; príkaz `./deploy` neexistuje. `deploy.sh` pracuje s nainštalovaným stromom a očakáva kanonickú konfiguráciu pod `/opt/aricoma-atlas/.runtime/`.

Po nainštalovaní možno rovnaký deployment opakovať cez:

```bash
sudo atlasctl deploy
```

## 5. Prevádzkové príkazy `atlasctl`

```text
sudo atlasctl deploy [deploy_atlas.py options]
sudo atlasctl status
sudo atlasctl openbao
sudo atlasctl oxidized reconcile
sudo atlasctl oxidized logs [docker compose logs options]
sudo atlasctl backend logs [docker compose logs options]
sudo atlasctl proxy logs [docker compose logs options]
```

Význam:

| Príkaz | Úloha |
|---|---|
| `sudo atlasctl deploy` | Nasadí alebo zosúladí celý Atlas stack. |
| `sudo atlasctl status` | Zobrazí stav kontajnerov a Oxidized inventára. |
| `sudo atlasctl openbao` | Otvorí interaktívny OpenBao access helper. |
| `sudo atlasctl oxidized reconcile` | Overí runtime mounty a zosúladí/reloadne Oxidized inventory. |
| `sudo atlasctl oxidized logs -f` | Sleduje live logy Oxidized. |
| `sudo atlasctl backend logs -f` | Sleduje live logy Atlas FastAPI backendu. |
| `sudo atlasctl proxy logs -f` | Sleduje live logy Nginx HTTPS ingressu. |

## Prihlasovanie a hranice prístupu

Oxidized nemá vlastný používateľský login. Nginx chráni celý jeho web aj API pomocou Basic Auth cez HTTPS. [Nginx Basic Auth](https://nginx.org/en/docs/http/ngx_http_auth_basic_module.html), [oxidized-web](https://github.com/ytti/oxidized-web) (overené 30. 9. 2026).

Pri prvom deployi `deploy_proxy.py` vygeneruje účet a uloží ho do KV v2 na `atlas/oxidized/web` (`username`, `password`). Pri opakovanom deployi sa existujúce hodnoty použijú znova. Chybný secret alebo chyba oprávnení zastaví deployment; heslo sa potichu nezmení. Nginx dostane iba SHA-512 crypt hash do `.runtime/proxy/oxidized.htpasswd`, nie plaintext heslo ani OpenBao token.

Prihlásený administrátor s príslušným oprávnením môže secret načítať cez OpenBao CLI:

```bash
bao kv get -mount=atlas oxidized/web
```

Výstup obsahuje heslo; neukladaj ho do zdieľaných logov. NetBox používa vlastné používateľské účty a API tokeny. OpenBao zostáva dostupné iba lokálne a proxy ho nesprístupňuje.

## Deployment poradie

```text
Docker preflight
        ↓
OpenBao init / unseal / Raft readiness / bootstrap
        ↓
OpenBao readiness gate
        ↓
NetBox runtime + secrets + healthcheck + bootstrap
        ↓
Oxidized runtime + waiting/collection mode
        ↓
Atlas backend + Operations UI + healthcheck
        ↓
Nginx HTTPS + Oxidized Basic Auth
        ↓
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
- počas `atlasctl deploy` držia iba v pamäti procesu,
- po úspešnom fresh deploymente sa ešte raz zobrazia na konci,
- neukladajú do súboru,
- neukladajú do Git repozitára,
- nemajú zostať na tom istom Atlas serveri.

Admin ich musí uložiť mimo servera podľa interného recovery procesu. Initial root token sa používa iba počas prvotného bootstrapu a následne sa automaticky revokuje.

---

# OpenBao po reštarte

Bez auto-unseal sa OpenBao po reštarte vráti do stavu `sealed`.

Opätovné zosúladenie stacku:

```bash
sudo atlasctl deploy
```

Deployment zistí sealed stav a interaktívne vyžiada 3 z 5 unseal keys:

```text
Unseal key 1/3:
Unseal key 2/3:
Unseal key 3/3:
```

Po úspešnom unseal-e pokračuje automaticky. `sudo atlasctl openbao` je servisný access helper; nenahrádza štandardný deployment/unseal flow.

---

# OpenBao storage

OpenBao používa integrovaný **Raft storage**.

Persistentné dáta sú uložené v Docker volume a pretrvávajú medzi reštartmi kontajnera.

Fresh deploy flow po inicializácii explicitne čaká na Raft leader election pred vykonaním bootstrap operácií. Tým sa predchádza race condition, pri ktorej môže byť storage krátko po unseal-e read-only.

---

# OpenBao KV v2

NetBox ukladá iba explicitný `credential_profile`; username/password a SNMP secrets patria do OpenBao. Platformy, `DeviceType.default_platform`, audit nepokrytých typov a validácie popisuje [device model](docs/device-model.md). Oxidized reconcile z tohto modelu zostavuje úplný desired-state inventár.

Atlas používa KV v2 mount:

```text
atlas/
```

Device credential namespace (implementovaný dátový model; podrobnosti v [platform/credential kontrakte](docs/device-model.md)):

```text
atlas/
├── devices/
│   ├── credentials/
│       └── <manufacturer.slug>/
│           └── <credential_profile>/
│               ├── admin
│               └── snmp
│
├── netbox/
│   └── ...
├── checkmk/
│   └── ...
└── oxidized/
    └── ...
```

Význam:

- `atlas/devices/credentials/*` – spoločné credentials pre spravované zariadenia,
- `atlas/netbox/*` – interné NetBox secrets,
- `atlas/checkmk/*` – interné Checkmk secrets,
- `atlas/oxidized/*` – interné Oxidized secrets.

KV v2 nepoužíva klasické adresáre. Prefix sa v UI zobrazí až vtedy, keď pod ním existuje key alebo metadata. Fresh deployment preto bežne zobrazuje iba reálne použité prefixy, napríklad:

```text
atlas/
├── netbox/
└── oxidized/
```

`devices/` a `checkmk/` sa zobrazia až po prvom zápise zodpovedajúceho secretu. Policies a AppRoles samy o sebe KV paths nevytvárajú. Toto je očakávané správanie, nie chyba bootstrapu.

---

# OpenBao AppRoles

Beta verzia vytvára samostatné machine identities.

## `atlas-deployer`

Používa sa pre deployment a bootstrap. Má oprávnenia potrebné na čítanie a správu Atlas secrets.

## `atlas-operator`

Oddelená operator identita pre autentifikovaný `generate-root` workflow. Nepoužíva sa ako bežná aplikačná identita.

## `atlas-backend`

Runtime machine identita Atlas aplikácie. Samostatná policy `atlas-backend` povoľuje iba `read` pre presný NetBox token path `atlas/data/netbox/api` a device credentials pod `atlas/data/devices/credentials/*`. Neposkytuje `list`, zápis, mazanie, root operácie ani prístup k NetBox admin, PostgreSQL, Redis alebo deployer secretom.

Fresh OpenBao bootstrap túto policy, AppRole a machine identity vytvorí alebo zosúladí automaticky. Na už existujúcej inštalácii spusti `sudo atlasctl openbao` a zvoľ `2. Reconcile atlas-backend policy`. Operácia vyžaduje quorum unseal shares, použije krátkodobý temporary root iba pre tento reconcile, token nevypíše a po operácii sa ho pokúsi vždy revokovať.

## `netbox-runtime`

Runtime identita pripravená pre NetBox.

Policies:

```text
netbox-runtime
device-credentials-read
```

## `checkmk-runtime`

Runtime identita pripravená pre budúci Checkmk deployment.

Policies:

```text
checkmk-runtime
device-credentials-read
```

## `oxidized-runtime`

Runtime identita, ktorú Atlas reconcile používa na čítanie Oxidized/device secrets z OpenBao.

Policies:

```text
oxidized-runtime
device-credentials-read
```

Runtime policies sú navrhnuté ako read-only. Zápis a správa secrets zostáva deployment/Atlas aplikačnej vrstve.

---

# Shared device credentials policy

Policy:

```text
device-credentials-read
```

umožňuje runtime službám čítať spoločný strom:

```text
atlas/devices/credentials/*
```

Cieľom je, aby sa rovnaké credentials sieťových zariadení nemuseli duplikovať pre Checkmk, Oxidized a ďalšie Atlas aplikácie. App-specific secrets zostávajú oddelené.

Oxidized reconcile číta tento strom cez `oxidized-runtime`, v pamäti zostaví úplný desired-state inventár a atómovo nahradí ephemeral `router.json`.

---

# OpenBao audit logging

OpenBao audit device je v beta verzii zapnutý a smeruje na `stdout` kontajnera.

Audit log je dostupný cez Docker:

```bash
sudo docker compose \
  -f /opt/aricoma-atlas/deployment/openbao/docker-compose.yml \
  logs openbao
```

Posledných 100 riadkov:

```bash
sudo docker compose \
  -f /opt/aricoma-atlas/deployment/openbao/docker-compose.yml \
  logs --tail=100 openbao
```

Live sledovanie:

```bash
sudo docker compose \
  -f /opt/aricoma-atlas/deployment/openbao/docker-compose.yml \
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

Preferovaný servisný vstup:

```bash
sudo atlasctl openbao
```

Wrapper spúšťa interaktívny `openbao_access.py` v nainštalovanom Atlas prostredí.

Pre vývoj v source checkoute možno použiť:

```bash
python3 scripts/openbao_access.py
```

Helper aktuálne podporuje napríklad:

- vytvorenie krátkodobého Atlas deployer tokenu,
- zosúladenie `atlas-backend` policy, AppRole a lokálnej machine identity na existujúcej inštalácii,
- quorum-based temporary root workflow,
- revoke tokenu,
- inspect tokenu.

Temporary root token je vytvorený iba na zvolený čas a quorum-generated bootstrap root je po výmene okamžite revokovaný. Root prístup je break-glass mechanizmus, nie bežný deployment credential.

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
/opt/aricoma-atlas/.runtime/netbox-docker/docker-compose.override.yml
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

Cieľom je reprodukovateľnosť rovnakého Atlas release. Hodnota `network.listen_address` v tejto deklarácii opisuje NetBox runtime konfiguráciu; efektívne publikovanie host portu a izoláciu vždy over v generovanom Compose runtime a cez `docker ps`. Podporovaný externý vstup ostáva Nginx HTTPS.

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

Preferovaný nainštalovaný vstup:

```bash
sudo atlasctl deploy --prepare-only
```

Priamy vývojový vstup:

```bash
python3 scripts/deploy_atlas.py --prepare-only
```

OpenBao sa spustí a overí. NetBox, Oxidized, Atlas backend a Nginx pripravia runtime, Compose konfiguráciu a potrebné secrets bez štartu svojich aplikačných kontajnerov. Aj tento režim vyžaduje platnú proxy konfiguráciu a certifikáty.

Samostatne možno použiť:

```bash
python3 scripts/deploy_netbox.py --prepare-only
python3 scripts/deploy_oxidized.py --prepare-only
python3 scripts/deploy_backend.py --prepare-only
python3 scripts/deploy_proxy.py --prepare-only
```

---

# Verbose režim

Preferovaný vstup:

```bash
sudo atlasctl deploy --verbose
```

Priamy vývojový vstup:

```bash
python3 scripts/deploy_atlas.py --verbose
python3 scripts/deploy_netbox.py --verbose
```

---

# Opakované spustenie

Deployment je navrhnutý ako idempotentný:

```bash
sudo atlasctl deploy
```

Pri existujúcom deploymente má:

- znovu použiť OpenBao Raft storage,
- vyžiadať unseal shares iba ak je OpenBao sealed,
- overiť AppRole identities,
- ponechať existujúce secrets,
- znovu použiť NetBox volumes,
- overiť NetBox API token,
- zosúladiť Atlas baseline a Device Catalog,
- zachovať Oxidized inventory, `known_hosts` a Git volume,
- znovu použiť Oxidized web credentials,
- obnoviť proxy kontajner iba podľa potreby.

Secrets sa pri bežnom redeploymente neregenerujú. Zmena NetBox zariadenia alebo jeho OpenBao credentials sa aplikuje cez `sudo atlasctl oxidized reconcile`; príkaz vykoná atómový zápis a natívny `GET /reload` bez recreate alebo restartu kontajnera.

---

# Správa NetBox stacku

Celkový stav Atlasu:

```bash
sudo atlasctl status
```

Priama práca s NetBox runtime:

```bash
cd /opt/aricoma-atlas/.runtime/netbox-docker
```

Stav:

```bash
sudo docker compose -p atlas-netbox ps
```

Zastavenie/spustenie/reštart:

```bash
sudo docker compose -p atlas-netbox stop
sudo docker compose -p atlas-netbox start
sudo docker compose -p atlas-netbox restart
```

Odstránenie kontajnerov bez zmazania volumes:

```bash
sudo docker compose -p atlas-netbox down
```

Následné zosúladenie:

```bash
sudo atlasctl deploy
```

---

# Pozor na persistentné dáta

Pri bežnej správe nepoužívaj bez jasného dôvodu:

```bash
sudo docker compose -p atlas-netbox down -v
sudo docker volume prune
sudo docker system prune --volumes
```

Rizikové persistentné dáta zahŕňajú:

- OpenBao Raft storage,
- NetBox PostgreSQL a media volumes,
- Oxidized Git volume `atlas-oxidized-data`,
- `/run/atlas/oxidized/router.json` (ephemeral materializácia credentials),
- `/opt/aricoma-atlas/.runtime/oxidized/ssh/known_hosts`,
- site-specific proxy/TLS runtime.

Odstránenie OpenBao volume znamená stratu secrets a identity konfigurácie. Odstránenie NetBox PostgreSQL volume znamená stratu NetBox dát. Odstránenie Oxidized Git volume znamená stratu histórie konfigurácií, aj keď inventory súbor ostane na hoste.

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

Prvá reusable runtime collection vrstva pre CPU je popísaná v
[Profylaxia runtime dokumentácii](docs/prophylaxis-runtime.md). Podporuje
explicitné platformy `cisco-ios`, `cisco-ios-xe`, `cisco-cbs` a `fortios`,
načítava admin credentials cez existujúcu OpenBao AppRole vrstvu a vracia
secret-free normalizovaný výsledok. Threshold evaluation a scheduler zatiaľ nie
sú súčasťou tejto vrstvy. Manuálnu CPU kontrolu, lokálnu históriu a frontend
tlačidlo poskytuje Atlas Operations UI.

Pre lokálny development a test je host-key verification dočasne vypnutá cez
`ATLAS_SSH_STRICT_HOST_KEYS=false`. Produkčný backend deployment ho nastavuje
explicitne na `true` a zdieľa existujúci persistentný Oxidized `known_hosts`.

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
python3 scripts/build_catalog.py
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

Hlavný Python orchestrátor Atlasu. V nainštalovanom systéme ho volá:

```bash
sudo atlasctl deploy
```

Priamy vývojový vstup:

```bash
python3 scripts/deploy_atlas.py
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
Atlas backend
  ↓
Nginx
```

Atlas backend sa zostaví lokálne z rovnakého commitu, ktorý nasadil `install.sh`. Deployment ho publikuje iba na `127.0.0.1:8081` a pokračuje k Nginx až po úspešnom kontajnerovom healthchecku.

---

# `deploy_openbao.py`

Samostatný OpenBao deployment/bootstrap entrypoint:

```bash
python3 scripts/deploy_openbao.py
```

Zabezpečuje:

- Docker preflight,
- štart OpenBao,
- init 5/3,
- interaktívny unseal,
- Raft leader readiness,
- KV v2 mount,
- policies,
- AppRoles,
- machine identities,
- initial root revocation.

---

# `deploy_netbox.py`

Samostatný NetBox deployment entrypoint. Vyžaduje pripravený a unsealed OpenBao.

```bash
python3 scripts/deploy_netbox.py
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

# Oxidized

## Aktuálny model

Oxidized `0.37.0` beží v samostatnom Compose projekte a používa:

- runtime konfiguráciu z `/opt/aricoma-atlas/.runtime/oxidized/`,
- JSONFile source `/run/atlas/oxidized/router.json`,
- credentials načítané počas reconcile z OpenBao,
- persistentný a read-only pripojený SSH trust store,
- persistentný Git output v Docker volume,
- interný web/API listener na porte `8888`,
- Nginx ako jediný podporovaný externý HTTPS vstup,
- polling interval `3600` sekúnd.

Kontajner typicky nesie meno:

```text
atlas-oxidized-oxidized-1
```

## Prázdny inventár: `WAITING_FOR_INVENTORY`

Fresh deploy vytvorí prázdny:

```text
/run/atlas/oxidized/router.json
```

Súbor obsahuje `[]`. Prázdny inventár je podporovaný stav. Wrapper udrží kontajner a web/API zdravé, ale nespustí zariadeniové joby. Po prihlásení cez Nginx inventory endpoint vracia prázdny zoznam.

Tento stav znamená:

```text
backend pripravený
+
žiadne zariadenia na zber
≠
chyba deploymentu
```

Po pridaní prvého zariadenia spusti:

```bash
sudo atlasctl oxidized reconcile
```

Reconcile načíta zariadenia z NetBoxu, credentials z OpenBao, atómovo nahradí celý JSON súbor a až potom zavolá natívny Oxidized `GET /reload`. Príkaz nevykoná Compose `up`, recreate ani restart.

## Runtime a persistence

Host runtime:

```text
/opt/aricoma-atlas/.runtime/oxidized/
├── config
└── ssh/
    └── known_hosts

/run/atlas/oxidized/
└── router.json
```

Relevantné mounty:

```text
config       → /etc/oxidized/config
router.json  → /etc/atlas-oxidized/router.json:ro
ssh/         → /home/oxidized/.ssh/:ro
Git volume   → /home/oxidized/.config/oxidized/
```

Dôležité vlastnosti:

- `router.json` je ephemeral a po boote začína ako `[]`,
- reconcile ho vždy znovu zostaví z aktuálneho desired state,
- `known_hosts` je mimo kontajnera a zachováva sa pri recreate/redeployi,
- Git repository je v persistentnom Docker volume `atlas-oxidized-data`,
- `down -v`, volume prune alebo odstránenie volume zmaže Git históriu,
- runtime súbory sa nesmú commitovať do Atlas Git repozitára.

## Inventory formát

Inventár je JSON pole. Každý prvok obsahuje minimálne `name`, `ip`, `model`, `username` a `password`; voliteľne `enable`. Hodnoty sa nevypisujú do logov. Súbor je citlivý aj keď je ephemeral: neposielaj ho do ticketov ani diagnostických výstupov.

NetBox je zdroj identity zariadenia, management IP, platformy a príznaku `oxidized_enabled`. OpenBao je zdroj credentials. Neúplné enabled zariadenie alebo chýbajúci per-device secret sa uvedie v structured issues; ak už bolo zariadenie v current inventory, jeho posledná platná položka sa zachová. Globálne zlyhanie NetBoxu alebo OpenBao ukončí operáciu pred zápisom.

Súbor má mode `0640` a inventárový adresár `0750`; Oxidized proces používa GID hostiteľského inventárového adresára ako svoju primárnu runtime skupinu a mount zostáva read-only. Je to potrebné preto, že upstream image spúšťa aplikáciu cez `gosu oxidized`, ktoré zostaví skupiny procesu nanovo. Hostiteľský parent `/run/atlas` má mode `0710` a vlastní ho `root:atlas`. Adresárový mount zabezpečí, že atómová výmena súboru je po `GET /reload` viditeľná bez world-readable credentials.

## SSH host-key verification

Oxidized má v tejto beta konfigurácii vypnuté host-key verification (`secure: false`). Oxidized 0.37.0 preto nastaví Net::SSH `verify_host_key: :never`. Toto odstraňuje onboarding blokovaný neznámym alebo zmeneným kľúčom, ale zároveň neoveruje identitu SSH servera a zvyšuje riziko MITM útoku.

Persistentný súbor je naďalej pripravený na budúci návrat k `secure: true`:

```text
/opt/aricoma-atlas/.runtime/oxidized/ssh/known_hosts
```

Interaktívne SSH pripojenie používateľa zapisuje do `~/.ssh/known_hosts`; tým sa automaticky nepridá trust pre Oxidized.

Po opätovnom zapnutí `secure: true` použi tento acceptance flow:

1. zobraz kľúče, ktoré zariadenie aktuálne prezentuje:

```bash
sudo atlasctl oxidized host-key scan 10.200.200.1
```

2. fingerprint over nezávislým kanálom, napríklad v konzole zariadenia alebo v schválenej dokumentácii,
3. prijmi presný overený fingerprint:

```bash
sudo atlasctl oxidized host-key accept \
  10.200.200.1 SHA256:OVERENY_FINGERPRINT
```

Ak staršie zariadenie ponúka iba `diffie-hellman-group1-sha1`, bežný
`ssh-keyscan` kľúč nezíska. Pre takéto zariadenie použi explicitný legacy
režim pri oboch krokoch:

```bash
sudo atlasctl oxidized host-key scan 10.200.200.1 --legacy-ssh
sudo atlasctl oxidized host-key accept \
  10.200.200.1 SHA256:OVERENY_FINGERPRINT --legacy-ssh
```

Legacy režim povoľuje `diffie-hellman-group1-sha1`, `ssh-rsa` a `aes128-cbc`
iba v dočasnom procese na získanie host key. Automaticky ho nepridáva do
trvalého trust store a nemení predvolenú bezpečnostnú politiku.

4. spusti reconcile a over prvý backup:

```bash
sudo atlasctl oxidized reconcile
sudo atlasctl status
sudo atlasctl oxidized logs -f
```

Pri `secure: false` obsah `known_hosts` pripojenie neobmedzuje. Príkaz `scan` trust nemení a `accept` zostáva pripravený pre budúci návrat na strict verification.

## FortiGate a náhodne prešifrované ENC hodnoty

FortiOS môže pri každom výpise konfigurácie vrátiť inú zašifrovanú hodnotu `ENC`, aj keď sa význam konfigurácie nezmenil. Atlas preto nastavuje pre model `fortigate` vstavaný Oxidized režim `output_store_mode: on_significant`. Oxidized konfiguráciu naďalej načíta podľa intervalu alebo manuálneho requestu, ale nevytvorí novú Git verziu, ak model vyhodnotí, že sa zmenili iba nestabilné `ENC` hodnoty. Uložený backup zostáva úplný; nejde o redakciu secretov.

Kompromis tohto režimu: samostatná zmena hesla bez akejkoľvek inej významnej zmeny konfigurácie nevytvorí novú verziu. Nové zašifrované hodnoty sa uložia až s nasledujúcou významnou zmenou. Alternatíva `remove_secret: true` stabilizuje diff redakciou, ale backup už neobsahuje použiteľné secrets.

## Pridanie a odstránenie zariadenia

Pridanie:

1. v NetBoxe nastav zariadenie do stavu `active`, primary management IP, podporovanú platformu, `credential_profile` a `oxidized_enabled=true`,
2. ulož príslušné credentials do OpenBao,
3. spusti `sudo atlasctl oxidized reconcile`,
4. over `/nodes.json`, log a prvý commit v Oxidized Git histórii.

Odstránenie:

1. nastav `oxidized_enabled=false`, zmeň stav zariadenia z `active` na neaktívny stav alebo zariadenie odstráň z NetBoxu,
2. spusti `sudo atlasctl oxidized reconcile`,
3. over, že zariadenie už nie je v `/nodes.json`.

Výstup má `clean_obsolete_nodes: false`. Odstránenie zariadenia z inventára preto nemaže jeho existujúci konfiguračný súbor ani Git históriu. Mazanie persistentného Docker volume je samostatná deštruktívna operácia.

## Legacy SSH zariadenia

Pri testovanom legacy zariadení boli potrebné algoritmy:

```bash
ssh \
  -oKexAlgorithms=+diffie-hellman-group1-sha1 \
  -oHostKeyAlgorithms=+ssh-rsa \
  -oCiphers=+aes128-cbc \
  admin@10.200.200.1
```

Tieto algoritmy sú zastarané. Používaj ich iba pre explicitne identifikovaný legacy profil a obmedzené management siete. Globálne oslabovanie SSH policy pre všetky zariadenia nie je akceptovateľné.

## Polling a manuálny trigger

Aktuálna konfigurácia používa:

```yaml
interval: 3600
```

Oxidized teda plánuje automatický zber približne raz za hodinu. Inventory reload nemení tento interval. API endpoint na vyžiadanie ďalšieho jobu je vhodný na acceptance test, nie ako náhrada normálneho schedulera.

## Prevádzkové príkazy

```bash
sudo atlasctl status
sudo atlasctl oxidized reconcile
sudo atlasctl oxidized logs --tail=100
sudo atlasctl oxidized logs -f
```

Do kontajnera vstupuj iba pri diagnostike. Bežné pridanie zariadenia nesmie vyžadovať manuálnu editáciu súborov vo vnútri kontajnera.

## `deploy_oxidized.py`

Priamy vývojový entrypoint:

```bash
python3 scripts/deploy_oxidized.py
python3 scripts/deploy_oxidized.py --prepare-only
```

Pripravuje pripnutý kontajner podľa `deployment/oxidized.yaml`, internú sieť, runtime mounty a persistentný Git volume. Plný deploy zostaví inventár z NetBoxu a OpenBao pred štartom služby. Prepínač `--reconcile-only` je prevádzková cesta bez Compose redeployu a používa ho `atlasctl`; volá rovnakú `OxidizedSyncService` ako API, nie druhú implementáciu diffu alebo resolution logiky.

## Clean-install acceptance checklist

Tento checklist spusti až po review, commite a úspešnom CI na čistej podporovanej Ubuntu VM:

1. nainštaluj Atlas cez `sudo ./install.sh` a over `sudo atlasctl status`,
2. priprav site-specific proxy/TLS konfiguráciu a spusti `sudo atlasctl deploy`,
3. bezpečne ulož 5 OpenBao unseal shares mimo Atlas servera; threshold je 3,
4. s prázdnym NetBox desired state over autentizovaný `WAITING_FOR_INVENTORY` a `[]`,
5. pridaj jedno testovacie zariadenie, jeho OpenBao secret a overený SSH host key,
6. spusti reconcile, over zariadenie v `/nodes.json`, backup a prvý Git commit,
7. pridaj druhé zariadenie a over add bez recreate/restartu kontajnera,
8. zakáž alebo odstráň prvé zariadenie, spusti reconcile a over, že zmizlo z nodes, ale jeho Git história zostala,
9. zopakuj `sudo atlasctl deploy` a over zachovanie secrets, trustu a Git histórie,
10. reštartuj VM, odomkni OpenBao cez `sudo atlasctl deploy` a over, že ephemeral inventár sa znovu vytvoril z NetBoxu a OpenBao.

Clean-install test zatiaľ nie je v tomto pracovnom strome vykonaný; je posledným acceptance testom po uzavretí a review všetkých P0.

---

# `deploy_proxy.py`

```bash
python3 scripts/deploy_proxy.py
python3 scripts/deploy_proxy.py --prepare-only
```

Vyžaduje platnú proxy konfiguráciu a pripravené OpenBao. Vygeneruje alebo znovu použije secret `atlas/oxidized/web`, vytvorí Nginx runtime a overí konfiguráciu pred štartom.

Proxy je pripojený na webové siete backendov, nie na NetBox databázovú sieť. Pri redeployi sa proxy kontajner obnoví, aby načítal aktuálne certifikáty a hash; počas tohto kroku môže krátko vypadnúť webový prístup.

Obnova certifikátu:

1. obnov zdrojové PEM súbory uvedené v `/opt/aricoma-atlas/.runtime/proxy.yaml`,
2. spusti `sudo atlasctl deploy` alebo priamo `python3 scripts/deploy_proxy.py` vo vývojovom prostredí.

Rotácia Oxidized web hesla: oprávnený správca aktualizuje `password` na `atlas/oxidized/web` a následne spustí proxy/deployment reconcile. Automatické vydávanie a obnova certifikátov nie sú implementované.

---

# `bootstrap_netbox.py`

Použi, keď NetBox už existuje a chceš iba aplikovať Atlas konfiguráciu.

Dry-run:

```bash
python3 scripts/bootstrap_netbox.py
```

Apply:

```bash
python3 scripts/bootstrap_netbox.py --apply
```

Pri `--apply` sa vykoná:

```text
prepare / validate catalog
        ↓
Atlas baseline
        ↓
Atlas Device Catalog
        ↓
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
python3 scripts/bootstrap_netbox.py
```

Apply:

```bash
python3 scripts/bootstrap_netbox.py --apply
```

Pri štandardnom `atlasctl deploy` sa NetBox API token automaticky vytvára, validuje a ukladá do OpenBao na:

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
python3 scripts/bootstrap_netbox.py --rebuild-catalog
```

alebo:

```bash
python3 scripts/build_catalog.py
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
python3 scripts/audit_catalog.py
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
python3 scripts/audit_component_fields.py
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
        ↓
lokálna validácia
        ↓
fresh test alebo dry-run
        ↓
apply na test prostredí
        ↓
reconcile + opakovaný run / idempotencia
        ↓
git diff
        ↓
commit
        ↓
push
        ↓
GitHub Actions
```

Pred commitom:

```bash
python3 -m compileall -q atlas scripts tests
python3 -m unittest discover -s tests -v
git status
git diff --cached --check
git diff --cached --stat
```

Oxidized zmeny overuj minimálne v dvoch stavoch:

1. prázdny inventár → zdravý `WAITING_FOR_INVENTORY`,
2. platný inventár → reload, SSH trust enforcement, backup a Git commit.

Release test má navyše potvrdiť redeploy/reboot persistence runtime a dát.

---

# GitHub Actions

Repozitár obsahuje:

```text
.github/workflows/validate.yml
```

CI sa spúšťa pri pushi a pull requeste. Aktuálna validácia môže zahŕňať:

- Python syntax,
- YAML konfiguráciu,
- Atlas manifest,
- baseline deklarácie,
- pinned Device Type Library checkout,
- build Atlas Device Catalogu,
- audit katalógu,
- audit component fields,
- ingress a proxy testy,
- Oxidized empty-inventory a persistence scenáre.

CI nesmie vyžadovať produkčné secrets.

Ingress/Oxidized testy majú pokrývať:

- validáciu proxy konfigurácie, certifikátu, názvu a private key,
- zachovanie web hesla pri redeployi,
- HTTPS s dočasnou CI CA bez vypnutia certificate verification,
- odmietnutie zlého hesla pre Oxidized web/API,
- skutočný NetBox login POST cez HTTPS vrátane CSRF,
- neprítomnosť publikovaného Oxidized host portu,
- prázdny inventár: zdravý waiting backend a stále povinná proxy autentifikácia,
- klienta mimo povolenej podsiete aj pri podvrhnutom `X-Forwarded-For`,
- zachovanie Oxidized Git commit-u, runtime a web credentials pri redeployi,
- upgrade staršej Atlas inštalácie bez straty NetBox dát.

`scripts/ci_ingress.py` vytvára certifikáty iba pre izolovaný CI runner. Jeho DNS mená, CIDR rozsahy a krátka platnosť sa nepoužívajú pri zákazníckom nasadení. Pre natívne Oxidized fixture možno použiť `interval: 0`, aby sa nespúšťali reálne SSH zbery.

Lokálne testy bez Dockeru:

```bash
python3 -m unittest discover -s tests -v
```

---

# Versioning

Projekt používa Git tagy na release verzie.

Prvá beta verzia:

```text
v0.1.0-beta
```

Aktuálny tag:

```text
v0.2.0-beta
```

Tag sa vytvára až po úspešnom clean-install a release-gate teste. Release reprezentuje konkrétnu kombináciu:

```text
Atlas installer + deployment code
        +
OpenBao configuration and policies
        +
NetBox baseline and catalog manifest
        +
pinned Device Type Library
        +
pinned NetBox Docker/image
        +
pinned Oxidized image/configuration
        +
Atlas FastAPI backend image/configuration
        +
Nginx ingress configuration
```

Cieľom je reprodukovateľnosť konkrétneho release.

Odporúčaný release postup:

```bash
git status
python3 -m unittest discover -s tests -v
git tag -a v0.2.0-beta -m "Aricoma Atlas v0.2.0-beta"
git push origin v0.2.0-beta
```

Tag nevytváraj, ak working tree nie je čistý alebo ak reálny Oxidized backup/Git persistence test ešte neprešiel.

---

# Historický scope tagu `v0.1.2-beta`

Nasledujúci zoznam opisuje už vydaný tag, nie aktuálny pracovný strom. `v0.1.2-beta` zahŕňa:

- `install.sh` a nainštalovaný strom `/opt/aricoma-atlas`,
- `deploy.sh` a administračný wrapper `atlasctl`,
- OpenBao deployment s Raft storage,
- Shamir 5/3 unseal a initial root revocation,
- KV v2 mount `atlas/` a lazy namespace creation,
- OpenBao audit logging,
- `atlas-deployer`, `atlas-operator`, `netbox-runtime`, `checkmk-runtime` a `oxidized-runtime` AppRoles,
- shared `device-credentials-read` policy,
- NetBox `4.7.1` cez NetBox Docker `5.1.1`,
- PostgreSQL + Valkey,
- OpenBao-backed NetBox secrets,
- automatický NetBox superuser a API token,
- Atlas baseline, 1 Choice Set a 4 Custom Fields,
- Cisco, Fortinet a Juniper Device Types,
- 690 Device Types a 26 342 component templates,
- Oxidized `0.37.0`,
- zdravý `WAITING_FOR_INVENTORY` stav,
- persistentný CSV inventory, SSH trust a Git história,
- complete CSV mapping vrátane `username`, `password` a `enable`,
- strict SSH host-key verification,
- reconcile/reload cez `atlasctl`,
- hodinový polling (`interval: 3600`),
- Nginx HTTPS proxy s internou CA a CIDR allowlistom,
- OpenBao-backed Basic Auth pre Oxidized web/API,
- idempotentný provisioning a configuration drift detection,
- clean-install flow testovaný pri reštriktívnom `umask 077`.

Nie je súčasťou tejto beta verzie:

- automatická synchronizácia NetBox → Oxidized,
- automatická distribúcia zariadeniových credentials z OpenBao do Oxidized,
- automatické host-key approval bez zásahu administrátora,
- Checkmk deployment,
- auto-unseal,
- produkčný SSO/RBAC pre Oxidized web/API.

---

# Pred produkciou

Pred označením Atlas deploymentu ako produkčného je potrebné minimálne doriešiť:

- TLS pre OpenBao a odstránenie `tls_disable`,
- CA trust a certificate verification vo všetkých klientoch,
- lifecycle certifikátov a overenie proxy/firewall izolácie,
- persistentný audit-log destination, rotáciu a forwarding,
- backup/restore OpenBao Raft storage,
- backup/restore NetBox PostgreSQL a media dát,
- backup/restore Oxidized Git volume a runtime trustu,
- dependency/version pinning pre produkčný Python runtime,
- hardening Docker hosta a filesystem permissions,
- formálny rotation/runbook proces pre už schválené SSH host keys,
- explicitné per-platform legacy SSH profily,
- individuálne prístupy, RBAC alebo SSO pre web/API,
- opakovateľný clean-install, upgrade, reboot a disaster-recovery test.

Auto-unseal nie je súčasťou beta verzie. Aktuálny recovery model zámerne používa manuálny 3-of-5 unseal.

---

# Ďalší plánovaný rozvoj

Ďalšie fázy projektu:

- aplikačná logika nad existujúcim Atlas FastAPI skeletonom,
- GUI/API nad existujúcim výberom NetBox zariadení a reconcile logikou,
- pending-approval evidencia a riadená rotácia SSH host keys,
- automatická validácia prvého backupu po schválení zariadenia,
- Checkmk deployment a provisioning,
- napojenie Checkmk/Oxidized na spoločné device credentials,
- individuálne prístupy alebo SSO pre proxy,
- centrálne logging/monitoring,
- zákaznícke deployment profily,
- produkčný TLS hardening.

---

# Cieľ projektu

Dlhodobý cieľ:

```text
nový Linux server
        ↓
Git clone aricoma-atlas
        ↓
site-specific TLS + proxy.yaml
        ↓
sudo ./install.sh
        ↓
sudo ./deploy.sh
        ↓
OpenBao
  ├── secrets
  ├── identities
  └── audit
        +
NetBox
  ├── source of truth
  ├── Atlas baseline
  └── Device Catalog
        +
Atlas application
  ├── device onboarding
  ├── credential orchestration
  ├── SSH trust workflow
  └── integration control
        +
Oxidized
  ├── inventory reconcile
  ├── configuration collection
  └── Git history
        +
Checkmk and additional integrations
```

Aktuálna beta už pokrýva základ:

```text
installer + atlasctl
   +
OpenBao
   +
NetBox deployment and bootstrap
   +
Oxidized waiting/collection runtime and Git persistence
   +
Nginx HTTPS and protected Oxidized web/API
   +
prepared runtime identities for future services
```

Kľúčová hranica návrhu:

```text
site-specific inputs
        ↓
reproducible Atlas deployment
        ↓
Atlas application automates operational onboarding
```

---
