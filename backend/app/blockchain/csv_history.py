from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any

from app.models.transaction import Input, Output, Transaction
from app.models.utxo import UTXO

_TXID_ALIASES = frozenset({"txid", "transaction_id", "transaction", "hash", "tx_hash", "tx"})
_ROLE_ALIASES = frozenset({"role", "type", "direction", "kind"})
_INDEX_ALIASES = frozenset({"index", "n", "vout", "vin", "output_index", "input_index"})
_ADDRESS_ALIASES = frozenset({"address", "addr", "scriptpubkey_address"})
_VALUE_SATS_ALIASES = frozenset({"value_sats", "value_sat", "sats", "satoshis"})
_VALUE_BTC_ALIASES = frozenset({"value_btc", "btc", "amount_btc"})
_VALUE_GENERIC_ALIASES = frozenset({"value", "amount"})
_PREV_TXID_ALIASES = frozenset({"previous_txid", "prev_txid", "prevout_txid", "vin_txid"})
_PREV_VOUT_ALIASES = frozenset({"previous_vout", "prev_vout", "prevout_n", "vin_vout"})
_TIMESTAMP_ALIASES = frozenset({"timestamp", "time", "block_time", "date", "datetime"})
_HEIGHT_ALIASES = frozenset({"block_height", "height", "block"})
_FEE_ALIASES = frozenset({"fee", "fee_sats", "fee_sat"})
_CONFIRMED_ALIASES = frozenset({"confirmed"})
_SCRIPT_ALIASES = frozenset({"script_type", "scriptpubkey_type"})
_INPUT_ADDR_ALIASES = frozenset({"input_addresses", "inputs", "from_addresses"})
_OUTPUT_ADDR_ALIASES = frozenset({"output_addresses", "outputs", "to_addresses"})


class CsvParseError(ValueError):
    """Raised when an uploaded history CSV cannot be normalized."""


def parse_wallet_csv(
    content: str | bytes,
    *,
    address: str | None = None,
    max_transactions: int | None = None,
) -> tuple[list[Transaction], list[UTXO], list[str]]:
    text = _decode(content)
    rows = _read_rows(text)
    if not rows:
        raise CsvParseError("CSV has no data rows")

    headers = set(rows[0].keys())
    warnings: list[str] = []

    if headers & _ROLE_ALIASES:
        transactions = _parse_role_rows(rows)
    elif (headers & _INPUT_ADDR_ALIASES) or (headers & _OUTPUT_ADDR_ALIASES):
        transactions = _parse_wide_rows(rows)
        warnings.append(
            "Wide CSV format used. Input/output values are split evenly across listed addresses "
            "when per-address amounts are omitted."
        )
    else:
        transactions = _parse_flat_rows(rows, wallet_address=address)
        warnings.append(
            "Flat CSV format used (one row per transaction). Common-input, change, and peel-chain "
            "heuristics need per-input/per-output rows (role=input|output) for full accuracy."
        )

    transactions = _sort_transactions(transactions)
    if address:
        transactions = [tx for tx in transactions if _tx_touches_address(tx, address)]
        if not transactions:
            raise CsvParseError(f"No transactions in the CSV involve address {address}")
    if max_transactions is not None:
        transactions = transactions[:max_transactions]

    utxos = derive_utxos(transactions, address=address)
    return transactions, utxos, warnings


def derive_utxos(transactions: list[Transaction], *, address: str | None = None) -> list[UTXO]:
    spent = {(inp.previous_txid, inp.previous_vout) for tx in transactions for inp in tx.inputs}
    utxos: list[UTXO] = []
    for tx in transactions:
        for output in tx.outputs:
            if (tx.txid, output.vout) in spent:
                continue
            if address and output.address != address:
                continue
            utxos.append(
                UTXO(
                    txid=tx.txid,
                    vout=output.vout,
                    address=output.address,
                    value=output.value,
                    confirmed=tx.confirmed,
                    block_height=tx.block_height,
                )
            )
    return utxos


def _decode(content: str | bytes) -> str:
    if isinstance(content, str):
        return content
    return content.decode("utf-8-sig")


def _read_rows(text: str) -> list[dict[str, str]]:
    sample = text.lstrip("\ufeff")[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise CsvParseError("CSV is missing a header row")
    normalized_names = [_normalize_header(name) for name in reader.fieldnames]
    if not any(name in _TXID_ALIASES for name in normalized_names):
        raise CsvParseError("CSV must include a txid / transaction_id column")
    rows: list[dict[str, str]] = []
    for raw in reader:
        row = {_normalize_header(key): (value or "").strip() for key, value in raw.items() if key}
        if not any(row.values()):
            continue
        rows.append(row)
    return rows


def _normalize_header(name: str) -> str:
    return name.strip().lower().replace(" ", "_").replace("-", "_")


def _get(row: dict[str, str], aliases: frozenset[str]) -> str:
    for key in aliases:
        if row.get(key):
            return row[key]
    return ""


def _parse_role_rows(rows: list[dict[str, str]]) -> list[Transaction]:
    grouped: dict[str, dict[str, Any]] = {}
    for row_number, row in enumerate(rows, start=2):
        txid = _get(row, _TXID_ALIASES)
        if not txid:
            raise CsvParseError(f"Row {row_number} is missing txid")
        role = _normalize_role(_get(row, _ROLE_ALIASES))
        if role is None:
            raise CsvParseError(
                f"Row {row_number} has invalid role; use input or output"
            )
        bucket = grouped.setdefault(txid, {"inputs": [], "outputs": [], "meta": {}})
        _merge_tx_meta(bucket["meta"], row)
        value = _parse_value_sats(row, row_number)
        index = _parse_optional_int(_get(row, _INDEX_ALIASES))
        address = _get(row, _ADDRESS_ALIASES) or None
        if role == "input":
            prev_txid = _get(row, _PREV_TXID_ALIASES) or "unknown"
            prev_vout = _parse_optional_int(_get(row, _PREV_VOUT_ALIASES))
            bucket["inputs"].append(
                Input(
                    previous_txid=prev_txid,
                    previous_vout=prev_vout if prev_vout is not None else (index or 0),
                    address=address,
                    value=value,
                )
            )
        else:
            bucket["outputs"].append(
                Output(
                    vout=index if index is not None else len(bucket["outputs"]),
                    address=address,
                    value=value,
                    script_type=_get(row, _SCRIPT_ALIASES) or None,
                )
            )

    return [_build_transaction(txid, bucket) for txid, bucket in grouped.items()]


def _parse_wide_rows(rows: list[dict[str, str]]) -> list[Transaction]:
    transactions: list[Transaction] = []
    for row_number, row in enumerate(rows, start=2):
        txid = _get(row, _TXID_ALIASES)
        if not txid:
            raise CsvParseError(f"Row {row_number} is missing txid")
        input_addresses = _split_list(_get(row, _INPUT_ADDR_ALIASES))
        output_addresses = _split_list(_get(row, _OUTPUT_ADDR_ALIASES))
        value = _parse_optional_value(row)
        inputs = [
            Input(previous_txid="unknown", previous_vout=i, address=addr, value=_share(value, len(input_addresses)))
            for i, addr in enumerate(input_addresses)
        ]
        outputs = [
            Output(vout=i, address=addr, value=_share(value, len(output_addresses)))
            for i, addr in enumerate(output_addresses)
        ]
        if not inputs and not outputs:
            raise CsvParseError(f"Row {row_number} has no input_addresses or output_addresses")
        transactions.append(
            Transaction(
                txid=txid,
                timestamp=_parse_timestamp(_get(row, _TIMESTAMP_ALIASES)),
                block_height=_parse_optional_int(_get(row, _HEIGHT_ALIASES)),
                inputs=inputs,
                outputs=outputs,
                fee=_parse_optional_int(_get(row, _FEE_ALIASES)),
                confirmed=_parse_confirmed(_get(row, _CONFIRMED_ALIASES)),
            )
        )
    return transactions


def _parse_flat_rows(rows: list[dict[str, str]], *, wallet_address: str | None) -> list[Transaction]:
    transactions: list[Transaction] = []
    for row_number, row in enumerate(rows, start=2):
        txid = _get(row, _TXID_ALIASES)
        if not txid:
            raise CsvParseError(f"Row {row_number} is missing txid")
        signed_value = _parse_signed_value(row, row_number)
        address = _get(row, _ADDRESS_ALIASES) or wallet_address
        abs_value = abs(signed_value)
        if signed_value >= 0:
            inputs: list[Input] = []
            outputs = [Output(vout=0, address=address, value=abs_value)]
        else:
            inputs = [Input(previous_txid="unknown", previous_vout=0, address=address, value=abs_value)]
            outputs = []
        transactions.append(
            Transaction(
                txid=txid,
                timestamp=_parse_timestamp(_get(row, _TIMESTAMP_ALIASES)),
                block_height=_parse_optional_int(_get(row, _HEIGHT_ALIASES)),
                inputs=inputs,
                outputs=outputs,
                fee=_parse_optional_int(_get(row, _FEE_ALIASES)),
                confirmed=_parse_confirmed(_get(row, _CONFIRMED_ALIASES)),
            )
        )
    return transactions


def _merge_tx_meta(meta: dict[str, Any], row: dict[str, str]) -> None:
    timestamp = _parse_timestamp(_get(row, _TIMESTAMP_ALIASES))
    if timestamp is not None:
        meta["timestamp"] = timestamp
    height = _parse_optional_int(_get(row, _HEIGHT_ALIASES))
    if height is not None:
        meta["block_height"] = height
    fee = _parse_optional_int(_get(row, _FEE_ALIASES))
    if fee is not None:
        meta["fee"] = fee
    confirmed_raw = _get(row, _CONFIRMED_ALIASES)
    if confirmed_raw:
        meta["confirmed"] = _parse_confirmed(confirmed_raw)


def _build_transaction(txid: str, bucket: dict[str, Any]) -> Transaction:
    meta = bucket["meta"]
    outputs = sorted(bucket["outputs"], key=lambda item: item.vout)
    return Transaction(
        txid=txid,
        timestamp=meta.get("timestamp"),
        block_height=meta.get("block_height"),
        inputs=bucket["inputs"],
        outputs=outputs,
        fee=meta.get("fee"),
        confirmed=bool(meta.get("confirmed", True)),
    )


def _normalize_role(value: str) -> str | None:
    lowered = value.strip().lower()
    if lowered in {"input", "in", "vin", "spend", "sent"}:
        return "input"
    if lowered in {"output", "out", "vout", "receive", "received"}:
        return "output"
    return None


def _parse_value_sats(row: dict[str, str], row_number: int) -> int:
    raw = _get(row, _VALUE_SATS_ALIASES) or _get(row, _VALUE_GENERIC_ALIASES)
    btc = _get(row, _VALUE_BTC_ALIASES)
    if btc:
        return _btc_to_sats(btc, row_number)
    if not raw:
        raise CsvParseError(f"Row {row_number} is missing value_sats")
    if "." in raw:
        return _btc_to_sats(raw, row_number)
    try:
        return abs(int(raw.replace(",", "")))
    except ValueError as exc:
        raise CsvParseError(f"Row {row_number} has invalid satoshi value {raw!r}") from exc


def _parse_optional_value(row: dict[str, str]) -> int | None:
    raw = _get(row, _VALUE_SATS_ALIASES) or _get(row, _VALUE_GENERIC_ALIASES) or _get(row, _VALUE_BTC_ALIASES)
    if not raw:
        return None
    if "." in raw or _get(row, _VALUE_BTC_ALIASES):
        return abs(_btc_to_sats(raw, 0))
    return abs(int(raw.replace(",", "")))


def _parse_signed_value(row: dict[str, str], row_number: int) -> int:
    raw = _get(row, _VALUE_GENERIC_ALIASES) or _get(row, _VALUE_SATS_ALIASES) or _get(row, _VALUE_BTC_ALIASES)
    if not raw:
        raise CsvParseError(f"Row {row_number} is missing value")
    negative = raw.strip().startswith("-")
    magnitude = _parse_value_sats({**row, "value_sats": raw.lstrip("+-")}, row_number)
    return -magnitude if negative else magnitude


def _btc_to_sats(raw: str, row_number: int) -> int:
    try:
        return abs(int(round(float(raw.replace(",", "")) * 100_000_000)))
    except ValueError as exc:
        raise CsvParseError(f"Row {row_number} has invalid BTC value {raw!r}") from exc


def _parse_optional_int(raw: str) -> int | None:
    if raw == "":
        return None
    try:
        return int(float(raw))
    except ValueError as exc:
        raise CsvParseError(f"Invalid integer {raw!r}") from exc


def _parse_timestamp(raw: str) -> int | None:
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return int(datetime.strptime(raw[:19], fmt).timestamp())
        except ValueError:
            continue
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
    except ValueError as exc:
        raise CsvParseError(f"Invalid timestamp {raw!r}") from exc


def _parse_confirmed(raw: str) -> bool:
    if not raw:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "unconfirmed"}


def _split_list(raw: str) -> list[str]:
    if not raw:
        return []
    for sep in ("|", ";", ","):
        if sep in raw:
            return [part.strip() for part in raw.split(sep) if part.strip()]
    return [raw.strip()]


def _share(total: int | None, count: int) -> int:
    if not total or count <= 0:
        return 0
    return total // count


def _tx_touches_address(tx: Transaction, address: str) -> bool:
    return any(item.address == address for item in (*tx.inputs, *tx.outputs))


def _sort_transactions(transactions: list[Transaction]) -> list[Transaction]:
    return sorted(
        transactions,
        key=lambda tx: (tx.block_height is None, tx.block_height or 0, tx.timestamp or 0, tx.txid),
        reverse=True,
    )
