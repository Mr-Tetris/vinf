import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crawler.storage import Storage, read_tsv, statistics, write_tsv
from crawler.urls import normalize_url


class StorageTests(unittest.TestCase):
    def test_tsv_roundtrip_tabs_newlines_quotes_unicode(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "table.tsv"
            rows = [{"url": 'https://example.org/"x"', "error": 'Žluťoučký\ttext\nnew line\r\n"quoted"'}]
            write_tsv(path, ["url", "error"], rows)
            self.assertEqual(read_tsv(path), rows)

    def test_restore_queue_and_consistent_checkpoint_after_interruption(self):
        with tempfile.TemporaryDirectory() as root:
            store = Storage(root)
            pending = [{"key": "manga:12", "url": "https://mangabaka.org/manga/12/A",
                        "state": "temporary_failure", "transient_failures": "1", "not_before_utc": "", "error": "Timeout"}]
            store.checkpoint(pending, [], {"forbidden_count": "1"})
            # Failure while exporting public views after a committed generation.
            from crawler.storage import write_tsv as real_write
            def interrupted_export(path, *args):
                if Path(path) == Path(root) / "pending_urls.tsv":
                    raise OSError("simulated interruption")
                return real_write(path, *args)
            changed = [{**pending[0], "transient_failures": "2"}]
            with patch("crawler.storage.write_tsv", side_effect=interrupted_export):
                with self.assertRaises(OSError):
                    store.checkpoint(changed, [], {"forbidden_count": "2"})
            restored, processed, control = store.load()
            self.assertEqual(restored, changed)
            self.assertEqual(processed, [])
            self.assertEqual(control["forbidden_count"], "2")

    def test_atomic_html_failure_keeps_previous_file(self):
        with tempfile.TemporaryDirectory() as root:
            store = Storage(root)
            target = normalize_url("/manga/12/A")
            relative, size, digest = store.save_html(target, b"original\x00\xff")
            with patch("crawler.storage.os.replace", side_effect=OSError("simulated disk error")):
                with self.assertRaises(OSError):
                    store.save_html(target, b"new")
            self.assertEqual((Path(root) / relative).read_bytes(), b"original\x00\xff")
            self.assertEqual(size, 10)
            self.assertEqual(len(digest), 64)

    def test_statistics_verify_real_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            store = Storage(root)
            target = normalize_url("/manga/12/A")
            path, size, digest = store.save_html(target, b"test HTML")
            store.append_attempt(dict(requested_url=target.url, final_url=target.url, manga_id="12",
                                      http_status=200, html_path=path, saved_bytes=size, sha256=digest))
            self.assertEqual(statistics(root)["unique_pages"], 1)
            (Path(root) / path).write_bytes(b"corrupt")
            stats = statistics(root)
            self.assertEqual(stats["unique_pages"], 0)
            self.assertEqual(stats["invalid_html_files"], [path])


if __name__ == "__main__":
    unittest.main()
