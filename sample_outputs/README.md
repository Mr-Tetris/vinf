# Výstupy

Výsledky spracovania z 21. 10. 2026: 20 573 manga záznamov.

- `extracted/`: extrahované údaje.
- `index/`: invertovaný index s oboma IDF.
- `query_comparison.tsv`: výsledky piatich dopytov.
- `reports/`: štatistiky a kontrola HTML.
- `test_results.tsv`: výsledky 59 testov.

```sh
python -m manga_ir.cli search --index-dir sample_outputs/index --query "magic fantasy" --idf smooth
python -m manga_ir.cli index --input-dir sample_outputs/extracted --output-dir data/rebuilt_index
```

Plné HTML nie je súčasťou ZIP. Cesty k HTML v TSV odkazujú na pôvodný zber.
Tabuľky čítaj cez `csv` s tabulátorom; textové polia môžu obsahovať nové riadky.
