FROM python:3.12-slim
WORKDIR /app
COPY service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY domain /app/domain
COPY workflow /app/workflow
COPY service /app/service
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "service.runner"]
