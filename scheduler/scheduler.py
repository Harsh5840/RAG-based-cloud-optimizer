"""
Scheduler and orchestration for Cloud Cost Optimizer.

Uses APScheduler to run:
- **Daily at 02:00 UTC** — AWS + GCP data ingestion.
- **Every hour** — Anomaly detection → RAG retrieval → Claude analysis → GitHub PR → Slack notification.

Handles graceful shutdown on SIGINT / SIGTERM.

Usage
-----
    python -m scheduler.scheduler
"""

from __future__ import annotations

import logging
import signal
import sys
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from actions.github_pr import create_optimization_pr
from actions.slack_notify import send_notification, send_summary_notification
from actions.terraform_gen import generate_recommendation
from detect.detector import run_detection
from detect.models import Anomaly, Recommendation
from ingest.gcp_ingest import run_gcp_ingestion
from ingest.ingest import run_ingestion
from rag.optimization_rag import retrieve_context

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Pipeline Functions
# ──────────────────────────────────────────────────────────────────────────────


def ingest_job():
    """Daily ingestion job: fetches AWS + GCP cost data into InfluxDB."""
    logger.info("=" * 60)
    logger.info("INGESTION JOB STARTED at %s", datetime.now(timezone.utc).isoformat())
    logger.info("=" * 60)

    try:
        aws_result = run_ingestion()
        logger.info("AWS ingestion result: %s", aws_result)
    except Exception as exc:
        logger.error("AWS ingestion failed: %s", exc, exc_info=True)

    try:
        gcp_result = run_gcp_ingestion()
        logger.info("GCP ingestion result: %s", gcp_result)
    except Exception as exc:
        logger.error("GCP ingestion failed: %s", exc, exc_info=True)


def _process_anomaly(anomaly: Anomaly) -> tuple[Recommendation | None, str]:
    """
    Process a single anomaly through the RAG → Claude → GitHub → Slack pipeline.

    Returns (recommendation, pr_url) or (None, "") on failure.
    """
    try:
        # Step 1: Retrieve RAG context
        logger.info("Retrieving RAG context for: %s", anomaly)
        context = retrieve_context(anomaly, top_k=5)

        # Step 2: Generate recommendation via Claude
        logger.info("Generating recommendation via Claude...")
        recommendation = generate_recommendation(anomaly, context)

        # Step 3: Create GitHub PR
        logger.info("Creating GitHub PR...")
        pr_url = create_optimization_pr(recommendation)

        # Step 4: Send Slack notification
        logger.info("Sending Slack notification...")
        send_notification(anomaly, recommendation, pr_url)

        logger.info(
            "✅ Processed: %s → savings $%.2f/mo → PR: %s",
            anomaly,
            recommendation.savings_estimate,
            pr_url,
        )
        return recommendation, pr_url

    except Exception as exc:
        logger.error("Failed to process anomaly %s: %s", anomaly, exc, exc_info=True)
        return None, ""


def detection_job():
    """Hourly detection job: finds anomalies and runs the full action pipeline."""
    logger.info("=" * 60)
    logger.info("DETECTION JOB STARTED at %s", datetime.now(timezone.utc).isoformat())
    logger.info("=" * 60)

    try:
        anomalies = run_detection()
    except Exception as exc:
        logger.error("Detection failed: %s", exc, exc_info=True)
        return

    if not anomalies:
        logger.info("No anomalies detected this hour")
        return

    logger.info("Detected %d anomalies, processing...", len(anomalies))

    total_savings = 0.0
    pr_count = 0

    for anomaly in anomalies:
        recommendation, pr_url = _process_anomaly(anomaly)
        if recommendation:
            total_savings += recommendation.savings_estimate
            if pr_url:
                pr_count += 1

    # Send daily summary if we found anything
    if anomalies:
        send_summary_notification(anomalies, total_savings, pr_count)

    logger.info(
        "Detection job complete: %d anomalies, %d PRs, $%.2f/mo total savings",
        len(anomalies),
        pr_count,
        total_savings,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Scheduler Setup
# ──────────────────────────────────────────────────────────────────────────────


def main():
    """Start the scheduler with configured jobs."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    scheduler = BlockingScheduler(timezone="UTC")

    # Daily ingestion at 02:00 UTC
    scheduler.add_job(
        ingest_job,
        trigger=CronTrigger(hour=2, minute=0),
        id="daily_ingest",
        name="Daily Cloud Cost Ingestion",
        misfire_grace_time=3600,
    )

    # Hourly anomaly detection
    scheduler.add_job(
        detection_job,
        trigger=IntervalTrigger(hours=1),
        id="hourly_detection",
        name="Hourly Anomaly Detection & Action",
        misfire_grace_time=900,
    )

    # Graceful shutdown
    def shutdown(signum, frame):
        logger.info("Received signal %s, shutting down scheduler...", signum)
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    logger.info("╔══════════════════════════════════════════════════╗")
    logger.info("║       Cloud Cost Optimizer — Scheduler          ║")
    logger.info("║                                                  ║")
    logger.info("║  Daily ingest:  02:00 UTC                       ║")
    logger.info("║  Hourly detect: every 60 minutes                ║")
    logger.info("╚══════════════════════════════════════════════════╝")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")


if __name__ == "__main__":
    main()
