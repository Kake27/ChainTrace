from fastapi import APIRouter, Query, Request

from app.api.routes.wallet import load_mempool_history
from app.api.schemas.analysis import (
    AddressReuseRequest,
    AddressReuseResponse,
    ChangeDetectionRequest,
    ChangeDetectionResponse,
    CommonInputRequest,
    CommonInputResponse,
)
from app.heuristics.address_reuse import detect_address_reuse
from app.heuristics.change_detection import detect_change_candidates
from app.heuristics.common_input import detect_common_input_ownership
from app.models.transaction import Transaction

router = APIRouter(prefix="/api/v1", tags=["heuristics"])


@router.get("/wallet/{address}/heuristics/address-reuse", response_model=AddressReuseResponse)
def address_reuse_from_wallet(
    request: Request,
    address: str,
    max_transactions: int = Query(default=None, ge=1, le=1000),
) -> AddressReuseResponse:
    transactions, _utxos, warnings = load_mempool_history(
        request, address, max_transactions=max_transactions
    )
    return _response(
        transactions,
        source="mempool",
        address=address,
        warnings=warnings,
    )


@router.post("/heuristics/address-reuse", response_model=AddressReuseResponse)
def address_reuse_from_history(body: AddressReuseRequest) -> AddressReuseResponse:
    return _response(body.transactions, source="provided_history")


def _response(
    transactions: list[Transaction],
    *,
    source: str,
    address: str | None = None,
    warnings: list[str] | None = None,
) -> AddressReuseResponse:
    findings = detect_address_reuse(transactions)
    return AddressReuseResponse(
        source=source,
        address=address,
        transaction_count=len(transactions),
        reused_address_count=len(findings),
        findings=findings,
        warnings=warnings or [],
    )


@router.get("/wallet/{address}/heuristics/common-input", response_model=CommonInputResponse)
def common_input_from_wallet(
    request: Request,
    address: str,
    max_transactions: int = Query(default=None, ge=1, le=1000),
) -> CommonInputResponse:
    transactions, _utxos, warnings = load_mempool_history(
        request, address, max_transactions=max_transactions
    )
    return _common_input_response(
        transactions,
        source="mempool",
        address=address,
        warnings=warnings,
    )


@router.post("/heuristics/common-input", response_model=CommonInputResponse)
def common_input_from_history(body: CommonInputRequest) -> CommonInputResponse:
    return _common_input_response(body.transactions, source="provided_history")


def _common_input_response(
    transactions: list[Transaction],
    *,
    source: str,
    address: str | None = None,
    warnings: list[str] | None = None,
) -> CommonInputResponse:
    findings = detect_common_input_ownership(transactions)
    return CommonInputResponse(
        source=source,
        address=address,
        transaction_count=len(transactions),
        pair_count=len(findings),
        findings=findings,
        warnings=warnings or [],
    )


@router.get("/wallet/{address}/heuristics/change", response_model=ChangeDetectionResponse)
def change_from_wallet(
    request: Request,
    address: str,
    max_transactions: int = Query(default=None, ge=1, le=1000),
) -> ChangeDetectionResponse:
    transactions, _utxos, warnings = load_mempool_history(
        request, address, max_transactions=max_transactions
    )
    return _change_response(
        transactions,
        source="mempool",
        address=address,
        warnings=warnings,
    )


@router.post("/heuristics/change", response_model=ChangeDetectionResponse)
def change_from_history(body: ChangeDetectionRequest) -> ChangeDetectionResponse:
    return _change_response(body.transactions, source="provided_history")


def _change_response(
    transactions: list[Transaction],
    *,
    source: str,
    address: str | None = None,
    warnings: list[str] | None = None,
) -> ChangeDetectionResponse:
    findings = detect_change_candidates(transactions)
    return ChangeDetectionResponse(
        source=source,
        address=address,
        transaction_count=len(transactions),
        candidate_count=len(findings),
        findings=findings,
        warnings=warnings or [],
    )
