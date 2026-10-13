"""
PII-redaction Lambda (opt-in; triggered by S3 OBJECT_CREATED on the WORM bucket).

For each newly delivered WORM object (gzip or plain, one JSON chat record per
line), this function:
  1. reads the object from the WORM (original, immutable) bucket,
  2. runs Amazon Comprehend DetectPiiEntities over the free-text chat fields
     (user_message / system_text_message and a few common aliases),
  3. masks each detected PII span in place,
  4. writes a REDACTED copy to the reviewer-readable bucket under the same key.

The original WORM object is NEVER modified or deleted — the redacted copy is a
derived artifact for reviewer/agent access. If Comprehend fails for a record,
that record's text is masked wholesale (fail closed) rather than passed through.

Env:
  REDACTED_BUCKET     - destination bucket for redacted copies (required)
  COMPREHEND_LANGUAGE - language code for DetectPiiEntities (default "en")
"""

from __future__ import annotations

import gzip
import json
import os
import urllib.parse

import boto3

s3 = boto3.client("s3")
comprehend = boto3.client("comprehend")

REDACTED_BUCKET = os.environ["REDACTED_BUCKET"]
LANGUAGE = os.environ.get("COMPREHEND_LANGUAGE", "en")

# Comprehend PII entity types to PRESERVE (not redact). Dates and times are
# needed for e-discovery context (when something was said), so DATE_TIME is
# kept by default. Override with env PII_KEEP_TYPES (comma-separated) to keep
# or drop additional types.
_KEEP_TYPES = {
    t.strip().upper()
    for t in os.environ.get("PII_KEEP_TYPES", "DATE_TIME").split(",")
    if t.strip()
}

# Free-text fields that may contain PII and should be redacted.
TEXT_FIELDS = (
    "user_message",
    "system_text_message",
    "prompt",
    "response",
    "message",
    "text",
)

# Comprehend DetectPiiEntities accepts up to 100 KB of UTF-8 per call.
_MAX_BYTES = 95_000


def _mask_text(text: str) -> str:
    """Return `text` with every Comprehend-detected PII span replaced by
    [REDACTED:<TYPE>], EXCEPT entity types in _KEEP_TYPES (e.g. DATE_TIME),
    which are preserved because they are needed for e-discovery context.
    Fails closed: on any error, mask the whole string."""
    if not text:
        return text
    try:
        # Truncate to Comprehend's size limit (bytes), on a char boundary.
        probe = text.encode("utf-8")[:_MAX_BYTES].decode("utf-8", "ignore")
        resp = comprehend.detect_pii_entities(Text=probe, LanguageCode=LANGUAGE)
        entities = sorted(
            resp.get("Entities", []), key=lambda e: e["BeginOffset"], reverse=True
        )
        masked = text
        for e in entities:
            if e["Type"] in _KEEP_TYPES:
                continue  # preserve dates/times (and any other kept types)
            b, end = e["BeginOffset"], e["EndOffset"]
            masked = masked[:b] + f"[REDACTED:{e['Type']}]" + masked[end:]
        return masked
    except Exception as exc:  # noqa: BLE001 - fail closed
        print(f"[redact] Comprehend error, masking whole field: {exc}")
        return "[REDACTED]"


def _redact_record(rec: dict) -> dict:
    for field in TEXT_FIELDS:
        if isinstance(rec.get(field), str) and rec[field]:
            rec[field] = _mask_text(rec[field])
    return rec


def _read_object(bucket: str, key: str) -> bytes:
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    if key.endswith(".gz") or body[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(body)
        except OSError:
            return body
    return body


def handler(event, context):
    written = 0
    for record in event.get("Records", []):
        src_bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        try:
            raw = _read_object(src_bucket, key).decode("utf-8", "ignore")
        except Exception as exc:  # noqa: BLE001
            print(f"[redact] cannot read s3://{src_bucket}/{key}: {exc}")
            continue

        out_lines = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                out_lines.append(json.dumps(_redact_record(rec)))
            except json.JSONDecodeError:
                # Non-JSON line: mask wholesale rather than leak it.
                out_lines.append(json.dumps({"redacted": "[REDACTED]"}))

        # Redacted copies are stored uncompressed JSON under the same key
        # (minus any .gz suffix) so reviewers/agents can read them directly.
        out_key = key[:-3] if key.endswith(".gz") else key
        s3.put_object(
            Bucket=REDACTED_BUCKET,
            Key=out_key,
            Body=("\n".join(out_lines) + "\n").encode("utf-8"),
            ContentType="application/x-ndjson",
        )
        written += 1
        print(f"[redact] wrote redacted copy s3://{REDACTED_BUCKET}/{out_key}")

    return {"redacted_objects": written}
