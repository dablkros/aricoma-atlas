# Cisco Support API integration

Stav implementácie: zahrnuté v release `v0.4.3`.

## Cieľ

Atlas rozšíri Cisco IOS, IOS-XE, CBS, NX-OS a ASA profylaxiu o dva nezávislé
externé zdroje:

- Cisco Software Suggestion API V2 pre kompatibilné odporúčané vydania;
- Cisco EoX API pre hardvérové a softvérové lifecycle míľniky.

Ansible naďalej iba zbiera identitu zariadenia. Atlas spojí PID, platformu a
nainštalovanú verziu s Cisco dátami a vyhodnotí existujúce kontroly
`firmware_recommendation` a `eol_status`.

## Rozsah prvej iterácie

1. Použiť jednu manuálne vytvorenú KV v2 secret cestu:

   ```text
   atlas/integrations/cisco/support-api
   ```

   Hodnota obsahuje iba:

   ```json
   {
     "client_id": "...",
     "client_secret": "..."
   }
   ```

2. Backend AppRole dostane iba `read` na presnú secret cestu. Žiadny wildcard
   nad ostatnými integráciami a žiadne write/list oprávnenie.
3. Implementovať OAuth 2.0 Client Credentials voči Cisco Common Identity.
4. Implementovať diagnostiku:

   ```console
   sudo atlasctl integrations cisco test
   ```

   Diagnostika beží cez Atlas backend, overí OpenBao secret, OAuth,
   Software Suggestion API a EoX API a vypíše iba bezpečné stavové kódy.
5. Pridať typované klientské kontrakty, klasifikáciu chýb a bezpečné logovanie.
6. Zapojiť Cisco dáta do existujúcich výsledkov profylaxie.

Konfiguračný CLI príkaz sa nepridáva. Operátor vytvorí alebo rotuje secret
manuálne cez OpenBao UI alebo schválený OpenBao administrátorský postup.

## Bezpečnostný kontrakt

- Client Secret ani access token sa nesmú objaviť v logoch, výnimkách,
  reportoch, API odpovediach ani v `repr` objektov.
- Access token sa drží iba v pamäti procesu a nepersistuje sa.
- Všetky Cisco spojenia používajú overenie TLS; neexistuje `verify=False` ani
  operátorská výnimka.
- Backend prijíma Cisco credentials iba z presnej OpenBao cesty.
- Diagnostický endpoint vyžaduje existujúci Atlas operator token.
- Chyba Cisco API nesmie zmeniť úspešný zber zariadenia na device connection
  failure.

## Diagnostický výstup

Príkaz vráti nenulový exit code, ak secret, OAuth alebo niektoré požadované API
nie sú dostupné. Výstup nesmie obsahovať raw odpoveď ani identifikátory
credentials:

```text
oauth: OK
software_suggestion: OK
eox: NOT_AUTHORIZED
overall: PARTIAL
```

Stabilné diagnostické stavy:

- `OK`
- `NOT_TESTED`
- `SECRET_NOT_FOUND`
- `INVALID_SECRET`
- `CONNECTION_FAILED`
- `TLS_VALIDATION_FAILED`
- `AUTHENTICATION_FAILED`
- `NOT_AUTHORIZED`
- `INVALID_RESPONSE`
- `UNAVAILABLE`

HTTP 200 bez použiteľného produktového záznamu potvrdzuje API dostupnosť, nie
pokrytie konkrétneho PID.

## Identifikátory a zdroj dát

Atlas použije NetBox ako zdroj požadovanej identity, ale externý lookup spustí
až po porovnaní s identitou vyzbieranou zo zariadenia:

| Účel | Cisco vstup | Atlas zdroj | Podmienka |
| --- | --- | --- | --- |
| hardvérový EoX | serial number | NetBox `device.serial` | `serial_inventory` je `matched` alebo `populated` |
| Software Suggestion | base PID | NetBox `device_type.model` | hodnota je presný Cisco PID a `model_inventory` je `matched` |
| softvérový EoX | release string + OS type | vyzbierané `os_version` + NetBox platforma | identita zariadenia je overená |

Ak je NetBox serial prázdny, existujúci `serial_inventory` ho najprv doplní zo
zariadenia. Pri serial alebo model mismatch sa Cisco lookup nevykoná a výsledok
ostane `UNKNOWN` s dôvodom identity mismatch. Atlas nesmie potichu použiť inú
identitu, pretože by mohol vyhodnotiť lifecycle iného fyzického zariadenia.

`device_type.model` sa nepovažuje automaticky za PID. Lookup je povolený iba
pre Device Type katalóg, v ktorom je model presný orderable/base PID, napríklad
`CISCO2811`, `WS-C2960X-24TS-LL` alebo `C9200L-24T-4G-E`. Marketingový názov,
séria alebo používateľský popis nie sú platnou náhradou PID.

## Dátový tok profylaxie

1. NetBox vyberie aktívne zariadenie, platformu a profylaxie.
2. Platformový identity collector vyzbiera identitu iba raz. IOS/IOS-XE,
   NX-OS a ASA používajú `show version` a `show inventory`; CBS zachováva
   existujúci zber `show system`, `show version` a `show inventory`.
3. Atlas porovná vyzbieraný chassis PID a serial s NetBox inventárom.
4. Atlas normalizuje nainštalovanú verziu a OS typ podľa platformy:
   `cisco-ios -> IOS`, `cisco-ios-xe -> IOS-XE`,
   `cisco-nxos -> NX-OS`, `cisco-asa -> ASA`. Cisco EoX neuvádza samostatný
   OS typ pre CBS, preto CBS posiela iba presný release string bez druhého
   parametra.
5. Atlas deduplikuje lookupy podľa serialu, PID, verzie a OS typu.
6. Software Suggestion použije overený NetBox PID a vráti odporúčané
   kompatibilné vydania.
7. Hardvérový EoX použije overený NetBox serial. Softvérový EoX sa vyhodnotí
   samostatne podľa presného release stringu a OS typu.
8. Výsledky sa vložia do existujúcich checks bez raw Cisco payloadu.

Podporované Atlas Cisco platformy v tejto iterácii sú `cisco-ios`,
`cisco-ios-xe`, `cisco-cbs`, `cisco-cbs250-350`, `cisco-nxos` a `cisco-asa`.
NX-OS a ASA majú zatiaľ iba spoločný read-only identity collector potrebný pre
inventár a lifecycle kontroly. CBS softvérový EoX dotaz bez OS typu môže vrátiť
širšiu množinu záznamov; chýbajúci alebo nejednoznačný záznam preto zostáva
`UNKNOWN`, nikdy sa neinterpretuje ako podporovaná verzia.

## Výsledkové pravidlá

Software Suggestion rozlišuje:

- presnú zhodu s Cisco suggested vydaním;
- inú odporúčanú kompatibilnú alternatívu;
- známy produkt bez odporúčania;
- produkt bez pokrytia;
- nedostupný alebo neautorizovaný zdroj.

Atlas nebude určovať „najvyššiu“ Cisco verziu lexikografickým ani všeobecným
SemVer porovnaním. Viacero odporúčaných trainov sa zachová ako viacero
rovnocenných vendor výsledkov.

EoX rozlišuje hardvérové a softvérové dátumy. Chýbajúci záznam alebo výpadok
zdroja je `UNKNOWN`, nikdy `PASS` ani dôkaz aktuálnosti. Publikovaný EoX záznam
je najmenej `WARNING`; prekročený `LastDateOfSupport` je `FAIL`.
Vyhodnotenie prejde všetky stránky a všetky záznamy. Do uloženého výsledku sa
z dôvodu veľkosti prenesie najviac 50 normalizovaných záznamov, celkové počty
a príznak skrátenia.

## Cache a odolnosť

Backend používa hodinovú in-memory cache podľa kombinácie API, serialu, PID,
verzie a OS typu. Cache uchová iba normalizované dáta, nie credentials alebo
access token. Expirovaný záznam sa neposkytuje ako aktuálny; backend vykoná
nový lookup. OAuth token sa drží iba v pamäti a obnoví sa pred expiráciou.

## Implementačné fázy

1. Implementované: OpenBao policy, secret validácia a diagnostický príkaz.
2. Implementované: Software Suggestion klient a typovaný výsledok.
3. Implementované: hardvérový a softvérový EoX klient.
4. Implementované: zapojenie do `firmware_recommendation` a `eol_status`.
5. Implementované: token cache, lookup cache a API pagination.
6. Zostáva: pilot na reálnych PID a verziách pre každú podporovanú Cisco
   platformu, najmä pokrytie CBS bez EoX OS typu a starších ASA zariadení.

## Akceptačné kritériá prvej fázy

- backend môže z OpenBao prečítať iba presnú Cisco integračnú cestu;
- chýbajúci alebo neplatný secret vráti bezpečný stabilný stav;
- úspešný OAuth test nevypíše token;
- Software Suggestion a EoX sa testujú oddelene;
- HTTP 401/403, timeout, TLS chyba a nevalidný JSON sú rozlíšené;
- CLI neakceptuje Client ID ani Client Secret ako argument alebo environment
  premennú;
- existujúce zariadenia, profylaxia a deployment fungujú aj bez Cisco secretu;
- dokumentácia uvádza manuálny OpenBao postup a presnú secret schému.

## Primárne zdroje

- Cisco Support API Authentication:
  <https://developer.cisco.com/docs/support-apis/authentication/>
- Cisco Software Suggestion API V2:
  <https://developer.cisco.com/docs/support-apis/software-suggestion/>
- Cisco EoX API:
  <https://developer.cisco.com/docs/support-apis/eox/>

Zdrojové kontrakty boli skontrolované 23. 10. 2026. Reálne entitlementy a
produktové pokrytie sa overujú diagnostikou a pilotom; dokumentácia sama ich
nepotvrdzuje.
