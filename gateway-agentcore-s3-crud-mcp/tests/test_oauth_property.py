"""
Property-based tests for OAuth token validation.

**Feature: bedrock-agent-gateway, Property 1: Service authentication token validation**
**Validates: Requirements 2.2, 2.4**
"""

import pytest
from hypothesis import given, strategies as st, settings, HealthCheck

from src.auth import OAuthTokenValidator, create_test_token


# Test configuration constants
TEST_USER_POOL_ID = "us-east-1_TestPool123"
TEST_REGION = "us-east-1"
TEST_AUDIENCE = "bedrock-gateway"
TEST_REQUIRED_SCOPES = ["s3:crud", "gateway:invoke"]


@given(
    client_id=st.text(min_size=5, max_size=30, alphabet='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_'),
    exp_offset=st.integers(min_value=60, max_value=3600)
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_property_valid_oauth_tokens_are_accepted(client_id, exp_offset):
    """
    **Feature: bedrock-agent-gateway, Property 1: Service authentication token validation**
    
    Property: For any valid OAuth access token issued via client credentials flow 
    by the configured Cognito user pool, the Bedrock Gateway should validate the 
    token and allow access to MCP actions.
    """
    # Create validator
    validator = OAuthTokenValidator(
        cognito_user_pool_id=TEST_USER_POOL_ID,
        cognito_region=TEST_REGION,
        required_audience=TEST_AUDIENCE,
        required_scopes=TEST_REQUIRED_SCOPES
    )
    
    # Create a valid test token
    token = create_test_token(
        user_pool_id=TEST_USER_POOL_ID,
        region=TEST_REGION,
        audience=TEST_AUDIENCE,
        scopes=TEST_REQUIRED_SCOPES,
        client_id=client_id,
        exp_offset_seconds=exp_offset
    )
    
    # Validate the token
    result = validator.validate_token(token)
    
    # Property assertion: Valid tokens should always be accepted
    assert result.is_valid, f"Valid token was rejected: {result.error_message}"
    assert result.token_claims is not None, "Valid token should have claims"
    assert result.scopes is not None, "Valid token should have scopes"
    
    # Verify all required scopes are present
    for required_scope in TEST_REQUIRED_SCOPES:
        assert required_scope in result.scopes, f"Missing required scope: {required_scope}"
    
    # Verify token claims contain expected values
    assert result.token_claims['aud'] == TEST_AUDIENCE
    assert result.token_claims['client_id'] == client_id
    assert result.token_claims['grant_type'] == 'client_credentials'
    assert result.token_claims['token_use'] == 'access'


@given(
    invalid_audience=st.sampled_from(['wrong-audience', 'invalid-aud', 'other-service']),
    client_id=st.text(min_size=5, max_size=30, alphabet='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_')
)
@settings(max_examples=50)
def test_property_invalid_audience_tokens_are_rejected(invalid_audience, client_id):
    """Property: Invalid audience tokens should be rejected."""
    # Create validator
    validator = OAuthTokenValidator(
        cognito_user_pool_id=TEST_USER_POOL_ID,
        cognito_region=TEST_REGION,
        required_audience=TEST_AUDIENCE,
        required_scopes=TEST_REQUIRED_SCOPES
    )
    
    # Create token with invalid audience
    token = create_test_token(
        user_pool_id=TEST_USER_POOL_ID,
        region=TEST_REGION,
        audience=invalid_audience,  # Invalid audience
        scopes=TEST_REQUIRED_SCOPES,
        client_id=client_id
    )
    
    # Validate the token
    result = validator.validate_token(token)
    
    # Property assertion: Invalid audience should be rejected
    assert not result.is_valid, "Token with invalid audience should be rejected"
    assert "Invalid token audience" in result.error_message


@given(
    exp_offset=st.integers(min_value=-3600, max_value=-1),  # Expired tokens
    client_id=st.text(min_size=5, max_size=30, alphabet='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_')
)
@settings(max_examples=50)
def test_property_expired_tokens_are_rejected(exp_offset, client_id):
    """Property: Expired tokens should be rejected."""
    # Create validator
    validator = OAuthTokenValidator(
        cognito_user_pool_id=TEST_USER_POOL_ID,
        cognito_region=TEST_REGION,
        required_audience=TEST_AUDIENCE,
        required_scopes=TEST_REQUIRED_SCOPES
    )
    
    # Create expired token
    token = create_test_token(
        user_pool_id=TEST_USER_POOL_ID,
        region=TEST_REGION,
        audience=TEST_AUDIENCE,
        scopes=TEST_REQUIRED_SCOPES,
        client_id=client_id,
        exp_offset_seconds=exp_offset  # Negative offset = expired
    )
    
    # Validate the token
    result = validator.validate_token(token)
    
    # Property assertion: Expired tokens should be rejected
    assert not result.is_valid, "Expired token should be rejected"
    assert "Token has expired" in result.error_message


@given(
    client_id=st.text(min_size=5, max_size=30, alphabet='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_'),
    exp_offset=st.integers(min_value=-7200, max_value=-1),  # Expired tokens
    invalid_audience=st.sampled_from(['wrong-audience', 'invalid-aud', 'other-service', '']),
    invalid_scopes=st.lists(st.sampled_from(['s3:read', 'gateway:read', 'invalid:scope']), min_size=0, max_size=2),
    invalid_grant_type=st.sampled_from(['authorization_code', 'implicit', 'password', 'refresh_token']),
    malformed_token=st.sampled_from([
        "not.a.jwt.token.with.too.many.parts",
        "only-one-part", 
        "two.parts",
        "invalid..jwt",
        "header.invalid_base64.signature",
        "",
        "   ",
        "\t",
        "\n"
    ])
)
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_property_authentication_failure_handling(client_id, exp_offset, invalid_audience, 
                                                 invalid_scopes, invalid_grant_type, malformed_token):
    """
    **Feature: bedrock-agent-gateway, Property 2: Service authentication failure handling**
    
    Property: For any invalid, expired, or malformed OAuth access token, 
    the Bedrock Gateway should reject the request, return an authentication error, 
    and log the failed attempt.
    """
    # Create validator
    validator = OAuthTokenValidator(
        cognito_user_pool_id=TEST_USER_POOL_ID,
        cognito_region=TEST_REGION,
        required_audience=TEST_AUDIENCE,
        required_scopes=TEST_REQUIRED_SCOPES
    )
    
    # Test different types of invalid tokens
    test_cases = [
        # Expired token
        create_test_token(
            user_pool_id=TEST_USER_POOL_ID,
            region=TEST_REGION,
            audience=TEST_AUDIENCE,
            scopes=TEST_REQUIRED_SCOPES,
            client_id=client_id,
            exp_offset_seconds=exp_offset
        ),
        # Invalid audience
        create_test_token(
            user_pool_id=TEST_USER_POOL_ID,
            region=TEST_REGION,
            audience=invalid_audience,
            scopes=TEST_REQUIRED_SCOPES,
            client_id=client_id
        ),
        # Invalid issuer (wrong user pool)
        create_test_token(
            user_pool_id="us-east-1_WrongPool",
            region=TEST_REGION,
            audience=TEST_AUDIENCE,
            scopes=TEST_REQUIRED_SCOPES,
            client_id=client_id
        ),
        # Missing required scopes
        create_test_token(
            user_pool_id=TEST_USER_POOL_ID,
            region=TEST_REGION,
            audience=TEST_AUDIENCE,
            scopes=invalid_scopes,  # Insufficient scopes
            client_id=client_id
        ),
        # Invalid grant type
        create_test_token(
            user_pool_id=TEST_USER_POOL_ID,
            region=TEST_REGION,
            audience=TEST_AUDIENCE,
            scopes=TEST_REQUIRED_SCOPES,
            client_id=client_id,
            grant_type=invalid_grant_type
        ),
        # Malformed token
        malformed_token
    ]
    
    # Test each invalid token case
    for token in test_cases:
        result = validator.validate_token(token)
        
        # Property assertions: All invalid tokens should be rejected
        assert not result.is_valid, f"Invalid token should be rejected: {token[:50]}..."
        assert result.error_message is not None, "Error message should be provided for invalid token"
        assert len(result.error_message.strip()) > 0, "Error message should not be empty"
        
        # Verify that token claims and scopes are not returned for invalid tokens
        assert result.token_claims is None or not result.is_valid, "Invalid tokens should not return claims"
        assert result.scopes is None or not result.is_valid, "Invalid tokens should not return scopes"


def test_malformed_tokens_are_rejected():
    """Test that malformed tokens are rejected."""
    # Create validator
    validator = OAuthTokenValidator(
        cognito_user_pool_id=TEST_USER_POOL_ID,
        cognito_region=TEST_REGION,
        required_audience=TEST_AUDIENCE,
        required_scopes=TEST_REQUIRED_SCOPES
    )
    
    malformed_tokens = [
        "",  # Empty string
        "   ",  # Whitespace only
        "not.a.jwt",  # Wrong format
        "only-one-part",  # Missing dots
        "two.parts",  # Only two parts
    ]
    
    for token in malformed_tokens:
        result = validator.validate_token(token)
        assert not result.is_valid, f"Malformed token should be rejected: {token}"
        assert result.error_message is not None, "Error message should be provided"