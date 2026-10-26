# MangaBaka

## Inštalácia

Python 3.10+, macOS alebo Linux.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Crawler

Nový zber do prázdneho adresára:

```sh
python -m crawler.cli --sitemaps --details-only --max-sitemaps 10 --max-pages 100 --max-requests 200 --content-ratings pornographic,safe,suggestive,erotica --output-dir data/new
```

Pokračovanie existujúceho zberu:

```sh
python -m crawler.cli --resume --sitemaps --details-only --max-sitemaps 1000 --max-pages 1000000 --max-requests 2000000 --content-ratings pornographic,safe,suggestive,erotica --output-dir data
```

Limity platia na jedno spustenie. Prerušenie: Ctrl+C. Na opakovanie predtým
filtrovaných titulov pridaj `--retry-content-filtered`.

```sh
python -m crawler.cli --stats --output-dir data
```

## Extrakcia a index

```sh
python -m manga_ir.cli extract --input-dir data --output-dir data/extracted
python -m manga_ir.cli index --input-dir data/extracted --output-dir data/index
```

## Vyhľadávanie

```sh
python -m manga_ir.cli search --index-dir data/index --query "magic fantasy" --idf classic --top-k 10
python -m manga_ir.cli search --index-dir data/index --query "magic fantasy" --idf smooth --top-k 10
python -m manga_ir.cli compare --index-dir data/index --queries-file examples/queries.txt --output data/query_comparison.tsv
```

## Štatistiky a kontrola HTML

```sh
python -m manga_ir.cli stats --crawl-dir data --extracted-dir data/extracted --index-dir data/index --output-dir data/reports
python -m manga_ir.cli audit --crawl-dir data --output data/reports/html_validation.tsv
```

Voliteľné počítanie tokenov:

```sh
python -m pip install -r requirements-bonus.txt
python -m manga_ir.cli prepare-tokenizer --encoding cl100k_base
python -m manga_ir.cli stats --crawl-dir data --extracted-dir data/extracted --index-dir data/index --output-dir data/reports --tiktoken cl100k_base
```

## Demo bez sťahovania

```sh
python -m tools.demo --output-dir data/demo
python -m manga_ir.cli search --index-dir sample_outputs/index --query "school romance" --idf classic
```

## Testy a ZIP

```sh
python -m unittest discover -v
```
