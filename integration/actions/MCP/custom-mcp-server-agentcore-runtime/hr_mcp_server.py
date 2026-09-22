"""
HR MCP Server - Demonstrates hosting HR tools on Amazon Bedrock AgentCore Runtime

This MCP server provides 5 HR-related tools that can be invoked by AI agents:
1. create_leave_request - Submit employee leave requests
2. update_employee_record - Modify employee information
3. check_leave_balance - Query remaining leave days
4. create_support_ticket - Create IT/HR support tickets
5. get_employee_info - Retrieve complete employee details

The server uses FastMCP with stateless_http=True for AgentCore Runtime compatibility.
"""

import json
from datetime import datetime

from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
# - host="0.0.0.0" allows external connections (required for AgentCore Runtime)
# - stateless_http=True enables session isolation via Mcp-Session-Id header
# This configuration is REQUIRED for Amazon Bedrock AgentCore Runtime deployment
mcp = FastMCP(host="0.0.0.0", stateless_http=True)

# Mock database for demonstration purposes
# In production, this would connect to a real database (RDS, DynamoDB, etc.)
EMPLOYEES_DB = {
    "EMP001": {
        "name": "Alice Johnson",
        "department": "Engineering",
        "leave_balance": 15,  # Days of leave remaining
        "email": "alice@company.com",
    },
    "EMP002": {
        "name": "Bob Smith",
        "department": "HR",
        "leave_balance": 20,
        "email": "bob@company.com",
    },
}

# In-memory storage for leave requests and tickets
# In production, these would be persisted to a database
LEAVE_REQUESTS = []
SUPPORT_TICKETS = []


# Tool 1: Create Leave Request
# Purpose: Allow employees to submit leave requests through the AI agent
# Why: Automates the leave request process without manual form filling
# Use case: "I need to request vacation from March 1-5"
@mcp.tool()
def create_leave_request(
    employee_id: str, start_date: str, end_date: str, leave_type: str
) -> str:
    """Create a leave request for an employee

    This tool creates a new leave request in the system. It validates the employee exists,
    generates a unique request ID, and stores the request with a pending status.

    Args:
        employee_id: Employee ID (e.g., EMP001) - Used to identify who is requesting leave
        start_date: Start date in YYYY-MM-DD format - First day of leave
        end_date: End date in YYYY-MM-DD format - Last day of leave
        leave_type: Type of leave (vacation, sick, personal) - Category for tracking

    Returns:
        JSON string with success status, request ID, and full request details

    Example:
        create_leave_request("EMP001", "2024-03-01", "2024-03-05", "vacation")
    """
    # Validate employee exists in the system
    if employee_id not in EMPLOYEES_DB:
        return f"Error: Employee {employee_id} not found"

    # Create leave request object with auto-generated ID
    # Request ID format: LR001, LR002, etc.
    request = {
        "request_id": f"LR{len(LEAVE_REQUESTS) + 1:03d}",
        "employee_id": employee_id,
        "start_date": start_date,
        "end_date": end_date,
        "leave_type": leave_type,
        "status": "pending",  # All new requests start as pending
        "created_at": datetime.now().isoformat(),  # Timestamp for audit trail
    }

    # Store the request (in production, this would be a database insert)
    LEAVE_REQUESTS.append(request)

    # Return formatted JSON response
    return json.dumps(
        {
            "success": True,
            "message": f"Leave request {request['request_id']} created successfully",
            "request": request,
        },
        indent=2,
    )


# Tool 2: Update Employee Record
# Purpose: Allow authorized updates to employee information
# Why: Enables self-service updates (email, department changes) through conversational AI
# Use case: "Update my email to newemail@company.com"
@mcp.tool()
def update_employee_record(employee_id: str, field: str, value: str) -> str:
    """Update an employee record field

    This tool allows modification of specific employee fields. It validates both the
    employee existence and the field being updated to prevent unauthorized changes.
    Only 'department' and 'email' fields can be updated for security.

    Args:
        employee_id: Employee ID (e.g., EMP001) - Target employee to update
        field: Field to update (department, email) - Which attribute to modify
        value: New value for the field - The updated information

    Returns:
        JSON string with success status, old value, and new value for verification

    Example:
        update_employee_record("EMP001", "email", "alice.new@company.com")
    """
    # Validate employee exists
    if employee_id not in EMPLOYEES_DB:
        return f"Error: Employee {employee_id} not found"

    # Security check: Only allow updates to specific fields
    # This prevents unauthorized modification of sensitive data like leave_balance
    if field not in ["department", "email"]:
        return f"Error: Cannot update field '{field}'. Only 'department' and 'email' are allowed"

    # Store old value for audit trail
    old_value = EMPLOYEES_DB[employee_id].get(field)

    # Update the field
    EMPLOYEES_DB[employee_id][field] = value

    # Return confirmation with before/after values
    return json.dumps(
        {
            "success": True,
            "message": f"Updated {field} for {employee_id}",
            "old_value": old_value,
            "new_value": value,
        },
        indent=2,
    )


# Tool 3: Check Leave Balance
# Purpose: Query remaining leave days for an employee
# Why: Provides instant access to leave balance without navigating HR portals
# Use case: "How many vacation days do I have left?"
@mcp.tool()
def check_leave_balance(employee_id: str) -> str:
    """Check leave balance for an employee

    This tool retrieves the current leave balance along with basic employee information.
    Useful for employees to check their remaining days before requesting leave.

    Args:
        employee_id: Employee ID (e.g., EMP001) - Employee to check balance for

    Returns:
        JSON string with employee name, department, and current leave balance

    Example:
        check_leave_balance("EMP001")
        Returns: {"employee_id": "EMP001", "name": "Alice Johnson", "leave_balance": 15, ...}
    """
    # Validate employee exists
    if employee_id not in EMPLOYEES_DB:
        return f"Error: Employee {employee_id} not found"

    # Retrieve employee data
    employee = EMPLOYEES_DB[employee_id]

    # Return formatted response with relevant information
    return json.dumps(
        {
            "employee_id": employee_id,
            "name": employee["name"],
            "leave_balance": employee["leave_balance"],  # Days remaining
            "department": employee["department"],
        },
        indent=2,
    )


# Tool 4: Create Support Ticket
# Purpose: Create IT/HR support tickets through conversational interface
# Why: Simplifies ticket creation - no need to navigate ticketing systems
# Use case: "I need help resetting my laptop password"
@mcp.tool()
def create_support_ticket(employee_id: str, category: str, description: str) -> str:
    """Create an IT/HR support ticket

    This tool creates support tickets for various employee issues (IT problems,
    HR questions, facilities requests). Tickets are auto-assigned a unique ID
    and tracked with timestamps.

    Args:
        employee_id: Employee ID (e.g., EMP001) - Who is creating the ticket
        category: Ticket category (IT, HR, Facilities) - Department to route to
        description: Description of the issue - Detailed explanation of the problem

    Returns:
        JSON string with success status, ticket ID, and full ticket details

    Example:
        create_support_ticket("EMP001", "IT", "Laptop won't connect to VPN")
    """
    # Validate employee exists
    if employee_id not in EMPLOYEES_DB:
        return f"Error: Employee {employee_id} not found"

    # Create ticket object with auto-generated ID
    # Ticket ID format: TKT0001, TKT0002, etc.
    ticket = {
        "ticket_id": f"TKT{len(SUPPORT_TICKETS) + 1:04d}",
        "employee_id": employee_id,
        "category": category,  # Used for routing to correct support team
        "description": description,
        "status": "open",  # All new tickets start as open
        "created_at": datetime.now().isoformat(),  # Timestamp for SLA tracking
    }

    # Store the ticket (in production, this would trigger notifications)
    SUPPORT_TICKETS.append(ticket)

    # Return confirmation with ticket details
    return json.dumps(
        {
            "success": True,
            "message": f"Support ticket {ticket['ticket_id']} created successfully",
            "ticket": ticket,
        },
        indent=2,
    )


# Tool 5: Get Employee Info
# Purpose: Retrieve comprehensive employee information including history
# Why: Provides a complete view of employee data, requests, and tickets in one call
# Use case: "Show me all my information and pending requests"
@mcp.tool()
def get_employee_info(employee_id: str) -> str:
    """Get complete employee information

    This tool provides a comprehensive view of an employee including:
    - Basic information (name, department, email, leave balance)
    - All leave requests (pending, approved, rejected)
    - All support tickets (open, closed)

    This is useful for getting a complete overview in a single query.

    Args:
        employee_id: Employee ID (e.g., EMP001) - Employee to retrieve information for

    Returns:
        JSON string with complete employee profile, leave requests, and support tickets

    Example:
        get_employee_info("EMP001")
        Returns: Full employee profile with all associated records
    """
    # Validate employee exists
    if employee_id not in EMPLOYEES_DB:
        return f"Error: Employee {employee_id} not found"

    # Get basic employee information
    employee = EMPLOYEES_DB[employee_id]

    # Filter leave requests for this employee
    # This shows all historical leave requests, not just pending ones
    employee_leaves = [lr for lr in LEAVE_REQUESTS if lr["employee_id"] == employee_id]

    # Filter support tickets for this employee
    # This shows all tickets regardless of status
    employee_tickets = [t for t in SUPPORT_TICKETS if t["employee_id"] == employee_id]

    # Return comprehensive employee data
    return json.dumps(
        {
            "employee_id": employee_id,
            "info": employee,  # Basic employee information
            "leave_requests": employee_leaves,  # All leave requests
            "support_tickets": employee_tickets,  # All support tickets
        },
        indent=2,
    )


# Server entry point
# This runs when the script is executed directly (not imported)
# The server listens on 0.0.0.0:8000/mcp using streamable-HTTP transport
# This is the required configuration for Amazon Bedrock AgentCore Runtime
if __name__ == "__main__":
    # Start the MCP server with streamable-HTTP transport
    # This makes the server compatible with AgentCore Runtime's stateless architecture
    mcp.run(transport="streamable-http")
