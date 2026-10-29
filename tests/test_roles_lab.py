"""Manager, Supervisor, Lead Analyst and Chemical Hygiene Officer roles."""

from datetime import date

import pytest

from aurum_lims.db.models import Analysis
from aurum_lims.errors import PermissionDenied, ValidationError
from aurum_lims.security.roles import Role
from aurum_lims.services import inventory, oos, review, samples, testing
from aurum_lims.services.inventory import ItemDetails
from aurum_lims.services.samples import SampleDetails
from aurum_lims.services.testing import AnalysisStatus
from aurum_lims.ui.main_window import MainWindow
from tests.conftest import USER_PASSWORD
from tests.test_review import LOW_ASSAY, signed  # noqa: F401 - fixture
from tests.test_testing import setup  # noqa: F401 - fixture


@pytest.mark.parametrize("role", [Role.SUPERVISOR, Role.LEAD_ANALYST])
def test_supervisor_and_lead_analyst_peer_review(db, signed, make_user, role):
    reviewer = make_user(f"reviewer.{role.value}", role)
    with db.session() as s:
        assert {t.id for t in review.tests_awaiting_review(s, reviewer)} == {signed["assay_test"], signed["app_test"]}
        assert review.accept_test(s, reviewer, signed["assay_test"], USER_PASSWORD).ok
    with db.session() as s:
        review.return_test(s, reviewer, signed["app_test"], "Appearance wording does not match the SOP")
    with db.session() as s:
        assert s.get(Analysis, signed["assay_test"]).status == AnalysisStatus.REVIEWED
        assert s.get(Analysis, signed["app_test"]).status == AnalysisStatus.RETURNED


@pytest.mark.parametrize("role", [Role.SUPERVISOR, Role.LEAD_ANALYST])
def test_reviewers_cannot_review_own_work_or_decide(db, signed, make_user, role):
    reviewer = make_user(f"reviewer.{role.value}", role)
    # The reviewer runs a test of their own: they cannot be its second person.
    with db.session() as s:
        own = testing.assign_tests(s, reviewer, signed["sample"], [signed["appearance"]])[0].id
        testing.save_values(s, reviewer, own, {("appearance", 1): "White round tablet"})
    with db.session() as s:
        testing.complete_analysis(s, reviewer, own, USER_PASSWORD)
    with db.session() as s:
        with pytest.raises(ValidationError, match="second person"):
            review.accept_test(s, reviewer, own, USER_PASSWORD)
    # The final sample decision stays with QA.
    for analysis_id in (signed["assay_test"], signed["app_test"], own):
        with db.session() as s:
            review.accept_test(s, signed["qa"], analysis_id, USER_PASSWORD)
    with db.session() as s:
        with pytest.raises(PermissionDenied):
            review.decide_sample(s, reviewer, signed["sample"], approve=True, password=USER_PASSWORD)


def test_supervisor_cannot_close_oos(db, setup, make_user):  # noqa: F811
    supervisor = make_user("supervisor1", Role.SUPERVISOR)
    with db.session() as s:
        testing.set_specification(s, setup["analyst"], setup["sample"], setup["spec"])
        [assay] = [a.id for a in testing.assign_tests(s, setup["analyst"], setup["sample"], [setup["assay"]])]
        testing.save_values(s, setup["analyst"], assay, LOW_ASSAY)
    with db.session() as s:
        testing.complete_analysis(s, setup["analyst"], assay, USER_PASSWORD, {"assay": "Low"})
    with db.session() as s:
        [inv] = oos.list_investigations(s, supervisor)
        oos.update(s, supervisor, inv.id, phase=oos.PHASES[0], investigation_notes="Checked the standard",
                   root_cause=None)  # supervisors can record the investigation...
    with db.session() as s:
        with pytest.raises(PermissionDenied):  # ...but only QA closes it
            oos.close(s, supervisor, inv.id, conclusion=oos.CONCLUSIONS[0], root_cause="x", password=USER_PASSWORD)


def test_manager_sees_queues_but_cannot_act(db, signed, make_user):
    manager = make_user("manager1", Role.MANAGER)
    with db.session() as s:
        assert len(review.tests_awaiting_review(s, manager)) == 2
        review.samples_awaiting_decision(s, manager)
        with pytest.raises(PermissionDenied):
            review.accept_test(s, manager, signed["assay_test"], USER_PASSWORD)
        with pytest.raises(PermissionDenied):
            review.return_test(s, manager, signed["assay_test"], "x")
        with pytest.raises(PermissionDenied):
            samples.register_samples(s, manager, 1, SampleDetails(material_name="x", lot_number="1"))
        with pytest.raises(PermissionDenied):
            testing.save_values(s, manager, signed["assay_test"], {})


def test_chemical_hygiene_officer_manages_inventory_only(db, setup, make_user):  # noqa: F811
    cho = make_user("cho1", Role.CHEMICAL_HYGIENE_OFFICER)
    with db.session() as s:
        item = inventory.create_item(s, cho, ItemDetails(name="Hydrochloric acid 37%", kind="Reagent", unit="mL",
                                                         min_stock="500", days_after_opening=365))
        lot = inventory.receive_lot(s, cho, item.id, lot_number="HCL-2210", quantity="2500",
                                    expiry_date=date(2028, 1, 1))
        lot_id = lot.id
    with db.session() as s:
        inventory.open_lot(s, cho, lot_id)
        inventory.adjust_stock(s, cho, lot_id, "2400", "Monthly stock check")
        inventory.discard_lot(s, cho, lot_id, "Container cracked")
    with db.session() as s:
        with pytest.raises(PermissionDenied):
            samples.register_samples(s, cho, 1, SampleDetails(material_name="x", lot_number="1"))
        with pytest.raises(PermissionDenied):
            testing.assign_tests(s, cho, setup["sample"], [setup["assay"]])


@pytest.mark.parametrize("role, review_page, users_page", [
    (Role.MANAGER, True, False), (Role.SUPERVISOR, True, False), (Role.LEAD_ANALYST, True, False),
    (Role.CHEMICAL_HYGIENE_OFFICER, False, False),
])
def test_navigation_for_new_roles(qtbot, db, admin, make_user, role, review_page, users_page):
    user = make_user(f"nav.{role.value}", role)
    window = MainWindow(db, user)
    qtbot.addWidget(window)
    window._signing_out = True  # close quietly at the end
    titles = {p.title for p in window.pages}
    assert ("Review" in titles) == review_page
    assert ("Users" in titles) == users_page
    for title in titles:
        assert window.show_page(title)
    # All four can manage inventory (the Manager through the master-data rights shared with QA / admins).
    inventory_page = next(p for p in window.pages if p.title == "Inventory")
    assert not inventory_page.receive_button.isHidden()
