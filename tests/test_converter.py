from __future__ import annotations

import io
import zipfile

from backend.converter import DocxMarkdownConverter


def minimal_docx() -> bytes:
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
    relationships = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
    document = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Knowledge title</w:t></w:r></w:p>
    <w:p><w:r><w:t>Hello from DOCX</w:t></w:r></w:p>
    <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>First item</w:t></w:r></w:p>
    <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Header</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Value</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
  </w:body>
</w:document>"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", document)
    return output.getvalue()


def test_docx_conversion_preserves_text_structure(tmp_path):
    source = tmp_path / "guide.docx"
    output = tmp_path / "guide.md"
    source.write_bytes(minimal_docx())

    DocxMarkdownConverter(100_000).convert(source, output, ".docx")

    markdown = output.read_text()
    assert "Knowledge title" in markdown
    assert "Hello from DOCX" in markdown
    assert "Header" in markdown
    assert markdown.endswith("\n")


def test_markdown_conversion_normalizes_utf8(tmp_path):
    source = tmp_path / "notes.md"
    output = tmp_path / "notes-output.md"
    source.write_bytes(b"\xef\xbb\xbf# Notes\r\n")

    DocxMarkdownConverter(100_000).convert(source, output, ".md")

    assert output.read_text() == "# Notes\n"
