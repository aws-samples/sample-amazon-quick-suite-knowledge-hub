"""
Fraud Detection Tool
===================
Analyzes insurance claims for potential fraud indicators using statistical analysis and pattern recognition.

Key Features:
- Multi-factor fraud scoring based on claim amounts, timing, and patterns
- Driver age and vehicle age risk assessment
- Medical vs property damage ratio analysis
- Text analysis for fraud-related keywords
- Historical comparison and anomaly detection

Returns fraud probability scores and detailed risk factors for each claim.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd
from utils.constants import (
    DEFAULT_FRAUD_CONFIG,
    FRAUD_KEYWORDS,
    LITIGATION_KEYWORDS,
)

# Set up logging
# Set root logger level explicitly
logging.getLogger().setLevel(logging.INFO)

# Get logger for this module
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@dataclass
class FraudScore:
    claim_id: str
    fraud_probability: float
    risk_factors: list[str]
    anomaly_score: float
    red_flags: list[str]


class FraudDetectionService:
    def __init__(self, fraud_config=None):
        self.config = fraud_config or DEFAULT_FRAUD_CONFIG
        self.fraud_indicators = {
            "amount_anomalies": ["unusually_high_amount", "round_number_amount"],
            "timing_anomalies": ["weekend_claim", "holiday_claim", "quick_report"],
            "pattern_anomalies": [
                "duplicate_amounts",
                "similar_claims",
                "frequent_claimant",
            ],
            "behavioral_anomalies": ["multiple_policies", "recent_policy_change"],
        }

    def _calculate_fraud_score(self, claim: pd.Series) -> FraudScore:
        risk_factors = []
        red_flags = []
        score = 0.0

        paid = float(claim.get("paidtotal") or 0)
        incurred = float(claim.get("totalincurred") or 0)

        if paid > 0 and paid % self.config["amount_thresholds"]["low"] == 0:
            risk_factors.append("round_number_amount")
            red_flags.append(f"Round number amount: ${paid:,.0f}")
            score += self.config["score_weights"]["amount_anomaly"]

        if paid > self.config["amount_thresholds"]["high"]:
            risk_factors.append("moderately_high_amount")
            red_flags.append(f"High claim amount: ${paid:,.0f}")
            score += self.config["score_weights"]["amount_anomaly"]

        if paid > self.config["amount_thresholds"]["very_high"]:
            risk_factors.append("unusually_high_amount")
            red_flags.append(f"Very high claim amount: ${paid:,.0f}")
            score += self.config["score_weights"]["amount_anomaly"]

        med = float(claim.get("medpdtotal") or 0)
        if incurred > 0:
            med_share = med / incurred
            if (
                med_share > self.config["ratios"]["medical_share_high"]
                and incurred > self.config["amount_thresholds"]["medium"]
            ):
                risk_factors.append("high_medical_share")
                red_flags.append(f"High medical share: {med_share:.2f}")
                score += self.config["score_weights"]["ratio_anomaly"]

        try:
            age = int(claim.get("driverage") or 0)
        except (ValueError, TypeError):
            age = 0

        if age and (
            age < self.config["age_thresholds"]["young_driver"]
            or age > self.config["age_thresholds"]["senior_driver"]
        ):
            risk_factors.append("high_risk_driver_age")
            red_flags.append(f"High-risk driver age: {age}")
            score += self.config["score_weights"]["demographic_anomaly"]

        try:
            vehicle_year = int(claim.get("vehicleyear") or 0)
        except (ValueError, TypeError):
            vehicle_year = 0

        if vehicle_year:
            current_year = datetime.utcnow().year
            vehicle_age = max(0, current_year - vehicle_year)
            if (
                vehicle_age < self.config["vehicle_thresholds"]["new_vehicle"]
                and incurred > self.config["amount_thresholds"]["high"]
            ):
                risk_factors.append("new_vehicle_high_severity")
                red_flags.append(f"High severity on new vehicle (age {vehicle_age})")
                score += self.config["score_weights"]["pattern_anomaly"]
            if (
                vehicle_age > self.config["vehicle_thresholds"]["old_vehicle"]
                and paid > self.config["amount_thresholds"]["medium"]
            ):
                risk_factors.append("old_vehicle_high_payout")
                red_flags.append(f"High payout on old vehicle (age {vehicle_age})")
                score += self.config["score_weights"]["pattern_anomaly"]

        body_part = str(claim.get("bodypartproductcode") or "").upper()
        losstype = str(claim.get("losstype") or "").upper()
        injury_desc = str(claim.get("injurydescription") or "").lower()

        severe_body_parts = {"HEAD", "L2", "SPINE", "BACK"}
        if body_part in severe_body_parts or any(
            w in injury_desc for w in ["head", "spine", "back", "neck"]
        ):
            if incurred > 10000:
                risk_factors.append("severe_injury_high_cost")
                red_flags.append("Severe injury with high cost")
                score += self.config["score_weights"]["severe_injury"]

        soft_tissue_terms = ["whiplash", "soft tissue", "sprain", "strain"]
        if any(w in injury_desc for w in soft_tissue_terms) and incurred > 5000:
            risk_factors.append("soft_tissue_high_cost")
            red_flags.append("Soft tissue injury with high cost")
            score += self.config["score_weights"]["soft_tissue"]

        if "3PTY" in losstype and incurred > 25000:
            risk_factors.append("third_party_bi_high_severity")
            red_flags.append("High-severity third-party BI claim")
            score += self.config["score_weights"]["third_party_bi"]

        text = " ".join(
            [
                str(claim.get("note_text") or ""),
                str(claim.get("lossdescription") or ""),
                str(claim.get("injurydescription") or ""),
            ]
        ).lower()

        if any(w in text for w in FRAUD_KEYWORDS):
            risk_factors.append("fraud_keywords")
            red_flags.append("Fraud-related keywords in notes")
            score += self.config["score_weights"]["keyword_match"]

        if any(w in text for w in LITIGATION_KEYWORDS):
            risk_factors.append("litigation_keywords")
            red_flags.append("Litigation keywords in notes")
            score += self.config["score_weights"]["keyword_match"]

        if any(w in text for w in ["total loss", "write off", "beyond repair"]):
            risk_factors.append("total_loss_language")
            red_flags.append("Total loss language")
            score += self.config["score_weights"]["total_loss"]

        if any(
            w in text for w in ["fog", "black ice", "heavy rain", "hail", "snowstorm"]
        ):
            if incurred > 10000:
                risk_factors.append("severe_weather_high_cost")
                red_flags.append("Weather narrative with high cost")
                score += 0.1

        anomaly_score = self._calculate_anomaly_score(claim)
        if anomaly_score > 0:
            risk_factors.append("paid_incurred_ratio_anomaly")
            red_flags.append(f"Unusual paid/incurred ratio: {anomaly_score:.2f}")
        score += anomaly_score * 0.3

        fraud_probability = min(1.0, score)

        return FraudScore(
            claim_id=str(
                claim.get("claimnumber") or claim.get("claim_number") or "unknown"
            ),
            fraud_probability=fraud_probability,
            risk_factors=risk_factors,
            anomaly_score=anomaly_score,
            red_flags=red_flags,
        )

    def _calculate_anomaly_score(self, claim: pd.Series) -> float:
        try:
            paid = float(claim.get("paidtotal") or 0)
            incurred = float(claim.get("totalincurred") or 0)
            if incurred <= 0:
                return 0.0
            ratio = paid / incurred
            if ratio > 1.0 or ratio < 0.3:
                return min(1.0, abs(ratio - 0.75) * 2.0)
            return 0.0
        except (ValueError, TypeError, ZeroDivisionError):
            return 0.0

    def _detect_organized_fraud(
        self, df: pd.DataFrame, fraud_scores: list[dict[str, Any]]
    ) -> dict[str, Any]:
        organized_indicators = []
        try:
            if "paidtotal" in df.columns:
                amounts = pd.to_numeric(df["paidtotal"], errors="coerce").fillna(0)
                counts = amounts.value_counts()
                for amount, count in counts.items():
                    if count >= 3 and amount > 1000:
                        organized_indicators.append(
                            {
                                "type": "duplicate_amounts",
                                "description": f"{count} claims with identical amount: ${amount:,.0f}",
                                "severity": "high" if count >= 5 else "medium",
                            }
                        )

            high_fraud_claims = [
                s for s in fraud_scores if s.get("fraud_probability", 0) > 0.7
            ]
            if len(high_fraud_claims) >= 3:
                organized_indicators.append(
                    {
                        "type": "high_fraud_cluster",
                        "description": f"{len(high_fraud_claims)} claims with high fraud probability",
                        "severity": "high",
                    }
                )

        except Exception as e:
            organized_indicators.append(
                {
                    "type": "analysis_error",
                    "description": f"Error in organized fraud detection: {str(e)}",
                    "severity": "low",
                }
            )

        return {
            "indicators": organized_indicators,
            "total_indicators": len(organized_indicators),
            "high_severity_count": sum(
                1 for i in organized_indicators if i.get("severity") == "high"
            ),
        }


def score_fraud_risk(data, fraud_config=None):
    """
    Analyze claims data for fraud indicators and return risk scores.

    Args:
        data: Claims data (list of dictionaries or DataFrame)
        fraud_config: Optional fraud configuration overrides
    """
    try:
        if not data:
            return {
                "fraud_scores": [],
                "ranked_claims": [],
                "organized_fraud_indicators": {},
                "summary": {
                    "total_claims": 0,
                    "high_risk_claims": 0,
                    "medium_risk_claims": 0,
                    "flagged_claims": 0,
                    "average_fraud_score": 0.0,
                },
            }

        if isinstance(data, dict) and "data" in data:
            claims_list = data["data"]
        else:
            claims_list = data

        if not isinstance(claims_list, list):
            claims_list = [claims_list]

        df = pd.DataFrame(claims_list)
        total_claims = len(df)

        service = FraudDetectionService(fraud_config)

        all_scores = []
        for _, row in df.iterrows():
            score_obj = service._calculate_fraud_score(row)
            all_scores.append(score_obj.__dict__)

        ranked_all = sorted(
            all_scores, key=lambda x: x.get("fraud_probability", 0.0), reverse=True
        )
        fraud_scores = ranked_all[:50]
        ranked_claims = fraud_scores

        organized_fraud = service._detect_organized_fraud(df, all_scores)

        if all_scores:
            avg_score = sum(s.get("fraud_probability", 0.0) for s in all_scores) / len(
                all_scores
            )
            high = sum(1 for s in all_scores if s.get("fraud_probability", 0.0) > 0.7)
            med = sum(
                1 for s in all_scores if 0.3 < s.get("fraud_probability", 0.0) <= 0.7
            )
        else:
            avg_score = high = med = 0

        return {
            "fraud_scores": fraud_scores,
            "ranked_claims": ranked_claims,
            "organized_fraud_indicators": organized_fraud,
            "summary": {
                "total_claims": total_claims,
                "high_risk_claims": high,
                "medium_risk_claims": med,
                "flagged_claims": len(
                    [s for s in all_scores if s.get("fraud_probability", 0.0) > 0.3]
                ),
                "average_fraud_score": avg_score,
            },
        }

    except Exception as e:
        return {
            "error": f"Failed to analyze fraud risk: {str(e)}",
            "fraud_scores": [],
            "ranked_claims": [],
            "organized_fraud_indicators": {},
            "summary": {
                "total_claims": 0,
                "high_risk_claims": 0,
                "medium_risk_claims": 0,
                "flagged_claims": 0,
                "average_fraud_score": 0.0,
            },
        }
