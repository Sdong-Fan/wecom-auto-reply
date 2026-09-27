# tests/test_upload_tables.py
"""Excel / Word 上传解析。

真造 xlsx / docx 文件来读（openpyxl / python-docx 都能写），不 mock ——
这一层的坑全在"真实文件长什么样"上（合并单元格、日期格式、float 尾巴、空列）。

行级切块和同义词是**实测出来的**：整张表嵌一块时「索尼A7M4一天多少钱」只有
0.63（门槛 0.65）；改成一句话一行、并给「日租」补上「一天」，才稳定过线，
而且不会让「周租 600」抢到第一。
"""

import datetime

import pytest

from pipeline import upload as up


# ── 单元格整形 ────────────────────────────────────────────────────────

def test_cell_float_integer_drops_dot_zero():
    """120.0 喂给 LLM 会变成"一百二十点零"，必须整形。"""
    assert up._cell_str(120.0) == "120"
    assert up._cell_str(-3.0) == "-3"


def test_cell_float_keeps_decimals():
    assert up._cell_str(0.85) == "0.85"
    assert up._cell_str(1.5) == "1.5"


def test_cell_datetime_date_only():
    assert up._cell_str(datetime.datetime(2024, 5, 1)) == "2024-05-01"


def test_cell_datetime_with_time():
    assert up._cell_str(datetime.datetime(2024, 5, 1, 9, 30)) == "2024-05-01 09:30"


def test_cell_date_object():
    assert up._cell_str(datetime.date(2024, 5, 1)) == "2024-05-01"


def test_cell_bool_is_not_number():
    """bool 是 int 的子类，顺序写错会输出 1/0。"""
    assert up._cell_str(True) == "是"
    assert up._cell_str(False) == "否"


def test_cell_none_and_whitespace():
    assert up._cell_str(None) == ""
    assert up._cell_str("  索尼 ") == "索尼"


# ── 表头识别 / 行转句 / 同义词 ────────────────────────────────────────

def test_looks_like_header():
    assert up._looks_like_header(["器材", "日租", "押金"]) is True
    assert up._looks_like_header(["索尼A7M4", "120"]) is False      # 有数字
    assert up._looks_like_header(["这是一个特别长的说明文字", "二"]) is False
    assert up._looks_like_header(["只有一列"]) is False


def test_row_sentence_with_headers():
    got = up._row_sentence(["索尼A7M4", "120", "2000"], [0, 1, 2],
                           {0: "器材", 1: "日租", 2: "押金"})
    assert got == "索尼A7M4：日租（一天）120，押金 2000"


def test_row_sentence_without_headers():
    assert up._row_sentence(["索尼A7M4", "120"], [0, 1], None) == "索尼A7M4，120"


def test_row_sentence_single_cell():
    assert up._row_sentence(["闪光灯"], [0], {0: "器材"}) == "闪光灯"


def test_row_sentence_ragged_row_keeps_alignment():
    """"备注"为空的那一行少一列 —— 标签不能错位。"""
    headers = {0: "器材", 1: "日租", 2: "押金", 3: "备注"}
    got = up._row_sentence(["佳能R5", "150", "3000"], [0, 1, 2], headers)
    assert got == "佳能R5：日租（一天）150，押金 3000"


def test_synonym_for_rent_units():
    assert up._synonym("日租") == "一天"
    assert up._synonym("日租金") == "一天"          # 子串也算
    assert up._synonym("周租") == "一周"
    assert up._synonym("月租") == "一个月"
    assert up._synonym("押金") == ""               # 别的表头不乱加词
    assert up._synonym("器材") == ""


# ── Excel 2007+ ───────────────────────────────────────────────────────

def _make_xlsx(path, sheets):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    first = True
    for name, rows in sheets:
        ws = wb.active if first else wb.create_sheet()
        ws.title = name
        first = False
        for row in rows:
            ws.append(row)
    wb.save(path)
    return path


def test_xlsx_single_sheet_rows(tmp_path):
    f = _make_xlsx(tmp_path / "价目表.xlsx", [("价格", [
        ["器材", "日租", "押金"],
        ["索尼A7M4", 120, 2000],
        ["佳能R5", 150.5, 3000],
    ])])
    out = up.parse_file(f)
    lines = out.splitlines()
    assert lines[0] == "器材 | 日租 | 押金"
    assert lines[1] == "索尼A7M4 | 120 | 2000"      # 120 不是 120.0
    assert lines[2] == "佳能R5 | 150.5 | 3000"
    assert "【" not in out                            # 单表不加前缀


def test_xlsx_chunks_are_sentences(tmp_path):
    f = _make_xlsx(tmp_path / "价目表.xlsx", [("价格", [
        ["器材", "日租", "押金"],
        ["索尼A7M4", 120, 2000],
    ])])
    _text, chunks = up.parse_records(f)
    assert chunks == ["索尼A7M4：日租（一天）120，押金 2000"]


def test_xlsx_multi_sheet_gets_prefix(tmp_path):
    f = _make_xlsx(tmp_path / "总表.xlsx", [
        ("租金", [["器材", "日租"], ["索尼A7M4", 120]]),
        ("押金", [["器材", "押金"], ["索尼A7M4", 2000]]),
    ])
    out = up.parse_file(f)
    assert "【租金】" in out
    assert "【押金】" in out


def test_xlsx_weekly_and_daily_do_not_mix(tmp_path):
    """日租 120 / 周租 600 必须分得清，否则会把 600 当成一天的价格报出去。"""
    f = _make_xlsx(tmp_path / "表.xlsx", [
        ("日租", [["器材", "日租"], ["索尼A7M4", 120]]),
        ("周租", [["器材", "周租"], ["索尼A7M4", 600]]),
    ])
    _text, chunks = up.parse_records(f)
    assert chunks == ["【日租】索尼A7M4：日租（一天）120",
                      "【周租】索尼A7M4：周租（一周）600"]


def test_xlsx_skips_empty_rows_and_cells(tmp_path):
    f = _make_xlsx(tmp_path / "空行.xlsx", [("S", [
        ["索尼", None, "120"],
        [None, None, None],
        ["", "", ""],
    ])])
    assert up.parse_file(f).splitlines() == ["索尼 | 120"]


def test_xlsx_empty_sheet_skipped(tmp_path):
    f = _make_xlsx(tmp_path / "空表.xlsx", [("空的", []), ("有货", [["器材", "日租"], ["灯架", 10]])])
    out = up.parse_file(f)
    assert "【空的】" not in out
    assert "灯架 | 10" in out


def test_xlsx_date_cell(tmp_path):
    f = _make_xlsx(tmp_path / "日期.xlsx", [("S", [
        ["项目", "日期"],
        ["年检", datetime.datetime(2024, 5, 1)],
    ])])
    assert "年检 | 2024-05-01" in up.parse_file(f)


def test_xlsx_bool_cell(tmp_path):
    f = _make_xlsx(tmp_path / "布尔.xlsx", [("S", [["含电池", True]])])
    assert up.parse_file(f).strip() == "含电池 | 是"


def test_headerless_table_still_gets_chunks(tmp_path):
    """第一行就是数据（没有表头）时不能把数据行当表头吞掉。"""
    f = _make_xlsx(tmp_path / "无表头.xlsx", [("S", [
        ["索尼A7M4", 120], ["佳能R5", 150]])])
    _text, chunks = up.parse_records(f)
    assert chunks == ["索尼A7M4，120", "佳能R5，150"]


def test_corrupt_xlsx_raises(tmp_path):
    f = tmp_path / "坏.xlsx"
    f.write_bytes(b"this is not a zip")
    with pytest.raises(Exception):
        up.parse_file(f)


# ── Excel 2003 (.xls) ─────────────────────────────────────────────────

def test_xls_rows(tmp_path):
    xlwt = pytest.importorskip("xlwt")
    f = tmp_path / "老表.xls"
    wb = xlwt.Workbook()
    ws = wb.add_sheet("价格")
    for r, row in enumerate([["器材", "日租"], ["索尼A7M4", 120]]):
        for c, v in enumerate(row):
            ws.write(r, c, v)
    wb.save(str(f))

    out = up.parse_file(f)
    assert "器材 | 日租" in out
    assert "索尼A7M4 | 120" in out
    assert up.parse_records(f)[1] == ["索尼A7M4：日租（一天）120"]


def test_xls_date_cell(tmp_path):
    xlwt = pytest.importorskip("xlwt")
    f = tmp_path / "老日期.xls"
    wb = xlwt.Workbook()
    ws = wb.add_sheet("S")
    style = xlwt.XFStyle()
    style.num_format_str = "YYYY-MM-DD"
    ws.write(0, 0, "项目")
    ws.write(0, 1, "日期")
    ws.write(1, 0, "年检")
    ws.write(1, 1, datetime.datetime(2024, 5, 1), style)
    wb.save(str(f))
    assert "年检 | 2024-05-01" in up.parse_file(f)


# ── Word (.docx) ──────────────────────────────────────────────────────

def _make_docx(path, paragraphs, tables=()):
    docx = pytest.importorskip("docx")
    d = docx.Document()
    for p in paragraphs:
        d.add_paragraph(p)
    for rows in tables:
        t = d.add_table(rows=len(rows), cols=len(rows[0]))
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                t.cell(r, c).text = str(v)
    d.save(str(path))
    return path


def test_docx_paragraphs(tmp_path):
    f = _make_docx(tmp_path / "须知.docx", ["租期最少一天", "押金当天退回"])
    out = up.parse_file(f)
    assert "租期最少一天" in out
    assert "押金当天退回" in out


def test_docx_table_is_read(tmp_path):
    """价目表基本都在 Word 表格里，只读段落等于什么都没读到。"""
    f = _make_docx(tmp_path / "价目.docx", ["价目如下"],
                   tables=[[["器材", "日租"], ["索尼A7M4", "120"]]])
    out = up.parse_file(f)
    assert "价目如下" in out
    assert "器材 | 日租" in out
    assert "索尼A7M4 | 120" in out
    assert "【表格】" in out


def test_docx_multi_tables_numbered(tmp_path):
    f = _make_docx(tmp_path / "两表.docx", [],
                   tables=[[["甲", "1"]], [["乙", "2"]]])
    out = up.parse_file(f)
    assert "【表格1】" in out
    assert "【表格2】" in out


def test_docx_blank_paragraphs_skipped(tmp_path):
    f = _make_docx(tmp_path / "空段.docx", ["", "  ", "有内容"])
    assert up.parse_file(f).splitlines() == ["有内容"]


def test_corrupt_docx_raises(tmp_path):
    f = tmp_path / "坏.docx"
    f.write_bytes(b"not a docx")
    with pytest.raises(Exception):
        up.parse_file(f)


# ── 老格式：明确报错，不读乱码 ────────────────────────────────────────

def test_doc_gives_actionable_error(tmp_path):
    f = tmp_path / "老文档.doc"
    f.write_bytes(b"\xd0\xcf\x11\xe0")      # OLE 头
    with pytest.raises(ValueError, match="另存为"):
        up.parse_file(f)


def test_wps_gives_actionable_error(tmp_path):
    f = tmp_path / "老表.wps"
    f.write_bytes(b"\xd0\xcf\x11\xe0")
    with pytest.raises(ValueError, match="另存为"):
        up.parse_file(f)


# ── 上限保护 ──────────────────────────────────────────────────────────

def test_too_many_rows_rejected_loudly(tmp_path, monkeypatch):
    """静默只导一半 = 半份价目表，比没有更危险。"""
    monkeypatch.setattr(up, "MAX_TABLE_ROWS", 3)
    f = _make_xlsx(tmp_path / "大表.xlsx",
                   [("S", [["名称", "值"]] + [[f"器材{i}", i] for i in range(10)])])
    with pytest.raises(ValueError, match="表格太大"):
        up.parse_file(f)


def test_csv_also_guarded(tmp_path, monkeypatch):
    monkeypatch.setattr(up, "MAX_TABLE_ROWS", 2)
    f = tmp_path / "大.csv"
    f.write_text("a,1\nb,2\nc,3\nd,4\n", encoding="utf-8")   # 表头 + 3 行数据
    with pytest.raises(ValueError, match="表格太大"):
        up.parse_file(f)


# ── 全链路：xlsx 走一遍导入（嵌入用桩） ──────────────────────────────

def test_xlsx_import_end_to_end(tmp_path, monkeypatch):
    import sys
    import types

    calls = []
    mod = types.ModuleType("pipeline.embedder")
    mod.embed_and_store = lambda chunks, qdrant, collection_name="", source="": (
        calls.append((list(chunks), source)) or len(chunks))
    monkeypatch.setitem(sys.modules, "pipeline.embedder", mod)
    monkeypatch.setattr(up, "UPLOAD_DIR", tmp_path / "uploaded")

    f = _make_xlsx(tmp_path / "价目表.xlsx", [("价格", [
        ["器材", "日租"], ["索尼A7M4", 120]])])
    res = up.import_files([f], qdrant="Q")

    assert res["failed"] == []
    assert res["ok"][0]["source"] == "价目表.xlsx"      # 源留的是原文件，后缀还在
    assert calls[0][0] == ["索尼A7M4：日租（一天）120"]
    # 源留了一份，而且是**原始 xlsx 字节**（重建索引靠它，存文本会丢掉表格结构）
    saved = tmp_path / "uploaded" / "价目表.xlsx"
    assert saved.is_file()
    assert up.parse_records(saved)[1] == calls[0][0]


# ── 界面筛选器 ────────────────────────────────────────────────────────

def test_dialog_filter_lists_excel_and_word():
    from pathlib import Path
    s = (Path(__file__).resolve().parent.parent / "gui" / "kb_dialog.py").read_text(
        encoding="utf-8")
    assert "*.xlsx" in s and "*.docx" in s
    assert "Excel" in s
