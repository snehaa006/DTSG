# Internship report

`dtsg_internship_report.tex` — the ACM Research Summer Internship 2026 report for
DTSG, written against the department's template (title page, undertaking,
certificate, acknowledgement, contents, then Sections 1–7 and references).

## Build

```bash
pdflatex dtsg_internship_report.tex   # run twice so the table of contents resolves
```

Packages used beyond a base TeX Live install: `booktabs`, `tabularx`,
`algorithm` + `algpseudocode`, `tikz`, `listings`, `hyperref`
(`texlive-latex-recommended`, `texlive-science`, `texlive-pictures`).

## Before submitting

Every placeholder is marked with a `% <<< FILL` comment. They are:

- title page — student names, enrollment numbers, official emails, faculty name
  and designation
- dedication page (optional)
- student undertaking — date, faculty name, names of AI-assisted tools used
- certificate — date, student names
- acknowledgement — to be written by the student
- the contributions table (Table 1) — split as the team actually divided it; the
  entries there are a plausible split, not a record of one

## Where the numbers come from

Nothing in the report is invented; each figure is reproducible from the repo:

| Claim in the report | Command |
| --- | --- |
| DTSG 3/3 vs baseline 0/3 correct; 0/4 vs 3/4 stale top hits (Tables 7–8) | `cd backend && .venv/bin/python scripts/run_benchmark.py` |
| The Delhi→Berlin term ablation (Table 9) | `cd backend && .venv/bin/python scripts/check_retrieval.py` |
| 103 passing tests | `cd backend && DATABASE_URL=unused:// .venv/bin/python -m pytest` |
| Schema counts (Tables 4–5) | `supabase/migrations/0001–0004` |
| 15 canonical predicates, 22 synonyms | `backend/app/extraction.py` |

## Citations

The related-work section cites 22 real papers. Verify the arXiv identifiers and
venues against the published versions before submission — they are quoted from
memory and a wrong number is the easiest thing for an evaluator to catch.
