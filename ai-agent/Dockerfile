# Use a slim Python 3.11 base image
FROM python:3.11-slim

# Create a non-root user and group for security
RUN groupadd --system appgroup && useradd --system --gid appgroup appuser

# Set the working directory
WORKDIR /app

# Copy and install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the source code
COPY src/ ./src/

# Switch to non-root user
USER appuser

# Run the agent script
CMD ["python", "src/main.py"]
