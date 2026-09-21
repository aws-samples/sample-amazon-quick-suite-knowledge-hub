"""
Monitoring Tool - KPI tracking and alerting for actuarial oversight.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd
from utils.constants import DEFAULT_MONITORING_CONFIG

# Set root logger level explicitly
logging.getLogger().setLevel(logging.INFO)

# Get logger for this module
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@dataclass
class Alert:
    alert_id: str
    alert_type: str
    severity: str
    message: str
    triggered_at: str
    data_context: dict[str, Any]


@dataclass
class KPI:
    name: str
    current_value: float
    target_value: float
    threshold_upper: float
    threshold_lower: float
    status: str
    trend: str


class MonitoringService:
    def __init__(self, monitoring_config=None):
        self.config = monitoring_config or DEFAULT_MONITORING_CONFIG
        self.alert_thresholds = self.config.get(
            "alert_thresholds",
            {
                "loss_ratio": 0.8,
                "frequency_spike": 2.0,
                "severity_increase": 0.3,
                "reserve_adequacy": 0.9,
            },
        )
        self.kpi_targets = self.config.get(
            "kpi_targets",
            {
                "loss_ratio": 0.65,
                "claim_frequency": 0.05,
                "avg_severity": 5000,
                "reserve_ratio": 0.15,
            },
        )

    def monitor_development(self, claims_data: dict[str, Any]) -> dict[str, Any]:
        try:
            df = pd.DataFrame(
                claims_data["data"] if "data" in claims_data else claims_data
            )

            current_kpis = self._calculate_kpis(df)
            dashboard_metrics = self._generate_dashboard_metrics(df, current_kpis)
            all_alerts = self._check_kpi_alerts(current_kpis)

            return {
                "alerts": [alert.__dict__ for alert in all_alerts],
                "kpis": [kpi.__dict__ for kpi in current_kpis],
                "dashboard_metrics": dashboard_metrics,
            }

        except Exception as e:
            raise Exception(f"Failed to monitor claim development: {str(e)}") from e

    def _calculate_kpis(self, df: pd.DataFrame) -> list[KPI]:
        kpis = []

        try:
            # Financial KPIs
            if "totalincurred" in df.columns and "paidtotal" in df.columns:
                total_incurred = df["totalincurred"].sum()
                total_paid = df["paidtotal"].sum()
                total_reserves = (
                    df["reservetotal"].sum() if "reservetotal" in df.columns else 0
                )

                # Loss Ratio KPI
                loss_ratio = total_paid / total_incurred if total_incurred > 0 else 0
                kpis.append(
                    KPI(
                        name="loss_ratio",
                        current_value=float(loss_ratio),
                        target_value=float(self.kpi_targets["loss_ratio"]),
                        threshold_upper=float(self.alert_thresholds["loss_ratio"]),
                        threshold_lower=0.4,
                        status="above_threshold"
                        if loss_ratio > self.alert_thresholds["loss_ratio"]
                        else "normal",
                        trend="stable",
                    )
                )

                # Payment Ratio KPI
                payment_ratio = total_paid / total_incurred if total_incurred > 0 else 0
                kpis.append(
                    KPI(
                        name="payment_ratio",
                        current_value=float(payment_ratio),
                        target_value=0.75,
                        threshold_upper=0.95,
                        threshold_lower=0.50,
                        status="above_threshold" if payment_ratio > 0.95 else "normal",
                        trend="stable",
                    )
                )

                # Reserve Ratio KPI
                reserve_ratio = (
                    total_reserves / total_incurred if total_incurred > 0 else 0
                )
                kpis.append(
                    KPI(
                        name="reserve_ratio",
                        current_value=float(reserve_ratio),
                        target_value=0.25,
                        threshold_upper=0.50,
                        threshold_lower=0.10,
                        status="above_threshold"
                        if reserve_ratio > 0.50
                        else "below_threshold"
                        if reserve_ratio < 0.10
                        else "normal",
                        trend="stable",
                    )
                )

                # Average Severity KPI
                avg_severity = df["totalincurred"].mean()
                kpis.append(
                    KPI(
                        name="avg_severity",
                        current_value=float(avg_severity),
                        target_value=float(self.kpi_targets["avg_severity"]),
                        threshold_upper=float(self.kpi_targets["avg_severity"] * 1.5),
                        threshold_lower=float(self.kpi_targets["avg_severity"] * 0.5),
                        status="above_threshold"
                        if avg_severity > self.kpi_targets["avg_severity"] * 1.5
                        else "normal",
                        trend="stable",
                    )
                )

            # Operational KPIs
            if len(df) > 0:
                # Claim Frequency KPI
                claim_count = len(df)
                kpis.append(
                    KPI(
                        name="claim_frequency",
                        current_value=float(claim_count),
                        target_value=100.0,
                        threshold_upper=200.0,
                        threshold_lower=50.0,
                        status="above_threshold"
                        if claim_count > 200
                        else "below_threshold"
                        if claim_count < 50
                        else "normal",
                        trend="stable",
                    )
                )

                # Large Claims Percentage
                if "totalincurred" in df.columns:
                    large_claims_threshold = 50000
                    large_claims = len(df[df["totalincurred"] > large_claims_threshold])
                    large_claims_pct = (large_claims / len(df)) * 100
                    kpis.append(
                        KPI(
                            name="large_claims_percentage",
                            current_value=float(large_claims_pct),
                            target_value=5.0,
                            threshold_upper=15.0,
                            threshold_lower=1.0,
                            status="above_threshold"
                            if large_claims_pct > 15.0
                            else "normal",
                            trend="stable",
                        )
                    )

                # Open Claims Ratio
                if "claimstatus" in df.columns:
                    open_claims = len(df[df["claimstatus"].str.lower() == "open"])
                    open_claims_ratio = (open_claims / len(df)) * 100
                    kpis.append(
                        KPI(
                            name="open_claims_ratio",
                            current_value=float(open_claims_ratio),
                            target_value=30.0,
                            threshold_upper=60.0,
                            threshold_lower=10.0,
                            status="above_threshold"
                            if open_claims_ratio > 60.0
                            else "normal",
                            trend="stable",
                        )
                    )

            # Time-based KPIs
            if "policyeffectivedate" in df.columns and "note_date" in df.columns:
                df["policy_date"] = pd.to_datetime(
                    df["policyeffectivedate"], errors="coerce"
                )
                df["report_date"] = pd.to_datetime(df["note_date"], errors="coerce")

                # Average Reporting Delay
                valid_dates = df.dropna(subset=["policy_date", "report_date"])
                if len(valid_dates) > 0:
                    reporting_delays = (
                        valid_dates["report_date"] - valid_dates["policy_date"]
                    ).dt.days
                    avg_reporting_delay = reporting_delays.mean()
                    kpis.append(
                        KPI(
                            name="avg_reporting_delay_days",
                            current_value=float(avg_reporting_delay),
                            target_value=30.0,
                            threshold_upper=90.0,
                            threshold_lower=5.0,
                            status="above_threshold"
                            if avg_reporting_delay > 90.0
                            else "normal",
                            trend="stable",
                        )
                    )

        except Exception as e:
            logger.error(f"Error calculating KPIs: {str(e)}")
            # Return basic KPI if calculation fails
            kpis.append(
                KPI(
                    name="basic_metrics",
                    current_value=float(len(df)),
                    target_value=100.0,
                    threshold_upper=200.0,
                    threshold_lower=50.0,
                    status="normal",
                    trend="stable",
                )
            )

        return kpis

    def _check_kpi_alerts(self, kpis: list[KPI]) -> list[Alert]:
        alerts = []

        try:
            for kpi in kpis:
                if kpi.status == "above_threshold":
                    alerts.append(
                        Alert(
                            alert_id=f"kpi_{kpi.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                            alert_type="kpi_threshold",
                            severity="warning",
                            message=f"KPI {kpi.name} exceeded threshold: {kpi.current_value:.3f} > {kpi.threshold_upper:.3f}",
                            triggered_at=datetime.now().isoformat(),
                            data_context={
                                "kpi_name": kpi.name,
                                "current": float(kpi.current_value),
                                "threshold": float(kpi.threshold_upper),
                            },
                        )
                    )
        except Exception as e:
            logger.error(f"Error checking KPI alerts: {str(e)}")

        return alerts

    def _generate_dashboard_metrics(
        self, df: pd.DataFrame, kpis: list[KPI]
    ) -> dict[str, Any]:
        metrics = {
            "summary_statistics": {
                "total_claims": len(df),
                "total_incurred": float(df["totalincurred"].sum())
                if "totalincurred" in df.columns
                else 0,
                "total_paid": float(df["paidtotal"].sum())
                if "paidtotal" in df.columns
                else 0,
                "total_reserves": float(df["reservetotal"].sum())
                if "reservetotal" in df.columns
                else 0,
                "avg_claim_size": float(df["totalincurred"].mean())
                if "totalincurred" in df.columns
                else 0,
                "median_claim_size": float(df["totalincurred"].median())
                if "totalincurred" in df.columns
                else 0,
                "max_claim_size": float(df["totalincurred"].max())
                if "totalincurred" in df.columns
                else 0,
            },
            "claim_distribution": self._analyze_claim_distribution(df),
            "line_of_business_analysis": self._analyze_by_line_of_business(df),
            "temporal_analysis": self._analyze_temporal_patterns(df),
            "status_breakdown": self._analyze_claim_status(df),
            "performance_indicators": {
                "claims_per_day": len(df) / 30
                if len(df) > 0
                else 0,  # Assuming 30-day period
                "avg_reserve_per_claim": float(df["reservetotal"].mean())
                if "reservetotal" in df.columns
                else 0,
                "settlement_rate": self._calculate_settlement_rate(df),
            },
        }

        return metrics

    def _analyze_claim_distribution(self, df: pd.DataFrame) -> dict[str, Any]:
        if "totalincurred" not in df.columns or len(df) == 0:
            return {}

        amounts = df["totalincurred"]
        return {
            "small_claims_0_10k": len(amounts[amounts <= 10000]),
            "medium_claims_10k_50k": len(
                amounts[(amounts > 10000) & (amounts <= 50000)]
            ),
            "large_claims_50k_100k": len(
                amounts[(amounts > 50000) & (amounts <= 100000)]
            ),
            "very_large_claims_100k_plus": len(amounts[amounts > 100000]),
            "percentiles": {
                "25th": float(amounts.quantile(0.25)),
                "50th": float(amounts.quantile(0.50)),
                "75th": float(amounts.quantile(0.75)),
                "90th": float(amounts.quantile(0.90)),
                "95th": float(amounts.quantile(0.95)),
            },
        }

    def _analyze_by_line_of_business(self, df: pd.DataFrame) -> dict[str, Any]:
        if "lineofbusiness" not in df.columns or len(df) == 0:
            return {}

        lob_analysis = {}
        for lob in df["lineofbusiness"].unique():
            lob_data = df[df["lineofbusiness"] == lob]
            lob_analysis[str(lob)] = {
                "claim_count": len(lob_data),
                "total_incurred": float(lob_data["totalincurred"].sum())
                if "totalincurred" in lob_data.columns
                else 0,
                "avg_severity": float(lob_data["totalincurred"].mean())
                if "totalincurred" in lob_data.columns
                else 0,
                "percentage_of_total": float((len(lob_data) / len(df)) * 100),
            }

        return lob_analysis

    def _analyze_temporal_patterns(self, df: pd.DataFrame) -> dict[str, Any]:
        if "policyeffectivedate" not in df.columns or len(df) == 0:
            return {}

        try:
            df["policy_date"] = pd.to_datetime(
                df["policyeffectivedate"], errors="coerce"
            )
            df["accident_year"] = df["policy_date"].dt.year

            yearly_analysis = {}
            for year in df["accident_year"].dropna().unique():
                year_data = df[df["accident_year"] == year]
                yearly_analysis[str(int(year))] = {
                    "claim_count": len(year_data),
                    "total_incurred": float(year_data["totalincurred"].sum())
                    if "totalincurred" in year_data.columns
                    else 0,
                    "avg_severity": float(year_data["totalincurred"].mean())
                    if "totalincurred" in year_data.columns
                    else 0,
                }

            return {
                "by_accident_year": yearly_analysis,
                "trend_analysis": {
                    "years_covered": len(yearly_analysis),
                    "most_active_year": max(
                        yearly_analysis.keys(),
                        key=lambda x: yearly_analysis[x]["claim_count"],
                    )
                    if yearly_analysis
                    else None,
                },
            }
        except Exception as e:
            logger.error(f"Error in temporal analysis: {str(e)}")
            return {}

    def _analyze_claim_status(self, df: pd.DataFrame) -> dict[str, Any]:
        if "claimstatus" not in df.columns or len(df) == 0:
            return {}

        status_counts = df["claimstatus"].value_counts()
        total_claims = len(df)

        return {
            "status_distribution": {
                str(status): {
                    "count": int(count),
                    "percentage": float((count / total_claims) * 100),
                }
                for status, count in status_counts.items()
            },
            "open_vs_closed": {
                "open_claims": int(status_counts.get("Open", 0)),
                "closed_claims": int(
                    status_counts.get("Close", 0) + status_counts.get("Closed", 0)
                ),
                "open_percentage": float(
                    (status_counts.get("Open", 0) / total_claims) * 100
                ),
            },
        }

    def _calculate_settlement_rate(self, df: pd.DataFrame) -> float:
        if "claimstatus" not in df.columns or len(df) == 0:
            return 0.0

        closed_statuses = ["Close", "Closed", "Settled"]
        closed_claims = sum(
            df["claimstatus"].str.contains(
                "|".join(closed_statuses), case=False, na=False
            )
        )
        return float((closed_claims / len(df)) * 100)


def monitor_development(data, monitoring_config=None):
    """Monitor claim development patterns and generate alerts."""
    config = monitoring_config or DEFAULT_MONITORING_CONFIG
    try:
        if not data or not isinstance(data, list):
            return {
                "alerts": [],
                "kpis": [],
                "summary": {
                    "total_alerts": 0,
                    "critical_alerts": 0,
                    "kpi_status": "No data",
                },
            }

        df = pd.DataFrame(data)

        field_mapping = {
            "accident_date": ["policyeffectivedate", "loss_date", "incident_date"],
            "report_date": ["note_date", "claim_date", "reported_date"],
            "paid_amount": ["paidtotal", "paid_total", "amount_paid"],
            "incurred_amount": ["totalincurred", "total_incurred", "incurred_total"],
            "reserve_amount": ["reservetotal", "reserve_total", "reserves"],
        }

        for expected_field, possible_fields in field_mapping.items():
            if expected_field not in df.columns:
                for possible_field in possible_fields:
                    if possible_field in df.columns:
                        df[expected_field] = df[possible_field]
                        break

        service = MonitoringService(config)
        return service.monitor_development(df.to_dict("records"))

    except Exception as e:
        return {
            "error": f"Monitoring failed: {str(e)}",
            "alerts": [],
            "kpis": [],
            "summary": {"total_alerts": 0, "critical_alerts": 0, "kpi_status": "Error"},
        }
