import time
import psutil
import os
import joblib
from functools import wraps
from flask import render_template

# Load the trained machine learning model safely
MODEL_PATH = os.path.join('models', 'random_forest_tuned.joblib')
model = None
if os.path.exists(MODEL_PATH):
    try:
        model = joblib.load(MODEL_PATH)
    except Exception as e:
        print(f"Warning: Could not load ML model: {e}")

# 1. Timing Decorator for Latency and Inference
def measure_time(metric_name):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                duration_ms = (time.perf_counter() - start_time) * 1000
                print(f"[BENCHMARK] {metric_name} -> Execution Time: {duration_ms:.2f} ms")
        return wrapper
    return decorator

# 2. Memory Footprint Checker
def log_memory_usage(checkpoint_name):
    process = psutil.Process(os.getpid())
    memory_mb = process.memory_info().rss / (1024 * 1024)
    print(f"[BENCHMARK] Memory Footprint ({checkpoint_name}) -> {memory_mb:.2f} MB")
    return memory_mb

@measure_time("Database Query Latency")
def fetch_barangay_data(db_connection):
    cursor = db_connection.cursor()
    cursor.execute("SELECT * FROM dengue_record")
    return cursor.fetchall()

@measure_time("Batch Template Data Ingestion")
def process_batch_upload(file_path):
    print(f"Processing file: {file_path}")
    time.sleep(0.05)

@measure_time("ML Inference Runtime")
def run_model_prediction(input_features):
    if model is None:
        raise RuntimeError("Machine learning model is not loaded. Please run train_model_2.py first to generate models/random_forest_tuned.joblib.")
    prediction = model.predict(input_features)
    return prediction