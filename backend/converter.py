"""In-process DOCX to Markdown conversion."""

from __future__ import annotations

from pathlib import Path

from markitdown import MarkItDown

from .files import FileValidationError, normalize_markdown


class DocxMarkdownConverter:
    def __init__(self, max_output_bytes: int):
        self.max_output_bytes = max_output_bytes
        self.converter = MarkItDown(enable_plugins=False)

    def convert(self, source: Path, output: Path, extension: str) -> None:
        if extension == ".md":
            data = source.read_bytes()
            try:
                markdown = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise FileValidationError("Markdown file must be UTF-8") from exc
        elif extension == ".docx":
            with source.open("rb") as stream:
                result = self.converter.convert_stream(stream, file_extension=".docx")
            markdown = result.markdown
        else:
            raise FileValidationError("unsupported source extension")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(normalize_markdown(markdown, self.max_output_bytes))
