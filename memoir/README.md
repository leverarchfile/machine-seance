# Memoir

`pamietnik.py` produces a clean transcript and an English translation of a
scanned Polish typescript, using a local vision-language model through Ollama.
The PDF is split into one PNG per page. Each PNG goes to the model together
with the PDF's rough OCR text for that page, and the model returns a corrected
transcript. The pages are then translated one at a time. Because the typescript
is paginated mid-sentence, each page's prompt also carries the last paragraph
of the previous page, in Polish and in the English already produced for it, and
the first paragraph of the next page in Polish, marked as context and not to be
translated. A second pass checks each translation against its source. Every
stage is resumable per page.

The memoir itself, the glossary of names and terms used in translating it, and
all output are kept out of this repository.

Needs `glossary.md` beside the script (copy `glossary.example.md` and fill it
in) and `poppler-utils`. Run with `uv run pamietnik.py <stage>`. `--help` lists
the stages.

## Stages

| Stage        | Input → output                              | Page  | Total   |
|--------------|---------------------------------------------|-------|---------|
| `prepare`    | PDF → `png/` (200 dpi), `ocr/` (OCR text)   | —     | ~3 min  |
| `transcribe` | page image + OCR text → `pl/`               | ~12 s | ~40 min |
| `names`      | all of `pl/` → `out/names.md`               | —     | ~2 min  |
| `translate`  | Polish + neighbouring context → `en/`       | ~9 s  | ~30 min |
| `review`     | Polish + draft + context → `en_reviewed/`   | ~7 s  | ~25 min |
| `assemble`   | per-page files → `out/memoir_{pl,en}.md`    | —     | seconds |

Per-page files are named `page_NNN.md`. Timings are for `qwen3.8-27b-64k`
(Q4_K_M) on an RTX PRO 4000 Blackwell with 24 GB of VRAM; totals are for 195
pages. A stage skips pages whose output exists; `--pages 5-7` restricts it to a
range, and `--force` redoes pages. Because translation uses the English of the
preceding page, redo a page together with the one after it. `all` runs
transcribe → names → translate → assemble; `review` and a final `assemble` are
run separately. `status` reports progress and lists the pages with the most
`[?]` markers and the lowest OCR similarity. The assembled files carry
`<!-- p. N -->` markers, where N is the PDF page number.

## Transcription

The model receives the page image and the OCR text and is told to treat the
image as the authority, using the OCR text only where the typewriter strike is
ambiguous. Words split across lines with the typist's double hyphen (`do-` /
`-wód`) are rejoined. Headings are output as `## `, handwriting as
`[handwritten: ...]`, and words the model cannot read as `[?]`. Across 195
pages the model marked no word as `[?]`, yet comparing its transcripts against
the scans shows that it did misread words: where it could not read one, it
supplied a plausible word with the same confidence as the rest. The `[?]`
count is therefore not usable as a confidence signal with this model; the
OCR-similarity score in `transcribe_log.csv` is the better guide to pages
worth checking.

The `names` stage lists every person, place, and organisation found in the
transcript. The list is appended to the glossary for the translate and review
stages so that spellings stay consistent across pages.

## Model and settings

`qwen3.8-27b-64k:latest` in Ollama for every stage (it has vision). Thinking
off. Temperature 0.2 for transcription, 0.3 for translation and review;
sampling is not deterministic, so a redone page returns in slightly different
words. `num_ctx` 16384 is sufficient for a page with context. Model, host, DPI,
and the four system prompts (`TRANSCRIBE_SYSTEM`, `TRANSLATE_SYSTEM`,
`REVIEW_SYSTEM`, `NAMES_SYSTEM`) are constants at the top of the script. The
glossary is read from `glossary.md` plus `out/names.md` at the start of
translate and review.

## Quality

One page was compared line by line against the scan, with the help of a larger
model since the author does not read Polish; several pages were read in English
by the author for sense. Findings:

- Substance: every sentence present, in order, meaning intact.
- Voice: the register of the original is kept; the model does not modernise.
- Typical slips: one misparsed clause per few pages, an occasional slightly-off
  word choice, and occasional borrowing of a detail from an earlier sentence
  to make the English flow. The `review` stage catches some of these, not all.
- In the test pages, errors clustered in specialist vocabulary; adding the
  terms to `glossary.md` and redoing those pages with `--force` resolved them.

Per-page timings, `[?]` counts, OCR similarity, and length ratios are written
to `out/*_log.csv`.
