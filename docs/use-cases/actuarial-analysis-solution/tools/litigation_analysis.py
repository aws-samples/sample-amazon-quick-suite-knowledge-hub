"""
Litigation Analysis Tool
=======================
Detects litigation indicators in insurance claims using natural language processing and pattern matching.

Key Features:
- Keyword-based litigation detection (attorney, lawyer, lawsuit, etc.)
- Strong signal identification (represented by counsel, retained attorney)
- Confidence scoring based on litigation indicators
- Text analysis of claim notes and descriptions
- Friction and dispute pattern recognition

Returns litigation probability and confidence scores with detailed indicators.
"""

import logging
from dataclasses import dataclass
from typing import Any

from utils.constants import (
    DEFAULT_LITIGATION_CONFIG,
    LITIGATION_KEYWORDS,
    LITIGATION_STRONG_SIGNALS,
)

# Set up logging
# Set root logger level explicitly
logging.getLogger().setLevel(logging.INFO)

# Get logger for this module
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@dataclass
class LitigationSignal:
    claim_id: str
    has_litigation: bool
    has_high_friction: bool
    confidence_score: float
    indicators: list[str]


class LitigationAnalysisService:
    def __init__(self, litigation_config=None):
        self.config = litigation_config or DEFAULT_LITIGATION_CONFIG
        self.generic_keywords = LITIGATION_KEYWORDS + [
            "dispute",
            "denied",
            "denial",
            "appeal",
            "complaint",
            "coverage issue",
            "coverage dispute",
            "bad faith",
            "investigation",
        ]

        self.rep_terms = LITIGATION_STRONG_SIGNALS

        self.suit_terms = [
            "lawsuit filed",
            "has filed a lawsuit",
            "filed suit",
            "filed a suit",
            "filed a law suit",
            "complaint filed",
            "filed complaint",
            "civil complaint",
            "civil action",
            "statement of claim",
            "summons and complaint",
            "served with summons",
            "served with complaint",
            "served with papers",
            "service of process completed",
            "service of process",
            "court case opened",
            "trial",
            "trial date",
            "going to trial",
            "scheduled for trial",
        ]

        self.friction_terms = [
            "claim denied",
            "denied claim",
            "denial of claim",
            "coverage denied",
            "coverage issue",
            "coverage dispute",
            "dispute claim",
            "disputed claim",
            "formal complaint",
            "filed a complaint",
            "escalated complaint",
            "ombudsman",
            "bad faith",
            "unfair settlement",
            "legal review",
            "legal department reviewing",
            "under investigation",
            "fraud investigation",
        ]

    def _litigation_confidence(self, text: str) -> float:
        t = text.lower()
        score = 0.0

        for kw in self.generic_keywords:
            if kw in t:
                score += 0.01

        rep_hit = any(p in t for p in self.rep_terms)
        suit_hit = any(p in t for p in self.suit_terms)
        strong_signal = rep_hit or suit_hit

        if rep_hit:
            score += self.config["score_weights"]["strong_signal_weight"]
        if suit_hit:
            score += self.config["score_weights"]["strong_signal_weight"]

        if not strong_signal:
            return min(score, self.config["confidence_thresholds"]["low"])

        if "deposition" in t or "subpoena" in t or "interrogatories" in t:
            score += self.config["score_weights"]["weak_signal_weight"]
        if (
            "demand letter" in t
            or "settlement demand" in t
            or "policy limits demand" in t
        ):
            score += self.config["score_weights"]["weak_signal_weight"]

        return min(1.0, score)

    def score_one(self, claim: dict[str, Any]) -> LitigationSignal:
        text = " ".join(
            [
                str(claim.get("claimantname") or ""),
                str(claim.get("note_text") or ""),
                str(claim.get("lossdescription") or ""),
                str(claim.get("injurydescription") or ""),
            ]
        ).lower()

        conf = self._litigation_confidence(text)
        has_litigation = conf > self.config["confidence_thresholds"]["high"]
        has_high_friction = any(term in text for term in self.friction_terms)

        indicators = [kw for kw in self.generic_keywords if kw in text]

        return LitigationSignal(
            claim_id=str(claim.get("claimnumber") or claim.get("claim_id") or ""),
            has_litigation=has_litigation,
            has_high_friction=has_high_friction,
            confidence_score=conf,
            indicators=indicators,
        )


def analyze_litigation_signals(data, litigation_config=None):
    try:
        if not data:
            return {
                "signals": [],
                "summary": {
                    "total_claims": 0,
                    "strict_litigation_claims": 0,
                    "high_friction_claims": 0,
                    "either_strict_or_high_friction": 0,
                    "litigation_rate_strict": 0.0,
                    "litigation_rate_broad": 0.0,
                    "avg_litigation_confidence_strict": 0.0,
                },
            }

        if isinstance(data, dict) and "data" in data:
            claims_list = data["data"]
        else:
            claims_list = data

        if not isinstance(claims_list, list):
            claims_list = [claims_list]

        service = LitigationAnalysisService(litigation_config)
        signals = []
        total = 0

        for claim in claims_list:
            if not isinstance(claim, dict):
                continue
            total += 1
            res = service.score_one(claim)
            signals.append(res.__dict__)

        strict_flags = [s for s in signals if s["has_litigation"]]
        friction_flags = [s for s in signals if s["has_high_friction"]]
        broad_flags = [
            s for s in signals if s["has_litigation"] or s["has_high_friction"]
        ]

        strict_count = len(strict_flags)
        friction_count = len(friction_flags)
        broad_count = len(broad_flags)

        avg_conf_strict = (
            sum(s["confidence_score"] for s in strict_flags) / strict_count
            if strict_count
            else 0.0
        )

        return {
            "signals": signals,
            "summary": {
                "total_claims": total,
                "strict_litigation_claims": strict_count,
                "high_friction_claims": friction_count,
                "either_strict_or_high_friction": broad_count,
                "litigation_rate_strict": strict_count / total if total else 0.0,
                "litigation_rate_broad": broad_count / total if total else 0.0,
                "avg_litigation_confidence_strict": avg_conf_strict,
            },
        }

    except Exception as e:
        return {
            "error": f"Failed to analyze litigation signals: {str(e)}",
            "signals": [],
            "summary": {
                "total_claims": 0,
                "strict_litigation_claims": 0,
                "high_friction_claims": 0,
                "either_strict_or_high_friction": 0,
                "litigation_rate_strict": 0.0,
                "litigation_rate_broad": 0.0,
                "avg_litigation_confidence_strict": 0.0,
            },
        }


def detect_litigation(data, litigation_config=None):
    """
    Detect litigation indicators in claims data.

    Args:
        data: Claims data (list of dictionaries or DataFrame)
        litigation_config: Optional litigation configuration overrides
    """
    result = analyze_litigation_signals(data, litigation_config)

    if "error" in result:
        return {
            "error": result["error"],
            "litigation_flags": [],
            "summary": {
                "total_claims": 0,
                "litigation_claims": 0,
                "high_friction_claims": 0,
                "litigation_rate": 0.0,
            },
        }

    all_litigation_flags = [s for s in result["signals"] if s["has_litigation"]]
    all_friction_flags = [s for s in result["signals"] if s["has_high_friction"]]
    litigation_flags = all_litigation_flags[:100]

    return {
        "litigation_flags": litigation_flags,
        "high_friction_claims": all_friction_flags[:100],
        "summary": {
            "total_claims": result["summary"]["total_claims"],
            "litigation_claims": len(all_litigation_flags),
            "high_friction_claims": len(all_friction_flags),
            "litigation_rate": len(all_litigation_flags)
            / result["summary"]["total_claims"]
            if result["summary"]["total_claims"] > 0
            else 0.0,
            "friction_rate": len(all_friction_flags) / result["summary"]["total_claims"]
            if result["summary"]["total_claims"] > 0
            else 0.0,
        },
    }
