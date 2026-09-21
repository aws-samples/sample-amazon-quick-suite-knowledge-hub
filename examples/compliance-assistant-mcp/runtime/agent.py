"""
AgentCore Runtime agent — runs the CrewAI Compliance Assistant crew.

Deployed via `agentcore deploy`. Invoked by Lambda (fire-and-forget).
Updates DynamoDB with progress as each agent completes.
Uploads final report to S3.

Payload format (from Lambda):
{
    "job_id": "job-abc123",
    "topic": "PCI DSS 4.0 requirements for banking",
    "jobs_table": "compliance-assistant-v2-jobs",
    "reports_bucket": "compliance-assistant-v2-reports-xxxxx"
}
"""

import os
import time

import boto3
from bedrock_agentcore import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

REGION = os.environ.get("AWS_REGION_NAME", "us-east-1")


def update_status(table, job_id: str, status: str, progress: str, **extra):
    update_expr = "SET #s = :s, progress = :p"
    attr_names = {"#s": "status"}
    attr_values = {":s": status, ":p": progress}
    for key, value in extra.items():
        update_expr += f", {key} = :{key}"
        attr_values[f":{key}"] = value
    table.update_item(
        Key={"job_id": job_id},
        UpdateExpression=update_expr,
        ExpressionAttributeNames=attr_names,
        ExpressionAttributeValues=attr_values,
    )


@app.entrypoint
def invoke(payload):
    job_id = payload.get("job_id")
    topic = payload.get("topic")
    table_name = payload.get("jobs_table", "compliance-assistant-v2-jobs")
    bucket_name = payload.get("reports_bucket", "compliance-assistant-v2-reports")

    if not job_id or not topic:
        return {"error": "Missing job_id or topic"}

    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)
    table = dynamodb.Table(table_name)

    update_status(
        table,
        job_id,
        "RUNNING",
        "Agent 1/3: Compliance Analyst analyzing regulations...",
    )

    try:
        os.chdir("/tmp")

        from datetime import datetime

        from compliance_assistant.crew import ComplianceAssistant

        inputs = {"topic": topic, "current_year": str(datetime.now().year)}
        crew_instance = ComplianceAssistant()
        result = crew_instance.crew().kickoff(inputs=inputs)
        report_content = result.raw if hasattr(result, "raw") else str(result)

        s3_key = f"reports/{job_id}/report.md"
        s3.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=report_content.encode("utf-8"),
            ContentType="text/markdown",
        )

        update_status(
            table,
            job_id,
            "COMPLETED",
            "Analysis complete. All 3 agents finished successfully.",
            report_s3_key=s3_key,
            completed_at=int(time.time()),
        )

        return {"job_id": job_id, "status": "COMPLETED", "report_s3_key": s3_key}

    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        update_status(
            table,
            job_id,
            "FAILED",
            "Analysis failed.",
            error_message=error_msg,
            failed_at=int(time.time()),
        )
        return {"job_id": job_id, "status": "FAILED", "error": error_msg}


if __name__ == "__main__":
    app.run()
