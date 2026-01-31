"""
Weather data integration service for vegetation analysis.

Data sources (in priority order):
1. Nekazari Weather Module (internal API) - if available
2. IoT Soil Sensors (via Orion-LD) - real-time field data
3. Open-Meteo API (fallback) - free, no API key required

Provides weather context alongside vegetation indices for better
interpretation of crop health and planning.
"""

import logging
import os
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field
import httpx

logger = logging.getLogger(__name__)

# Configuration
WEATHER_MODULE_URL = os.getenv("WEATHER_MODULE_URL", "http://weather-worker:8000")
ORION_LD_URL = os.getenv("ORION_LD_URL", "http://orion-ld:1026")


@dataclass
class SoilSensorData:
    """Real-time soil sensor data from IoT devices."""
    sensor_id: str
    timestamp: datetime
    soil_moisture: Optional[float] = None  # % volumetric
    soil_temperature: Optional[float] = None  # °C
    soil_ec: Optional[float] = None  # dS/m (electrical conductivity)
    soil_ph: Optional[float] = None
    leaf_wetness: Optional[float] = None  # % or hours
    location: Optional[Dict[str, float]] = None  # {lat, lon}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sensor_id": self.sensor_id,
            "timestamp": self.timestamp.isoformat(),
            "soil_moisture": self.soil_moisture,
            "soil_temperature": self.soil_temperature,
            "soil_ec": self.soil_ec,
            "soil_ph": self.soil_ph,
            "leaf_wetness": self.leaf_wetness,
            "location": self.location,
            "data_source": "iot_sensor"
        }


@dataclass
class WeatherData:
    """Weather data for a specific date and location."""
    date: date
    temperature_max: float  # °C
    temperature_min: float  # °C
    temperature_mean: float  # °C
    precipitation: float  # mm
    precipitation_hours: float  # hours
    evapotranspiration: float  # mm (ET0)
    soil_moisture_0_10cm: Optional[float]  # m³/m³
    soil_moisture_10_40cm: Optional[float]  # m³/m³
    wind_speed_max: float  # km/h
    shortwave_radiation: float  # MJ/m² (for photosynthesis)
    data_source: str = "open_meteo"  # open_meteo, weather_module, iot_sensor

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "temperature": {
                "max": self.temperature_max,
                "min": self.temperature_min,
                "mean": self.temperature_mean,
                "unit": "°C"
            },
            "precipitation": {
                "total": self.precipitation,
                "hours": self.precipitation_hours,
                "unit": "mm"
            },
            "evapotranspiration": {
                "et0": self.evapotranspiration,
                "unit": "mm"
            },
            "soil_moisture": {
                "0_10cm": self.soil_moisture_0_10cm,
                "10_40cm": self.soil_moisture_10_40cm,
                "unit": "m³/m³"
            },
            "wind_speed_max": {
                "value": self.wind_speed_max,
                "unit": "km/h"
            },
            "radiation": {
                "shortwave": self.shortwave_radiation,
                "unit": "MJ/m²"
            }
        }


@dataclass
class WeatherSummary:
    """Weather summary for vegetation analysis."""
    period_start: date
    period_end: date
    total_precipitation: float  # mm
    avg_temperature: float  # °C
    total_evapotranspiration: float  # mm
    water_balance: float  # mm (precipitation - ET0)
    growing_degree_days: float  # GDD base 10°C
    frost_days: int  # days with min temp < 0°C
    heat_stress_days: int  # days with max temp > 35°C
    drought_index: float  # simplified drought indicator

    def to_dict(self) -> Dict[str, Any]:
        return {
            "period": {
                "start": self.period_start.isoformat(),
                "end": self.period_end.isoformat()
            },
            "precipitation": {
                "total": self.total_precipitation,
                "unit": "mm"
            },
            "temperature": {
                "average": self.avg_temperature,
                "unit": "°C"
            },
            "evapotranspiration": {
                "total": self.total_evapotranspiration,
                "unit": "mm"
            },
            "water_balance": {
                "value": self.water_balance,
                "unit": "mm",
                "interpretation": "positive=surplus, negative=deficit"
            },
            "growing_degree_days": {
                "value": self.growing_degree_days,
                "base": 10,
                "unit": "°C-days"
            },
            "stress_indicators": {
                "frost_days": self.frost_days,
                "heat_stress_days": self.heat_stress_days,
                "drought_index": self.drought_index
            }
        }


class WeatherService:
    """
    Weather data service with multiple data sources.

    Data Sources (priority order):
    1. Nekazari Weather Module - internal weather worker
    2. IoT Soil Sensors - real-time field data from Orion-LD
    3. Open-Meteo API - free fallback

    Provides:
    - Historical weather data
    - Weather forecasts
    - Real-time soil sensor data
    - Agricultural metrics (GDD, water balance, stress indicators)
    """

    BASE_URL = "https://api.open-meteo.com/v1"
    ARCHIVE_URL = "https://archive-api.open-meteo.com/v1"

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        self._client = None
        self.weather_module_url = WEATHER_MODULE_URL
        self.orion_url = ORION_LD_URL

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def get_from_weather_module(
        self,
        latitude: float,
        longitude: float,
        start_date: date,
        end_date: date,
        tenant_id: str
    ) -> Optional[List[WeatherData]]:
        """
        Try to get weather data from Nekazari Weather Module.

        Args:
            latitude, longitude: Location
            start_date, end_date: Date range
            tenant_id: Tenant ID for auth

        Returns:
            List of WeatherData or None if module unavailable
        """
        try:
            client = await self._get_client()
            response = await client.get(
                f"{self.weather_module_url}/api/weather/historical",
                params={
                    "lat": latitude,
                    "lon": longitude,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat()
                },
                headers={"X-Tenant-ID": tenant_id}
            )

            if response.status_code == 200:
                data = response.json()
                weather_list = []
                for day in data.get("daily", []):
                    weather_list.append(WeatherData(
                        date=date.fromisoformat(day["date"]),
                        temperature_max=day.get("temp_max", 0),
                        temperature_min=day.get("temp_min", 0),
                        temperature_mean=day.get("temp_mean", 0),
                        precipitation=day.get("precipitation", 0),
                        precipitation_hours=day.get("precip_hours", 0),
                        evapotranspiration=day.get("et0", 0),
                        soil_moisture_0_10cm=day.get("soil_moisture_10cm"),
                        soil_moisture_10_40cm=day.get("soil_moisture_40cm"),
                        wind_speed_max=day.get("wind_max", 0),
                        shortwave_radiation=day.get("radiation", 0),
                        data_source="weather_module"
                    ))
                logger.info(f"Got {len(weather_list)} days from Weather Module")
                return weather_list

        except Exception as e:
            logger.debug(f"Weather Module not available: {e}")

        return None

    async def get_soil_sensors_data(
        self,
        entity_id: str,
        tenant_id: str,
        hours: int = 24
    ) -> List[SoilSensorData]:
        """
        Get real-time soil sensor data from IoT devices via Orion-LD.

        Queries for SoilProbe entities linked to the parcel.

        Args:
            entity_id: AgriParcel entity ID
            tenant_id: Tenant ID
            hours: How many hours of data to retrieve

        Returns:
            List of SoilSensorData from IoT sensors
        """
        try:
            client = await self._get_client()

            # Query Orion-LD for SoilProbe devices in this parcel
            response = await client.get(
                f"{self.orion_url}/ngsi-ld/v1/entities",
                params={
                    "type": "SoilProbe",
                    "q": f"refParcel=={entity_id}",
                    "attrs": "soilMoisture,soilTemperature,soilEC,soilPH,leafWetness,location"
                },
                headers={
                    "Accept": "application/ld+json",
                    "NGSILD-Tenant": tenant_id
                }
            )

            if response.status_code == 200:
                entities = response.json()
                sensor_data = []

                for entity in entities:
                    sensor_data.append(SoilSensorData(
                        sensor_id=entity.get("id", "unknown"),
                        timestamp=datetime.fromisoformat(
                            entity.get("soilMoisture", {}).get("observedAt", datetime.utcnow().isoformat()).replace("Z", "+00:00")
                        ) if "soilMoisture" in entity else datetime.utcnow(),
                        soil_moisture=entity.get("soilMoisture", {}).get("value"),
                        soil_temperature=entity.get("soilTemperature", {}).get("value"),
                        soil_ec=entity.get("soilEC", {}).get("value"),
                        soil_ph=entity.get("soilPH", {}).get("value"),
                        leaf_wetness=entity.get("leafWetness", {}).get("value"),
                        location=entity.get("location", {}).get("value", {}).get("coordinates")
                    ))

                logger.info(f"Retrieved {len(sensor_data)} soil sensors for {entity_id}")
                return sensor_data

        except Exception as e:
            logger.debug(f"Could not fetch soil sensors: {e}")

        return []

    async def get_weather_with_sensors(
        self,
        latitude: float,
        longitude: float,
        entity_id: str,
        tenant_id: str,
        start_date: date,
        end_date: date
    ) -> Dict[str, Any]:
        """
        Get combined weather data from all available sources.

        Merges data from Weather Module, IoT sensors, and Open-Meteo.

        Args:
            latitude, longitude: Location
            entity_id: Parcel entity ID
            tenant_id: Tenant ID
            start_date, end_date: Date range

        Returns:
            Dict with weather data, sensor data, and source info
        """
        result = {
            "weather_data": [],
            "soil_sensors": [],
            "sources_used": [],
            "period": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat()
            }
        }

        # 1. Try Weather Module first
        weather = await self.get_from_weather_module(
            latitude, longitude, start_date, end_date, tenant_id
        )
        if weather:
            result["weather_data"] = [w.to_dict() for w in weather]
            result["sources_used"].append("weather_module")
        else:
            # 2. Fallback to Open-Meteo
            weather = await self.get_historical_weather(
                latitude, longitude, start_date, end_date
            )
            if weather:
                result["weather_data"] = [w.to_dict() for w in weather]
                result["sources_used"].append("open_meteo")

        # 3. Get IoT soil sensor data
        sensors = await self.get_soil_sensors_data(entity_id, tenant_id)
        if sensors:
            result["soil_sensors"] = [s.to_dict() for s in sensors]
            result["sources_used"].append("iot_sensors")

        return result

    async def get_historical_weather(
        self,
        latitude: float,
        longitude: float,
        start_date: date,
        end_date: date
    ) -> List[WeatherData]:
        """
        Get historical weather data for a location.

        Args:
            latitude: Location latitude
            longitude: Location longitude
            start_date: Start date for data
            end_date: End date for data

        Returns:
            List of WeatherData objects
        """
        client = await self._get_client()

        # Use archive API for dates older than 7 days
        days_ago = (date.today() - end_date).days
        base_url = self.ARCHIVE_URL if days_ago > 7 else self.BASE_URL

        params = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "daily": [
                "temperature_2m_max",
                "temperature_2m_min",
                "temperature_2m_mean",
                "precipitation_sum",
                "precipitation_hours",
                "et0_fao_evapotranspiration",
                "wind_speed_10m_max",
                "shortwave_radiation_sum"
            ],
            "timezone": "auto"
        }

        # Add soil moisture if using forecast API (not available in archive)
        if base_url == self.BASE_URL:
            params["daily"].extend([
                "soil_moisture_0_to_10cm_mean",
                "soil_moisture_10_to_40cm_mean"
            ])

        try:
            response = await client.get(
                f"{base_url}/forecast",
                params=params
            )
            response.raise_for_status()
            data = response.json()

            daily = data.get("daily", {})
            dates = daily.get("time", [])

            weather_list = []
            for i, date_str in enumerate(dates):
                weather_list.append(WeatherData(
                    date=date.fromisoformat(date_str),
                    temperature_max=daily.get("temperature_2m_max", [None])[i] or 0,
                    temperature_min=daily.get("temperature_2m_min", [None])[i] or 0,
                    temperature_mean=daily.get("temperature_2m_mean", [None])[i] or 0,
                    precipitation=daily.get("precipitation_sum", [None])[i] or 0,
                    precipitation_hours=daily.get("precipitation_hours", [None])[i] or 0,
                    evapotranspiration=daily.get("et0_fao_evapotranspiration", [None])[i] or 0,
                    soil_moisture_0_10cm=daily.get("soil_moisture_0_to_10cm_mean", [None])[i] if i < len(daily.get("soil_moisture_0_to_10cm_mean", [])) else None,
                    soil_moisture_10_40cm=daily.get("soil_moisture_10_to_40cm_mean", [None])[i] if i < len(daily.get("soil_moisture_10_to_40cm_mean", [])) else None,
                    wind_speed_max=daily.get("wind_speed_10m_max", [None])[i] or 0,
                    shortwave_radiation=daily.get("shortwave_radiation_sum", [None])[i] or 0
                ))

            logger.info(f"Retrieved {len(weather_list)} days of weather data for ({latitude}, {longitude})")
            return weather_list

        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch weather data: {e}")
            return []

    async def get_weather_forecast(
        self,
        latitude: float,
        longitude: float,
        days: int = 7
    ) -> List[WeatherData]:
        """
        Get weather forecast for upcoming days.

        Args:
            latitude: Location latitude
            longitude: Location longitude
            days: Number of forecast days (1-16)

        Returns:
            List of WeatherData objects for forecast period
        """
        end_date = date.today() + timedelta(days=days)
        return await self.get_historical_weather(
            latitude, longitude,
            date.today(), end_date
        )

    def calculate_summary(
        self,
        weather_data: List[WeatherData],
        gdd_base: float = 10.0
    ) -> WeatherSummary:
        """
        Calculate weather summary and agricultural metrics.

        Args:
            weather_data: List of daily weather data
            gdd_base: Base temperature for Growing Degree Days (default 10°C)

        Returns:
            WeatherSummary with aggregated metrics
        """
        if not weather_data:
            return None

        # Sort by date
        weather_data = sorted(weather_data, key=lambda x: x.date)

        # Calculate metrics
        total_precip = sum(w.precipitation for w in weather_data)
        total_et0 = sum(w.evapotranspiration for w in weather_data)
        avg_temp = sum(w.temperature_mean for w in weather_data) / len(weather_data)

        # Growing Degree Days (GDD)
        gdd = sum(max(0, w.temperature_mean - gdd_base) for w in weather_data)

        # Stress indicators
        frost_days = sum(1 for w in weather_data if w.temperature_min < 0)
        heat_days = sum(1 for w in weather_data if w.temperature_max > 35)

        # Simple drought index (water balance / ET0)
        water_balance = total_precip - total_et0
        drought_index = water_balance / total_et0 if total_et0 > 0 else 0

        return WeatherSummary(
            period_start=weather_data[0].date,
            period_end=weather_data[-1].date,
            total_precipitation=round(total_precip, 1),
            avg_temperature=round(avg_temp, 1),
            total_evapotranspiration=round(total_et0, 1),
            water_balance=round(water_balance, 1),
            growing_degree_days=round(gdd, 1),
            frost_days=frost_days,
            heat_stress_days=heat_days,
            drought_index=round(drought_index, 2)
        )

    def interpret_for_vegetation(
        self,
        summary: WeatherSummary,
        vegetation_change: float,
        lang: str = "es"
    ) -> Dict[str, Any]:
        """
        Interpret weather conditions in context of vegetation change.

        Args:
            summary: Weather summary
            vegetation_change: Recent NDVI change percentage
            lang: Language for interpretation

        Returns:
            Dict with interpretation and recommendations
        """
        factors = []
        recommendations = []

        if lang == "es":
            # Water balance interpretation
            if summary.water_balance < -20:
                factors.append("Déficit hídrico significativo")
                recommendations.append("Considerar riego suplementario")
            elif summary.water_balance < 0:
                factors.append("Ligero déficit hídrico")
            elif summary.water_balance > 50:
                factors.append("Exceso de agua")
                recommendations.append("Verificar drenaje de la parcela")

            # Temperature stress
            if summary.frost_days > 0:
                factors.append(f"{summary.frost_days} días de helada")
                recommendations.append("Evaluar daños por frío")
            if summary.heat_stress_days > 0:
                factors.append(f"{summary.heat_stress_days} días de estrés térmico")
                recommendations.append("Aumentar riego en días calurosos")

            # GDD context
            if summary.growing_degree_days < 50:
                factors.append("Acumulación térmica baja - crecimiento lento esperado")
            elif summary.growing_degree_days > 200:
                factors.append("Alta acumulación térmica - crecimiento rápido")

            # NDVI correlation
            if vegetation_change < -10 and summary.drought_index < -0.5:
                interpretation = "La caída del NDVI probablemente está relacionada con el estrés hídrico detectado."
            elif vegetation_change < -10 and summary.frost_days > 0:
                interpretation = "La caída del NDVI puede estar relacionada con daños por heladas."
            elif vegetation_change > 5 and summary.growing_degree_days > 100:
                interpretation = "El aumento del NDVI es consistente con las buenas condiciones de crecimiento."
            else:
                interpretation = "Las condiciones meteorológicas no explican completamente los cambios observados."

        else:  # English
            if summary.water_balance < -20:
                factors.append("Significant water deficit")
                recommendations.append("Consider supplementary irrigation")
            elif summary.water_balance < 0:
                factors.append("Slight water deficit")
            elif summary.water_balance > 50:
                factors.append("Water excess")
                recommendations.append("Check field drainage")

            if summary.frost_days > 0:
                factors.append(f"{summary.frost_days} frost days")
                recommendations.append("Assess cold damage")
            if summary.heat_stress_days > 0:
                factors.append(f"{summary.heat_stress_days} heat stress days")
                recommendations.append("Increase irrigation during hot days")

            if summary.growing_degree_days < 50:
                factors.append("Low thermal accumulation - slow growth expected")
            elif summary.growing_degree_days > 200:
                factors.append("High thermal accumulation - rapid growth")

            if vegetation_change < -10 and summary.drought_index < -0.5:
                interpretation = "NDVI drop is likely related to detected water stress."
            elif vegetation_change < -10 and summary.frost_days > 0:
                interpretation = "NDVI drop may be related to frost damage."
            elif vegetation_change > 5 and summary.growing_degree_days > 100:
                interpretation = "NDVI increase is consistent with good growing conditions."
            else:
                interpretation = "Weather conditions do not fully explain observed changes."

        return {
            "factors": factors,
            "recommendations": recommendations,
            "interpretation": interpretation,
            "weather_summary": summary.to_dict()
        }


# Singleton instance
weather_service = WeatherService()
