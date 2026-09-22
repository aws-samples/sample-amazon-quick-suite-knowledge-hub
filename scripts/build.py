"""Build script for the Amazon Quick Knowledge Hub.

Each tool is declared once in TOOLS, with its check command and the commands
that fix what it reports. Checks and fixes therefore cannot drift apart, and
adding a tool means adding one row.

    uv run build                      # all checks
    uv run build format lint markdown # a subset
    uv run fix                        # auto-fix what is fixable
    uv run help                       # list commands

This hub publishes with MkDocs Material, so the docs check builds the site
with `mkdocs build` to catch build errors.
"""

import subprocess
import sys
from enum import StrEnum

import click
from loguru import logger
from pydantic import BaseModel

from scripts.install import gitleaks_binary

MARKDOWN_PATHS = ["docs/", "*.md"]
MDFORMAT = f"python -m mdformat {' '.join(MARKDOWN_PATHS)}"

# Use the pinned binary from .tools/ rather than whatever is on PATH, so every
# machine and CI run scan with the same version. See scripts/install.py.
# "gitleaks detect" reads committed history only, so uncommitted secrets slip
# past it. "gitleaks dir" scans the working tree.
GITLEAKS = f'"{gitleaks_binary()}" dir . --config .gitleaks.toml --no-banner --redact'


class CheckKey(StrEnum):
    """Selectable build check keys."""

    FORMAT = "format"
    LINT = "lint"
    MARKDOWN = "markdown"
    PRETTIER = "prettier"
    SECRETS = "secrets"
    SPELLING = "spelling"
    DOCS = "docs"


class Tool(BaseModel):
    """A single quality tool: how to check with it, and how to fix its findings."""

    key: CheckKey
    description: str
    check: str
    fixes: list[str] = []


TOOLS: list[Tool] = [
    Tool(
        key=CheckKey.FORMAT,
        description="Format check (Ruff)",
        check="ruff format --check .",
        fixes=["ruff format ."],
    ),
    Tool(
        key=CheckKey.LINT,
        description="Lint (Ruff)",
        check="ruff check .",
        # --fix can reflow code, so re-run the formatter afterwards.
        fixes=["ruff check . --fix", "ruff format ."],
    ),
    Tool(
        key=CheckKey.MARKDOWN,
        description="Markdown format check (mdformat)",
        check=f"{MDFORMAT} --check",
        fixes=[MDFORMAT],
    ),
    Tool(
        key=CheckKey.PRETTIER,
        description="Prettier check",
        check="npx --yes prettier --check .",
        fixes=["npx --yes prettier --write ."],
    ),
    Tool(
        key=CheckKey.SECRETS,
        description="Secret scan (gitleaks)",
        check=GITLEAKS,
    ),
    Tool(
        key=CheckKey.SPELLING,
        description="Spelling (typos)",
        check="typos",
        fixes=["typos --write-changes"],
    ),
    Tool(
        key=CheckKey.DOCS,
        description="Docs build (MkDocs)",
        check="mkdocs build",
    ),
]

TOOLS_BY_KEY: dict[str, Tool] = {tool.key.value: tool for tool in TOOLS}
CHECK_KEYS: list[str] = [tool.key.value for tool in TOOLS]


class StepStatus(StrEnum):
    """Outcome of a single command."""

    PASSED = "PASSED"
    FAILED = "FAILED"


class Step(BaseModel):
    """One named command to run."""

    name: str
    command: str


class StepResult(BaseModel):
    """Outcome of running one step."""

    name: str
    command: str
    status: StepStatus
    return_code: int = 0


class RunRequest(BaseModel):
    """A sequence of steps to run in order."""

    steps: list[Step]


class RunResponse(BaseModel):
    """Results of a run, truncated at the first failure."""

    results: list[StepResult]
    success: bool


class CommandRunner:
    """Runs a sequence of shell commands in order, stopping at the first failure."""

    def run(self, request: RunRequest) -> RunResponse:
        """Execute each step until one fails, then return what happened."""
        results: list[StepResult] = []

        for step in request.steps:
            logger.info(f"Running: {step.name}")
            logger.debug(f"Command: {step.command}")

            process = subprocess.run(step.command, shell=True)
            status = StepStatus.PASSED if process.returncode == 0 else StepStatus.FAILED

            results.append(
                StepResult(
                    name=step.name,
                    command=step.command,
                    status=status,
                    return_code=process.returncode,
                )
            )

            if status == StepStatus.FAILED:
                logger.error(f"Failed: {step.name}")
                return RunResponse(results=results, success=False)

            logger.success(f"Passed: {step.name}")

        return RunResponse(results=results, success=True)


def _configure_logging(verbose: bool = False) -> None:
    """Send loguru output to stderr at the requested level."""
    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if verbose else "INFO")


def _exit_on(response: RunResponse, success_message: str) -> None:
    """Log the outcome and exit with the matching status code."""
    if response.success:
        logger.success(success_message)
        sys.exit(0)

    failed = next(r for r in response.results if r.status == StepStatus.FAILED)
    logger.error(f"Stopped at: {failed.name}")
    sys.exit(1)


@click.command()
@click.argument("checks", nargs=-1, type=click.Choice(CHECK_KEYS, case_sensitive=False))
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
def cli(checks: tuple[str, ...], verbose: bool) -> None:
    """Run build checks. Runs every check when none are named.

    Examples:
        uv run build
        uv run build format lint markdown
    """
    _configure_logging(verbose)

    selected = checks or tuple(CHECK_KEYS)
    steps = [
        Step(name=TOOLS_BY_KEY[key].description, command=TOOLS_BY_KEY[key].check)
        for key in selected
    ]
    _exit_on(CommandRunner().run(RunRequest(steps=steps)), "All checks passed.")


def fix() -> None:
    """Auto-fix everything the checks can fix, in tool order."""
    _configure_logging()

    steps = [
        Step(name=f"{tool.description} fix", command=command)
        for tool in TOOLS
        for command in tool.fixes
    ]
    _exit_on(CommandRunner().run(RunRequest(steps=steps)), "All fixes applied.")


def docs() -> None:
    """Build the documentation site."""
    _configure_logging()
    tool = TOOLS_BY_KEY[CheckKey.DOCS.value]
    steps = [Step(name=tool.description, command=tool.check)]
    _exit_on(CommandRunner().run(RunRequest(steps=steps)), "Docs built.")


def docs_serve() -> None:
    """Start the local documentation server."""
    _configure_logging()
    url = "http://127.0.0.1:8000"
    logger.info(f"Starting docs server at {url}")
    subprocess.run("mkdocs serve", shell=True)


if __name__ == "__main__":
    cli()
