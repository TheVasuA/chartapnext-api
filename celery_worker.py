# Re-export celery_app so Celery CLI can find it:
#   celery -A celery_worker.celery_app worker --loglevel=info
#   celery -A celery_worker.celery_app beat  --loglevel=info
from app.tasks.signal_tasks import celery_app  # noqa: F401
