"""Rotinas automáticas (rodam dentro do mesmo processo do painel)."""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from .service import PromoService

log = logging.getLogger(__name__)


def start_scheduler(svc: PromoService) -> BackgroundScheduler:
    sch = BackgroundScheduler(timezone=svc.s.timezone, job_defaults={"coalesce": True, "max_instances": 1})
    for n in svc.niches.values():
        if n.enabled:
            sch.add_job(svc.collect, "interval", minutes=n.collect_every_minutes, args=[n.id],
                        id=f"collect:{n.id}")
    sch.add_job(svc.monitor_sent, "interval", minutes=60, id="monitor_sent")
    sch.add_job(svc.expire_stale_queue, "interval", minutes=30, id="expire_queue")
    sch.add_job(svc.purge_old_content, "interval", minutes=60, id="purge_content")
    sch.start()
    log.info("Agendador iniciado: %s", [j.id for j in sch.get_jobs()])
    return sch
