# FortiClient IPsec: izolovaný pilot

Tento dokument opisuje návrh pokračovania. Register VPN zostáva P1 a žiadny
worker ani endpoint na pripájanie tu ešte nie je implementovaný.

## Overené hranice

- ARICOMA: Ubuntu 24.04, strongSwan 6.1.0 s experimentálnym notify 61520,
  Network ID 11, PSK + EAP-MSCHAPv2, IKE aj CHILD SA a prenos ping/DNS.
- VPN_Viena: IKE_SA_INIT prešiel s Network ID 11; používateľ potvrdil integritu
  EncX s exportným heslom, no úspešnú autentifikáciu ani prenos cez VPN nie.
- Zdrojový patch pre nastaviteľné ID je pripravený proti archívu 6.1.0; jeho
  kompilácia a vyjednávanie s ľubovoľným iným ID ešte nie sú overené.
- Existujúce P1 `runtime_status=not_deployed` a OpenBao metadata-only policy
  sa nemenia, kým worker a jeho autorizácia nie sú zavedené a otestované.

## Izolácia a postupné prepínanie

Pre jeden aktívny zákaznícky profil vznikne samostatný pracovný kontajner s
vlastným sieťovým priestorom. IKE/ESP, routy, DNS a vykonávanie Ansible úloh
pre daného zákazníka patria do toho istého priestoru. Základný API kontajner,
NetBox a OpenBao si ponechávajú vlastnú dostupnosť; zákaznícky default route
nesmie zmeniť ich hostiteľské routy ani DNS. Po ukončení úlohy sa daný priestor
uvoľní. Siete rôznych zákazníkov sa môžu prekrývať, preto sa nesmú smerovať
spoločne cez jedinú hostiteľskú routing tabuľku.

Pred prepnutím na iný profil treba ukončiť predošlé úlohy a CHILD/IKE SA,
uvoľniť súkromné konfiguračné a DNS údaje a potvrdiť neaktívny stav. Jeden
globálny zámok chráni prepnutie proti súbehu medzi API procesmi; timeout alebo
pád musí viesť k obnoviteľnému chybovému stavu, nie k presmerovaniu úlohy do
inej VPN. Podľa existujúceho resolvera musí byť každá cieľová IP v tenantovej
VRF a `allowed_prefixes` pred odoslaním do workera znovu validovaná.

## Tajomstvá a hranice oprávnení

VPN username, password, exportné heslo, PSK ani celý importovaný profil sa
nesmú zapísať do SQLite, auditného logu, výsledku API, argumentov procesu alebo
shell history. OpenBao má používať samostatnú identitu workera so scope na VPN
data; existujúca metadata-only identita backendu nesmie dostať wildcard práva.
Dešifrovanie `Enc` a `EncX` robí kontrolu integrity a skončí chybou pred
použitím pri nezhode. Exportné heslo sa odlišuje od používateľského VPN hesla.
Zostavený swanctl config je citlivý a smie existovať iba v privátnej
krátkodobej pamäti/súborovom priestore workera.

Komunikačné rozhranie workeru musí byť explicitne typované, overovať klienta a
nesmie poskytovať generické vykonávanie shell príkazov alebo Docker socket.
Nesmie umožniť zmeniť Tenant, connection ID či cieľ po validácii v registri.
Sieťové pravidlá musia zabezpečiť, aby zákaznícka prevádzka neunikla mimo VPN,
ani počas reconnectu alebo zlyhania tunela. Návrat DNS a routovania sa musí
riešiť v izolovanom priestore, nie prepisovaním DNS fyzického hostiteľa.

## Pred nasadením P2

Treba implementovať a otestovať vlastnú AppRole, profilový import so zápisom do
OpenBao, životný cyklus kontajnera a VPN, stavový automat, krytie pre súbeh a
obnovu po páde a skutočné vykonávanie existujúceho Ansible backendu v tom istom
priestore. Potrebná je integrácia s existujúcim SQLite registrom, auth/API a UI.
Akceptácia na Ubuntu VM musí overiť aspoň dva Tenants s prekrývajúcimi sa
adresami, reconnect a rekey, DNS, zabránenie úniku prevádzky, cleanup, chyby
OpenBao a nepovolený prístup. Dovtedy sa stav nesmie označiť za `connected`
podľa samotného uloženia profilu.
