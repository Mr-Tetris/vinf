"""Crawler command-line interface."""

import argparse
import json
import logging
import sys
from pathlib import Path

from .storage import statistics


def parser():
    cli = argparse.ArgumentParser(description="Šetrný HTML crawler pre MangaBaka.")
    cli.add_argument("urls", nargs="*", help="Seed URL (HTTPS, presne mangabaka.org).")
    cli.add_argument("--seed", action="append", default=[], help="Seed URL; možno zopakovať.")
    cli.add_argument("--seed-file", type=Path, help="UTF-8 súbor, jedna URL na riadok, # komentáre.")
    cli.add_argument("--max-pages", type=int, default=10, help="Nové úspešne uložené stránky v tomto spustení (default 10).")
    cli.add_argument("--max-requests", type=int, default=40, help="Všetky HTTP pokusy v tomto spustení vrátane robots/retry/redirect (default 40).")
    cli.add_argument("--output-dir", type=Path, default=Path("data"))
    cli.add_argument("--connect-timeout", type=float, default=5)
    cli.add_argument("--read-timeout", type=float, default=30)
    cli.add_argument("--delay-min", type=float, default=3, help="Pauza po požiadavke, minimálne 3 s.")
    cli.add_argument("--delay-max", type=float, default=5)
    cli.add_argument("--user-agent", default="MangaIRStudentBot/0.1")
    cli.add_argument("--max-redirects", type=int, default=5)
    cli.add_argument("--resume", action="store_true")
    cli.add_argument('--content-ratings', default='',
                     help='Voliteľná content_rating cookie: safe,suggestive,erotica,pornographic (aj URL-encoded). Uveďte pri každom spustení.')
    cli.add_argument('--retry-content-filtered', action='store_true',
                     help='Pri --resume a --content-ratings vrátiť predtým filtrované manga tituly do fronty.')
    cli.add_argument('--sitemaps', action='store_true', help='Objavovať aj XML/gzip sitemap z robots.txt.')
    cli.add_argument('--details-only', action='store_true', help='Spracúvať iba manga detaily a sitemap; zoznamy ostanú pending.')
    cli.add_argument('--max-sitemap-urls', type=int, default=50000, help='Limit prijatých URL z jedného sitemap súboru.')
    cli.add_argument('--max-sitemaps', type=int, default=2, help='Najviac úspešných sitemap súborov na spustenie; zvyšok zostáva pending.')
    cli.add_argument("--stats", action="store_true", help="Len lokálne štatistiky, bez siete.")
    return cli


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    if args.stats:
        try:
            print(json.dumps(statistics(args.output_dir), ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, KeyError) as error:
            print(f"Chyba lokálnych štatistík: {error}", file=sys.stderr)
            return 1
    # requests is only imported for actual crawling, never for --stats.
    from .crawler import Config, Crawler, DEFAULT_SEEDS
    seeds = args.urls + args.seed
    if args.seed_file:
        try:
            seeds.extend(line.strip() for line in args.seed_file.read_text(encoding="utf-8").splitlines()
                         if line.strip() and not line.lstrip().startswith("#"))
        except OSError as error:
            cli.error(str(error))
    if not seeds and not args.resume:
        seeds = DEFAULT_SEEDS
    config = Config(output_dir=str(args.output_dir), max_pages=args.max_pages, max_requests=args.max_requests,
                    connect_timeout=args.connect_timeout, read_timeout=args.read_timeout,
                    delay_min=args.delay_min, delay_max=args.delay_max, user_agent=args.user_agent,
                    max_redirects=args.max_redirects, use_sitemaps=args.sitemaps,
                    details_only=args.details_only, max_sitemap_urls=args.max_sitemap_urls, max_sitemaps=args.max_sitemaps,
                    content_ratings=args.content_ratings, retry_content_filtered=args.retry_content_filtered)
    try:
        config.validate()
        args.output_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                            handlers=[logging.FileHandler(args.output_dir / "crawler.log", encoding="utf-8"),
                                      logging.StreamHandler()])
        result = Crawler(config).run(seeds, resume=args.resume)
        print(json.dumps({"run": result, "total": statistics(args.output_dir)}, ensure_ascii=False, indent=2))
        reason = result["reason"]
        if reason == "interrupted":
            return 130
        if reason.startswith(("paused", "robots_unavailable")) or reason == "temporary_failures_remaining":
            return 2
        return 0
    except (OSError, ValueError, KeyError) as error:
        print(f"Chyba: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
