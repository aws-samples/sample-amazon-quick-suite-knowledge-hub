#!/usr/bin/env python
import os
import sys
import warnings
from datetime import datetime

from compliance_assistant.crew import ComplianceAssistant

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

# Fetch the topic defined in .env file
topic = os.environ.get("TOPIC")
if topic is None:
    raise Exception("TOPIC is not defined. Please add the topic as an argument")


def run():
    """Run the crew."""
    inputs = {"topic": topic, "current_year": str(datetime.now().year)}

    try:
        ComplianceAssistant().crew().kickoff(inputs=inputs)
    except Exception as e:
        raise Exception(f"An error occurred while running the crew: {e}") from e


def train():
    """Train the crew for a given number of iterations."""
    inputs = {"topic": topic}
    try:
        ComplianceAssistant().crew().train(
            n_iterations=int(sys.argv[1]), filename=sys.argv[2], inputs=inputs
        )
    except Exception as e:
        raise Exception(f"An error occurred while training the crew: {e}") from e


def replay():
    """Replay the crew execution from a specific task."""
    try:
        ComplianceAssistant().crew().replay(task_id=sys.argv[1])
    except Exception as e:
        raise Exception(f"An error occurred while replaying the crew: {e}") from e


def test():
    """Test the crew execution and returns the results."""
    inputs = {"topic": topic, "current_year": str(datetime.now().year)}
    try:
        ComplianceAssistant().crew().test(
            n_iterations=int(sys.argv[1]),
            openai_model_name=sys.argv[2],
            inputs=inputs,
        )
    except Exception as e:
        raise Exception(f"An error occurred while testing the crew: {e}") from e
