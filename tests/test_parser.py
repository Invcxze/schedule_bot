import io
import os
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from schedule_bot.config import ParserConfig
from schedule_bot.models import normalize_group
from schedule_bot.parser import ParseError, parse_workbook


def _sheet_xml(cells, merges=()):
    rows = {}
    for address, value in cells.items():
        row = int("".join(c for c in address if c.isdigit()))
        xml = (
            f'<c r="{address}" t="n"><v>{value}</v></c>'
            if isinstance(value, int)
            else f'<c r="{address}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
        )
        rows.setdefault(row, []).append(xml)
    sheet_xml = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    sheet_xml += (
        "<sheetData>"
        + "".join(f'<row r="{row}">{"".join(values)}</row>' for row, values in sorted(rows.items()))
        + "</sheetData>"
    )
    sheet_xml += (
        "<mergeCells>" + "".join(f'<mergeCell ref="{m}"/>' for m in merges) + "</mergeCells>"
    )
    sheet_xml += "</worksheet>"
    return sheet_xml


def xlsx(cells, merges=(), sheet="Main"):
    """Tiny OOXML test fixture, not a user workbook or an alternate authoring tool."""
    return xlsx_multi({sheet: (cells, merges)})


def xlsx_multi(sheets: dict[str, tuple[dict, tuple]]):
    """Same tiny OOXML fixture, but with several worksheets in one workbook."""
    buf = io.BytesIO()
    with ZipFile(buf, "w", ZIP_DEFLATED) as z:
        overrides = "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, len(sheets) + 1)
        )
        z.writestr(
            "[Content_Types].xml",
            f"""<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
          <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
          <Default Extension="xml" ContentType="application/xml"/>
          <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
          {overrides}
        </Types>""",
        )
        z.writestr(
            "_rels/.rels",
            """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
          <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
        </Relationships>""",
        )
        sheet_tags = "".join(
            f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>'
            for i, name in enumerate(sheets, start=1)
        )
        z.writestr(
            "xl/workbook.xml",
            f'''<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
          <sheets>{sheet_tags}</sheets></workbook>''',
        )
        rels = "".join(
            f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, len(sheets) + 1)
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            f"""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
          {rels}
        </Relationships>""",
        )
        for i, (cells, merges) in enumerate(sheets.values(), start=1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(cells, merges))
    return buf.getvalue()


def fixture_cells():
    return {
        "B2": "B26-CSE-01 (27)",
        "C2": "B26-CSE-02 (25)",
        "A3": "MONDAY",
        "A4": "09:00-10:30",
        "B4": "Math <lec>",
        "B5": "Teacher",
        "B6": 108,
        "A7": "10:40-12:10",
        "B7": "PE",
        "A10": "TUESDAY",
        "A11": "09:00-10:30",
        "B11": "Lab",
        "B12": "Tutor",
        "B13": "ONLINE (ONLY ON 01/09)",
    }


def test_horizontal_and_vertical_merges_and_empty_cells():
    data = xlsx(fixture_cells(), ["A4:A6", "B4:C4", "B5:C5", "B6:C6", "B7:C9", "A7:A9"])
    result = parse_workbook(data, ParserConfig(("Main",)))
    first, second = result["B26-CSE-01"], result["B26-CSE-02"]
    assert first.days["w:0"] == second.days["w:0"]
    assert first.days["w:0"][0].room == "108"
    assert first.days["w:0"][1].teacher == first.days["w:0"][1].room == ""
    assert second.days["w:1"] == ()  # Blank lab is not copied from neighbour.
    assert first.days["w:1"][0].room == "ONLINE (ONLY ON 01/09)"
    assert first.on_date(date(2026, 8, 31))[0].subject == "Math <lec>"
    assert first.on_date(date(2026, 9, 6)) == ()


def test_time_column_belongs_to_own_year_block():
    cells = fixture_cells() | {
        "E2": "M26-SE-01",
        "D3": "MONDAY",
        "D4": "09:20-10:50",
        "E4": "Masters",
        "E5": "Other",
        "E6": "300",
    }
    result = parse_workbook(xlsx(cells), ParserConfig(("Main",)))
    assert result["M26-SE-01"].days["w:0"][0].start == "09:20"


def test_dated_grid_and_period_limits():
    cells = fixture_cells() | {"A3": "31.08.2026", "A10": "2026-09-01"}
    result = parse_workbook(xlsx(cells), ParserConfig(("Main",), valid_until=date(2026, 9, 1)))
    schedule = result["B26-CSE-01"]
    assert len(schedule.on_date(date(2026, 8, 31))) == 2
    assert not schedule.on_date(date(2026, 9, 7))
    assert not schedule.in_period(date(2026, 9, 2))


@pytest.mark.parametrize("value", ["b26-cse-01", " B26 – CSE – 01 (27) ", "B26-CSE-01"])
def test_group_normalization(value):
    assert normalize_group(value) == "B26-CSE-01"


def test_duplicate_groups_are_rejected():
    with pytest.raises(ParseError, match="дважды"):
        parse_workbook(xlsx(fixture_cells() | {"C2": "B26-CSE-01"}), ParserConfig(("Main",)))


def test_group_on_two_sheets_is_merged_not_rejected():
    # A group split across sheets: core courses on "Main", an elective on "Extra".
    # A Monday lesson identical on both sheets must not be duplicated (dedup via
    # the same Lesson-set logic used within a single sheet).
    extra_cells = {
        "B2": "B26-CSE-01 (27)",
        "A3": "MONDAY",
        "A4": "09:00-10:30",
        "B4": "Math <lec>",
        "B5": "Teacher",
        "B6": 108,
        "A7": "WEDNESDAY",
        "A8": "14:00-15:30",
        "B8": "Elective",
        "B9": "Other Teacher",
        "B10": "201",
    }
    data = xlsx_multi({"Main": (fixture_cells(), ()), "Extra": (extra_cells, ())})
    result = parse_workbook(data, ParserConfig(("Main", "Extra")))
    merged = result["B26-CSE-01"]
    single_sheet = parse_workbook(xlsx(fixture_cells()), ParserConfig(("Main",)))["B26-CSE-01"]
    assert merged.sheets == ("Main", "Extra")
    # The identical Monday lesson repeated on "Extra" collapses instead of duplicating.
    assert merged.days["w:0"] == single_sheet.days["w:0"]
    assert merged.days["w:2"][0].subject == "Elective"
    # A group untouched by the second sheet keeps only its own sheet.
    assert result["B26-CSE-02"].sheets == ("Main",)


@pytest.mark.parametrize("patch", [{"A4": "25:00-26:30"}, {"A3": "MONDYA"}, {"A5": "10:00-11:30"}])
def test_broken_structure_is_not_cancellation(patch):
    with pytest.raises(ParseError):
        parse_workbook(xlsx(fixture_cells() | patch), ParserConfig(("Main",)))


def test_missing_sheet_and_html_response():
    with pytest.raises(ParseError, match="Не найден лист"):
        parse_workbook(xlsx(fixture_cells()), ParserConfig(("Missing",)))
    with pytest.raises(ParseError, match="XLSX"):
        parse_workbook(b"<html>Please log in</html>", ParserConfig(("Main",)))


def test_real_user_workbook():
    value = os.environ.get("SAMPLE_XLSX")
    if not value:
        pytest.skip("Set SAMPLE_XLSX for integration test against the supplied workbook")
    result = parse_workbook(Path(value).read_bytes(), ParserConfig(("1st block common",)))
    assert len(result) == 39
    assert sum(len(day) for s in result.values() for day in s.days.values()) == 625
    assert result["B26-CSE-02"].days["w:0"][0].subject == "Logic and Discrete Math (lec)"
    assert result["B26-CSE-01"].days["w:0"][0].room == "108"
    assert result["B25-CSE-01"].days["w:1"][0].teacher == ""


def test_real_user_workbook_both_sheets_merge_shared_group():
    value = os.environ.get("SAMPLE_XLSX")
    if not value:
        pytest.skip("Set SAMPLE_XLSX for integration test against the supplied workbook")
    result = parse_workbook(
        Path(value).read_bytes(), ParserConfig(("1st block common", "Ru Programs"))
    )
    # "Ru Programs" adds groups absent from "1st block common" (e.g. MFAI, AI360)
    # and B24-RO-01 is present on both sheets and must merge, not error out.
    assert len(result) == 59
    ro = result["B24-RO-01"]
    assert ro.sheets == ("1st block common", "Ru Programs")
    assert sum(len(day) for day in ro.days.values()) == 14
    assert result["B26-MFAI-01"].sheets == ("Ru Programs",)
