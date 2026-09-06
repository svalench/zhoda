"""Ограниченный source bundle: явные roots, без обхода диска и без URL-fetch."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from zhoda_core.evidence import MAX_EMBED_CHARS, redact_secrets

MAX_FILE_BYTES = 65_536
MAX_FILES = 8
MAX_INLINE_CHARS = MAX_EMBED_CHARS

_URL_PREFIXES = ("http://", "https://", "ftp://", "file:")


class SourceError(ValueError):
    """Вход источника отклонён до запуска совета."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def as_dict(self) -> dict[str, str]:
        return {"error": self.code, "message": self.message}


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_allowed_file(path: str, allowed_roots: Sequence[str]) -> Path:
    """Симлинк resolve() + relative_to. Traversal и выход за root — ошибка."""
    raw = path.strip()
    if not raw:
        raise SourceError("invalid_source", "empty source path")
    lowered = raw.casefold()
    if lowered.startswith(_URL_PREFIXES) or "://" in raw:
        raise SourceError(
            "url_fetch_forbidden",
            "decision-review does not fetch URLs; pass inline text or an allowed local file",
        )
    roots = [Path(r).expanduser().resolve() for r in allowed_roots if r.strip()]
    if not roots:
        raise SourceError(
            "allowed_roots_required",
            "source_paths require explicit allowed_roots; the tool does not scan disk",
        )
    target = Path(raw).expanduser().resolve()
    if not any(_is_under(target, root) for root in roots):
        raise SourceError(
            "path_outside_allowed_roots",
            f"{raw!r} resolves outside allowed_roots",
        )
    if not target.is_file():
        raise SourceError("not_a_file", f"{raw!r} is not a regular file under allowed_roots")
    return target


def load_text_file(path: Path) -> str:
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise SourceError(
            "oversized_source",
            f"{path.name} is {size} bytes; max {MAX_FILE_BYTES} per file",
        )
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SourceError("invalid_source", f"{path.name} is not UTF-8 text") from exc


def assemble_context(
    *,
    source_text: str = "",
    source_paths: Sequence[str] | None = None,
    allowed_roots: Sequence[str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Собрать context для engine. Секреты редактируются до передачи."""
    paths = list(source_paths or ())
    if len(paths) > MAX_FILES:
        raise SourceError("oversized_source", f"at most {MAX_FILES} source_paths")
    parts: list[str] = []
    manifest: list[dict[str, Any]] = []
    inline = (source_text or "").strip()
    if inline:
        if len(inline) > MAX_INLINE_CHARS:
            raise SourceError(
                "oversized_source",
                f"source_text is {len(inline)} chars; max {MAX_INLINE_CHARS}",
            )
        redacted, had_secret = redact_secrets(inline)
        parts.append(redacted)
        manifest.append(
            {
                "origin": "inline",
                "chars": len(redacted),
                "redacted": had_secret,
            }
        )
    roots = list(allowed_roots or ())
    for raw in paths:
        file_path = resolve_allowed_file(raw, roots)
        body = load_text_file(file_path)
        redacted, had_secret = redact_secrets(body)
        parts.append(f"# source: {file_path.name}\n{redacted}")
        manifest.append(
            {
                "origin": "file",
                "name": file_path.name,
                "chars": len(redacted),
                "redacted": had_secret,
            }
        )
    context = "\n\n".join(parts)
    if len(context) > MAX_INLINE_CHARS:
        raise SourceError(
            "oversized_source",
            f"combined sources are {len(context)} chars; max {MAX_INLINE_CHARS}",
        )
    return context, manifest
