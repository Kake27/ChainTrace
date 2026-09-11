from app.blockchain.parser import parse_transaction, parse_utxo
from tests.esplora_samples import SAMPLE_COINBASE_TX, SAMPLE_TX, SAMPLE_UTXO


def test_parse_transaction_uses_satoshis_and_esplora_fields() -> None:
    tx = parse_transaction(SAMPLE_TX)
    assert tx.txid == "abc123def456"
    assert tx.fee == 250
    assert tx.block_height == 800000
    assert tx.timestamp == 1700000000
    assert tx.confirmed is True
    assert len(tx.inputs) == 2
    assert tx.inputs[0].address.endswith("aaaaa")
    assert tx.inputs[0].value == 100000
    assert tx.outputs[0].value == 120000
    assert tx.outputs[1].script_type == "v0_p2wpkh"


def test_parse_coinbase_input_has_no_spent_address() -> None:
    tx = parse_transaction(SAMPLE_COINBASE_TX)
    assert tx.inputs[0].address is None
    assert tx.inputs[0].value == 0
    assert tx.outputs[0].value == 5_000_000_000


def test_parse_utxo() -> None:
    utxo = parse_utxo(SAMPLE_UTXO, address="bc1qchangedddddddddddddddddddddddddddd")
    assert utxo.value == 29750
    assert utxo.confirmed is True
    assert utxo.spent_by_txid is None
