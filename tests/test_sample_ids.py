from datetime import date

import pytest

from aurum_lims.db.models import SampleType
from aurum_lims.errors import PermissionDenied, ValidationError
from aurum_lims.services import sample_ids, sample_types
from aurum_lims.services.sample_ids import ResetPolicy

D = date(2026, 10, 27)


def test_render_tokens():
    assert sample_ids.render("AU-{YYYY}-{SEQ:5}", 7, D) == "AU-2026-00007"
    assert sample_ids.render("{TYPE}{YY}{MM}{DD}-{SEQ}", 12, D, "RM") == "RM261027-0012"


@pytest.mark.parametrize(
    "pattern, reset, message",
    [
        ("AU-{YYYY}", "yearly", "exactly once"),
        ("{SEQ}-{SEQ}-{YYYY}", "yearly", "exactly once"),
        ("AU-{SEQ:5}", "yearly", "yearly restart"),
        ("{YYYY}-{SEQ}", "monthly", "monthly restart"),
        ("{YYYY}{MM}-{SEQ}", "daily", "daily restart"),
        ("{FOO}-{SEQ}", "never", "Unknown"),
        ("AU {SEQ}", "never", "Fixed text"),
        ("AU-{SEQ:12}", "never", "between 1 and 10"),
        ("AU-{YYYY:2}-{SEQ}", "yearly", "does not take a width"),
        ("AU-{SEQ", "never", "braces"),
    ],
)
def test_invalid_formats(pattern, reset, message):
    problems = sample_ids.format_problems(pattern, reset)
    assert any(message in p for p in problems), problems


def test_valid_formats():
    assert sample_ids.format_problems("AU-{YYYY}-{SEQ:5}", "yearly") == []
    assert sample_ids.format_problems("{SEQ:6}", "never") == []
    assert sample_ids.format_problems("{TYPE}-{YYYY}{MM}-{SEQ:3}", "monthly") == []


def _seed(db):
    with db.session() as s:
        sample_types.seed_defaults(s)
    with db.session() as s:
        return {t.code: t.id for t in sample_types.list_types(s)}


def test_lab_format_shared_counter(db):
    types = _seed(db)
    with db.session() as s:
        rm, fp = s.get(SampleType, types["RM"]), s.get(SampleType, types["FP"])
        assert sample_ids.next_sample_id(s, rm, D) == "AU-2026-00001"
        assert sample_ids.next_sample_id(s, fp, D) == "AU-2026-00002"
        assert sample_ids.next_sample_id(s, rm, date(2027, 1, 2)) == "AU-2027-00001"  # yearly restart


def test_type_token_counts_per_type(db, admin):
    types = _seed(db)
    with db.session() as s:
        sample_ids.set_lab_format(s, admin, "{TYPE}-{YY}-{SEQ:4}", ResetPolicy.YEARLY, "SOP-QC-010")
    with db.session() as s:
        rm, fp = s.get(SampleType, types["RM"]), s.get(SampleType, types["FP"])
        assert sample_ids.next_sample_id(s, rm, D) == "RM-26-0001"
        assert sample_ids.next_sample_id(s, rm, D) == "RM-26-0002"
        assert sample_ids.next_sample_id(s, fp, D) == "FP-26-0001"


def test_type_specific_format(db, admin):
    types = _seed(db)
    with db.session() as s:
        stb = s.get(SampleType, types["STB"])
        sample_types.update_type(
            s, admin, stb.id, reason="Stability numbering per SOP", name=stb.name, code=stb.code,
            category=stb.category, description=stb.description, id_format="STAB{SEQ:6}", id_reset="never",
            is_active=True,
        )
    with db.session() as s:
        assert sample_ids.next_sample_id(s, s.get(SampleType, types["STB"]), D) == "STAB000001"
        assert sample_ids.next_sample_id(s, s.get(SampleType, types["RM"]), D) == "AU-2026-00001"


def test_set_lab_format_rules(db, admin, make_user):
    analyst = make_user("jsmith")
    with db.session() as s:
        with pytest.raises(PermissionDenied):
            sample_ids.set_lab_format(s, analyst, "X-{SEQ}", "never", "test")
        with pytest.raises(ValidationError, match="reason"):
            sample_ids.set_lab_format(s, admin, "X-{SEQ}", "never", "")
        with pytest.raises(ValidationError, match="yearly"):
            sample_ids.set_lab_format(s, admin, "X-{SEQ}", "yearly", "test")
