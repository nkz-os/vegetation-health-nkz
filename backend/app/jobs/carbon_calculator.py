#!/usr/bin/env python3
"""
DEPRECATED — Carbon logic has been extracted to nkz-module-carbon.
This file is kept temporarily to avoid breaking the Celery task registry.

Migration: nkz-module-carbon/backend/app/services/carbon_engine.py
Remove this file once the carbon module Celery worker is deployed and running.
"""

import sys
import os
import logging
from datetime import date
from typing import Dict, Any

# Add backend directory to sys.path
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from app.database import SessionLocal
from app.services.fiware_integration import FIWAREClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("carbon_calculator")

# Constants (LUE in gC/MJ)
LUE_VALUES = {
    "olive": 1.2,
    "vineyard": 1.0,
    "wheat": 1.5,
    "corn": 1.7,
    "default": 1.1
}

class CarbonCalculator:
    def __init__(self):
        url = os.getenv("FIWARE_CONTEXT_BROKER_URL", "http://orion-ld-service:1026")
        self.fiware = FIWAREClient(url, tenant_id="master")

    def calculate_daily_carbon(self, parcel: Dict[str, Any], ndvi: float, date_obs: date):
        """
        Calculate Daily GPP and accumulate CO2.
        """
        parcel_id = parcel['id']
        crop = parcel.get('cropSpecies', {}).get('value', 'default').lower()
        lue = LUE_VALUES.get(crop, LUE_VALUES['default'])

        # 1. Estimate fAPAR from NDVI (Simple linear model)
        # fAPAR approx = (NDVI - 0.2) / 0.8 roughly, clamped 0-1
        fapar = max(0.0, min(0.95, (ndvi - 0.2) * 1.25))

        # 2. Get PAR (Photosynthetically Active Radiation)
        # TODO: Connect to Weather API. For MVP, use daily avg ~ 20 MJ/m2 roughly in summer
        par = 20.0 

        # 3. Calculate GPP (gC/m2/day)
        gpp = par * fapar * lue
        
        # 4. Convert to CO2 (Net = GPP * 0.5 respiration discount)
        npp = gpp * 0.5
        
        # gC to gCO2 (x 3.664)
        co2_g_m2 = npp * 3.664
        
        # Total kg CO2 for parcel
        area = parcel.get('areaServed', {}).get('value', 1.0) # Hectares? Meters?
        # Assuming areaServed is Has, convert to m2 (x10000)
        # If areaServed is not present, check location area.
        area_m2 = area * 10000 
        
        daily_co2_kg = (co2_g_m2 * area_m2) / 1000
        
        logger.info(f"Parcel {parcel_id}: NDVI={ndvi:.2f} -> fAPAR={fapar:.2f} -> GPP={gpp:.2f} -> +{daily_co2_kg:.2f} kg CO2")
        
        # 5. Update Cumulative
        current_total = parcel.get('co2SequesteredTotal', {}).get('value', 0.0)
        new_total = current_total + daily_co2_kg
        
        self.fiware.update_entity({
            "id": parcel_id,
            "co2SequesteredTotal": {
                "type": "Property", 
                "value": new_total,
                "unitCode": "KGM",
                "observedAt": date_obs.isoformat()
            },
            "dailyGPP": {
                "type": "Property",
                "value": gpp,
                "unitCode": "G_M2",
                "observedAt": date_obs.isoformat()
            }
        })

if __name__ == "__main__":
    calc = CarbonCalculator()
    # Mock run
    # calc.calculate_daily_carbon(mock_parcel, 0.75, date.today())
