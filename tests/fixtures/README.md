# Pôvod referenčnej vzorky

`reference.tsv` obsahuje 20 ručne kurátorovaných očakávaní. Hodnoty title,
status, roky, chapters a volumes boli odčítané z primárneho H1 a atribútov
`aria-label="Published ..."`, `Series has ...` v uloženom HTML. Autori a
ilustrátori boli porovnaní s viditeľnými staff odkazmi a explicitnými rolami
v JSON-LD ComicSeries s rovnakým ID. Japonské názvy a krátke kontrolné slová
opisu boli overené v primárnej hlavičke/opise. Očakávané hodnoty neboli
vytvorené volaním testovaného extraktora.

Fixtures `pages/<id>.html.gz` sú gzip kópie CELÝCH pôvodných HTML bajtov,
bez redukcie, opravy markup alebo doplnenia obsahu. Súčet v reference.tsv sa
vzťahuje na dekomprimované pôvodné HTML, nie gzip transport. URL a hash boli
prevzaté a overené proti crawler auditu. Staršie stránky boli získané
12. 10. 2026; rozšírenie je z 19. 10. 2026. Konkrétny čas každého snapshotu
je v stĺpci fetched_at_utc.

Prázdne skalárne end_year/chapters/volumes sú OVERENÉ chýbajúce hodnoty.
description_missing=1 znamená viditeľnú hlášku „No description available for
this series.“, ktorá sa musí zmeniť na prázdny opis, nie indexovať ako opis.
Prázdny ja_title znamená, že toto pole v danom riadku nebolo vybraté na
porovnanie; nevyjadruje neprítomnosť všetkých japonských variantov.
Meno v authors/illustrators je presné meno v zdroji, nie vlastná transliterácia.
Žiadne aktuálne informácie z webu nenahrádzajú hodnoty tohto snapshotu.

Vzorka zahŕňa rozdelené role tvorcov (676, 725, 1824, 6029), japončinu,
pokračujúce/dokončené/hiatus/unknown stavy, apostrof a ×, rôzne počty aj
chýbajúce kapitoly/zväzky/opis (521697, 328133, 591006).

Obnova z rovnakého lokálneho datasetu bez siete:

```sh
python -m tools.build_fixtures --crawl-dir data
python -m unittest discover -v
```

ZIP obsahuje tieto fixtures aj reference.tsv. Testy po rozbalení nepotrebujú
data/, internet, robots.txt ani tiktoken. Plné verejné HTML môže obsahovať
externé URL a vložený JavaScript; testy ich iba čítajú ako bajty, nevykonávajú.
