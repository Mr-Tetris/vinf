# Aktuálne odvodené výsledky – 21. 10. 2026

TSV sú nezmenené kópie aktuálnej offline pipeline nad hlavným datasetom data/.
Extrakcia obsahuje 20 573 manga, index N=20 573;
query_comparison.tsv obsahuje 100 skutočných výsledkov piatich dopytov
pri classic aj smooth IDF. Reporty popisujú celý crawl, nie fixture demo.
test_results.tsv zaznamenáva 59 úspešných unittest metód z overenia 21. 10. 2026.

Plné HTML (4 800 923 616 B), XML, HTTP audit a checkpointy sú iba lokálne
v data/ a nie sú priložené v ZIP. Cesty v extrakcii a html_validation sú pôvodné
cesty. Prenositeľná manuálne anotovaná podmnožina 20 pôvodných HTML je vo fixtures.
Demo má vlastný N=20 a odlišné IDF. Historické výstupy pre N=25 sú zachované
lokálne v data/history/2026-10-19/.

Bez HTML možno znovu zostaviť celý index z priložených TSV:

```sh
python -m manga_ir.cli index --input-dir sample_outputs/extracted --output-dir data/rebuilt_index
python -m manga_ir.cli search --index-dir data/rebuilt_index --query 'magic fantasy' --idf smooth
```

Hotový index možno použiť cez --index-dir sample_outputs/index.
TSV opis/text môže obsahovať nové riadky; použite csv.DictReader s delimiter tab.
Počet fyzických riadkov nie je počet záznamov. Štatistiky chýb HTTP sú kumulatívne
aj po úspešnom retry; nemožno ich zameniť s počtom nedostupných manga ID.
