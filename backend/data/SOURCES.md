# Source inventory

`sources.json` lists the official public sources ingested by
`python -m app.ingest --manifest data/sources.json --prune --reindex`.
All come from `https://christuniversity.in/` (public pages and linked PDFs; no login, no student portals).
The site serves no `robots.txt` (the path returns the home page), so there are no published crawl rules;
ingestion fetches only the listed URLs, one at a time with a 1-second pause. Retrieval dates are stored
per document (`fetched_at`) at ingest time.

Publication dates are recorded only where the official file name carries an upload timestamp; other
documents have no publication date rather than a guessed one.

## Supplied documents

`Scholarship Notice.zip` (supplied locally, 8 PDFs). No official URL could be found for these files, so they are
ingested with provenance `supplied_document` and a `supplied://scholarship-notices/...` reference:

```
python -m app.ingest --zip "<path to>/Scholarship Notice.zip" --collection scholarship-notices --reindex
```

The academic calendar PDF that was supplied with it is byte-identical (SHA-256) to the official file, so it is
ingested from its official URL instead.

## Reviewed but not ingested

| Source | Reason |
|---|---|
| Scholarship Policy PDF (`uploads/userfiles/Scholarship Policy(4).pdf`) | Scanned: no text layer (OCR not supported) |
| CU Regulation Student Code of Conduct PDF | Scanned: no text layer |
| Regulation for Substance Abuse PDF | Scanned: no text layer |
| CU Regulation for Information Technology PDF | Scanned: no text layer |
| Code of Conduct for IT End Users PDF | Scanned: no text layer |
| CU Regulation for Prevention and Control of Ragging (2019) PDF | Scanned: no text layer |
| 5 of 8 supplied scholarship notices (HDFC ECSS, LIC Golden Jubilee, SAGE IT, Swami Dayananda, Santoor) | Poster PDFs: only the shared "About Us" block is text; details are images |
| Hostel & Dining page (`/hostel/main-campus/hostel-and-dining`) | Page HTML contains only navigation; content not in the HTML |
| Library portal (`library.christuniversity.in`) | No text in the served HTML (rendered by JavaScript) |
| `/support-and-assistance/main-campus/examination` | Same content as `/examination` |
| Code of conduct, substance abuse, IT regulation, Academic Bank of Credits, campus life, academics, programmes listing pages | Link lists (under ~1,000 characters of text); their linked PDFs are listed above |
