# Centralizovaný multi-tenant NetBox

Táto etapa pridáva dátovú izoláciu zákazníkov bez implementácie VPN klientov,
kontajnerov alebo policy routingu.

## Vlastníctvo objektov

- jeden NetBox `Tenant` predstavuje jedného zákazníka;
- Tenant môže voliteľne patriť do idempotentne vytvorenej skupiny, napríklad
  `customers` alebo `internal`; existujúci Tenant sa automaticky nemigruje;
- nový `Site` vždy patrí Tenantovi a používa globálne unikátny slug;
- `Device` musí patriť tomu istému Tenantovi ako jeho Site;
- management `VRF`, `Prefix` a `IP Address` patria rovnakému Tenantovi;
- management IP je priradená management interfacu a nastavená ako `primary_ip4`.

Atlas existujúce objekty automaticky nepremenuje ani nemigruje. Existujúci
Site s legacy slugom sa znovu použije iba pri jednoznačnej zhode názvu a
Tenanta. Cross-tenant väzba je konflikt.

## Connection mapping

Baseline pridáva textové custom field `atlas_connection_id` na Site a Device.
Device hodnota má prioritu, Site je predvolená hodnota. Identifikátor odkazuje
na perzistentný SQLite VPN Connection Registry. Inventory YAML nie je registrom
spojení a neobsahuje paralelný zoznam profilov. Metadata a pravidlá resolvera
sú opísané v [VPN Connection Registry](vpn-connection-registry.md).

Resolver prijme Device ID a vráti Device, Tenant, Site, management IP/VRF,
Connection ID, zdroj mapovania a netajné metadata. Overuje Tenant na Site, IP,
VRF aj registrovanom spojení. Chýbajúce mapovanie vracia `unmapped`;
neexistujúce, cross-tenant alebo VRF-conflicting spojenie je validačná chyba.
Resolver nikdy nevyberá iné dostupné spojenie ako fallback.

## Manuálna akceptácia na Ubuntu VM

1. Spustite deployment a overte, že baseline vytvoril custom field
   `atlas_connection_id` pre Site a Device.
2. V Operations UI vytvorte Tenant cez dry-run, potvrdenie a apply. Zopakovaný
   plán musí vrátiť `UNCHANGED`.
3. V tom istom okne vytvorte dva Sites a skontrolujte tenant-prefixované slugy.
4. Vytvorte management VRF cez `atlasctl inventory vrf add` najprv s
   `--dry-run`, potom s `--apply`; opakovanie nesmie vytvoriť duplicitu.
5. Pre dvoch Tenantov vytvorte odlišné VRF a importujte rovnakú management IP
   s príslušným VRF. NetBox musí uchovať dva IP objekty.
6. Vytvorte VPN Connection cez autorizované REST API a do OpenBao manuálne
   vložte secret na presnej ceste `atlas/vpn/connections/<connection_id>`.
7. Nastavte `atlas_connection_id` na Site, voliteľne iný override na Device,
   a overte resolver cez `atlasctl vpn resolve --device-id ID`. Cross-tenant
   spojenie musí byť odmietnuté.
8. Overte, že existujúci Tenant `aricoma-systems`, Site `DCmartin` a hodnoty
   existujúcich custom fields zostali nezmenené.

Táto etapa zámerne nevytvára VPN spojenia a nespúšťa sieťové úlohy cez profil
so stavom `planned` alebo `disabled`.
