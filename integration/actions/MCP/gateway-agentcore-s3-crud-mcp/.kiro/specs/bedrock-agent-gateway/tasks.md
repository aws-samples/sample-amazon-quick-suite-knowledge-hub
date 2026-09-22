# Implementation Plan

- [x] 1. Create CloudFormation template structure and parameters




  - Define template metadata, description, and version
  - Create input parameters for S3 bucket name, environment, and deployment configuration
  - Set up template outputs for gateway endpoint URL and Cognito details
  - _Requirements: 1.1, 1.2, 1.3_

- [x] 2. Implement Cognito User Pool for service-to-service OAuth




  - [x] 2.1 Create Cognito User Pool with OAuth 2.0 configuration


    - Configure user pool for client credentials flow
    - Set up OAuth 2.0 scopes for service authorization (s3:crud, gateway:invoke)
    - Configure token expiration policies for access tokens
    - _Requirements: 2.1_

  - [x] 2.2 Create Cognito User Pool Client for service authentication


    - Configure app client for client credentials grant type
    - Set up client authentication flow settings
    - Configure OAuth scopes and allowed OAuth flows
    - _Requirements: 2.1_

  - [x] 2.3 Write property test for OAuth token validation










    - **Property 1: Service authentication token validation**
    - **Validates: Requirements 2.2, 2.4**

  - [x] 2.4 Write property test for authentication failure handling





    - **Property 2: Service authentication failure handling**
    - **Validates: Requirements 2.3, 2.5**

- [x] 3. Implement Python Lambda function for S3 CRUD operations





  - [x] 3.1 Create Lambda function structure and dependencies


    - Set up Python runtime environment and requirements
    - Configure Lambda function handler and environment variables
    - Set up boto3 S3 client configuration
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [x] 3.2 Implement S3 create operation


    - Write function to create objects in S3 bucket
    - Add input validation for object keys and content
    - Implement error handling for S3 create operations
    - _Requirements: 3.1_

  - [ ]* 3.3 Write property test for CRUD round-trip consistency
    - **Property 3: CRUD operation round-trip consistency**
    - **Validates: Requirements 3.1, 3.2**

  - [x] 3.4 Implement S3 read operation


    - Write function to retrieve objects from S3 bucket
    - Handle object not found scenarios
    - Return object content and metadata
    - _Requirements: 3.2_

  - [x] 3.5 Implement S3 update operation


    - Write function to update existing objects in S3 bucket
    - Validate object existence before update
    - Handle update-specific error scenarios
    - _Requirements: 3.3_

  - [ ]* 3.6 Write property test for update operation consistency
    - **Property 4: Update operation consistency**
    - **Validates: Requirements 3.3, 3.2**

  - [x] 3.7 Implement S3 delete operation


    - Write function to delete objects from S3 bucket
    - Handle delete confirmation and error scenarios
    - Implement proper cleanup logic
    - _Requirements: 3.4_

  - [ ]* 3.8 Write property test for delete operation effectiveness
    - **Property 5: Delete operation effectiveness**
    - **Validates: Requirements 3.4, 3.5**

  - [x] 3.9 Implement comprehensive error handling


    - Add error handling for permissions, network issues, and invalid input
    - Implement retry logic with exponential backoff
    - Create sanitized error response formatting
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [ ]* 3.10 Write property test for error message specificity
    - **Property 7: Error message specificity**
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.5**

  - [ ]* 3.11 Write property test for input validation consistency
    - **Property 8: Input validation consistency**
    - **Validates: Requirements 5.3**

  - [ ]* 3.12 Write unit tests for Lambda function operations
    - Create unit tests for each CRUD operation
    - Test error handling scenarios
    - Test input validation logic
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 5.1, 5.2, 5.3_

- [x] 4. Create IAM roles and policies





  - [x] 4.1 Create Lambda execution role


    - Define IAM role for Lambda function execution
    - Attach basic Lambda execution policy
    - Add S3 access permissions for specified bucket
    - _Requirements: 1.4, 6.1, 6.2_

  - [x] 4.2 Create Bedrock Gateway service role


    - Define IAM role for Bedrock Gateway service
    - Add permissions for Lambda function invocation
    - Configure Cognito token validation permissions
    - _Requirements: 1.4, 6.1_

  - [x] 4.3 Implement least-privilege access policies


    - Review and minimize permissions for all roles
    - Implement resource-specific access controls
    - Add condition-based policy restrictions
    - _Requirements: 6.1_

- [x] 5. Checkpoint - Ensure all tests pass





  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Configure Bedrock AgentCore Gateway








  - [x] 6.1 Create Gateway with OAuth inbound authorization



    - Configure gateway with Cognito OIDC discovery URL
    - Set up OAuth 2.0 inbound authorization configuration
    - Configure allowed audiences and scopes
    - _Requirements: 2.2, 4.1_



  - [x] 6.2 Configure Lambda target for Gateway





    - Set up Lambda function as MCP target
    - Configure request/response transformation
    - Set up proper IAM permissions for gateway-to-lambda communication


    - _Requirements: 4.1_

  - [x] 6.3 Define MCP tool schemas




    - Create MCP tool definitions for S3 CRUD operations
    - Define input schemas for each operation
    - Configure tool descriptions and metadata
    - _Requirements: 4.2, 4.3, 4.4, 4.5_

  - [ ]* 6.4 Write property test for MCP request routing consistency
    - **Property 6: MCP request routing consistency**
    - **Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5**

- [x] 7. Add CloudFormation outputs and finalization





  - [x] 7.1 Configure template outputs


    - Add Gateway endpoint URL output
    - Add Cognito User Pool ID and client details
    - Add Lambda function ARN and other resource identifiers
    - _Requirements: 1.3_

  - [x] 7.2 Add resource dependencies and ordering


    - Configure proper DependsOn relationships
    - Set up resource creation order
    - Add deletion policies where appropriate
    - _Requirements: 1.1, 1.5_

  - [ ]* 7.3 Write integration tests for CloudFormation deployment
    - Test template deployment and resource creation
    - Verify proper IAM permissions setup
    - Test template deletion and cleanup
    - _Requirements: 1.1, 1.4, 1.5_

- [x] 8. Implement security and logging














  - [x] 8.1 Configure CloudWatch logging



    - Set up Lambda function logging
    - Configure Gateway request/response logging
    - Add security event logging
    - _Requirements: 2.5, 6.3_

  - [x] 8.2 Implement security event monitoring


    - Add authentication failure logging
    - Configure authorization event tracking
    - Implement error logging without sensitive data exposure
    - _Requirements: 2.5, 6.3, 6.5_

  - [ ]* 8.3 Write property test for security event logging
    - **Property 9: Security event logging**
    - **Validates: Requirements 2.5, 6.3, 6.5**

  - [x] 8.4 Configure HTTPS/TLS for all communications


    - Ensure Gateway uses HTTPS endpoints
    - Configure Lambda-to-S3 encrypted connections
    - Verify end-to-end encryption
    - _Requirements: 6.4_

- [x] 9. Final Checkpoint - Make sure all tests are passing





  - Ensure all tests pass, ask the user if questions arise.