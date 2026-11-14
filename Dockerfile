FROM python:3.12-slim

WORKDIR /srv/workpilot

COPY backend/requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./alembic.ini
COPY seed ./seed

ENV PYTHONPATH=/srv/workpilot

CMD ["sh", "-c", "alembic upgrade head && python -m app.seed_import && uvicorn app.main:app --host 0.0.0.0 --port \"$PORT\""]
