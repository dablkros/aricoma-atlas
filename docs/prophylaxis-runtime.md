# Profylaxia runtime: Atlas orchestration and Ansible execution

Cisco IOS tematický collector plán, výsledkové stavy a report kontrakt sú
zdokumentované v [prophylaxis-cisco-ios.md](prophylaxis-cisco-ios.md).
FortiOS transporty, očakávania a kontrolná matica sú v
[prophylaxis-fortios.md](prophylaxis-fortios.md).

Stav dokumentácie: 2026-10-20. Implementácia zachováva Atlas ako orchestrátor;
Ansible je iba execution vrstva pre komunikáciu so zariadeniami.

## Flow

```text
Atlas UI/API
  -> ProphylaxisService.collect(...) alebo DeviceFactsRunService
  -> existujúci NetBoxService
  -> active + profylaxia_enabled + profylaxia_checks
  -> device/IP/platform/vendor/credential_profile
  -> NetBox Application Service -> connection port
  -> existujúci OpenBaoService a prophylaxis credential namespace
  -> AnsiblePlatformRegistry
  -> AnsibleBackend / ansible-runner
  -> read-only Ansible collection module
  -> RawCollectorResult
  -> platform-specific Python parser
  -> normalizovaný check result
  -> SQLite história alebo
     runs/<run_id>/{result.json,report-data.json,report.txt} / API / UI
```

Nevzniká druhý NetBox ani OpenBao klient. Oxidized naďalej používa SSH polia
`username`, `password` a voliteľné `enable_password` z:

```text
devices/credentials/<manufacturer>/<credential_profile>/oxidized
```

Profylaxia používa samostatný účelový secret:

```text
devices/credentials/<manufacturer>/<credential_profile>/prophylaxis
```

Pre FortiGate s profilom `forti-mt` je cesta:

```text
devices/credentials/fortinet/forti-mt/prophylaxis
```

SSH platformy z neho načítajú
`username`, `password` a voliteľný `enable_password`. FortiGate načíta
`api_token`, ak má zariadenie NetBox službu `fortios-api`; bez tejto služby
načíta `username` a `password` pre SSH. Resolver vráti iba polia požadované
zvoleným transportom.

Pri upgrade skopíruj iba potrebné hodnoty z existujúceho `admin` secretu do
nového `prophylaxis` secretu cez autentifikované OpenBao UI. Atlas nemá fallback
na `admin`, aby profylaxia nemohla omylom použiť širšie administrátorské
oprávnenia. Chýbajúci alebo neplatný FortiGate token sa vracia ako
`api_token_missing_or_invalid`; nejde o Ansible connection failure.

## Port služby z NetBoxu

Pre platformu `fortios` Atlas vyhľadá na konkrétnom zariadení Application
Service s presným názvom `fortios-api`. Z NetBox 4.7 poľa `port_mappings`
akceptuje práve jednu TCP hodnotu, napríklad:

```text
fortios-api -> TCP/444 -> ["tcp/444"]
```

Port sa odovzdá do `ansible.netcommon.httpapi` ako `ansible_httpapi_port`.
Atlas porty aktívne neskenuje. Ak služba `fortios-api` na zariadení neexistuje,
Atlas vyberie SSH na porte 22 a spustí iba allow-listed príkazy
zoskupené v rovnakých tematických collectoroch ako API. Chyba existujúceho API
spojenia nespustí SSH fallback. Viac služieb s rovnakým názvom alebo viac port
mappings skončí s
`ambiguous_service_port`; iný protokol, neplatný port alebo neplatná odpoveď
skončí s `invalid_service_port`. Port sa vyhodnotí ešte pred čítaním secretu.

Kontrakt zodpovedá NetBox 4.7.1, ktorý reprezentuje mapovania ako zoznam
`protocol/port` reťazcov a podporuje REST filtre `device_id` a `name`:

- [NetBox 4.7.1 Service model](https://github.com/netbox-community/netbox/blob/v4.7.1/docs/models/ipam/service.md)
- [NetBox 4.7.1 ServiceFilterSet](https://github.com/netbox-community/netbox/blob/v4.7.1/netbox/ipam/filtersets.py#L1292-L1368)

## Skupiny a stabilné check ID

NetBox používateľ vyberá skupiny:

```text
identity
resources
interfaces
compliance
operations
```

Atlas ich rozbalí na interné výsledky. Implementované alebo explicitne
plánované výsledky zahŕňajú:

```text
cpu_utilization
device_facts
os_version
cpu_temperature
uptime
memory_utilization
environment_health
fan_health
interface_state
interface_utilization
interface_error_counters
license_state
pki_certificates
ntp_sync
recent_log_events
routing_table
routing_protocol_neighbors
ha_status
configuration_backup
session_health
stability_events
storage_health
fortiguard_status
acme_status
sdwan_health
vpn_tunnels
log_delivery
```

`device_facts` normalizuje hostname, model, nainštalovaný OS identifikátor,
sériové číslo a uptime v sekundách; samostatné kontroly `os_version` a `uptime`
používajú ten istý identity/facts collector, takže výber skupiny `identity`
nevytvorí tri collector joby. Hromadný beh zostaví plán zo skupín v
`profylaxia_checks` každého zariadenia. Registry vráti pre známu platformu a
neimplementovaný výsledok stav `unsupported`; beh ho uloží ako `skipped` s
kódom `unsupported_check`, nevydáva ho za connection error a nevytvára nulovú
hodnotu. Počas migrácie resolver akceptuje aj rozpoznané staré individuálne
hodnoty. Historické hodnoty `Routing table`, `Routing protocol neighbors` a
`HA status` sa pri migrácii zachovajú prostredníctvom skupiny `operations`.
Cisco IOS, IOS-XE a CBS majú pre tematické skupiny read-only collectory;
NX-OS a ASA majú zatiaľ read-only identity collector. Neznámy formát ostáva
`UNKNOWN`/`UNSUPPORTED`. `firmware_recommendation` a `eol_status` sa
nevyhodnocujú z CLI; pre podporované Cisco platformy ich poskytujú Cisco
Software Suggestion API a Cisco EoX API. Nové NetBox UI individuálne voľby
neponúka.

FortiOS `firmware_recommendation` oddeľuje dve rôzne tvrdenia. Pole
`firmware_update_available` opisuje iba aktuálny katalóg dostupný konkrétnemu
zariadeniu: cez SSH sa získava príkazom `diagnose fdsm image-list`, cez API
selectorom `system_firmware`. Hodnota je `true` iba pri explicitnom označení
`upgrade`. Hodnota `false` sa pri SSH nastaví iba vtedy, keď počet parsovaných
záznamov zodpovedá deklarovanému `Total` a žiadny záznam nie je `upgrade`;
neúplný alebo nejednoznačný výstup ostáva `null`/`UNKNOWN`.
`firmware_recommended_version` ostáva `null`, kým Atlas nemá samostatný
autoritatívny zdroj odporúčanej FortiOS vetvy. Absencia položky `upgrade`
preto neznamená, že nainštalovaná verzia je najnovšia alebo odporúčaná. Atlas
neporovnáva FortiOS verzie lexikograficky a nevyberá najvyššiu verziu zo
zoznamu downgrade obrazov.

Zdroje kontraktu: FortiOS 8.0 CLI referencia pre
[`diagnose fdsm`](https://docs.fortinet.com/document/fortigate/8.0.0/cli-reference/293744549/diagnose-fdsm),
Fortinet príklad významu
[`upgrade`/`downgrade`](https://docs.fortinet.com/document/fortigate/8.0.0/new-features/153433/enhancements-to-required-upgrade-when-firmware-license-is-invalid-or-device-is-eoes)
a dokumentácia `fortinet.fortios` 2.6.0 pre API selector
[`system_firmware`](https://docs.ansible.com/projects/ansible/latest/collections/fortinet/fortios/fortios_monitor_fact_module.html).

`configuration_backup` je vyhodnotenie existujúceho Oxidized flow. Atlas načíta
`nodes.json`, vyžaduje presnú zhodu názvu zariadenia a kontroluje `last.status`.
Úspešný posledný job je `PASS`; chýbajúci uzol alebo ešte neukončený backup je
`WARNING` s požiadavkou na manuálnu kontrolu; neúspešný posledný job je `FAIL`.
Čas posledného dokončenia a vek v hodinách sú iba informačné metriky, pretože
prahová hodnota čerstvosti ešte nie je nakonfigurovaná. Profylaxia nespúšťa
backup ani nečíta jeho obsah.

Oxidized 0.37.0 serializuje pre uzol `name`, `mtime` a voliteľný posledný job s
poľami `start`, `end`, `status` a `time`:
[Oxidized 0.37.0 `Node#serialize`](https://github.com/ytti/oxidized/blob/0.37.0/lib/oxidized/node.rb#L86-L105).

FortiOS PKI parser ukladá počet certifikátov, najkratšiu zostávajúcu platnosť a
počet už expirovaných certifikátov. V tejto fáze nad nimi neuplatňuje Atlas
warning/critical thresholdy. Stav automatickej obnovy zostáva `unverified`;
samotná dlhá platnosť ju nedokazuje. Existujúce Cisco parsery si zachovávajú
svoj doterajší kontrakt.

Profylaxia zároveň vykonáva interné kontroly `serial_inventory` a
`model_inventory` pre každý aktívny device s `profylaxia_enabled=true`. Ak plán
ešte neobsahuje identity/facts collector, pridá jeden collector job bez pridania
používateľského checku. Jeden čerstvý detail zariadenia z NetBoxu poskytne
uložené hodnoty pre obe porovnania.

## Všeobecná uptime politika

Uptime sa vyhodnocuje až nad normalizovanou hodnotou `uptime_seconds`, preto je
politika spoločná pre platformy s existujúcim hodnotiacim kontraktom. FortiOS
je v tejto iterácii collection-first: uptime uloží ako `UNKNOWN` a nepoužije
nasledujúce prahy, kým nebude nakonfigurovaná explicitná politika:

| Hodnota | Evaluation | Reason code |
| --- | --- | --- |
| menej než 3 dni | `WARNING` | `uptime_too_short` |
| 3 až 365 dní vrátane | `PASS` | bez reason kódu |
| viac než 365 dní | `WARNING` | `uptime_too_long` |

Krátky uptime upozorňuje na nedávny reload alebo výpadok. Uptime dlhší než rok
upozorňuje na zariadenie bez reštartu a potrebu preveriť patch/reboot okno.
Oba stavy patria v reporte do časti `Naplánovať`; samotný zber pritom zostáva
`COLLECTED` a nestratí pôvodnú hodnotu v sekundách. Operations UI a textový
report zobrazia uptime v ľudskom formáte, napríklad
`1 rok 2 dni 3 hodiny 4 minúty`; JSON/API naďalej uchováva presné
`uptime_seconds` pre strojové spracovanie.

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

| NetBox platform | Connection | Network OS | identity/facts collector |
| --- | --- | --- | --- |
| `cisco-ios` | SSH `network_cli`; explicit Telnet fallback | `cisco.ios.ios` | `show version` + `show inventory`, NTC Templates |
| `cisco-ios-xe` | `ansible.netcommon.network_cli` | `cisco.ios.ios` | `show version` + `show inventory`, NTC Templates |
| `cisco-cbs` | `ansible.netcommon.network_cli` | `community.ciscosmb.ciscosmb` | `community.ciscosmb.command` + NTC Templates `cisco_s300` |
| `cisco-cbs250-350` | `ansible.netcommon.network_cli` | `community.ciscosmb.ciscosmb` | `community.ciscosmb.command` + NTC Templates `cisco_s300` |
| `cisco-nxos` | `ansible.netcommon.network_cli` | `cisco.nxos.nxos` | `nxos_command` + NTC `show version`/`show inventory` |
| `cisco-asa` | `ansible.netcommon.network_cli` | `cisco.asa.asa` | `asa_command` + NTC `show version`/`show inventory` |
| `aruba-aos-cx` | `ansible.netcommon.network_cli` | `arubanetworks.aoscx.aoscx` | `aoscx_command` + NTC `show system` |
| `fortios` | `ansible.netcommon.httpapi` pri službe `fortios-api`; inak lokálny Ansible modul cez SSH/Paramiko | `fortinet.fortios.fortios` pre API; bez `network_os` pre SSH | API `system_status` + `web-ui/state`, alebo CLI `get system status` + `get system performance status` |
| `checkpoint-gaia` | lokálny Ansible modul cez SSH/Paramiko | Gaia Clish | allow-list `show hostname`, `show asset all`, `show version all`, `show uptime` |

`checkpoint-gaia` podporuje v tejto iterácii `device_facts`, `os_version`,
`uptime`, `serial_inventory` a `model_inventory`. Modul vyžaduje, aby SSH účet
otváral Gaia Clish, a odmietne Expert prompt. CPU collector nie je zapnutý,
pretože zdokumentovaný `cpstat -f cpu os` používa Expert mode; jeho povolenie
vyžaduje samostatne overený read-only zdroj (napríklad konkrétny SNMP OID alebo
API kontrakt). `checkpoint-gaia-embedded` zostáva mimo tohto profilu.

Cisco IOS aj IOS-XE plánujú tematické collectory `ios_identity`,
`ios_resources`, `ios_interfaces`, `ios_compliance` a `ios_operations`. Obe
platformy používajú `show processes cpu`. Cisco CBS
samostatne používa `show cpu utilization`. FortiOS používa monitor selector
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
- [cisco.nxos 11.2.0](https://docs.ansible.com/projects/ansible/latest/collections/cisco/nxos/index.html)
- [cisco.asa 6.1.0](https://docs.ansible.com/projects/ansible/11/collections/cisco/asa/index.html)
- [community.ciscosmb 1.0.12](https://docs.ansible.com/projects/ansible/latest/collections/community/ciscosmb/index.html)
- [community.ciscosmb.command](https://docs.ansible.com/projects/ansible/latest/collections/community/ciscosmb/command_module.html)
- [fortinet.fortios 2.6.0](https://docs.ansible.com/projects/ansible/latest/collections/fortinet/fortios/index.html)
- [fortios_monitor_fact](https://docs.ansible.com/projects/ansible/latest/collections/fortinet/fortios/fortios_monitor_fact_module.html)
- [Aruba AOS-CX collection](https://github.com/aruba/aoscx-ansible-collection)
- [Paramiko 4.0.0](https://pypi.org/project/paramiko/4.0.0/)
- [NTC Templates 9.3.0](https://github.com/networktocode/ntc-templates/tree/v9.3.0/ntc_templates/templates)

Aruba `aoscx_command` používaný cez `network_cli` vyžaduje na Ansible control
node knižnicu Paramiko. Backend image ju preto inštaluje ako explicitne
pripnutú Python runtime závislosť; samotné `ansible-galaxy collection install`
Python requirements kolekcie neinštaluje.

Pozorovaný Check Point Gaia R80.10 endpoint skončil proti Paramiko 5.0.0
chybou `IncompatiblePeer`. Paramiko 5.0.0 už neobsahuje SHA-1 KEX ani
`ssh-rsa`, preto je runtime pripnutý na 4.0.0. Vlastný Gaia Clish modul pridáva
`diffie-hellman-group14-sha1`, následne `diffie-hellman-group1-sha1`, a
`ssh-rsa` až za modernejšie algoritmy. Legacy algoritmus sa preto použije iba
vtedy, keď Gaia neponúkne spoločný modernejší algoritmus; ostatné platformové
profily tento Gaia transport nepoužívajú.

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
sú read-only a zrkadlia tematické IOS sady pre identity, resources, interfaces,
compliance a operations.
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

Nasadzovaný beta profil má `fortios_validate_certs: false`. Pri zariadení so
službou `fortios-api` Atlas neoveruje CA ani identitu HTTPS servera. Hodnota sa
nastavuje v `deployment/backend.yaml` a aplikuje cez `sudo atlasctl deploy`;
interný fallback aplikácie je tiež `false`.

FortiGate API token sa v Atlase nevytvára ani mu Atlas nemení oprávnenia.
Prevádzkovateľ ho musí vopred vytvoriť s read-only API profilom obmedzeným na
monitorovacie endpointy potrebné pre zvolené checks; full-admin token nie je
súčasťou podporovaného produkčného nastavenia.

SSH režim používa účelový read-only účet a pevný allow-list zdokumentovaných
`get`, `diagnose` a obmedzených `execute log` príkazov. Lokálny Ansible modul obsluhuje FortiOS `--More--` stránkovanie
medzerníkom; nemení tým konfiguráciu zariadenia. Zdrojom významu týchto
príkazov je dokumentácia FortiOS:

- [FortiOS 7.6.0 CLI troubleshooting cheat sheet](https://docs.fortinet.com/document/fortigate/7.6.0/cli-troubleshooting-cheat-sheet/420966/cli-troubleshooting-cheat-sheet)
- [FortiOS 7.0.15 Troubleshooting CPU and network resources](https://docs.fortinet.com/document/fortigate/7.0.15/administration-guide/152469/troubleshooting-cpu-and-network-resources)

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
