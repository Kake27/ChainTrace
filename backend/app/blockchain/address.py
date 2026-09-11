import re

# Legacy P2PKH/P2SH (mainnet + testnet) and Bech32/Bech32m (bc1 / tb1).
_ADDRESS_RE = re.compile(
    r"^(?:"
    r"[13][a-km-zA-HJ-NP-Z1-9]{25,34}"
    r"|[mn2][a-km-zA-HJ-NP-Z1-9]{25,34}"
    r"|(?:bc1|tb1)[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{11,87}"
    r")$",
    re.IGNORECASE,
)


def is_valid_bitcoin_address(address: str) -> bool:
    return bool(_ADDRESS_RE.fullmatch(address.strip()))
