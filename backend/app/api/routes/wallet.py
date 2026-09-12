from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile

from app.api.schemas.wallet import WalletHistoryResponse
from app.blockchain.address import is_valid_bitcoin_address
from app.blockchain.csv_history import CsvParseError, parse_wallet_csv
from app.blockchain.mempool_client import AddressNotFoundError, BlockchainAPIError
from app.config import settings
from app.models.transaction import Transaction
from app.models.utxo import UTXO

router = APIRouter(prefix="/api/v1", tags=["wallet"])


def load_mempool_history(
    request: Request,
    address: str,
    *,
    max_transactions: int | None = None,
) -> tuple[list[Transaction], list[UTXO], list[str]]:
    if not is_valid_bitcoin_address(address):
        raise HTTPException(status_code=400, detail="Invalid Bitcoin address")

    limit = max_transactions or settings.max_transactions
    client = request.app.state.blockchain_client
    try:
        history = client.get_wallet_history(address, max_transactions=limit)
    except AddressNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BlockchainAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _unpack_history(history)


@router.get("/wallet/{address}/history", response_model=WalletHistoryResponse)
def get_wallet_history(
    request: Request,
    address: str,
    max_transactions: int = Query(default=None, ge=1, le=1000),
) -> WalletHistoryResponse:
    limit = max_transactions or settings.max_transactions
    transactions, utxos, warnings = load_mempool_history(
        request, address, max_transactions=max_transactions
    )
    return _history_response(
        address=address,
        source="mempool",
        transactions=transactions,
        utxos=utxos,
        truncated=len(transactions) >= limit,
        warnings=warnings,
    )


@router.post("/wallet/history/csv", response_model=WalletHistoryResponse)
async def upload_wallet_history_csv(
    file: UploadFile = File(..., description="Wallet history CSV"),
    address: str | None = Form(default=None),
    max_transactions: int | None = Form(default=None, ge=1, le=1000),
) -> WalletHistoryResponse:
    if address and not is_valid_bitcoin_address(address):
        raise HTTPException(status_code=400, detail="Invalid Bitcoin address")

    filename = (file.filename or "").lower()
    if filename and not filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Upload a .csv file")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="CSV file is empty")
    if len(raw) > settings.csv_max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"CSV exceeds the {settings.csv_max_bytes} byte upload limit",
        )

    limit = max_transactions or settings.max_transactions
    try:
        transactions, utxos, warnings = parse_wallet_csv(
            raw,
            address=address,
            max_transactions=limit,
        )
    except CsvParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _history_response(
        address=address or "uploaded-csv",
        source="csv",
        transactions=transactions,
        utxos=utxos,
        truncated=len(transactions) >= limit,
        warnings=warnings,
    )


def _unpack_history(
    history: tuple[list[Transaction], list[UTXO]] | tuple[list[Transaction], list[UTXO], list[str]],
) -> tuple[list[Transaction], list[UTXO], list[str]]:
    if len(history) == 3:
        return history[0], history[1], history[2]
    return history[0], history[1], []


def _history_response(
    *,
    address: str,
    source: str,
    transactions: list[Transaction],
    utxos: list[UTXO],
    truncated: bool,
    warnings: list[str] | None = None,
) -> WalletHistoryResponse:
    return WalletHistoryResponse(
        address=address,
        source=source,
        truncated=truncated,
        transaction_count=len(transactions),
        utxo_count=len(utxos),
        total_utxo_value=sum(utxo.value for utxo in utxos),
        transactions=transactions,
        utxos=utxos,
        warnings=warnings or [],
    )
