# ChainTrace

Local-first Bitcoin privacy analysis for BOSS Battle 2026 (Machine Money / AI Chain-Analysis).

This slice fetches **public wallet history** and runs **address reuse** and **common-input ownership** on those normalized transactions.

History is **not stored**. Each `GET /api/v1/wallet/{address}/history` call hits the explorer, returns JSON, and drops the result when the response is sent. There is no SQLite/file cache yet. Addresses with more than 500 UTXOs (for example the genesis address) still return transaction history; `utxos` may be empty and `warnings` will explain the explorer limit.

## Backend setup

Requires Python 3.11+.

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Health check: `GET http://127.0.0.1:8000/health`

Wallet history (satoshis, not BTC floats):

```text
GET http://127.0.0.1:8000/api/v1/wallet/{address}/history
GET http://127.0.0.1:8000/api/v1/wallet/{address}/history?max_transactions=50
GET http://127.0.0.1:8000/api/v1/wallet/{address}/heuristics/address-reuse
POST http://127.0.0.1:8000/api/v1/heuristics/address-reuse
GET http://127.0.0.1:8000/api/v1/wallet/{address}/heuristics/common-input
POST http://127.0.0.1:8000/api/v1/heuristics/common-input
```

The POST body is `{ "transactions": [ ... ] }` using the `transactions` array from the history response.

- Address reuse: same address in two or more distinct txids (linkability, not identity).
- Common-input ownership: distinct input addresses in the same tx are a *candidate* same-owner link. CoinJoin-like equal-output spends are flagged and scored much lower.

Optional env: `MEMPOOL_BASE_URL` (default `https://mempool.space/api`), `MAX_TRANSACTIONS` (default `500`).

```bash
pytest
```
