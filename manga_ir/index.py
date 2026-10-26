"""Inverted index with classic and smoothed IDF."""

import hashlib
import math
import os
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from crawler.storage import iter_tsv, read_tsv, write_tsv
from .text import compose_text, iter_records, tokenize

DOC_FIELDS = ['document_id', 'manga_id', 'title', 'source_url', 'token_count', 'text_sha256', 'text']
POST_FIELDS = ['term', 'document_id', 'tf']
TERM_FIELDS = ['term', 'df', 'idf_classic', 'idf_smooth']
FORMAT = 'manga-ir-tsv-1'


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def idfs(n, df):
    if not 1 <= df <= n:
        raise ValueError('IDF vyžaduje 1 <= DF <= N.')
    return math.log(n / df), math.log((n + 1) / (df + 1)) + 1


def build_index(input_dir, output_dir):
    source, out = Path(input_dir), Path(output_dir)
    fingerprints = {name: file_hash(source / name) for name in ['manga.tsv', 'titles.tsv', 'creators.tsv', 'tags.tsv']}
    fingerprint = hashlib.sha256(''.join(name + value for name, value in fingerprints.items()).encode()).hexdigest()
    out.mkdir(parents=True, exist_ok=True)
    postings = defaultdict(dict)
    n, skipped = 0, []
    with tempfile.TemporaryDirectory(prefix='.build-', dir=out) as temporary:
        temporary = Path(temporary)
        def documents():
            nonlocal n
            for scalar, titles, creators, tags in iter_records(source):
                text = compose_text(scalar, titles, creators, tags)
                frequencies = Counter(tokenize(text))
                if not frequencies:
                    skipped.append({'manga_id': scalar['manga_id'], 'reason': 'empty_indexable_text'})
                    continue
                document_id = scalar['manga_id']
                n += 1
                for term, tf in frequencies.items():
                    postings[term][document_id] = tf
                yield dict(document_id=document_id, manga_id=scalar['manga_id'], title=scalar['title'],
                           source_url=scalar['source_url'], token_count=sum(frequencies.values()),
                           text_sha256=hashlib.sha256(text.encode()).hexdigest(), text=text)
        write_tsv(temporary / 'documents.tsv', DOC_FIELDS, documents())
        terms = sorted(postings)
        write_tsv(temporary / 'postings.tsv', POST_FIELDS,
                  (dict(term=term, document_id=doc, tf=postings[term][doc]) for term in terms for doc in sorted(postings[term], key=int)))
        write_tsv(temporary / 'terms.tsv', TERM_FIELDS,
                  (dict(term=term, df=len(postings[term]), idf_classic=repr(idfs(n, len(postings[term]))[0]),
                        idf_smooth=repr(idfs(n, len(postings[term]))[1])) for term in terms))
        write_tsv(temporary / 'skipped_documents.tsv', ['manga_id', 'reason'], skipped)
        meta = {'format': FORMAT, 'N': n, 'terms': len(terms), 'postings': sum(len(v) for v in postings.values()),
                'skipped_empty': len(skipped), 'input_sha256': fingerprint, 'normalization': 'html.unescape -> NFC -> casefold',
                'tokenizer': r'[^\W_]+ (Unicode)', 'stopwords': 'none', 'stemming': 'none',
                'field_weight': 'equal; unique values once; title,alternatives,creators,description,genres,themes',
                'idf_classic': 'ln(N/df)', 'idf_smooth': 'ln((N+1)/(df+1))+1',
                'score': 'sum((1+ln(tf))*idf for unique known query terms); OR; numeric manga_id tie'}
        for name, digest in fingerprints.items():
            meta['input:' + name] = digest
        for name in ['documents.tsv', 'postings.tsv', 'terms.tsv', 'skipped_documents.tsv']:
            meta['sha256:' + name] = file_hash(temporary / name)
        # Publish hashes last to detect an interrupted write.
        for name in ['documents.tsv', 'postings.tsv', 'terms.tsv', 'skipped_documents.tsv']:
            os.replace(temporary / name, out / name)
        write_tsv(out / 'index_metadata.tsv', ['name', 'value'], ({'name': k, 'value': str(v)} for k, v in meta.items()))
    return {'N': n, 'terms': len(terms), 'postings': meta['postings'], 'skipped_empty': len(skipped)}


class Index:
    def __init__(self, root):
        self.root = Path(root)
        try:
            self._load()
        except (OSError, ValueError, KeyError, OverflowError) as error:
            raise ValueError(f'Poškodený alebo nekompatibilný index: {error}') from error

    def _load(self):
        rows = list(iter_tsv(self.root / 'index_metadata.tsv', ['name', 'value']))
        self.metadata = {row['name']: row['value'] for row in rows}
        if len(rows) != len(self.metadata) or self.metadata.get('format') != FORMAT:
            raise ValueError('nepodporovaná verzia alebo duplicitné metadáta')
        for name in ['documents.tsv', 'postings.tsv', 'terms.tsv', 'skipped_documents.tsv']:
            if file_hash(self.root / name) != self.metadata['sha256:' + name]:
                raise ValueError('nesúhlasí SHA-256 ' + name)
        self.documents = {}
        for row in iter_tsv(self.root / 'documents.tsv', DOC_FIELDS):
            doc = row['document_id']
            if doc in self.documents or doc != row['manga_id'] or int(doc) <= 0:
                raise ValueError('neplatné alebo duplicitné document_id')
            if hashlib.sha256(row['text'].encode()).hexdigest() != row['text_sha256'] or len(tokenize(row['text'])) != int(row['token_count']):
                raise ValueError('nekonzistentný text dokumentu')
            self.documents[doc] = row
        n = len(self.documents)
        if n != int(self.metadata['N']):
            raise ValueError('nesúhlasí N')
        self.terms = {}
        for row in iter_tsv(self.root / 'terms.tsv', TERM_FIELDS):
            term = row['term']
            df = int(row['df'])
            classic, smooth = idfs(n, df)
            if term in self.terms or not term or tokenize(term) != [term]:
                raise ValueError('neplatný alebo duplicitný term')
            if not math.isclose(float(row['idf_classic']), classic, rel_tol=1e-12, abs_tol=1e-12) or not math.isclose(float(row['idf_smooth']), smooth, rel_tol=1e-12, abs_tol=1e-12):
                raise ValueError('nesprávne IDF')
            self.terms[term] = (df, classic, smooth)
        self.postings = defaultdict(dict)
        counts = Counter()
        for row in iter_tsv(self.root / 'postings.tsv', POST_FIELDS):
            term, doc, tf = row['term'], row['document_id'], int(row['tf'])
            if term not in self.terms or doc not in self.documents or tf < 1 or doc in self.postings[term]:
                raise ValueError('neplatný alebo duplicitný posting')
            self.postings[term][doc] = tf
            counts[doc] += tf
        if any(len(self.postings[term]) != data[0] for term, data in self.terms.items()):
            raise ValueError('DF nezodpovedá postingom')
        if any(counts[doc] != int(row['token_count']) for doc, row in self.documents.items()):
            raise ValueError('TF nezodpovedá dĺžke dokumentu')
        if len(self.terms) != int(self.metadata['terms']) or sum(len(v) for v in self.postings.values()) != int(self.metadata['postings']):
            raise ValueError('nesúhlasia počty termov/postingov')

    def search(self, query, method='classic', top_k=10):
        if method not in {'classic', 'smooth'} or top_k < 1:
            raise ValueError('IDF musí byť classic/smooth a top-k kladné.')
        query_terms = set(tokenize(query))
        if not query_terms:
            raise ValueError('Prázdny dopyt: zadajte aspoň jedno písmeno alebo číslo.')
        scores = defaultdict(float)
        for term in sorted(query_terms):
            if term not in self.terms:
                continue
            weight = self.terms[term][1 if method == 'classic' else 2]
            for doc, tf in self.postings[term].items():
                scores[doc] += (1 + math.log(tf)) * weight
        ordered = sorted(scores, key=lambda doc: (-scores[doc], int(self.documents[doc]['manga_id'])))[:top_k]
        return [dict(rank=rank, manga_id=doc, title=self.documents[doc]['title'], score=scores[doc],
                     source_url=self.documents[doc]['source_url']) for rank, doc in enumerate(ordered, 1)]
