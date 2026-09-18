"""
AWS Lambda function for S3 CRUD operations.

This module provides the main Lambda handler and S3 operations for the Bedrock Agent Gateway.
Supports Create, Read, Update, Delete operations on S3 objects with comprehensive error handling.
"""

import json
import os
import logging
from typing import Dict, Any, Optional
import boto3
from botocore.exceptions import ClientError, NoCredentialsError, BotoCoreError
from botocore.config import Config as BotoCoreConfig
import time
import random

from .config import Config
from .error_handler import ErrorHandler
from .security_logger import security_logger_instance

# Simple in-memory rate limiting (for demonstration - in production use Redis/DynamoDB)
_request_counts = {}
_rate_limit_window = 60  # 1 minute window
_max_requests_per_window = 100

# Configure logging
Config.configure_logging()
logger = logging.getLogger(__name__)

# Validate HTTPS configuration on module load
try:
    Config.validate_https_configuration()
    logger.info("HTTPS/TLS configuration validated successfully")
except Exception as e:
    logger.warning(f"HTTPS/TLS configuration validation warning: {e}")
    # Continue execution - the actual clients are configured correctly

# Initialize S3 client
s3_client = None

def get_s3_client():
    """Get or create S3 client with proper configuration."""
    global s3_client
    if s3_client is None:
        # Configure S3 client with HTTPS enforcement and security settings
        s3_client = boto3.client(
            's3',
            config=BotoCoreConfig(
                # Enforce HTTPS for all requests
                use_ssl=True,
                # Set signature version for enhanced security
                signature_version='s3v4',
                # Configure retry settings
                retries={
                    'max_attempts': Config.MAX_RETRIES,
                    'mode': 'adaptive'
                },
                # Security settings
                s3={
                    'addressing_style': 'virtual'  # Use virtual-hosted-style requests (more secure)
                }
            )
        )
    return s3_client

def _check_rate_limit(source_ip: str, request_id: Optional[str] = None) -> bool:
    """
    Check if source IP has exceeded rate limits.
    
    Args:
        source_ip: Source IP address
        request_id: Request ID for logging
        
    Returns:
        True if rate limit exceeded
    """
    current_time = int(time.time())
    window_start = current_time - _rate_limit_window
    
    # Clean up old entries
    if source_ip in _request_counts:
        _request_counts[source_ip]['requests'] = [
            req_time for req_time in _request_counts[source_ip]['requests']
            if req_time > window_start
        ]
    else:
        _request_counts[source_ip] = {'requests': [], 'count': 0}
    
    # Add current request
    _request_counts[source_ip]['requests'].append(current_time)
    _request_counts[source_ip]['count'] = len(_request_counts[source_ip]['requests'])
    
    # Check if limit exceeded
    return _request_counts[source_ip]['count'] > _max_requests_per_window

def _is_suspicious_request(event: Dict[str, Any], source_ip: str) -> bool:
    """
    Check if a request shows suspicious patterns.
    
    Args:
        event: Lambda event
        source_ip: Source IP address
        
    Returns:
        True if request appears suspicious
    """
    # Check for unusually large payloads
    event_size = len(str(event))
    if event_size > 100000:  # 100KB threshold
        return True
    
    # Check for suspicious IP patterns (private ranges that shouldn't be external)
    if source_ip and (
        source_ip.startswith('10.') or 
        source_ip.startswith('192.168.') or 
        source_ip.startswith('172.')
    ):
        # These might be legitimate internal requests, but worth monitoring
        return False
    
    # Check for unusual event structure
    if 'tool_name' in event:
        # MCP request - check for unusual tool names
        tool_name = event.get('tool_name', '')
        if not tool_name.startswith('s3_'):
            return True
        
        # Check for unusual argument patterns
        arguments = event.get('arguments', {})
        if 'key' in arguments:
            key = arguments['key']
            # Check for path traversal attempts
            if '..' in key or key.startswith('/') or '\\' in key:
                return True
    
    return False

def lambda_handler(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    Main Lambda handler for S3 CRUD operations.
    Handles both direct invocation and MCP Gateway requests.
    
    Args:
        event: Lambda event containing operation details
        context: Lambda context object
        
    Returns:
        Dict containing operation result and status
    """
    request_id = getattr(context, 'aws_request_id', None)
    
    try:
        # Extract request metadata for security logging
        source_ip = event.get('requestContext', {}).get('identity', {}).get('sourceIp')
        user_agent = event.get('requestContext', {}).get('identity', {}).get('userAgent')
        
        # Log request start with security monitoring
        logger.info(f"Processing request: {request_id}, source_ip: {source_ip}")
        
        # Monitor for suspicious activity patterns
        if source_ip:
            # Check for rate limiting
            if _check_rate_limit(source_ip, request_id):
                security_logger_instance.log_rate_limit_exceeded(
                    source_ip=source_ip,
                    request_count=_request_counts.get(source_ip, {}).get('count', 0),
                    time_window=f"{_rate_limit_window}s",
                    request_id=request_id
                )
                return ErrorHandler._create_error_response(
                    429,
                    "RATE_LIMIT_EXCEEDED",
                    "Too many requests from this IP address"
                )
            
            # Check for potential security issues
            if _is_suspicious_request(event, source_ip):
                security_logger_instance.log_suspicious_activity(
                    activity_type="SUSPICIOUS_REQUEST_PATTERN",
                    description="Request pattern indicates potential security concern",
                    source_ip=source_ip,
                    additional_data={
                        "event_keys": list(event.keys()),
                        "request_size": len(str(event))
                    },
                    request_id=request_id
                )
        
        # Check if this is an MCP Gateway request
        if 'tool_name' in event and 'arguments' in event:
            result = handle_mcp_request(event, context)
        else:
            # Handle direct invocation (legacy format)
            result = handle_direct_request(event, context)
        
        # Log successful request completion
        if result.get('statusCode') == 200:
            logger.info(f"Request completed successfully: {request_id}")
            
            # Log successful authorization for security monitoring
            operation = event.get('tool_name') or event.get('operation', 'unknown')
            security_logger_instance.log_authorization_success(
                client_id="gateway-service",  # Since auth is handled by gateway
                resource="s3-bucket",
                action=operation,
                scopes=["s3:crud"],
                source_ip=source_ip,
                request_id=request_id
            )
        else:
            # Log error without sensitive data
            error_code = result.get('body', {}).get('error', {}).get('code', 'UNKNOWN')
            logger.warning(f"Request failed: {request_id}, error_code: {error_code}")
            
            # Log security event for failed requests
            security_logger_instance.log_security_error(
                error_code=error_code,
                error_message="Request processing failed",
                source_ip=source_ip,
                user_agent=user_agent,
                request_id=request_id
            )
        
        return result
            
    except Exception as e:
        # Log unexpected error as security event
        security_logger_instance.log_security_error(
            error_code="UNEXPECTED_ERROR",
            error_message=f"Unexpected error in lambda_handler: {str(e)}",
            source_ip=event.get('requestContext', {}).get('identity', {}).get('sourceIp'),
            request_id=request_id
        )
        return ErrorHandler.handle_unexpected_error(e, "lambda_handler")

def handle_mcp_request(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    Handle MCP Gateway requests with tool_name and arguments format.
    
    Args:
        event: MCP Gateway event with tool_name and arguments
        context: Lambda context object
        
    Returns:
        Dict containing operation result and status
    """
    try:
        # Extract MCP parameters
        tool_name = event.get('tool_name')
        arguments = event.get('arguments', {})
        
        # Log the incoming MCP request (without sensitive data)
        logger.info(f"Processing MCP request: tool_name={tool_name}, key={arguments.get('key')}")
        
        # Map MCP tool names to operations
        operation_mapping = {
            's3_create_object': 'create',
            's3_read_object': 'read',
            's3_update_object': 'update',
            's3_delete_object': 'delete'
        }
        
        operation = operation_mapping.get(tool_name)
        if not operation:
            return ErrorHandler.handle_validation_error("INVALID_TOOL", f"Unsupported tool: {tool_name}")
        
        # Extract parameters from arguments
        bucket = Config.S3_BUCKET_NAME  # Always use configured bucket
        key = arguments.get('key')
        content = arguments.get('content')
        metadata = arguments.get('metadata', {})
        
        # Validate required parameters
        if not key:
            return ErrorHandler.handle_validation_error("MISSING_KEY", "Object key parameter is required")
        
        # Route to appropriate operation handler
        if operation == 'create':
            if not content:
                return ErrorHandler.handle_validation_error("MISSING_CONTENT", "Content parameter is required for create operation")
            return handle_create_operation(bucket, key, content, metadata)
        elif operation == 'read':
            return handle_read_operation(bucket, key)
        elif operation == 'update':
            if not content:
                return ErrorHandler.handle_validation_error("MISSING_CONTENT", "Content parameter is required for update operation")
            return handle_update_operation(bucket, key, content, metadata)
        elif operation == 'delete':
            return handle_delete_operation(bucket, key)
        else:
            return ErrorHandler.handle_validation_error("INVALID_OPERATION", f"Unsupported operation: {operation}")
            
    except Exception as e:
        return ErrorHandler.handle_unexpected_error(e, "handle_mcp_request")

def handle_direct_request(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    Handle direct Lambda invocation requests (legacy format).
    
    Args:
        event: Direct invocation event with operation, key, content, etc.
        context: Lambda context object
        
    Returns:
        Dict containing operation result and status
    """
    try:
        # Log the incoming request (without sensitive data)
        logger.info(f"Processing direct request: operation={event.get('operation')}, key={event.get('key')}")
        
        # Extract operation parameters
        operation = event.get('operation')
        bucket = event.get('bucket') or Config.S3_BUCKET_NAME
        key = event.get('key')
        content = event.get('content')
        metadata = event.get('metadata', {})
        
        # Validate required parameters
        if not operation:
            return ErrorHandler.handle_validation_error("MISSING_OPERATION", "Operation parameter is required")
        
        if not bucket:
            return ErrorHandler.handle_validation_error("MISSING_BUCKET", "Bucket parameter is required")
        
        if not key:
            return ErrorHandler.handle_validation_error("MISSING_KEY", "Object key parameter is required")
        
        # Route to appropriate operation handler
        if operation == 'create':
            return handle_create_operation(bucket, key, content, metadata)
        elif operation == 'read':
            return handle_read_operation(bucket, key)
        elif operation == 'update':
            return handle_update_operation(bucket, key, content, metadata)
        elif operation == 'delete':
            return handle_delete_operation(bucket, key)
        else:
            return ErrorHandler.handle_validation_error("INVALID_OPERATION", f"Unsupported operation: {operation}")
            
    except Exception as e:
        return ErrorHandler.handle_unexpected_error(e, "handle_direct_request")

def create_error_response(status_code: int, error_code: str, message: str, details: Optional[Dict] = None) -> Dict[str, Any]:
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
        "headers": {
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'none'"
        },
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

def create_success_response(data: Any = None) -> Dict[str, Any]:
    """
    Create standardized success response.
    
    Args:
        data: Optional response data
        
    Returns:
        Standardized success response dictionary
    """
    response = {
        "statusCode": 200,
        "headers": {
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'none'"
        },
        "body": {
            "success": True
        }
    }
    
    if data is not None:
        response["body"]["data"] = data
    
    return response

def validate_object_key(key: str) -> bool:
    """
    Validate S3 object key according to AWS requirements.
    
    Args:
        key: Object key to validate
        
    Returns:
        True if key is valid, False otherwise
    """
    if not key or len(key) > Config.MAX_KEY_LENGTH:
        return False
    
    # Check for invalid characters
    invalid_chars = ['\0', '\r', '\n']
    if any(char in key for char in invalid_chars):
        return False
    
    # Key cannot start with '/'
    if key.startswith('/'):
        return False
    
    return True

def validate_content(content: str) -> bool:
    """
    Validate object content.
    
    Args:
        content: Content to validate
        
    Returns:
        True if content is valid, False otherwise
    """
    if content is None:
        return False
    
    # Check content size
    content_bytes = content.encode('utf-8') if isinstance(content, str) else content
    if len(content_bytes) > Config.MAX_OBJECT_SIZE:
        return False
    
    return True

def handle_create_operation(bucket: str, key: str, content: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle S3 create operation.
    
    Args:
        bucket: S3 bucket name
        key: Object key
        content: Object content
        metadata: Object metadata
        
    Returns:
        Operation result dictionary
    """
    try:
        # Validate inputs
        if not validate_object_key(key):
            return ErrorHandler.handle_validation_error(
                "INVALID_KEY", 
                "Object key is invalid or exceeds maximum length",
                {"key": key, "max_length": Config.MAX_KEY_LENGTH}
            )
        
        if not validate_content(content):
            return ErrorHandler.handle_validation_error(
                "INVALID_CONTENT",
                "Content is invalid or exceeds maximum size",
                {"max_size": Config.MAX_OBJECT_SIZE}
            )
        
        # Prepare S3 put parameters
        put_params = {
            'Bucket': bucket,
            'Key': key,
            'Body': content
        }
        
        # Add metadata if provided
        if metadata:
            # Ensure metadata keys are strings and values are strings
            sanitized_metadata = {}
            for k, v in metadata.items():
                if isinstance(k, str) and isinstance(v, (str, int, float, bool)):
                    sanitized_metadata[k] = str(v)
            
            if sanitized_metadata:
                put_params['Metadata'] = sanitized_metadata
        
        # Perform S3 put operation with retry logic
        s3_client = get_s3_client()
        response = retry_s3_operation(
            lambda: s3_client.put_object(**put_params)
        )
        
        # Log successful operation
        logger.info(f"Successfully created object: bucket={bucket}, key={key}")
        
        # Log security event for data creation
        security_logger_instance.log_authorization_success(
            client_id="lambda-function",
            resource=f"s3://{bucket}/{key}",
            action="create",
            scopes=["s3:crud"],
            request_id=getattr(context, 'aws_request_id', None) if 'context' in locals() else None
        )
        
        # Return success response with object metadata
        return create_success_response({
            "operation": "create",
            "bucket": bucket,
            "key": key,
            "etag": response.get('ETag', '').strip('"'),
            "size": len(content.encode('utf-8')) if isinstance(content, str) else len(content)
        })
        
    except ClientError as e:
        return ErrorHandler.handle_s3_client_error(e, "create", bucket, key)
    
    except NoCredentialsError:
        return ErrorHandler.handle_credentials_error("create")
    
    except Exception as e:
        return ErrorHandler.handle_unexpected_error(e, "create")

def retry_s3_operation(operation, max_retries: int = None):
    """
    Retry S3 operation with exponential backoff.
    
    Args:
        operation: Function to retry
        max_retries: Maximum number of retries
        
    Returns:
        Operation result
        
    Raises:
        Last exception if all retries fail
    """
    if max_retries is None:
        max_retries = Config.MAX_RETRIES
    
    last_exception = None
    
    for attempt in range(max_retries + 1):
        try:
            return operation()
        except (ClientError, BotoCoreError) as e:
            last_exception = e
            
            # Don't retry on certain error types
            if isinstance(e, ClientError):
                error_code = e.response['Error']['Code']
                non_retryable_errors = [
                    'NoSuchBucket', 'AccessDenied', 'InvalidBucketName',
                    'NoSuchKey', 'InvalidRequest'
                ]
                if error_code in non_retryable_errors:
                    raise e
            
            if attempt < max_retries:
                # Calculate backoff delay
                delay = min(
                    Config.RETRY_BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 1),
                    Config.RETRY_BACKOFF_MAX
                )
                logger.warning(f"S3 operation failed, retrying in {delay:.2f}s (attempt {attempt + 1}/{max_retries + 1})")
                time.sleep(delay)
    
    # All retries failed
    raise last_exception

def handle_read_operation(bucket: str, key: str) -> Dict[str, Any]:
    """
    Handle S3 read operation.
    
    Args:
        bucket: S3 bucket name
        key: Object key
        
    Returns:
        Operation result dictionary
    """
    try:
        # Validate inputs
        if not validate_object_key(key):
            return ErrorHandler.handle_validation_error(
                "INVALID_KEY",
                "Object key is invalid or exceeds maximum length",
                {"key": key, "max_length": Config.MAX_KEY_LENGTH}
            )
        
        # Perform S3 get operation with retry logic
        s3_client = get_s3_client()
        response = retry_s3_operation(
            lambda: s3_client.get_object(Bucket=bucket, Key=key)
        )
        
        # Read object content
        content = response['Body'].read()
        
        # Try to decode as UTF-8, fallback to base64 for binary content
        try:
            content_str = content.decode('utf-8')
            content_type = 'text'
        except UnicodeDecodeError:
            import base64
            content_str = base64.b64encode(content).decode('utf-8')
            content_type = 'binary'
        
        # Extract metadata
        object_metadata = {
            "key": key,
            "size": response.get('ContentLength', len(content)),
            "lastModified": response.get('LastModified').isoformat() if response.get('LastModified') else None,
            "etag": response.get('ETag', '').strip('"'),
            "contentType": response.get('ContentType', 'application/octet-stream'),
            "metadata": response.get('Metadata', {})
        }
        
        # Log successful operation
        logger.info(f"Successfully read object: bucket={bucket}, key={key}, size={object_metadata['size']}")
        
        # Log security event for data access
        security_logger_instance.log_authorization_success(
            client_id="lambda-function",
            resource=f"s3://{bucket}/{key}",
            action="read",
            scopes=["s3:crud"],
            request_id=getattr(context, 'aws_request_id', None) if 'context' in locals() else None
        )
        
        # Return success response
        return create_success_response({
            "operation": "read",
            "bucket": bucket,
            "content": content_str,
            "contentType": content_type,
            "metadata": object_metadata
        })
        
    except ClientError as e:
        return ErrorHandler.handle_s3_client_error(e, "read", bucket, key)
    
    except NoCredentialsError:
        return ErrorHandler.handle_credentials_error("read")
    
    except Exception as e:
        return ErrorHandler.handle_unexpected_error(e, "read")
def handle_update_operation(bucket: str, key: str, content: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle S3 update operation.
    
    Args:
        bucket: S3 bucket name
        key: Object key
        content: New object content
        metadata: Object metadata
        
    Returns:
        Operation result dictionary
    """
    try:
        # Validate inputs
        if not validate_object_key(key):
            return ErrorHandler.handle_validation_error(
                "INVALID_KEY",
                "Object key is invalid or exceeds maximum length",
                {"key": key, "max_length": Config.MAX_KEY_LENGTH}
            )
        
        if not validate_content(content):
            return ErrorHandler.handle_validation_error(
                "INVALID_CONTENT",
                "Content is invalid or exceeds maximum size",
                {"max_size": Config.MAX_OBJECT_SIZE}
            )
        
        # First, check if the object exists
        s3_client = get_s3_client()
        try:
            retry_s3_operation(
                lambda: s3_client.head_object(Bucket=bucket, Key=key)
            )
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                return ErrorHandler.handle_validation_error(
                    "OBJECT_NOT_FOUND",
                    f"Cannot update non-existent object: {key}",
                    {"bucket": bucket, "key": key}
                )
            # Re-raise other errors to be handled by outer try-catch
            raise
        
        # Prepare S3 put parameters for update
        put_params = {
            'Bucket': bucket,
            'Key': key,
            'Body': content
        }
        
        # Add metadata if provided
        if metadata:
            # Ensure metadata keys are strings and values are strings
            sanitized_metadata = {}
            for k, v in metadata.items():
                if isinstance(k, str) and isinstance(v, (str, int, float, bool)):
                    sanitized_metadata[k] = str(v)
            
            if sanitized_metadata:
                put_params['Metadata'] = sanitized_metadata
        
        # Perform S3 put operation (which overwrites existing object)
        response = retry_s3_operation(
            lambda: s3_client.put_object(**put_params)
        )
        
        # Log successful operation
        logger.info(f"Successfully updated object: bucket={bucket}, key={key}")
        
        # Log security event for data modification
        security_logger_instance.log_authorization_success(
            client_id="lambda-function",
            resource=f"s3://{bucket}/{key}",
            action="update",
            scopes=["s3:crud"],
            request_id=getattr(context, 'aws_request_id', None) if 'context' in locals() else None
        )
        
        # Return success response with object metadata
        return create_success_response({
            "operation": "update",
            "bucket": bucket,
            "key": key,
            "etag": response.get('ETag', '').strip('"'),
            "size": len(content.encode('utf-8')) if isinstance(content, str) else len(content)
        })
        
    except ClientError as e:
        return ErrorHandler.handle_s3_client_error(e, "update", bucket, key)
    
    except NoCredentialsError:
        return ErrorHandler.handle_credentials_error("update")
    
    except Exception as e:
        return ErrorHandler.handle_unexpected_error(e, "update")
def handle_delete_operation(bucket: str, key: str) -> Dict[str, Any]:
    """
    Handle S3 delete operation.
    
    Args:
        bucket: S3 bucket name
        key: Object key
        
    Returns:
        Operation result dictionary
    """
    try:
        # Validate inputs
        if not validate_object_key(key):
            return ErrorHandler.handle_validation_error(
                "INVALID_KEY",
                "Object key is invalid or exceeds maximum length",
                {"key": key, "max_length": Config.MAX_KEY_LENGTH}
            )
        
        # First, check if the object exists
        s3_client = get_s3_client()
        object_exists = True
        try:
            retry_s3_operation(
                lambda: s3_client.head_object(Bucket=bucket, Key=key)
            )
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                object_exists = False
            else:
                # Re-raise other errors to be handled by outer try-catch
                raise
        
        # If object doesn't exist, return appropriate error
        if not object_exists:
            return ErrorHandler.handle_validation_error(
                "OBJECT_NOT_FOUND",
                f"Cannot delete non-existent object: {key}",
                {"bucket": bucket, "key": key}
            )
        
        # Perform S3 delete operation with retry logic
        retry_s3_operation(
            lambda: s3_client.delete_object(Bucket=bucket, Key=key)
        )
        
        # Verify deletion by checking if object still exists
        try:
            retry_s3_operation(
                lambda: s3_client.head_object(Bucket=bucket, Key=key)
            )
            # If we reach here, object still exists - deletion failed
            logger.error(f"Object still exists after delete operation: bucket={bucket}, key={key}")
            return ErrorHandler._create_error_response(
                500,
                "DELETE_FAILED",
                "Object deletion was not successful"
            )
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                # Object successfully deleted
                pass
            else:
                # Other error during verification
                raise
        
        # Log successful operation
        logger.info(f"Successfully deleted object: bucket={bucket}, key={key}")
        
        # Log security event for data deletion (high-risk operation)
        security_logger_instance.log_authorization_success(
            client_id="lambda-function",
            resource=f"s3://{bucket}/{key}",
            action="delete",
            scopes=["s3:crud"],
            request_id=getattr(context, 'aws_request_id', None) if 'context' in locals() else None
        )
        
        # Return success response
        return create_success_response({
            "operation": "delete",
            "bucket": bucket,
            "key": key,
            "deleted": True
        })
        
    except ClientError as e:
        return ErrorHandler.handle_s3_client_error(e, "delete", bucket, key)
    
    except NoCredentialsError:
        return ErrorHandler.handle_credentials_error("delete")
    
    except Exception as e:
        return ErrorHandler.handle_unexpected_error(e, "delete")