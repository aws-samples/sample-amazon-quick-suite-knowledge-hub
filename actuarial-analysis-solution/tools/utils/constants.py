# Actuarial Tools Constants
# Static constants and default configurations

# DEFAULT CONFIGURATIONS (used as fallbacks when no custom config provided)
DEFAULT_FRAUD_CONFIG = {
    "amount_thresholds": {
        "low": 1000,
        "medium": 5000,
        "high": 20000,
        "very_high": 50000,
    },
    "score_weights": {
        "amount_anomaly": 0.2,
        "pattern_anomaly": 0.3,
        "ratio_anomaly": 0.1,
        "demographic_anomaly": 0.08,
        "keyword_match": 0.1,
        "severe_injury": 0.15,
        "soft_tissue": 0.15,
        "third_party_bi": 0.15,
        "total_loss": 0.1,
    },
    "age_thresholds": {"young_driver": 25, "senior_driver": 70},
    "vehicle_thresholds": {"new_vehicle": 3, "old_vehicle": 15},
    "ratios": {"medical_share_high": 0.7},
}

DEFAULT_LITIGATION_CONFIG = {
    "confidence_thresholds": {"high": 0.7, "low": 0.15},
    "score_weights": {"strong_signal_weight": 0.7, "weak_signal_weight": 0.15},
    "limits": {"max_results": 100},
}

DEFAULT_MONITORING_CONFIG = {
    "alert_thresholds": {
        "development_factor_change": 0.15,
        "claim_frequency_spike": 2.0,
        "severity_increase": 0.25,
        "reserve_adequacy": 0.8,
    },
    "kpi_targets": {
        "avg_claim_cost": 15000,
        "claims_per_month": 100,
        "loss_ratio": 1.15,
        "expense_ratio": 0.20,
    },
    "development_factors": {
        "12_to_24": 1.15,
        "24_to_36": 1.3,
        "36_to_48": 1.15,
        "48_to_60": 1.08,
    },
    "time_periods": {"trend_days": 30, "min_claims": 100},
    "trend_thresholds": {"increase": 1.05, "decrease": 0.95},
}

# COMMON FIELD MAPPINGS
FIELD_MAPPINGS = {
    "DATE_FIELDS": ["accident_date", "report_date", "loss_date", "date_of_loss"],
    "AMOUNT_FIELDS": ["paidtotal", "totalincurred", "reservetotal", "claim_amount"],
    "REQUIRED_COLUMNS": ["claim_number", "accident_date", "totalincurred"],
}

# KEYWORD SETS
FRAUD_KEYWORDS = [
    "fraud",
    "staged",
    "suspicious",
    "exaggerated",
    "inflated",
    "questionable",
    "inconsistent",
    "fabricated",
]

LITIGATION_KEYWORDS = [
    "attorney",
    "lawyer",
    "legal",
    "lawsuit",
    "litigation",
    "court",
    "settlement",
    "deposition",
    "subpoena",
    "trial",
    "plaintiff",
    "defendant",
    "counsel",
    "sue",
    "suing",
    "sued",
]

LITIGATION_STRONG_SIGNALS = [
    "represented by counsel",
    "represented by an attorney",
    "represented by attorney",
    "has an attorney",
    "has attorney",
    "attorney involved",
    "legal representation",
    "hired an attorney",
    "has hired an attorney",
    "retained counsel",
    "retained an attorney",
    "has retained counsel",
    "plaintiff's counsel",
    "defense counsel",
    "their attorney",
    "my attorney",
    "insured's attorney",
    "claimant's attorney",
]

WEATHER_KEYWORDS = [
    "rain",
    "snow",
    "ice",
    "fog",
    "storm",
    "hail",
    "wind",
    "slippery",
    "wet",
    "icy",
    "weather",
]

BODY_PART_KEYWORDS = [
    "neck",
    "back",
    "spine",
    "head",
    "brain",
    "shoulder",
    "knee",
    "ankle",
    "wrist",
    "hip",
]

# AWS/SYSTEM CONSTANTS
AWS_CONFIG = {"WAIT_TIME": 30, "MAX_RESULTS": 100}
