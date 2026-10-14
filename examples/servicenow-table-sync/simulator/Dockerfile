# Lambda container image for the ServiceNow simulator (Mangum entry point app.handler).
# Build from the simulator/ package root:
#   docker build -f simulator/Dockerfile simulator/

FROM public.ecr.aws/lambda/python:3.12

RUN pip install --no-cache-dir uv==0.9.17

COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt --target "${LAMBDA_TASK_ROOT}" \
    && rm -f /tmp/requirements.txt

COPY src/ "${LAMBDA_TASK_ROOT}/"

CMD [ "app.handler" ]
