FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 runner \
    && mkdir -p /app/state && chown runner:runner /app/state
COPY processor ./processor
USER runner
EXPOSE 8001
CMD ["python", "-m", "processor"]
