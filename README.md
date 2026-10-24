# MangaBaka – konzultácia 2b / prvé odovzdanie

Python crawler, samostatný REGEX extraktor, vlastný invertovaný index a CLI
vyhľadávanie s dvoma IDF. Bez SQL/SQLite, pandas, NLTK, Scrapy a hotového
vyhľadávacieho frameworku. Spark, Wikipedia join, PyLucene a webové UI nie sú
súčasťou tejto verzie.

## Overený stav 21. 10. 2026

| Ukazovateľ | Hodnota |
| --- | --- |
| HTTP pokusy celého data/ | 20 873 |
| Použiteľné unikátne HTML | 20 574: 20 573 detailov + 1 zoznam |
| HTML na disku vrátane historických verzií | 4 800 923 616 B = 4.800924 GB = 4.471209 GiB |
| Bajty obsahovo použiteľných HTML | 4 800 300 046 |
| Extrahované / indexované manga | 20 573 / 20 573 |
| Unikátne termy / postingy | 75 811 / 380 936 |
| Tokeny vlastného indexu | 451 401 |
| tiktoken cl100k_base | 1 185 990 |
| Referenčné reálne detaily vo fixtures | 20 |
| Offline testy | 59, všetky úspešné |

Poznáme 205 238 unikátnych manga ID: 20 573 získaných, 6 nedostupných a 184 659 čakajúcich. Celkom zostáva 184 668 URL vrátane 8 zoznamov. Získaný je sitemap index a všetkých 9 jeho URL setov, bez skrátenia limitom. **Pokrytie je stále čiastočné.**

Hranica **500 MB aj 500 MiB HTML je splnená**. Viac než milión manga záznamov zatiaľ získaných nie je. Bajty HTML zahŕňajú zachované historické verzie; venv, index, TSV a checkpointy sa do tohto čísla nezapočítavajú.

Vo fyzickej fronte je navyše 1 už úspešne uložený detail, ktorý sa pri prerušení nestihol potvrdiť checkpointom. Manifest ho počíta medzi získané, nie medzi zostávajúce; --resume ho obnoví lokálne bez opätovného HTTP.

Podrobné výsledky: `konzultacia_2b_podklady.md`. `sample_outputs/` obsahuje aktuálne extrakčné TSV, celý odvodený index, výsledky dopytov a reporty. Pôvodná 25-dokumentová verzia je zachovaná lokálne v `data/history/2026-10-19/`.

## Inštalácia

Python 3.10+, macOS/Linux (crawler používa POSIX fcntl). Aktuálne overené:
Python 3.14.6 a requests 2.34.2. Požiadavka je `requests>=2.32.5,<3`, nie lockfile.
Voliteľný tiktoken má vlastný requirements súbor.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -v
# Voliteľný bonus:
python -m pip install -r requirements-bonus.txt
python -m manga_ir.cli prepare-tokenizer --encoding cl100k_base
```

`prepare-tokenizer` explicitne povoľuje prvotné stiahnutie encoding do cache.
Extrakcia, index, vyhľadávanie a stats sú offline. Stats encoding nikdy
automaticky nesťahuje: pri chýbajúcom balíku/cache vykáže bonus unavailable
a hlavná pipeline pokračuje bez odhadu tokenov.

## Crawler: malá skúška a pokračovanie

Nový výstupný adresár:

```sh
python -m crawler.cli --max-pages 5 --max-requests 12 --output-dir data/small \
  https://mangabaka.org/popular \
  https://mangabaka.org/manga/676/Ichi-the-Witch
python -m crawler.cli --resume --details-only --sitemaps --max-pages 20 \
  --max-requests 32 --max-sitemaps 2 --output-dir data/small
python -m crawler.cli --stats --output-dir data/small
```

Predvolené seedy: /popular, kandidátny /search, detaily 677, 676, 849.
Podporované sú pozičné URL, opakované --seed a --seed-file (UTF-8, jedna URL
na riadok, prázdne riadky a # komentáre ignorované). Pri resume bez explicitných
seedov sa nové default seedy nepridávajú. Existujúci zber vyžaduje --resume.

**Limity platia na jedno spustenie aj pri resume:** --max-pages počíta nové
použiteľné HTML, nie robots/sitemap/chyby/redirect kroky ani lokálnu obnovu.
--max-requests počíta každý skutočný request. --details-only ponechá list URL
vo fronte, ale nesťahuje ich. --max-sitemaps (default 2) obmedzí úspešne
spracované sitemap súbory; ďalšie zostávajú pending. --max-sitemap-urls
(default 50 000) je limit prijatých unikátnych relevantných URL z jedného XML;
truncation sa eviduje. XML má limit 50 MiB po rozbalení. DTD/entities sa odmietajú.

### Obsahové preferencie MangaBaka

Voliteľný parameter `--content-ratings pornographic,safe,suggestive,erotica`
nastaví cookie `content_rating` na hodnotu
`pornographic%2Csafe%2Csuggestive%2Cerotica`. Prijíma aj už zakódovanú hodnotu;
čiarky sa zakódujú iba raz. Povolené kategórie sú `safe`, `suggestive`, `erotica`
a `pornographic`. Cookie sa posiela cez HTTPS pre doménu mangabaka.org a cestu `/`.
Bez parametra crawler vlastnú preferenčnú cookie nenastavuje. Parameter uvádzajte
pri každom spustení; aktuálna hodnota sa eviduje v `control.tsv` ako `content_rating`.

Na opätovný zber predtým odmietnutých titulov pridajte
`--retry-content-filtered` (vyžaduje `--resume` aj `--content-ratings`).
Iba záznamy manga s chybou `content_filtered` a stavom `permanent_failure` sa
vrátia na začiatok fronty. Už získané dokumenty ani ostatné chyby sa neresetujú.
Vrátené položky zostávajú vo fronte aj po prerušení; opakovanie prepínača nie je
potrebné na ich dokončenie. Ak server aj s cookie vráti Filtered Content, odpoveď
sa znovu odmietne bez automatického nekonečného opakovania.

Bežiaci proces nové parametre neprevezme. Zastavte ho cez Ctrl+C, počkajte na
ukončenie a v termináli projektu na macOS spustite:

```sh
caffeinate -i .venv/bin/python -m crawler.cli --resume --sitemaps --details-only --max-sitemaps 1000 --max-sitemap-urls 50000 --max-pages 1000000 --max-requests 2000000 --content-ratings pornographic,safe,suggestive,erotica --retry-content-filtered --output-dir data
```

Pôvodné HTML a HTTP audit zostávajú zachované. Ak nový obsah má rovnaké manga ID
a iné bajty, uloží sa do ďalšieho HTML súboru s príponou SHA-256 v názve.
Extrakcia a obnovenie používajú posledný uložený úspešný pokus pre dané ID.
Historické počty chýb v štatistike sa úspešným opakovaním nezmažú.

### Dlhší zber – samostatne v termináli

Rozsiahly zber už prebehol; posledný uložený stav je `interrupted` po Ctrl+C.
Ďalšie pokračovanie použije zachovanú frontu. Príkaz s obsahovými preferenciami
je uvedený vyššie. Menšia dávka môže vyzerať takto:

```sh
python -m crawler.cli --resume --details-only --sitemaps --max-sitemaps 1 \
  --max-pages 100 --max-requests 140 --content-ratings pornographic,safe,suggestive,erotica --output-dir data
```

Po každom rozšírení zberu treba obnoviť extrakciu, index, porovnanie dopytov
a štatistiky podľa nasledujúcej offline pipeline. Zmení sa N, DF aj obe IDF;
samotný nový crawl tieto výstupy neprepočíta. Čas celého zberu zahŕňa pauzy,
prerušenia a retry, preto z neho neodvodzujeme garantované tempo.

### Zachované obmedzenia a stav

- Jedna requests.Session, jeden sekvenčný downloader. Zámok chráni rovnaký
  output dir; nie je globálny pre rôzne nezávislé procesy/output dir.
- UA `MangaIRStudentBot/0.1`, Accept `text/html,application/xhtml+xml;q=0.9`,
  Accept-Language en. Vlastný UA s reálnym kontaktom/URL cez --user-agent.
- Connect timeout 5 s, read timeout 30 s (medzi čítaniami, nie celkový download).
  Náhodná pauza 3–5 s po odpovedi, minimum konfigurovateľné iba nahor.
  Prísnejšie Crawl-delay/Request-rate majú prednosť; hodiny sú monotónne.
- Timeout/spojenie/5xx: najviac 2 dodatočné pokusy, backoff ≥10 a ≥30 s.
  Dávka prerušená limitom sa obnoví; vyčerpaná dočasná chyba dostane pri
  explicitnom resume novú dávku. Nevyvoláva nekonečný loop v jednom spustení.
- 429: Retry-After sekundy aj dátum; fallback 60 s. Druhý výskyt na doméne
  pozastaví. 403 bez retry; druhá 403 pozastaví. 404 bez retry. Počítadlá/cooldown
  prežijú resume. Obsahové preferencie sa nastavujú iba explicitným
  --content-ratings; nenastavujú sa automaticky ako reakcia na chybu.
- Robots pred každým zberom; 404/410 sa odlišuje od nedostupnosti. Sieťová chyba,
  odmietnutie, HTML/challenge alebo neplatná odpoveď nedávajú povolenie zberu.
  Uložený robots zakazuje /search, povoľuje relevantné ostatné cesty.
- Ručné redirecty, default max 5; HTTPS, presný mangabaka.org, scope a robots
  sa kontrolujú pred každým krokom. URL query sa neprehľadávajú kombinatoricky.
- Detaily `/manga/<id>[/slug]`, list `/popular[?page=1..500]`, kandidátny /search.
  Detaily sú deduplikované podľa ID, listy podľa normalizovanej URL. Obrázky,
  kapitoly, externé hosty a ďalšie karty detailu sa nesťahujú.
- HTTP 200 sa overuje obsahovo: title/H1, canonical ID, challenge a Filtered Content.
  Po objavení brány sa 4 historické false positives offline preklasifikovali.
  Pôvodný HTTP audit ani HTML sa neprepisovali. Neúspešný request nenahradí
  predtým úspešný súbor. SHA-256 znamená integritu, nie deduplikáciu obsahu.
- Pending/processed/control sa commitujú spoločným generačným pointerom cez
  dočasné súbory + fsync + atomic replace. Ctrl+C zachová aktívnu URL. Resume
  lokálne overuje hash/veľkosť a obnovuje odkazy. Celý výstupný adresár je stav.

Návratové kódy crawlera: 0 fronta/limit, 1 lokálna chyba, 2 pozastavenie/dočasné
chyby, 130 Ctrl+C. Append-only audit nie je transakčný pri násilnom SIGKILL.

## Offline pipeline

```sh
python -m manga_ir.cli audit --crawl-dir data --repair-state
python -m manga_ir.cli extract --input-dir data --output-dir data/extracted
python -m manga_ir.cli extract --file data/html/manga_676.html --output-dir data/one
python -m manga_ir.cli index --input-dir data/extracted --output-dir data/index
python -m manga_ir.cli search --index-dir data/index --query 'magic fantasy' --idf classic --top-k 10
python -m manga_ir.cli search --index-dir data/index --query 'magic fantasy' --idf smooth --top-k 10
python -m manga_ir.cli compare --index-dir data/index --queries-file examples/queries.txt --output data/query_comparison.tsv
python -m manga_ir.cli stats --crawl-dir data --extracted-dir data/extracted --index-dir data/index --output-dir data/reports --tiktoken cl100k_base
```

Bez bonusu vynechajte --tiktoken. Crawler --stats je samostatná lokálna
štatistika bez načítania indexu. Pipeline stats odmietne index zastaraný voči
vstupným extrakčným TSV. Index sa vytvára raz, nie pri každom dopyte. Search
vypisuje TSV poradie, manga_id, title, score, source_url; pri neznámych termoch
vráti prázdnu tabuľku s vysvetlením, pri prázdnom dopyte/poškodenom indexe chybu.

### Extrakcia a zdroj polí

Vlastné re extraktory čítajú hlavný H1, primárny header/meta, Description
section, staff odkazy a tag atribúty. html.unescape a odstránenie značiek
zachovajú Unicode a hranice slov. Údaje odporúčaných sérií sa nepovažujú za údaje
hlavnej série. Neznáme počty sú prázdne, nie nula. Hláška o chýbajúcom opise sa
neindexuje ako opis. Z UI tagov sa odstráni weight/spoiler prípona.

**JSON-LD je pomocný zdroj:** vyberá sa ComicSeries s presne rovnakým MangaBaka ID.
Dopĺňa alternatívne názvy neviditeľné v hlavičke a explicitné role author/
illustrator k menám vyextrahovaným REGEX zo staff odkazov. Bez doloženej roly sa
uloží unknown. Core skalárne polia, opis a žánre/značky nie sú JSON export.
Každá viacnásobná hodnota má origin. Pôvodné HTML sa nemení.

### Text, tokenizácia a ranking

Pevné poradie: title → alternatívy → tvorcovia → opis → žánre → témy.
Rovnaké hodnoty (NFC/casefold kľúč) sa vložia raz, oddelené newline. Rovnaké meno
autora/ilustrátora sa neopakuje a titul nemá skrytú vyššiu váhu.

Dokumenty aj dopyty používajú `html.unescape → NFC → casefold → re [^\W_]+`.
Bez stemmingu a stopslov. Japončina bez medzier zostáva často jediným dlhým
termom: nejde o morfologickú segmentáciu ani preklad. Prázdny tokenový dokument
sa nezaradí do N a je v skipped_documents.tsv.

```text
TF(t,d) = počet výskytov termu v dokumente
DF(t)   = počet rôznych dokumentov s termom
idf_classic = ln(N / DF)
idf_smooth  = ln((N + 1) / (DF + 1)) + 1
score(d,q)  = sum((1 + ln(TF(t,d))) * idf(t) pre unikátne známe termy q v d)
```

Kandidáti sú OR zjednotenie postingov, aj so skóre 0. Neznáme termy sa preskočia.
Pri zhode skóre rozhoduje číselné manga ID. Index číta zoradené TSV streamom a
merge-joinom; jeden dokumentový text naraz, žiadne hromadné čítanie HTML.
Slovník termov a postingy zostávajú v RAM pri build aj search. Pre aktuálnu
vzorku je to primerané; čiastkové diskové indexy/merge pre veľké kolekcie zatiaľ
nie sú implementované. Hashy a verzia formátu chránia pred poškodeným indexom.

## Formáty TSV a súbory

Všetky uložené tabuľky sú UTF-8 cez csv, delimiter tabulátor, štandardné quoting.
Čítajte ich cez csv, nie split: opis/text môžu obsahovať newline/tab.

| Výstup | Polia / účel |
| --- | --- |
| crawl_metadata.tsv | requested_url, final_url, manga_id, fetched_at_utc, http_status, content_type, saved_bytes, html_path, sha256, attempt, error; každý HTTP pokus |
| pending_urls.tsv | key, url, state, transient_failures, not_before_utc, error |
| processed_urls.tsv | key, url, final_url, state, manga_id, html_path, saved_bytes, sha256, error; vrátane sitemap_success |
| control.tsv / checkpoint.tsv | name,value pre stav / generation pre atomický pointer |
| discovery.tsv | key, url, method, source_url, discovered_at_utc; nové objavy, starší dataset môže mať pôvod len v HTML |
| sitemap_metadata.tsv | url, fetched_at_utc, path, saved_bytes, sha256, kind, entries, accepted_urls, truncated |
| extracted/manga.tsv | manga_id, title, description, work_type, status, start_year, end_year, chapters, volumes, source_url, html_path, html_sha256 |
| extracted/titles.tsv | manga_id, title, language, origin |
| extracted/creators.tsv | manga_id, name, role, origin |
| extracted/tags.tsv | manga_id, name, kind, tag_id, origin |
| extraction_errors.tsv / field_coverage.tsv | html_path,source_url,error / field,filled,total |
| index/documents.tsv | document_id, manga_id, title, source_url, token_count, text_sha256, text |
| index/postings.tsv | term, document_id, tf |
| index/terms.tsv | term, df, idf_classic, idf_smooth |
| index/index_metadata.tsv | name,value; formát, N, počty, normalizácia, vzorce, input a file SHA-256 |
| index/skipped_documents.tsv | manga_id, reason |
| query_comparison.tsv | query, method, rank, manga_id, title, score, source_url |
| reports/statistics.tsv / dataset_manifest.tsv | name,value; štatistika a čiastočný rozsah |
| reports/html_validation.tsv | source_url,manga_id,html_path,sha256,usable,error |
| reports/tiktoken_counts.tsv | document_id,manga_id,encoding,token_count,text_sha256 |
| reports/tiktoken_metadata.tsv | name,value; dostupnosť, verzia, encoding, dokumenty, total_tokens |

SHA v názve list HTML je hash URL; v metadátach hash uložených bajtov. Robots a
sitemap majú v HTTP audite HTML polia prázdne/0, osobitné originály sú uložené.
fetched_at_utc je začiatok requestu v UTC, attempt je kumulatívny pre URL.

tiktoken počíta originálny deterministický text z documents.tsv **pred** indexovou
normalizáciou, každý dokument samostatne, potom sčíta výsledky. Používa
`encoding.encode(text, disallowed_special=())`, takže token-like reťazce sú text.
Jeho tokeny sa nesmú zamieňať s vlastnými 451 401 vyhľadávacími tokenmi.

## Testy, odovzdanie a reprodukcia po rozbalení ZIP

```sh
python -m unittest discover -v
python -m tools.package_submission --output dist/vinf_2b.zip
```

ZIP má kód, README, report, requirements, queries, 20 full-byte gzip fixtures,
ručne kurátorované očakávania s URL/hash a malé skutočné sample_outputs TSV.
Nemá venv, celý crawl, pôvodné sitemap súbory, tajomstvá ani checkpoint kópie.

Po rozbalení a inštalácii základných requirements:

```sh
python -m unittest discover -v
python -m tools.demo --output-dir data/demo
python -m manga_ir.cli search --index-dir data/demo/index --query 'magic fantasy' --idf smooth
python -m manga_ir.cli search --index-dir sample_outputs/index --query 'magic fantasy' --idf classic
```

Demo zrekonštruuje **20 pôvodných snapshotov a presnú podmnožinu historického
auditu**, nevyrába nové sieťové výsledky. Má vlastný 20-dokumentový index a iné
počty/IDF než plný 20 573-dokumentový index v sample_outputs. Celá jeho pipeline je
offline. tools.build_fixtures len overí a zabalí známe originály podľa
reference.tsv, nevypočítava referenčné očakávania testovaným extraktorom.

## Hranice výsledku

Na 20 rôznych reálnych detailoch sa kontrolujú presné tituly, role tvorcov,
statusy, roky, kapitoly/zväzky, overené prázdne hodnoty, vybraná japončina,
žánre a kontrolné slová opisov. Tento ručný referenčný súbor zostal oddelený
od veľkého korpusu; počet získaných HTML nie je počet ručne anotovaných stránok.

Porovnanie piatich viacslovných dopytov sa zopakovalo na novom indexe.
Poradie top-10 sa medzi classic a smooth líši pri 0 z 5 dopytov.
Ide o ukážku rankingu bez úplných relevance judgments, nie meranie precision/recall.

Zostávajúce URL bránia tvrdeniu o úplnom pokrytí webu. Index bol úspešne
zostavený na aktuálnom korpuse, ale postingy zostávajú v RAM; diskové čiastkové
indexy, external merge a wiki publikácia nie sú súčasťou tejto verzie.
