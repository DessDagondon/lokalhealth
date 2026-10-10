import os
from pathlib import Path

import pandas as pd
import sqlcipher3
from dotenv import load_dotenv

from utils import fetch_barangay_data, process_batch_upload, run_model_prediction, log_memory_usage


PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / '.env')


def run_all_tests():
    print("=== STARTING SYSTEM PERFORMANCE BENCHMARKS ===")
    
    # 1. Memory Check at Startup
    log_memory_usage("Application Startup")
    
    # 2. Test Database Query Latency
    conn = None
    try:
        passphrase = os.environ.get('DB_PASSPHRASE')
        if not passphrase:
            raise RuntimeError('DB_PASSPHRASE is not set; cannot open the encrypted database.')

        database_path = PROJECT_ROOT / 'instance' / 'database.db'
        conn = sqlcipher3.connect(str(database_path))
        escaped_passphrase = passphrase.replace("'", "''")
        conn.execute(f"PRAGMA key = '{escaped_passphrase}'")
        print("\n--- Testing Database Query ---")
        fetch_barangay_data(conn)
    except Exception as e:
        print(f"Note on DB test: {e}")
    finally:
        if conn is not None:
            conn.close()

    # 3. Test Batch Template Ingestion
    print("\n--- Testing Batch Ingestion ---")
    process_batch_upload("sample_dengue_data.csv")

    # 4. Test ML Inference Runtime
    print("\n--- Testing ML Inference ---")
    try:
        # Match the feature names used when the model was trained.
        dummy_features = pd.DataFrame([{
            'lag_1': 1.0,
            'lag_2': 2.0,
            'morbidity_week': 3,
        }])
        prediction = run_model_prediction(dummy_features)
        print(f"Prediction result: {prediction}")
    except Exception as e:
        print(f"ML Test Note: {e}")

    # 5. Memory Check after tests
    log_memory_usage("Post-Execution / Peak Load")
    print("\n=== BENCHMARKS COMPLETE ===")

if __name__ == "__main__":
    run_all_tests()