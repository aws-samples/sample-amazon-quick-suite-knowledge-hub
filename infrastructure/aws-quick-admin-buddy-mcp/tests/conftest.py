"""Shared pytest / Hypothesis configuration for the Quick Admin Buddy MCP tests.

Registers a Hypothesis profile that runs each property-based test with a minimum
of 100 iterations (examples), and
activates it for the whole test session.
"""

from hypothesis import HealthCheck, settings

# Minimum number of Hypothesis examples per property test (design requirement).
MIN_ITERATIONS = 100

# A profile that guarantees at least MIN_ITERATIONS examples per property test.
settings.register_profile(
    "quick_admin_buddy",
    settings(
        max_examples=MIN_ITERATIONS,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    ),
)

# Activate the profile for every test run in this project.
settings.load_profile("quick_admin_buddy")
