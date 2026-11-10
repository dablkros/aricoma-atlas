# Cisco CLI profylaxia: tematické collectory a raw collection

Stav implementácie: 2026-11-10. Raw režimy podporujú presné platformové slugs
`cisco-ios`, `cisco-ios-xe`, `cisco-nxos`, `cisco-asa`, `cisco-cbs`,
`cisco-cbs250-350`, `cisco-catalyst-1200` a `cisco-catalyst-1300`.
Tematické collectory zostávajú kompatibilné s existujúcimi nastaveniami.

## Raw režimy

`identity` je ľahký connection/authentication/privileged CLI test s tromi
samostatnými príkazmi. `full_collection` sekvenčne vykoná fixný katalóg 49
príkazov v jednej Ansible `network_cli` session. Každý príkaz má stav
`success`, `unsupported`, `empty`, `permission_denied`, `timeout`, `error`
alebo `truncated`; nepodporovaný príkaz nezlyhá celé zariadenie.

Atlas uchová raw stdout po line-based redakcii známych secret tvarov a
best-effort TextFSM reprezentáciu. TextFSM nie je hodnotiaca autorita:
`template_missing` a `failed` sú parser metadáta, nie chyby transportu. Limity
výstupu riadi `backend.prophylaxis_raw_command_max_bytes`, predvolene 1 MiB na
príkaz.

Výsledné chránené JSON artefakty sú zoskupené po Tenant/Site, pričom každý
záznam zariadenia nesie NetBox Device ID, platformu, Device Type, management
IP, credential profile, connection status, počty a jednotlivé výstupy.

## Skupiny, výsledky a capability register

**Fakt:** NetBox ponúka päť používateľských skupín. Atlas vybranú skupinu
rozbalí na stabilné interné `CheckId`, platformový register priradí podporované
výsledky ku `CollectorId` a Ansible Runner vykoná každý naplánovaný collector
raz na zariadenie. Existujúce API polia, OpenBao účel `prophylaxis`,
normalizácia, uloženie a report flow ostávajú zachované.

| Hodnota v NetBoxe | Výsledky skupiny |
| --- | --- |
| `identity` | `device_facts`, `os_version`, `uptime` |
| `resources` | `cpu_utilization`, `memory_utilization`, `environment_health`, `cpu_temperature`, `fan_health` |
| `interfaces` | `interface_state`, `interface_utilization`, `interface_error_counters` |
| `compliance` | `license_state`, `pki_certificates`, `firmware_recommendation`, `eol_status` |
| `operations` | `ntp_sync`, `recent_log_events`, `routing_table`, `routing_protocol_neighbors`, `ha_status`, `configuration_backup` |

**Fakt:** `configuration_backup` iba číta posledný stav jobu z Oxidized
`nodes.json`. Profylaxia nespúšťa druhú zálohu, nečíta konfiguráciu a nemení
Oxidized polling ani Git históriu.

| `CollectorId` | Zamýšľané `CheckId` | Stav v tomto reze |
| --- | --- | --- |
| `ios_identity` | `device_facts`, `os_version`, `uptime`, `serial_inventory`, `model_inventory` | implementované z `show version` + `show inventory` |
| `ios_resources` | `cpu_utilization`, `memory_utilization`, `environment_health`, `cpu_temperature`, `fan_health` | CLI zber a konzervatívne normalizátory implementované |
| `ios_interfaces` | `interface_state`, `interface_utilization`, `interface_error_counters` | CLI zber implementovaný; očakávaný stav, prahy a counter delta zatiaľ neexistujú |
| `ios_compliance` | `license_state`, `pki_certificates` | lokálny licenčný a PKI zber implementovaný |
| `ios_operations` | `ntp_sync`, `recent_log_events`, `routing_table`, `routing_protocol_neighbors`, `ha_status` | CLI zber a konzervatívne normalizátory implementované |
| Cisco Support API | `firmware_recommendation`, `eol_status` | implementované cez overený NetBox PID/serial a zozbieranú IOS verziu; chyba API vracia `UNKNOWN` |

**Fakt:** Profily `cisco-ios` a `cisco-ios-xe` preferujú všetkých päť
tematických IOS collectorov. Predchádzajúce interné ID `device_facts` a
`cpu_usage` ostávajú vykonateľné ako kompatibilný kontrakt pre existujúcich
priamych volajúcich backendu.

**Fakt:** Jeden tematický collector vykoná svoju sadu príkazov raz a jednotlivé
checks nad ňou používajú samostatné normalizátory. Neplatný alebo nepodporovaný
príkaz sa neinterpretuje ako zdravý nulový stav.

## Typovaný výsledkový kontrakt

**Fakt:** `ProphylaxisCheckResult` zachováva pôvodné polia `status` a
`evaluation` a pridáva `collection_status` s hodnotami `COLLECTED`, `FAILED` a
`UNSUPPORTED`. `evaluation` má hodnoty `PASS`, `WARNING`, `FAIL`,
`NOT_APPLICABLE`, `UNKNOWN` a `UNSUPPORTED`.

**Fakt:** `collection_status=COLLECTED` neznamená automaticky `PASS`. Parser môže
vrátiť `UNKNOWN`, hodnotenie môže vrátiť `WARNING` alebo `FAIL` a payload pritom
ostáva zachovaný. Staršie uložené JSON výsledky bez `collection_status` sa pri
načítaní mapujú kompatibilne podľa payloadu a pôvodného `status`.

**Fakt:** Nové neskalárne kontroly používajú validovaný `DiagnosticValues`
payload s outcome, reason kódom, krátkym súhrnom, bezpečnými metrikami,
ovplyvnenými položkami a zoznamom zdrojových príkazov. Raw CLI výstup sa do
reportu ani API výsledku nekopíruje.

**Inference:** Po získaní reálnych výstupov možno ostatné všeobecné diagnostické modely
sprísniť na doménové payloady:

| Model | Minimálny normalizovaný obsah |
| --- | --- |
| `MemoryUtilizationValues` | total/used/free bytes a vypočítané percento; žiadny odhad pri chýbajúcom total |
| `EnvironmentHealthValues` | zoznam pomenovaných senzorov, ich stav a explicitný outcome |
| `InterfaceStateValues` | interface, admin/oper stav a prípadne rýchlosť |
| `InterfaceErrorCounterValues` | interface, absolútne input/output error countery, čas vzorky; trend iba s druhou vzorkou alebo Zabbix históriou |
| `LicenseStateValues` | lokálny feature/license názov, stav a explicitný outcome |
| `PKICertificateValues` | trustpoint/subject, serial, platnosť od/do a outcome expirácie |
| `FirmwareRecommendationValues` | implementované: PID, zistená verzia, všetky Cisco suggested formáty, software type a outcome |
| `EOLStatusValues` | implementované: serial, PID, OS/verzia, oddelený hardware/software stav, bulletin a lifecycle míľniky |
| `NTPStatusValues` | synchronizácia, vybraný peer a stratum, iba ak ich výstup poskytne |
| `RecentLogValues` | obmedzený počet normalizovaných záznamov bez raw dumpu |

## Report

**Fakt:** `report.txt` je zoskupený podľa zariadenia a pre každé zariadenie má
časti `Vyžaduje zásah`, `Naplánovať`, `Zdravie`, `Inventár` a
`Nepodporované/neoverené`. Každý riadok uvádza collection status, evaluation a
pôvodný kompatibilný status.

**Fakt:** `FAIL` alebo zlyhanie zberu patrí do `Vyžaduje zásah`; `WARNING` do
`Naplánovať`; neznámy/nepodporovaný/neaplikovateľný výsledok do
`Nepodporované/neoverené`; úspešná identita do `Inventár`; ostatné úspešné
checks do `Zdravie`.

## Parser gate a overené zdroje

**Fakt:** Runtime pripína `ntc-templates==9.3.0`. Index tejto verzie obsahuje
presné Cisco IOS šablóny pre `show processes memory sorted`,
`show crypto pki certificates`, `show license status`, `show logging`,
`show environment temperature` a `show environment power all`.

**Fakt:** Rovnaký index neobsahuje presnú Cisco IOS šablónu pre
`show interfaces counters errors`, `show ntp status`, `show ntp associations`
ani `show environment all`.

**Inference:** Pre tieto príkazy Atlas používa dočasné úzke regex
normalizátory. Ak formát nie je rozpoznaný, výsledok je `UNKNOWN` alebo
`UNSUPPORTED`, nikdy `PASS` odvodený z absencie dát. Každá odchýlka z reálneho
zariadenia sa má najprv uložiť ako anonymizovaná fixture a až potom opraviť
parser.

Primárne zdroje, verzia 9.3.0:

- [NTC Templates v9.3.0 index](https://raw.githubusercontent.com/networktocode/ntc-templates/v9.3.0/ntc_templates/templates/index)
- [NTC Templates v9.3.0 release](https://github.com/networktocode/ntc-templates/releases/tag/v9.3.0)

## Potrebné vstupy pre ďalšiu iteráciu

**Neoverené:** Pred produkčným označením podpory treba dodať anonymizované
výstupy aspoň z jedného staršieho IOS routera, jedného Catalyst switcha a podľa
možnosti IOS-XE zariadenia pre:

- `show processes memory sorted`
- `show environment all`
- `show interfaces counters errors`
- `show ntp status`
- `show ntp associations`
- `show license summary` a/alebo `show license status`
- `show crypto pki certificates`

Výstupy nesmú obsahovať heslá, tokeny ani `show running-config`.

## Zámerne nezaradené príkazy

**Fakt:** Tematické collectory naďalej nepoužívajú `show running-config`.
Explicitný režim `full_collection` ho však obsahuje ako citlivý diagnostický
snapshot spolu s ARP/MAC/VLAN/CDP/LLDP, Nexus a ASA príkazmi. Tento snapshot
nenahrádza Oxidized backup ani jeho Git históriu a nepodporované príkazy sa
nefiltrujú vopred.

**Inference:** Ak budú topológia, AAA/RADIUS alebo platformovo špecifické HA
kontroly požadované ako samostatné výsledky, majú dostať vlastný `CheckId`,
fixture a platformový collector; nemajú byť skryto prilepené k inému health
checku.
