"""実験レポート（`report.md`）の Markdown を HTML に変える。

対象は `evaluation/adapters/search_report.py` と `report.py` が出す書式だけに絞る（2026-10-27 の
人間の決定「自前の小さな変換」）: 見出し（`#`〜`####`）、表（セルの `\\|` と `\\\\` の逃がしを
含む）、箇条書き（2 空白ごとの入れ子）、段落、行内のコード（`` `…` ``）と太字（`**…**`）。
それ以外の記法は段落の文字としてそのまま出す（解釈しない）。文字はすべて HTML の逃がしを通す。

見出しの 2 段目（`## `）ごとに節へ分けて返し、画面は節の単位で折りたたみと版の比較をする。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

_HEADING = re.compile(r"^(#{1,6}) (.*)$")
_LIST_ITEM = re.compile(r"^( *)- (.*)$")
_TABLE_SEPARATOR = re.compile(r"^\|(\s*:?-+:?\s*\|)+\s*$")
_NUMBER = re.compile(r"^[-+−]?[\d,]*\.?\d+(e[-+]?\d+)?%?$", re.IGNORECASE)

#: 日付だけを見せる列（2026-10-27 の人間の指示「選定区間・検証区間・実行区間は日付までで時刻は無くて
#: 良い」）。
DATE_ONLY_COLUMNS = frozenset({"選定区間", "検証区間", "実行区間"})
#: 区間の文字（`common.time.Interval` の `[開始, 終わり)`）。
_INTERVAL = re.compile(r"^\[(\d{4}-\d{2}-\d{2})T[^,\]]*,\s*(\d{4}-\d{2}-\d{2})T[^)\]]*\)$")


def date_only(text: str) -> str:
    """区間の文字を日付だけにする（`[2017-03-25T12:00:00Z, 2019-03-23T12:00:00Z)` →
    `2017-03-25 〜 2019-03-23`）。区間の形でなければそのまま返す。"""
    match = _INTERVAL.match(text.strip())
    return f"{match.group(1)} 〜 {match.group(2)}" if match else text


@dataclass(frozen=True, slots=True)
class Section:
    """`## ` で始まる 1 つの節。`title` は見出しの文字（逃がす前）。"""

    title: str
    html: str


@dataclass(slots=True)
class Document:
    """変換の結果。`title` は先頭の `# ` の見出し（無ければ空）。"""

    title: str = ""
    preamble: str = ""
    sections: list[Section] = field(default_factory=list)


def inline(text: str) -> str:
    """行内の記法を HTML にする。コードの中は太字を解釈しない。"""
    parts = text.split("`")
    out: list[str] = []
    for index, part in enumerate(parts):
        if index % 2 == 1 and index < len(parts) - 1:
            out.append(f"<code>{html.escape(part)}</code>")
            continue
        # 閉じない `` ` `` は文字として戻す。
        piece = html.escape(part if index % 2 == 0 else "`" + part)
        out.append(re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", piece))
    return "".join(out)


def split_cells(line: str) -> list[str]:
    """表の 1 行をセルに分ける。`\\|` は縦棒、`\\\\` は逆斜線の 1 文字に戻す。"""
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    cells: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\" and index + 1 < len(body) and body[index + 1] in "|\\":
            current.append(body[index + 1])
            index += 2
            continue
        if char == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    cells.append("".join(current).strip())
    return cells


def _cell_html(tag: str, text: str) -> str:
    numeric = tag == "td" and _NUMBER.match(text.replace(" ", "")) is not None
    attr = ' class="num"' if numeric else ""
    return f"<{tag}{attr}>{inline(text)}</{tag}>"


def _table(lines: list[str]) -> str:
    header = split_cells(lines[0])
    rows = [split_cells(line) for line in lines[2:]]
    date_columns = {index for index, name in enumerate(header) if name in DATE_ONLY_COLUMNS}
    rows = [
        [date_only(cell) if index in date_columns else cell for index, cell in enumerate(row)]
        for row in rows
    ]
    out = ['<div class="table-wrap"><table>', "<thead><tr>"]
    out.extend(_cell_html("th", cell) for cell in header)
    out.append("</tr></thead><tbody>")
    for row in rows:
        out.append("<tr>" + "".join(_cell_html("td", cell) for cell in row) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def _list(items: list[tuple[int, str]]) -> str:
    """(入れ子の深さ, 文字) の並びを入れ子の `<ul>` にする。"""
    out: list[str] = []
    depth = -1
    for level, text in items:
        # 深さは 1 段ずつしか深くしない（飛んだ字下げは 1 段下として読む）。
        level = min(level, depth + 1)
        if level > depth:
            out.append("<ul>")
            depth = level
        else:
            while depth > level:
                out.append("</li></ul>")
                depth -= 1
            out.append("</li>")
        out.append(f"<li>{inline(text)}")
    while depth >= 0:
        out.append("</li></ul>")
        depth -= 1
    return "".join(out)


def _blocks(lines: list[str]) -> str:
    """節の中身（見出しの 2 段目を含まない行）を HTML にする。"""
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        heading = _HEADING.match(line)
        if heading:
            level = min(len(heading.group(1)) + 1, 6)
            out.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
            index += 1
            continue
        if (
            line.startswith("|")
            and index + 1 < len(lines)
            and _TABLE_SEPARATOR.match(lines[index + 1])
        ):
            end = index + 2
            while end < len(lines) and lines[end].startswith("|"):
                end += 1
            out.append(_table(lines[index:end]))
            index = end
            continue
        if _LIST_ITEM.match(line):
            items: list[tuple[int, str]] = []
            while index < len(lines):
                item = _LIST_ITEM.match(lines[index])
                if item:
                    items.append((len(item.group(1)) // 2, item.group(2)))
                elif lines[index].startswith(" ") and lines[index].strip() and items:
                    # 箇条の続きの行（字下げだけの行）は直前の箇条に足す。
                    level, text = items[-1]
                    items[-1] = (level, text + " " + lines[index].strip())
                else:
                    break
                index += 1
            out.append(_list(items))
            continue
        paragraph: list[str] = []
        while index < len(lines):
            current = lines[index]
            if (
                not current.strip()
                or _HEADING.match(current)
                or _LIST_ITEM.match(current)
                or current.startswith("|")
            ):
                break
            paragraph.append(current.strip())
            index += 1
        out.append("<p>" + "<br>".join(inline(text) for text in paragraph) + "</p>")
    return "\n".join(out)


def convert(markdown: str) -> Document:
    """Markdown の全体を、先頭の見出し・前置き・節の並びに分けて HTML にする。"""
    document = Document()
    current_title: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        if current_title is None:
            document.preamble = _blocks(buffer)
        else:
            document.sections.append(Section(current_title, _blocks(buffer)))

    for line in markdown.splitlines():
        if line.startswith("# ") and not document.title and current_title is None:
            document.title = line[2:].strip()
            continue
        if line.startswith("## "):
            flush()
            current_title = line[3:].strip()
            buffer = []
            continue
        buffer.append(line)
    flush()
    return document
