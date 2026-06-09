FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq-dev gcc curl \
    && rm -rf /var/lib/apt/lists/*

# Instalar solo dependencias del dashboard + DB + agente RAG
RUN pip install --no-cache-dir \
    streamlit>=1.35.0 \
    plotly>=5.20.0 \
    psycopg2-binary>=2.9.9 \
    sqlalchemy>=2.0.0 \
    pandas>=2.2.0 \
    python-dotenv>=1.0.0 \
    google-genai>=1.0.0 \
    chromadb>=0.5.0

COPY app/      ./app/
COPY db/       ./db/
COPY agent/    ./agent/
COPY .streamlit/ ./.streamlit/

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "app/main.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501"]
