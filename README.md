# Aricoma Atlas

Automatizované nasadenie a provisioning infraštruktúry pre projekt **Aricoma Atlas**.

Aktuálna beta baseline nasadzuje a pripravuje:

- **OpenBao 2.7.0** ako centrálny secrets backend,
- **NetBox 4.7.1** cez pripnutý `netbox-docker` runtime,
- voliteľný **Oxidized 0.37.0** s persistentnou Git históriou konfigurácií,
- voliteľný **Zabbix 7.0.31 LTS** ako monitoring backend s oddeleným PostgreSQL,
- **Atlas FastAPI backend 0.4.2** zostavený a spustený v samostatnom kontajneri,
- plánovaný a potvrdený [Ansible monitoring provisioning](docs/monitoring-provisioning.md)
  pre FortiOS, Cisco IOS/IOS-XE, Cisco CBS a Aruba AOS-CX/AOS-Switch,
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
sudo atlasctl configure
sudo atlasctl deploy
```

`install.sh` pripraví nainštalovaný strom v `/opt/aricoma-atlas`, privátny runtime a administračný wrapper. `deploy.sh` následne spustí deployment z nainštalovaného stromu. Po inštalácii sa bežná prevádzka vykonáva cez:

```bash
sudo atlasctl configure
sudo atlasctl deploy
sudo atlasctl status
sudo atlasctl openbao
sudo atlasctl oxidized reconcile
sudo atlasctl oxidized logs -f
sudo atlasctl zabbix status
sudo atlasctl zabbix sync
sudo atlasctl zabbix credentials
sudo atlasctl zabbix logs -f
sudo atlasctl integrations cisco test
sudo atlasctl prophylaxis logs -f
sudo atlasctl backend logs -f
sudo atlasctl proxy logs -f
```

Pred prvým deploymentom `atlasctl configure` vytvorí z jedného validovaného
vstupu chránené `proxy.yaml`, TLS a monitoring policy súbory. DNS záznamy a
produkčné certifikáty zostávajú vstupom zákazníckeho prostredia. Priame
`python3 scripts/*.py` entrypointy zostávajú dostupné najmä pre vývoj a
diagnostiku.

---

# Aktuálny stav

**Verzia tejto dokumentácie a backendu:** `v0.4.2`

Zmeny oproti `v0.4.1-beta` zahŕňajú kategorizované Cisco IOS/CBS profylaxie,
Check Point Gaia zber, kontrolu sériového čísla a modelu, grafy zo Zabbixu a
monotónny výber voliteľných komponentov pri first boot. Podrobný prehľad je v
[CHANGELOG.md](CHANGELOG.md).

V monitoring UI tlačidlo **Plan all** pripraví prehľad plánovaných zmien po
zariadeniach. Zápisy sa spustia až po samostatnom potvrdení; chyba pri príprave
niektorého plánu zablokuje hromadné vykonanie. Stav `COMPLETED` s verifikáciou
`PENDING` znamená dokončenú konfiguráciu a čakanie na čerstvú SNMP vzorku zo Zabbixu.

Pre CBS250/350 nastav na zariadení v NetBoxe platformu `cisco-cbs250-350`:
tá používa vlastný template `Aricoma Atlas Cisco CBS250-350 by SNMP`.
Platforma `cisco-cbs` zostáva generická pre CBS220/legacy CBS. Bootstrap nastavuje
predvolenú platformu Device Types; existujúce Devices automaticky nemigruje.
RAM CBS350 a systémové metriky testovanej Aruba CX OVA zostávajú bez overeného
SNMP kontraktu; hranice sú uvedené v [dokumentácii systémových metrík](docs/prophylaxis-system-metrics.md).

Fresh VM konfiguráciu a výber zdrojovej SNMP IP pri viacerých rozhraniach opisuje
[site configuration](docs/site-configuration.md). Spoločný backend operator token
sa uchováva v OpenBao na `atlas/backend`; zariadeniové SNMP secrets používajú
`devices/credentials/<vendor>/<device-name>/snmp`. Podrobnosti vrátane migrácie
sú v [monitoring provisioning](docs/monitoring-provisioning.md).

Táto dokumentácia opisuje aktuálny pracovný strom. Historický scope už vydaného tagu `v0.1.2-beta` je uvedený samostatne nižšie.

Definovaný clean-install flow zahŕňa:

- prípravu zákazníckeho alebo LAB TLS vstupu,
- import lokálnej `.runtime/` konfigurácie cez `install.sh`,
- deployment z `/opt/aricoma-atlas` cez `deploy.sh`/`atlasctl`,
- OpenBao init, Shamir 5/3 unseal, Raft readiness a bootstrap,
- zdravý NetBox stack a idempotentný NetBox bootstrap,
- zdravý Oxidized backend s prázdnym inventárom,
- zdravý Zabbix API/server/web/database stack,
- waiting stránku namiesto chyby backendu pri nulovom inventári,
- reload inventára cez `atlasctl oxidized reconcile`,
- vedome vypnuté Oxidized SSH host-key verification (`secure: false`),
- persistentný Oxidized `known_hosts` a Git runtime,
- polling interval `3600` sekúnd.

Release gate vyžaduje na cieľovej VM aspoň jeden úspešný reálny backup zariadenia, vznik Git verzie a zachovanie stavu po redeployi/reštarte.

Deklarovaná kombinácia pre tento deployment:

- OpenBao `2.7.0`
- NetBox `4.7.1`
- NetBox Docker `5.1.1`
- NetBox image `docker.io/netboxcommunity/netbox:v4.7.1-5.1.1`
- Oxidized image `docker.io/oxidized/oxidized:0.37.0`
- Zabbix server/web `alpine-7.0.31`
- Zabbix PostgreSQL `16.15-alpine3.24`
- Atlas backend image `aricoma-atlas-backend:0.4.2`
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
| Sophos | 21 |
| HPE Aruba switches | 109 |
| **Spolu** | **820** |

## Component templates

| Typ komponentu | Počet |
|---|---:|
| Console ports | 1 111 |
| Console server ports | 5 |
| Power ports | 456 |
| Interfaces | 27 545 |
| Rear ports | 2 |
| Front ports | 0 |
| Module bays | 1 962 |
| Device bays | 2 |
| Power outlets | 4 |
| **Spolu** | **31 087** |

---

# Beta security status

Táto verzia je **beta baseline**, nie finálny produkčný hardening.

## HTTPS vstup a zostávajúce TLS obmedzenia

V aktuálnom beta prostredí:

- OpenBao API používa lokálne HTTP,
- OpenBao listener má `tls_disable = true`,
- OpenBao API je bindnuté na loopback adresu,
- NetBox, Oxidized a Zabbix UI sú publikované cez spoločný Nginx a HTTPS,
- Atlas backend je publikovaný iba na loopback adrese `127.0.0.1:8081`,
- Atlas Operations UI nie je pripojené do Nginx, pretože používateľská autentifikácia je rozsah nasledujúceho milestone,
- Oxidized web/API nemá publikovaný host port,
- Nginx vyžaduje konkrétnu management IP a explicitný CIDR allowlist,
- Nginx → backend komunikácia prebieha cez HTTP v oddelených lokálnych Docker sieťach,
- Oxidized web/API je chránený OpenBao-backed Basic Auth,
- Oxidized má v tejto beta konfigurácii vedome nastavené `secure: false`; SSH host key sa preto neoveruje,
- Atlas Profylaxia Ansible SSH má v beta deployment profile vedome nastavené
  `ssh_strict_host_keys: false`; týka sa to Cisco aj Aruba kolektorov,
- persistentný `known_hosts` a fingerprint príkazy sú iba diagnostické; pri `secure: false` pripojenie neobmedzujú.

Oxidized inventory je JSON pole generované Atlasom z NetBoxu a OpenBao. Zariadeniové credentials sa materializujú iba do ephemeral súboru `/run/atlas/oxidized/router.json`, ktorý zostáva mimo Git a je do kontajnera pripojený read-only.

Pred produkčným nasadením je potrebné doplniť minimálne:

1. TLS pre OpenBao,
2. dôveryhodný CA trust a certificate verification v Python klientoch,
3. odstránenie `tls_disable`,
4. lifecycle a obnovu certifikátov,
5. backup/restore OpenBao, NetBox, Zabbix PostgreSQL a Oxidized Git dát,
6. definovaný host-key rotation proces so samostatným schválením,
7. individuálne roly alebo SSO pre administratívny web/API prístup.

Produkčná verzia nesmie používať `verify=False` ako náhradu za správne overenie TLS certifikátu. Vypnuté Oxidized a Profylaxia SSH host-key verification sú samostatné, explicitne zdokumentované bezpečnostné výnimky.

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
    │      ├── host-key verification vypnuté (`secure: false`)
    │      └── web/API iba v internej Docker sieti
    │
    ├── 4. Zabbix
    │      ├── server, web a samostatný PostgreSQL
    │      ├── OpenBao credentials a API integrácia
    │      └── import Atlas-managed templates
    │
    ├── 5. Atlas backend
    │      ├── lokálny build z pripnutého checkoutu
    │      ├── non-root kontajner s read-only root filesystemom
    │      ├── healthcheck GET /api/health
    │      └── host bind iba 127.0.0.1:8081
    │
    └── 6. Nginx
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
│   │   ├── oxidized.py
│   │   ├── zabbix.py
│   │   └── zabbix_sync.py
│   ├── integrations/
│   │   └── zabbix/
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
│   ├── zabbix.yaml
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
│   ├── zabbix.yaml
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
│           └── oxidized-runtime.hcl
│
├── scripts/
│   ├── deploy_atlas.py
│   ├── deploy_openbao.py
│   ├── deploy_netbox.py
│   ├── deploy_oxidized.py
│   ├── deploy_zabbix.py
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
├── update.sh
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

Atlas podporuje dva súvisiace kontexty:

1. **bootstrap input v source checkoute** – `~/aricoma-atlas/.runtime/`,
2. **kanonický nainštalovaný runtime** – `/opt/aricoma-atlas/.runtime/`.

Odporúčaný fresh-install flow vytvorí site-specific súbory až v nainštalovanom
runtime cez `sudo atlasctl configure`. Source `.runtime/proxy.yaml` a TLS súbory
zostávajú podporované ako kompatibilný pre-install import. Bežná prevádzka a
`atlasctl` pracujú s `/opt/aricoma-atlas/.runtime/`.

Typický nainštalovaný runtime:

```text
/opt/aricoma-atlas/.runtime/
├── proxy.yaml
├── tls/
│   ├── ca.crt
│   ├── netbox.crt
│   ├── netbox.key
│   ├── oxidized.crt
│   ├── oxidized.key
│   ├── zabbix.crt
│   └── zabbix.key
├── monitoring/
│   ├── config.yaml
│   ├── operator-token
│   ├── jobs/
│   │   └── jobs.sqlite3
│   └── trust/
├── proxy/
│   └── oxidized.htpasswd
├── openbao-approle.json
├── openbao-operator.json
├── openbao-backend.json
├── openbao-netbox.json
├── openbao-oxidized.json
├── netbox-docker/
├── backend/
│   └── docker-compose.yml
├── zabbix/
│   ├── docker-compose.yml
│   └── secrets/               # 0600, mimo Git
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

- Ubuntu 24.04 alebo 26.04 so `sudo`,
- Git,
- Python 3.12 alebo novší a modul `venv`,
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

Projekt vyžaduje Python 3.12 alebo novší; túto hranicu určuje najprísnejšia pripnutá runtime závislosť (`ansible-core 2.21.5`), nie iba FastAPI. V dokumentácii a skriptoch používaj explicitne `python3`. Na staršom systémovom Pythone v macOS môže `urllib3` zobrazovať `NotOpenSSLWarning` kvôli LibreSSL; nejde o podporované release prostredie a produkcia musí používať podporovaný Python/OpenSSL stack.

## Atlas backend API — Milestone 1

Pri štandardnom nasadení `sudo atlasctl deploy` zostaví image z aktuálneho nainštalovaného Git checkoutu, spustí backend ako piatu zo šiestich fáz a počká na stav `healthy`. Konfigurácia image, portu a timeoutu je v `deployment/backend.yaml`; generovaný Compose runtime je v `.runtime/backend/docker-compose.yml`.

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
{"status":"ok","service":"Aricoma Atlas","version":"0.4.2","environment":"development"}
```

Konfigurácia používa environment premenné s prefixom `ATLAS_`. Bezpečné lokálne hodnoty sú zdokumentované v `.env.example`; reálny `.env` zostáva mimo Git. Premenné Milestone 1 sú `ATLAS_APP_NAME`, `ATLAS_APP_VERSION`, `ATLAS_ENVIRONMENT`, `ATLAS_API_PREFIX` a `ATLAS_LOG_LEVEL`.

`GET /api/health` zostáva iba liveness kontrola procesu. Nekontroluje NetBox, OpenBao, Oxidized ani Zabbix.

## Atlas backend API — Milestone 2

Milestone 2 pridáva bezpečnú read-only dependency vrstvu a samostatný readiness endpoint. Kontrakty endpointov sú zámerne oddelené:

| Endpoint | Význam | HTTP odpoveď |
| --- | --- | --- |
| `GET /api/health` | proces FastAPI beží | `200` bez ohľadu na stav externých služieb |
| `GET /api/ready` | OpenBao, NetBox, Oxidized a Zabbix sú použiteľné | `200` pri úspechu, inak `503` |

Readiness overuje, že OpenBao je dostupné, inicializované a odomknuté a že funguje AppRole login identity `atlas-backend`. Následne cez tokeny uložené výhradne v OpenBao vykoná lacný read-only NetBox request, načíta Oxidized `nodes.json` a overí Zabbix `apiinfo.version` aj autorizovaný `host.get`. Prázdne Oxidized pole `[]` je pripravený stav, nie chyba. Kontrola nevykonáva žiadny zápis.

Bezpečné dependency nastavenia:

```text
ATLAS_OPENBAO_URL
ATLAS_OPENBAO_IDENTITY_FILE
ATLAS_NETBOX_URL
ATLAS_OXIDIZED_URL
ATLAS_ZABBIX_URL
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

Od Milestone 4 má backend kontajner read-only mount AppRole identity, read-write mount výhradne na ephemeral Oxidized inventory adresár a pripojenie do štyroch interných aplikačných sietí vrátane Zabbix API siete. Neobsahuje Docker socket a beží pod numerickým UID/GID neprivilegovaného Atlas service accountu. Kontajnerový healthcheck naďalej používa lacný `/api/health`; dependency readiness je samostatný endpoint.

## Atlas backend API — Milestone 3

Milestone 3 pridáva prvú zápisovú orchestration operáciu:

```http
POST /api/oxidized/sync
```

Atlas načíta cez paginovaný NetBox API iba zariadenia so stavom `active` a s `oxidized_enabled=true`, striktne overí meno, primary IP, výrobcu, platform mapping a `credential_profile`, a cez AppRole `atlas-backend` načíta výhradne `oxidized` credentials z OpenBao. Zariadenie v inom stave (napríklad `offline`) sa do desired-state inventára nezaradí. IPv4 aj IPv6 prefix sa pred odovzdaním Oxidized odstráni. Neexistuje fallback platformy podľa výrobcu ani fallback z `oxidized` na `admin` credentials.

Cisco platformy `cisco-cbs` a `cisco-cbs250-350` používajú explicitný Oxidized model `ciscosmb` a credential namespace `atlas/devices/credentials/cisco/<credential_profile>/oxidized`. Mapovanie je experimentálne, kým neprejde backup a restore kontrolou na fyzických CBS220/250/350 zariadeniach; nepoužíva fallback na model `ios`.

Check Point platforma `checkpoint-gaia` používa model `gaiaos` z pripnutého Oxidized 0.37.0. `checkpoint-gaia-embedded` zostáva bez Oxidized mapovania, kým sa celý príkazový kontrakt neoverí na fyzickom Spark zariadení.

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

- aplikačný stav Atlasu, OpenBao, NetBoxu, Oxidized a Zabbixu,
- porovnanie NetBox-enabled zariadení s aktuálnym runtime inventárom,
- bezpečný zoznam zariadení obsahujúci iba `name`, `ip` a `model`,
- explicitnú operáciu **Sync inventory**,
- výsledok posledného syncu v aktuálnej browser session,
- explicitnú operáciu **Queue backup** iba pre zariadenie z runtime inventára,
- operátorskú operáciu **Run Prophylaxis**, ktorá pre aktívne zariadenia s
  `profylaxia_enabled=true` asynchrónne rozbalí skupiny vybrané v
  `profylaxia_checks` a spustí podporované platformové collectory,
- priebežný stav behu a súhrn `success`, `failure` a `skipped`,
- pôvodný jednotkový CPU endpoint a lokálnu SQLite históriu pre kompatibilitu.

Každé tlačidlo volá konkrétny Atlas API endpoint a následne shared Python service. UI nespúšťa shell, `sudo` ani `atlasctl`; backend nemá Docker socket a neposkytuje deploy, OpenBao admin helper ani raw log viewer.

HTML aj statické JavaScript/CSS assets sa servujú s `Cache-Control: no-store`.
Asset URL obsahuje release cache-busting identifikátor, aby nový HTML layout po
deployi nikdy nepoužil starý JavaScript z browser cache.

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
| `POST /api/prophylaxis/runs` | Po autorizácii operator tokenom vytvorí asynchrónny beh kontrol vybraných v NetBoxe a vráti `202` so stavom `PENDING`. |
| `GET /api/prophylaxis/runs/{run_id}` | Po autorizácii vráti stav, súhrn a normalizované výsledky konkrétneho behu. |
| `GET /api/prophylaxis/runs/{run_id}/charts/{artifact}` | Po autorizácii vráti lokálne vygenerovaný SVG graf uvedený v poli `charts`. |

Profylaxia história je SQLite databáza v kontajneri na
`/run/atlas/prophylaxis/results.sqlite3`. Deployment pripája persistentný host
adresár `/opt/aricoma-atlas/.runtime/prophylaxis/`, takže výsledky prežijú
reštart alebo výmenu backend kontajnera. Databáza má režim `0600`, adresár
`0700` a predvolený retention limit 10 000 výsledkov. Najstaršie záznamy sa po
prekročení limitu odstránia. Neukladá sa username, password, OpenBao token ani
raw príkazový výstup.

Každý hromadný beh vybraných kontrol má samostatný adresár
`/run/atlas/prophylaxis/runs/<run_id>/` s atomicky aktualizovaným
`result.json`, čitateľným `report.txt` a voliteľnými Zabbix SVG grafmi v
`charts/`. CPU graf sa vytvorí iba vtedy, keď Atlas-managed Zabbix host s
rovnakým `netbox_id` poskytne numerickú CPU históriu. Nedostupný graf nemení
výsledok profylaxie. Adresáre majú režim `0700` a súbory
`0600`. Artefakty obsahujú iba identitu zariadenia, management IP, platformu,
ID kontroly, normalizované hodnoty a bezpečné chybové kódy.
Credentials ani raw CLI výstup sa do nich neukladajú. Maximálny počet súbežne
spracovaných zariadení nastavuje `backend.prophylaxis_concurrency` v
`deployment/backend.yaml` (predvolene `4`).

`result.json` zachováva ploché pole `results` pre existujúcich API klientov a
zároveň poskytuje odvodené pole `devices`. Každá položka `devices` obsahuje
jednoznačnú identitu zariadenia, jeho vlastný súhrn, pole `checks` a príslušné
Zabbix grafy. Hodnoty v oboch pohľadoch pochádzajú z rovnakých normalizovaných
výsledkov; `devices` sa pri načítaní znovu zostaví z kanonického `results`.

### Manuálny test Profylaxia flow

Najprv over, že browser dostáva aktuálny JavaScript a nie starú cache:

```bash
curl -sSI 'http://127.0.0.1:8081/static/app.js?v=20261020-1' \
  | grep -i '^cache-control:'
```

Očakávaná hodnota je `Cache-Control: no-store`. Pôvodný CPU flow možno naďalej
overiť podľa NetBox ID:

```bash
curl -sS http://127.0.0.1:8081/api/prophylaxis/devices \
  | python3 -m json.tool

curl -sS -X POST \
  http://127.0.0.1:8081/api/prophylaxis/devices/DEVICE_ID/checks/cpu \
  | python3 -m json.tool

curl -sS 'http://127.0.0.1:8081/api/prophylaxis/results?limit=5' \
  | python3 -m json.tool
```

Pre hromadný flow musí mať cieľ v NetBoxe stav `active` a
`profylaxia_enabled=true`. Atlas načíta skupiny vo výbere
`profylaxia_checks`, rozbalí ich na interné výsledky; implementované collectory
spustí raz a nepodporované výsledky zaznamená ako `skipped`. Používa rovnaký
runtime operator token ako Monitoring Provisioning:

```bash
read -rsp 'Atlas operator token: ' OPERATOR_TOKEN
printf '\n'

RUN_ID="$(curl -sS -X POST \
  -H "Authorization: Bearer ${OPERATOR_TOKEN}" \
  http://127.0.0.1:8081/api/prophylaxis/runs \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')"

curl -sS \
  -H "Authorization: Bearer ${OPERATOR_TOKEN}" \
  "http://127.0.0.1:8081/api/prophylaxis/runs/${RUN_ID}" \
  | python3 -m json.tool

unset OPERATOR_TOKEN
```

Počas checku možno sledovať filtrované bezpečné structured logy:

```bash
sudo atlasctl prophylaxis logs --tail=100 -f
```

Príkaz zobrazuje pôvodné collection eventy aj asynchrónne
`prophylaxis_run_completed`, `prophylaxis_run_failed`,
`prophylaxis_device_failed` a `prophylaxis_device_skipped`. Pri chybe obsahuje
bezpečnú kategóriu `reason_code`, platformu a stav; Ansible chyba navyše stav a
návratový kód Runnera a názov eventu/tasku. Credentials, exception text a raw
CLI output sa nelogujú. Celý backend stream zostáva dostupný cez
`sudo atlasctl backend logs`.

NetBox multiselect custom field sa z REST API číta ako zoznam objektov
`{value, label}`. Atlas používa presnú hodnotu `value`; prijíma aj raw string
zoznam kvôli kompatibilite so staršími odpoveďami a testovacími fixtures.

`GET /api/oxidized/status` zámerne nevykonáva credential resolution ani plný sync. `inventory_issues` je lacný stavový údaj: počet názvov prítomných iba v jednej z množín NetBox-enabled/runtime plus chýbajúce alebo duplicitné mená z NetBox odpovede. Zmena credentials alebo ostatných polí sa ukáže až vo výsledku explicitného syncu.

### CLI a API hranice

| CLI operácia | UI/API ekvivalent | Rozdiel |
| --- | --- | --- |
| `atlasctl status` | `GET /api/status` | CLI môže navyše kontrolovať Docker, filesystem a host runtime; API zostáva bez host privileges. |
| `atlasctl oxidized reconcile` | `POST /api/oxidized/sync` | Obe cesty používajú shared reconciliation service. |
| `atlasctl zabbix sync` | `POST /api/zabbix/sync` | CLI, API aj potvrdený Monitoring Provisioning používajú spoločný `ZabbixSyncService`. |
| `atlasctl zabbix status` | `GET /api/zabbix/status` | Overí Zabbix verziu, autentifikáciu a prístup k hostom. |
| `atlasctl integrations cisco test` | `POST /api/integrations/cisco/test` | Chránená diagnostika beží v backend kontajneri a nevypisuje secret ani access token. |
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

## 2. Vyber first-boot site konfiguráciu

Odporúčaný flow používa po inštalácii jeden validovaný konfigurátor. Pre
interaktívne nasadenie netreba v source checkoute vopred vytvárať `.runtime`:

```text
sudo ./install.sh
sudo atlasctl configure
sudo atlasctl deploy
```

Pri úplne novej interaktívnej inštalácii sa inštalátor opýta, či má nasadiť
Oxidized a Zabbix. NetBox, OpenBao, Ansible a profylaxie sú vždy pevnou súčasťou
Atlasu. Pre unattended inštaláciu sa výber zadáva explicitne:

```bash
sudo ./install.sh --components oxidized,zabbix
sudo ./install.sh --components zabbix
sudo ./install.sh --components none
```

Výber sa uloží do chráneného `.runtime/components.yaml`. Aktualizácia ho
zachová. Neskôr možno komponent iba pridať; automatické vypnutie alebo
odstránenie už nasadeného komponentu nie je podporované:

```bash
sudo atlasctl components show
sudo atlasctl components add oxidized
sudo atlasctl configure --site-file /secure/path/customer-site.yaml --force
sudo atlasctl deploy
```

Pri pridaní komponentu musí nový site súbor obsahovať jeho DNS hostname a TLS
materiál. Existujúce databázy, volumes ani konfigurácia sa týmto príkazom
neodstraňujú.

Register voliteľných komponentov je zámerne explicitný. Budúci modul, napríklad
Security Onion integrácia, musí dodať vlastný deploy krok, siete, ingress a
backend capability; následne sa pridá do registra. Staršie manifesty novú
položku nemajú, preto ju po aktualizácii považujú za vypnutú, kým ju operátor
výslovne nepridá.

Pre unattended/cloud-init flow priprav jeden súbor podľa
[`deployment/site.example.yaml`](deployment/site.example.yaml) a po inštalácii
ho najprv over bez zápisu:

```bash
sudo atlasctl configure --site-file /secure/path/customer-site.yaml --check
sudo atlasctl configure --site-file /secure/path/customer-site.yaml
sudo atlasctl deploy
```

Kompletný field reference, TLS režimy, ochrana existujúcej konfigurácie a
troubleshooting sú v [First-boot site configuration](docs/site-configuration.md).

### Kompatibilný manuálny import pred inštaláciou

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
| `services.zabbix.hostname` | Samostatný DNS názov Zabbix UI smerujúci na rovnakú management IP. |
| `services.*.certificate` | Absolútna cesta k PEM server certifikátu vrátane intermediate chain. |
| `services.*.private_key` | Absolútna cesta k príslušnému nešifrovanému PEM private key. |

Certifikát musí pokrývať príslušný DNS názov. Jeden SAN certifikát môže pokrývať
všetky tri názvy. Deploy overuje zhodu certifikátu s kľúčom, platnosť, DNS meno
a reťazec voči nastavenej CA; nevydáva certifikáty ani nemení DNS.

### Doplnenie Zabbix webu na existujúcej inštalácii

Atlas nevytvára DNS záznamy ani nevydáva certifikáty. Pred deploymentom vytvor
v internom DNS samostatný Zabbix hostname smerujúci na hodnotu
`proxy.listen_address`. Pre izolovaný LAB môže byť rovnaké mapovanie dočasne v
`/etc/hosts` na správcovskej stanici.

Najprv over, či existujúci server certifikát už obsahuje Zabbix hostname:

```bash
sudo openssl x509 \
  -in /opt/aricoma-atlas/.runtime/tls/netbox.crt \
  -noout -ext subjectAltName
```

Ak požadovaný `DNS:zabbix.example.internal` v SAN chýba, certifikát musí interná
CA znovu vydať. Existujúci certifikát a private key možno použiť pre všetky tri
služby iba vtedy, keď certifikát pokrýva všetky tri hostname.

Do zdrojového `.runtime/proxy.yaml` doplň tretiu službu; konkrétne DNS meno a
cesty nahraď hodnotami zákazníckeho prostredia:

```yaml
services:
  netbox:
    # existujúca konfigurácia
  oxidized:
    # existujúca konfigurácia
  zabbix:
    hostname: zabbix.example.internal
    certificate: /absolute/path/to/server-fullchain.pem
    private_key: /absolute/path/to/server.key
```

Potom nainštaluj commit, importuj site konfiguráciu a vykonaj celý reconcile:

```bash
sudo ./install.sh
sudo atlasctl deploy
sudo atlasctl zabbix status
sudo atlasctl zabbix credentials
```

Posledný príkaz zobrazí cieľovú HTTPS URL a administrátorské údaje iba v
aktuálnej sudo session. V Operations UI sa po úspešnom deploymente pri Zabbix
karte zobrazí odkaz **Open**.

### LAB-only CA a SAN certifikát

Nasledujúci príklad je určený iba pre izolovaný LAB. Privátny CA kľúč nesmie zostať na produkčnom Atlas serveri.

```bash
export ATLAS_IP="10.99.98.114"
export ATLAS_ALLOWED_NETWORK="10.99.98.0/23"
export NETBOX_HOSTNAME="netbox.atlas.test"
export OXIDIZED_HOSTNAME="oxidized.atlas.test"
export ZABBIX_HOSTNAME="zabbix.atlas.test"

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
subjectAltName=DNS:${NETBOX_HOSTNAME},DNS:${OXIDIZED_HOSTNAME},DNS:${ZABBIX_HOSTNAME}
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
    ("zabbix", os.environ["ZABBIX_HOSTNAME"]),
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

Ak installer neimportoval starší source `.runtime/proxy.yaml`, vytvor kanonickú
proxy, TLS a monitoring konfiguráciu teraz:

```bash
sudo atlasctl configure
```

Alebo deklaratívne:

```bash
sudo atlasctl configure --site-file /secure/path/customer-site.yaml --check
sudo atlasctl configure --site-file /secure/path/customer-site.yaml
```

Po installeri over import runtime vstupov:

```bash
sudo test -f /opt/aricoma-atlas/.runtime/proxy.yaml
sudo test -f /opt/aricoma-atlas/.runtime/tls/ca.crt
sudo test -f /opt/aricoma-atlas/.runtime/tls/netbox.crt
sudo test -f /opt/aricoma-atlas/.runtime/tls/oxidized.crt
sudo test -f /opt/aricoma-atlas/.runtime/tls/zabbix.crt
sudo test -f /opt/aricoma-atlas/.runtime/monitoring/config.yaml
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

## Aktualizácia existujúcej inštalácie

V source checkoute na Atlas VM možno aktualizáciu kódu, inštaláciu do
`/opt/aricoma-atlas` a následný deployment vykonať jedným príkazom:

```bash
cd ~/aricoma-atlas
sudo ./update.sh
```

`update.sh` aktualizuje iba aktuálnu branch voči jej nakonfigurovanému upstreamu
a povoľuje výhradne fast-forward. Pred zmenou overí čistý source checkout a po
aktualizácii vyžaduje presnú zhodu lokálneho a upstream commitu. Pri lokálnych
zmenách, untracked súboroch, detached HEAD, chýbajúcom upstreame alebo
divergovanej histórii skončí bez spustenia installera. Git operácie vykonáva ako
vlastník source checkoutu; `install.sh` a `atlasctl deploy` zostávajú spustené
s oprávneniami správcu.

Parametre odovzdané skriptu sa posunú do deploymentu, napríklad:

```bash
sudo ./update.sh --verbose
```

## 5. Prevádzkové príkazy `atlasctl`

```text
sudo atlasctl configure [--site-file FILE] [--check] [--force]
sudo atlasctl deploy [deploy_atlas.py options]
sudo atlasctl status
sudo atlasctl openbao
sudo atlasctl oxidized reconcile
sudo atlasctl oxidized logs [docker compose logs options]
sudo atlasctl zabbix status
sudo atlasctl zabbix sync
sudo atlasctl zabbix credentials
sudo atlasctl zabbix logs [docker compose logs options]
sudo atlasctl integrations cisco test
sudo atlasctl prophylaxis logs [docker compose logs options]
sudo atlasctl backend logs [docker compose logs options]
sudo atlasctl proxy logs [docker compose logs options]
```

Význam:

| Príkaz | Úloha |
|---|---|
| `sudo atlasctl configure` | Interaktívne vytvorí chránenú proxy, TLS a monitoring site konfiguráciu. |
| `sudo atlasctl configure --site-file FILE --check` | Overí deklaratívny site vstup a TLS bez zápisu. |
| `sudo atlasctl configure --site-file FILE --force` | Po explicitnej kontrole nahradí iba konfigurátorom spravované site súbory. |
| `sudo atlasctl deploy` | Nasadí alebo zosúladí celý Atlas stack. |
| `sudo atlasctl status` | Zobrazí stav kontajnerov, Zabbix servera a Oxidized inventára. |
| `sudo atlasctl openbao` | Otvorí interaktívny OpenBao access helper. |
| `sudo atlasctl oxidized reconcile` | Overí runtime mounty a zosúladí/reloadne Oxidized inventory. |
| `sudo atlasctl oxidized logs -f` | Sleduje live logy Oxidized. |
| `sudo atlasctl zabbix status` | Overí Zabbix JSON-RPC API a počet Atlas-managed hostov. |
| `sudo atlasctl zabbix sync` | Zosúladí `monitoring_enabled=true` zariadenia z NetBoxu do Zabbixu. |
| `sudo atlasctl zabbix credentials` | Zobrazí HTTPS URL a administrátorské prihlasovacie údaje z OpenBao iba v lokálnej sudo session. |
| `sudo atlasctl zabbix logs -f` | Sleduje Zabbix server, web a databázové logy. |
| `sudo atlasctl integrations cisco test` | Overí Cisco OAuth, Software Suggestion a EoX entitlement cez Atlas backend. |
| `sudo atlasctl prophylaxis logs -f` | Sleduje iba bezpečné profylaxia/Ansible diagnostické eventy z backendu. |
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

# Zabbix monitoring

Zabbix je monitoring backend spravovaný Atlasom, nie source of truth.

```text
NetBox monitoring_enabled + platform + primary IP + credential_profile
        │
        ▼
Atlas reconciliation ── OpenBao SNMP secret
        │
        ▼
Zabbix host + template + SNMP interface + ownership tags
```

`atlasctl zabbix sync`, `POST /api/zabbix/sync` a potvrdené Monitoring
Provisioning joby používajú spoločný `ZabbixSyncService`. Vendor driver vlastní
iba zariadenie a SNMP desired state. Atlas plánuje a validuje zmenu; izolovaný
Ansible monitoring playbook vykonáva mutácie pre FortiOS, Cisco IOS/IOS-XE,
Cisco CBS a Aruba AOS-CX/AOS-Switch bez ďalšieho Zabbix writeru. Zabbix časť
vždy dostane normalizovanú identitu, adresu, SNMP secret, port, template a
ownership tagy.

Nasadenie používa Zabbix `7.0.31` LTS, oficiálne server/web image a samostatný
PostgreSQL kontajner. Verzia je pripnutá v `deployment/zabbix.yaml` a mapovanie
v `catalog/zabbix.yaml`. Zabbix server ani databáza nemajú hostom publikovaný
port; web je lokálne na `127.0.0.1:8082` a administrátorské UI sa sprístupňuje
cez existujúci Nginx/TLS model ako `zabbix` služba v `.runtime/proxy.yaml`.
Podporu a vydanie verzie dokumentuje [Zabbix lifecycle policy](https://www.zabbix.com/life_cycle_and_release_policy)
a [release note 7.0.31](https://www.zabbix.com/rn/rn7.0.31) (overené 2026-10-08).

Reconciliation používa iba aktívne NetBox zariadenia s
`monitoring_enabled=true`. Stabilná identita je NetBox device ID v tagu
`netbox_id`; každý vlastnený host má aj `managed_by=aricoma-atlas`. Atlas
číta a mení iba takto označené hosty. Monitoring Provisioning preto bezpečne
adoptuje hosta vytvoreného skorším `atlasctl zabbix sync` namiesto vytvorenia
duplicity. Zariadenie odstránené z NetBoxu alebo s
vypnutým monitoringom sa v Zabbixe deaktivuje, nemaže, aby sa zachovala
história. Manuálne Zabbix hosty zostávajú mimo reconciliation query.

Runtime používa deploymentom vytvorenú host group `Aricoma Atlas`; NetBox site
ukladá do tagu `atlas_site`. Host group nevytvára runtime API účet, takže mu
stačí obmedzený Zabbix `Admin` typ s explicitne povolenými host, interface,
macro a item metódami. Privilegovaný deployment bootstrap vytvorí skupinu a
zosúladí rolu.

Centralizované mapovanie pre Zabbix 7.0 je:

| NetBox platform | Zabbix template |
|---|---|
| `cisco-ios`, `cisco-ios-xe` | `Cisco IOS by SNMP` |
| `cisco-cbs` (CBS220 / legacy CBS) | `Network Generic Device by SNMP` |
| `cisco-cbs250-350` | `Aricoma Atlas Cisco CBS250-350 by SNMP` |
| `fortios` | `FortiGate by SNMP` |
| `junos` | `Juniper by SNMP` |
| `sophos-sfos` | `Aricoma Atlas Sophos SFOS by SNMP` |
| `sophos-ap` | `Network Generic Device by SNMP` |
| `aruba-aos-cx` | `Network Generic Device by SNMP` |
| `aruba-aos-switch` | `Network Generic Device by SNMP` |

Zabbix 7.0 neobsahuje samostatný oficiálny Cisco Small Business ani Sophos
template. Atlas preto počas deploymentu idempotentne importuje vlastné
`catalog/zabbix/templates/cisco_cbs250_350_by_snmp.yaml` a
`catalog/zabbix/templates/sophos_sfos_by_snmp.yaml`. Cisco template je určený
iba pre CBS250/350, dedí `Network Generic Device by SNMP` a pridáva tri CPU
intervaly, teplotu, firmware, sériové číslo, výrobcu, ventilátory, alerty a
CPU/temperature grafy z OID publikovaných spoločnosťou Cisco. CBS220 zostáva na
generic template, pretože Cisco tieto OID pre CBS220 nedokumentuje.

Bootstrap nastaví nový default `cisco-cbs250-350` na Device Types CBS250 a
CBS350. Existujúce NetBox Devices Atlas zámerne nemigruje: ak už majú uloženú
platformu `cisco-cbs`, treba ju jednorazovo zmeniť na `cisco-cbs250-350` a
spustiť `sudo atlasctl zabbix sync`. CBS220 a ostatné staršie CBS zariadenia
zostávajú na backward-compatible `cisco-cbs` a generic template.

Sophos template dedí
`Network Generic Device by SNMP` pre dostupnosť a interface discovery a pridáva
vendor-specific SFOS firmware, pamäť, disk, swap, služby, HA a system-health
itemy z aktuálnej oficiálnej MIB vetvy `1.3.6.1.4.1.2604.5`.

CPU utilization nie je v aktuálnej Sophos private MIB definovaná. Atlas template
preto používa compatibility probe: historický scalar
`1.3.6.1.4.1.21067.2.1.2.2.1.0`, ak ho zariadenie ešte publikuje, inak priemer
štandardných `HOST-RESOURCES-MIB::hrProcessorLoad` hodnôt. Ak firewall nevráti
ani jeden zdroj, item ostane unsupported a template nevytvorí falošnú hodnotu.
CPU/NPU temperature z MIB 22.0+ sú pre kompatibilitu so staršími SFOS predvolene
vypnuté. Po deployment-e spusti `sudo atlasctl zabbix sync`; existujúci
Atlas-owned Sophos host sa prepne z generic na nový managed template bez zmazania
histórie. Komunitné Sophos template sa nepoužívajú, pretože ich historická vetva
OID `1.3.6.1.4.1.21067` nezodpovedá aktuálnej SFOS MIB.
`sophos-red` je inventárna platforma bez priameho Zabbix mappingu, pretože
SD-RED sa spravuje cez nadradený Sophos Firewall a samostatný SNMP agent nebol
pre tento release overený. Chýbajúci template alebo CPU item je explicitný
stav, nie silent fallback. Názvy sú viazané na oficiálny
[Zabbix integrations katalóg](https://www.zabbix.com/integrations/snmp),
[Zabbix 7.0 template source](https://git.zabbix.com/projects/ZBX/repos/zabbix/browse/templates/net?at=release%2F7.0),
[Sophos Firewall SNMP dokumentáciu](https://docs.sophos.com/nsg/sophos-firewall/21.0/Help/en-us/webhelp/onlinehelp/AdministratorHelp/Administration/SNMP/index.html)
a [Sophos AP6/APX SNMP dokumentáciu](https://docs.sophos.com/central/customer/help/en-us/ManageYourProducts/Wireless/AccessPoints/index.html),
ako aj [Cisco CBS250/350 SNMP OID dokumentáciu](https://www.cisco.com/c/en/us/support/docs/smb/switches/Cisco-Business-Switching/kmgmt3636-snmpv3-common-oids-cbs350.html).

Aruba AOS-CX a AOS-Switch v tejto etape používajú generic SNMP template.
Oficiálny Zabbix 7.0 Aruba template je určený pre CX 8300s, preto sa bez
overenia OID nepriraďuje celej AOS-CX ani staršej AOS-Switch rodine.

SNMPv2c community resolver najprv hľadá Atlas-generated per-device secret na
`atlas/devices/snmp/<manufacturer>/<netbox-device-id>/monitoring`. Ak neexistuje,
zachová pôvodný operator-managed profilový lookup na
`atlas/devices/credentials/<manufacturer>/<credential_profile>/snmp`. Community
sa vloží do Zabbix secret macro `{$SNMP_COMMUNITY}` a nikdy sa nevracia cez
Atlas API.
Napríklad profil `sophos-default` používa cestu
`atlas/devices/credentials/sophos/sophos-default/snmp`. Zariadenie musí mať v
NetBoxe stav `active`, Primary IP, príslušnú Sophos platformu,
`credential_profile=sophos-default` a `monitoring_enabled=true`.
Schéma už rozpoznáva SNMPv3 credentials, ale prvá Zabbix verzia ich zámerne
odmietne ako `unsupported_snmp_version`, kým nebude implementované presné
mapovanie security level, authPriv/authNoPriv, algoritmov a Zabbix interface
detail polí. Sophos Firewall aj AP6/APX podporujú SNMPv3; obmedzenie je v
aktuálnom Atlas reconciliation payload-e, nie v Sophose ani Zabbixe.
Požadované Zabbix polia a ich číselné hodnoty sú zdokumentované v
[Zabbix 7.0 Host interface API](https://www.zabbix.com/documentation/7.0/en/manual/api/reference/hostinterface/object).

Atlas backend sa autentifikuje Zabbix API tokenom z `atlas/zabbix/api`.
Administrátorské heslo používa iba idempotentný deployment bootstrap a je v
`atlas/zabbix/admin`; backend k nemu nemá OpenBao právo. JSON-RPC klient používa
oficiálne `api_jsonrpc.php` API s Bearer tokenom, timeoutmi a secret-free
chybami podľa [Zabbix API manual 7.0](https://www.zabbix.com/documentation/7.0/en/manual/api).

Prevádzkové a aplikačné rozhrania:

```text
sudo atlasctl zabbix status
sudo atlasctl zabbix sync
sudo atlasctl zabbix credentials
sudo atlasctl zabbix logs -f

POST /api/zabbix/sync
GET  /api/zabbix/status
GET  /api/zabbix/hosts
GET  /api/zabbix/problems
GET  /api/devices/{device_id}/monitoring
GET  /api/devices/{device_id}/monitoring/metrics/{metric}?window=1h|24h|7d|30d
```

`sudo atlasctl zabbix credentials` je explicitná privilegovaná operácia: cez
deployer AppRole načíta iba `atlas/zabbix/admin`, krátkodobý OpenBao token vždy
revokuje a zobrazí heslo v aktuálnom termináli. Výstup neposielaj do CI logov ani
ticketov. Operations UI preberá validované HTTPS originy z `.runtime/proxy.yaml`
a pri kartách NetBox, Oxidized a Zabbix zobrazí odkaz **Open** do novej karty.

Metrika sa hľadá podľa centralizovaného item-key prefixu, nie podľa nestabilného
`itemid`. Okná `1h` a `24h` používajú `history.get`; `7d` a `30d` používajú
`trend.get`. API vracia normalizované body `{timestamp, value}` určené pre
vlastný Atlas graf, nie embed celého Zabbix frontendu.

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
│               ├── automation
│               ├── oxidized
│               └── snmp
│
├── integrations/
│   └── cisco/
│       └── support-api
│
├── netbox/
│   └── ...
├── zabbix/
│   ├── admin
│   ├── api
│   └── database
└── oxidized/
    └── ...
```

Význam:

- `atlas/devices/credentials/*` – spoločná hierarchia účelovo oddelených credentials pre spravované zariadenia,
- `atlas/integrations/cisco/support-api` – globálny Cisco Support API Client ID a Client Secret,
- `atlas/netbox/*` – interné NetBox secrets,
- `atlas/zabbix/api` – token obmedzeného Atlas API používateľa,
- `atlas/zabbix/admin` a `atlas/zabbix/database` – iba deployment/bootstrap secrets,
- `atlas/oxidized/*` – interné Oxidized secrets.

KV v2 nepoužíva klasické adresáre. Prefix sa v UI zobrazí až vtedy, keď pod ním existuje key alebo metadata. Fresh deployment preto bežne zobrazuje iba reálne použité prefixy, napríklad:

```text
atlas/
├── netbox/
└── oxidized/
```

`devices/` sa zobrazí až po prvom zápise zodpovedajúceho secretu. Policies a AppRoles samy o sebe KV paths nevytvárajú. Toto je očakávané správanie, nie chyba bootstrapu.

---

# OpenBao AppRoles

Beta verzia vytvára samostatné machine identities.

## `atlas-deployer`

Používa sa pre deployment a bootstrap. Má oprávnenia potrebné na čítanie a správu Atlas secrets.

## `atlas-operator`

Oddelená operator identita pre autentifikovaný `generate-root` workflow. Nepoužíva sa ako bežná aplikačná identita.

## `atlas-backend`

Runtime machine identita Atlas aplikácie. Samostatná policy `atlas-backend`
povoľuje iba `read` pre presné token paths `atlas/data/netbox/api`,
`atlas/data/zabbix/api`, `atlas/data/integrations/cisco/support-api` a device
credentials pod `atlas/data/devices/credentials/*`. Neposkytuje `list`, zápis,
mazanie, root operácie ani prístup k admin, PostgreSQL, Redis alebo deployer
secretom.

Cisco Support API credentials sa vkladajú manuálne do KV v2 mountu `atlas` na
relatívnu cestu `integrations/cisco/support-api`:

```json
{
  "client_id": "...",
  "client_secret": "..."
}
```

Atlas neposkytuje CLI na ich zápis. Po vytvorení alebo rotácii secretu možno
bezpečne overiť OAuth a oba API entitlementy cez
`sudo atlasctl integrations cisco test`. Výstup obsahuje iba stavové kódy.
Podrobný kontrakt a plán zapojenia do profylaxie je v
[Cisco Support API integration](docs/cisco-support-api.md).

Pre NetBox platformy `cisco-ios`, `cisco-ios-xe`, `cisco-cbs`,
`cisco-cbs250-350`, `cisco-nxos` a `cisco-asa` používa compliance skupina
overený `device.serial` pre hardvérový EoX, presný `device_type.model` ako
Cisco base PID pre Software Suggestion a zozbieranú verziu pre softvérový EoX.
Lookup sa vykoná iba po zhode serialu a modelu so zariadením. Chýbajúce
entitlementy, nepokrytý PID alebo nedostupné Cisco API vracajú `UNKNOWN` a
nemenia úspešný zber zariadenia na connection failure. CBS softvérový EoX
posiela release bez OS typu, pretože Cisco EoX samostatný CBS typ neuvádza.

Fresh OpenBao bootstrap túto policy, AppRole a machine identity vytvorí alebo zosúladí automaticky. Na už existujúcej inštalácii spusti `sudo atlasctl openbao` a zvoľ `2. Reconcile atlas-backend policy`. Operácia vyžaduje quorum unseal shares, použije krátkodobý temporary root iba pre tento reconcile, token nevypíše a po operácii sa ho pokúsi vždy revokovať.

## `netbox-runtime`

Runtime identita pripravená pre NetBox.

Policies:

```text
netbox-runtime
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

Cieľom je mať jeden konzistentný strom bez zdieľania nadbytočných oprávnení. Oxidized číta iba koncový leaf `oxidized`; `admin` a `snmp` zostávajú oddelené. `automation` je rezervovaný účel bez aktuálneho konzumenta. App-specific secrets zostávajú mimo tohto stromu.

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

OpenBao sa spustí a overí. NetBox, Oxidized, Zabbix, Atlas backend a Nginx pripravia runtime, Compose konfiguráciu a potrebné secrets bez štartu svojich aplikačných kontajnerov. Aj tento režim vyžaduje platnú proxy konfiguráciu a certifikáty.

Samostatne možno použiť:

```bash
python3 scripts/deploy_netbox.py --prepare-only
python3 scripts/deploy_oxidized.py --prepare-only
python3 scripts/deploy_zabbix.py --prepare-only
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
- Zabbix PostgreSQL volume `atlas-zabbix-database`,
- `/run/atlas/oxidized/router.json` (ephemeral materializácia credentials),
- `/opt/aricoma-atlas/.runtime/oxidized/ssh/known_hosts`,
- site-specific proxy/TLS runtime.

Odstránenie OpenBao volume znamená stratu secrets a identity konfigurácie. Odstránenie NetBox alebo Zabbix PostgreSQL volume znamená stratu príslušných dát. Odstránenie Oxidized Git volume znamená stratu histórie konfigurácií, aj keď inventory súbor ostane na hoste.

---

# Atlas NetBox baseline

Atlas baseline predstavuje konfiguráciu, ktorá má existovať na každej Atlas NetBox inštancii bez ohľadu na konkrétneho zákazníka.

Aktuálne obsahuje:

- 1× Custom Field Choice Set,
- 6× Custom Fields.

Konfigurácia baseline je deklaratívne uložená v:

```text
catalog/baseline/
```

## Custom Field Choice Set

### Profylaxia checks

Reusable runtime collection vrstva je popísaná v
[Profylaxia runtime dokumentácii](docs/prophylaxis-runtime.md). Podporuje
CPU collector pre explicitné platformy `cisco-ios`, `cisco-ios-xe`,
`cisco-cbs`, `cisco-cbs250-350` a `fortios`. Collector `device_facts` navyše
podporuje `aruba-aos-cx` a `checkpoint-gaia` a zbiera `hostname`, `model`,
`os_version`, sériové číslo a uptime v sekundách.
Platforma sa berie výhradne z `device.platform.slug`; Atlas ju neodhaduje podľa
výrobcu. Vrstva načítava účelové `prophylaxis` credentials cez existujúcu
OpenBao AppRole vrstvu a vracia
secret-free normalizovaný výsledok. Cisco CLI a FortiOS monitor API alebo
allow-listed FortiOS SSH CLI vykonáva Ansible cez `ansible-runner`; Atlas už
nemá Netmiko ani všeobecný SSH driver.
Profylaxia číta výhradne účelový OpenBao secret bez fallbacku na `/admin`:

```text
devices/credentials/<manufacturer>/<credential_profile>/prophylaxis
```

Príklad pre FortiGate s `credential_profile=forti-mt` je
`devices/credentials/fortinet/forti-mt/prophylaxis`.

SSH platformy očakávajú `username`, `password` a voliteľný
`enable_password`. FortiOS so službou `fortios-api` očakáva `api_token`;
FortiOS bez nej očakáva `username` a `password`. Oxidized naďalej používa
samostatnú koncovku `/oxidized`.
Plná threshold evaluation a periodický scheduler zatiaľ nie sú súčasťou tejto
vrstvy. Spoločná uptime politika však vracia `WARNING` pri hodnote pod 3 dni
alebo nad 365 dní bez ohľadu na platformu. UI a textový report zobrazujú uptime
v ľudskom formáte, kým JSON/API zachováva sekundy. Operations UI poskytuje
manuálne spustenie asynchrónneho behu podľa skupín v `profylaxia_checks`. Skupina
`identity` vytvára výsledky
`device_facts`, `os_version` a `uptime` z jedného facts collector jobu. Skupina
`resources` na Cisco CBS používa jeden read-only SNMP GET job pre overené
CPU/teplotné/fan OID a samostatný read-only CLI job pre prostredie a pokus o
normalizáciu pamäte. Cisco IOS/IOS-XE používajú tematické SSH collectory.
Výsledky `PASS`, `FAIL`, `NOT_APPLICABLE`, `UNKNOWN` a `UNSUPPORTED`, presné
OID, Aruba obmedzenia a porovnanie Zabbix šablón dokumentuje
[Profylaxia system metrics](docs/prophylaxis-system-metrics.md). Pôvodná
manuálna CPU kontrola zostáva dostupná cez API.

FortiOS používa rovnaké tematické check ID cez API aj SSH. Úplnú maticu,
konzervatívne hranice parserov a JSON pole `profylaxia_expectations` opisuje
[FortiOS Profylaxia kontrakt](docs/prophylaxis-fortios.md).

| Platform slug | Transport | `device_facts` implementácia |
| --- | --- | --- |
| `cisco-ios` | SSH `network_cli`, explicitný Telnet fallback | NTC `show version` + `show inventory` |
| `cisco-ios-xe` | `network_cli` | NTC `show version` + `show inventory` |
| `cisco-cbs`, `cisco-cbs250-350` | `network_cli` | allow-listed show príkazy, NTC Templates `cisco_s300` a explicitný PID parser |
| `cisco-nxos` | `network_cli` | `show version` + `show inventory`, NTC Templates `cisco_nxos` |
| `cisco-asa` | `network_cli` | `show version` + `show inventory`, NTC Templates `cisco_asa` |
| `aruba-aos-cx` | `network_cli` | `arubanetworks.aoscx.aoscx_command` + NTC `show system` |
| `fortios` | `httpapi` cez HTTPS pri službe `fortios-api`; inak SSH | API `system_status` + `web-ui/state`, alebo allow-listed `get system status` + `get system performance status` |
| `checkpoint-gaia` | SSH do Gaia Clish | `show hostname`, `show asset all`, `show version all`, `show uptime` |

Check Point Gaia používa vlastný allow-list Ansible modul nad Paramiko, pretože
Gaia Clish nie je POSIX shell a Atlas nemá pre túto platformu overený
`network_cli` plugin. Modul odmietne Expert prompt a nikdy neposiela ľubovoľný
príkaz zo vstupu používateľa. V tejto iterácii podporuje iba spoločný
`device_facts` collector, z ktorého vzniknú aj kontroly `os_version`, `uptime`,
`serial_inventory` a `model_inventory`. CPU ostáva explicitne nepodporované:
oficiálny príkaz `cpstat -f cpu os` je v CLI dokumentácii zobrazovaný v Expert
mode a Atlas profylaxia Expert credentials nevyžaduje.

Použité Check Point kontrakty:

- [Gaia R82.10 System Information Overview](https://sc1.checkpoint.com/documents/R82.10/WebAdminGuides/EN/CP_R82.10_Gaia_AdminGuide/Content/Topics-GAG/System-Information-Overview.htm)
- [Gaia Hardware Health Monitoring — `show asset`](https://sc1.checkpoint.com/documents/R80.30/WebAdminGuides/EN/CP_R80.30_Gaia_AdminGuide/214758.htm)
- [R82.10 CLI Reference — `cpstat`](https://sc1.checkpoint.com/documents/R82.10/WebAdminGuides/EN/CP_R82.10_CLI_ReferenceGuide/CP_R82.10_CLI_ReferenceGuide.pdf)

Aruba SSH/CLI modul potrebuje v backend image aj Python knižnicu Paramiko.
Atlas ju pripína v `requirements.txt`; zmena sa prejaví až po novom zostavení
backend image cez deployment.

Cisco CBS facts collector posiela samostatné výstupy príkazov do pripnutého
balíka `ntc-templates 9.3.0`. NTC šablóny `cisco_s300/show system` a
`cisco_s300/show version` poskytujú hostname a OS verziu. Model sa parsuje ako
PID zo `show inventory`, pre ktorý NTC nemá `cisco_s300` šablónu.

Legacy profil `cisco-ios` pridáva libssh KEX
`diffie-hellman-group1-sha1` a host-key algoritmus `ssh-rsa` za modernejšie
možnosti. Do dočasného SSH configu pre konkrétny Runner job pridáva aj
`aes128-cbc` a `hmac-sha1`; config načíta libssh cez dočasný
`$HOME/.ssh/config` a `ANSIBLE_LIBSSH_CONFIG_FILE`. `3des-cbc`, skrátený SHA-1
MAC ani MD5 varianty nepovoľuje. Ostatné platformové profily tieto výnimky
nepovoľujú.

Telnet fallback sa pre `cisco-ios` aktivuje iba explicitnou TCP službou
`telnet` na konkrétnom zariadení v NetBoxe. Atlas použije port tejto služby
(štandardne 23) a Telnet skúsi iba po chybe SSH vyjednávania. Zlé heslo,
neznámy alebo zmenený SSH host key fallback nespustia. Telnet prenáša
credentials aj dáta bez šifrovania; služba v NetBoxe preto predstavuje vedomé
povolenie pre dané zariadenie. Telnet facts sa parsujú cez pripnuté NTC
Templates `cisco_ios`.

Textový Profylaxia report pri úspešných checkoch uvádza normalizované CPU
percentá, hostname, model, OS verziu, sériové číslo a uptime v sekundách. Raw
CLI výstupy a credentials do reportu nezapisuje; typované hodnoty zostávajú
dostupné aj v `result.json` a API behu.

Pre platformy `cisco-ios` a `cisco-ios-xe` Atlas plánuje tematické collectory
`ios_identity`, `ios_resources`, `ios_interfaces`, `ios_compliance` a
`ios_operations`, takže jeden zber môže vytvoriť viac normalizovaných výsledkov
bez opakovania rovnakej sady príkazov. Neznámy výstup zostáva explicitne
`UNKNOWN` alebo `UNSUPPORTED`, kým nemá overený parser a fixture. Výsledok
oddeľuje `collection_status` od `evaluation` a textový report
je zoskupený podľa zariadenia do častí Vyžaduje zásah, Naplánovať, Zdravie,
Inventár a Nepodporované/neoverené. Presný návrh a parser gate dokumentuje
[Cisco IOS tematický collector plán](docs/prophylaxis-cisco-ios.md).

Každý aktívny NetBox device s `profylaxia_enabled=true` vykoná aj interné
kontroly `serial_inventory` a `model_inventory`. Atlas na ne použije jeden
spoločný identity/facts collector aj vtedy, keď používateľ vybral iba inú
profylaktickú kontrolu. Výsledok sa riadi nasledujúcimi pravidlami:

- ak je natívne pole NetBox Device `serial` prázdne a zariadenie vráti sériové
  číslo, Atlas ho doplní cez NetBox REST API;
- ak sa uložená a zistená hodnota zhodujú bez ohľadu na veľkosť písmen, stav je
  `matched`;
- ak sa hodnoty nezhodujú, Atlas existujúce sériové číslo nikdy neprepíše,
  vráti `FAIL`/`mismatch` a celý beh bude najmenej `PARTIAL`;
- ak platforma sériové číslo neposkytne alebo NetBox zápis zlyhá, výsledok je
  explicitne `serial_not_collected`, `serial_netbox_read_failed` alebo
  `serial_netbox_update_failed`;
- model z collectora sa porovná s `device_type.model` v NetBoxe; zhoda vráti
  `matched`, rozdiel `FAIL`/`mismatch` a NetBox Device Type sa automaticky
  nemení;
- chýbajúci model používa `model_not_collected`, `model_netbox_read_failed`
  alebo `model_not_available_in_netbox`.

Štandardne vytvorený Atlas NetBox token patrí NetBox superuserovi. Pri ručne
dodanom tokene musí mať účet okrem čítania aj oprávnenie meniť `dcim.device`,
inak sa prázdne sériové číslo nedoplní. Porovnanie používa čerstvý detail
zariadenia z NetBoxu; zoznam načítaný na začiatku behu sa nepovažuje za
aktuálny podklad pre zápis. Serial aj model sa z tohto detailu vyhodnotia
spoločne, bez druhého NetBox GET requestu.

FortiOS používa HTTPS API iba vtedy, keď má zariadenie v NetBoxe aplikačnú
službu `fortios-api`; jej port určí API port. Bez tejto služby použije SSH/22
a účelové `username`/`password` z rovnakého `/prophylaxis` secretu. Existujúce
API spojenie pri chybe automaticky neprechádza na SSH. Beta deployment profil
má `fortios_validate_certs: false`, takže pri API sa neoveruje identita HTTPS
servera.

Beta backend deployment nastavuje `ATLAS_SSH_STRICT_HOST_KEYS=false`, takže
Ansible pri Cisco a Aruba kolektoroch nevyžaduje uložený SSH host key. Toto
odstraňuje onboarding blokovaný neznámym kľúčom, ale neoveruje identitu SSH
servera a zvyšuje riziko MITM útoku.

Aktuálne možnosti v NetBoxe:

- `identity` — Identity & inventory
- `resources` — Resources & environment
- `interfaces` — Interfaces
- `compliance` — Compliance
- `operations` — Operations

Každá skupina sa rozbalí na stabilné interné výsledky a platforma vykoná iba
collectory, ktoré má explicitne povolené. Neimplementované výsledky zostanú
viditeľné ako `UNSUPPORTED`; Atlas ich potichu nevynechá. Počas migrácie backend
naďalej akceptuje rozpoznané staré individuálne hodnoty už uložené na
zariadeniach. Historické voľby `Routing table`, `Routing protocol neighbors` a
`HA status` sa zachovajú cez skupinu `operations`; na IOS/IOS-XE/CBS ich
vyhodnotia tematické collectory a neznámy formát zostane neoverený.
Skupina `operations` navyše zobrazuje stav posledného Oxidized backup jobu.
Profylaxia nespúšťa vlastnú zálohu, nečíta konfiguráciu a nemení samostatný
Oxidized polling ani Git históriu.

## Custom Fields

| Name | Type | Group |
|---|---|---|
| `monitoring_enabled` | Boolean | Monitoring |
| `oxidized_enabled` | Boolean | backuping |
| `profylaxia_enabled` | Boolean | Profylaxia |
| `profylaxia_checks` | Multiple selection | Profylaxia |
| `profylaxia_expectations` | JSON | Profylaxia |
| `credential_profile` | Text | Atlas |

Všetky aktuálne Custom Fields sú priradené k objektu:

```text
dcim.device
```

Baseline provisioner kontroluje aj configuration drift.

Pri upgrade provisioner najprv vytvorí `monitoring_enabled`, skopíruje hodnotu
z historického `checkmk_enabled` na každom zariadení a až po úspešnom prenose
staré pole odstráni. Operácia je idempotentná; clean install staré pole
nevytvára. Atlas monitoring používa výhradne `monitoring_enabled`.

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
Sophos       21
HPE         109
----------------
Spolu       820
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

Fortinet, Juniper a Sophos sa v aktuálnej beta verzii vyberajú celé. Z HPE sa
vyberá iba explicitný zoznam Aruba AOS-CX a AOS-Switch rodín; servery, storage,
AP/IAP, controllery, Instant On, EdgeConnect a Comware sa neimportujú.

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
   ├── reviewed Atlas supplements
   │   catalog/device-types/
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
NetBox Community Device Type Library     catalog/device-types/
                │                              │
                │ pinned Git commit            │ reviewed supplements
                └──────────────┬───────────────┘
                               ▼
                       catalog/manifest.yaml
                               │
                               ▼
                       build_catalog.py
                               │
                               ▼
                      Atlas Device Catalog
                         865 Device Types
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

Oxidized má zámerne a trvalo vypnuté host-key verification (`secure: false`). Oxidized 0.37.0 preto nastaví Net::SSH `verify_host_key: :never`. Toto odstraňuje onboarding blokovaný neznámym alebo zmeneným kľúčom, ale zároveň neoveruje identitu SSH servera a zvyšuje riziko MITM útoku.

Persistentný súbor zostáva dostupný pre diagnostiku, ale pri aktuálnom kontrakte nevynucuje trust:

```text
/opt/aricoma-atlas/.runtime/oxidized/ssh/known_hosts
```

Interaktívne SSH pripojenie používateľa zapisuje do `~/.ssh/known_hosts`; tým sa automaticky nepridá trust pre Oxidized.

Voliteľný diagnostický flow na zobrazenie a evidenciu fingerprintu:

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

Pri `secure: false` obsah `known_hosts` pripojenie neobmedzuje. Príkaz `scan` trust nemení a `accept` iba eviduje schválený fingerprint; Oxidized ho pri tomto nastavení nevynucuje.

## FortiGate a náhodne prešifrované ENC hodnoty

FortiOS môže pri každom výpise konfigurácie vrátiť inú zašifrovanú hodnotu `ENC`, aj keď sa význam konfigurácie nezmenil. Atlas preto nastavuje pre model `fortigate` vstavaný Oxidized režim `output_store_mode: on_significant`. Oxidized konfiguráciu naďalej načíta podľa intervalu alebo manuálneho requestu, ale nevytvorí novú Git verziu, ak model vyhodnotí, že sa zmenili iba nestabilné `ENC` hodnoty. Uložený backup zostáva úplný; nejde o redakciu secretov.

Kompromis tohto režimu: samostatná zmena hesla bez akejkoľvek inej významnej zmeny konfigurácie nevytvorí novú verziu. Nové zašifrované hodnoty sa uložia až s nasledujúcou významnou zmenou. Alternatíva `remove_secret: true` stabilizuje diff redakciou, ale backup už neobsahuje použiteľné secrets.

## Pridanie a odstránenie zariadenia

Pridanie:

1. v NetBoxe nastav zariadenie do stavu `active`, primary management IP, podporovanú platformu, `credential_profile` a `oxidized_enabled=true`,
2. ulož read-only SSH účet do `atlas/devices/credentials/<manufacturer>/<credential_profile>/oxidized`,
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
Existing: 820
Missing:  0
```

Component templates:

```text
TOTAL COMPONENTS
catalog=31087
existing=31087
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

Jedinou úzkou výnimkou je verzovaná migrácia pôvodného Atlas choice setu
`Profylaxia checks`: ak sa existujúci objekt presne zhoduje so starou Atlas
schémou, `--apply` nahradí individuálne voľby piatimi skupinami `identity`,
`resources`, `interfaces`, `compliance` a `operations`. Ak sa líši ktorékoľvek
ďalšie pole alebo zoznam obsahuje zákaznícku úpravu, objekt zostane chránený a
bootstrap naďalej oznámi drift. Pri podporovanej Atlas migrácii provisioner
najprv dočasne pridá nové skupiny bez odstránenia starých choices, premapuje
`profylaxia_checks` na zariadeniach a až potom odstráni staré choices. Postup je
idempotentný a bezpečne pokračuje po prerušení. Backend počas migrácie akceptuje
aj rozpoznané staré individuálne hodnoty vrátane `CPU utilization`.

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
Device Types: 820
```

Component coverage:

```text
console-ports         1111
console-server-ports     5
power-ports            456
interfaces           27545
rear-ports               2
module-bays            1962
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

Release tag pre túto verziu (publikovaný až po úspešnom CI na `main`):

```text
v0.4.2
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
# Po merge PR a úspešnom CI na konkrétnom main commite:
git switch main
git pull --ff-only
git tag -a v0.4.2 -m "Aricoma Atlas v0.4.2"
git push origin v0.4.2
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
- vedome vypnuté SSH host-key verification (`secure: false`),
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

- rozšírenie existujúcej profylaxie a monitoring Operations UI,
- autentifikácia a autorizácia pre verejné sprístupnenie Atlas GUI/API,
- pending-approval evidencia a riadená rotácia SSH host keys,
- automatická validácia prvého backupu po schválení zariadenia,
- Zabbix alert notifications a ďalšie overené vendor-specific templates,
- rozšírenie Zabbix SNMPv3 podpory a Atlas monitoring dashboardu,
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
Zabbix monitoring and additional integrations
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
