from app.models.transaction import Input, Output, Transaction
from app.models.utxo import UTXO


def parse_transaction(raw: dict) -> Transaction:
    status = raw.get("status") or {}
    confirmed = bool(status.get("confirmed", False))
    return Transaction(
        txid=raw["txid"],
        timestamp=status.get("block_time"),
        block_height=status.get("block_height"),
        inputs=[_parse_input(vin) for vin in raw.get("vin") or []],
        outputs=[_parse_output(index, vout) for index, vout in enumerate(raw.get("vout") or [])],
        fee=raw.get("fee"),
        confirmed=confirmed,
    )


def parse_utxo(raw: dict, address: str | None = None) -> UTXO:
    status = raw.get("status") or {}
    return UTXO(
        txid=raw["txid"],
        vout=raw["vout"],
        address=address,
        value=int(raw["value"]),
        confirmed=bool(status.get("confirmed", False)),
        block_height=status.get("block_height"),
    )


def _parse_input(vin: dict) -> Input:
    prevout = vin.get("prevout") or {}
    previous_txid = vin.get("txid") or ("0" * 64 if vin.get("is_coinbase") else "")
    previous_vout = int(vin.get("vout") or 0)
    return Input(
        previous_txid=previous_txid,
        previous_vout=previous_vout,
        address=prevout.get("scriptpubkey_address"),
        value=int(prevout.get("value") or 0),
    )


def _parse_output(index: int, vout: dict) -> Output:
    return Output(
        vout=index,
        address=vout.get("scriptpubkey_address"),
        value=int(vout.get("value") or 0),
        script_type=vout.get("scriptpubkey_type"),
    )
