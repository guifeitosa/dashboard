FROM python:3.12-slim

WORKDIR /app

# Install dependencies first — separate layer for cache efficiency
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code (metrics.db excluded via .dockerignore)
COPY . .

# Non-root user for security
RUN useradd -m appuser && chown -R appuser /app
USER appuser

EXPOSE 8501

# --server.address=0.0.0.0 required so Streamlit is reachable outside the container.
# --server.headless=true disables the browser-open prompt.
CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true"]
