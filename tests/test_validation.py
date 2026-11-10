import pytest
from pydantic import ValidationError

from conftest import bearer
from schemas import EmployeeCreate, EmployeeScan, PreRegister, VisitRef, WalkIn

VISITOR = {"full_name": "John Moyo", "phone": "+263 77 123 4567", "national_id": "63-123456 a 78",
           "purpose": "Meeting", "host_employee_code": "EXODUS-5"}


@pytest.mark.parametrize("code", ["EXODUS-99", "exodus-99", "A1", "EMP_007"])
def test_employee_code_accepts(code):
    assert EmployeeScan(employee_code=code).employee_code == code.upper()


@pytest.mark.parametrize("code", ["", "a", "EXODUS 99", "EXO;DROP", "<b>x</b>", "X" * 33, "-ABC"])
def test_employee_code_rejects(code):
    with pytest.raises(ValidationError):
        EmployeeScan(employee_code=code)


@pytest.mark.parametrize("phone", ["12345", "abcdefghij", "+263-77-<script>", "1" * 25])
def test_phone_rejects(phone):
    with pytest.raises(ValidationError):
        WalkIn(**{**VISITOR, "phone": phone})


def test_phone_normalised():
    assert WalkIn(**VISITOR).phone == "+263771234567"


@pytest.mark.parametrize("plate", ["A", "AB<", "ACX 1234; DROP", "-ACX1"])
def test_plate_rejects(plate):
    with pytest.raises(ValidationError):
        EmployeeCreate(employee_code="EXODUS-1", full_name="Jane Doe", vehicle={"plate": plate})


def test_plate_normalised():
    e = EmployeeCreate(employee_code="EXODUS-1", full_name="Jane Doe", vehicle={"plate": "acx  1234"})
    assert e.vehicle.plate == "ACX 1234"


def test_national_id_and_name_rules():
    assert WalkIn(**VISITOR).national_id == "63-123456 A 78"
    for bad in ({"national_id": "12"}, {"national_id": "<img src=x>"}, {"full_name": "<script>alert(1)</script>"},
                {"full_name": "J"}):
        with pytest.raises(ValidationError):
            WalkIn(**{**VISITOR, **bad})


def test_free_text_is_html_escaped():
    w = WalkIn(**{**VISITOR, "purpose": "<script>alert('x')</script> R&D"})
    assert "<script>" not in w.purpose and "&lt;script&gt;" in w.purpose and "R&amp;D" in w.purpose


def test_length_caps():
    with pytest.raises(ValidationError):
        WalkIn(**{**VISITOR, "purpose": "x" * 256})
    with pytest.raises(ValidationError):  # escaping can push it over the cap
        WalkIn(**{**VISITOR, "purpose": "&" * 100})


def test_extra_fields_forbidden_and_vehicle_rule():
    with pytest.raises(ValidationError):
        EmployeeScan(employee_code="EXODUS-1", is_admin=True)
    with pytest.raises(ValidationError):
        WalkIn(**{**VISITOR, "arrival_type": "vehicle"})  # plate required


def test_visit_ref_needs_exactly_one():
    with pytest.raises(ValidationError):
        VisitRef()
    with pytest.raises(ValidationError):
        VisitRef(badge_code="ABCDEF12", visit_id=3)
    assert VisitRef(badge_code="abcdef12").badge_code == "ABCDEF12"


def test_preregister_past_date_rejected():
    with pytest.raises(ValidationError):
        PreRegister(**{k: v for k, v in VISITOR.items() if k != "host_employee_code"}, expected_date="2000-01-01")


# ---- through the HTTP layer -----------------------------------------------------------------
def test_422_uses_consistent_json_and_does_not_echo_input(client, token_for):
    r = client.post("/api/v1/access/employee/check-in", headers=bearer(token_for("SECURITY_GUARD")),
                    json={"employee_code": "bad code <script>"})
    assert r.status_code == 422 and r.json["code"] == "VALIDATION_ERROR" and r.json["status"] == "error"
    assert "<script>" not in r.get_data(as_text=True)


def test_non_object_body_is_400(client, token_for):
    r = client.post("/api/v1/access/employee/check-in", headers=bearer(token_for("SECURITY_GUARD")), json=[1, 2])
    assert r.status_code == 400 and r.json["code"] == "INVALID_JSON"


def test_oversized_body_is_413(client, token_for):
    r = client.post("/api/v1/access/employee/check-in", headers=bearer(token_for("SECURITY_GUARD")),
                    data=b"x" * (5 * 1024 * 1024 + 1), content_type="application/json")
    assert r.status_code == 413 and r.json["status"] == "error"
