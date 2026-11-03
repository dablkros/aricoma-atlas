# Izolovaný VPN worker a profylaxia

Samostatný privilegovaný worker vytvára dočasný tunel pre connectivity test
alebo jeden beh profylaxie a vždy ho ukončí. OpenVPN profylaxia používa
neprivilegovaný collector kontajner, ktorý zdieľa iba network namespace
workera; Ansible preto nemá `NET_ADMIN`.

## Implementovaný testovací tok

Backend najprv cez existujúci resolver overí, že vybraný aktívny Device patrí
rovnakému Tenantovi a že jeho Site alebo explicitný Device override ukazuje na
presne testované `connection_id`. Worker dostane iba identifikátory, overené
gateway metadata, cieľovú IPv4 adresu a TCP port; nedostane VPN heslá cez API.
Vlastnou read-only AppRole identitou načíta presný OpenBao leaf, vytvorí
krátkodobý swanctl config, spustí patched strongSwan 6.1.0, vyžiada CHILD SA a
otestuje TCP spojenie. Výsledok odlišuje vytvorený tunel od dostupnosti cieľa.

Windows FortiClient XML s klasickým SSL-VPN username/password profilom sa
importuje ako `ssl-vpn` + `openfortivpn`. Worker odovzdá heslo iba cez dočasný
konfiguračný súbor `0600`, spustí PPP/TLS klienta, nájde nové PPP rozhranie,
vykoná rovnaký TCP test a proces aj rozhranie ukončí. SSO/SAML, interaktívne
SSO/SAML a macOS SSL profil nie sú v tomto reze podporované.
TLS overovanie sa nevypína a import neukladá exportné heslo ani PSK, pretože
klasické SSL-VPN ich nepoužíva.

OpenVPN `.ovpn` import akceptuje iba presnú allowlist klientskych direktív,
`dev tun`, jednu `remote`, inline `<ca>`, `<cert>` a `<key>` a interné
`auth-user-pass`. Externé súbory, skripty a pluginy sa odmietnu. Celý validovaný
profil je secret v OpenBao; worker ho zapisuje iba do krátkodobého súboru
`0600`. Externé MFA je explicitná vlastnosť registry, nie odhad z profilu.
Nový import vyžaduje režim `none`, `push` alebo `otp`; staršie záznamy s
`external_mfa` zostávajú podporované ako kompatibilný režim `auto`.

FortiGate IPsec s e-mailovým FortiToken kódom používa asynchrónny testovací
tok. Worker najprv vykoná PSK a EAP-MSCHAPv2 s prihlasovacím heslom. Až keď
gateway požiada o druhé EAP Identity a EAP-GTC odpoveď, worker označí operáciu
ako `waiting_for_otp` a UI zobrazí jednorazové pole. Kód sa odošle do
krátkodobého súboru `0600`, spotrebuje ho iba patchnutý EAP-GTC modul a súbor
sa ihneď odstráni. Kód sa neukladá do OpenBao, registry ani výsledku API.
Pri režime `push` UI nezobrazí OTP pole a worker čaká na externé potvrdenie.
Ak gateway napriek tomu vyžiada EAP-GTC, test bezpečne skončí s dôvodom
`unexpected_otp_challenge`. Výsledok obsahuje iba bezpečný `reason_code` a
fázu zlyhania; stdout, heslá, PSK ani OTP sa do API a logov neposielajú.

`finally` vetva ukončí IKE SA aj daemon, flushne XFRM state/policy iba vo
workerovom network namespace a odstráni profilový fragment. Globálny zámok
odmietne druhý súbežný test. Backend nemá `NET_ADMIN`; worker má iba
`NET_ADMIN`/`NET_RAW`, read-only root filesystem, tmpfs `/run` a súkromný
control network chránený náhodným bearer tokenom. Worker neposkytuje generický
shell ani Docker socket.

## Overené hranice

- ARICOMA: Ubuntu 24.04, strongSwan 6.1.0 s experimentálnym notify 61520,
  Network ID 11, PSK + EAP-MSCHAPv2, IKE aj CHILD SA a prenos ping/DNS.
- VPN_Viena: dodaný FortiClient PCAP a `iked.log` potvrdili Network ID 11,
  Fortinet Vendor ID, notify 61720, prvé EAP-MSCHAPv2 kolo a druhé EAP-GTC
  kolo s interaktívnym FortiToken promptom. Úspešný Atlas tunel tým ešte nie je
  potvrdený.
- Zdrojové patche pre strongSwan 6.1.0 sa aplikujú na presný kontrolovaný
  upstream archív. Ich kompiláciu v cieľovom image a reálne vyjednávanie s
  FortiGate treba ešte overiť na Ubuntu VM.
- Trvalý `runtime_status` zostáva `not_deployed`: MFA test používa krátkodobú
  asynchrónnu operáciu, ale po každom výsledku sa odpojí. Stav `passed` sa
  neukladá ako aktívna VPN.

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
Zostavený swanctl, openfortivpn alebo OpenVPN config je citlivý a smie existovať iba v
privátnom krátkodobom súborovom priestore workera s režimom `0600`.

Komunikačné rozhranie workeru musí byť explicitne typované, overovať klienta a
nesmie poskytovať generické vykonávanie shell príkazov alebo Docker socket.
Nesmie umožniť zmeniť Tenant, connection ID či cieľ po validácii v registri.
Sieťové pravidlá musia zabezpečiť, aby zákaznícka prevádzka neunikla mimo VPN,
ani počas reconnectu alebo zlyhania tunela. Návrat DNS a routovania sa musí
riešiť v izolovanom priestore, nie prepisovaním DNS fyzického hostiteľa.

## Zostávajúca akceptácia a ďalší P2 rozsah

Startup workeru a jeho healthcheck možno diagnostikovať podporovaným príkazom
`sudo atlasctl vpn logs --tail=200`. Výstup môže obsahovať technické chyby
procesov, nesmie však obsahovať PSK ani používateľské heslá.

Akceptácia na Ubuntu VM musí overiť reálny build patched image, IKE/CHILD,
úspešný aj neúspešný TCP cieľ, timeout a cleanup aj druhý súbežný test.
Samostatne treba overiť aj reálny FortiGate SSL-VPN profil, certifikačný reťazec,
pridelenú PPP adresu, routy a dostupnosť cieľa. Hostiteľ musí poskytovať
char device `/dev/ppp`; compose ho pripája iba workeru. Ak zariadenie nie je
dostupné (napríklad na neprivilegovanom CI runneri), deployment pokračuje bez
PPP mountu, vypíše varovanie a ponechá funkčný backend aj IPsec testy; klasický
SSL-VPN test zostane nedostupný. OpenVPN profylaxia používa dlhšie žijúcu
session s hodinovým watchdogom. IPsec a openfortivpn profylaxia zostávajú mimo
tohto prvého session kontraktu; connectivity test pre ne zostáva zachovaný.
Širšia akceptácia musí overiť aspoň dva Tenants s prekrývajúcimi sa
adresami, reconnect a rekey, DNS, zabránenie úniku prevádzky, cleanup, chyby
OpenBao a nepovolený prístup. Dovtedy sa stav nesmie označiť za `connected`
podľa samotného uloženia profilu.
