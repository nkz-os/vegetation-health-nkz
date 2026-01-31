"""
Anomaly detection service for vegetation health monitoring.

Detects significant changes in vegetation indices and triggers alerts
via webhooks (N8N compatible) or internal notifications.
"""

import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum
import numpy as np
import httpx

logger = logging.getLogger(__name__)


class AnomalyType(str, Enum):
    """Types of vegetation anomalies."""
    NDVI_DROP = "ndvi_drop"           # Sudden NDVI decrease
    NDVI_SPIKE = "ndvi_spike"         # Unexpected NDVI increase
    MOISTURE_STRESS = "moisture_stress"  # Low NDMI/NDWI
    CHLOROPHYLL_DROP = "chlorophyll_drop"  # Low CIre/GNDVI
    COVERAGE_LOSS = "coverage_loss"   # Significant area affected
    SEASONAL_ANOMALY = "seasonal_anomaly"  # Deviation from seasonal norm


class AlertSeverity(str, Enum):
    """Alert severity levels."""
    INFO = "info"       # Informational, no action needed
    WARNING = "warning"  # Attention recommended
    CRITICAL = "critical"  # Immediate action required


@dataclass
class Anomaly:
    """Represents a detected anomaly."""
    anomaly_type: AnomalyType
    severity: AlertSeverity
    entity_id: str
    index_type: str
    current_value: float
    previous_value: float
    change_percent: float
    threshold: float
    detected_at: datetime
    area_affected_percent: Optional[float] = None
    recommendation: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "anomaly_type": self.anomaly_type.value,
            "severity": self.severity.value,
            "entity_id": self.entity_id,
            "index_type": self.index_type,
            "current_value": self.current_value,
            "previous_value": self.previous_value,
            "change_percent": self.change_percent,
            "threshold": self.threshold,
            "detected_at": self.detected_at.isoformat(),
            "area_affected_percent": self.area_affected_percent,
            "recommendation": self.recommendation,
            "metadata": self.metadata or {}
        }


class AnomalyDetector:
    """
    Detects anomalies in vegetation time series data.

    Uses multiple detection methods:
    1. Threshold-based: Simple percentage change detection
    2. Statistical: Z-score based outlier detection
    3. Seasonal: Comparison with historical seasonal patterns
    """

    # Default thresholds (configurable per tenant)
    DEFAULT_THRESHOLDS = {
        "ndvi_drop_warning": -0.10,      # -10% NDVI drop
        "ndvi_drop_critical": -0.20,     # -20% NDVI drop
        "moisture_warning": 0.0,         # NDMI below 0
        "moisture_critical": -0.2,       # NDMI below -0.2
        "chlorophyll_warning": -0.15,    # -15% CIre drop
        "chlorophyll_critical": -0.25,   # -25% CIre drop
        "z_score_threshold": 2.5,        # Standard deviations
        "min_observations": 5,           # Minimum data points for stats
    }

    def __init__(self, thresholds: Optional[Dict[str, float]] = None):
        self.thresholds = {**self.DEFAULT_THRESHOLDS, **(thresholds or {})}

    def detect_threshold_anomaly(
        self,
        entity_id: str,
        index_type: str,
        current_value: float,
        previous_value: float,
        timestamp: datetime
    ) -> Optional[Anomaly]:
        """
        Detect anomaly based on simple threshold comparison.

        Args:
            entity_id: NGSI-LD entity ID
            index_type: Vegetation index type (NDVI, NDMI, etc.)
            current_value: Current index value
            previous_value: Previous index value
            timestamp: Detection timestamp

        Returns:
            Anomaly if detected, None otherwise
        """
        if previous_value == 0:
            return None

        change_percent = (current_value - previous_value) / abs(previous_value)

        # Check for NDVI drop
        if index_type.upper() in ['NDVI', 'EVI', 'SAVI', 'MSAVI']:
            if change_percent <= self.thresholds["ndvi_drop_critical"]:
                return Anomaly(
                    anomaly_type=AnomalyType.NDVI_DROP,
                    severity=AlertSeverity.CRITICAL,
                    entity_id=entity_id,
                    index_type=index_type,
                    current_value=current_value,
                    previous_value=previous_value,
                    change_percent=change_percent * 100,
                    threshold=self.thresholds["ndvi_drop_critical"] * 100,
                    detected_at=timestamp,
                    recommendation="Inspeccionar el cultivo inmediatamente. Posible estrés severo, enfermedad o daño."
                )
            elif change_percent <= self.thresholds["ndvi_drop_warning"]:
                return Anomaly(
                    anomaly_type=AnomalyType.NDVI_DROP,
                    severity=AlertSeverity.WARNING,
                    entity_id=entity_id,
                    index_type=index_type,
                    current_value=current_value,
                    previous_value=previous_value,
                    change_percent=change_percent * 100,
                    threshold=self.thresholds["ndvi_drop_warning"] * 100,
                    detected_at=timestamp,
                    recommendation="Vigilar la parcela. Considerar inspección visual si persiste."
                )

        # Check for moisture stress
        elif index_type.upper() in ['NDMI', 'NDWI']:
            if current_value <= self.thresholds["moisture_critical"]:
                return Anomaly(
                    anomaly_type=AnomalyType.MOISTURE_STRESS,
                    severity=AlertSeverity.CRITICAL,
                    entity_id=entity_id,
                    index_type=index_type,
                    current_value=current_value,
                    previous_value=previous_value,
                    change_percent=change_percent * 100,
                    threshold=self.thresholds["moisture_critical"],
                    detected_at=timestamp,
                    recommendation="Estrés hídrico severo detectado. Considerar riego urgente."
                )
            elif current_value <= self.thresholds["moisture_warning"]:
                return Anomaly(
                    anomaly_type=AnomalyType.MOISTURE_STRESS,
                    severity=AlertSeverity.WARNING,
                    entity_id=entity_id,
                    index_type=index_type,
                    current_value=current_value,
                    previous_value=previous_value,
                    change_percent=change_percent * 100,
                    threshold=self.thresholds["moisture_warning"],
                    detected_at=timestamp,
                    recommendation="Contenido de agua bajo. Monitorear y planificar riego si es necesario."
                )

        # Check for chlorophyll drop
        elif index_type.upper() in ['CIRE', 'GNDVI', 'NDRE']:
            if change_percent <= self.thresholds["chlorophyll_critical"]:
                return Anomaly(
                    anomaly_type=AnomalyType.CHLOROPHYLL_DROP,
                    severity=AlertSeverity.CRITICAL,
                    entity_id=entity_id,
                    index_type=index_type,
                    current_value=current_value,
                    previous_value=previous_value,
                    change_percent=change_percent * 100,
                    threshold=self.thresholds["chlorophyll_critical"] * 100,
                    detected_at=timestamp,
                    recommendation="Caída significativa de clorofila. Posible deficiencia de nitrógeno o enfermedad."
                )
            elif change_percent <= self.thresholds["chlorophyll_warning"]:
                return Anomaly(
                    anomaly_type=AnomalyType.CHLOROPHYLL_DROP,
                    severity=AlertSeverity.WARNING,
                    entity_id=entity_id,
                    index_type=index_type,
                    current_value=current_value,
                    previous_value=previous_value,
                    change_percent=change_percent * 100,
                    threshold=self.thresholds["chlorophyll_warning"] * 100,
                    detected_at=timestamp,
                    recommendation="Contenido de clorofila reducido. Evaluar estado nutricional."
                )

        return None

    def detect_statistical_anomaly(
        self,
        entity_id: str,
        index_type: str,
        time_series: List[Tuple[datetime, float]],
        current_value: float,
        timestamp: datetime
    ) -> Optional[Anomaly]:
        """
        Detect anomaly using z-score based statistical analysis.

        Args:
            entity_id: NGSI-LD entity ID
            index_type: Vegetation index type
            time_series: Historical data points [(timestamp, value), ...]
            current_value: Current index value
            timestamp: Detection timestamp

        Returns:
            Anomaly if detected, None otherwise
        """
        if len(time_series) < self.thresholds["min_observations"]:
            return None

        values = np.array([v for _, v in time_series])
        mean = np.mean(values)
        std = np.std(values)

        if std == 0:
            return None

        z_score = (current_value - mean) / std

        if abs(z_score) >= self.thresholds["z_score_threshold"]:
            severity = AlertSeverity.CRITICAL if abs(z_score) >= 3.0 else AlertSeverity.WARNING
            anomaly_type = AnomalyType.NDVI_DROP if z_score < 0 else AnomalyType.NDVI_SPIKE

            return Anomaly(
                anomaly_type=anomaly_type,
                severity=severity,
                entity_id=entity_id,
                index_type=index_type,
                current_value=current_value,
                previous_value=mean,
                change_percent=((current_value - mean) / mean) * 100 if mean != 0 else 0,
                threshold=self.thresholds["z_score_threshold"],
                detected_at=timestamp,
                recommendation=f"Valor estadísticamente anómalo (z-score: {z_score:.2f}). Investigar causa.",
                metadata={
                    "z_score": z_score,
                    "historical_mean": mean,
                    "historical_std": std,
                    "observation_count": len(time_series)
                }
            )

        return None

    def analyze_parcel(
        self,
        entity_id: str,
        index_type: str,
        current_value: float,
        previous_value: Optional[float],
        time_series: Optional[List[Tuple[datetime, float]]] = None,
        timestamp: Optional[datetime] = None
    ) -> List[Anomaly]:
        """
        Run all anomaly detection methods on a parcel.

        Args:
            entity_id: NGSI-LD entity ID
            index_type: Vegetation index type
            current_value: Current index value
            previous_value: Previous observation value
            time_series: Historical data for statistical analysis
            timestamp: Detection timestamp

        Returns:
            List of detected anomalies (may be empty)
        """
        timestamp = timestamp or datetime.utcnow()
        anomalies = []

        # Threshold-based detection
        if previous_value is not None:
            anomaly = self.detect_threshold_anomaly(
                entity_id, index_type, current_value, previous_value, timestamp
            )
            if anomaly:
                anomalies.append(anomaly)

        # Statistical detection
        if time_series:
            anomaly = self.detect_statistical_anomaly(
                entity_id, index_type, time_series, current_value, timestamp
            )
            if anomaly:
                # Avoid duplicate alerts
                if not any(a.anomaly_type == anomaly.anomaly_type for a in anomalies):
                    anomalies.append(anomaly)

        return anomalies


class AlertService:
    """
    Service for sending alerts via webhooks and internal channels.

    Supports:
    - N8N webhooks
    - Generic HTTP webhooks
    - Internal notification queue
    """

    def __init__(self, webhook_timeout: float = 10.0):
        self.webhook_timeout = webhook_timeout

    async def send_webhook(
        self,
        webhook_url: str,
        anomaly: Anomaly,
        extra_data: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Send anomaly alert to webhook endpoint.

        Args:
            webhook_url: Webhook URL (N8N or generic)
            anomaly: Detected anomaly
            extra_data: Additional data to include

        Returns:
            True if successful, False otherwise
        """
        payload = {
            "event": "vegetation_anomaly",
            "timestamp": datetime.utcnow().isoformat(),
            "anomaly": anomaly.to_dict(),
            "platform": "nekazari",
            "module": "vegetation-prime",
            **(extra_data or {})
        }

        try:
            async with httpx.AsyncClient(timeout=self.webhook_timeout) as client:
                response = await client.post(
                    webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )
                response.raise_for_status()
                logger.info(f"Webhook sent successfully to {webhook_url}")
                return True

        except httpx.HTTPError as e:
            logger.error(f"Webhook failed: {e}")
            return False

    def format_alert_message(self, anomaly: Anomaly, lang: str = "es") -> str:
        """
        Format anomaly as human-readable message.

        Args:
            anomaly: Detected anomaly
            lang: Language code (es, en)

        Returns:
            Formatted message string
        """
        if lang == "es":
            severity_labels = {
                AlertSeverity.INFO: "INFO",
                AlertSeverity.WARNING: "AVISO",
                AlertSeverity.CRITICAL: "ALERTA"
            }
            type_labels = {
                AnomalyType.NDVI_DROP: "Caída de NDVI",
                AnomalyType.NDVI_SPIKE: "Pico anómalo de NDVI",
                AnomalyType.MOISTURE_STRESS: "Estrés hídrico",
                AnomalyType.CHLOROPHYLL_DROP: "Caída de clorofila",
                AnomalyType.COVERAGE_LOSS: "Pérdida de cobertura",
                AnomalyType.SEASONAL_ANOMALY: "Anomalía estacional"
            }

            return f"""
🌱 {severity_labels[anomaly.severity]}: {type_labels[anomaly.anomaly_type]}

📍 Parcela: {anomaly.entity_id}
📊 Índice: {anomaly.index_type}
📉 Valor actual: {anomaly.current_value:.3f}
📈 Valor anterior: {anomaly.previous_value:.3f}
📐 Cambio: {anomaly.change_percent:+.1f}%

💡 Recomendación: {anomaly.recommendation or 'Revisar la parcela'}

🕐 Detectado: {anomaly.detected_at.strftime('%Y-%m-%d %H:%M')}
            """.strip()

        else:  # English
            severity_labels = {
                AlertSeverity.INFO: "INFO",
                AlertSeverity.WARNING: "WARNING",
                AlertSeverity.CRITICAL: "ALERT"
            }
            type_labels = {
                AnomalyType.NDVI_DROP: "NDVI Drop",
                AnomalyType.NDVI_SPIKE: "Anomalous NDVI Spike",
                AnomalyType.MOISTURE_STRESS: "Moisture Stress",
                AnomalyType.CHLOROPHYLL_DROP: "Chlorophyll Drop",
                AnomalyType.COVERAGE_LOSS: "Coverage Loss",
                AnomalyType.SEASONAL_ANOMALY: "Seasonal Anomaly"
            }

            return f"""
🌱 {severity_labels[anomaly.severity]}: {type_labels[anomaly.anomaly_type]}

📍 Parcel: {anomaly.entity_id}
📊 Index: {anomaly.index_type}
📉 Current: {anomaly.current_value:.3f}
📈 Previous: {anomaly.previous_value:.3f}
📐 Change: {anomaly.change_percent:+.1f}%

💡 Recommendation: {anomaly.recommendation or 'Review the parcel'}

🕐 Detected: {anomaly.detected_at.strftime('%Y-%m-%d %H:%M')}
            """.strip()


# Singleton instances
anomaly_detector = AnomalyDetector()
alert_service = AlertService()
