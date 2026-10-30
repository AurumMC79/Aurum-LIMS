"""Importing instrument CSV / TXT exports through import templates."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from aurum_lims.db.models import AuditEntry, ImportedFile, Sample
from aurum_lims.errors import NotFound, PermissionDenied, ValidationError
from aurum_lims.services import instrument_import as ii
from aurum_lims.services import methods, testing
from aurum_lims.services.audit import AuditAction
from aurum_lims.services.instrument_import import RuleSpec as R
from aurum_lims.services.instrument_import import TemplateSpec as T
from aurum_lims.services.methods import ParameterSpec as P
from tests.conftest import USER_PASSWORD
from tests.test_testing import ASSAY_PARAMS, ASSAY_VALUES, setup  # noqa: F401 - fixture

CDS_EXPORT = """Report Method,Assay Summary
Project,QC 2026

Sample Name,Injection,Area,Retention Time
STD-A,1,10115,4.52
STD-A,2,10125,4.51
{sid},1,10234,4.52
{sid},2,10101,4.53
Mean,,10143.75,
"""

PARAMS = methods.validate_parameters(ASSAY_PARAMS)
CDS_TEMPLATE = T("Empower assay", "HPLC-ASSAY", header_row=4, rules=[
    R("area_std", "Area", "Sample Name", "STD-A", "mean"),
    R("area_spl", "area", "sample name", "{sample_id}", "replicates"),
])


def _read(template, content: str, sample_id="AU-1", params=PARAMS, encoding="utf-8"):
    return ii.read_file(template, content.format(sid=sample_id).encode(encoding), params, sample_id)


# ----- reading files -----------------------------------------------------------

def test_chromatography_export_with_metadata_lines():
    table, result = _read(CDS_TEMPLATE, CDS_EXPORT)
    assert table.headers == ["Sample Name", "Injection", "Area", "Retention Time"] and table.delimiter == ","
    assert result.values == {("area_std", 1): "10120", ("area_spl", 1): "10234", ("area_spl", 2): "10101"}
    assert result.errors == []
    assert result.warnings == []
    assert result.notes == ["Standard peak area (mean): average of 2 rows: 10115, 10125."]


def test_balance_export_with_tabs_and_decimal_commas():
    content = "Datum\tProbe\tNetto [mg]\n29.10.2026\tAU-1 E1\t25,30\n29.10.2026\tAU-1 E2\t1.025,05\n"
    template = T("Balance", "HPLC-ASSAY", decimal_comma=True, rules=[R("wt_spl", "Netto [mg]", "Probe", "{sample_id}")])
    table, result = _read(template, content)
    assert table.delimiter == "\t"
    assert result.values == {("wt_spl", 1): "25.30", ("wt_spl", 2): "1025.05"}


def test_semicolon_utf16_file_without_headings():
    content = "AU-1;7.02\nAU-2;6.98\n"
    params = methods.validate_parameters([P("ph", "pH", "result", decimals=2)])
    template = T("pH meter", "PH", header_row=0, rules=[R("ph", "Column 2", "Column 1", "{sample_id}", "first")])
    table, result = _read(template, content, params=params, encoding="utf-16")
    assert table.headers == ["Column 1", "Column 2"] and table.delimiter == ";"
    assert result.values == {("ph", 1): "7.02"} and result.warnings == []


def test_count_mismatches_are_warnings():
    _, result = _read(T("x", "HPLC-ASSAY", header_row=4, rules=[R("area_spl", "Area", "Sample Name", "STD")]),
                      CDS_EXPORT)
    assert result.values == {("area_spl", 1): "10115", ("area_spl", 2): "10125"}
    _, result = _read(T("x", "HPLC-ASSAY", header_row=4, rules=[R("area_spl", "Area", "Sample Name", "{sample_id}")]),
                      CDS_EXPORT.replace("{sid},2,10101,4.53\n", ""))
    assert result.values == {("area_spl", 1): "10234"}
    assert "only 1 row(s) matched; the method has 2 replicate(s)" in result.warnings[0]


def test_problems_are_reported_per_parameter():
    template = T("x", "HPLC-ASSAY", header_row=4, rules=[
        R("area_std", "Peak Area", "Sample Name", "STD-A", "mean"),   # wrong column
        R("area_spl", "Area"),                                        # every row, including "Mean" -> fine
        R("wt_std", "Sample Name", "Sample Name", "STD-A", "first"),  # not a number
        R("wt_spl", "Area", "Sample Name", "nothing-like-this"),      # no rows
        R("assay", "Area"),                                           # calculated
    ])
    _, result = _read(template, CDS_EXPORT)
    errors = "\n".join(result.errors)
    assert "column 'Peak Area' is not in the file" in errors
    assert "not a number: 'STD-A'" in errors
    assert "no matching rows" in errors
    assert "not an entered parameter" in errors
    assert set(result.values) == {("area_spl", 1), ("area_spl", 2)}


def test_text_and_pass_fail_results():
    params = methods.validate_parameters([P("app", "Appearance", "result", result_type="text"),
                                          P("id", "Identification", "result", result_type="pass_fail")])
    content = "Sample,Appearance,ID\nAU-1,White round tablet,pass\n"
    template = T("x", "APP", rules=[R("app", "Appearance", "Sample", "{sample_id}", "first"),
                                    R("id", "ID", "Sample", "{sample_id}", "first")])
    assert _read(template, content, params=params)[1].values == {("app", 1): "White round tablet", ("id", 1): "Pass"}
    template.rules[0].take = "mean"
    assert "cannot be averaged" in _read(template, content, params=params)[1].errors[0]


# ----- templates -----------------------------------------------------------------

def test_template_validation_and_audit(db, setup, admin):  # noqa: F811
    with db.session() as s:
        for spec, message in [
            (T("", "HPLC-ASSAY", rules=[R("area_spl", "Area")]), "name"),
            (T("x", "NOPE", rules=[R("a", "b")]), "not found"),
            (T("x", "HPLC-ASSAY", rules=[]), "at least one rule"),
            (T("x", "HPLC-ASSAY", rules=[R("assay", "Area")]), "entered parameter"),
            (T("x", "HPLC-ASSAY", rules=[R("area_spl", "Area"), R("area_spl", "B")]), "already has a rule"),
            (T("x", "HPLC-ASSAY", rules=[R("area_spl", " ")]), "column"),
            (T("x", "HPLC-ASSAY", rules=[R("area_spl", "Area", "", "STD")]), "match text"),
        ]:
            with pytest.raises(ValidationError, match=message):
                ii.create_template(s, admin, spec)
        with pytest.raises(PermissionDenied):
            ii.create_template(s, setup["analyst"], CDS_TEMPLATE)
        template_id = ii.create_template(s, admin, T("Empower assay", "hplc-assay", header_row=4,
                                                     rules=CDS_TEMPLATE.rules)).id
    with db.session() as s:
        with pytest.raises(ValidationError, match="already has a template"):
            ii.create_template(s, admin, CDS_TEMPLATE)
        with pytest.raises(ValidationError, match="reason"):
            ii.update_template(s, admin, template_id, CDS_TEMPLATE, "")
        changed = T("Empower assay v2", "HPLC-ASSAY", header_row=4, rules=CDS_TEMPLATE.rules)
        ii.update_template(s, admin, template_id, changed, "New export layout")
        ii.set_active(s, admin, template_id, False, "Replaced")
    with db.session() as s:
        assert ii.templates_for(s, "HPLC-ASSAY") == []
        [row] = ii.templates_for(s, "HPLC-ASSAY", include_inactive=True)
        assert row.name == "Empower assay v2" and ii.TemplateSpec.from_row(row).rules == CDS_TEMPLATE.rules
        actions = [e.action for e in s.scalars(select(AuditEntry).where(AuditEntry.entity_type == "import_template"))]
        assert actions == ["IMPORT_TEMPLATE_CREATED", "IMPORT_TEMPLATE_UPDATED", "IMPORT_TEMPLATE_UPDATED"]


# ----- importing into a test -------------------------------------------------------

def test_import_into_a_test(db, setup, admin):  # noqa: F811
    with db.session() as s:
        template_id = ii.create_template(s, admin, CDS_TEMPLATE).id
        testing.set_specification(s, setup["analyst"], setup["sample"], setup["spec"])
        [test] = [a.id for a in testing.assign_tests(s, setup["analyst"], setup["sample"], [setup["assay"]])]
        sample_id = s.get(Sample, setup["sample"]).sample_id
    data = CDS_EXPORT.format(sid=sample_id).encode()
    with db.session() as s:
        with pytest.raises(PermissionDenied):
            ii.import_file(s, setup["qa"].__class__(**{**setup["qa"].__dict__, "role": "viewer"}), test,
                           template_id, "seq.csv", data)
        record, result = ii.import_file(s, setup["analyst"], test, template_id, "seq.csv", data)
        assert result.values[("area_spl", 2)] == "10101"
        file_id = record.id
    with db.session() as s:
        [row] = ii.imported_files_for(s, test)
        assert row.file_name == "seq.csv" and row.template_name == "Empower assay" and row.imported_by == "Jsmith"
        assert ii.get_file(s, setup["qa"], file_id) == ("seq.csv", data)
        entry = s.scalars(select(AuditEntry).where(AuditEntry.action == AuditAction.RESULTS_IMPORTED)).one()
        assert row.sha256 in entry.new_values and "area_spl[2]" in entry.new_values
        # Nothing is saved as a result until the analyst saves the form.
        assert testing.stored_values(s, test) == {}
    with db.session() as s, pytest.raises(DBAPIError, match="immutable"):
        s.execute(text("UPDATE imported_files SET file_name = 'x'"))
    with db.session() as s:  # a file that yields nothing is refused
        with pytest.raises(ValidationError, match="No values"):
            ii.import_file(s, setup["analyst"], test, template_id, "other.csv", b"x\nx\nx\na,b\n1,2\n")
    # The values can then be saved and signed as usual.
    with db.session() as s:
        testing.save_values(s, setup["analyst"], test, {**ASSAY_VALUES, **result.values})
    with db.session() as s:
        assert testing.complete_analysis(s, setup["analyst"], test, USER_PASSWORD).ok
    with db.session() as s:
        with pytest.raises(ValidationError, match="no longer be entered"):
            ii.import_file(s, setup["analyst"], test, template_id, "seq.csv", data)
        with pytest.raises(NotFound):
            ii.import_file(s, setup["analyst"], 999, template_id, "seq.csv", data)
        assert s.scalar(select(ImportedFile.size_bytes)) == len(data)


def test_template_must_match_the_test_method(db, setup, admin):  # noqa: F811
    with db.session() as s:
        template_id = ii.create_template(s, admin, T("App", "APP", rules=[R("appearance", "Result")])).id
        [test] = [a.id for a in testing.assign_tests(s, setup["analyst"], setup["sample"], [setup["assay"]])]
        with pytest.raises(ValidationError, match="does not belong"):
            ii.import_file(s, setup["analyst"], test, template_id, "a.csv", b"Result\nWhite\n")
