"""Small dependency-free HTML table reader for the three fixed sources."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Iterable


SPACE_RE = re.compile(r"\s+")


def clean_text(value: str) -> str:
    return SPACE_RE.sub(" ", html.unescape(value)).strip()


@dataclass(frozen=True)
class TableCell:
    text: str
    links: tuple[str, ...] = ()


@dataclass(frozen=True)
class TableRow:
    cells: tuple[TableCell, ...]
    attributes: dict[str, str]


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[TableRow] = []
        self._row_cells: list[TableCell] | None = None
        self._row_attributes: dict[str, str] = {}
        self._cell_parts: list[str] | None = None
        self._cell_links: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = {key: value or "" for key, value in attrs}
        if tag == "tr":
            self._row_cells = []
            self._row_attributes = attributes
        elif tag in {"td", "th"} and self._row_cells is not None:
            self._cell_parts = []
            self._cell_links = []
        elif tag == "a" and self._cell_parts is not None:
            href = attributes.get("href", "").strip()
            if href:
                self._cell_links.append(href)
        elif tag in {"br", "p", "div", "li"} and self._cell_parts is not None:
            self._cell_parts.append(" ")
        elif tag == "input" and self._cell_parts is not None:
            value = attributes.get("value", "").strip()
            if value:
                self._cell_parts.extend((" ", value))

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell_parts is not None:
            assert self._row_cells is not None
            self._row_cells.append(
                TableCell(clean_text("".join(self._cell_parts)), tuple(self._cell_links))
            )
            self._cell_parts = None
            self._cell_links = []
        elif tag == "tr" and self._row_cells is not None:
            if self._row_cells:
                self.rows.append(TableRow(tuple(self._row_cells), self._row_attributes))
            self._row_cells = None
            self._row_attributes = {}

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)


def table_rows(document: str) -> list[TableRow]:
    parser = _TableParser()
    parser.feed(document)
    parser.close()
    return parser.rows


def row_texts(row: TableRow) -> tuple[str, ...]:
    return tuple(cell.text for cell in row.cells)


def first_link(row: TableRow) -> str:
    return next(
        (link for cell in row.cells for link in cell.links if link.strip()),
        "",
    )


def rows_with_cell_count(rows: Iterable[TableRow], minimum: int) -> list[TableRow]:
    return [row for row in rows if len(row.cells) >= minimum]
