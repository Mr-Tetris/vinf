"""UTF-8 TSV storage and atomic, generation-consistent checkpoints."""

import csv
import hashlib
import os
import shutil
import tempfile
import uuid
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from .validation import blocked_document

METADATA_FIELDS = ["requested_url", "final_url", "manga_id", "fetched_at_utc",
                   "http_status", "content_type", "saved_bytes", "html_path",
                   "sha256", "attempt", "error"]
PENDING_FIELDS = ["key", "url", "state", "transient_failures", "not_before_utc", "error"]
PROCESSED_FIELDS = ["key", "url", "final_url", "state", "manga_id", "html_path",
                    "saved_bytes", "sha256", "error"]
CONTROL_FIELDS = ["name", "value"]


def read_tsv(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file, delimiter="\t"))


def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sync_directory(path):
    if os.name == 'posix':
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def write_tsv(path, fields, rows):
    # Stream large postings/URL tables instead of retaining a second full TSV in RAM.
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as file:
            writer = csv.DictWriter(file, fieldnames=fields, delimiter='\t')
            writer.writeheader()
            writer.writerows(rows)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def iter_tsv(path, fields=None):
    with Path(path).open(encoding='utf-8', newline='') as file:
        reader = csv.DictReader(file, delimiter='\t')
        if fields is not None and reader.fieldnames != fields:
            raise ValueError(f'Neplatná TSV schéma: {path}')
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f'Neúplný TSV riadok: {path}')
            yield row


class Storage:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "html").mkdir(exist_ok=True)

    @contextmanager
    def lock(self):
        """The workspace runs on POSIX; lock prevents two writers in one output dir."""
        import fcntl
        with (self.root / ".crawler.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError("Výstupný adresár už používa iný crawler.") from error
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def append_attempt(self, row):
        path = self.root / "crawl_metadata.tsv"
        new = not path.exists() or path.stat().st_size == 0
        with path.open("a", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=METADATA_FIELDS, delimiter="\t")
            if new:
                writer.writeheader()
            writer.writerow({name: row.get(name, "") for name in METADATA_FIELDS})
            file.flush()
            os.fsync(file.fileno())

    def save_html(self, target, data):
        relative = "html/" + target.filename
        path = self.root / relative
        # A retried preference gate may already have a historical audited snapshot.
        # Keep that immutable so its original audit row still verifies correctly.
        if path.exists() and path.read_bytes() != data:
            digest = hashlib.sha256(data).hexdigest()
            relative = str(Path(relative).with_name(path.stem + '_' + digest + '.html'))
        atomic_bytes(self.root / relative, data)
        # Hash and size of the actual persisted bytes, not response.text.
        saved = (self.root / relative).read_bytes()
        return relative, len(saved), hashlib.sha256(saved).hexdigest()

    def checkpoint(self, pending, processed, control):
        generations = self.root / ".checkpoints"
        generations.mkdir(exist_ok=True)
        previous = read_tsv(self.root / "checkpoint.tsv")
        generation = uuid.uuid4().hex
        destination = generations / generation
        destination.mkdir()
        write_tsv(destination / "pending_urls.tsv", PENDING_FIELDS, pending)
        write_tsv(destination / "processed_urls.tsv", PROCESSED_FIELDS, processed)
        write_tsv(destination / "control.tsv", CONTROL_FIELDS,
                  [{"name": k, "value": str(v)} for k, v in control.items()])
        # Commit both tables together by atomically replacing their pointer.
        write_tsv(self.root / "checkpoint.tsv", ["generation"], [{"generation": generation}])
        # Human-readable exports; resume reads the committed pair above.
        write_tsv(self.root / "pending_urls.tsv", PENDING_FIELDS, pending)
        write_tsv(self.root / "processed_urls.tsv", PROCESSED_FIELDS, processed)
        write_tsv(self.root / "control.tsv", CONTROL_FIELDS,
                  [{"name": k, "value": str(v)} for k, v in control.items()])
        keep = {generation} | {row["generation"] for row in previous}
        for old in generations.iterdir():
            if old.is_dir() and old.name not in keep:
                shutil.rmtree(old)

    def load(self):
        pointer = read_tsv(self.root / "checkpoint.tsv")
        if pointer:
            generation = pointer[0]["generation"]
            if not re_generation(generation):
                raise ValueError("Neplatný checkpoint pointer.")
            source = self.root / ".checkpoints" / generation
            if not all((source / name).is_file() for name in ("pending_urls.tsv", "processed_urls.tsv", "control.tsv")):
                raise ValueError("Checkpoint je neúplný; zber sa bezpečne zastavil.")
        else:
            source = self.root
        pending = read_tsv(source / "pending_urls.tsv")
        processed = read_tsv(source / "processed_urls.tsv")
        control = {row["name"]: row["value"] for row in read_tsv(source / "control.tsv")}
        done = {row["key"] for row in processed}
        return [row for row in pending if row["key"] not in done], processed, control


def re_generation(value):
    return len(value) == 32 and all(char in "0123456789abcdef" for char in value)


def statistics(root):
    """Local-only; verify files against stored bytes and hashes before counting."""
    root = Path(root)
    if not root.exists():
        pending, processed = [], []
    else:
        pending, processed, _ = Storage(root).load()
    attempts = read_tsv(root / "crawl_metadata.tsv")
    errors = Counter()
    for row in attempts:
        status = row["http_status"]
        if status and int(status) >= 400:
            errors["HTTP " + status] += 1
        elif row["error"]:
            errors[row["error"].split(":", 1)[0]] += 1
    valid = {}
    invalid = []
    # Use durable successful metadata as well: a crash after saving HTML but before
    # checkpoint must not erase genuinely collected pages from local statistics.
    for row in attempts:
        if row["html_path"] and not row["error"]:
            key = "manga:" + row["manga_id"] if row["manga_id"] else "list:" + row["final_url"]
            valid[key] = row
    for row in processed:
        if row["state"] == "success":
            valid.setdefault(row["key"], row)
    verified = {}
    rejected = []
    for key, row in valid.items():
        path = root / row["html_path"]
        if not path.is_file():
            invalid.append(row["html_path"])
            continue
        content = path.read_bytes()
        if len(content) != int(row["saved_bytes"]) or hashlib.sha256(content).hexdigest() != row["sha256"]:
            invalid.append(row["html_path"])
        elif reason := blocked_document(content):
            rejected.append({'html_path': row['html_path'], 'error': reason})
        else:
            verified[key] = row
    total_bytes = sum(p.stat().st_size for p in (root / "html").glob("*.html") if p.is_file())
    skipped = Counter(row["error"] for row in processed if row["error"] in {"robots_disallowed", "outside_scope"})
    return {"network_attempts": len(attempts), "unique_pages": len(verified),
            "manga_pages": sum(bool(row["manga_id"]) for row in verified.values()),
            "list_pages": sum(not row["manga_id"] for row in verified.values()),
            "errors": dict(sorted(errors.items())), "skipped_urls": dict(sorted(skipped.items())),
            "remaining_urls": len(pending),
            "html_bytes_on_disk": total_bytes, "invalid_html_files": invalid,
            'rejected_documents': rejected}
