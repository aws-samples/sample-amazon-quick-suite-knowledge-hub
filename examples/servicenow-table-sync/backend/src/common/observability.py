"""Shared observability primitives.

Exposes the single Powertools `Logger`, `Tracer`, and `Metrics` instances the backend imports.
Every handler and activity imports these rather than constructing its own, so logs, traces, and
metrics share one service name and namespace.
"""

from aws_lambda_powertools import Logger, Metrics, Tracer

SERVICE = "ServiceNowIngestion"
METRICS_NAMESPACE = "ServiceNowIngestion"

logger = Logger(service=SERVICE)
tracer = Tracer(service=SERVICE)
metrics = Metrics(namespace=METRICS_NAMESPACE, service=SERVICE)
