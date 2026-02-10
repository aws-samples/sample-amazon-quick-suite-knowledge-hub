#!/usr/bin/env python3
import aws_cdk as cdk
from stacks.genai_ops_stack import GenAIOperationsStack
from stacks.quicksight_stack import QuickSightStack

app = cdk.App()

# Deploy base infrastructure first
base_stack = GenAIOperationsStack(app, "GenAIOperationsStack")

# Deploy QuickSight resources after base infrastructure
quicksight_stack = QuickSightStack(
    app, 
    "GenAIQuickSightStack",
    database_name=base_stack.database_name,
    workgroup_name=base_stack.workgroup_name
)
quicksight_stack.add_dependency(base_stack)

app.synth()
