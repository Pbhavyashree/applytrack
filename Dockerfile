FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput \
    && useradd --create-home app
USER app

EXPOSE 8000
# Migrations run on start so a fresh deploy comes up ready.
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn config.wsgi --bind 0.0.0.0:8000 --workers 2 --access-logfile -"]
