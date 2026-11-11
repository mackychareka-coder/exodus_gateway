"""Pydantic request models + sanitizers.

Strict regexes for phone / plate / national ID / employee ID, length caps everywhere,
and HTML-escaping (markupsafe) for free-text fields. Names, IDs, phones and plates are
restricted by regex to charsets that contain no HTML metacharacters.
"""
import re
from datetime import date
from typing import Annotated, Literal, Optional

from flask import request
from markupsafe import escape
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from errors import ApiError

ROLE_NAMES = ("SUPER_ADMIN", "SECURITY_GUARD", "GATE_OPERATOR", "EMPLOYEE")

_CTRL = re.compile(r"[\x00-\x1f\x7f]+")
_WS = re.compile(r"\s+")


def _rx(pattern, msg):
    rx = re.compile(pattern)

    def check(v):
        if not rx.fullmatch(v):
            raise ValueError(msg)
        return v
    return check


def _collapse(v):
    return _WS.sub(" ", v)


def _text(max_len):
    """Single-line free text: drop control chars, HTML-escape, cap the *escaped* length."""
    def clean(v):
        out = str(escape(_WS.sub(" ", _CTRL.sub(" ", v)).strip()))
        if len(out) > max_len:
            raise ValueError(f"must be at most {max_len} characters")
        return out
    return clean


def _phone(v):
    v = re.sub(r"[ \-()]", "", v)
    if not re.fullmatch(r"\+?\d{7,15}", v):
        raise ValueError("must be a valid phone number, e.g. +263771234567")
    return v


def _S(lo, hi):
    return StringConstraints(strip_whitespace=True, min_length=lo, max_length=hi)


EmployeeCode = Annotated[str, _S(2, 32), AfterValidator(str.upper),
                         AfterValidator(_rx(r"[A-Z0-9][A-Z0-9_\-]{1,31}", "letters, digits, '-' and '_' only"))]
FullName = Annotated[str, _S(2, 100), AfterValidator(_collapse),
                     AfterValidator(_rx(r"[^\W\d_](?:[^\W\d_]|[ .'\-]){1,98}", "letters, spaces, . ' - only"))]
Phone = Annotated[str, _S(7, 24), AfterValidator(_phone)]
Plate = Annotated[str, _S(2, 16), AfterValidator(str.upper), AfterValidator(_collapse),
                  AfterValidator(_rx(r"[A-Z0-9][A-Z0-9 \-]{0,14}[A-Z0-9]", "letters, digits, space, '-' only"))]
NationalId = Annotated[str, _S(5, 20), AfterValidator(str.upper),
                       AfterValidator(_rx(r"[A-Z0-9][A-Z0-9 \-]{3,18}[A-Z0-9]", "letters, digits, space, '-' only"))]
BadgeCode = Annotated[str, _S(6, 12), AfterValidator(str.upper), AfterValidator(_rx(r"[A-Z0-9]{6,12}", "invalid badge code"))]
Email = Annotated[str, _S(5, 254), AfterValidator(str.lower),
                  AfterValidator(_rx(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]{2,}", "invalid email"))]
Username = Annotated[str, _S(3, 64), AfterValidator(_rx(r"[A-Za-z0-9_.@\-]+", "letters, digits and _ . @ - only"))]
Purpose = Annotated[str, _S(3, 255), AfterValidator(_text(255))]
ModelText = Annotated[str, _S(1, 50), AfterValidator(_text(50))]
ColorText = Annotated[str, _S(1, 30), AfterValidator(_text(30))]
LabelText = Annotated[str, _S(2, 64), AfterValidator(_text(64))]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: Username
    password: Annotated[str, Field(min_length=1, max_length=128)]  # never stripped


class VehicleIn(Strict):
    plate: Plate
    model: Optional[ModelText] = None
    color: Optional[ColorText] = None


class EmployeeCreate(Strict):
    employee_code: EmployeeCode
    full_name: FullName
    phone: Optional[Phone] = None
    email: Optional[Email] = None
    vehicle: Optional[VehicleIn] = None


class EmployeeScan(Strict):
    employee_code: EmployeeCode
    arrival_type: Optional[Literal["vehicle", "walk_in"]] = None
    method: Optional[Literal["QR", "NFC", "MANUAL"]] = None
    vehicle_id: Optional[int] = Field(default=None, ge=1)  # one of the employee's registered vehicles
    vehicle: Optional[VehicleIn] = None                    # a vehicle not yet on file: saved to their profile

    @model_validator(mode="after")
    def _vehicle(self):
        if self.vehicle is not None and self.vehicle_id is not None:
            raise ValueError("send either vehicle_id or vehicle, not both")
        if self.arrival_type == "walk_in" and (self.vehicle is not None or self.vehicle_id is not None):
            raise ValueError("vehicle details cannot be given for an on-foot arrival")
        return self


class VisitorBase(Strict):
    full_name: FullName
    phone: Phone
    national_id: NationalId
    purpose: Purpose
    arrival_type: Literal["vehicle", "walk_in"] = "walk_in"
    plate: Optional[Plate] = None
    vehicle_model: Optional[ModelText] = None
    vehicle_color: Optional[ColorText] = None

    @model_validator(mode="after")
    def _vehicle(self):
        if self.arrival_type == "vehicle" and not self.plate:
            raise ValueError("plate is required when arriving by vehicle")
        if self.arrival_type == "walk_in":
            self.plate = self.vehicle_model = self.vehicle_color = None
        return self


class PreRegister(VisitorBase):
    expected_date: Optional[date] = None
    host_employee_code: Optional[EmployeeCode] = None  # only honoured for SUPER_ADMIN

    @model_validator(mode="after")
    def _date(self):
        if self.expected_date and self.expected_date < date.today():
            raise ValueError("expected_date cannot be in the past")
        return self


class WalkIn(VisitorBase):
    host_employee_code: EmployeeCode


class VisitRef(Strict):
    badge_code: Optional[BadgeCode] = None
    visit_id: Optional[int] = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _one(self):
        if (self.badge_code is None) == (self.visit_id is None):
            raise ValueError("provide exactly one of badge_code or visit_id")
        return self


class TerminalCreate(Strict):
    name: LabelText
    gate_location: LabelText


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")  # no whitespace stripping: passwords are used verbatim
    username: Username
    password: Annotated[str, Field(min_length=12, max_length=128)]
    role: Literal["SUPER_ADMIN", "SECURITY_GUARD", "GATE_OPERATOR", "EMPLOYEE"]
    employee_code: Optional[EmployeeCode] = None

    @model_validator(mode="after")
    def _emp(self):
        if self.role == "EMPLOYEE" and not self.employee_code:
            raise ValueError("employee_code is required for the EMPLOYEE role")
        return self


def parse_json(model):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "INVALID_JSON", "Request body must be a JSON object.")
    return model.model_validate(data)


class RoleChange(Strict):
    role: Literal["SUPER_ADMIN", "SECURITY_GUARD", "GATE_OPERATOR", "EMPLOYEE"]
    employee_code: Optional[EmployeeCode] = None

    @model_validator(mode="after")
    def _emp(self):
        if self.role == "EMPLOYEE" and not self.employee_code:
            raise ValueError("employee_code is required for the EMPLOYEE role")
        return self


class PasswordReset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: Annotated[str, Field(min_length=12, max_length=128)]


class EmployeeUpdate(Strict):
    email: Optional[Email] = None
    phone: Optional[Phone] = None

    @model_validator(mode="after")
    def _any(self):
        if self.email is None and self.phone is None:
            raise ValueError("provide email and/or phone")
        return self


class AdminEmployeeUpdate(Strict):
    """SUPER_ADMIN edit. Only fields actually sent are changed; phone/email may be null to clear them."""
    employee_code: Optional[EmployeeCode] = None
    full_name: Optional[FullName] = None
    phone: Optional[Phone] = None
    email: Optional[Email] = None
    is_active: Optional[bool] = None

    @model_validator(mode="after")
    def _check(self):
        sent = self.model_fields_set
        if not sent:
            raise ValueError("nothing to update")
        for f in ("employee_code", "full_name", "is_active"):
            if f in sent and getattr(self, f) is None:
                raise ValueError(f"{f} cannot be null")
        return self


class EmployeeDelete(Strict):
    purge: bool = False
    confirm_code: Optional[EmployeeCode] = None
