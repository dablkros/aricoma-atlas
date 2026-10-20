# Profylaxia runtime: Atlas orchestration and Ansible execution

Stav dokumentácie: 2026-10-16. Implementácia zachováva Atlas ako orchestrátor;
Ansible je iba execution vrstva pre komunikáciu so zariadeniami.

## Flow

```text
Atlas UI/API
  -> ProphylaxisService.collect(...) alebo DeviceFactsRunService
  -> existujúci NetBoxService
  -> active + profylaxia_enabled + profylaxia_checks
  -> device/IP/platform/vendor/credential_profile
  -> NetBox Application Service -> connection port
  -> existujúci OpenBaoService a admin credential namespace
  -> AnsiblePlatformRegistry
  -> AnsibleBackend / ansible-runner
  -> read-only Ansible collection module
  -> RawCollectorResult
  -> platform-specific Python parser
  -> normalizovaný check result
  -> SQLite história alebo runs/<run_id>/{result.json,report.txt} / API / UI
```

Nevzniká druhý NetBox ani OpenBao klient. Oxidized naďalej používa SSH polia
`username`, `password` a voliteľné `enable_password` z:

```text
devices/credentials/<manufacturer>/<credential_profile>/admin
```

FortiGate profylaxia používa z toho istého secretu pole `api_token`. Kombinovaný
Fortinet secret preto môže obsahovať SSH údaje pre Oxidized aj API token pre
Ansible. Resolver vráti iba polia požadované konkrétnym consumerom.

Pri upgrade existujúceho Fortinet profilu pridaj `api_token` do rovnakého
`admin` secretu cez autentifikované OpenBao UI. Zachovaj existujúce
`username`/`password`, ak profil používa aj Oxidized. Chýbajúci alebo neplatný
token sa vracia ako `api_token_missing_or_invalid`; nejde o Ansible connection
failure.

## Port služby z NetBoxu

Pre platformu `fortios` Atlas vyhľadá na konkrétnom zariadení Application
Service s presným názvom `fortios-api`. Z NetBox 4.7 poľa `port_mappings`
akceptuje práve jednu TCP hodnotu, napríklad:

```text
fortios-api -> TCP/444 -> ["tcp/444"]
```

Port sa odovzdá do `ansible.netcommon.httpapi` ako `ansible_httpapi_port`.
Atlas porty aktívne neskenuje. Ak služba `fortios-api` na zariadení neexistuje,
použije platformový default `443`, aby ostala zachovaná kompatibilita. Viac
služieb s rovnakým názvom alebo viac port mappings skončí s
`ambiguous_service_port`; iný protokol, neplatný port alebo neplatná odpoveď
skončí s `invalid_service_port`. Port sa vyhodnotí ešte pred čítaním secretu.

Kontrakt zodpovedá NetBox 4.7.1, ktorý reprezentuje mapovania ako zoznam
`protocol/port` reťazcov a podporuje REST filtre `device_id` a `name`:

- [NetBox 4.7.1 Service model](https://github.com/netbox-community/netbox/blob/v4.7.1/docs/models/ipam/service.md)
- [NetBox 4.7.1 ServiceFilterSet](https://github.com/netbox-community/netbox/blob/v4.7.1/netbox/ipam/filtersets.py#L1292-L1368)

## Stabilné check ID

Interný kontrakt používa:

```text
cpu_utilization
device_facts
os_version
cpu_temperature
uptime
```

V tejto iterácii sú implementované `cpu_utilization`, `device_facts`,
`os_version` a `uptime`. `device_facts` normalizuje hostname, model, OS verziu,
sériové číslo a uptime v sekundách; samostatné kontroly `os_version` a `uptime`
používajú ten istý facts collector, takže pri spoločnom výbere nevznikne druhý
collector job. Hromadný beh zostaví plán z
`profylaxia_checks` každého zariadenia. Registry vráti pre známu platformu a
neimplementovaný check stav `unsupported`; beh ho uloží ako `skipped` s kódom
`unsupported_check`, nevydáva ho za connection error a nevytvára nulovú
hodnotu.

Profylaxia zároveň vykonáva interné kontroly `serial_inventory` a
`model_inventory` pre každý aktívny device s `profylaxia_enabled=true`. Ak plán
ešte neobsahuje `device_facts`, pridá jeden facts collector job bez pridania
používateľského checku. Jeden čerstvý detail zariadenia z NetBoxu poskytne
uložené hodnoty pre obe porovnania.

Sériové číslo sa porovná s natívnym poľom NetBox Device `serial`:

| Stav | Správanie |
| --- | --- |
| `populated` | NetBox pole bolo prázdne; Atlas ho doplnil z collectora. |
| `matched` | NetBox a zariadenie majú rovnaké sériové číslo. |
| `mismatch` | Hodnoty sú rozdielne; Atlas NetBox neprepíše a vráti `FAIL`. |
| `serial_not_collected` | Collector neposkytol použiteľné sériové číslo. |
| `serial_netbox_read_failed` | Pred porovnaním nebolo možné načítať čerstvý detail zariadenia. |
| `serial_netbox_update_failed` | Prázdne pole nebolo možné zapísať alebo NetBox nevrátil uloženú hodnotu. |

Porovnanie ignoruje rozdiel vo veľkosti písmen a okrajové/nadbytočné medzery.
Zápis sa vykonáva iba pri prázdnom poli. Existujúca rozdielna hodnota zostáva
zachovaná, aby profylaxia nemohla zakryť fyzickú výmenu zariadenia. Ručne
dodaný NetBox API token preto potrebuje oprávnenie na zmenu `dcim.device`;
štandardný deployment používa token automaticky vytvoreného superusera.

Model z collectora sa porovná s `device_type.model` v NetBoxe. `matched` je
`PASS`; `mismatch` je `FAIL` a spôsobí najmenej stav behu `PARTIAL`. Atlas
Device Type automaticky neprepisuje, pretože zmena typu môže ovplyvniť model
komponentov a interpretáciu inventára. Chýbajúce údaje majú explicitné kódy
`model_not_collected`, `model_netbox_read_failed` a
`model_not_available_in_netbox`. Aj tu porovnanie ignoruje iba veľkosť písmen
a nadbytočné medzery; produktové aliasy ani podobné názvy sa neodhadujú.

Použité NetBox kontrakty:

- [NetBox 4.7.1 Device model — Serial Number](https://github.com/netbox-community/netbox/blob/v4.7.1/docs/models/dcim/device.md#serial-number)
- [NetBox 4.7.1 REST API — detail endpoint pre update objektu](https://github.com/netbox-community/netbox/blob/v4.7.1/docs/integrations/rest-api.md#endpoints)

NetBox choice set používa stabilné ID ako value a ľudský text ako label.
Resolver počas migrácie akceptuje aj pôvodnú hodnotu `CPU utilization`, aby
už uložené zariadenia neprestali fungovať okamžite po upgrade.

## Platform registry

| NetBox platform | Connection | Network OS | `device_facts` modul |
| --- | --- | --- | --- |
| `cisco-ios` | SSH `network_cli`; explicit Telnet fallback | `cisco.ios.ios` | `show version` + `show inventory`, NTC Templates |
| `cisco-ios-xe` | `ansible.netcommon.network_cli` | `cisco.ios.ios` | `show version` + `show inventory`, NTC Templates |
| `cisco-cbs` | `ansible.netcommon.network_cli` | `community.ciscosmb.ciscosmb` | `community.ciscosmb.command` + NTC Templates `cisco_s300` |
| `cisco-cbs250-350` | `ansible.netcommon.network_cli` | `community.ciscosmb.ciscosmb` | `community.ciscosmb.command` + NTC Templates `cisco_s300` |
| `aruba-aos-cx` | `ansible.netcommon.network_cli` | `arubanetworks.aoscx.aoscx` | `aoscx_command` + NTC `show system` |
| `fortios` | `ansible.netcommon.httpapi` | `fortinet.fortios.fortios` | `system_status` + HTTPS `web-ui/state` |

Cisco IOS/IOS XE používa `show processes cpu`; Cisco CBS samostatne používa
`show cpu utilization`. FortiOS používa monitor selector
`system_resource_usage` s `resource: cpu` a API tokenom.

Cisco CBS facts collector zachováva výstupy `show system`, `show version` a
`show inventory` oddelene. Hostname a verzia sa parsujú cez pripnuté
`ntc-templates 9.3.0` šablóny `cisco_s300`; model sa berie z explicitného PID
v `show inventory`, pretože NTC katalóg nemá pre túto dvojicu platformy a
príkazu šablónu.

Primárne zdroje:

- [Ansible Runner Python interface](https://docs.ansible.com/projects/runner/en/latest/python_interface/)
- [ansible.netcommon 8.6.2](https://docs.ansible.com/projects/ansible/latest/collections/ansible/netcommon/index.html)
- [ansible.netcommon.telnet](https://docs.ansible.com/projects/ansible/latest/collections/ansible/netcommon/telnet_module.html)
- [ansible.utils 6.1.0](https://docs.ansible.com/projects/ansible/latest/collections/ansible/utils/index.html)
- [cisco.ios 11.5.1](https://docs.ansible.com/projects/ansible/latest/collections/cisco/ios/index.html)
- [community.ciscosmb 1.0.12](https://docs.ansible.com/projects/ansible/latest/collections/community/ciscosmb/index.html)
- [community.ciscosmb.command](https://docs.ansible.com/projects/ansible/latest/collections/community/ciscosmb/command_module.html)
- [fortinet.fortios 2.6.0](https://docs.ansible.com/projects/ansible/latest/collections/fortinet/fortios/index.html)
- [fortios_monitor_fact](https://docs.ansible.com/projects/ansible/latest/collections/fortinet/fortios/fortios_monitor_fact_module.html)
- [Aruba AOS-CX collection](https://github.com/aruba/aoscx-ansible-collection)
- [Paramiko 5.0.0](https://pypi.org/project/paramiko/5.0.0/)
- [NTC Templates 9.3.0](https://github.com/networktocode/ntc-templates/tree/v9.3.0/ntc_templates/templates)

Aruba `aoscx_command` používaný cez `network_cli` vyžaduje na Ansible control
node knižnicu Paramiko. Backend image ju preto inštaluje ako explicitne
pripnutú Python runtime závislosť; samotné `ansible-galaxy collection install`
Python requirements kolekcie neinštaluje.

Profil `cisco-ios` pridáva do predvoleného libssh zoznamu legacy KEX
`diffie-hellman-group1-sha1` a povoľuje server host key `ssh-rsa` až za
modernejšími typmi kľúčov. Do dočasného per-job SSH configu zároveň pridáva
`aes128-cbc` za moderné šifry a `hmac-sha1` za moderné MAC algoritmy. Slabší
algoritmus sa použije iba vtedy, keď zariadenie neponúkne spoločný modernejší
algoritmus. Config sa uloží do
dočasného `$HOME/.ssh/config` a jeho cesta sa odovzdá cez procesnú premennú
`ANSIBLE_LIBSSH_CONFIG_FILE`; odstráni sa spolu s dočasným Runner adresárom.
`3des-cbc`, `hmac-sha1-96` ani MD5 varianty sa nepovoľujú. `cisco-ios-xe`, CBS
a Aruba profily tieto výnimky nepovoľujú.

Telnet je pre `cisco-ios` explicitný fallback, nie predvolený transport. Atlas
ho povolí iba vtedy, keď má konkrétne zariadenie v NetBoxe práve jednu TCP
službu s presným názvom `telnet`; použije port tejto služby, štandardne 23.
Fallback sa vykoná iba po `ssh_negotiation_failed`. Neaktivuje sa pri zlom
hesle, neznámom alebo zmenenom SSH host key ani pri inom zlyhaní. Telnet úlohy
sú read-only a zbierajú iba CPU alebo `show version` + `show inventory`.
Výstupy device facts parsujú pripnuté NTC Templates `cisco_ios`; prihlasovacie
údaje ostávajú v procesnom environment a Telnet task používa `no_log`.
Telnet neprenáša prihlásenie ani dáta šifrovane, preto služba v NetBoxe slúži
ako vedomé povolenie pre konkrétne zariadenie.

Dokončený `report.txt` obsahuje pri úspešných checkoch aj normalizované
hodnoty: CPU percentá, hostname, model, OS verziu, sériové číslo a uptime v
sekundách. Report neobsahuje raw CLI výstup ani credentials. Rovnaké typované
hodnoty sú uložené v `result.json` a vrátené cez API behu.

Pre check `cpu_utilization` sa Atlas po dokončení behu pokúsi načítať jednu
hodinu histórie z Atlas-managed Zabbix hosta označeného rovnakým `netbox_id`.
Ak existuje numerický CPU item a história, uloží lokálny SVG graf do
`runs/<run_id>/charts/` a zobrazí ho v Operations UI. Graf je voliteľný
artefakt: chýbajúci Zabbix host, item, história alebo chyba Zabbixu nemení
výsledok checku ani súhrnný stav behu. Stav je uvedený v poli `charts` a v
`report.txt`; credentials ani raw SNMP dáta sa do grafu neukladajú.

## Runtime a secrets

Atlas vytvorí pre každý job jediný in-memory inventory host `atlas_target`.
Používateľ ani API neposiela názov playbooku alebo task súboru. Check aj
platforma sa prekladajú cez interné allow-listy v Pythone a v role.

Credentials sa Ansible Runneru odovzdávajú iba ako environment konkrétneho
procesu. `suppress_env_files=True` zabraňuje zápisu env/extravars súborov.
Runner zároveň nastaví procesné `USER=atlas` a `LOGNAME=atlas`; nie sú to
device credentials, ale stabilná lokálna identita pre Python/Ansible aj vtedy,
keď hostiteľský runtime UID nemá v kontajnerovom `/etc/passwd` vlastný záznam.
Runner používa adresár vytvorený cez `TemporaryDirectory`, práva `0700`, a celý
adresár vrátane eventov/artifactov sa po jobe odstráni. Event handler uchová iba
kontrakt tasku `ATLAS_RESULT` a posledný failure event; eventy neukladá.
FortiOS modul deklaruje parameter `access_token` ako `no_log`, takže ho Ansible
maskuje. Atlas navyše pred zápisom diagnostického `ansible_message` nahradí
všetky hodnoty credentials a bežné token/password formáty za `[REDACTED]`.

Nasadzovaný beta profil má `ssh_strict_host_keys: false`. Ansible preto pri
Cisco a Aruba kolektoroch prijme aj neznámy SSH host key. Toto zjednodušuje
onboarding, ale neoveruje identitu SSH servera a zvyšuje riziko MITM útoku.
Ak sa strict režim neskôr zapne, backend skopíruje existujúci Oxidized
`known_hosts` do dočasného Runner HOME.

Nasadzovaný beta profil má `fortios_validate_certs: false`. FortiOS sa stále
kontaktuje cez HTTPS, ale Atlas neoveruje CA ani identitu servera. Hodnota sa
nastavuje v `deployment/backend.yaml` a aplikuje cez `sudo atlasctl deploy`;
interný fallback aplikácie je tiež `false`.

FortiGate API token sa v Atlase nevytvára ani mu Atlas nemení oprávnenia.
Prevádzkovateľ ho musí vopred vytvoriť s read-only API profilom obmedzeným na
monitorovacie endpointy potrebné pre zvolené checks; full-admin token nie je
súčasťou podporovaného produkčného nastavenia.

## Prevádzková diagnostika

Filtrované profylaxia a Ansible eventy z backend kontajnera zobrazí:

```bash
sudo atlasctl prophylaxis logs --tail=100
sudo atlasctl prophylaxis logs -f
```

Príkaz akceptuje rovnaké voľby ako `docker compose logs`, napríklad `--tail`,
`--since` a `-f`. Zobrazuje pôvodné collection/Ansible eventy aj asynchrónne
run a per-device eventy. Diagnostika môže obsahovať bezpečný `reason_code`,
platformu, stav, Runner status/return code, názov Ansible eventu a tasku a
redigovaný modulový `msg`. Pre FortiOS sa pridá aj numerický HTTP status a
bezpečný API status, ak ich modul vráti. Credentials, traceback, stderr a raw
výstup zariadenia sa do logu nezapisujú.

## Výsledok

Transport vracia interný `RawCollectorResult`. Python parser ho normalizuje napríklad
na:

```json
{
  "schema_version": 1,
  "device_id": 42,
  "device": "SW01",
  "platform": "cisco-ios",
  "check": "cpu_utilization",
  "status": "ok",
  "values": {
    "current_percent": 12.0,
    "one_minute_percent": 8.0,
    "five_minute_percent": 6.0
  },
  "error_code": null,
  "collected_at": "2026-10-07T10:00:00Z"
}
```

FortiOS parser číta štruktúrovanú hodnotu `results.cpu[0].current`. Voliteľný
jednominútový priemer prevezme iba vtedy, ak ho zariadenie skutočne vráti.
Chýbajúce hodnoty zostanú `null`; nevypĺňajú sa odhadom.

SQLite používa všeobecnú tabuľku `check_results`. Pri prvom otvorení migruje
existujúce CPU riadky z `cpu_check_results` na check ID `cpu_utilization`.

## Pripnuté dependencies

Python balíky:

```text
ansible-core==2.21.5
ansible-pylibssh==1.4.0
ansible-runner==2.4.3
```

Collections v `automation/collections/requirements.yml`:

```text
ansible.netcommon 8.6.2
ansible.utils 6.1.0
cisco.ios 11.5.1
community.ciscosmb 1.0.12
fortinet.fortios 2.6.0
```

Backend image ich inštaluje do `/usr/share/ansible/collections`; nepoužíva
nekontrolované `latest`.
