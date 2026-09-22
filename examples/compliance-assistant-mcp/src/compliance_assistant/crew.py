import os

from crewai import Agent, Crew, Task
from crewai.project import CrewBase, agent, crew, task
from crewai_tools.aws.bedrock.agents.invoke_agent_tool import BedrockInvokeAgentTool


@CrewBase
class ComplianceAssistant:
    """ComplianceAssistant crew"""

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    # Initialize the Bedrock Agent Tool
    agent_tool = BedrockInvokeAgentTool(
        agent_id=os.environ.get("AGENT_ID"),
        agent_alias_id=os.environ.get("AGENT_ALIAS_ID"),
    )

    @agent
    def compliance_analyst(self) -> Agent:
        return Agent(
            config=self.agents_config["compliance_analyst"],
            tools=[self.agent_tool],
            allow_delegation=True,
            verbose=True,
        )

    @agent
    def compliance_specialist(self) -> Agent:
        return Agent(
            config=self.agents_config["compliance_specialist"],
            verbose=True,
        )

    @agent
    def solutions_architect(self) -> Agent:
        return Agent(
            config=self.agents_config["solutions_architect"],
            verbose=True,
        )

    @task
    def compliance_analysis_task(self) -> Task:
        return Task(
            config=self.tasks_config["compliance_analysis_task"],
        )

    @task
    def compliance_reporting_task(self) -> Task:
        return Task(
            config=self.tasks_config["compliance_reporting_task"],
        )

    @task
    def compliance_solution_task(self) -> Task:
        return Task(
            config=self.tasks_config["compliance_solution_task"],
            output_file="report.md",
        )

    @crew
    def crew(self) -> Crew:
        """Creates the Compliance Automation crew"""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            verbose=True,
            max_rpm=10,
        )
