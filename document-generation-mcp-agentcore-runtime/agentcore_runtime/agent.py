"""
Document Skills Agent — Deployed to AgentCore Runtime.

This is the Strands agent that runs on AgentCore Runtime as a containerized service.
It uses Claude Sonnet as the model and AgentCore Code Interpreter for file generation.

Architecture:
  submit_job Lambda → InvokeAgentRuntime (returns immediately)
  Agent runs in a background thread, direct-persists result to S3 + DynamoDB.
  Step Function polls DynamoDB until the agent finishes.

The entrypoint uses AgentCore's async task management (add_async_task /
complete_async_task) so InvokeAgentRuntime returns immediately while the
agent continues processing in the background. This avoids any Lambda
timeout issues — the agent runs as long as it needs.
"""

import json
import logging
import os
import threading
import time

from bedrock_agentcore.runtime import BedrockAgentCoreApp

os.environ["BYPASS_TOOL_CONSENT"] = "true"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Heavy imports deferred to first invocation to stay within 30s init limit.
# strands, strands_tools, base64, re are imported inside create_agent() / _extract_file().


def _get_hook_classes():
    """Lazily import and return hook base classes."""
    from strands.hooks.events import AfterToolCallEvent, BeforeToolCallEvent
    from strands.hooks.registry import HookProvider, HookRegistry
    return HookProvider, HookRegistry, BeforeToolCallEvent, AfterToolCallEvent


class MaxToolCallsHook:
    """Hard-stops the agent event loop after a maximum number of tool calls.

    Three-phase approach:
    1. Normal calls (1 to max_calls-2): just log the count.
    2. Warning call (max_calls-1): cancel the tool and tell the model its NEXT
       call MUST be the base64 output step. The model gets one more chance.
    3. Final call (max_calls): allow it through — this should be the base64 step.
    4. Over limit (max_calls+1): hard-stop via stop_event_loop.
    """

    def __init__(self, max_calls: int = 20):
        self.max_calls = max_calls
        self._call_count = 0

    def register_hooks(self, registry, **kwargs) -> None:
        _, _, BeforeToolCallEvent, _ = _get_hook_classes()
        registry.add_callback(BeforeToolCallEvent, self._before_tool_call)

    def _before_tool_call(self, event) -> None:
        self._call_count += 1

        if self._call_count > self.max_calls:
            # Hard stop — force the event loop to end
            logger.warning(
                "Tool call limit exceeded (%d/%d). Force-stopping event loop.",
                self._call_count,
                self.max_calls,
            )
            event.cancel_tool = (
                f"HARD LIMIT REACHED ({self.max_calls} calls). Stopping now."
            )
            request_state = event.invocation_state.get("request_state", {})
            request_state["stop_event_loop"] = True
            return

        if self._call_count == self.max_calls - 1:
            # Warning: one call left after this — cancel current and warn
            logger.warning(
                "Tool call %d/%d — WARNING: next call is your LAST. "
                "Cancelling this call. You MUST output base64 next.",
                self._call_count,
                self.max_calls,
            )
            event.cancel_tool = (
                f"WARNING: You have used {self._call_count - 1} of {self.max_calls} tool calls. "
                f"You have exactly 1 tool call remaining. "
                "Your NEXT and FINAL tool call MUST be the base64 output step. "
                "If you have not saved the file yet, the file generation is complete enough. "
                "Run the base64 output code NOW on your next tool call."
            )
            return

        logger.info("Tool call %d/%d", self._call_count, self.max_calls)


class Base64CaptureHook:
    """Captures base64 file output from tool results before conversation trimming.

    The SlidingWindowConversationManager trims old messages between turns,
    which can remove the base64 output before _extract_file sees it.
    This hook inspects every tool result as it arrives and saves the file
    bytes immediately, so they survive conversation trimming.
    """

    def __init__(self):
        self.captured_bytes: bytes | None = None

    def register_hooks(self, registry, **kwargs) -> None:
        _, _, _, AfterToolCallEvent = _get_hook_classes()
        registry.add_callback(AfterToolCallEvent, self._after_tool_call)

    def _after_tool_call(self, event) -> None:
        if self.captured_bytes is not None:
            return  # Already captured

        result = getattr(event, "result", None)
        if result is None:
            return

        text_parts = []
        content = None
        if isinstance(result, dict):
            content = result.get("content", [])
        elif hasattr(result, "content"):
            content = result.content
        if not content:
            return

        for item in content:
            if isinstance(item, dict):
                t = item.get("text", "")
                if t:
                    text_parts.append(t)

        full_text = "".join(text_parts)
        if "BASE64_FILE_START" in full_text and "BASE64_FILE_END" in full_text:
            import base64 as b64mod
            import re
            start = full_text.index("BASE64_FILE_START") + len("BASE64_FILE_START")
            end = full_text.index("BASE64_FILE_END", start)
            raw = full_text[start:end].strip()
            cleaned = re.sub(r"[^A-Za-z0-9+/=]", "", raw)
            cleaned = cleaned.rstrip("=")
            cleaned += "=" * ((4 - len(cleaned) % 4) % 4)
            if len(cleaned) < 100:
                logger.warning(
                    "Base64CaptureHook: too short (%d chars) — ignoring", len(cleaned)
                )
                return
            if cleaned:
                try:
                    decoded = b64mod.b64decode(cleaned)
                    if len(decoded) < 50:
                        logger.warning(
                            "Base64CaptureHook: decoded too small (%d bytes) — ignoring",
                            len(decoded),
                        )
                        return
                    self.captured_bytes = decoded
                    logger.info(
                        "Base64CaptureHook: captured %d bytes", len(self.captured_bytes)
                    )
                except Exception as e:
                    logger.warning("Base64CaptureHook: decode failed: %s", e)


# ─── Configuration ───
AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")
# Claude Sonnet 4.6 — reliable large output generation, cross-region inference profile
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")

# Shared base64 output instructions — writes to file then reads in small chunks
_BASE64_OUTPUT_STEP = """
CRITICAL FINAL STEP — you MUST do this after saving the file. NEVER skip this step.
Run this EXACT code to output the file as base64. Do NOT modify it:
```python
import base64, sys, os

filepath = '{output_file}'
assert os.path.exists(filepath), f"File not found: {{filepath}}"
file_size = os.path.getsize(filepath)
print(f"File exists: {{filepath}}, size: {{file_size}} bytes")

with open(filepath, 'rb') as f:
    raw = f.read()

encoded = base64.b64encode(raw).decode('utf-8')
print(f"Base64 length: {{len(encoded)}} chars")

# Write base64 to a text file first, then read and print in chunks
with open('b64out.txt', 'w') as bf:
    bf.write('BASE64_FILE_START')
    bf.write(encoded)
    bf.write('BASE64_FILE_END')

# Now read and print in 50KB chunks
with open('b64out.txt', 'r') as bf:
    while True:
        chunk = bf.read(51200)
        if not chunk:
            break
        sys.stdout.write(chunk)
    sys.stdout.flush()
print()
print("TRANSFER_COMPLETE")
```

ABSOLUTE RULES:
- You MUST execute the above code block EXACTLY as shown. Do NOT modify it.
- You MUST NOT skip this step or say the output is "too large" or has "size limitations".
- You MUST NOT summarize the file contents instead of outputting base64.
- The base64 output IS the deliverable. Without it, the task is FAILED.
- If the code errors, fix the error and run it again.
"""

# ─── Skill Prompts ───
# Budget: 20 tool calls max. Warning fires at call 19, hard stop at call 21.
# Ideal flow: install(1) + generate(1) + base64(1) = 3 calls, leaving 17 for error recovery.

_BUDGET_PREAMBLE = """⚠️ TOOL CALL BUDGET: You have a STRICT LIMIT of 20 total code executions.
If you exceed this limit, the system will FORCE-STOP you and the task WILL FAIL.

You MUST plan your work to fit within this budget:
- Call 1: Install dependencies
- Call 2: Generate the ENTIRE file in ONE script (the biggest call)
- Call 3: Output base64 (mandatory — without this, the task fails)
- Calls 4-20: Reserved for error recovery ONLY

THE #1 RULE: Generate ALL content in a SINGLE code execution (Call 2).
Never split work across multiple calls. Write one big script that does everything.
For complex requests, use data structures (lists, dicts) to define content,
then loop through them to build the document programmatically.

CRITICAL FOR COMPLEX DOCUMENTS: Even if the user asks for 10+ sections, tables,
risk matrices, timelines, etc. — you MUST generate it ALL in ONE script.
Define all content as Python data structures (lists of dicts), then loop to build.
Do NOT split into "part 1" and "part 2" scripts. ONE script, ONE call.

"""

SKILL_PROMPTS = {
    "docx": _BUDGET_PREAMBLE
    + """You are a professional document creation specialist.

STEP 1 — Install (one call):
```python
import subprocess
subprocess.check_call(['pip', 'install', 'python-docx', 'Pillow'])
print("DEPS_INSTALLED")
```

STEP 2 — Generate ENTIRE document in ONE script. Save to 'output.docx'.
Use python-docx: headings, paragraphs, tables, bullet points, fonts, page numbers.
For long/complex documents (technical designs, proposals, reports with many sections):
- Define ALL section content as a list of dicts at the top of the script
- Define ALL table data as lists of lists
- Use helper functions: add_heading(), add_table(), add_paragraph()
- Loop through the data structures to build the document
- This approach handles 20+ page documents in a single script execution
- Do NOT try to add images or complex graphics — focus on text, tables, and formatting

STEP 3 — Output base64 (mandatory):
"""
    + _BASE64_OUTPUT_STEP.format(output_file="output.docx"),
    "pdf": _BUDGET_PREAMBLE
    + """You are a professional PDF creation specialist.

STEP 1 — Install (one call):
```python
import subprocess
subprocess.check_call(['pip', 'install', 'reportlab', 'Pillow'])
print("DEPS_INSTALLED")
```

STEP 2 — Generate ENTIRE PDF in ONE script. Save to 'output.pdf'.
Use reportlab platypus: Paragraph, Table, Spacer, PageBreak. Build all content as a single list of flowables.

STEP 3 — Output base64 (mandatory):
"""
    + _BASE64_OUTPUT_STEP.format(output_file="output.pdf"),
    "pptx": _BUDGET_PREAMBLE
    + """You are a professional presentation specialist.

STEP 1 — Install (one call):
```python
import subprocess
subprocess.check_call(['pip', 'install', 'python-pptx', 'Pillow', 'matplotlib'])
print("DEPS_INSTALLED")
```

STEP 2 — Generate ENTIRE presentation in ONE script. Save to 'output.pptx'.
Use python-pptx: create all slides in a single script with helper functions.
For 10+ slides, prioritize content over elaborate visuals.

STEP 3 — Output base64 (mandatory):
"""
    + _BASE64_OUTPUT_STEP.format(output_file="output.pptx"),
    "xlsx": _BUDGET_PREAMBLE
    + """You are a professional spreadsheet specialist.

STEP 1 — Install (one call):
```python
import subprocess
subprocess.check_call(['pip', 'install', 'openpyxl', 'matplotlib', 'pandas'])
print("DEPS_INSTALLED")
```

STEP 2 — Generate ENTIRE spreadsheet in ONE script. Save to 'output.xlsx'.
Use openpyxl: create ALL sheets, data, formatting, formulas, charts in one script.
Define sample data as lists/dicts, then iterate to populate sheets programmatically.

STEP 3 — Output base64 (mandatory):
"""
    + _BASE64_OUTPUT_STEP.format(output_file="output.xlsx"),
    "frontend-design": _BUDGET_PREAMBLE
    + """You are a frontend design specialist.

STEP 1 — Generate ENTIRE HTML file in ONE script. Save to 'output.html'.
Write complete HTML/CSS/JS. Use modern CSS, responsive design, distinctive palette.

STEP 2 — Output base64 (mandatory):
"""
    + _BASE64_OUTPUT_STEP.format(output_file="output.html"),
}


CONTENT_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "html": "text/html",
}


def _direct_persist(
    file_bytes: bytes,
    job_id: str,
    filename: str,
    file_type: str,
    bucket: str,
    table: str,
    region: str,
) -> str:
    """Upload result to S3 and mark job COMPLETED in DynamoDB.

    This runs inside the agent runtime *before* returning the response,
    so the result is persisted even if the calling Lambda times out
    reading the streaming response.

    Returns the S3 key.
    """
    import boto3

    s3_key = f"generated/{job_id}/{filename}"
    content_type = CONTENT_TYPES.get(file_type, "application/octet-stream")

    s3 = boto3.client("s3", region_name=region)
    s3.put_object(
        Bucket=bucket,
        Key=s3_key,
        Body=file_bytes,
        ContentType=content_type,
        ContentDisposition=f'attachment; filename="{filename}"',
        ServerSideEncryption="AES256",
    )
    logger.info(
        "Direct-persist: uploaded %d bytes to s3://%s/%s",
        len(file_bytes),
        bucket,
        s3_key,
    )

    dynamodb = boto3.resource("dynamodb", region_name=region)
    dynamodb.Table(table).update_item(
        Key={"job_id": job_id},
        UpdateExpression="SET #s = :status, s3_key = :key, filename = :fn, file_type = :ft, updated_at = :now",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":status": "COMPLETED",
            ":key": s3_key,
            ":fn": filename,
            ":ft": file_type,
            ":now": int(time.time()),
        },
    )
    logger.info("Direct-persist: marked job %s COMPLETED in DynamoDB", job_id)
    return s3_key


def create_agent(skill_type: str):
    """Create a Strands agent configured for the given skill type."""
    from strands import Agent
    from strands.agent.conversation_manager import SlidingWindowConversationManager
    from strands.models.bedrock import BedrockModel
    from strands_tools.code_interpreter import AgentCoreCodeInterpreter
    import botocore.config

    system_prompt = SKILL_PROMPTS.get(skill_type)
    if not system_prompt:
        raise ValueError(f"Unknown skill_type: {skill_type}")

    # Configure boto3 with extended timeouts for large streaming responses.
    bedrock_config = botocore.config.Config(
        read_timeout=600,  # 10 min read timeout for long streams
        connect_timeout=30,
        retries={"max_attempts": 3, "mode": "adaptive"},
    )

    model = BedrockModel(
        model_id=MODEL_ID,
        region_name=AWS_REGION,
        boto_client_config=bedrock_config,
    )

    code_interpreter = AgentCoreCodeInterpreter(region=AWS_REGION)

    conversation_manager = SlidingWindowConversationManager(
        window_size=20,
        should_truncate_results=True,
        per_turn=True,
    )

    max_tool_calls_hook = MaxToolCallsHook(max_calls=20)
    b64_capture_hook = Base64CaptureHook()

    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        tools=[code_interpreter.code_interpreter],
        conversation_manager=conversation_manager,
        hooks=[max_tool_calls_hook, b64_capture_hook],
    )
    agent._b64_capture_hook = b64_capture_hook
    return agent


# ─── AgentCore Runtime App ───
app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload, context):
    """
    AgentCore Runtime invocation handler.

    Uses async task management: returns immediately to the caller, then
    runs the agent in a background thread. The caller (submit_job Lambda)
    gets an instant response and doesn't need to wait.

    Expected payload:
    {
        "skill_type": "docx",
        "prompt": "Create a professional Q4 earnings report...",
        "filename": "Q4_Report.docx",
        "job_id": "uuid",
        "docs_bucket": "bucket",
        "jobs_table": "table"
    }

    Returns immediately:
    { "status": "accepted", "job_id": "..." }

    The background thread runs the agent, direct-persists to S3 + DynamoDB,
    then calls complete_async_task so the runtime session can go idle.
    """
    logger.info(f"Received payload: {json.dumps(payload)}")

    skill_type = payload.get("skill_type", "").lower().strip()
    prompt = payload.get("prompt", "").strip()
    filename = payload.get("filename", "")
    job_id = payload.get("job_id", "")
    docs_bucket = payload.get("docs_bucket", "")
    jobs_table = payload.get("jobs_table", "")

    if not skill_type or not prompt:
        return {"status": "error", "error": "skill_type and prompt are required"}

    if not filename:
        ext = "html" if skill_type == "frontend-design" else skill_type
        filename = f"document.{ext}"

    # If no job_id provided (e.g. direct CLI invoke), run synchronously
    if not job_id or not docs_bucket or not jobs_table:
        return _run_agent_sync(skill_type, prompt, filename)

    # Async path: register a background task and return immediately
    task_id = app.add_async_task(
        "document_generation",
        {
            "job_id": job_id,
            "skill_type": skill_type,
        },
    )

    def background_work():
        try:
            _run_agent_and_persist(
                skill_type=skill_type,
                prompt=prompt,
                filename=filename,
                job_id=job_id,
                docs_bucket=docs_bucket,
                jobs_table=jobs_table,
            )
        except Exception as e:
            logger.error(
                f"Background agent failed for job {job_id}: {e}", exc_info=True
            )
            # Mark job FAILED in DynamoDB so the polling loop detects it
            try:
                import boto3

                dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
                dynamodb.Table(jobs_table).update_item(
                    Key={"job_id": job_id},
                    UpdateExpression="SET #s = :status, #e = :error, updated_at = :now",
                    ExpressionAttributeNames={"#s": "status", "#e": "error"},
                    ExpressionAttributeValues={
                        ":status": "FAILED",
                        ":error": str(e)[:1000],
                        ":now": int(time.time()),
                    },
                )
            except Exception:
                logger.error(
                    "Failed to mark job %s as FAILED in DynamoDB", job_id, exc_info=True
                )
        finally:
            app.complete_async_task(task_id)

    threading.Thread(target=background_work, daemon=True).start()

    logger.info(f"Job {job_id}: async task started, returning immediately")
    return {
        "status": "accepted",
        "job_id": job_id,
        "message": f"Document generation started for job {job_id}",
    }


def _mark_processing(job_id, jobs_table, region):
    """Mark job as PROCESSING in DynamoDB so the user knows the agent picked it up."""
    import boto3

    try:
        dynamodb = boto3.resource("dynamodb", region_name=region)
        dynamodb.Table(jobs_table).update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #s = :status, started_at = :now, updated_at = :now",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": "PROCESSING",
                ":now": int(time.time()),
            },
        )
        logger.info("Job %s: marked PROCESSING in DynamoDB", job_id)
    except Exception as e:
        logger.warning("Job %s: failed to mark PROCESSING: %s", job_id, e)


def _run_agent_and_persist(
    skill_type, prompt, filename, job_id, docs_bucket, jobs_table
):
    """Run the agent and direct-persist the result. Called from background thread."""
    _mark_processing(job_id, jobs_table, AWS_REGION)

    last_error = None
    agent = None
    for attempt in range(3):
        try:
            agent = create_agent(skill_type)
            agent(prompt)
            break
        except Exception as retry_err:
            err_str = str(retry_err).lower()
            if (
                "prematurely" in err_str
                or "timeout" in err_str
                or "connection" in err_str
                or "timed out" in err_str
            ):
                last_error = retry_err
                logger.warning(
                    f"Job {job_id}: attempt {attempt + 1} failed with transient error: {retry_err}"
                )
                time.sleep(5 * (attempt + 1))  # backoff: 5s, 10s, 15s
                continue
            raise
    else:
        raise last_error

    # Extract file bytes
    file_bytes = None
    if hasattr(agent, "_b64_capture_hook") and agent._b64_capture_hook.captured_bytes:
        file_bytes = agent._b64_capture_hook.captured_bytes
        logger.info(
            "Job %s: file extracted from Base64CaptureHook (%d bytes)",
            job_id,
            len(file_bytes),
        )
    else:
        file_bytes = _extract_file(agent.messages)

    if file_bytes is None:
        raise RuntimeError("Code Interpreter did not produce an output file.")

    ext = "html" if skill_type == "frontend-design" else skill_type
    _direct_persist(
        file_bytes=file_bytes,
        job_id=job_id,
        filename=filename,
        file_type=ext,
        bucket=docs_bucket,
        table=jobs_table,
        region=AWS_REGION,
    )
    logger.info("Job %s: agent completed and persisted successfully", job_id)


def _run_agent_sync(skill_type, prompt, filename):
    """Synchronous agent run — used for direct CLI invocations without job tracking."""
    try:
        last_error = None
        for attempt in range(3):
            try:
                agent = create_agent(skill_type)
                agent(prompt)
                break
            except Exception as retry_err:
                err_str = str(retry_err).lower()
                if (
                    "prematurely" in err_str
                    or "timeout" in err_str
                    or "connection" in err_str
                    or "timed out" in err_str
                ):
                    last_error = retry_err
                    logger.warning(
                        f"Attempt {attempt + 1} failed with transient error: {retry_err}"
                    )
                    time.sleep(5 * (attempt + 1))
                    continue
                raise
        else:
            raise last_error

        file_bytes = None
        if (
            hasattr(agent, "_b64_capture_hook")
            and agent._b64_capture_hook.captured_bytes
        ):
            file_bytes = agent._b64_capture_hook.captured_bytes
        else:
            file_bytes = _extract_file(agent.messages)

        if file_bytes is None:
            return {
                "status": "error",
                "error": "Code Interpreter did not produce an output file.",
            }

        ext = "html" if skill_type == "frontend-design" else skill_type
        return {
            "status": "success",
            "filename": filename,
            "file_type": ext,
            "file_base64": __import__('base64').b64encode(file_bytes).decode("utf-8"),
            "file_size": len(file_bytes),
        }
    except Exception as e:
        logger.error(f"Skill execution failed: {str(e)}", exc_info=True)
        return {"status": "error", "error": str(e)}


def _clean_base64(raw: str) -> str:
    """Strip whitespace and non-base64 characters from extracted base64 string."""
    import re
    cleaned = raw.strip()
    cleaned = re.sub(r"[^A-Za-z0-9+/=]", "", cleaned)
    cleaned = cleaned.rstrip("=")
    padding = (4 - len(cleaned) % 4) % 4
    cleaned += "=" * padding
    return cleaned


def _safe_b64_decode(raw: str, source: str = "") -> bytes | None:
    """Decode base64 with validation — returns None instead of crashing."""
    cleaned = _clean_base64(raw)
    if len(cleaned) < 100:
        logger.warning(
            "Base64 too short (%d chars) from %s — not a real file",
            len(cleaned),
            source,
        )
        return None
    try:
        import base64
        data = base64.b64decode(cleaned)
        if len(data) < 50:
            logger.warning(
                "Decoded file too small (%d bytes) from %s — not a real file",
                len(data),
                source,
            )
            return None
        return data
    except Exception as e:
        logger.warning("Base64 decode failed from %s: %s", source, e)
        return None


def _extract_file(messages) -> bytes | None:
    """Extract generated file bytes from Strands agent conversation messages.

    Looks for BASE64_FILE_START...BASE64_FILE_END markers in text output from
    Code Interpreter. Also checks for native file blocks in toolResult.
    """
    if not messages:
        return None

    # Collect ALL text from all messages to find markers
    all_text_parts = []

    for msg in messages:
        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue

                text = block.get("text", "")
                if text:
                    all_text_parts.append(text)

                tool_result = block.get("toolResult", {})
                if tool_result:
                    for item in tool_result.get("content", []):
                        if isinstance(item, dict):
                            if "file" in item:
                                import base64
                                data = item["file"].get("data", b"")
                                if isinstance(data, str):
                                    return base64.b64decode(data)
                                return data
                            item_text = item.get("text", "")
                            if item_text:
                                all_text_parts.append(item_text)

    full_text = "".join(all_text_parts)

    if "BASE64_FILE_START" in full_text and "BASE64_FILE_END" in full_text:
        start = full_text.index("BASE64_FILE_START") + len("BASE64_FILE_START")
        end = full_text.index("BASE64_FILE_END", start)
        return _safe_b64_decode(full_text[start:end], source="_extract_file")

    return None


app.run()
