"""Customer Scoring MCP Server - Lambda Handler
Scores frustrated customers and identifies top performers for bonuses"""

import json
from typing import List, Dict, Any


def lambda_handler(event, context):
    """Handle MCP requests for customer scoring"""

    # Parse MCP request
    body = json.loads(event['body']) if isinstance(event.get('body'), str) else event.get('body', {})
    method = body.get('method')
    request_id = body.get('id')
    params = body.get('params', {})

    # Handle MCP methods
    if method == 'initialize':
        return mcp_response(request_id, {
            'protocolVersion': '2024-11-05',
            'capabilities': {'tools': {}},
            'serverInfo': {
                'name': 'customer-scoring-mcp',
                'version': '1.0.0'
            }
        })

    elif method == 'tools/list':
        return mcp_response(request_id, {
            'tools': [
                {
                    'name': 'calculate_customer_scores',
                    'description': 'Calculate customer loyalty scores based on multiple factors',
                    'inputSchema': {
                        'type': 'object',
                        'properties': {
                            'customers': {
                                'type': 'array',
                                'description': 'List of customer records with metrics',
                                'items': {
                                    'type': 'object',
                                    'properties': {
                                        'customer_id': {'type': 'string'},
                                        'customer_name': {'type': 'string'},
                                        'total_purchases': {'type': 'number'},
                                        'account_age_months': {'type': 'number'},
                                        'frustration_incidents': {'type': 'number'},
                                        'avg_purchase_value': {'type': 'number'},
                                        'support_tickets': {'type': 'number'}
                                    }
                                }
                            },
                            'weights': {
                                'type': 'object',
                                'description': 'Scoring weights (optional)',
                                'properties': {
                                    'purchases': {'type': 'number', 'default': 0.3},
                                    'account_age': {'type': 'number', 'default': 0.2},
                                    'frustration_penalty': {'type': 'number', 'default': 0.2},
                                    'purchase_value': {'type': 'number', 'default': 0.2},
                                    'support_penalty': {'type': 'number', 'default': 0.1}
                                }
                            }
                        },
                        'required': ['customers']
                    }
                },
                {
                    'name': 'get_top_customers',
                    'description': 'Get top N customers by score for bonus allocation',
                    'inputSchema': {
                        'type': 'object',
                        'properties': {
                            'scored_customers': {
                                'type': 'array',
                                'description': 'List of customers with scores'
                            },
                            'top_n': {
                                'type': 'integer',
                                'description': 'Number of top customers to return',
                                'default': 2
                            },
                            'bonus_amount': {
                                'type': 'number',
                                'description': 'Bonus amount per customer',
                                'default': 100
                            }
                        },
                        'required': ['scored_customers']
                    }
                },
                {
                    'name': 'apply_scoring_rules',
                    'description': 'Apply custom business rules to customer data',
                    'inputSchema': {
                        'type': 'object',
                        'properties': {
                            'customers': {
                                'type': 'array',
                                'description': 'Customer data'
                            },
                            'rules': {
                                'type': 'object',
                                'description': 'Custom scoring rules',
                                'properties': {
                                    'min_purchases': {'type': 'number'},
                                    'min_account_age': {'type': 'number'},
                                    'max_frustration': {'type': 'number'},
                                    'bonus_for_loyalty': {'type': 'number'}
                                }
                            }
                        },
                        'required': ['customers', 'rules']
                    }
                }
            ]
        })

    elif method == 'tools/call':
        tool_name = params.get('name')
        arguments = params.get('arguments', {})

        if tool_name == 'calculate_customer_scores':
            result = calculate_customer_scores(arguments)
        elif tool_name == 'get_top_customers':
            result = get_top_customers(arguments)
        elif tool_name == 'apply_scoring_rules':
            result = apply_scoring_rules(arguments)
        else:
            return mcp_error(request_id, -32601, f'Tool not found: {tool_name}')

        return mcp_response(request_id, {
            'content': [{'type': 'text', 'text': json.dumps(result, indent=2)}]
        })

    else:
        return mcp_error(request_id, -32601, f'Method not found: {method}')


def calculate_customer_scores(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Calculate customer loyalty scores based on call center data

    Scoring factors from call center dataset:
    - Call frequency (engagement level)
    - Resolution quality (first_call_resolution, resolution_status)
    - CSAT scores (customer satisfaction)
    - Efficiency (low queue time, low transfers, low hold time)
    - Issue severity (complaints vs general inquiries)
    """
    customers = arguments.get('customers', [])
    weights = arguments.get('weights', {})

    # Default weights optimized for call center data
    w_engagement = weights.get('engagement', 0.25)      # Call frequency
    w_satisfaction = weights.get('satisfaction', 0.30)   # CSAT scores
    w_resolution = weights.get('resolution', 0.20)      # FCR and resolution quality
    w_efficiency = weights.get('efficiency', 0.15)      # Low transfers, hold time
    w_issue_severity = weights.get('issue_severity', 0.10)  # Complaint vs inquiry ratio

    scored_customers = []

    for customer in customers:
        # Extract metrics from call center data
        total_calls = customer.get('total_calls', 0)
        avg_csat = customer.get('avg_csat_score', 0)
        fcr_rate = customer.get('first_call_resolution_rate', 0)  # Percentage
        avg_transfers = customer.get('avg_transfer_count', 0)
        avg_hold_time = customer.get('avg_hold_time_seconds', 0)
        complaint_ratio = customer.get('complaint_ratio', 0)  # Complaints / total calls
        resolved_rate = customer.get('resolved_rate', 0)  # Percentage

        # 1. Engagement Score (0-100): More calls = more engaged
        # Cap at 20 calls for normalization
        engagement_score = min((total_calls / 20) * 100, 100)

        # 2. Satisfaction Score (0-100): Based on CSAT (1-5 scale)
        # Convert CSAT to 0-100 scale
        satisfaction_score = (avg_csat / 5) * 100 if avg_csat > 0 else 0

        # 3. Resolution Score (0-100): FCR + resolved rate
        resolution_score = (fcr_rate * 0.6 + resolved_rate * 0.4)

        # 4. Efficiency Score (0-100): Penalize high transfers and hold time
        # Low transfers = good (0-1 transfers = 100, 3+ = 0)
        transfer_score = max(100 - (avg_transfers * 33), 0)
        # Low hold time = good (0-300s = 100, 600s+ = 0)
        hold_score = max(100 - (avg_hold_time / 6), 0)
        efficiency_score = (transfer_score * 0.6 + hold_score * 0.4)

        # 5. Issue Severity Score (0-100): Lower complaint ratio = better
        # 0% complaints = 100, 50%+ complaints = 0
        issue_severity_score = max(100 - (complaint_ratio * 200), 0)

        # Calculate weighted total score
        total_score = (
            engagement_score * w_engagement +
            satisfaction_score * w_satisfaction +
            resolution_score * w_resolution +
            efficiency_score * w_efficiency +
            issue_severity_score * w_issue_severity
        )

        # Determine customer tier
        if total_score >= 80:
            tier = 'Platinum'
        elif total_score >= 65:
            tier = 'Gold'
        elif total_score >= 50:
            tier = 'Silver'
        else:
            tier = 'Bronze'

        scored_customers.append({
            'customer_id': customer.get('customer_id'),
            'loyalty_score': round(total_score, 2),
            'tier': tier,
            'score_breakdown': {
                'engagement': round(engagement_score * w_engagement, 2),
                'satisfaction': round(satisfaction_score * w_satisfaction, 2),
                'resolution': round(resolution_score * w_resolution, 2),
                'efficiency': round(efficiency_score * w_efficiency, 2),
                'issue_severity': round(issue_severity_score * w_issue_severity, 2)
            },
            'metrics': {
                'total_calls': total_calls,
                'avg_csat_score': avg_csat,
                'fcr_rate': fcr_rate,
                'resolved_rate': resolved_rate,
                'avg_transfer_count': avg_transfers,
                'avg_hold_time_seconds': avg_hold_time,
                'complaint_ratio': complaint_ratio
            },
            'risk_flags': {
                'high_transfers': avg_transfers >= 2,
                'low_csat': avg_csat < 3,
                'high_complaints': complaint_ratio > 0.3,
                'poor_resolution': resolved_rate < 70
            }
        })

    # Sort by score descending
    scored_customers.sort(key=lambda x: x['loyalty_score'], reverse=True)

    return {
        'scored_customers': scored_customers,
        'total_customers': len(scored_customers),
        'avg_score': round(sum(c['loyalty_score'] for c in scored_customers) / len(scored_customers), 2) if scored_customers else 0,
        'tier_distribution': {
            'Platinum': len([c for c in scored_customers if c['tier'] == 'Platinum']),
            'Gold': len([c for c in scored_customers if c['tier'] == 'Gold']),
            'Silver': len([c for c in scored_customers if c['tier'] == 'Silver']),
            'Bronze': len([c for c in scored_customers if c['tier'] == 'Bronze'])
        }
    }


def get_top_customers(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Get top N customers for bonus allocation"""
    scored_customers = arguments.get('scored_customers', [])
    top_n = arguments.get('top_n', 2)
    bonus_amount = arguments.get('bonus_amount', 100)

    # Sort by score if not already sorted
    sorted_customers = sorted(
        scored_customers,
        key=lambda x: x.get('loyalty_score', 0),
        reverse=True
    )

    # Get top N
    top_customers = sorted_customers[:top_n]

    # Add bonus information
    for i, customer in enumerate(top_customers):
        customer['rank'] = i + 1
        customer['bonus_amount'] = bonus_amount
        customer['bonus_reason'] = f"Top {i+1} loyal customer despite frustration incidents"

    return {
        'top_customers': top_customers,
        'total_bonus_allocated': bonus_amount * len(top_customers),
        'selection_criteria': f'Top {top_n} by loyalty score'
    }


def apply_scoring_rules(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Apply custom business rules to filter and score customers"""
    customers = arguments.get('customers', [])
    rules = arguments.get('rules', {})

    min_purchases = rules.get('min_purchases', 0)
    min_account_age = rules.get('min_account_age', 0)
    max_frustration = rules.get('max_frustration', 999)
    bonus_for_loyalty = rules.get('bonus_for_loyalty', 10)

    qualified_customers = []
    disqualified_customers = []

    for customer in customers:
        disqualification_reasons = []

        # Check rules
        if customer.get('total_purchases', 0) < min_purchases:
            disqualification_reasons.append(f"Purchases below minimum ({min_purchases})")

        if customer.get('account_age_months', 0) < min_account_age:
            disqualification_reasons.append(f"Account age below minimum ({min_account_age} months)")

        if customer.get('frustration_incidents', 0) > max_frustration:
            disqualification_reasons.append(f"Too many frustration incidents (>{max_frustration})")

        if disqualification_reasons:
            disqualified_customers.append({
                'customer_id': customer.get('customer_id'),
                'customer_name': customer.get('customer_name'),
                'reasons': disqualification_reasons
            })
        else:
            # Apply loyalty bonus
            base_score = customer.get('loyalty_score', 0)
            adjusted_score = base_score + bonus_for_loyalty
            qualified_customers.append({
                **customer,
                'adjusted_score': adjusted_score,
                'loyalty_bonus_applied': bonus_for_loyalty,
                'qualified': True
            })

    return {
        'qualified_customers': qualified_customers,
        'disqualified_customers': disqualified_customers,
        'qualification_rate': f"{len(qualified_customers)}/{len(customers)} ({round(len(qualified_customers)/len(customers)*100, 1)}%)" if customers else "0%",
        'rules_applied': rules
    }


def mcp_response(request_id, result):
    """Format MCP success response"""
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type'
        },
        'body': json.dumps({
            'jsonrpc': '2.0',
            'id': request_id,
            'result': result
        })
    }


def mcp_error(request_id, code, message):
    """Format MCP error response"""
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*'
        },
        'body': json.dumps({
            'jsonrpc': '2.0',
            'id': request_id,
            'error': {
                'code': code,
                'message': message
            }
        })
    }
