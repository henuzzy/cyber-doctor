# PubMed full-text collection

The collector uses PubMed to find articles for the exact species name and
downloads accessible PMC JATS XML, with Europe PMC as a fallback for the same
PMCID. It does not count an abstract as a completed article. Publisher-only
articles and paywalled full text are outside this collector's current sources.
The collection stage preserves article text; it does not generate clinical
conclusions or call a language model.

## Pilot on DGX

The input is a byte-identical copy of the specified local `positive.jsonl`.
It contains 1,760 unique NameIDs. The older UpToDate input is a different copy
and is not overwritten. The pilot selects the first 100 unique NameIDs in input
order and aims for three different PMIDs for each pathogen.

```bash
cd /home/zhangyue/cyber-doctor
.venv/bin/python scripts/data/collect_pubmed_fulltext.py crawl \
  --input /home/zhangyue/outputs/pubmed_positive_fulltext_input/positive.jsonl \
  --output-dir /home/zhangyue/outputs/pubmed_positive_fulltext_pilot100 \
  --limit 100 --articles 3 --pause 2
```

Requests are serial. Each successful or failed HTTP request is followed by a
two-second pause. HTTP 429 and transient server failures trigger longer backoff,
respecting `Retry-After`. Repeated 429/503 responses or PubMed access denial
pause the entire run. The script never rotates addresses or bypasses access
restrictions. No API credential is stored in source or request logs.

Each pathogen searches up to 100 PubMed candidates ranked by relevance, using:

```text
"Species name"[Title/Abstract] AND "pubmed pmc"[sb]
```

The collector preserves input order and does not substitute genus-level hits.
It checks that the target species occurs in the article title, abstract or body,
not merely in references. This is a relevance screen, not a guarantee of direct
human clinical evidence; that distinction still requires article review.

## Output and resumption

Output contains:

* `collector.json`: format marker that prevents adoption of an unrelated corpus.
* `run.json`: selected NameIDs, input hash, options, process ID and run status.
* `summary.json` and `results.jsonl`: atomic progress snapshots.
* `requests.jsonl`: request timestamps, response status and configured pause.
* `pathogens/<NameID-and-hash>/manifest.json`: pathogen state, articles,
  candidate failures and manually rejected PMIDs.
* `pathogens/<NameID-and-hash>/articles/PMID_<PMID>/`: original `source.xml`,
  rendered `article.md` and provenance in `metadata.json`.
* `cache/`: previously validated full-text XML, shared across pathogens.
* `trash/`: archived files from reset/reject operations.

An article counts only when its XML has a substantive `<body>` (at least 500
characters and prose paragraphs), its species and PMID match, and its Markdown
exists. Abstracts are stored alongside the body when present. Tables, figure
captions and back matter are retained; images and supplementary downloads are
not collected. Original XML remains available for checking conversion quality.

Rerun the same crawl command to resume. A pathogen is skipped only after three
stored articles pass integrity checks. Missing, modified, abstract-only or
otherwise invalid files make it eligible for repair. Partial and failed
pathogens are retried. Old Markdown directories are not treated as completed
full-text collections just because their names exist.

`complete` means the target article count was verified. `partial`,
`no_fulltext`, `no_query_name` and `error` are not complete. Candidate exhaustion
and the search result count are recorded, so a limited candidate pool is not
presented as an exhaustive literature search.

Inspect progress without interrupting the collector:

```bash
.venv/bin/python scripts/data/collect_pubmed_fulltext.py status \
  --output-dir /home/zhangyue/outputs/pubmed_positive_fulltext_pilot100
```

## Stop, check, delete and recollect

Stop cleanly before modifying an active collection. The current HTTP operation
finishes, then the job saves its state and exits. A file lock prevents a reset,
reject or audit from racing with a running writer.

```bash
.venv/bin/python scripts/data/collect_pubmed_fulltext.py stop \
  --output-dir /home/zhangyue/outputs/pubmed_positive_fulltext_pilot100
```

Wait until `run.json` shows `stopped`, then audit saved articles:

```bash
.venv/bin/python scripts/data/collect_pubmed_fulltext.py audit \
  --output-dir /home/zhangyue/outputs/pubmed_positive_fulltext_pilot100
```

The audit writes `audit.json` and changes incomplete pathogens to `pending`.
It checks structure and integrity; it cannot automatically establish that
every scientific claim in an article is correct or relevant to a patient.

Reject one article after manual review. Its files move to `trash/`; the PMID
is excluded for this pathogen and its state becomes `pending`. The next crawl
keeps its other valid articles and searches for a replacement.

```bash
.venv/bin/python scripts/data/collect_pubmed_fulltext.py reject \
  --output-dir /home/zhangyue/outputs/pubmed_positive_fulltext_pilot100 \
  --name-id 'id|00099539|BAC|S_1894172|' --pmid 34734070 \
  --reason 'Full text or relevance did not pass manual review'
```

Reset a whole pathogen, including its XML cache, to download again. All old
pathogen files move to `trash/`, so an accidental reset is recoverable. Prior
manual PMID exclusions are retained unless `--clear-rejections` is also used.
The original input `positive.jsonl` remains the source of truth.

```bash
.venv/bin/python scripts/data/collect_pubmed_fulltext.py reset \
  --output-dir /home/zhangyue/outputs/pubmed_positive_fulltext_pilot100 \
  --name-id 'id|00099539|BAC|S_1894172|' --drop-cache
```

Recollect a particular pathogen (repeat `--name-id` for multiple targets):

```bash
.venv/bin/python scripts/data/collect_pubmed_fulltext.py crawl \
  --input /home/zhangyue/outputs/pubmed_positive_fulltext_input/positive.jsonl \
  --output-dir /home/zhangyue/outputs/pubmed_positive_fulltext_pilot100 \
  --name-id 'id|00099539|BAC|S_1894172|' --articles 3 --pause 2 \
  --resume-stopped
```

After an explicit stop or server throttle, `--resume-stopped` acknowledges the
pause and resumes. Without it, a persistent stop marker prevents network work.

## Verification

```bash
python -m unittest discover -s tests -p test_pubmed_fulltext_collector.py -v
```
