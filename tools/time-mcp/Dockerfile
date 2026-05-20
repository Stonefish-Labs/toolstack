FROM python:3.12-slim

RUN useradd -u 10000 -m app
WORKDIR /home/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

USER 10000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5100"]
