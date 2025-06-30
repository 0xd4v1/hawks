FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV GO_VERSION=1.21.5
ENV PATH="/usr/local/go/bin:/root/go/bin:${PATH}"

# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Go
RUN wget https://golang.org/dl/go${GO_VERSION}.linux-amd64.tar.gz \
    && tar -C /usr/local -xzf go${GO_VERSION}.linux-amd64.tar.gz \
    && rm go${GO_VERSION}.linux-amd64.tar.gz

# Set work directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Go tools
RUN go install -v github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest && \
    go install -v github.com/projectdiscovery/httpx/cmd/httpx@latest && \
    go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest && \
    go install -v github.com/projectdiscovery/chaos-client/cmd/chaos@latest

# Update Nuclei templates
RUN nuclei -update-templates

# Copy application code
COPY . .

# Create directories
RUN mkdir -p templates/custom app/static

# Create default .env if not exists
RUN if [ ! -f .env ]; then \
    python3 -c "import secrets, string; \
    secret_key = secrets.token_urlsafe(64); \
    chars = string.ascii_letters + string.digits + '!@#\$%^&*'; \
    secure_password = ''.join(secrets.choice(chars) for _ in range(16)); \
    print(f'SECRET_KEY={secret_key}'); \
    print(f'ADMIN_USERNAME=admin'); \
    print(f'ADMIN_PASSWORD={secure_password}'); \
    print(f'CHAOS_API_KEY='); \
    print(f'DATABASE_URL=sqlite:///./hawks.db')" > .env; \
    fi

# Initialize database
RUN python3 -c "from app.database import init_db; init_db()"

# Set proper permissions
RUN chmod 600 .env 2>/dev/null || true
RUN chmod 640 hawks.db 2>/dev/null || true

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python3 -c "import requests; requests.get('http://localhost:8000', timeout=5)" || exit 1

# Run the application
CMD ["python3", "main.py"] 