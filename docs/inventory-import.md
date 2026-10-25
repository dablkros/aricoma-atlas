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
```

Tieto príkazy vykonajú zmenu predvolene; `--dry-run` iba zobrazí plán. Slug je
deterministický. Existujúci rovnaký slug s iným názvom alebo Site priradený
inému Tenantovi je konflikt a Atlas objekt neprepíše.

## CSV a XLSX

Povinné stĺpce sú `Hostname`, `Hardware model` a `Management IP`. Voliteľné sú
`Tenant`, `Site`, `Device Role`, `Platform` a `VRF`. Ukážka je v
`examples/inventory/acme-devices.csv`.

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
platí pre celý súbor. Site z CLI je predvolená pre prázdny stĺpec; explicitne
odlišná Site v riadku je `CONFLICT`. Bez CLI Tenanta sa použije Tenant uložený
v `.runtime/inventory.yaml`.

## Rozlíšenie objektov

Device Type musí existovať v Atlas katalógu ako jednoznačná presná zhoda modelu
alebo nakonfigurovaného aliasu. Platforma sa prevezme z overeného
`DeviceType.default_platform`; explicitná odlišná platforma je konflikt.
Device Role sa vyberá v poradí: riadok, mapovanie modelu/platformy, predvolená
rola. Importer žiadne z týchto objektov automaticky nevytvára.

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

- neznáme Sites, Device Types, Roles, Platformy a VRF sa nevytvárajú;
- existujúce konfliktné Devices ani IP adresy sa automaticky neopravujú;
- podporované sú CSV a XLSX, nie starý binárny formát XLS;
- vstup musí byť dostupný servisnému účtu `atlas`; odporúčaný je absolútny
  chránený path.
