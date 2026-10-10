import os
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import GridSearchCV, TimeSeriesSplit, train_test_split
from sklearn.metrics import mean_squared_error, mean_absolute_error

# --- STEP 0: LOAD AND STANDARDIZE YOUR EXCEL DATASETS ---
file_names = [
    'forATENEO_DENGUE_2023.xlsx',
    'forATENEO_DENGUE_2024.xlsx',
    'forATENEO_Dengue_2025.xlsx' if os.path.exists('forATENEO_Dengue_2025.xlsx') else 'forATENEO_DENGUE_2025.xlsx',
    'forATENEO_DENGUE_2026.xlsx'
]

dataframes = []
for file in file_names:
    if os.path.exists(file):
        print(f"Loading {file}...")
        df_temp = pd.read_excel(file)
        
        # Normalize column names to lowercase and strip whitespace
        df_temp.columns = [str(c).strip().lower() for c in df_temp.columns]
        
        # Detect year from filename if year column is missing
        if 'year' not in df_temp.columns:
            if '2023' in file: df_temp['year'] = 2023
            elif '2024' in file: df_temp['year'] = 2024
            elif '2025' in file: df_temp['year'] = 2025
            elif '2026' in file: df_temp['year'] = 2026
            
        mw_col = next((c for c in df_temp.columns if 'week' in c or c == 'mw'), None)
        if mw_col and mw_col != 'morbidity_week':
            df_temp['morbidity_week'] = pd.to_numeric(df_temp[mw_col], errors='coerce')
        elif 'morbidity_week' in df_temp.columns:
            df_temp['morbidity_week'] = pd.to_numeric(df_temp['morbidity_week'], errors='coerce')
        else:
            df_temp['morbidity_week'] = (df_temp.index % 52) + 1
            
        dataframes.append(df_temp[['year', 'morbidity_week']])
    else:
        print(f"Warning: {file} not found. Skipping.")

if not dataframes:
    raise FileNotFoundError("No dengue dataset Excel files were found.")

df = pd.concat(dataframes, ignore_index=True)
df = df.dropna(subset=['year', 'morbidity_week'])
df['year'] = df['year'].astype(int)
df['morbidity_week'] = df['morbidity_week'].astype(int)

# --- ROBUST FEATURE ENGINEERING ---
weekly_counts = df.groupby(['year', 'morbidity_week']).size().reset_index(name='case_count')
weekly_counts = weekly_counts.sort_values(['year', 'morbidity_week']).reset_index(drop=True)

# 1. Extended Lag Features (1 to 4 weeks back for deeper historical context)
for lag in range(1, 5):
    weekly_counts[f'lag_{lag}'] = weekly_counts['case_count'].shift(lag)

# 2. Rolling Statistics (smooths out erratic reporting spikes/drops)
weekly_counts['rolling_mean_3'] = weekly_counts['case_count'].shift(1).rolling(window=3).mean()
weekly_counts['rolling_std_3'] = weekly_counts['case_count'].shift(1).rolling(window=3).std().fillna(0)
weekly_counts['rolling_max_3'] = weekly_counts['case_count'].shift(1).rolling(window=3).max()

# 3. Cyclical Seasonality Encoding (sin/cos mapping prevents abrupt year-end jumps)
weekly_counts['sin_week'] = np.sin(2 * np.pi * weekly_counts['morbidity_week'] / 52.0)
weekly_counts['cos_week'] = np.cos(2 * np.pi * weekly_counts['morbidity_week'] / 52.0)

# Drop NaN resulting from rolling windows & lags
training_data = weekly_counts.dropna().reset_index(drop=True)

feature_cols = [
    'lag_1', 'lag_2', 'lag_3', 'lag_4',
    'rolling_mean_3', 'rolling_std_3', 'rolling_max_3',
    'morbidity_week', 'sin_week', 'cos_week'
]

X = training_data[feature_cols]
y = training_data['case_count']

print(f"Successfully prepared robust training data with shape: {X.shape}")

# Chronological split (preserving time order)
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, shuffle=False
)

# --- ROBUST HYPERPARAMETER GRID (Prevents Overfitting & Flimsiness) ---
param_grid = {
    'n_estimators': [200, 300],
    'max_depth': [8, 12, 16],
    'min_samples_split': [5, 10],
    'min_samples_leaf': [2, 4],
    'max_features': ['sqrt', 0.8]
}

rf = RandomForestRegressor(random_state=42, bootstrap=True)
tscv = TimeSeriesSplit(n_splits=5)

grid_search = GridSearchCV(
    estimator=rf,
    param_grid=param_grid,
    cv=tscv,
    scoring='neg_root_mean_squared_error',
    n_jobs=-1
)

print('Training robust, anti-flimsy Random Forest model...')
grid_search.fit(X_train, y_train)

best_model = grid_search.best_estimator_
print('Best Hyperparameters found:', grid_search.best_params_)

# Evaluate on test set
y_pred = best_model.predict(X_test)
rmse = np.sqrt(mean_squared_error(y_test, y_pred))
mae = mean_absolute_error(y_test, y_pred)
print(f"Validation Performance -> Test RMSE: {rmse:.2f} | Test MAE: {mae:.2f}")

# --- SAVE MODEL ---
os.makedirs('models', exist_ok=True)
joblib.dump(best_model, 'models/random_forest_tuned.joblib')
print('Robust model saved successfully to models/random_forest_tuned.joblib!')