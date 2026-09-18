"""
Comprehensive error handling module for Lambda function.

This module provides centralized error handling, logging, and response formatting
for all types of errors that can occur during S3 operations.
"""

import logging
import traceback
from typing import Dict, Any, Optional
from botocore.exceptions import ClientError, NoCredentialsError, BotoCoreError

logger = logging.getLogger(__name__)

class ErrorHandler:
    """Centralized error handler for Lambda operations."""
    
    @staticmethod
    def handle_s3_client_error(e: ClientError, operation: str, bucket: str = None, key: str = None) -> Dict[str, Any]:
        """
        Handle S3 ClientError exceptions with specific error mapping.
        
        Args:
            e: ClientError exception
            operation: Operation being performed (create, read, update, delete)
            bucket: S3 bucket name (optional)
            key: Object key (optional)
            
        Returns:
            Standardized error response
        """
        error_code = e.response['Error']['Code']
        error_message = e.response['Error']['Message']
        
        # Log the error with context
        logger.error(f"S3 ClientError in {operation} operation: {error_code} - {error_message}")
        
        # Map S3 error codes to standardized responses
        error_mappings = {
            'NoSuchKey': {
                'status_code': 404,
                'error_code': 'OBJECT_NOT_FOUND',
                'message': f"The specified object does not exist: {key}" if key else "Object not found",
                'details': {'bucket': bucket, 'key': key} if bucket and key else None
            },
            'NoSuchBucket': {
                'status_code': 404,
                'error_code': 'BUCKET_NOT_FOUND',
                'message': f"The specified bucket does not exist: {bucket}" if bucket else "Bucket not found"
            },
            'AccessDenied': {
                'status_code': 403,
                'error_code': 'ACCESS_DENIED',
                'message': f"Access denied for S3 {operation} operation"
            },
            'InvalidBucketName': {
                'status_code': 400,
                'error_code': 'INVALID_BUCKET',
                'message': f"Invalid bucket name: {bucket}" if bucket else "Invalid bucket name"
            },
            'BucketNotEmpty': {
                'status_code': 409,
                'error_code': 'BUCKET_NOT_EMPTY',
                'message': "Cannot delete non-empty bucket"
            },
            'InvalidRequest': {
                'status_code': 400,
                'error_code': 'INVALID_REQUEST',
                'message': "Invalid request parameters"
            },
            'RequestTimeout': {
                'status_code': 408,
                'error_code': 'REQUEST_TIMEOUT',
                'message': "Request timed out"
            },
            'ServiceUnavailable': {
                'status_code': 503,
                'error_code': 'SERVICE_UNAVAILABLE',
                'message': "S3 service temporarily unavailable"
            },
            'SlowDown': {
                'status_code': 503,
                'error_code': 'RATE_LIMITED',
                'message': "Request rate too high, please slow down"
            },
            'InternalError': {
                'status_code': 500,
                'error_code': 'S3_INTERNAL_ERROR',
                'message': "S3 internal error"
            }
        }
        
        # Get mapped error or default
        mapped_error = error_mappings.get(error_code, {
            'status_code': 500,
            'error_code': 'S3_ERROR',
            'message': f"S3 operation failed: {error_code}"
        })
        
        return ErrorHandler._create_error_response(
            mapped_error['status_code'],
            mapped_error['error_code'],
            mapped_error['message'],
            mapped_error.get('details')
        )
    
    @staticmethod
    def handle_validation_error(error_type: str, message: str, details: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Handle input validation errors.
        
        Args:
            error_type: Type of validation error
            message: Error message
            details: Additional error details
            
        Returns:
            Standardized error response
        """
        logger.warning(f"Validation error: {error_type} - {message}")
        
        return ErrorHandler._create_error_response(
            400,
            error_type,
            message,
            details
        )
    
    @staticmethod
    def handle_credentials_error(operation: str) -> Dict[str, Any]:
        """
        Handle AWS credentials errors.
        
        Args:
            operation: Operation being performed
            
        Returns:
            Standardized error response
        """
        logger.error(f"No AWS credentials available for {operation} operation")
        
        return ErrorHandler._create_error_response(
            500,
            "CREDENTIALS_ERROR",
            "AWS credentials not available"
        )
    
    @staticmethod
    def handle_unexpected_error(e: Exception, operation: str) -> Dict[str, Any]:
        """
        Handle unexpected errors with proper logging.
        
        Args:
            e: Exception that occurred
            operation: Operation being performed
            
        Returns:
            Standardized error response
        """
        # Log full traceback for debugging
        logger.error(f"Unexpected error in {operation} operation: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        
        return ErrorHandler._create_error_response(
            500,
            "INTERNAL_ERROR",
            f"An unexpected error occurred during {operation} operation"
        )
    
    @staticmethod
    def handle_network_error(e: Exception, operation: str) -> Dict[str, Any]:
        """
        Handle network-related errors.
        
        Args:
            e: Network exception
            operation: Operation being performed
            
        Returns:
            Standardized error response
        """
        logger.error(f"Network error in {operation} operation: {str(e)}")
        
        return ErrorHandler._create_error_response(
            503,
            "NETWORK_ERROR",
            "Network connectivity issue occurred"
        )
    
    @staticmethod
    def _create_error_response(status_code: int, error_code: str, message: str, details: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Create standardized error response.
        
        Args:
            status_code: HTTP status code
            error_code: Application-specific error code
            message: Human-readable error message
            details: Optional additional error details
            
        Returns:
            Standardized error response dictionary
        """
        response = {
            "statusCode": status_code,
            "body": {
                "success": False,
                "error": {
                    "code": error_code,
                    "message": message
                }
            }
        }
        
        if details:
            response["body"]["error"]["details"] = details
        
        return response
    
    @staticmethod
    def sanitize_error_message(message: str) -> str:
        """
        Sanitize error messages to remove sensitive information.
        
        Args:
            message: Original error message
            
        Returns:
            Sanitized error message
        """
        # Remove potential sensitive information patterns
        sensitive_patterns = [
            r'arn:aws:[^:]*:[^:]*:\d+:',  # AWS ARNs
            r'[A-Z0-9]{20}',  # AWS Access Key IDs
            r'[A-Za-z0-9/+=]{40}',  # AWS Secret Access Keys
            r'password[=:]\s*\S+',  # Passwords
            r'token[=:]\s*\S+',  # Tokens
        ]
        
        import re
        sanitized = message
        for pattern in sensitive_patterns:
            sanitized = re.sub(pattern, '[REDACTED]', sanitized, flags=re.IGNORECASE)
        
        return sanitized