# Testovacie dáta

`pages/<id>.html.gz` obsahuje 20 HTML stránok zo zberov 12. a 19. 10. 2026.
URL, čas získania a SHA-256 pôvodného HTML sú v `source_metadata.tsv`.
Hash sa kontroluje po rozbalení gzip.

`reference.tsv` obsahuje očakávané hodnoty pre test extraktora. Názvy, stav,
roky a počty pochádzajú z hlavného H1 a aria-label atribútov. Tvorcovia a ich
role pochádzajú zo staff odkazov a JSON-LD ComicSeries s rovnakým ID.
Očakávané hodnoty boli skontrolované v HTML, nezískavajú sa testovaným extraktorom.

Prázdne `end_year`, `chapters` a `volumes` označujú chýbajúce údaje.
`description_missing=1` označuje hlášku „No description available for this series.“,
ktorú extraktor nahrádza prázdnym opisom. Prázdne `ja_title` znamená, že sa
japonský názov v danom teste nekontroluje.

Testy používajú uložené stránky a nepotrebujú sieť:

```sh
python -m unittest discover -v
```

Obnova gzip súborov z rovnakého pôvodného zberu:

```sh
python -m tools.build_fixtures --crawl-dir data
```
