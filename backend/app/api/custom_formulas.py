"""
Custom vegetation formula endpoints.
"""
from __future__ import annotations

import re
import uuid as uuid_mod
from typing import Any, Dict, List, Optional

import simpleeval
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db_with_tenant
from app.middleware.auth import require_auth
from app.models import VegetationCustomFormula

router = APIRouter(prefix="/api/vegetation/custom-formulas", tags=["custom-formulas"])

ALLOWED_BANDS = {"B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B11", "B12"}
ALLOWED_FUNCTIONS = {
    "sqrt": lambda x: x ** 0.5,
    "abs": abs,
    "log": __import__("math").log,
    "log10": __import__("math").log10,
    "exp": __import__("math").exp,
    "sin": __import__("math").sin,
    "cos": __import__("math").cos,
    "tan": __import__("math").tan,
    "arctan": __import__("math").atan,
    "arctan2": __import__("math").atan2,
    "clip": lambda x, lo, hi: max(lo, min(hi, x)),
    "maximum": max,
    "minimum": min,
    "power": pow,
}
MAX_FORMULA_LENGTH = 300
MAX_NAME_LENGTH = 80


class FormulaCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=MAX_NAME_LENGTH)
    formula: str = Field(..., min_length=1, max_length=MAX_FORMULA_LENGTH)
    description: Optional[str] = Field(default=None, max_length=500)


class FormulaValidateRequest(BaseModel):
    formula: str = Field(..., min_length=1, max_length=MAX_FORMULA_LENGTH)


def _extract_bands_from_formula(formula: str) -> List[str]:
    found = sorted(set(re.findall(r"\bB(?:0[2-8]|8A|1[12])\b", formula.upper())))
    return [band for band in found if band in ALLOWED_BANDS]


def _validate_formula(formula: str) -> Dict[str, Any]:
    expression = formula.strip()
    if not expression:
        raise HTTPException(status_code=422, detail="Formula is required")
    if len(expression) > MAX_FORMULA_LENGTH:
        raise HTTPException(status_code=422, detail="Formula is too long")

    bands = _extract_bands_from_formula(expression)
    if not bands:
        raise HTTPException(
            status_code=422,
            detail="Formula must reference at least one valid Sentinel-2 band",
        )

    evaluator = simpleeval.EvalWithCompoundTypes(functions=ALLOWED_FUNCTIONS, names={})
    for band in bands:
        evaluator.names[band] = 1.0

    try:
        result = evaluator.eval(expression)
    except simpleeval.InvalidExpression as exc:
        raise HTTPException(status_code=422, detail=f"Invalid formula syntax: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid formula: {exc}") from exc

    if not isinstance(result, (int, float)):
        raise HTTPException(status_code=422, detail="Formula must evaluate to a numeric value")

    return {"formula": expression, "bands": bands}


def _serialize_formula(item: VegetationCustomFormula) -> Dict[str, Any]:
    return {
        "id": str(item.id),
        "name": item.name,
        "description": item.description,
        "formula": item.formula,
        "is_validated": bool(item.is_validated),
        "validation_error": item.validation_error,
        "usage_count": item.usage_count,
        "last_used_at": item.last_used_at,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


@router.get("")
async def list_custom_formulas(
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    tenant_id = current_user["tenant_id"]
    rows = (
        db.query(VegetationCustomFormula)
        .filter(VegetationCustomFormula.tenant_id == tenant_id)
        .order_by(VegetationCustomFormula.created_at.desc())
        .all()
    )
    return {"items": [_serialize_formula(row) for row in rows], "total": len(rows)}


@router.post("")
async def create_custom_formula(
    request: FormulaCreateRequest,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    tenant_id = current_user["tenant_id"]
    validated = _validate_formula(request.formula)
    normalized_name = request.name.strip()
    if not normalized_name:
        raise HTTPException(status_code=422, detail="Name is required")

    existing = (
        db.query(VegetationCustomFormula)
        .filter(
            VegetationCustomFormula.tenant_id == tenant_id,
            VegetationCustomFormula.name.ilike(normalized_name),
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="A formula with this name already exists")

    row = VegetationCustomFormula(
        tenant_id=tenant_id,
        name=normalized_name,
        description=request.description.strip() if request.description else None,
        formula=validated["formula"],
        is_validated=True,
        validation_error=None,
        created_by=current_user.get("user_id"),
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    payload = _serialize_formula(row)
    payload["bands"] = validated["bands"]
    return payload


@router.post("/validate")
async def validate_custom_formula(
    request: FormulaValidateRequest,
    current_user: dict = Depends(require_auth),
):
    _ = current_user
    validated = _validate_formula(request.formula)
    return {
        "valid": True,
        "formula": validated["formula"],
        "bands": validated["bands"],
    }


@router.delete("/{formula_id}")
async def delete_custom_formula(
    formula_id: str,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    tenant_id = current_user["tenant_id"]
    try:
        formula_uuid = uuid_mod.UUID(formula_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid formula id") from exc

    row = (
        db.query(VegetationCustomFormula)
        .filter(
            VegetationCustomFormula.id == formula_uuid,
            VegetationCustomFormula.tenant_id == tenant_id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Custom formula not found")

    db.delete(row)
    db.commit()
    return {"deleted": True, "id": formula_id}
