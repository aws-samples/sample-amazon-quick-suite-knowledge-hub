"""Observability setup using AWS Lambda Powertools."""

from aws_lambda_powertools import Logger, Metrics, Tracer

from common.env import SERVICE

logger = Logger(service=SERVICE)
tracer = Tracer(service=SERVICE)
metrics = Metrics(service=SERVICE)
