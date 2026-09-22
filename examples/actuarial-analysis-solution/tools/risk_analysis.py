"""
Risk Analysis Tool
=================
Performs comprehensive risk factor analysis and claim segmentation for insurance portfolios.

Key Features:
- Risk factor identification and correlation analysis
- Claim segmentation by severity, frequency, and characteristics
- Statistical analysis of risk drivers (age, vehicle type, geography)
- Trend analysis and pattern recognition
- Risk scoring and classification

Returns detailed risk profiles, segments, and statistical insights for portfolio management.
"""

import logging
import warnings
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# Set up logging
# Set root logger level explicitly
logging.getLogger().setLevel(logging.INFO)

# Get logger for this module
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@dataclass
class RiskFactor:
    factor_name: str
    segments: list[str]
    loss_ratios: dict[str, float]
    frequency_rates: dict[str, float]
    significance_score: float
    is_significant: bool


class RiskAnalysisService:
    def __init__(self):
        self.significance_threshold = (
            0.05  # p-value threshold for statistical significance
        )

    def analyze_risk_factors(self, claims_data: dict[str, Any]) -> dict[str, Any]:
        try:
            # Convert to DataFrame
            df = pd.DataFrame(
                claims_data["data"] if "data" in claims_data else claims_data
            )

            # Identify risk factors to analyze
            risk_factors = self._identify_risk_factors(df)

            # Analyze each risk factor
            factor_analyses = []
            for factor in risk_factors:
                analysis = self._analyze_single_factor(df, factor)
                factor_analyses.append(analysis.__dict__)

            # Rank factors by importance
            ranked_factors = sorted(
                factor_analyses, key=lambda x: x["significance_score"], reverse=True
            )

            # Generate overall risk insights
            risk_insights = self._generate_risk_insights(df, factor_analyses)

            return {
                "risk_factor_analyses": factor_analyses,
                "ranked_factors": ranked_factors,
                "risk_insights": risk_insights,
                "summary": {
                    "total_factors_analyzed": len(factor_analyses),
                    "significant_factors": sum(
                        1 for f in factor_analyses if f["is_significant"]
                    ),
                    "total_claims": len(df),
                },
            }

        except Exception as e:
            raise Exception(f"Failed to analyze risk factors: {str(e)}") from e

    def _identify_risk_factors(self, df: pd.DataFrame) -> list[str]:
        risk_factors = []

        # Categorical risk factors using actual CSV column names
        categorical_columns = [
            "lineofbusiness",
            "claimstatus",
            "losstype",
            "causeofloss",
            "garagestate",
            "accidentstate",
        ]
        for col in categorical_columns:
            if col in df.columns:
                risk_factors.append(col)

        # Derived risk factors from dates using actual CSV column names
        if "note_date" in df.columns:
            df["note_date"] = pd.to_datetime(df["note_date"], errors="coerce")
            df["accident_year"] = df["note_date"].dt.year
            df["accident_month"] = df["note_date"].dt.month
            df["accident_day_of_week"] = df["note_date"].dt.dayofweek
            df["is_weekend"] = df["accident_day_of_week"] >= 5

            risk_factors.extend(["accident_year", "accident_month", "is_weekend"])

        # Amount-based risk factors using actual CSV column names
        if "paidtotal" in df.columns:
            df["amount_category"] = pd.cut(
                df["paidtotal"],
                bins=[0, 1000, 5000, 25000, float("inf")],
                labels=["Small", "Medium", "Large", "Very Large"],
            )
            risk_factors.append("amount_category")

        return risk_factors

    def _analyze_single_factor(self, df: pd.DataFrame, factor: str) -> RiskFactor:
        if factor not in df.columns:
            return RiskFactor(
                factor_name=factor,
                segments=[],
                loss_ratios={},
                frequency_rates={},
                significance_score=0.0,
                is_significant=False,
            )

        # Get segments (unique values)
        segments = df[factor].dropna().unique().tolist()
        segments = [str(s) for s in segments]  # Convert to strings

        # Calculate loss ratios and frequency rates by segment
        loss_ratios = {}
        frequency_rates = {}
        segment_data = []

        for segment in segments:
            segment_df = df[df[factor] == segment]

            if len(segment_df) == 0:
                continue

            # Calculate loss ratio (paid amount / premium - simplified)
            # In real implementation, this would use actual premium data
            avg_paid = (
                segment_df["paidtotal"].mean()
                if "paidtotal" in segment_df.columns
                else 0
            )
            loss_ratios[segment] = avg_paid / 10000  # Simplified loss ratio

            # Calculate frequency rate (claims per unit exposure)
            frequency_rates[segment] = len(segment_df)  # Simplified frequency

            # Store data for statistical testing
            if "paidtotal" in segment_df.columns:
                segment_data.append(segment_df["paidtotal"].values)

        # Perform statistical significance test
        significance_score = self._calculate_statistical_significance(segment_data)
        is_significant = significance_score < self.significance_threshold

        return RiskFactor(
            factor_name=factor,
            segments=segments,
            loss_ratios=loss_ratios,
            frequency_rates=frequency_rates,
            significance_score=significance_score,
            is_significant=is_significant,
        )

    def _calculate_statistical_significance(
        self, segment_data: list[np.ndarray]
    ) -> float:
        try:
            if len(segment_data) < 2:
                return 1.0  # Not significant if less than 2 segments

            # Remove empty segments
            segment_data = [seg for seg in segment_data if len(seg) > 0]

            if len(segment_data) < 2:
                return 1.0

            # Simple variance-based significance test (replaces ANOVA/t-test)
            # Calculate coefficient of variation across segments
            means = [np.mean(seg) for seg in segment_data]
            overall_mean = np.mean(means)
            cv = np.std(means) / (overall_mean + 0.001)  # Coefficient of variation

            # Higher CV = more significant difference between segments
            # Convert to p-value-like score (0 = very significant, 1 = not significant)
            p_value = max(0.0, min(1.0, 1.0 - cv))
            return p_value

        except Exception:
            return 1.0  # Return non-significant if test fails

    def _generate_risk_insights(
        self, df: pd.DataFrame, factor_analyses: list[dict[str, Any]]
    ) -> dict[str, Any]:
        insights = {}

        # Identify highest risk segments
        high_risk_segments = []
        for analysis in factor_analyses:
            if analysis["is_significant"]:
                factor_name = analysis["factor_name"]
                loss_ratios = analysis["loss_ratios"]

                if loss_ratios:
                    max_segment = max(loss_ratios.keys(), key=lambda k: loss_ratios[k])
                    max_ratio = loss_ratios[max_segment]

                    high_risk_segments.append(
                        {
                            "factor": factor_name,
                            "segment": max_segment,
                            "loss_ratio": max_ratio,
                        }
                    )

        insights["high_risk_segments"] = sorted(
            high_risk_segments, key=lambda x: x["loss_ratio"], reverse=True
        )[:5]  # Top 5

        # Identify emerging patterns
        emerging_patterns = self._identify_emerging_patterns(df)
        insights["emerging_patterns"] = emerging_patterns

        # Calculate portfolio-level metrics
        portfolio_metrics = self._calculate_portfolio_metrics(df)
        insights["portfolio_metrics"] = portfolio_metrics

        return insights

    def _identify_emerging_patterns(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        patterns = []

        # Time-based patterns
        if "accident_date" in df.columns:
            df["accident_date"] = pd.to_datetime(df["accident_date"])

            # Monthly trend analysis
            monthly_claims = df.groupby(df["accident_date"].dt.to_period("M")).size()
            if len(monthly_claims) > 3:
                # Simple trend detection
                recent_avg = monthly_claims.tail(3).mean()
                historical_avg = (
                    monthly_claims.head(-3).mean()
                    if len(monthly_claims) > 6
                    else monthly_claims.mean()
                )

                if recent_avg > historical_avg * 1.2:  # 20% increase
                    patterns.append(
                        {
                            "type": "increasing_frequency",
                            "description": f"Claim frequency increased by {((recent_avg / historical_avg - 1) * 100):.1f}% in recent months",
                            "severity": "medium",
                        }
                    )

        # Amount-based patterns
        if "paidtotal" in df.columns:
            # Check for outliers
            q75, q25 = np.percentile(df["paidtotal"], [75, 25])
            iqr = q75 - q25
            outlier_threshold = q75 + 1.5 * iqr
            outliers = df[df["paidtotal"] > outlier_threshold]

            if len(outliers) > len(df) * 0.05:  # More than 5% outliers
                patterns.append(
                    {
                        "type": "high_severity_outliers",
                        "description": f"{len(outliers)} claims ({len(outliers) / len(df) * 100:.1f}%) exceed normal severity range",
                        "severity": "high",
                    }
                )

        return patterns

    def _calculate_portfolio_metrics(self, df: pd.DataFrame) -> dict[str, Any]:
        metrics = {}

        if "paidtotal" in df.columns:
            metrics["average_claim_amount"] = df["paidtotal"].mean()
            metrics["median_claim_amount"] = df["paidtotal"].median()
            metrics["total_paid"] = df["paidtotal"].sum()
            metrics["claim_count"] = len(df)

        if "accident_date" in df.columns:
            df["accident_date"] = pd.to_datetime(df["accident_date"])
            date_range = df["accident_date"].max() - df["accident_date"].min()
            metrics["analysis_period_days"] = date_range.days

            if date_range.days > 0:
                metrics["claims_per_day"] = len(df) / date_range.days

        return metrics

    def detect_risk_trends(
        self, historical_data: list[dict[str, Any]], current_data: list[dict[str, Any]]
    ) -> dict[str, Any]:
        try:
            historical_df = pd.DataFrame(historical_data)
            current_df = pd.DataFrame(current_data)

            trends = {}

            # Compare claim frequencies
            if len(historical_df) > 0 and len(current_df) > 0:
                # Frequency trend
                historical_freq = len(historical_df)
                current_freq = len(current_df)
                freq_change = (
                    ((current_freq - historical_freq) / historical_freq * 100)
                    if historical_freq > 0
                    else 0
                )

                trends["frequency_trend"] = {
                    "historical_count": historical_freq,
                    "current_count": current_freq,
                    "change_percent": freq_change,
                    "trend_direction": "increasing"
                    if freq_change > 5
                    else "decreasing"
                    if freq_change < -5
                    else "stable",
                }

                # Severity trend
                if (
                    "paidtotal" in historical_df.columns
                    and "paidtotal" in current_df.columns
                ):
                    historical_avg = historical_df["paidtotal"].mean()
                    current_avg = current_df["paidtotal"].mean()
                    severity_change = (
                        ((current_avg - historical_avg) / historical_avg * 100)
                        if historical_avg > 0
                        else 0
                    )

                    trends["severity_trend"] = {
                        "historical_average": historical_avg,
                        "current_average": current_avg,
                        "change_percent": severity_change,
                        "trend_direction": "increasing"
                        if severity_change > 10
                        else "decreasing"
                        if severity_change < -10
                        else "stable",
                    }

            # Risk factor stability analysis
            risk_factor_stability = self._analyze_risk_factor_stability(
                historical_df, current_df
            )
            trends["risk_factor_stability"] = risk_factor_stability

            return {
                "trends": trends,
                "analysis_date": pd.Timestamp.now().isoformat(),
                "recommendation": self._generate_trend_recommendations(trends),
            }

        except Exception as e:
            raise Exception(f"Failed to detect risk trends: {str(e)}") from e

    def _analyze_risk_factor_stability(
        self, historical_df: pd.DataFrame, current_df: pd.DataFrame
    ) -> dict[str, Any]:
        stability_analysis = {}

        # Analyze line of business distribution if available
        if (
            "line_of_business" in historical_df.columns
            and "line_of_business" in current_df.columns
        ):
            hist_dist = historical_df["line_of_business"].value_counts(normalize=True)
            curr_dist = current_df["line_of_business"].value_counts(normalize=True)

            # Calculate distribution changes
            distribution_changes = {}
            for lob in set(hist_dist.index) | set(curr_dist.index):
                hist_pct = hist_dist.get(lob, 0) * 100
                curr_pct = curr_dist.get(lob, 0) * 100
                change = curr_pct - hist_pct
                distribution_changes[lob] = {
                    "historical_percent": hist_pct,
                    "current_percent": curr_pct,
                    "change": change,
                }

            stability_analysis["line_of_business_distribution"] = distribution_changes

        return stability_analysis

    def _generate_trend_recommendations(self, trends: dict[str, Any]) -> list[str]:
        recommendations = []

        # Frequency recommendations
        if "frequency_trend" in trends:
            freq_trend = trends["frequency_trend"]
            if freq_trend["trend_direction"] == "increasing":
                recommendations.append(
                    "Monitor increasing claim frequency - consider underwriting review"
                )
            elif freq_trend["trend_direction"] == "decreasing":
                recommendations.append(
                    "Decreasing claim frequency is positive - maintain current practices"
                )

        # Severity recommendations
        if "severity_trend" in trends:
            sev_trend = trends["severity_trend"]
            if sev_trend["trend_direction"] == "increasing":
                recommendations.append(
                    "Rising claim severity detected - review reserve adequacy"
                )
            elif sev_trend["trend_direction"] == "decreasing":
                recommendations.append(
                    "Decreasing claim severity - potential reserve release opportunity"
                )

        if not recommendations:
            recommendations.append("Risk trends appear stable - continue monitoring")

        return recommendations


def analyze_risk_factors(data):
    """Analyze risk factors"""
    service = RiskAnalysisService()
    return service.analyze_risk_factors(data)
