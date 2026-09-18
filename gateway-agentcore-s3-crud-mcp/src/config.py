"""
Configuration module for Lambda function environment variables and settings.
"""

import os
import logging
from typing import Optional

class Config:
    """Configuration class for Lambda function settings."""
    
    # S3 Configuration
    S3_BUCKET_NAME: Optional[str] = os.environ.get('S3_BUCKET_NAME')
    
    # Environment Configuration
    ENVIRONMENT: str = os.environ.get('ENVIRONMENT', 'dev')
    PROJECT_NAME: str = os.environ.get('PROJECT_NAME', 'bedrock-agent-gateway')
    
    # Logging Configuration
    ENABLE_LOGGING: bool = os.environ.get('ENABLE_LOGGING', 'false').lower() == 'true'
    LOG_LEVEL: str = os.environ.get('LOG_LEVEL', 'INFO').upper()
    SECURITY_LOG_GROUP: Optional[str] = os.environ.get('SECURITY_LOG_GROUP')
    
    # Retry Configuration
    MAX_RETRIES: int = int(os.environ.get('MAX_RETRIES', '3'))
    RETRY_BACKOFF_BASE: float = float(os.environ.get('RETRY_BACKOFF_BASE', '1.0'))
    RETRY_BACKOFF_MAX: float = float(os.environ.get('RETRY_BACKOFF_MAX', '60.0'))
    
    # Validation Configuration
    MAX_OBJECT_SIZE: int = int(os.environ.get('MAX_OBJECT_SIZE', '5242880'))  # 5MB default
    MAX_KEY_LENGTH: int = int(os.environ.get('MAX_KEY_LENGTH', '1024'))
    
    @classmethod
    def validate_config(cls) -> None:
        """Validate required configuration parameters."""
        if not cls.S3_BUCKET_NAME:
            raise ValueError("S3_BUCKET_NAME environment variable is required")
    
    @classmethod
    def validate_https_configuration(cls) -> None:
        """Validate that HTTPS/TLS is properly configured for all communications."""
        import ssl
        import boto3
        from botocore.config import Config as BotoCoreConfig
        
        # Verify SSL/TLS support is available
        if not hasattr(ssl, 'create_default_context'):
            raise RuntimeError("SSL/TLS support is not available in this environment")
        
        # Test S3 client HTTPS configuration
        try:
            test_s3_client = boto3.client(
                's3',
                config=BotoCoreConfig(use_ssl=True, signature_version='s3v4')
            )
            # Verify the client is configured for HTTPS
            if not test_s3_client._client_config.use_ssl:
                raise RuntimeError("S3 client is not configured to use HTTPS")
        except Exception as e:
            raise RuntimeError(f"Failed to configure S3 client with HTTPS: {e}")
        
        # Test CloudWatch Logs client HTTPS configuration
        try:
            test_logs_client = boto3.client(
                'logs',
                config=BotoCoreConfig(use_ssl=True, signature_version='v4')
            )
            # Verify the client is configured for HTTPS
            if not test_logs_client._client_config.use_ssl:
                raise RuntimeError("CloudWatch Logs client is not configured to use HTTPS")
        except Exception as e:
            raise RuntimeError(f"Failed to configure CloudWatch Logs client with HTTPS: {e}")
        
        logging.getLogger(__name__).info("HTTPS/TLS configuration validated successfully")
    
    @classmethod
    def configure_logging(cls) -> None:
        """Configure logging based on environment variables."""
        # Set up root logger
        root_logger = logging.getLogger()
        
        # Clear existing handlers
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
        
        # Set log level
        log_level = getattr(logging, cls.LOG_LEVEL, logging.INFO)
        root_logger.setLevel(log_level)
        
        # Create console handler with formatting
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        
        # Create formatter
        if cls.ENABLE_LOGGING:
            # Structured logging format for CloudWatch
            formatter = logging.Formatter(
                '{"timestamp": "%(asctime)s", "level": "%(levelname)s", '
                '"logger": "%(name)s", "message": "%(message)s", '
                '"environment": "' + cls.ENVIRONMENT + '", '
                '"project": "' + cls.PROJECT_NAME + '"}'
            )
        else:
            # Simple format for minimal logging
            formatter = logging.Formatter('%(levelname)s: %(message)s')
        
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
        
        # Configure security logger
        security_logger = logging.getLogger('security')
        security_logger.setLevel(logging.INFO if cls.ENABLE_LOGGING else logging.ERROR)
        
        # Prevent duplicate logs
        security_logger.propagate = False
        security_handler = logging.StreamHandler()
        security_handler.setFormatter(formatter)
        security_logger.addHandler(security_handler)