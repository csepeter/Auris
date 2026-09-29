"""Auris command line: import, generate, check and export books in batch.

Examples (from the reader folder, with the project environment)::

    .venv\\Scripts\\python.exe auris_cli.py import konyv.epub --mode single
    .venv\\Scripts\\python.exe auris_cli.py books
    .venv\\Scripts\\python.exe auris_cli.py export 3 --format m4b --intro --outro
    .venv\\Scripts\\python.exe auris_cli.py export 3 --package epub3 --chapters 1-5
    .venv\\Scripts\\python.exe auris_cli.py qa 3 --chapter 2
    .venv\\Scripts\\python.exe auris_cli.py speak "Jó napot!" --voice Narrátor -o hang.mp3

The commands talk to a running Auris server (``--server``, default
http://127.0.0.1:7860). With ``--start`` the CLI starts Auris in the
background of its own process when no server answers.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

DEFAULT_SERVER = os.environ.get("AURIS_SERVER", "http://127.0.0.1:7860")


class AurisError(RuntimeError):
    pass


class Client:
    def __init__(self, server: str):
        self.server = server.rstrip("/")

    def request(self, method: str, path: str, data=None, *, raw: bool = False, timeout: float = 600):
        headers = {}
        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.server + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            try:
                message = json.loads(text).get("error") or text
                if isinstance(message, dict):
                    message = message.get("message") or text
            except ValueError:
                message = text
            raise AurisError(f"HTTP {exc.code}: {message}") from None
        except urllib.error.URLError as exc:
            raise AurisError(f"Az Auris nem érhető el ({self.server}): {exc.reason}") from None
        return payload if raw else json.loads(payload or b"null")

    def upload(self, path: str, file_path: str):
        boundary = uuid.uuid4().hex
        name = os.path.basename(file_path)
        with open(file_path, "rb") as handle:
            content = handle.read()
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
        req = urllib.request.Request(
            self.server + path, data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise AurisError(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:400]}") from None

    def alive(self) -> bool:
        try:
            self.request("GET", "/api/tts/status", timeout=3)
            return True
        except AurisError:
            return False

    def wait_engine(self, timeout: float = 900) -> None:
        deadline = time.time() + timeout
        self.request("POST", "/api/tts/load", {})
        while time.time() < deadline:
            status = self.request("GET", "/api/tts/status")
            if status.get("state") == "ready":
                return
            if status.get("state") == "error":
                raise AurisError(status.get("message") or "A beszédmotor nem töltődött be.")
            time.sleep(2)
        raise AurisError("A beszédmotor nem lett kész időben.")

    def wait_job(self, job_id: str, *, quiet: bool = False) -> dict:
        last = ""
        while True:
            job = self.request("GET", f"/api/jobs/{job_id}")
            message = f"[{job.get('done', 0)}/{job.get('total', 0)}] {job.get('message', '')}"
            if message != last and not quiet:
                print(message, file=sys.stderr, flush=True)
                last = message
            if job.get("state") in ("complete", "failed", "cancelled", "interrupted"):
                if job["state"] != "complete":
                    raise AurisError(job.get("error") or job.get("message") or job["state"])
                return job
            time.sleep(1.5)


def start_in_process(server: str) -> None:
    """Run the Auris Flask app in a daemon thread of this process."""
    from urllib.parse import urlsplit

    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    os.chdir(here)
    import app as application

    parts = urlsplit(server)
    host, port = parts.hostname or "127.0.0.1", parts.port or 7860
    with application.app.app_context():
        application._startup()
    thread = threading.Thread(
        target=lambda: application.app.run(host=host, port=port, threaded=True, use_reloader=False),
        daemon=True,
    )
    thread.start()


def cmd_books(client: Client, args) -> None:
    books = client.request("GET", "/api/books")
    for book in books:
        print(f"{book['id']:>4}  {book.get('title', '')} — {book.get('author', '')}")


def cmd_import(client: Client, args) -> None:
    preview = client.upload("/api/import/preview", args.file)
    body = {"token": preview["token"], "narration_mode": args.mode}
    for key in ("title", "author", "language"):
        if getattr(args, key):
            body[key] = getattr(args, key)
    result = client.request("POST", "/api/import/confirm", body)
    book_id = result.get("book_id") or result.get("id")
    print(book_id)
    if result.get("analysis_job_id") and args.wait:
        client.wait_job(result["analysis_job_id"])


def _chapter_ids(client: Client, book_id: int, selection: str | None) -> list[int]:
    chapters = client.request("GET", f"/api/books/{book_id}/chapters")
    if not selection or selection == "all":
        return [c["id"] for c in chapters]
    wanted: set[int] = set()
    for part in selection.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            wanted.update(range(int(a), int(b) + 1))
        elif part:
            wanted.add(int(part))
    return [c["id"] for n, c in enumerate(chapters, 1) if n in wanted]


def cmd_generate(client: Client, args) -> None:
    client.wait_engine()
    for chapter_id in _chapter_ids(client, args.book_id, args.chapters):
        job = client.request("POST", f"/api/books/{args.book_id}/chapters/{chapter_id}/generate", {})
        if job.get("job_id"):
            client.wait_job(job["job_id"])


def cmd_export(client: Client, args) -> None:
    client.wait_engine()
    body = {
        "audio_fmt": args.format, "sub_fmt": args.subtitles, "chapters": args.chapters or "all",
        "package": args.package, "intro": args.intro, "outro": args.outro,
        "sample": args.sample, "abs_upload": args.upload,
    }
    job = client.request("POST", f"/api/books/{args.book_id}/export/chapterwise", body)
    done = client.wait_job(job["job_id"])
    result = done.get("result") or {}
    print(result.get("download_path") or result.get("export_path"))


def cmd_qa(client: Client, args) -> None:
    client.wait_engine()
    chapters = _chapter_ids(client, args.book_id, str(args.chapter) if args.chapter else None)
    for chapter_id in chapters:
        job = client.request("POST", f"/api/books/{args.book_id}/chapters/{chapter_id}/qa",
                             {"asr": not args.no_asr, "auto_regenerate": not args.no_regenerate})
        done = client.wait_job(job["job_id"])
        summary = client.request("GET", f"/api/books/{args.book_id}/chapters/{chapter_id}/qa")["summary"]
        print(json.dumps({"chapter_id": chapter_id, **summary, **(done.get("result") or {})},
                         ensure_ascii=False))


def cmd_speak(client: Client, args) -> None:
    client.wait_engine()
    fmt = args.format or os.path.splitext(args.output)[1].lstrip(".").lower() or "mp3"
    audio = client.request("POST", "/v1/audio/speech", {
        "model": "auris", "input": args.text, "voice": args.voice or "",
        "response_format": fmt, "speed": args.speed, "language": args.language,
    }, raw=True)
    with open(args.output, "wb") as handle:
        handle.write(audio)
    print(args.output)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="auris", description="Auris parancssor")
    parser.add_argument("--server", default=DEFAULT_SERVER, help="Auris szerver címe")
    parser.add_argument("--start", action="store_true",
                        help="Az Auris indítása ebben a folyamatban, ha nem fut")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("books", help="Könyvek listája")

    p = sub.add_parser("import", help="Dokumentum importálása")
    p.add_argument("file")
    p.add_argument("--title")
    p.add_argument("--author")
    p.add_argument("--language")
    p.add_argument("--mode", choices=["single", "characters"], default="single")
    p.add_argument("--wait", action="store_true", help="Várjon a szereplőelemzésre")

    p = sub.add_parser("generate", help="Fejezetek hangjának elkészítése")
    p.add_argument("book_id", type=int)
    p.add_argument("--chapters", help="pl. 1,3,5-8 vagy all")

    p = sub.add_parser("export", help="Könyv exportálása")
    p.add_argument("book_id", type=int)
    p.add_argument("--format", default="m4b", choices=["wav", "mp3", "m4b", "opus", "flac"])
    p.add_argument("--subtitles", default="none", choices=["none", "srt", "ass"])
    p.add_argument("--package", default="none",
                   choices=["none", "epub3", "audiobookshelf", "acx", "daw"])
    p.add_argument("--chapters")
    p.add_argument("--intro", action="store_true")
    p.add_argument("--outro", action="store_true")
    p.add_argument("--sample", action="store_true")
    p.add_argument("--upload", action="store_true", help="Feltöltés Audiobookshelfre")

    p = sub.add_parser("qa", help="Minőségellenőrzés")
    p.add_argument("book_id", type=int)
    p.add_argument("--chapter", type=int, help="Fejezet sorszáma (alapból mind)")
    p.add_argument("--no-asr", action="store_true")
    p.add_argument("--no-regenerate", action="store_true")

    p = sub.add_parser("speak", help="Egy szöveg felolvasása fájlba")
    p.add_argument("text")
    p.add_argument("-o", "--output", default="auris.mp3")
    p.add_argument("--voice", help="Mentett hangprofil neve, hangleírás vagy beépített hang")
    p.add_argument("--format", choices=["mp3", "wav", "opus", "flac", "aac"])
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--language", default="hu")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = Client(args.server)
    if not client.alive():
        if not args.start:
            print(f"Az Auris nem fut itt: {args.server}. Indítsd el, vagy használd a --start kapcsolót.",
                  file=sys.stderr)
            return 2
        start_in_process(args.server)
        for _ in range(60):
            if client.alive():
                break
            time.sleep(0.5)
    handlers = {"books": cmd_books, "import": cmd_import, "generate": cmd_generate,
                "export": cmd_export, "qa": cmd_qa, "speak": cmd_speak}
    try:
        handlers[args.command](client, args)
    except AurisError as exc:
        print(f"Hiba: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
