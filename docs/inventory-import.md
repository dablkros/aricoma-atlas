# NetBox Inventory Importer

Atlas používa existujúci NetBox REST klient a API token uložený v OpenBao.
Importer nepridáva službu ani databázu a nespúšťa Oxidized, Zabbix alebo
profylaxiu.

## Dátový model

`Tenant` predstavuje zákazníka, `Site` jeho fyzickú alebo logickú lokalitu a
`Device` patrí súčasne konkrétnemu Tenantovi aj Site. Site vytvorený Atlasom je
tiež priradený k danému Tenantovi. Predvoleného zákazníka možno zadať pri
`atlasctl configure`; po NetBox baseline ho deployment vytvorí alebo bezpečne
znovu použije podľa jednoznačného slugu.

Bez zadaného zákazníka sa tenant provisioning preskočí a pôvodný deployment
flow zostáva zachovaný.

## Tenant a lokality

```bash
sudo atlasctl inventory tenant add --name "ACME Slovakia"
sudo atlasctl inventory site add --tenant acme-slovakia --name "Bratislava DC"
sudo atlasctl inventory site add --tenant acme-slovakia --name "Zilina Office"
sudo atlasctl inventory site set-default \
  --tenant acme-slovakia \
  --name "Bratislava DC"
```

Tieto príkazy vykonajú zmenu predvolene; `--dry-run` iba zobrazí plán. Slug je
deterministický. Existujúci rovnaký slug s iným názvom alebo Site priradený
inému Tenantovi je konflikt a Atlas objekt neprepíše.

`site set-default` Site idempotentne vytvorí alebo znovu použije a uloží ju do
chránenej runtime konfigurácie. Import ju použije, ak riadok CSV/XLSX ani
parameter `--site` neurčujú inú Site. Pri prvom `atlasctl configure` sa názov
predvolenej Site zadáva hneď po Tenantovi a deployment pripraví oba objekty.

## CSV a XLSX

Povinné stĺpce sú `Hostname`, `Hardware model` a `Management IP`. Hodnota
`Hardware model` môže byť presný model, slug alebo PID/part number z Device Type
Library. Voliteľné sú
`Tenant`, `Site`, `Device Role`, `Platform` a `VRF`. Ukážka je v
`examples/inventory/acme-devices.csv`.

### Import cez Atlas Operations UI

Bežný operátor nemusí pripravovať runtime súbor ani používať textový editor.
V Atlas Operations UI otvorí v časti **Operator tools** položku **Import
devices** a vykoná tento kontrolovaný postup:

1. vloží tabuľku skopírovanú z Excelu alebo obsah CSV;
2. voliteľne určí spoločný Tenant a Site, inak sa použijú hodnoty z tabuľky
   alebo predvolená runtime konfigurácia;
3. spustí **Create dry-run plan** bez zápisu do NetBoxu;
4. vyrieši všetky riadky `INVALID`, `CONFLICT` alebo `ERROR`;
5. ak chýba Device Type, vloží jeho YAML priamo v okne; Atlas ho schémovo
   overí, uloží do chráneného runtime katalógu a automaticky zopakuje plán;
6. skontroluje presný výsledok, označí potvrdzovacie pole a až potom použije
   **Apply approved plan**.

Počas dlhšieho dry-runu UI zobrazuje spinner, aktuálnu fázu a uplynutý čas.
Nezobrazuje vymyslené percentá: prvá verzia endpointu je synchrónna a backend
neposkytuje počet dokončených krokov. Začiatok a dokončenie operácie sú zároveň
zapísané do štruktúrovaného backend logu.

Apply je viazané SHA-256 odtlačkom na presný obsah tabuľky a zadané Tenant/Site
hodnoty. Zmena ktoréhokoľvek vstupu zneplatní potvrdenie. Backend bezprostredne
pred zápisom zopakuje dry-run a odmietne operáciu, ak sa objaví neplatný,
konfliktný alebo chybový riadok. API operácie vyžadujú runtime operator token;
token ani vložené dáta sa neukladajú do browser storage a po zatvorení karty
sa nezachovávajú.

GUI prijíma tabulátor, čiarku alebo bodkočiarku ako oddeľovač. XLSX súbor možno
otvoriť v tabuľkovom editore a označené bunky priamo skopírovať do okna. CLI
import zostáva k dispozícii pre automatizáciu a natívne `.xlsx` súbory.

Súbor mimo runtime Atlasu možno bezpečne nahrať jedným príkazom:

```bash
sudo atlasctl inventory upload ~/customer-devices.csv
```

Príkaz vytvorí chránený runtime adresár, nastaví vlastníka `atlas` a vypíše
cieľovú cestu aj nasledujúci dry-run príkaz. Existujúci súbor prepíše iba s
explicitným `--force`.

```bash
sudo atlasctl inventory import /secure/devices.xlsx \
  --tenant acme-slovakia \
  --dry-run \
  --report-json /secure/inventory-plan.json

sudo atlasctl inventory import /secure/devices.xlsx \
  --tenant acme-slovakia \
  --apply \
  --report-json /secure/inventory-result.json
```

Import je bez `--apply` vždy dry-run a neposiela POST ani PATCH. Tenant z CLI
platí pre celý súbor. Site z CLI alebo uložená predvolená Site sa použije pre
prázdny stĺpec; explicitne odlišná Site v riadku je `CONFLICT`. Bez CLI Tenanta sa použije Tenant uložený
v `.runtime/inventory.yaml`.

## Rozlíšenie objektov

Device Type sa rozlišuje ako jednoznačná presná zhoda modelu alebo
nakonfigurovaného aliasu. Platforma sa prevezme z overeného
`DeviceType.default_platform`; explicitná odlišná platforma je konflikt.
Device Role sa vyberá v poradí: riadok, mapovanie modelu/platformy, predvolená
rola. Atlas baseline pri deploymente idempotentne vytvára štandardné role
`Network Device`, `Switch`, `Router`, `Firewall`, `Load Balancer` a
`Management Appliance`. Existujúce role s rovnakým slugom neupravuje.

Základné jednoznačné modelové a platformové mapovania sú súčasťou runtime
konfigurácie. Hodnota v `inventory.role_mappings` ich môže pre konkrétneho
zákazníka prepísať. Stĺpec `Device Role` má vždy najvyššiu prioritu. Atlas
neurčuje rolu z hostname a pri nejednoznačných rodinách, napríklad spoločnej
platforme pre routery aj switche, použije explicitné modelové pravidlo alebo
bezpečný fallback `network-device`.

## Dohľadanie a pridanie Device Type

Pred importom možno model explicitne vyhľadať v troch lokálnych zdrojoch:
v aktuálnom NetBoxe, v zostavenom Atlas katalógu a v checkout-e oficiálnej
Device Type Library na commite pripnutom v `catalog/manifest.yaml`.

```bash
sudo atlasctl inventory device-type search "CBS350-16T-2G"
sudo atlasctl inventory device-type search "CBS350-16T-2G" --json
sudo atlasctl inventory device-type add "CBS350-16T-2G" --dry-run
sudo atlasctl inventory device-type add "CBS350-16T-2G" --apply
```

`search` nič nemení. Výsledok rozlišuje `NETBOX`, `ATLAS_CATALOG`,
`PINNED_UPSTREAM` a `ATLAS_LOCAL`. Samostatný príkaz `add` predvolene iba
pripraví dry-run a môže zosúladiť definíciu z aktuálneho zostaveného Atlas
katalógu alebo z ručne dodanej lokálnej definície. Atlas nesťahuje Device Type
YAML z externých URL.

Ak `search` nenájde model v NetBoxe, Atlas katalógu ani pripnutom verejnom Git
repozitári, vypíše adresár pre manuálne dodanie definície:

```text
/opt/aricoma-atlas/.runtime/inventory/device-types/
```

Súbor treba nahrať ako YAML čitateľný servisným účtom `atlas`, napríklad:

```bash
sudo install -o atlas -g atlas -m 0640 \
  ./check-point-smart-1-600-m.yaml \
  /opt/aricoma-atlas/.runtime/inventory/device-types/check-point-smart-1-600-m.yaml

sudo atlasctl inventory device-type search "Smart-1 600-M"
sudo atlasctl inventory device-type add "Smart-1 600-M" --dry-run
sudo atlasctl inventory device-type add "Smart-1 600-M" --apply
```

Lokálny YAML zostáva mimo Git a Atlas ho neprepisuje. Pred provisioningom musí
prejsť rovnakou schémovou a komponentovou validáciou ako zostavený katalóg.
Za pôvod a oprávnenie na použitie ručne dodaného súboru zodpovedá operátor;
Atlas externý obsah automaticky nezískava ani nedistribuuje.

Rovnaký adresár je pripojený do backendu na
`/run/atlas/inventory/device-types`. YAML vložený cez Operations UI preto
prežije opätovné vytvorenie kontajnera a je dostupný aj následnému CLI importu.

CSV/XLSX import má integrovaný preflight. Pre každý chýbajúci model postupuje:

1. existujúci Device Type v NetBoxe;
2. jednoznačná zhoda v aktuálnom zostavenom Atlas katalógu;
3. jednoznačná zhoda v Device Type Library na commite pripnutom v manifeste;
4. ručne dodaná definícia v `ATLAS_LOCAL`, iba ak verejný repozitár model
   neobsahuje.

V dry-run režime sa upstream definícia iba validuje a vo výsledku sa zariadenie
označí operáciou `CREATE_DEVICE_TYPE_DEVICE_AND_MANAGEMENT`; NetBox sa nemení.
V apply režime Atlas najskôr cez existujúci provisioner vytvorí Manufacturer,
potrebnú Platformu, Device Type a jeho component templates a až potom pokračuje
v importe zariadení. Nepripnutý checkout, zastaraný Atlas katalóg, nejednoznačná
zhoda alebo nevalidná YAML definícia sa pre príslušné riadky skončí explicitným
`INVALID` dôvodom a Atlas typ neuhádne.

Nový Device má stav `active`, Tenant aj Site a tieto bezpečné hodnoty:

```text
oxidized_enabled = false
monitoring_enabled = false
profylaxia_enabled = false
profylaxia_checks = []
```

## Management IP a VRF

Importer vytvorí alebo znovu použije `mgmt0`, priradí IP a nastaví
`primary_ip4`. Samotná IPv4 dostane `/32`; dodaný CIDR zostane zachovaný.
Existujúca IP sa vyhodnocuje v konkrétnom VRF kontexte. Importer nevytvára
Prefix, VLAN ani VRF a nepredpokladá IP oddelenie podľa Tenant/Site.

## Výsledky a opakovanie

Každý riadok má stav `CREATE`, `UNCHANGED`, `INVALID`, `CONFLICT` alebo `ERROR`.
JSON report obsahuje vstupnú identitu, operáciu, dôvod a známe NetBox ID, ale
nie tokeny ani credentials. Zodpovedajúci existujúci stav nevytvára ďalší
zápis; po čiastočnom zlyhaní je preto bezpečné import zopakovať.

## Obmedzenia prvej verzie

- neznáme Sites, zákaznícke Roles, Platformy a VRF sa nevytvárajú; štandardné
  Atlas Device Roles vytvára baseline. Device Type sa môže
  doplniť iba z aktuálneho Atlas katalógu, oprávneného `ATLAS_LOCAL` alebo
  pripnutého upstream checkout-u;
- externé URL sa automaticky nesťahujú; YAML musí operátor vložiť do určeného
  runtime adresára;
- existujúce konfliktné Devices ani IP adresy sa automaticky neopravujú;
- podporované sú CSV a XLSX, nie starý binárny formát XLS;
- vstup musí byť dostupný servisnému účtu `atlas`; odporúčaný je absolútny
  chránený path.
