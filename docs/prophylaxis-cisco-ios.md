# Cisco IOS tematické collectory profylaxie

Stav návrhu: 2026-10-20. Rozsah: iba NetBox platforma `cisco-ios`.

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
| `operations` | `ntp_sync`, `recent_log_events` |

**Fakt:** Backup nie je skupina ani výsledok profylaxie. Oxidized inventory,
polling, konfigurácie a Git história zostávajú samostatným flow.

| `CollectorId` | Zamýšľané `CheckId` | Stav v tomto reze |
| --- | --- | --- |
| `ios_identity` | `device_facts`, `os_version`, `uptime`, `serial_inventory`, `model_inventory` | implementované z `show version` + `show inventory` |
| `ios_resources` | `cpu_utilization`, `memory_utilization`, `environment_health`, `cpu_temperature`, `fan_health` | implementované iba `cpu_utilization` z `show processes cpu` |
| `ios_interfaces` | `interface_state`, `interface_utilization`, `interface_error_counters` | explicitne unsupported |
| `ios_compliance` | `license_state`, `pki_certificates`, `firmware_recommendation`, `eol_status` | explicitne unsupported |
| `ios_operations` | `ntp_sync`, `recent_log_events` | explicitne unsupported |

**Fakt:** Profil `cisco-ios` preferuje `ios_identity` a `ios_resources`.
Predchádzajúce interné ID `device_facts` a `cpu_usage` ostávajú vykonateľné ako
kompatibilný kontrakt pre existujúcich priamych volajúcich backendu.

**Inference:** Ostatné tematické collectory sú pomenované vopred, ale capability
register ich nepovoľuje. Tým sa zachová stabilný plán bez predstierania podpory.

## Typovaný výsledkový kontrakt

**Fakt:** `ProphylaxisCheckResult` zachováva pôvodné polia `status` a
`evaluation` a pridáva `collection_status` s hodnotami `COLLECTED`, `FAILED` a
`UNSUPPORTED`. `evaluation` má hodnoty `PASS`, `WARNING`, `FAIL`,
`NOT_APPLICABLE`, `UNKNOWN` a `UNSUPPORTED`.

**Fakt:** `collection_status=COLLECTED` neznamená automaticky `PASS`. Parser môže
vrátiť `UNKNOWN`, hodnotenie môže vrátiť `WARNING` alebo `FAIL` a payload pritom
ostáva zachovaný. Staršie uložené JSON výsledky bez `collection_status` sa pri
načítaní mapujú kompatibilne podľa payloadu a pôvodného `status`.

**Návrh, zatiaľ neimplementovaný:** Po dodaní reálnych anonymizovaných výstupov
majú nové checks dostať samostatné Pydantic payloady, nie voľné slovníky:

| Model | Minimálny normalizovaný obsah |
| --- | --- |
| `MemoryUtilizationValues` | total/used/free bytes a vypočítané percento; žiadny odhad pri chýbajúcom total |
| `EnvironmentHealthValues` | zoznam pomenovaných senzorov, ich stav a explicitný outcome |
| `InterfaceStateValues` | interface, admin/oper stav a prípadne rýchlosť |
| `InterfaceErrorCounterValues` | interface, absolútne input/output error countery, čas vzorky; trend iba s druhou vzorkou alebo Zabbix históriou |
| `LicenseStateValues` | lokálny feature/license názov, stav a explicitný outcome |
| `PKICertificateValues` | trustpoint/subject, serial, platnosť od/do a outcome expirácie |
| `FirmwareRecommendationValues` | zistená verzia, odporúčaná kompatibilná verzia, zdroj a dátum aktualizácie zdroja |
| `EOLStatusValues` | produkt/PID, lifecycle míľniky, stav zdroja a dátum jeho aktualizácie |
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
ani `show environment all`. Atlas tieto formáty nesmie hádať; vyžaduje reálny
anonymizovaný výstup a úzky parser s fixture.

Primárne zdroje, verzia 9.3.0:

- [NTC Templates v9.3.0 index](https://raw.githubusercontent.com/networktocode/ntc-templates/v9.3.0/ntc_templates/templates/index)
- [NTC Templates v9.3.0 release](https://github.com/networktocode/ntc-templates/releases/tag/v9.3.0)

## Potrebné vstupy pre ďalšiu iteráciu

**Neoverené:** Podpora sa nerozšíri, kým nebudú dostupné anonymizované výstupy
aspoň z jedného staršieho IOS routera a jedného Catalyst switcha pre:

- `show processes memory sorted`
- `show environment all`
- `show interfaces counters errors`
- `show ntp status`
- `show ntp associations`
- `show license summary` a/alebo `show license status`
- `show crypto pki certificates`

Výstupy nesmú obsahovať heslá, tokeny ani `show running-config`.
