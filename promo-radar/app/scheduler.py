"""Rotinas automáticas (rodam dentro do mesmo processo do painel)."""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from .service import PromoService

log = logging.getLogger(__name__)


def start_scheduler(svc: PromoService) -> BackgroundScheduler:
    sch = BackgroundScheduler(timezone=svc.s.timezone, job_defaults={"coalesce": True, "max_instances": 1})
    sch.add_job(svc.run_saved_searches, "interval", minutes=svc.filters.saved_search_every_minutes,
                id="buscas_salvas")
    if svc.s.monitor_sent_enabled:
        sch.add_job(svc.monitor_sent, "interval", minutes=60, id="monitor_sent")
    sch.add_job(svc.expire_stale_queue, "interval", minutes=30, id="expire_queue")
    sch.add_job(svc.purge_old_content, "interval", minutes=60, id="purge_content")
    sch.add_job(svc.housekeeping, "cron", hour=4, minute=10, id="housekeeping")
    sch.start()
    log.info("Agendador iniciado: %s", [j.id for j in sch.get_jobs()])
    return sch
