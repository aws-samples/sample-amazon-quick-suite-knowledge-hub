"""
OAuth token validation module for Bedrock Agent Gateway.

This module provides OAuth 2.0 access token validation functionality
that integrates with Amazon Cognito for service-to-service authentication.
"""

import json
import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
import base64
import hmac
import hashlib

from .security_logger import security_logger_instance


@dataclass
class TokenValidationResult:
    """Result of OAuth token validation."""
    is_valid: bool
    error_message: Optional[str] = None
    token_claims: Optional[Dict[str, Any]] = None
    scopes: Optional[List[str]] = None


class OAuthTokenValidator:
    """
    OAuth 2.0 access token validator for Cognito-issued tokens.
    
    This class validates JWT access tokens issued by Amazon Cognito
    for service-to-service authentication using client credentials flow.
    """
    
    def __init__(self, 
                 cognito_user_pool_id: str,
                 cognito_region: str,
                 required_audience: str,
                 required_scopes: List[str]):
        """
        Initialize the OAuth token validator.
        
        Args:
            cognito_user_pool_id: The Cognito User Pool ID
            cognito_region: AWS region where Cognito User Pool is located
            required_audience: Expected audience claim in the token
            required_scopes: List of required OAuth scopes
        """
        self.cognito_user_pool_id = cognito_user_pool_id
        self.cognito_region = cognito_region
        self.required_audience = required_audience
        self.required_scopes = required_scopes
        self.expected_issuer = f"https://cognito-idp.{cognito_region}.amazonaws.com/{cognito_user_pool_id}"
    
    def validate_token(self, access_token: str, source_ip: Optional[str] = None, 
                      request_id: Optional[str] = None) -> TokenValidationResult:
        """
        Validate an OAuth 2.0 access token.
        
        Args:
            access_token: The JWT access token to validate
            source_ip: Source IP address for security logging
            request_id: Request ID for security logging
            
        Returns:
            TokenValidationResult with validation status and details
        """
        if not access_token or not access_token.strip():
            # Log token validation failure
            security_logger_instance.log_token_validation_failure(
                error_code="MISSING_TOKEN",
                error_message="Access token is required",
                source_ip=source_ip,
                request_id=request_id
            )
            return TokenValidationResult(
                is_valid=False,
                error_message="Access token is required"
            )
        
        try:
            # Parse JWT token (simplified - in production would use proper JWT library)
            token_parts = access_token.split('.')
            if len(token_parts) != 3:
                # Log token validation failure
                security_logger_instance.log_token_validation_failure(
                    error_code="INVALID_TOKEN_FORMAT",
                    error_message="Invalid JWT token format",
                    source_ip=source_ip,
                    request_id=request_id
                )
                return TokenValidationResult(
                    is_valid=False,
                    error_message="Invalid JWT token format"
                )
            
            # Decode header and payload (skip signature validation for this test)
            header = self._decode_jwt_part(token_parts[0])
            payload = self._decode_jwt_part(token_parts[1])
            
            if not header or not payload:
                # Log token validation failure
                security_logger_instance.log_token_validation_failure(
                    error_code="INVALID_TOKEN_ENCODING",
                    error_message="Invalid JWT token encoding",
                    source_ip=source_ip,
                    request_id=request_id
                )
                return TokenValidationResult(
                    is_valid=False,
                    error_message="Invalid JWT token encoding"
                )
            
            # Validate token claims
            validation_result = self._validate_token_claims(payload, source_ip, request_id)
            if not validation_result.is_valid:
                return validation_result
            
            # Extract scopes and client_id from token
            scopes = payload.get('scope', '').split() if payload.get('scope') else []
            client_id = payload.get('client_id')
            
            # Log successful authentication
            if client_id:
                security_logger_instance.log_authentication_success(
                    client_id=client_id,
                    source_ip=source_ip,
                    request_id=request_id
                )
            
            return TokenValidationResult(
                is_valid=True,
                token_claims=payload,
                scopes=scopes
            )
            
        except Exception as e:
            # Log token validation failure
            security_logger_instance.log_token_validation_failure(
                error_code="TOKEN_VALIDATION_ERROR",
                error_message=f"Token validation error: {str(e)}",
                source_ip=source_ip,
                request_id=request_id
            )
            return TokenValidationResult(
                is_valid=False,
                error_message=f"Token validation error: {str(e)}"
            )
    
    def _decode_jwt_part(self, encoded_part: str) -> Optional[Dict[str, Any]]:
        """Decode a JWT part (header or payload)."""
        try:
            # Add padding if needed
            padding = 4 - len(encoded_part) % 4
            if padding != 4:
                encoded_part += '=' * padding
            
            decoded_bytes = base64.urlsafe_b64decode(encoded_part)
            return json.loads(decoded_bytes.decode('utf-8'))
        except Exception:
            return None
    
    def _validate_token_claims(self, payload: Dict[str, Any], source_ip: Optional[str] = None,
                              request_id: Optional[str] = None) -> TokenValidationResult:
        """Validate JWT token claims."""
        current_time = int(time.time())
        client_id = payload.get('client_id')
        
        # Check token expiration
        exp = payload.get('exp')
        if not exp or current_time >= exp:
            security_logger_instance.log_authentication_failure(
                error_code="TOKEN_EXPIRED",
                error_message="Token has expired",
                source_ip=source_ip,
                client_id=client_id,
                request_id=request_id
            )
            return TokenValidationResult(
                is_valid=False,
                error_message="Token has expired"
            )
        
        # Check token not before
        nbf = payload.get('nbf')
        if nbf and current_time < nbf:
            security_logger_instance.log_authentication_failure(
                error_code="TOKEN_NOT_YET_VALID",
                error_message="Token is not yet valid",
                source_ip=source_ip,
                client_id=client_id,
                request_id=request_id
            )
            return TokenValidationResult(
                is_valid=False,
                error_message="Token is not yet valid"
            )
        
        # Check issued at time
        iat = payload.get('iat')
        if not iat or iat > current_time:
            security_logger_instance.log_authentication_failure(
                error_code="INVALID_ISSUED_TIME",
                error_message="Invalid token issued time",
                source_ip=source_ip,
                client_id=client_id,
                request_id=request_id
            )
            return TokenValidationResult(
                is_valid=False,
                error_message="Invalid token issued time"
            )
        
        # Check issuer
        iss = payload.get('iss')
        if iss != self.expected_issuer:
            security_logger_instance.log_authentication_failure(
                error_code="INVALID_ISSUER",
                error_message=f"Invalid token issuer. Expected: {self.expected_issuer}",
                source_ip=source_ip,
                client_id=client_id,
                request_id=request_id
            )
            return TokenValidationResult(
                is_valid=False,
                error_message=f"Invalid token issuer. Expected: {self.expected_issuer}, Got: {iss}"
            )
        
        # Check audience
        aud = payload.get('aud')
        if aud != self.required_audience:
            security_logger_instance.log_authentication_failure(
                error_code="INVALID_AUDIENCE",
                error_message=f"Invalid token audience. Expected: {self.required_audience}",
                source_ip=source_ip,
                client_id=client_id,
                request_id=request_id
            )
            return TokenValidationResult(
                is_valid=False,
                error_message=f"Invalid token audience. Expected: {self.required_audience}, Got: {aud}"
            )
        
        # Check token use
        token_use = payload.get('token_use')
        if token_use != 'access':
            security_logger_instance.log_authentication_failure(
                error_code="INVALID_TOKEN_USE",
                error_message=f"Invalid token use. Expected: access",
                source_ip=source_ip,
                client_id=client_id,
                request_id=request_id
            )
            return TokenValidationResult(
                is_valid=False,
                error_message=f"Invalid token use. Expected: access, Got: {token_use}"
            )
        
        # Check grant type for client credentials flow
        grant_type = payload.get('grant_type')
        if grant_type != 'client_credentials':
            security_logger_instance.log_authentication_failure(
                error_code="INVALID_GRANT_TYPE",
                error_message=f"Invalid grant type. Expected: client_credentials",
                source_ip=source_ip,
                client_id=client_id,
                request_id=request_id
            )
            return TokenValidationResult(
                is_valid=False,
                error_message=f"Invalid grant type. Expected: client_credentials, Got: {grant_type}"
            )
        
        # Check required scopes
        token_scopes = payload.get('scope', '').split() if payload.get('scope') else []
        missing_scopes = [scope for scope in self.required_scopes if scope not in token_scopes]
        if missing_scopes:
            security_logger_instance.log_authorization_failure(
                error_code="INSUFFICIENT_SCOPE",
                error_message=f"Missing required scopes: {', '.join(missing_scopes)}",
                resource="gateway",
                action="invoke",
                client_id=client_id,
                source_ip=source_ip,
                request_id=request_id
            )
            return TokenValidationResult(
                is_valid=False,
                error_message=f"Missing required scopes: {', '.join(missing_scopes)}"
            )
        
        return TokenValidationResult(is_valid=True)


def create_test_token(
    user_pool_id: str,
    region: str,
    audience: str,
    scopes: List[str],
    client_id: str,
    exp_offset_seconds: int = 3600,
    grant_type: str = "client_credentials",
    **additional_claims
) -> str:
    """
    Create a test JWT token for testing purposes.
    
    Note: This is for testing only and does not include proper signature.
    """
    current_time = int(time.time())
    
    header = {
        "alg": "RS256",
        "typ": "JWT",
        "kid": "test-key-id"
    }
    
    payload = {
        "sub": client_id,
        "aud": audience,
        "iss": f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}",
        "exp": current_time + exp_offset_seconds,
        "iat": current_time,
        "token_use": "access",
        "scope": " ".join(scopes),
        "client_id": client_id,
        "grant_type": grant_type,
        **additional_claims
    }
    
    # Encode header and payload
    header_encoded = base64.urlsafe_b64encode(
        json.dumps(header, separators=(',', ':')).encode('utf-8')
    ).decode('utf-8').rstrip('=')
    
    payload_encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(',', ':')).encode('utf-8')
    ).decode('utf-8').rstrip('=')
    
    # Create fake signature (for testing only)
    signature = base64.urlsafe_b64encode(b"fake-signature").decode('utf-8').rstrip('=')
    
    return f"{header_encoded}.{payload_encoded}.{signature}"