FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY ara/requirements.txt /app/ara/
RUN pip install --no-cache-dir -r /app/ara/requirements.txt \
    && pip install --no-cache-dir gunicorn gevent waitress reportlab

COPY ara/ARA_Brain/ /app/ara/ARA_Brain/
COPY gunicorn.conf.py /app/ara/ARA_Brain/

ENV PYTHONUNBUFFERED=1
ENV FLASK_ENV=production

WORKDIR /app/ara/ARA_Brain
EXPOSE 5000

CMD ["gunicorn", "-c", "gunicorn.conf.py", "ara_server:app"]
