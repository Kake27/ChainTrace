from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.analysis.service import analyse_wallet
from app.blockchain.mempool_client import AddressNotFoundError, BlockchainAPIError
from app.models.analysis import AnalysisResult

router = APIRouter(tags=["analysis"])


class AnalyseWalletRequest(BaseModel):
    wallet_address: str = Field(min_length=1)


@router.post("/analyse", response_model=AnalysisResult)
def analyse_wallet_route(request: Request, body: AnalyseWalletRequest) -> AnalysisResult:
    try:
        return analyse_wallet(body.wallet_address, request.app.state.blockchain_client)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AddressNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BlockchainAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
