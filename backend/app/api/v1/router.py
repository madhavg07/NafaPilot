"""Versioned API router. Feature routes are added here milestone by milestone."""

from fastapi import APIRouter

from app.api.v1 import (
    campaigns,
    data,
    experiments,
    llm,
    merchants,
    promotions,
    recommendations,
)

api_router = APIRouter()
api_router.include_router(llm.router)
api_router.include_router(merchants.router)
api_router.include_router(data.router)
api_router.include_router(campaigns.router)
api_router.include_router(recommendations.router)
api_router.include_router(experiments.router)
api_router.include_router(promotions.router)


@api_router.get("/meta", tags=["system"])
def meta() -> dict[str, object]:
    """Describes the data the API serves, so clients can label it honestly."""
    return {
        "api_version": "v1",
        "data_source": "synthetic",
        "execution_mode": "simulated",
        "disclaimer": "Prototype for the Paytm Build for India AI Hackathon 2026. "
        "Not an official Paytm product. All data is synthetic.",
    }
