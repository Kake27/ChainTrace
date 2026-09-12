from fastapi import APIRouter, Query, Request

from app.api.routes.wallet import load_mempool_history
from app.api.schemas.analysis import AddressReuseRequest, AddressReuseResponse
from app.heuristics.address_reuse import detect_address_reuse
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
