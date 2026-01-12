"""
Prediction API for Vegetation Prime
Provides time-series forecasting using Linear Regression.
Designed with N8N-friendly webhook response format.
"""

from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
import numpy as np
from sklearn.linear_model import LinearRegression

from app.database import get_db_with_tenant
from app.middleware.auth import require_auth
from app.models import VegetationIndexCache, VegetationScene


# Helper function for database dependency with tenant context
def get_db_for_tenant(current_user: dict = Depends(require_auth)):
    """Get database session with tenant context."""
    for db in get_db_with_tenant(current_user['tenant_id']):
        yield db

router = APIRouter(prefix="/api/vegetation/prediction", tags=["prediction"])


# ============================================================================
# Pydantic Models (N8N-friendly structure)
# ============================================================================

class PredictionPoint(BaseModel):
    """Single prediction point."""
    date: str
    predicted_value: float
    confidence_lower: float
    confidence_upper: float


class PredictionResponse(BaseModel):
    """N8N-friendly prediction response with webhook metadata."""
    entity_id: str
    index_type: str
    model_type: str = "linear_regression"
    training_samples: int
    r2_score: float
    predictions: List[PredictionPoint]
    
    # N8N webhook metadata
    webhook_metadata: Dict[str, Any] = Field(default_factory=dict)
    generated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    
    class Config:
        json_schema_extra = {
            "example": {
                "entity_id": "urn:ngsi-ld:AgriParcel:001",
                "index_type": "NDVI",
                "model_type": "linear_regression",
                "training_samples": 24,
                "r2_score": 0.87,
                "predictions": [
                    {"date": "2026-01-15", "predicted_value": 0.72, "confidence_lower": 0.68, "confidence_upper": 0.76}
                ],
                "webhook_metadata": {
                    "trigger": "scheduled",
                    "n8n_workflow_id": None
                }
            }
        }


class WebhookTriggerRequest(BaseModel):
    """Request body for N8N webhook trigger."""
    callback_url: Optional[str] = None
    workflow_id: Optional[str] = None
    extra_params: Dict[str, Any] = Field(default_factory=dict)


# ============================================================================
# Prediction Logic
# ============================================================================

def simple_linear_forecast(
    dates: List[datetime],
    values: List[float],
    days_ahead: int = 7
) -> tuple[List[PredictionPoint], float]:
    """
    Simple linear regression forecast.
    
    Args:
        dates: Historical dates
        values: Historical index values
        days_ahead: Number of days to forecast
        
    Returns:
        Tuple of (predictions list, R² score)
    """
    if len(dates) < 3:
        raise ValueError("Need at least 3 data points for prediction")
    
    # Convert dates to ordinal for regression
    X = np.array([d.toordinal() for d in dates]).reshape(-1, 1)
    y = np.array(values)
    
    # Fit model
    model = LinearRegression()
    model.fit(X, y)
    r2 = model.score(X, y)
    
    # Calculate residual std for confidence intervals
    y_pred = model.predict(X)
    residuals = y - y_pred
    std_residual = np.std(residuals)
    
    # Generate predictions
    last_date = max(dates)
    predictions = []
    
    for i in range(1, days_ahead + 1):
        future_date = last_date + timedelta(days=i)
        X_future = np.array([[future_date.toordinal()]])
        pred_value = float(model.predict(X_future)[0])
        
        # Clip to valid NDVI range
        pred_value = max(-1.0, min(1.0, pred_value))
        
        predictions.append(PredictionPoint(
            date=future_date.isoformat()[:10],
            predicted_value=round(pred_value, 4),
            confidence_lower=round(max(-1, pred_value - 1.96 * std_residual), 4),
            confidence_upper=round(min(1, pred_value + 1.96 * std_residual), 4)
        ))
    
    return predictions, r2


# ============================================================================
# API Endpoints
# ============================================================================

@router.get("/{entity_id}", response_model=PredictionResponse)
async def get_prediction(
    entity_id: str,
    index_type: str = Query("NDVI", description="Vegetation index type"),
    days_ahead: int = Query(7, ge=1, le=30, description="Days to forecast"),
    months_history: int = Query(12, ge=3, le=36, description="Months of training data"),
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_for_tenant)
):
    """
    Generate vegetation index prediction for an entity.
    
    Uses Linear Regression on historical data to forecast future values.
    Response is designed for N8N webhook integration.
    
    **N8N Integration**: Use this endpoint as a webhook source.
    The `webhook_metadata` field can be extended for workflow context.
    """
    from sqlalchemy import and_, desc
    from datetime import date
    
    # Calculate date range
    end_date = date.today()
    start_date = end_date - timedelta(days=months_history * 30)
    
    # Query historical stats
    results = db.query(
        VegetationScene.sensing_date,
        VegetationIndexCache.mean_value
    ).join(
        VegetationIndexCache,
        and_(
            VegetationIndexCache.scene_id == VegetationScene.id,
            VegetationIndexCache.entity_id == entity_id,
            VegetationIndexCache.index_type == index_type,
            VegetationIndexCache.tenant_id == current_user['tenant_id']
        )
    ).filter(
        VegetationScene.tenant_id == current_user['tenant_id'],
        VegetationScene.sensing_date >= start_date,
        VegetationScene.sensing_date <= end_date,
        VegetationScene.is_valid == True,
        VegetationIndexCache.mean_value.isnot(None)
    ).order_by(
        VegetationScene.sensing_date
    ).all()
    
    if len(results) < 3:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient data for prediction. Found {len(results)} points, need at least 3."
        )
    
    # Extract data
    dates = [r.sensing_date for r in results]
    values = [float(r.mean_value) for r in results]
    
    # Generate prediction
    try:
        predictions, r2 = simple_linear_forecast(dates, values, days_ahead)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prediction failed: {str(e)}")
    
    return PredictionResponse(
        entity_id=entity_id,
        index_type=index_type,
        model_type="linear_regression",
        training_samples=len(results),
        r2_score=round(r2, 4),
        predictions=predictions,
        webhook_metadata={
            "trigger": "api_call",
            "source": "vegetation-prime",
            "n8n_compatible": True,
            "intelligence_module_ready": True  # Flag for future AI module integration
        }
    )


@router.post("/{entity_id}/trigger-webhook")
async def trigger_prediction_webhook(
    entity_id: str,
    request: WebhookTriggerRequest,
    index_type: str = Query("NDVI"),
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_for_tenant)
):
    """
    Trigger prediction calculation and send to N8N webhook.
    
    This endpoint is designed for scheduled automation via N8N workflows.
    """
    import httpx
    
    # Generate prediction
    prediction = await get_prediction(
        entity_id=entity_id,
        index_type=index_type,
        days_ahead=7,
        months_history=12,
        current_user=current_user,
        db=db
    )
    
    # Enrich webhook metadata
    prediction.webhook_metadata.update({
        "trigger": "webhook",
        "workflow_id": request.workflow_id,
        **request.extra_params
    })
    
    # Send to callback if provided
    if request.callback_url:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    request.callback_url,
                    json=prediction.dict(),
                    timeout=10.0
                )
                return {
                    "status": "sent",
                    "callback_status": response.status_code,
                    "prediction": prediction
                }
        except Exception as e:
            return {
                "status": "callback_failed",
                "error": str(e),
                "prediction": prediction
            }
    
    return {"status": "generated", "prediction": prediction}
