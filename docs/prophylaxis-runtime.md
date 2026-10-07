# Profylaxia runtime: CPU collection

Táto vrstva implementuje prvý read-only Profylaxia collection flow a manuálnu
Operations UI operáciu. Nevykonáva threshold evaluation, plánovanie ani
remediation. Normalizované výsledky manuálnych behov ukladá lokálne na Atlas VM.

```text
NetBox device (ID alebo presné name)
        |
        | name, primary IP, platform.slug,
        | device_type.manufacturer.slug, credential_profile
        v
ProphylaxisService -----> OpenBao atlas/devices/credentials/<vendor>/<profile>/admin
        |
        v
ConnectionContext (secret fields sa nezobrazujú v repr)
        |
        v
DriverRegistry
  | cisco-ios, cisco-ios-xe -> CiscoIOSDriver
  | cisco-cbs              -> CiscoSMBDriver
  | fortios                -> FortiOSDriver
        |
        v
CPUCheckResult
        |
        v
SQLiteResultStore -> /opt/aricoma-atlas/.runtime/prophylaxis/results.sqlite3
```

## Reusable Python API

`build_dependency_services(settings).prophylaxis.collect_cpu(identifier)` prijíma
číselné NetBox device ID alebo presné meno zariadenia. Pri názve s viacerými
zhodami vráti `device_ambiguous`; resolver nehádá platformu ani vendor z názvu.
Táto nízkoúrovňová metóda výsledok neukladá. UI/API používa
`run_cpu_check(identifier)`, ktorá po collection zapíše normalizovaný výsledok
do lokálnej histórie.

Úspešný anonymizovaný výsledok API po uložení:

```json
{
  "id": 42,
  "collected_at": "2026-10-06T12:00:00Z",
  "device": "SW-CORE-01",
  "platform": "cisco-ios-xe",
  "check": "CPU utilization",
  "status": "ok",
  "values": {
    "current_percent": 8.0,
    "one_minute_percent": 6.0,
    "five_minute_percent": 5.0
  },
  "error": null
}
```

`check` používa existujúcu hodnotu NetBox choice setu `CPU utilization`; nevytvára
paralelnú hodnotu. Výsledok je iba collection. Hodnotenie warning/critical patrí
do budúcej vyššej vrstvy.

## Operations UI a lokálna história

Frontend načíta zariadenia cez `GET /api/prophylaxis/devices`. NetBox query
vyžaduje `status=active` a `profylaxia_enabled=true`; Atlas navyše kontroluje,
že `profylaxia_checks` obsahuje presnú hodnotu `CPU utilization`, zariadenie má
platný management IP, platformu a credential profil a platforma má registrovaný
driver. Disabled zariadenie sa v zozname nezobrazí. Pri priamom pokuse o beh sa
eligibility overí znova ešte pred načítaním credentials alebo SSH pripojením.
Vyradenie zo zoznamu sa zaznamená ako `prophylaxis_device_skipped` s bezpečným
`reason_code`, aby sa dalo odlíšiť chýbajúce pole od nepodporovanej platformy.

Tlačidlo **Run CPU check** volá
`POST /api/prophylaxis/devices/{id}/checks/cpu`. Uloží úspech aj normalizovanú
chybu, aby bola história diagnosticky použiteľná. `GET /api/prophylaxis/results`
vracia najnovšie záznamy v opačnom chronologickom poradí.

Produkčný host adresár je:

```text
/opt/aricoma-atlas/.runtime/prophylaxis/
└── results.sqlite3
```

Backend kontajner ho vidí na `/run/atlas/prophylaxis/`. Host adresár má režim
`0700`, databáza `0600`; backend root filesystem zostáva read-only. Predvolený
limit je 10 000 výsledkov a nastavuje sa cez
`ATLAS_PROPHYLAXIS_RESULT_RETENTION`. Najstaršie záznamy sa po prekročení limitu
odstránia. Databáza neobsahuje credentials ani raw CLI output.

## Implementované príkazy

| NetBox platform | Netmiko adapter | Command | Normalizované hodnoty |
|---|---|---|---|
| `cisco-ios`, `cisco-ios-xe` | `cisco_ios` | `show processes cpu` | 5 s total, 1 min, 5 min |
| `cisco-cbs` | `cisco_s300` | `show cpu utilization` | 5 s, 1 min, 5 min |
| `fortios` | `fortinet` | `get system performance status` | current busy CPU (`100 - idle`) |

Cisco IOS formát a význam prvej hodnoty sú zdokumentované v [Cisco show
processes CPU dokumentácii](https://www.cisco.com/c/en/us/support/docs/ios-nx-os-software/ios-software-releases-120-mainline/15102-showproc-cpu.html).
Cisco CBS syntax a výstup sú v [Cisco Business 250 CLI reference](https://www.cisco.com/c/en/us/td/docs/switches/lan/csbms/CBS_250_350/CLI/cbs-250-cli/system-management-commands.html).
FortiOS príkaz a `CPU states` výstup sú v [FortiGate 7.0.6 Administration
Guide](https://docs.fortinet.com/document/fortigate/7.0.6/administration-guide/152469/troubleshooting-cpu-and-network-resources).

CBS používa samostatný Atlas driver a samostatný parser. Netmiko 4.8.0 nemá
adapter pomenovaný CBS; `cisco_s300` je zvolený ako najmenší Cisco Small
Business transport adapter. Kompatibilita promptu a paging správania musí byť
potvrdená manuálnym testom na cieľovom CBS250/CBS350 pred produkčným použitím.

Transport používa priamo `netmiko==4.8.0` a jeho SSH exception typy z
`paramiko==5.0.0`; obe priame dependencies sú pripnuté v `requirements.txt`.

## SSH host keys a timeouty

Konfigurácia je centrálna:

```text
ATLAS_SSH_STRICT_HOST_KEYS
ATLAS_SSH_KNOWN_HOSTS_FILE
ATLAS_SSH_CONNECT_TIMEOUT
ATLAS_SSH_AUTH_TIMEOUT
ATLAS_SSH_COMMAND_TIMEOUT
```

`development` a `test` používajú default `ATLAS_SSH_STRICT_HOST_KEYS=false`,
preto nevyžadujú položku `known_hosts`. Toto je vedomé zníženie ochrany proti
MITM a je určené iba pre aktuálny lab/development režim.

Produkčný `deployment/backend.yaml` nastavuje strict režim explicitne na `true`
a backend dostane read-only mount existujúceho
`.runtime/oxidized/ssh/known_hosts`. Neznámy a zmenený host key sa rozlišujú ako
`host_key_unknown` a `host_key_mismatch`. Transport nepoužíva SSH agent ani
lokálne private keys a nastavuje osobitný TCP, authentication a command timeout.

## Chybové kódy

```text
device_not_found
device_ambiguous
missing_device_name
device_inactive
missing_primary_ip
missing_platform
missing_credential_profile
missing_manufacturer
prophylaxis_disabled
check_not_selected
unsupported_platform
credential_not_found
invalid_credential_schema
dependency_unavailable
host_key_unknown
host_key_mismatch
connection_timeout
connection_failed
authentication_failed
command_failed
parse_failed
result_store_unavailable
```

Výsledok ani structured log neobsahuje username, password, enable password,
OpenBao token alebo AppRole SecretID. Raw CLI output sa automaticky neukladá ani
neloguje.

## Referenčná diplomová práca

Analyzované boli `backend/drivers/base.py`, `capabilities.py`, `factory.py`,
`cisco_iosxe.py`, `backend/services/device_service.py`,
`backend/clients/netbox_client.py`, `backend/core/platforms.py` a
`requirements.txt` z `dablkros/diplomova_praca` (`main`, commit `33b7545`).

Prevzatý bol koncept malého driver interface, explicitného platform resolvera,
NetBox device lookupu a použitie Netmiko pre network CLI. CPU commandy, CPU
parsery, CBS driver ani FortiOS driver sa v analyzovanom kóde nenachádzali, preto
pochádzajú z vyššie uvedených vendor dokumentácií. Neprevzaté boli globálne SSH
credentials, široký `if/elif` factory, potláčanie NETCONF výnimiek a
`hostkey_verify=False`.
