#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["ollama>=0.4"]
# ///
"""
pamietnik.py — transcribe and translate a scanned Polish memoir with a local Ollama model.

Stages (run in order; each is resumable and skips pages already done):

  prepare     split the PDF into page images (png/) and rough OCR text (ocr/)
  transcribe  image + OCR text  ->  clean Polish transcript       pl/page_NNN.md
  names       collect personal/place names from pl/ into out/names.md (for the glossary)
  translate   Polish transcript  ->  English draft                en/page_NNN.md
  review      source + draft     ->  corrected English            en_reviewed/page_NNN.md
  assemble    stitch pages into out/memoir_pl.md and out/memoir_en.md
  all         transcribe + names + translate + assemble (no review)
  status      show how many pages each stage has completed

Everything talks to Ollama on localhost only. Nothing leaves the machine.
"""

import argparse
import csv
import difflib
import re
import subprocess
import sys
import time
from pathlib import Path

import ollama

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MODEL = "qwen3.8-27b-64k:latest"
HOST = "http://127.0.0.1:11434"
DPI = 200

ROOT = Path(__file__).resolve().parent
PDF = ROOT / "pamietnik-ocr.pdf"
GLOSSARY = ROOT / "glossary.md"

D = {
    "png": ROOT / "png",
    "ocr": ROOT / "ocr",
    "pl": ROOT / "pl",
    "en": ROOT / "en",
    "rev": ROOT / "en_reviewed",
    "out": ROOT / "out",
}

NO_TEXT = "[no text]"

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

TRANSCRIBE_SYSTEM = """You are transcribing one page of a Polish typewritten memoir from a scanned image.
You are also given a rough OCR of the same page. The OCR is unreliable for Polish diacritics
(ą ć ę ł ń ó ś ź ż) and sometimes mangles words, but it rarely invents whole words.

Produce an exact, corrected Polish transcript of the page.

Rules:
- The image is the authority. Use the OCR text only to help resolve what the typewriter struck.
- Keep every sentence. Do not translate, summarise, modernise, or correct the author's grammar or style. Add nothing.
- Words split across a line break are marked with a hyphen at the end of one line AND at the start
  of the next (e.g. "do-" / "-wód"). Join these back into one word ("dowód").
- Reflow each paragraph onto a single line. Separate paragraphs with one blank line.
- Put section headings (e.g. "I - WSTĘP") on their own line, prefixed with "## ".
- Omit the page number at the top of the page (e.g. "-2-").
- Include photo captions and any handwritten additions. Mark handwriting as [handwritten: ...].
- If a word is unreadable or you are not confident of it, give your best reading followed by [?].
- If the page contains no text at all, output exactly: [no text]
- Output only the transcript: no preamble, no commentary, no code fences."""

TRANSCRIBE_USER = """Rough OCR text of this page (unreliable):

{ocr}

Transcribe the page shown in the image."""

TRANSLATE_SYSTEM = """You are translating a Polish memoir into English for the author's family.

Style:
- British spelling (colour, organise, labour, centre, travelled).
- Use the serial (Oxford) comma.
- Keep the author's register and tone. Do not embellish, modernise, or editorialise.
- Where the Polish is awkward or repetitive, prefer clear, natural English that preserves the
  exact meaning over word-for-word literalness. Never smooth away content.

Rules:
- Translate faithfully and completely: every sentence, nothing added, nothing omitted, nothing summarised.
- Keep the paragraph breaks and headings exactly as in the source. Keep "## " heading prefixes.
- Follow the glossary below for every term it lists.
- Keep personal names and place names in their Polish spelling, including diacritics,
  unless the glossary gives an English form.
- Keep [?] markers where they appear in the source. Keep [handwritten: ...] markers and translate their content.
- Keep dates and numbers as written.
- If the source is exactly "[no text]", output exactly "[no text]".
- Output only the translation: no preamble, no translator's notes, no code fences.

Page breaks:
- The typescript was paginated mid-sentence. The page you are given may begin in the middle
  of a sentence and end in the middle of one. Translate exactly what is on this page, starting
  and stopping where it does, so that the pages join up when placed one after another.
- You may be given the end of the previous page (Polish and its English translation) and the
  start of the next page (Polish) FOR CONTEXT ONLY. Use them to understand a sentence that
  crosses the page break and to match the English wording at the join. Do NOT translate or
  repeat them in your output.

{glossary}"""

TRANSLATE_USER = """{context}Polish source (THIS PAGE — translate only this):

{pl}

Translate into English."""

CONTEXT_PREV = """End of the previous page, Polish (context only, do not translate):

{pl}

End of the previous page, English translation already made (context only, continue from it):

{en}

"""

CONTEXT_NEXT = """Start of the next page, Polish (context only, do not translate):

{pl}

"""

REVIEW_SYSTEM = """You are checking an English translation of one page of a Polish memoir against the Polish source.

Find and fix:
- sentences or clauses present in the Polish but missing from the English
- mistranslations or changes of meaning
- names, places, or glossary terms rendered inconsistently with the glossary below
- anything added that is not in the Polish

Keep everything that is already correct. Keep British spelling, the serial comma, paragraph
breaks, "## " headings, and any [?] or [handwritten: ...] markers.

The page may begin or end mid-sentence because the typescript was paginated that way. You may
be given the end of the previous page and the start of the next page FOR CONTEXT ONLY; use them
to check that sentences crossing the page break are translated correctly and join up, but do
not add them to your output.

Output only the corrected English translation of THIS PAGE: no commentary, no list of changes,
no code fences. If the draft needs no changes, output it unchanged.

{glossary}"""

REVIEW_USER = """{context}Polish source (THIS PAGE):

{pl}

English draft of this page:

{en}

Output the corrected English translation of this page."""

NAMES_SYSTEM = """You are indexing a Polish memoir. From the text you are given, list every personal name,
place name, and organisation or institution that appears.

- One entry per line, in the form:  Name — person|place|organisation
- Use the fullest form that appears in the text (e.g. first name and surname), Polish spelling with diacritics.
- For a person who appears under several forms (full name, surname only, nickname), give one entry
  with the full form and list the other forms after it in parentheses.
- No duplicates, no commentary, no headings. Output only the list."""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def page_count() -> int:
    out = subprocess.run(["pdfinfo", str(PDF)], capture_output=True, text=True, check=True).stdout
    m = re.search(r"^Pages:\s+(\d+)", out, re.M)
    if not m:
        die("could not read page count from pdfinfo")
    return int(m.group(1))


def parse_pages(spec: str | None, total: int) -> list[int]:
    if not spec:
        return list(range(1, total + 1))
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            pages.update(range(int(a), int(b) + 1))
        else:
            pages.add(int(part))
    return sorted(p for p in pages if 1 <= p <= total)


def pfile(d: Path, n: int, ext: str) -> Path:
    return d / f"page_{n:03d}.{ext}"


def png_path(n: int) -> Path:
    # pdftoppm pads to the width of the page count (3 digits for 100-999 pages)
    return D["png"] / f"page-{n:03d}.png"


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def write(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s.rstrip() + "\n", encoding="utf-8")


def strip_fences(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```[a-zA-Z]*\n", "", s)
    s = re.sub(r"\n```$", "", s)
    return s.strip()


def load_glossary() -> str:
    if not GLOSSARY.exists():
        die(
            f"{GLOSSARY.name} not found next to the script. Copy glossary.example.md to "
            f"{GLOSSARY.name} and fill in the names and terms for your document."
        )
    parts = [read(GLOSSARY).strip()]
    names = D["out"] / "names.md"
    if names.exists():
        parts.append(
            "## Names appearing in the document (keep Polish spelling)\n\n" + read(names).strip()
        )
    return "GLOSSARY\n\n" + "\n\n".join(parts)


_client: ollama.Client | None = None


def client() -> ollama.Client:
    global _client
    if _client is None:
        _client = ollama.Client(host=HOST)
    return _client


def chat(system: str, user: str, *, image: Path | None = None, temperature: float, num_ctx: int) -> str:
    msg: dict = {"role": "user", "content": user}
    if image is not None:
        msg["images"] = [str(image)]
    messages = [{"role": "system", "content": system}, msg]
    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            r = client().chat(
                model=MODEL,
                messages=messages,
                think=False,
                keep_alive=-1,
                options={
                    "temperature": temperature,
                    "num_ctx": num_ctx,
                },
            )
            return strip_fences(r.message.content or "")
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"    attempt {attempt} failed: {e}; retrying in {10 * attempt}s", flush=True)
            time.sleep(10 * attempt)
    raise RuntimeError(f"Ollama call failed after 3 attempts: {last_err}")


def tail_para(text: str, max_chars: int = 600) -> str:
    """Last paragraph of a page (or its final max_chars), for cross-page context."""
    text = text.strip()
    if not text or text == NO_TEXT:
        return ""
    para = [p for p in text.split("\n\n") if p.strip()][-1].strip()
    return para[-max_chars:]


def head_para(text: str, max_chars: int = 400) -> str:
    """First paragraph of a page (or its first max_chars), for cross-page context."""
    text = text.strip()
    if not text or text == NO_TEXT:
        return ""
    para = [p for p in text.split("\n\n") if p.strip()][0].strip()
    return para[:max_chars]


def page_context(n: int, en_dir: Path, total: int) -> str:
    """Context block for page n: end of page n-1 (pl + en) and start of page n+1 (pl)."""
    ctx = ""
    if n > 1:
        prev_pl = pfile(D["pl"], n - 1, "md")
        prev_en = pfile(en_dir, n - 1, "md")
        if prev_pl.exists():
            pl_tail = tail_para(read(prev_pl))
            en_tail = tail_para(read(prev_en)) if prev_en.exists() else ""
            if pl_tail:
                ctx += CONTEXT_PREV.format(pl=pl_tail, en=en_tail or "(not available)")
    if n < total:
        next_pl = pfile(D["pl"], n + 1, "md")
        if next_pl.exists():
            pl_head = head_para(read(next_pl))
            if pl_head:
                ctx += CONTEXT_NEXT.format(pl=pl_head)
    return ctx


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.split(), b.split()).ratio()


def log_row(name: str, header: list[str], row: list) -> None:
    p = D["out"] / f"{name}_log.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists()
    with p.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerow(row)


def fmt(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------


def stage_prepare(args) -> None:
    if not PDF.exists():
        die(f"{PDF} not found")
    for tool in ("pdfinfo", "pdftoppm", "pdftotext"):
        if subprocess.run(["which", tool], capture_output=True).returncode != 0:
            die(f"{tool} not found — install poppler-utils")
    total = page_count()
    print(f"{PDF.name}: {total} pages")

    D["png"].mkdir(exist_ok=True)
    D["ocr"].mkdir(exist_ok=True)

    missing_png = [n for n in range(1, total + 1) if not png_path(n).exists()]
    if missing_png or args.force:
        print(f"rendering page images at {DPI} dpi ...", flush=True)
        subprocess.run(
            ["pdftoppm", "-r", str(DPI), "-png", str(PDF), str(D["png"] / "page")],
            check=True,
        )
    else:
        print("page images already present")

    print("extracting OCR text layer per page ...", flush=True)
    for n in range(1, total + 1):
        out = pfile(D["ocr"], n, "txt")
        if out.exists() and not args.force:
            continue
        subprocess.run(
            ["pdftotext", "-layout", "-f", str(n), "-l", str(n), str(PDF), str(out)],
            check=True,
        )
    print("done. Next: transcribe (try --pages 1-3 first).")


def stage_transcribe(args) -> None:
    total = page_count()
    pages = parse_pages(args.pages, total)
    todo = [n for n in pages if args.force or not pfile(D["pl"], n, "md").exists()]
    print(f"transcribe: {len(todo)} of {len(pages)} pages to do")
    t_all = time.time()
    for i, n in enumerate(todo, 1):
        img = png_path(n)
        if not img.exists():
            die(f"{img} missing — run prepare first")
        ocr = read(pfile(D["ocr"], n, "txt")).strip() or "(OCR produced no text for this page)"
        t0 = time.time()
        text = chat(
            TRANSCRIBE_SYSTEM,
            TRANSCRIBE_USER.format(ocr=ocr),
            image=img,
            temperature=0.2,
            num_ctx=16384,
        )
        if not text:
            text = NO_TEXT
        write(pfile(D["pl"], n, "md"), text)
        dt = time.time() - t0
        unsure = text.count("[?]")
        sim = similarity(ocr, text)
        log_row(
            "transcribe",
            ["page", "seconds", "unsure", "ocr_similarity", "chars"],
            [n, f"{dt:.1f}", unsure, f"{sim:.3f}", len(text)],
        )
        elapsed = time.time() - t_all
        eta = elapsed / i * (len(todo) - i)
        print(
            f"[{i}/{len(todo)}] page {n:3d}  {dt:5.1f}s  unsure={unsure:2d}  "
            f"ocr_sim={sim:.2f}  chars={len(text):5d}  eta {fmt(eta)}",
            flush=True,
        )
    print("transcribe complete")


def stage_names(args) -> None:
    total = page_count()
    pl_pages = [n for n in range(1, total + 1) if pfile(D["pl"], n, "md").exists()]
    if not pl_pages:
        die("no transcripts in pl/ — run transcribe first")
    print(f"names: scanning {len(pl_pages)} transcribed pages")
    chunk = 15
    entries: dict[str, str] = {}
    for i in range(0, len(pl_pages), chunk):
        batch = pl_pages[i : i + chunk]
        text = "\n\n".join(read(pfile(D["pl"], n, "md")) for n in batch)
        out = chat(NAMES_SYSTEM, text, temperature=0.1, num_ctx=40000)
        for line in out.splitlines():
            line = line.strip(" -•*")
            if "—" not in line and " - " in line:
                line = line.replace(" - ", " — ", 1)
            if "—" not in line:
                continue
            key = line.split("—", 1)[0].strip().lower()
            if key and key not in entries:
                entries[key] = line
        print(f"  pages {batch[0]}-{batch[-1]}: {len(entries)} names so far", flush=True)
    lines = sorted(entries.values(), key=str.casefold)
    write(D["out"] / "names.md", "\n".join(lines))
    print(f"wrote out/names.md with {len(lines)} entries — skim it before translating")


def stage_translate(args) -> None:
    total = page_count()
    pages = parse_pages(args.pages, total)
    glossary = load_glossary()
    system = TRANSLATE_SYSTEM.format(glossary=glossary)
    todo = [n for n in pages if args.force or not pfile(D["en"], n, "md").exists()]
    print(f"translate: {len(todo)} of {len(pages)} pages to do")
    t_all = time.time()
    for i, n in enumerate(todo, 1):
        src = pfile(D["pl"], n, "md")
        if not src.exists():
            die(f"{src} missing — run transcribe first")
        pl = read(src).strip()
        t0 = time.time()
        if pl == NO_TEXT:
            en = NO_TEXT
        else:
            ctx = page_context(n, D["en"], total)
            en = chat(system, TRANSLATE_USER.format(context=ctx, pl=pl), temperature=0.3, num_ctx=16384)
        write(pfile(D["en"], n, "md"), en)
        dt = time.time() - t0
        ratio = len(en) / max(len(pl), 1)
        log_row(
            "translate",
            ["page", "seconds", "pl_chars", "en_chars", "ratio"],
            [n, f"{dt:.1f}", len(pl), len(en), f"{ratio:.2f}"],
        )
        elapsed = time.time() - t_all
        eta = elapsed / i * (len(todo) - i)
        flag = "  <-- check length" if pl != NO_TEXT and not (0.7 <= ratio <= 1.6) else ""
        print(
            f"[{i}/{len(todo)}] page {n:3d}  {dt:5.1f}s  ratio={ratio:.2f}  eta {fmt(eta)}{flag}",
            flush=True,
        )
    print("translate complete")


def stage_review(args) -> None:
    total = page_count()
    pages = parse_pages(args.pages, total)
    glossary = load_glossary()
    system = REVIEW_SYSTEM.format(glossary=glossary)
    todo = [n for n in pages if args.force or not pfile(D["rev"], n, "md").exists()]
    print(f"review: {len(todo)} of {len(pages)} pages to do")
    t_all = time.time()
    for i, n in enumerate(todo, 1):
        pl_p, en_p = pfile(D["pl"], n, "md"), pfile(D["en"], n, "md")
        if not (pl_p.exists() and en_p.exists()):
            die(f"page {n}: need both pl/ and en/ — run transcribe and translate first")
        pl, en = read(pl_p).strip(), read(en_p).strip()
        t0 = time.time()
        if pl == NO_TEXT:
            rev = NO_TEXT
        else:
            ctx = page_context(n, D["rev"], total)
            rev = chat(system, REVIEW_USER.format(context=ctx, pl=pl, en=en), temperature=0.2, num_ctx=16384)
        write(pfile(D["rev"], n, "md"), rev)
        dt = time.time() - t0
        changed = similarity(en, rev)
        log_row(
            "review",
            ["page", "seconds", "draft_similarity"],
            [n, f"{dt:.1f}", f"{changed:.3f}"],
        )
        elapsed = time.time() - t_all
        eta = elapsed / i * (len(todo) - i)
        print(
            f"[{i}/{len(todo)}] page {n:3d}  {dt:5.1f}s  kept={changed:.2f}  eta {fmt(eta)}",
            flush=True,
        )
    print("review complete")


def stage_assemble(args) -> None:
    total = page_count()
    use_rev = all(pfile(D["rev"], n, "md").exists() for n in range(1, total + 1))
    en_dir = D["rev"] if use_rev else D["en"]
    print(f"assemble: English from {en_dir.name}/")

    def build(src_dir: Path, title: str) -> tuple[str, int]:
        parts = [f"# {title}\n"]
        missing = 0
        for n in range(1, total + 1):
            p = pfile(src_dir, n, "md")
            parts.append(f"<!-- p. {n} -->\n")
            if p.exists():
                parts.append(read(p).strip() + "\n")
            else:
                parts.append(f"*[page {n} not yet processed]*\n")
                missing += 1
        return "\n".join(parts), missing

    pl_md, pl_missing = build(D["pl"], "Pamiętnik Leopolda")
    en_md, en_missing = build(en_dir, "Leopold's Memoir")
    write(D["out"] / "memoir_pl.md", pl_md)
    write(D["out"] / "memoir_en.md", en_md)
    print(f"wrote out/memoir_pl.md ({pl_missing} pages missing)")
    print(f"wrote out/memoir_en.md ({en_missing} pages missing)")


def stage_status(args) -> None:
    total = page_count()
    for name, d, ext in (
        ("png", D["png"], None),
        ("ocr", D["ocr"], "txt"),
        ("pl", D["pl"], "md"),
        ("en", D["en"], "md"),
        ("en_reviewed", D["rev"], "md"),
    ):
        if ext is None:
            done = sum(1 for n in range(1, total + 1) if png_path(n).exists())
        else:
            done = sum(1 for n in range(1, total + 1) if pfile(d, n, ext).exists())
        print(f"{name:12s} {done:4d} / {total}")
    names = D["out"] / "names.md"
    print(f"{'names.md':12s} {'yes' if names.exists() else 'no'}")
    log = D["out"] / "transcribe_log.csv"
    if log.exists():
        rows = list(csv.DictReader(log.open(encoding="utf-8")))
        if rows:
            unsure = sorted(rows, key=lambda r: -int(r["unsure"]))[:5]
            lowsim = sorted(rows, key=lambda r: float(r["ocr_similarity"]))[:5]
            print("\nmost [?] markers:   " + ", ".join(f"p{r['page']}({r['unsure']})" for r in unsure))
            print("least like OCR:     " + ", ".join(f"p{r['page']}({float(r['ocr_similarity']):.2f})" for r in lowsim))


def stage_all(args) -> None:
    stage_transcribe(args)
    stage_names(args)
    stage_translate(args)
    stage_assemble(args)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "stage",
        choices=["prepare", "transcribe", "names", "translate", "review", "assemble", "all", "status"],
    )
    ap.add_argument("--pages", help="page range, e.g. 1-3 or 5,10-12 (default: all)")
    ap.add_argument("--force", action="store_true", help="redo pages even if output exists")
    args = ap.parse_args()
    for d in D.values():
        d.mkdir(exist_ok=True)
    {
        "prepare": stage_prepare,
        "transcribe": stage_transcribe,
        "names": stage_names,
        "translate": stage_translate,
        "review": stage_review,
        "assemble": stage_assemble,
        "all": stage_all,
        "status": stage_status,
    }[args.stage](args)


if __name__ == "__main__":
    main()
