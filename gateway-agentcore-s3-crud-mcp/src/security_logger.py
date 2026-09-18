"""
Security event logging module for Bedrock Agent Gateway.

This module provides centralized security event logging functionality
for authentication, authorization, and security-relevant events.
"""

import json
import logging
import os
import time
from typing import Dict, Any, Optional, List
from enum import Enum
from dataclasses import dataclass, asdict
import boto3
from botocore.exceptions import ClientError

# Configure security logger
security_logger = logging.getLogger('security')
security_logger.setLevel(logging.INFO)

class SecurityEventType(Enum):
    """Types of security events."""
    AUTHENTICATION_SUCCESS = "authentication_success"
    AUTHENTICATION_FAILURE = "authentication_failure"
    AUTHORIZATION_SUCCESS = "authorization_success"
    AUTHORIZATION_FAILURE = "authorization_failure"
    TOKEN_VALIDATION_SUCCESS = "token_validation_success"
    TOKEN_VALIDATION_FAILURE = "token_validation_failure"
    ACCESS_DENIED = "access_denied"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    INVALID_REQUEST = "invalid_request"
    SECURITY_ERROR = "security_error"

@dataclass
class SecurityEvent:
    """Security event data structure."""
    event_type: SecurityEventType
    timestamp: float
    source_ip: Optional[str] = None
    user_agent: Optional[str] = None
    client_id: Optional[str] = None
    resource: Optional[str] = None
    action: Optional[str] = None
    result: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    additional_data: Optional[Dict[str, Any]] = None
    request_id: Optional[str] = None
    session_id: Optional[str] = None

class SecurityLogger:
    """
    Centralized security event logger.
    
    This class handles logging of security-relevant events to CloudWatch
    with proper sanitization and structured formatting.
    """
    
    def __init__(self):
        """Initialize the security logger."""
        self.enabled = os.environ.get('ENABLE_LOGGING', 'false').lower() == 'true'
        self.security_log_group = os.environ.get('SECURITY_LOG_GROUP')
        self.environment = os.environ.get('ENVIRONMENT', 'unknown')
        self.project_name = os.environ.get('PROJECT_NAME', 'bedrock-agent-gateway')
        
        # Initialize CloudWatch Logs client if security logging is enabled
        self.cloudwatch_client = None
        if self.enabled and self.security_log_group:
            try:
                # Configure CloudWatch client with HTTPS enforcement
                from botocore.config import Config as BotoCoreConfig
                self.cloudwatch_client = boto3.client(
                    'logs',
                    config=BotoCoreConfig(
                        use_ssl=True,  # Enforce HTTPS/TLS for all CloudWatch communications
                        signature_version='v4',
                        retries={
                            'max_attempts': 3,
                            'mode': 'adaptive'
                        }
                    )
                )
            except Exception as e:
                security_logger.warning(f"Failed to initialize CloudWatch client: {e}")
    
    def log_security_event(self, event: SecurityEvent) -> None:
        """
        Log a security event.
        
        Args:
            event: SecurityEvent to log
        """
        if not self.enabled:
            return
        
        try:
            # Create structured log entry
            log_entry = self._create_log_entry(event)
            
            # Log to CloudWatch (structured JSON)
            security_logger.info(json.dumps(log_entry, default=str))
            
            # Also send to dedicated security log group if configured
            if self.cloudwatch_client and self.security_log_group:
                self._send_to_security_log_group(log_entry)
                
        except Exception as e:
            # Don't let logging errors break the main application
            security_logger.error(f"Failed to log security event: {e}")
    
    def log_authentication_success(self, 
                                 client_id: str,
                                 source_ip: Optional[str] = None,
                                 user_agent: Optional[str] = None,
                                 request_id: Optional[str] = None) -> None:
        """Log successful authentication event."""
        event = SecurityEvent(
            event_type=SecurityEventType.AUTHENTICATION_SUCCESS,
            timestamp=time.time(),
            source_ip=source_ip,
            user_agent=user_agent,
            client_id=self._sanitize_client_id(client_id),
            result="success",
            request_id=request_id
        )
        self.log_security_event(event)
    
    def log_authentication_failure(self,
                                 error_code: str,
                                 error_message: str,
                                 source_ip: Optional[str] = None,
                                 user_agent: Optional[str] = None,
                                 client_id: Optional[str] = None,
                                 request_id: Optional[str] = None) -> None:
        """Log failed authentication event."""
        event = SecurityEvent(
            event_type=SecurityEventType.AUTHENTICATION_FAILURE,
            timestamp=time.time(),
            source_ip=source_ip,
            user_agent=user_agent,
            client_id=self._sanitize_client_id(client_id) if client_id else None,
            result="failure",
            error_code=error_code,
            error_message=self._sanitize_error_message(error_message),
            request_id=request_id
        )
        self.log_security_event(event)
    
    def log_authorization_success(self,
                                client_id: str,
                                resource: str,
                                action: str,
                                scopes: List[str],
                                source_ip: Optional[str] = None,
                                request_id: Optional[str] = None) -> None:
        """Log successful authorization event."""
        event = SecurityEvent(
            event_type=SecurityEventType.AUTHORIZATION_SUCCESS,
            timestamp=time.time(),
            source_ip=source_ip,
            client_id=self._sanitize_client_id(client_id),
            resource=resource,
            action=action,
            result="success",
            additional_data={"scopes": scopes},
            request_id=request_id
        )
        self.log_security_event(event)
    
    def log_authorization_failure(self,
                                error_code: str,
                                error_message: str,
                                resource: str,
                                action: str,
                                client_id: Optional[str] = None,
                                source_ip: Optional[str] = None,
                                request_id: Optional[str] = None) -> None:
        """Log failed authorization event."""
        event = SecurityEvent(
            event_type=SecurityEventType.AUTHORIZATION_FAILURE,
            timestamp=time.time(),
            source_ip=source_ip,
            client_id=self._sanitize_client_id(client_id) if client_id else None,
            resource=resource,
            action=action,
            result="failure",
            error_code=error_code,
            error_message=self._sanitize_error_message(error_message),
            request_id=request_id
        )
        self.log_security_event(event)
    
    def log_token_validation_failure(self,
                                   error_code: str,
                                   error_message: str,
                                   source_ip: Optional[str] = None,
                                   request_id: Optional[str] = None) -> None:
        """Log token validation failure."""
        event = SecurityEvent(
            event_type=SecurityEventType.TOKEN_VALIDATION_FAILURE,
            timestamp=time.time(),
            source_ip=source_ip,
            result="failure",
            error_code=error_code,
            error_message=self._sanitize_error_message(error_message),
            request_id=request_id
        )
        self.log_security_event(event)
    
    def log_access_denied(self,
                         resource: str,
                         action: str,
                         reason: str,
                         client_id: Optional[str] = None,
                         source_ip: Optional[str] = None,
                         request_id: Optional[str] = None) -> None:
        """Log access denied event."""
        event = SecurityEvent(
            event_type=SecurityEventType.ACCESS_DENIED,
            timestamp=time.time(),
            source_ip=source_ip,
            client_id=self._sanitize_client_id(client_id) if client_id else None,
            resource=resource,
            action=action,
            result="denied",
            error_message=reason,
            request_id=request_id
        )
        self.log_security_event(event)
    
    def log_suspicious_activity(self,
                              activity_type: str,
                              description: str,
                              source_ip: Optional[str] = None,
                              client_id: Optional[str] = None,
                              additional_data: Optional[Dict[str, Any]] = None,
                              request_id: Optional[str] = None) -> None:
        """Log suspicious activity."""
        event = SecurityEvent(
            event_type=SecurityEventType.SUSPICIOUS_ACTIVITY,
            timestamp=time.time(),
            source_ip=source_ip,
            client_id=self._sanitize_client_id(client_id) if client_id else None,
            error_message=description,
            additional_data={
                "activity_type": activity_type,
                **(additional_data or {})
            },
            request_id=request_id
        )
        self.log_security_event(event)
    
    def log_rate_limit_exceeded(self,
                              client_id: Optional[str] = None,
                              source_ip: Optional[str] = None,
                              request_count: Optional[int] = None,
                              time_window: Optional[str] = None,
                              request_id: Optional[str] = None) -> None:
        """Log rate limit exceeded event."""
        event = SecurityEvent(
            event_type=SecurityEventType.RATE_LIMIT_EXCEEDED,
            timestamp=time.time(),
            source_ip=source_ip,
            client_id=self._sanitize_client_id(client_id) if client_id else None,
            result="blocked",
            additional_data={
                "request_count": request_count,
                "time_window": time_window
            },
            request_id=request_id
        )
        self.log_security_event(event)
    
    def log_security_error(self,
                          error_code: str,
                          error_message: str,
                          source_ip: Optional[str] = None,
                          user_agent: Optional[str] = None,
                          client_id: Optional[str] = None,
                          request_id: Optional[str] = None) -> None:
        """Log general security error event."""
        event = SecurityEvent(
            event_type=SecurityEventType.SECURITY_ERROR,
            timestamp=time.time(),
            source_ip=source_ip,
            user_agent=user_agent,
            client_id=self._sanitize_client_id(client_id) if client_id else None,
            result="error",
            error_code=error_code,
            error_message=self._sanitize_error_message(error_message),
            request_id=request_id
        )
        self.log_security_event(event)
    
    def _create_log_entry(self, event: SecurityEvent) -> Dict[str, Any]:
        """Create structured log entry from security event."""
        log_entry = {
            "timestamp": event.timestamp,
            "event_type": event.event_type.value,
            "environment": self.environment,
            "project": self.project_name,
            "source": "bedrock-agent-gateway"
        }
        
        # Add non-None fields from the event
        event_dict = asdict(event)
        for key, value in event_dict.items():
            if key not in ['event_type', 'timestamp'] and value is not None:
                if key == 'event_type':
                    continue  # Already handled above
                log_entry[key] = value
        
        return log_entry
    
    def _send_to_security_log_group(self, log_entry: Dict[str, Any]) -> None:
        """Send log entry to dedicated security log group."""
        try:
            log_stream_name = f"security-events-{int(time.time() // 3600)}"  # Hourly streams
            
            # Create log stream if it doesn't exist
            try:
                self.cloudwatch_client.create_log_stream(
                    logGroupName=self.security_log_group,
                    logStreamName=log_stream_name
                )
            except ClientError as e:
                if e.response['Error']['Code'] != 'ResourceAlreadyExistsException':
                    raise
            
            # Send log event
            self.cloudwatch_client.put_log_events(
                logGroupName=self.security_log_group,
                logStreamName=log_stream_name,
                logEvents=[
                    {
                        'timestamp': int(log_entry['timestamp'] * 1000),  # CloudWatch expects milliseconds
                        'message': json.dumps(log_entry, default=str)
                    }
                ]
            )
            
        except Exception as e:
            security_logger.error(f"Failed to send to security log group: {e}")
    
    def _sanitize_client_id(self, client_id: str) -> str:
        """Sanitize client ID for logging (show only first/last few characters)."""
        if not client_id or len(client_id) < 8:
            return "[REDACTED]"
        return f"{client_id[:4]}...{client_id[-4:]}"
    
    def _sanitize_error_message(self, message: str) -> str:
        """Sanitize error messages to remove sensitive information."""
        if not message:
            return message
        
        # Remove potential sensitive information patterns
        import re
        sensitive_patterns = [
            (r'arn:aws:[^:]*:[^:]*:\d+:', '[AWS-ARN]'),
            (r'[A-Z0-9]{20}', '[ACCESS-KEY]'),
            (r'[A-Za-z0-9/+=]{40}', '[SECRET-KEY]'),
            (r'password[=:]\s*\S+', 'password=[REDACTED]'),
            (r'token[=:]\s*\S+', 'token=[REDACTED]'),
            (r'Bearer\s+[A-Za-z0-9\-._~+/]+=*', 'Bearer [REDACTED]'),
        ]
        
        sanitized = message
        for pattern, replacement in sensitive_patterns:
            sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)
        
        return sanitized

# Global security logger instance
security_logger_instance = SecurityLogger()