"""Đọc file DANH SÁCH BÀI (tạo Mix Set / Track lẻ) → mỗi bài 1 dòng «Tên bài - Ca sĩ».

Nhận: .txt, .csv, .docx, .doc (Word cũ — đọc theo kiểu «gắng hết sức»),
.xlsx / .xlsm (Excel), .xls (Excel cũ — cần thư viện xlrd, không có thì báo
lưu lại thành .xlsx). Không cần cài thêm thư viện cho .xlsx: file Excel mới
là file zip chứa XML, đọc thẳng bằng thư viện chuẩn.

Excel: mỗi HÀNG là 1 bài. Hàng có ≥ 2 ô chữ → «ô 1 - ô 2» (vd cột Tên bài |
cột Ca sĩ); hàng có 1 ô → giữ nguyên ô đó (đã viết sẵn «Tên bài - Ca sĩ»).
Bỏ cột số thứ tự (STT) và hàng tiêu đề (Tên bài / Ca sĩ / Title / Artist…).
"""
from __future__ import annotations

import csv
import io
import posixpath
import re
import zipfile
from xml.etree import ElementTree

MAX_BYTES = 5 * 1024 * 1024
ACCEPTED = (".txt", ".csv", ".docx", ".doc", ".xlsx", ".xlsm", ".xls")
_ACCEPT_TEXT = "Chỉ nhận file .txt, .docx, .doc, .xlsx, .xls hoặc .csv."

_HEADER_WORDS = {
    "stt", "tt", "no", "no.", "#", "tên bài", "ten bai", "tên bài hát", "bài hát",
    "bai hat", "ca sĩ", "ca si", "nghệ sĩ", "tác giả", "title", "song", "song name",
    "artist", "singer", "tên", "name",
}
_NUM = re.compile(r"^\d+(?:[.,]0+)?$")


def _decode_text(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1258"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError("Không đọc được file chữ; hãy lưu file ở dạng UTF-8.")


# ---------------- Word ----------------
def _docx_lines(payload: bytes) -> list[str]:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > 8 * 1024 * 1024:
                raise ValueError("Nội dung DOCX quá lớn.")
            xml = archive.read(info)
        root = ElementTree.fromstring(xml)
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise ValueError("File DOCX không hợp lệ hoặc đã hỏng.") from exc
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    raw: list[str] = []
    for paragraph in root.iter(ns + "p"):
        part = ""
        for node in paragraph.iter():
            if node.tag == ns + "t":
                part += node.text or ""
            elif node.tag in (ns + "br", ns + "cr"):
                raw.append(part)
                part = ""
        raw.append(part)
    return raw


_DOC_ADVICE = ("Không đọc được file Word cũ (.doc). Mở file bằng Word → Tệp → Lưu thành "
               "(Save As) → chọn «Word Document (.docx)» rồi chọn lại file .docx.")


def _doc_lines(payload: bytes) -> list[str]:
    """Word 97-2003: chữ nằm trong luồng nhị phân, dạng UTF-16 (có dấu tiếng Việt)
    hoặc 1 byte/ký tự. Lấy các đoạn chữ đọc được; chỉ những dòng có dạng
    «A - B» mới được dùng (lọc rác định dạng)."""
    if not payload.startswith(b"\xd0\xcf\x11\xe0"):
        # Một số file .doc thật ra là .docx / RTF / chữ thường bị đổi đuôi.
        if payload.startswith(b"PK"):
            return _docx_lines(payload)
        if payload.lstrip().startswith(b"{\\rtf"):
            raise ValueError(_DOC_ADVICE)
        return _decode_text(payload).splitlines()
    found: list[str] = []
    # UTF-16LE: chuỗi ký tự in được (kể cả chữ Việt) dài ≥ 3 ký tự.
    # Byte cao 00 = Latin cơ bản, 01 = ă đ ơ ư…, 03 = dấu tổ hợp, 1E = ạ ả ấ… ỹ.
    for m in re.finditer(rb"(?:[\x20-\x7e\xa0-\xff]\x00|[\x00-\xff][\x01\x1e]"
                         rb"|[\x00-\x6f]\x03|\r\x00){3,}", payload):
        try:
            found.extend(m.group().decode("utf-16-le").split("\r"))
        except UnicodeDecodeError:
            continue
    if not any(re.search(r"\s[-–—]\s", x) for x in found):
        # Word lưu chữ 1 byte (cp1252) khi tài liệu toàn chữ không dấu.
        for m in re.finditer(rb"[\x20-\x7e\r]{6,}", payload):
            found.extend(m.group().decode("cp1252").split("\r"))
    lines = [x for x in found if re.search(r"\S\s[-–—]\s\S", x)]
    if not lines:
        raise ValueError(_DOC_ADVICE)
    return lines


# ---------------- Excel ----------------
_X = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _col_index(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]*", ref.upper()).group():
        n = n * 26 + (ord(ch) - 64)
    return n


def _first_sheet_path(archive: zipfile.ZipFile) -> str:
    try:
        wb = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        first = next(wb.iter(_X + "sheet"))
        rid = first.get(_R + "id")
        for rel in rels.iter(_PKG + "Relationship"):
            if rel.get("Id") == rid:
                target = rel.get("Target", "")
                if target.startswith("/"):
                    return target.lstrip("/")
                return posixpath.normpath(posixpath.join("xl", target))
    except (KeyError, StopIteration, ElementTree.ParseError):
        pass
    return "xl/worksheets/sheet1.xml"


def _xlsx_rows(payload: bytes) -> list[list[str]]:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                for si in root.iter(_X + "si"):
                    shared.append("".join(t.text or "" for t in si.iter(_X + "t")))
            path = _first_sheet_path(archive)
            info = archive.getinfo(path)
            if info.file_size > 20 * 1024 * 1024:
                raise ValueError("Trang tính Excel quá lớn.")
            sheet = ElementTree.fromstring(archive.read(info))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise ValueError("File Excel không hợp lệ hoặc đã hỏng (hãy lưu lại dạng .xlsx).") from exc
    rows: list[list[str]] = []
    for row in sheet.iter(_X + "row"):
        cells: dict[int, str] = {}
        for i, c in enumerate(row.iter(_X + "c")):
            kind = c.get("t", "")
            if kind == "inlineStr":
                value = "".join(t.text or "" for t in c.iter(_X + "t"))
            else:
                v = c.find(_X + "v")
                value = v.text if v is not None and v.text else ""
                if kind == "s" and value:
                    try:
                        value = shared[int(value)]
                    except (ValueError, IndexError):
                        value = ""
            col = _col_index(c.get("r", "")) or (i + 1)
            cells[col] = value
        rows.append([cells[k] for k in sorted(cells)])
    return rows


def _xls_rows(payload: bytes) -> list[list[str]]:
    if payload.startswith(b"PK"):          # .xlsx bị đổi đuôi thành .xls
        return _xlsx_rows(payload)
    try:
        import xlrd  # type: ignore
    except ImportError as exc:
        raise ValueError("File Excel cũ (.xls): mở bằng Excel → Tệp → Lưu thành (Save As) "
                         "→ chọn «Excel Workbook (.xlsx)» rồi chọn lại file .xlsx.") from exc
    try:
        book = xlrd.open_workbook(file_contents=payload)
        sheet = book.sheet_by_index(0)
    except Exception as exc:
        raise ValueError("File .xls không hợp lệ hoặc đã hỏng.") from exc
    return [[str(v) for v in sheet.row_values(r)] for r in range(sheet.nrows)]


def _csv_rows(payload: bytes) -> list[list[str]]:
    text = _decode_text(payload)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [list(r) for r in csv.reader(io.StringIO(text), dialect)]


def _is_header(cells: list[str]) -> bool:
    words = [" ".join(c.strip().lower().split()) for c in cells if c.strip()]
    return bool(words) and all(w in _HEADER_WORDS for w in words)


def rows_to_lines(rows: list[list[str]]) -> list[str]:
    """Mỗi hàng bảng → 1 dòng «Tên bài - Ca sĩ»."""
    out: list[str] = []
    for row in rows:
        cells = [" ".join(str(c).split()) for c in row]
        cells = [c for c in cells if c]
        if not cells or _is_header(cells):
            continue
        if len(cells) >= 2 and _NUM.match(cells[0]):     # bỏ cột STT
            cells = cells[1:]
        if len(cells) == 1:
            out.append(cells[0])
        elif re.search(r"\s[-–—]\s", cells[0]):
            out.append(cells[0])                        # ô đầu đã đủ «Tên - Ca sĩ»
        else:
            out.append(f"{cells[0]} - {cells[1]}")
    return out


def read_song_lines(filename: str, payload: bytes) -> list[str]:
    """Đọc file danh sách → các dòng bài hát (đã bỏ gạch đầu dòng / số thứ tự)."""
    if len(payload) > MAX_BYTES:
        raise ValueError("File danh sách vượt quá 5 MB.")
    suffix = "." + (filename or "").lower().rsplit(".", 1)[-1]
    if suffix == ".txt":
        raw = _decode_text(payload).splitlines()
    elif suffix == ".docx":
        raw = _docx_lines(payload)
    elif suffix == ".doc":
        raw = _doc_lines(payload)
    elif suffix in (".xlsx", ".xlsm"):
        raw = rows_to_lines(_xlsx_rows(payload))
    elif suffix == ".xls":
        raw = rows_to_lines(_xls_rows(payload))
    elif suffix == ".csv":
        raw = rows_to_lines(_csv_rows(payload))
    else:
        raise ValueError(_ACCEPT_TEXT)
    lines = []
    for value in raw:
        value = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", value or "").strip()
        value = " ".join(value.split())
        if value:
            lines.append(value)
    if not lines:
        raise ValueError("File chưa có dòng bài hát nào.")
    return lines
