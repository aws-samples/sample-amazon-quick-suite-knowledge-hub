# Lambda container image for the ingestion backend.
#
# One image serves the backend Lambda functions for listing tables, extracting records,
# and extracting deletes. CDK sets each function's CMD to its handler.
# Runtime dependencies install from the locked pyproject.toml + uv.lock via
# `uv export` piped to pip into the Lambda base image's task root, so the runtime
# resolves them without uv present at run time.
#
# Build context is the backend/ package root:
#   docker build -f backend/Dockerfile backend/

FROM public.ecr.aws/lambda/python:3.12

# uv installs only to resolve the locked dependency set into a requirements file;
# it does not remain on the runtime path.
RUN pip install --no-cache-dir uv==0.9.17

COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt --target "${LAMBDA_TASK_ROOT}" \
    && rm -f /tmp/requirements.txt

# Source tree at the task root so the handler modules import cleanly.
COPY src/ "${LAMBDA_TASK_ROOT}/"

# Overridden per function by the CDK; the table extractor is the image default.
CMD [ "extract_table_handler.handler" ]
