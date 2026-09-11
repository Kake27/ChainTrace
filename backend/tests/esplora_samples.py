SAMPLE_TX = {
    "txid": "abc123def456",
    "fee": 250,
    "vin": [
        {
            "txid": "prevtx1",
            "vout": 0,
            "prevout": {
                "scriptpubkey_address": "bc1qinputaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "scriptpubkey_type": "v0_p2wpkh",
                "value": 100000,
            },
            "is_coinbase": False,
        },
        {
            "txid": "prevtx2",
            "vout": 1,
            "prevout": {
                "scriptpubkey_address": "bc1qinputbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "scriptpubkey_type": "v0_p2wpkh",
                "value": 50000,
            },
            "is_coinbase": False,
        },
    ],
    "vout": [
        {
            "scriptpubkey_address": "bc1qoutputcccccccccccccccccccccccccccc",
            "scriptpubkey_type": "v0_p2wpkh",
            "value": 120000,
        },
        {
            "scriptpubkey_address": "bc1qchangedddddddddddddddddddddddddddd",
            "scriptpubkey_type": "v0_p2wpkh",
            "value": 29750,
        },
    ],
    "status": {
        "confirmed": True,
        "block_height": 800000,
        "block_time": 1700000000,
    },
}

SAMPLE_COINBASE_TX = {
    "txid": "coinbase0001",
    "fee": 0,
    "vin": [{"is_coinbase": True, "txid": None, "vout": None}],
    "vout": [
        {
            "scriptpubkey_address": "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
            "scriptpubkey_type": "p2pkh",
            "value": 5000000000,
        }
    ],
    "status": {"confirmed": True, "block_height": 0, "block_time": 1231006505},
}

SAMPLE_UTXO = {
    "txid": "abc123def456",
    "vout": 1,
    "value": 29750,
    "status": {"confirmed": True, "block_height": 800000},
}
