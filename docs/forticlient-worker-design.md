# FortiClient IPsec: izolovaný pilot

Tento dokument opisuje implementovaný prvý P2 rez: samostatný worker, ktorý
na žiadosť GUI vytvorí jeden dočasný tunel, overí jeden NetBox Device a tunel
vždy ukončí. Spúšťanie Ansible úloh cez VPN ešte nie je súčasťou tohto rezu.

## Implementovaný testovací tok

Backend najprv cez existujúci resolver overí, že vybraný aktívny Device patrí
rovnakému Tenantovi a že jeho Site alebo explicitný Device override ukazuje na
presne testované `connection_id`. Worker dostane iba identifikátory, overené
gateway metadata, cieľovú IPv4 adresu a TCP port; nedostane VPN heslá cez API.
Vlastnou read-only AppRole identitou načíta presný OpenBao leaf, vytvorí
krátkodobý swanctl config, spustí patched strongSwan 6.1.0, vyžiada CHILD SA a
otestuje TCP spojenie. Výsledok odlišuje vytvorený tunel od dostupnosti cieľa.

`finally` vetva ukončí IKE SA aj daemon, flushne XFRM state/policy iba vo
workerovom network namespace a odstráni profilový fragment. Globálny zámok
odmietne druhý súbežný test. Backend nemá `NET_ADMIN`; worker má iba
`NET_ADMIN`/`NET_RAW`, read-only root filesystem, tmpfs `/run` a súkromný
control network chránený náhodným bearer tokenom. Worker neposkytuje generický
shell ani Docker socket.

## Overené hranice

- ARICOMA: Ubuntu 24.04, strongSwan 6.1.0 s experimentálnym notify 61520,
  Network ID 11, PSK + EAP-MSCHAPv2, IKE aj CHILD SA a prenos ping/DNS.
- VPN_Viena: IKE_SA_INIT prešiel s Network ID 11; používateľ potvrdil integritu
  EncX s exportným heslom, no úspešnú autentifikáciu ani prenos cez VPN nie.
- Zdrojový patch pre nastaviteľné ID je pripravený proti archívu 6.1.0; jeho
  kompilácia a vyjednávanie s ľubovoľným iným ID ešte nie sú overené.
- Trvalý `runtime_status` zostáva `not_deployed`: test je synchrónny a po
  každom výsledku sa odpojí. Stav `passed` sa neukladá ako aktívna VPN.

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
VRF a prípadných explicitných `allowed_prefixes` pred odoslaním do workera
znovu validovaná. Pri Site scope sa cieľ určuje podľa aktuálneho NetBox Device
a Site, nie ručne zadaným zoznamom prefixov.

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

## Zostávajúca akceptácia a ďalší P2 rozsah

Akceptácia na Ubuntu VM musí overiť reálny build patched image, IKE/CHILD,
úspešný aj neúspešný TCP cieľ, timeout, cleanup a druhý súbežný test. Následne
treba doplniť skutočné vykonávanie existujúceho Ansible backendu v rovnakom
network namespace a dlhšie žijúci stavový automat pre produkčné úlohy.
Širšia akceptácia musí overiť aspoň dva Tenants s prekrývajúcimi sa
adresami, reconnect a rekey, DNS, zabránenie úniku prevádzky, cleanup, chyby
OpenBao a nepovolený prístup. Dovtedy sa stav nesmie označiť za `connected`
podľa samotného uloženia profilu.
