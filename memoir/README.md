# Memoir

Produces a clean transcript and an English translation of a scanned Polish
typescript, using a local vision-language model through Ollama. The PDF is
split into one PNG per page. Each PNG goes to the model together with the
PDF's rough OCR text for that page, and the model returns a corrected
transcript. The pages are then translated one at a time. Because the
typescript is paginated mid-sentence, each page's prompt also carries the
last paragraph of the previous page, in Polish and in the English already
produced for it, and the first paragraph of the next page in Polish, marked
as context and not to be translated. A second pass checks each translation
against its source. Every stage is resumable per page.

The memoir itself, the glossary of names and terms used in translating it,
and all output are kept out of this repository.

Needs `glossary.md` beside the script (copy `glossary.example.md` and fill
it in) and `poppler-utils`. Run with `uv run pamietnik.py <stage>`. `--help`
lists the stages.
