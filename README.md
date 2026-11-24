# Kolejník – aktualizace veřejných jízdních řádů

Samostatné zpracování veřejných dopravních dat pro Kolejník. Aplikace a její
historie zůstávají v odděleném soukromém projektu. Tento projekt obsahuje pouze
importéry, ověřování dat, potřebné referenční podklady a testy.

## Provoz bez placených minut

- Veřejný projekt používá standardní `ubuntu-latest` GitHub Actions.
- PID se ověřuje každé čtyři hodiny; společná celostátní data jednou denně.
- Oba postupy lze spustit ručně. Běhy stejného postupu se nepřekrývají.
- Nepoužívají se placené velké servery ani ukládání Actions artefaktů.
- Mezipaměť zdrojů je pouze zrychlení; při jejím vymazání se data stáhnou znovu.
- Rozpočet účtu zůstává nulový; tento projekt nemění fakturaci ani Render.

## Publikace a ochrana dat

Nejprve se stáhnou a ověří zdroje. Úplné soubory se validují před změnou
ukazatele `manifest.json`. Publikace upravuje výhradně `spojeni/data-v2` nebo
`spojeni/data-national`. PID používá společnou adresu webu i APK z hlavní větve.
Celostátní data atomicky aktualizují hlavní i kompatibilní mobilní větev.
Souběžná změna se znovu načte; historie se nikdy nepřepisuje silou.
Při neúplných datech zůstává poslední platná publikace zachována.

Přístup k cílovému projektu používá samostatný deploy klíč v tajné proměnné
`KOLEJNIK_PUBLISH_KEY`. Klíč není součástí souborů, výstupů ani cache. Pracovní
postupy s klíčem běží pouze z hlavní větve, nikoli z cizích pull requestů.
Po zveřejnění se ověřují skutečné webové soubory i mobilní datový kanál.

GitHub může plánovaný běh opozdit; nejedná se o službu s garancí přesného času.
Stáří dat kontrolují validátory i klienti. Selhání se neskrývá přepsáním času
ověření bez skutečné kontroly zdroje.

Po úspěšném doručení se do `status/` ukládá malý veřejný doklad o revizi a času
ověření. Je to skutečná historie vydání a zároveň pravidelná aktivita projektu;
plánované úlohy tak nezůstávají v repozitáři bez aktivity po dobu 60 dnů.

## Zdroje a původ

- PID: https://data.pid.cz/PID_GTFS.zip
- CIS JŘ / Ministerstvo dopravy: https://portal.cisjr.cz/
- Správa železnic, SR70: původ a kontrolní součty v `reports/station-register-20261027/manifest.json`.
- KODIS: https://www.kodis.cz/prestupni-uzly
- OpenStreetMap contributors, ODbL 1.0: https://www.openstreetmap.org/copyright

Importéry a testy byly odděleny z Kolejníku, revize
`8487e49a4d2039d8fe96b01af78a7653689814b2`. Referenční podklady mají zachované
zdrojové odkazy, kontrolní součty a omezení své platnosti. Zveřejnění tohoto
projektu nemění podmínky použití původních dat.

## Lokální kontrola

Python 3.13, `PYTHONPATH=scripts:tests` (na Windows oddělovač `;`):

```sh
python -m unittest discover -s tests -p 'test_*.py'
```

Pěší propojení PID navíc používá SciPy. Celostátní zpracování používá standardní
knihovnu Pythonu. Pro skutečnou publikaci je nutný výslovně nastavený cílový
repozitář a přístup k němu.
