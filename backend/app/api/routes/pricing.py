"""Model pricing profiles and aggregate billing endpoints."""
from fastapi import APIRouter
from backend.app.persistence.store import store
from backend.app.observability import billing
from backend.app.observability.repository import model_call_rows

router = APIRouter()
@router.get('/api/pricing',response_model=list[billing.PricingProfile])
def pricing():
    return billing.profiles(store)

@router.post('/api/pricing/{profile_id}',response_model=billing.PricingProfile)
def update_pricing(profile_id:str,body:billing.PricingUpdate):
    return billing.save_profile(store,profile_id,body)

@router.get('/api/billing-summary')
def billing_summary():
    return billing.summarize(model_call_rows(store))

