"""
Quick script to run the mocked pipeline immediately without waiting for the scheduler.
"""
import logging
from scheduler.scheduler import ingest_job, detection_job

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    )
    
    print("="*60)
    print("🚀 Running Demo Pipeline")
    print("="*60)
    
    print("\n--- Phase 1: Ingestion ---")
    ingest_job()
    
    print("\n--- Phase 2: Anomaly Detection & Action Pipeline ---")
    detection_job()
    
    print("\n✅ Demo run complete!")

if __name__ == "__main__":
    main()
