import os
import re
import secrets
from io import BytesIO, StringIO
from datetime import date, datetime, timedelta
import sys
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_squared_error, mean_absolute_error
from sklearn.model_selection import GridSearchCV, TimeSeriesSplit
from sqlalchemy import case, event, text, or_
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from utils import log_memory_usage

load_dotenv() 

app = Flask(__name__)
app.config['SECRET_KEY'] = 'lokalhealth-epidemiological-secret-key-2026'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
os.makedirs(app.instance_path, exist_ok=True)
DB_PASSPHRASE = os.environ.get('DB_PASSPHRASE')

def database_file_looks_encrypted():
    database_path = os.path.join(app.instance_path, 'database.db')
    try:
        with open(database_path, 'rb') as database_file:
            return database_file.read(16) != b'SQLite format 3\x00'
    except OSError:
        return False

    # Guard: Block execution if an encrypted database file exists without a passphrase
if not DB_PASSPHRASE and database_file_looks_encrypted():
    print("\n" + "=" * 60)
    print("FATAL ERROR: DB_PASSPHRASE environment variable is missing!")
    print("The database is SQLCipher-encrypted and requires DB_PASSPHRASE.")
    print("Please set DB_PASSPHRASE in your .env file or terminal.")
    print("=" * 60 + "\n")
    sys.exit(1)

if not DB_PASSPHRASE and database_file_looks_encrypted():
    raise RuntimeError(
        'The database is SQLCipher-encrypted. Set DB_PASSPHRASE in this same terminal before starting app.py.'
    )

if DB_PASSPHRASE:
    import sqlcipher3

    encrypted_database_path = os.path.join(app.instance_path, 'database.db')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite://'
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'creator': lambda: sqlcipher3.connect(encrypted_database_path, check_same_thread=False),
    }
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'connect_args': {'check_same_thread': False}
    }

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

if DB_PASSPHRASE:
    @event.listens_for(Engine, 'connect')
    def set_sqlcipher_key(dbapi_connection, connection_record):
        escaped_passphrase = DB_PASSPHRASE.replace("'", "''")
        dbapi_connection.execute(f"PRAGMA key = '{escaped_passphrase}'")
        cipher_version = dbapi_connection.execute('PRAGMA cipher_version').fetchone()
        if not cipher_version or not cipher_version[0]:
            raise RuntimeError('DB_PASSPHRASE requires a SQLCipher-enabled SQLite driver.')

@app.before_request
def ensure_admin_on_every_request():
    ensure_primary_admin_account()

@app.before_request
def require_password_change_completion():
    if (
        current_user.is_authenticated
        and current_user.must_change_password
        and request.endpoint not in {'change_password', 'logout', 'static'}
    ):
        return redirect(url_for('change_password'))

# ==========================================
# DATABASE SCHEMAS (With RBAC and Permissions)
# ==========================================

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), default='Viewer') # Default role on sign up

    # Granular Permission Flags (Viewer defaults: read/export only)
    can_create = db.Column(db.Boolean, default=False) # Blocked from manual entry & uploads
    can_edit = db.Column(db.Boolean, default=False)
    can_delete = db.Column(db.Boolean, default=False)
    is_blocked = db.Column(db.Boolean, default=False)
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class DengueRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.String(50), unique=True, nullable=False)  # Anonymized ID
    year = db.Column(db.Integer, nullable=True, default=datetime.now().year)
    morbidity_month = db.Column(db.Integer, nullable=False)
    morbidity_week = db.Column(db.Integer, nullable=True)
    district = db.Column(db.String(100), nullable=False, default='Talomo')
    barangay = db.Column(db.String(100), nullable=False)
    age = db.Column(db.Integer, nullable=False)
    sex = db.Column(db.String(10), nullable=False)
    clinical_classification = db.Column(db.String(100), nullable=True)
    case_classification = db.Column(db.String(100), nullable=True)
    sync_status = db.Column(db.String(20), default='Synced')  # 'Manual/Local' or 'Synced'
    is_archived = db.Column(db.Boolean, default=False, server_default=db.false(), nullable=False)


class RecordAuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(db.Integer, nullable=False, index=True)
    username = db.Column(db.String(80), nullable=False)
    action_type = db.Column(db.String(20), nullable=False)
    field_changed = db.Column(db.String(100), nullable=False)
    old_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

def ensure_primary_admin_account():
    try:
        with app.app_context():
            existing = User.query.filter_by(username='admin').first()
            if existing is None:
                user = User(
                    username='admin',
                    role='Admin',
                    can_create=True,
                    can_edit=True,
                    can_delete=True,
                    is_blocked=False,
                    must_change_password=False,
                )
                user.set_password('Admin123!')
                db.session.add(user)
                db.session.commit()
                return True

            existing.role = 'Admin'
            existing.can_create = True
            existing.can_edit = True
            existing.can_delete = True
            existing.is_blocked = False
            existing.must_change_password = False
            existing.set_password('Admin123!')
            db.session.commit()
            return True
    except Exception:
        db.session.rollback()
        return False

@login_manager.user_loader
def load_user(user_id):
    user = db.session.get(User, int(user_id))
    return user if user and not user.is_blocked else None

class SystemEvaluation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    evaluator_role = db.Column(db.String(50), nullable=False) # e.g., 'BHW', 'City Health Officer'
    task_completion_seconds = db.Column(db.Float, nullable=False)
    sus_score = db.Column(db.Float, nullable=False) # Standard 0-100 SUS score
    watchlist_agreement = db.Column(db.String(20), nullable=False) # 'Agree', 'Neutral', 'Disagree'
    notes = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

@app.route('/evaluation', methods=['GET', 'POST'])
@login_required
def evaluation():
    if request.method == 'POST':
        try:
            # Extract the 10 standard SUS Likert responses (1 to 5)
            sus_responses = [int(request.form.get(f'q{i}', 3)) for i in range(1, 11)]
            
            # Calculate official standard SUS score (0-100)
            # Odd items (indices 0, 2, 4, 6, 8 for Q1, 3, 5, 7, 9): score = response - 1
            odd_sum = sum(r - 1 for i, r in enumerate(sus_responses) if i % 2 == 0)
            # Even items (indices 1, 3, 5, 7, 9 for Q2, 4, 6, 8, 10): score = 5 - response
            even_sum = sum(5 - r for i, r in enumerate(sus_responses) if i % 2 != 0)
            
            calculated_sus = (odd_sum + even_sum) * 2.5

            eval_record = SystemEvaluation(
                evaluator_role=request.form.get('evaluator_role', 'BHW'),
                task_completion_seconds=float(request.form.get('task_completion_seconds', 0)),
                sus_score=float(calculated_sus), # Automatically computed true SUS score
                watchlist_agreement=request.form.get('watchlist_agreement', 'Agree'),
                notes=request.form.get('notes', '')
            )
            db.session.add(eval_record)
            db.session.commit()
            flash('True SUS evaluation metric recorded successfully.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving evaluation: {str(e)}', 'error')
        return redirect(url_for('evaluation'))

    evaluations = SystemEvaluation.query.all()
    avg_sus = round(sum(e.sus_score for e in evaluations) / len(evaluations), 2) if evaluations else 0
    avg_time = round(sum(e.task_completion_seconds for e in evaluations) / len(evaluations), 2) if evaluations else 0
    
    return render_template('evaluation.html', evaluations=evaluations, avg_sus=avg_sus, avg_time=avg_time)

# ==========================================
# AUTHENTICATION ROUTES
# ==========================================

@app.route('/')
def index():
    return redirect(url_for('login'))

# Screen 1: Login & Sign Up Authentication
@app.route('/login', methods=['GET', 'POST'])
def login():
    ensure_primary_admin_account()

    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        
        if user and user.is_blocked:
            flash('This account has been blocked. Contact your System Administrator.', 'error')
        elif user and user.check_password(password):
            login_user(user)
            if user.must_change_password:
                return redirect(url_for('change_password'))
            return redirect(url_for('dashboard'))
        elif not user or not user.check_password(password):
            flash('Invalid username or password. Please try again.', 'error')
        
    return render_template('login.html')

@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        password = request.form.get('password', '')
        confirmation = request.form.get('confirmation', '')
        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'error')
        elif password != confirmation:
            flash('Passwords do not match.', 'error')
        else:
            current_user.set_password(password)
            current_user.must_change_password = False
            db.session.commit()
            flash('Your password has been updated.', 'success')
            return redirect(url_for('dashboard'))
    return render_template('change_password.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    flash('Self-registration is disabled. Contact your System Administrator to request access.', 'info')
    return redirect(url_for('login'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

# ==========================================
# CORE APPLICATION SCREENS
# ==========================================

# Screen 2: Main Surveillance Dashboard (Tiers 1, 2, & 3)
NULL_LIKE_VALUES = {'', 'nan', 'n/a', 'na', 'null', 'none', 'unavailable'}

def is_available_dashboard_value(value):
    return value is not None and str(value).strip().lower() not in NULL_LIKE_VALUES

@app.route('/test-load')
@login_required
def load_dashboard():
    # Log memory before/after loading heavy templates or data
    log_memory_usage("During Dashboard Load")
    return render_template('dashboard.html', user=current_user)

def get_dashboard_years():
    return [
        int(year)
        for year, in db.session.query(DengueRecord.year)
        .filter(
            DengueRecord.year.isnot(None),
            DengueRecord.is_archived.is_(False),
        )
        .distinct()
        .order_by(DengueRecord.year.asc())
        .all()
    ]

def get_morbidity_week_date_range(year, morbidity_week):
    year_start = date(year, 1, 1)
    first_week_start = year_start - timedelta(days=(year_start.weekday() + 1) % 7)
    week_start = first_week_start + timedelta(weeks=morbidity_week - 1)
    week_end = week_start + timedelta(days=6)
    start_label = f'{week_start.strftime("%b")} {week_start.day}'
    end_label = f'{week_end.strftime("%b")} {week_end.day}, {week_end.year}'
    return f'{start_label} \u2013 {end_label}'

def evaluate_dashboard_alert(current_count, baseline_threshold):
    pre_surge_threshold = round(0.75 * baseline_threshold, 2)
    if current_count == 0 and baseline_threshold == 0:
        return {
            'risk_level': 'GREEN',
            'risk_label': 'No Data / Normal State',
            'recommendation': 'No baseline cases are available for comparison.',
            'action_required': 'Action Required: Continue routine monitoring while awaiting reported case data.',
        }
    if current_count < pre_surge_threshold:
        return {
            'risk_level': 'GREEN',
            'risk_label': 'Normal State',
            'recommendation': 'Routine Monitoring',
            'action_required': 'Action Required: Continue routine monitoring and community prevention activities.',
        }
    if current_count <= baseline_threshold:
        return {
            'risk_level': 'YELLOW',
            'risk_label': 'Warning State',
            'recommendation': 'Pre-surge Advisory',
            'action_required': 'Action Required: Issue a pre-surge advisory and increase local surveillance.',
        }
    return {
        'risk_level': 'RED',
        'risk_label': 'Surge State',
        'recommendation': 'Threshold Breach & Targeted Intervention',
        'action_required': 'Action Required: Initiate targeted vector control and localized response.',
    }


def get_intervention_priority(active_cases):
    if active_cases >= 5:
        return 'SURGE', 'danger'
    if active_cases >= 2:
        return 'RISING', 'warning'
    return 'STABLE', 'success'


def get_dashboard_trend(current_year=None):
    current_year = current_year or datetime.now().year
    baseline_years = tuple(year for year in get_dashboard_years() if year < current_year)
    def calculate_quantile(values, quantile):
        return round(pd.Series(values, dtype='float64').quantile(quantile), 2) if values else 0

    relevant_years = baseline_years + (current_year,)
    reported_week_rows = db.session.query(
        DengueRecord.year,
        DengueRecord.morbidity_week,
    ).filter(
        DengueRecord.year.in_(relevant_years),
        DengueRecord.morbidity_week.between(1, 52),
        DengueRecord.is_archived.is_(False),
    ).distinct().all()
    reported_weeks_by_year = {}
    for year, week in reported_week_rows:
        if year is not None and week is not None:
            reported_weeks_by_year.setdefault(int(year), set()).add(int(week))

    weekly_baseline_years = tuple(
        year for year in baseline_years if year in reported_weeks_by_year
    )
    weekly_years = weekly_baseline_years + (current_year,)
    grouped_counts = db.session.query(
        DengueRecord.year,
        DengueRecord.morbidity_week,
        db.func.count(DengueRecord.id),
    ).filter(
        DengueRecord.year.in_(weekly_years),
        DengueRecord.morbidity_week.between(1, 52),
        DengueRecord.is_archived.is_(False),
        DengueRecord.case_classification.ilike('%confirmed%')
    ).group_by(DengueRecord.year, DengueRecord.morbidity_week).all()

    counts_by_year_week = {
        (int(year), int(week)): int(count)
        for year, week, count in grouped_counts
        if year is not None and week is not None
    }
    current_actual = [counts_by_year_week.get((current_year, week), 0) for week in range(1, 53)]
    current_week_data_available = current_year in reported_weeks_by_year
    if not current_week_data_available:
        current_actual = [None] * 52
    historical_values = [
        [counts_by_year_week.get((year, week), 0) for year in weekly_baseline_years]
        for week in range(1, 53)
    ]
    median = [calculate_quantile(values, 0.5) for values in historical_values]
    percentile_75 = [calculate_quantile(values, 0.75) for values in historical_values]

    grouped_month_counts = db.session.query(
        DengueRecord.year,
        DengueRecord.morbidity_month,
        db.func.count(DengueRecord.id),
    ).filter(
        DengueRecord.year.in_(relevant_years),
        DengueRecord.morbidity_month.isnot(None),
        DengueRecord.morbidity_month.between(1, 12),
        DengueRecord.is_archived.is_(False),
        DengueRecord.case_classification.ilike('%confirmed%'),
    ).group_by(DengueRecord.year, DengueRecord.morbidity_month).all()
    counts_by_year_month = {
        (int(year), int(month)): int(count)
        for year, month, count in grouped_month_counts
        if year is not None and month is not None
    }
    monthly_actual = [counts_by_year_month.get((current_year, month), 0) for month in range(1, 13)]
    monthly_baseline_values = [
        [counts_by_year_month.get((year, month), 0) for year in baseline_years]
        for month in range(1, 13)
    ]
    monthly_median = [calculate_quantile(values, 0.5) for values in monthly_baseline_values]
    monthly_percentile_75 = [calculate_quantile(values, 0.75) for values in monthly_baseline_values]

    active_year = datetime.now().year
    if current_year == active_year:
        reported_weeks = sorted(reported_weeks_by_year.get(current_year, set()))
        current_week = max(reported_weeks) if reported_weeks else min(datetime.now().isocalendar().week, 52)
    else:
        reported_weeks = reported_weeks_by_year.get(current_year, set())
        current_week = max(reported_weeks) if reported_weeks else 52
    current_index = current_week - 1
    current_count = current_actual[current_index]
    current_median = median[current_index]
    current_threshold = percentile_75[current_index]
    has_baseline_data = bool(weekly_baseline_years)
    if not has_baseline_data:
        alert = {
            'risk_level': 'BLUE',
            'risk_label': 'Historical Baseline',
            'recommendation': 'Historical baseline reference only.',
            'action_required': 'Insufficient prior historical data to establish a P75 epidemic threshold.',
        }
    elif not current_week_data_available:
        alert = {
            'risk_level': 'BLUE',
            'risk_label': 'Weekly Data Unavailable',
            'recommendation': 'Weekly alert unavailable.',
            'action_required': 'Morbidity-week data is unavailable for this year; review monthly trends instead.',
        }
    else:
        alert = evaluate_dashboard_alert(current_count, current_threshold)
    week_date_range = get_morbidity_week_date_range(current_year, current_week)

    return {
        'year': current_year,
        'weeks': list(range(1, 53)),
        'actual': current_actual,
        'median': median,
        'baseline_75th': percentile_75,
        'monthly_actual': monthly_actual,
        'monthly_median': monthly_median,
        'monthly_baseline_75th': monthly_percentile_75,
        'current_week': current_week,
        'week_date_range': week_date_range,
        'current_count': current_count,
        'current_median': current_median,
        'current_threshold': current_threshold,
        'has_baseline_data': has_baseline_data,
        'weekly_data_available': current_week_data_available,
        'pre_surge_threshold': round(0.75 * current_threshold, 2),
        **alert,
    }

@app.route('/api/dashboard/trend')
@login_required
def dashboard_trend_api():
    requested_year = request.args.get('year', type=int)
    return jsonify(get_dashboard_trend(requested_year))

@app.route('/api/dashboard/forecast')
@login_required
def dashboard_forecast_api():
    requested_year = request.args.get('year', type=int)
    current_year = requested_year or datetime.now().year
    trend = get_dashboard_trend(current_year)
    requested_week = request.args.get('week', type=int)
    current_week = requested_week if requested_week and 1 <= requested_week <= 52 else trend['current_week']
    horizon = request.args.get('horizon', default=1, type=int)
    horizon = max(1, min(horizon, 8))
    target_week = current_week + 1

    grouped_counts = db.session.query(
        DengueRecord.morbidity_week,
        db.func.count(DengueRecord.id),
    ).filter(
        DengueRecord.year == current_year,
        DengueRecord.morbidity_week.isnot(None),
        DengueRecord.morbidity_week.between(1, 52),
        DengueRecord.is_archived.is_(False),
        DengueRecord.case_classification.ilike('%confirmed%')
    ).group_by(DengueRecord.morbidity_week).order_by(DengueRecord.morbidity_week).all()

    if len(grouped_counts) < 4:
        return jsonify({
            'current_week': current_week,
            'target_week': target_week,
            'current_cases': None,
            'predicted_cases': None,
            'diff': None,
            'rmse_rf': None,
            'mae_rf': None,
            'rmse_naive': None,
            'mae_naive': None,
            'rmse_lr': None,
            'mae_lr': None,
            'rmse_seasonal': None,
            'mae_seasonal': None,
            'mean_error': None,
            'forecasts': [],
        })

    counts_by_week = {int(week): int(count) for week, count in grouped_counts}
    if current_week not in counts_by_week:
        return jsonify({
            'current_week': current_week,
            'target_week': target_week,
            'current_cases': None,
            'predicted_cases': None,
            'diff': None,
            'rmse_rf': None,
            'mae_rf': None,
            'rmse_naive': None,
            'mae_naive': None,
            'rmse_lr': None,
            'mae_lr': None,
            'rmse_seasonal': None,
            'mae_seasonal': None,
            'mean_error': None,
            'forecasts': [],
        })

    latest_cases = counts_by_week[current_week]
    weekly_counts = pd.DataFrame({
        'morbidity_week': list(range(1, current_week + 1)),
        'case_count': [counts_by_week.get(week, 0) for week in range(1, current_week + 1)],
    })
    weekly_counts['lag_1'] = weekly_counts['case_count'].shift(1)
    weekly_counts['lag_2'] = weekly_counts['case_count'].shift(2)
    training_data = weekly_counts.dropna()
    if training_data.empty:
        return jsonify({
            'current_week': current_week,
            'target_week': target_week,
            'current_cases': latest_cases,
            'predicted_cases': None,
            'diff': None,
            'rmse_rf': None,
            'mae_rf': None,
            'rmse_naive': None,
            'mae_naive': None,
            'rmse_lr': None,
            'mae_lr': None,
            'rmse_seasonal': None,
            'mae_seasonal': None,
            'mean_error': None,
            'forecasts': [],
        })

    # 1. Prepare Features & Target
    X = training_data[['lag_1', 'lag_2', 'morbidity_week']].dropna()
    y = training_data.loc[X.index, 'case_count']

    # Hyperparameter Grid Configuration
    param_grid = {
        'n_estimators': [50, 100, 200],
        'max_depth': [None, 10, 20, 30]
    }

    # Walk-Forward Validation Strategy using TimeSeriesSplit
    n_splits = min(3, len(training_data) - 1)
    if n_splits < 2:
        # Fallback for small sample sizes
        model = RandomForestRegressor(
            criterion='squared_error',
            n_estimators=100,
            min_samples_split=2,
            min_samples_leaf=1,
            random_state=42
        )
        model.fit(X, y)
    else:
        tscv = TimeSeriesSplit(n_splits=n_splits)
        rf = RandomForestRegressor(
            criterion='squared_error',
            min_samples_split=2,
            min_samples_leaf=1,
            random_state=42
        )
        grid_search = GridSearchCV(
            estimator=rf,
            param_grid=param_grid,
            cv=tscv,
            scoring='neg_mean_squared_error',
            n_jobs=1
        )
        grid_search.fit(X, y)
        model = grid_search.best_estimator_

    # In-sample metrics calculation for performance validation
    y_pred_in = model.predict(X)
    rmse_rf = round(float(np.sqrt(mean_squared_error(y, y_pred_in))), 2)
    mae_rf = round(float(mean_absolute_error(y, y_pred_in)), 2)
    mean_error = round(float(np.mean(y_pred_in - y)), 2)
    
    # 2. Naive baseline: forecast equals the previous period's value (lag_1)
    y_naive = X['lag_1']
    y_actual_naive = y.loc[y_naive.index]
    rmse_naive = round(float(np.sqrt(mean_squared_error(y_actual_naive, y_naive))), 2)
    mae_naive = round(float(mean_absolute_error(y_actual_naive, y_naive)), 2)

    # 3. Linear Regression Baseline 
    lr_model = LinearRegression()
    lr_model.fit(X, y)
    y_pred_lr = lr_model.predict(X)
    rmse_lr = round(float(np.sqrt(mean_squared_error(y, y_pred_lr))), 2)
    mae_lr = round(float(mean_absolute_error(y, y_pred_lr)), 2)

    # 4. Historical Seasonal Median Baseline 
    historical_medians = trend['median'] # from get_dashboard_trend()
    y_seasonal_baseline = X['morbidity_week'].apply(
        lambda w: historical_medians.iloc[int(w) - 1] if hasattr(historical_medians, 'iloc') and 1 <= int(w) <= len(historical_medians) 
        else (historical_medians[int(w) - 1] if 1 <= int(w) <= len(historical_medians) else y.mean())
    )
    rmse_seasonal = round(float(np.sqrt(mean_squared_error(y, y_seasonal_baseline))), 2)
    mae_seasonal = round(float(mean_absolute_error(y, y_seasonal_baseline)), 2)

    forecast_history = weekly_counts['case_count'].astype(float).tolist()
    forecasts = []
    for step in range(1, horizon + 1):
        forecast_week = current_week + step
        forecast_value = model.predict(pd.DataFrame([{
            'lag_1': forecast_history[-1],
            'lag_2': forecast_history[-2],
            'morbidity_week': forecast_week,
        }]))[0]
        forecast_value = max(0, float(forecast_value))
        uncertainty = 1.96 * rmse_rf * np.sqrt(step)
        forecasts.append({
            'week': forecast_week,
            'predicted_cases': round(forecast_value, 2),
            'lower_bound': round(max(0, forecast_value - uncertainty), 2),
            'upper_bound': round(forecast_value + uncertainty, 2),
        })
        forecast_history.append(forecast_value)

    predicted_cases = forecasts[0]['predicted_cases']

    return jsonify({
        'current_week': current_week,
        'target_week': target_week,
        'current_cases': latest_cases,
        'predicted_cases': predicted_cases,
        'diff': round(predicted_cases - latest_cases, 2),
        'rmse_rf': rmse_rf,
        'mae_rf': mae_rf,
        'rmse_naive': rmse_naive,
        'mae_naive': mae_naive,
        'rmse_lr': rmse_lr,
        'mae_lr': mae_lr,
        'rmse_seasonal': rmse_seasonal,
        'mae_seasonal': mae_seasonal,
        'mean_error': mean_error,
        'forecasts': forecasts,
    })

@app.route('/dashboard')
@login_required
def dashboard():
    page = request.args.get('page', 1, type=int)
    page = max(page, 1)
    requested_year = request.args.get('year', type=int)
    available_years = get_dashboard_years()
    selected_year = requested_year if requested_year in available_years else (available_years[-1] if available_years else datetime.now().year)

    records_list = DengueRecord.query.filter(
        DengueRecord.year == selected_year,
        DengueRecord.is_archived.is_(False),
    ).all()
    confirmed_records = [
        record for record in records_list
        if is_available_dashboard_value(record.case_classification)
        and 'confirmed' in str(record.case_classification).lower()
    ]
    confirmed_cases = len(confirmed_records)
    
    # Calculate trend and active morbidity week
    trend = get_dashboard_trend(selected_year)
    active_week = trend['current_week']

    # 1. Calculate cumulative YTD and active week cases per barangay
    barangay_counts = {}
    active_week_counts = {}
    for record in confirmed_records:
        if is_available_dashboard_value(record.barangay):
            barangay = str(record.barangay).strip()
            barangay_counts[barangay] = barangay_counts.get(barangay, 0) + 1
            
            try:
                rec_week = int(record.morbidity_week) if record.morbidity_week else None
                if rec_week == active_week:
                    active_week_counts[barangay] = active_week_counts.get(barangay, 0) + 1
            except (ValueError, TypeError):
                continue

    clusters = sorted(barangay_counts)

    # 2. Build Intervention Priority Watchlist using active week metrics
    watchlist = []
    for barangay in clusters if trend['weekly_data_available'] else ():
        active_cases = active_week_counts.get(barangay, 0)
        total_ytd = barangay_counts.get(barangay, 0)

        status, status_color = get_intervention_priority(active_cases)

        watchlist.append({
            'name': barangay,
            'active_cases': active_cases,
            'total_ytd': total_ytd,
            'status': status,
            'status_color': status_color
        })

    # Sort watchlist: Active Week Cases (descending) first, then Total YTD (descending)
    watchlist.sort(key=lambda x: (x['active_cases'], x['total_ytd']), reverse=True)

    # Age distribution calculations
    age_distribution = {
        '0-17': 0,
        '18-34': 0,
        '35-54': 0,
        '55+': 0,
    }
    for record in confirmed_records:
        if record.age is None or str(record.age).strip().lower() in NULL_LIKE_VALUES:
            age_distribution['Unavailable'] = age_distribution.get('Unavailable', 0) + 1
            continue
        try:
            age = int(record.age)
        except (TypeError, ValueError):
            age_distribution['Unavailable'] = age_distribution.get('Unavailable', 0) + 1
            continue
        bucket = '0-17' if age < 18 else '18-34' if age < 35 else '35-54' if age < 55 else '55+'
        age_distribution[bucket] += 1

    sex_distribution = {'Male': 0, 'Female': 0, 'Other / unavailable': 0}
    for record in confirmed_records:
        sex = str(record.sex or '').strip().lower()
        if sex in {'m', 'male'}:
            sex_distribution['Male'] += 1
        elif sex in {'f', 'female'}:
            sex_distribution['Female'] += 1
        else:
            sex_distribution['Other / unavailable'] += 1

    clinical_breakdown = {
        'Severe': 0,
        'Non-severe': 0,
        'Unavailable': 0,
    }
    case_breakdown = {
        'Confirmed': 0,
        'Suspected': 0,
        'Other / unavailable': 0,
    }
    for record in records_list:
        clinical_classification = str(record.clinical_classification or '').strip().lower()
        if not is_available_dashboard_value(clinical_classification):
            clinical_breakdown['Unavailable'] += 1
        elif 'severe' in clinical_classification and 'non-severe' not in clinical_classification:
            clinical_breakdown['Severe'] += 1
        else:
            clinical_breakdown['Non-severe'] += 1

        case_classification = str(record.case_classification or '').strip().lower()
        if not is_available_dashboard_value(case_classification):
            case_breakdown['Other / unavailable'] += 1
        elif 'confirmed' in case_classification:
            case_breakdown['Confirmed'] += 1
        elif 'suspected' in case_classification:
            case_breakdown['Suspected'] += 1
        else:
            case_breakdown['Other / unavailable'] += 1

    # Pagination logic
    per_page = 10
    total_clusters = len(clusters)
    total_pages = max(1, (total_clusters + per_page - 1) // per_page) if clusters else 1
    page = min(page, total_pages)
    start = (page - 1) * per_page
    end = start + per_page
    cluster_page = [
        {'name': barangay, 'count': barangay_counts[barangay]}
        for barangay in clusters[start:end]
    ]

    morbidity_week_trends = [
        {'week': week, 'count': count}
        for week, count in zip(trend['weeks'], trend['actual'])
    ]

    return render_template(
        'dashboard.html',
        user=current_user,
        total_cases=len(records_list),
        confirmed_cases=confirmed_cases,
        clusters=cluster_page,
        all_clusters=clusters,
        barangay_counts=barangay_counts,
        total_clusters=total_clusters,
        page=page,
        total_pages=total_pages,
        morbidity_week_trends=morbidity_week_trends,
        trend=trend,
        age_distribution=age_distribution,
        sex_distribution=sex_distribution,
        clinical_breakdown=clinical_breakdown,
        case_breakdown=case_breakdown,
        available_years=available_years,
        selected_year=selected_year,
        watchlist=watchlist,
        active_week=active_week,
        watchlist_period_label='YTD' if selected_year == datetime.now().year else 'Annual',
    )

# Screen 3: Data Entry, CSV Ingestion, and Offline Sync

def normalize_upload_header(value):
    text = str(value).strip().lower()
    text = text.replace('\ufeff', '')
    text = text.replace('(', '').replace(')', '').replace('-', '_').replace('/', '_')
    text = text.replace(' ', '_').replace('.', '').replace('&', 'and')
    while '__' in text:
        text = text.replace('__', '_')
    return text.strip('_')


def find_first_matching_column(columns, candidates):
    normalized_map = {normalize_upload_header(column): column for column in columns}
    for candidate in candidates:
        if candidate in normalized_map:
            return normalized_map[candidate]
    return None


def coalesce_text(value, fallback='Unavailable'):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return fallback
    text = str(value).strip()
    normalized = text.lower()
    if normalized in {'', 'nan', 'n/a', 'na', '#ref!', '#value!', 'null', 'none'}:
        return fallback
    return text or fallback


def get_yearly_data_availability(records=None):
    if records is None:
        records = DengueRecord.query.filter(DengueRecord.is_archived.is_(False)).all()

    def normalize_value(value):
        if value is None:
            return None
        if isinstance(value, float) and pd.isna(value):
            return None
        if isinstance(value, str):
            text = value.strip()
            if text == '':
                return None
            normalized = text.lower()
            if normalized in {'nan', 'n/a', 'na', 'null', 'none', 'unavailable', '#ref!', '#value!'}:
                return None
            return text
        if isinstance(value, (int, float)):
            return value
        return value

    variable_names = {
        'case_id': 'Case ID',
        'year': 'Year',
        'morbidity_month': 'Morbidity Month',
        'morbidity_week': 'Morbidity Week',
        'district': 'District',
        'barangay': 'Barangay',
        'age': 'Age',
        'sex': 'Sex',
        'clinical_classification': 'Clinical Classification',
        'case_classification': 'Case Classification',
    }

    records_by_year = {}
    for record in records:
        year = record.year
        if year is None:
            continue
        records_by_year.setdefault(int(year), []).append(record)

    summary = []
    for year in sorted(records_by_year):
        year_records = records_by_year[year]
        total_records = len(year_records)
        variable_breakdown = {}

        for attribute, label in variable_names.items():
            available = 0
            unavailable = 0
            for record in year_records:
                value = getattr(record, attribute, None)
                normalized = normalize_value(value)
                if normalized is None:
                    unavailable += 1
                else:
                    available += 1
            variable_breakdown[label] = {
                'available': available,
                'unavailable': unavailable,
            }

        summary.append({
            'year': year,
            'total_records': total_records,
            'variables': variable_breakdown,
        })
    return summary


def get_available_report_years():
    return [
        int(year_row[0])
        for year_row in db.session.query(DengueRecord.year)
        .filter(
            DengueRecord.year.isnot(None),
            DengueRecord.is_archived.is_(False),
        )
        .distinct()
        .order_by(DengueRecord.year.asc())
        .all()
    ]


def get_report_summary_for_year(year_value):
    year = int(year_value) if year_value is not None else None
    query = DengueRecord.query.filter(DengueRecord.is_archived.is_(False))
    if year is not None:
        query = query.filter(DengueRecord.year == year)
    records_list = query.all()
    total_records = len(records_list)
    month_counts = {}
    for record in records_list:
        month_value = record.morbidity_month
        if month_value not in (None, '', 'nan', 'n/a', 'na', 'null', 'none', 'unavailable'):
            month_counts[month_value] = month_counts.get(month_value, 0) + 1

    summary = {
        'year': year,
        'total_records': total_records,
        'month_counts': month_counts,
        'availability': {
            'Month': {
                'total': total_records,
                'available': sum(1 for record in records_list if record.morbidity_month is not None and str(record.morbidity_month).strip().lower() not in {'', '0', 'nan', 'n/a', 'na', 'null', 'none', 'unavailable'}),
                'unavailable': sum(1 for record in records_list if record.morbidity_month is None or str(record.morbidity_month).strip().lower() in {'', '0', 'nan', 'n/a', 'na', 'null', 'none', 'unavailable'}),
            },
            'Week': {
                'total': total_records,
                'available': sum(1 for record in records_list if record.morbidity_week is not None and str(record.morbidity_week).strip().lower() not in {'', '0', 'nan', 'n/a', 'na', 'null', 'none', 'unavailable'}),
                'unavailable': sum(1 for record in records_list if record.morbidity_week is None or str(record.morbidity_week).strip().lower() in {'', '0', 'nan', 'n/a', 'na', 'null', 'none', 'unavailable'}),
            },
            'Clinical Classification': {
                'total': total_records,
                'available': sum(1 for record in records_list if record.clinical_classification and str(record.clinical_classification).strip().lower() not in {'', 'nan', 'n/a', 'na', 'null', 'none', 'unavailable'}),
                'unavailable': sum(1 for record in records_list if not record.clinical_classification or str(record.clinical_classification).strip().lower() in {'', 'nan', 'n/a', 'na', 'null', 'none', 'unavailable'}),
            },
            'Case Classification': {
                'total': total_records,
                'available': sum(1 for record in records_list if record.case_classification and str(record.case_classification).strip().lower() not in {'', 'nan', 'n/a', 'na', 'null', 'none', 'unavailable'}),
                'unavailable': sum(1 for record in records_list if not record.case_classification or str(record.case_classification).strip().lower() in {'', 'nan', 'n/a', 'na', 'null', 'none', 'unavailable'}),
            },
            'District': {
                'total': total_records,
                'available': sum(1 for record in records_list if record.district and str(record.district).strip().lower() not in {'', 'nan', 'n/a', 'na', 'null', 'none', 'unavailable'}),
                'unavailable': sum(1 for record in records_list if not record.district or str(record.district).strip().lower() in {'', 'nan', 'n/a', 'na', 'null', 'none', 'unavailable'}),
            },
            'Barangay': {
                'total': total_records,
                'available': sum(1 for record in records_list if record.barangay and str(record.barangay).strip().lower() not in {'', 'nan', 'n/a', 'na', 'null', 'none', 'unavailable'}),
                'unavailable': sum(1 for record in records_list if not record.barangay or str(record.barangay).strip().lower() in {'', 'nan', 'n/a', 'na', 'null', 'none', 'unavailable'}),
            },
            'Age': {
                'total': total_records,
                'available': sum(1 for record in records_list if record.age is not None and str(record.age).strip().lower() not in {'', 'nan', 'n/a', 'na', 'null', 'none', 'unavailable'}),
                'unavailable': sum(1 for record in records_list if record.age is None or str(record.age).strip().lower() in {'', 'nan', 'n/a', 'na', 'null', 'none', 'unavailable'}),
            },
        },
    }
    return summary


def detect_upload_year(df=None, filename=''):
    if df is not None:
        for column in df.columns:
            candidate = normalize_upload_header(column)
            if candidate in {'year', 'morbidity_year', 'report_year', 'epidemiologic_year', 'calendar_year', 'yr'}:
                year_values = pd.to_numeric(df[column], errors='coerce').dropna()
                if not year_values.empty:
                    return int(year_values.iloc[0])

    year_match = re.search(r'(19\d{2}|20\d{2})', str(filename or ''))
    if year_match:
        return int(year_match.group(1))

    return datetime.now().year


def is_generic_case_id(value):
    if value is None:
        return True
    text = str(value).strip().lower()
    if not text:
        return True
    generic_values = {'dengue', 'a90', 'a91', 'unavailable', 'n/a', 'na', 'nan', 'null', 'none'}
    return text in generic_values or text.startswith('dengue')


def build_standardized_upload_df(df, filename=''):
    if df is None or df.empty:
        return pd.DataFrame(columns=[
            'year', 'case_id', 'morbidity_month', 'morbidity_week', 'district', 'barangay',
            'age', 'sex', 'clinical_classification', 'case_classification'
        ])

    df = df.copy()
    df = df.dropna(how='all').reset_index(drop=True)
    if df.empty:
        return pd.DataFrame(columns=[
            'year', 'case_id', 'morbidity_month', 'morbidity_week', 'district', 'barangay',
            'age', 'sex', 'clinical_classification', 'case_classification'
        ])

    upload_year = detect_upload_year(df, filename)
    normalized_columns = {normalize_upload_header(column): column for column in df.columns}
    year_candidates = ['year', 'morbidity_year', 'report_year', 'epidemiologic_year', 'calendar_year', 'yr']
    year_source = next((normalized_columns[c] for c in year_candidates if c in normalized_columns), None)
    if year_source is not None:
        df['year'] = pd.to_numeric(df[year_source], errors='coerce')
    else:
        df['year'] = upload_year

    df['year'] = df['year'].apply(lambda value: int(value) if pd.notna(value) and str(value).strip() not in {'', 'nan', 'n/a', 'na', 'null', 'none'} else upload_year)

    age_candidates = ['age_in_years', 'ageyears', 'age']
    age_source = next((normalized_columns[c] for c in age_candidates if c in normalized_columns), None)
    if age_source is not None:
        df['age'] = pd.to_numeric(df[age_source], errors='coerce')
    else:
        df['age'] = pd.Series([None] * len(df), index=df.index)

    def safe_age(value):
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return 0
        text = str(value).strip().lower()
        if text in {'', 'nan', 'n/a', 'na', 'null', 'none'}:
            return 0
        try:
            return int(float(value))
        except (TypeError, ValueError, OverflowError):
            return 0

    df['age'] = df['age'].apply(safe_age)

    case_id_source = find_first_matching_column(df.columns, ['case_code', 'case_id', 'caseid'])
    month_source = find_first_matching_column(df.columns, ['morbidity_month', 'morbiditymonth', 'month'])
    week_source = find_first_matching_column(df.columns, ['morbidity_week', 'mw'])
    district_source = find_first_matching_column(df.columns, ['district'])
    barangay_source = find_first_matching_column(df.columns, [
        'barangay', 'current_address_barangay', 'permanent_address_barangay', 'current_address_barangay2'
    ])
    sex_source = find_first_matching_column(df.columns, ['sex'])
    clinical_source = find_first_matching_column(df.columns, ['clinical_classification', 'clinclass'])
    case_classification_source = find_first_matching_column(df.columns, ['case_classification', 'caseclass'])

    def resolve_text_source(source_name, fallback='Unavailable'):
        if source_name is None or source_name not in df.columns:
            return pd.Series([fallback] * len(df), index=df.index)
        return df[source_name].apply(lambda value: coalesce_text(value, fallback))

    df['morbidity_week'] = pd.to_numeric(df[week_source], errors='coerce') if week_source else pd.Series([None] * len(df), index=df.index)
    df['morbidity_month'] = pd.to_numeric(df[month_source], errors='coerce') if month_source else pd.Series([None] * len(df), index=df.index)

    missing_month_mask = df['morbidity_month'].isna() & df['morbidity_week'].notna()
    if missing_month_mask.any():
        df.loc[missing_month_mask, 'morbidity_month'] = ((df.loc[missing_month_mask, 'morbidity_week'] - 1) // 4.33 + 1).fillna(0).astype(int)

    df['morbidity_month'] = df['morbidity_month'].apply(
        lambda value: max(1, min(12, int(value))) if pd.notna(value) else 1
    )

    df['morbidity_week'] = df['morbidity_week'].apply(
        lambda value: int(value) if pd.notna(value) else None
    )

    if case_id_source is not None:
        raw_case_ids = df[case_id_source].astype(str).str.strip()
    else:
        raw_case_ids = pd.Series([''] * len(df), index=df.index)

    generated_case_ids = []
    seen = {}
    for idx, value in enumerate(raw_case_ids):
        text = str(value).strip()
        if text and not is_generic_case_id(text):
            case_id = text
        elif text and is_generic_case_id(text):
            case_id = text.upper()
        else:
            case_id = f"{upload_year}-REC-{idx + 1:05d}"

        if case_id in seen:
            seen[case_id] += 1
            case_id = f"{case_id}-{seen[case_id]}"
        else:
            seen[case_id] = 0
        generated_case_ids.append(case_id)
    df['case_id'] = generated_case_ids

    df['district'] = resolve_text_source(district_source, fallback='Unavailable')
    df['barangay'] = resolve_text_source(barangay_source, fallback='Unavailable')
    df['sex'] = resolve_text_source(sex_source, fallback='Unavailable')
    df['clinical_classification'] = resolve_text_source(clinical_source, fallback='Unavailable')
    df['case_classification'] = resolve_text_source(case_classification_source, fallback='Unavailable')

    final_columns = [
        'year', 'case_id', 'morbidity_month', 'morbidity_week', 'district', 'barangay',
        'age', 'sex', 'clinical_classification', 'case_classification'
    ]
    df = df[final_columns].copy()

    # Drop rows that are still entirely empty after normalization so footer/blank spreadsheet rows
    # are not inserted as fake 'Unavailable' entries.
    blank_mask = df.apply(
        lambda row: row.dropna().astype(str).str.strip().str.lower().replace({'#ref!': '', '#value!': ''}).isin({'', 'nan', 'n/a', 'na', 'null', 'none', 'unavailable'}).all(),
        axis=1,
    )
    return df.loc[~blank_mask].reset_index(drop=True)


def safe_int(value, default=None):
    if value is None or pd.isna(value):
        return default

    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned or cleaned.lower() in {'#ref!', '#value!', '#n/a', 'n/a', 'na', 'nan', 'null', 'none'}:
            return default
        try:
            return int(float(cleaned))
        except (TypeError, ValueError, OverflowError):
            return default

    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def read_upload_dataframe(file, filename):
    file.seek(0)
    if filename.lower().endswith('.xlsx'):
        return pd.read_excel(file, engine='openpyxl')

    raw_bytes = file.read()
    if not raw_bytes:
        raise ValueError('The uploaded file is empty.')

    text_variants = []
    for encoding in ['utf-8-sig', 'utf-16', 'utf-16-le', 'cp1252', 'latin-1']:
        try:
            text_variants.append(raw_bytes.decode(encoding))
        except Exception:
            continue

    if not text_variants:
        text_variants.append(raw_bytes.decode('utf-8', errors='replace'))

    for text in text_variants:
        nonempty_lines = [line for line in text.replace('\r', '\n').split('\n') if line.strip()]
        if not nonempty_lines:
            continue

        candidate_seps = [',', ';', '\t', '|', ':']
        try:
            import csv
            sample = '\n'.join(nonempty_lines[:20])
            dialect = csv.Sniffer().sniff(sample, delimiters=';,\t|:')
            candidate_seps.insert(0, dialect.delimiter)
        except Exception:
            pass

        counts = {sep: sum(line.count(sep) for line in nonempty_lines[:20]) for sep in candidate_seps}
        ordered_seps = [sep for sep, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]

        for sep in dict.fromkeys(ordered_seps):
            try:
                df = pd.read_csv(
                    StringIO(text),
                    sep=sep,
                    engine='python',
                    dtype=str,
                    keep_default_na=True,
                    na_values=['', 'NA', 'N/A', 'NAN', 'NULL', 'None', 'null', 'none'],
                    skip_blank_lines=True,
                )
                if not df.empty and len(df.columns) > 1:
                    return df
            except Exception:
                continue

        try:
            df = pd.read_csv(
                StringIO(text),
                sep=None,
                engine='python',
                dtype=str,
                keep_default_na=True,
                na_values=['', 'NA', 'N/A', 'NAN', 'NULL', 'None', 'null', 'none'],
                skip_blank_lines=True,
            )
            if not df.empty and len(df.columns) > 1:
                return df
        except Exception:
            continue

    raise ValueError('Unable to parse the uploaded CSV file. Please use a standard comma, semicolon, or tab-delimited file.')


@app.route('/records', methods=['GET', 'POST'])
@login_required
def records():
    page = request.args.get('page', 1, type=int)
    page = max(page, 1)

    if request.method == 'POST':
        # Enforce RBAC permission for data creation / uploads
        if not current_user.can_create and current_user.role != 'Admin':
            flash('Access denied: Your account does not have permission to add or upload records.', 'error')
            return redirect(url_for('records'))

        if request.form.get('manual_entry'):
            try:
                case_id = request.form.get('case_id', '').strip()
                if not case_id:
                    raise ValueError('Case ID is required.')

                candidate_case_id = case_id
                duplicate_index = 1
                while DengueRecord.query.filter_by(case_id=candidate_case_id).first() is not None:
                    candidate_case_id = f'{case_id}-{duplicate_index}'
                    duplicate_index += 1

                record = DengueRecord(
                    case_id=candidate_case_id,
                    year=safe_int(request.form.get('year'), datetime.now().year),
                    district=request.form.get('district', '').strip() or 'Talomo',
                    morbidity_month=safe_int(request.form.get('morbidity_month'), 1),
                    morbidity_week=safe_int(request.form.get('morbidity_week')),
                    barangay=request.form.get('barangay', '').strip() or 'Unavailable',
                    age=safe_int(request.form.get('age'), 0),
                    sex=request.form.get('sex', '').strip() or 'Unavailable',
                    clinical_classification=request.form.get('clinical_classification') or 'Unavailable',
                    case_classification=request.form.get('case_classification') or 'Unavailable',
                    sync_status='Local'
                )
                db.session.add(record)
                db.session.flush()
                db.session.add(RecordAuditLog(
                    record_id=record.id,
                    username=current_user.username,
                    action_type='CREATE',
                    field_changed='record',
                    old_value=None,
                    new_value=record.case_id,
                ))
                db.session.commit()
                flash('Dengue case saved successfully as a new manually logged record.', 'success')
                page = 1
            except Exception as e:
                db.session.rollback()
                flash(f'Could not save record: {str(e)}', 'error')

        else:
            file = request.files.get('file')
            if file is not None:
                filename = (file.filename or '').strip()
                if filename.lower().endswith(('.csv', '.xlsx')):
                    try:
                        df = read_upload_dataframe(file, filename)

                        if df.empty:
                            raise ValueError('The uploaded file contains no data rows.')

                        df = build_standardized_upload_df(df, filename=filename)

                        with db.session.no_autoflush:
                            existing_case_ids = {
                                case_id for (case_id,) in db.session.query(DengueRecord.case_id).all()
                            }

                        # Loop through rows and insert into database
                        added_count = 0
                        imported_records = []
                        for _, row in df.iterrows():
                            if row.isnull().all():
                                continue

                            case_id_value = row.get('case_id')
                            case_id_val = '' if pd.isna(case_id_value) else str(case_id_value).strip()
                            if not case_id_val or case_id_val.upper() in {'#REF!', '#VALUE!', '#N/A', 'N/A', 'NAN', 'NULL', 'UNAVAILABLE'}:
                                case_id_val = f"{datetime.now().year}-REC-{added_count + 1000:05d}"

                            candidate_case_id = case_id_val
                            duplicate_index = 1
                            while candidate_case_id in existing_case_ids:
                                candidate_case_id = f"{case_id_val}-{duplicate_index}"
                                duplicate_index += 1

                            record = DengueRecord(
                                case_id=candidate_case_id,
                                year=safe_int(row.get('year'), datetime.now().year),
                                morbidity_month=safe_int(row.get('morbidity_month'), 1),
                                morbidity_week=safe_int(row.get('morbidity_week')),
                                district=str(row.get('district') or 'Unavailable').strip() or 'Unavailable',
                                barangay=str(row.get('barangay') or 'Unavailable').strip() or 'Unavailable',
                                age=safe_int(row.get('age'), 0),
                                sex=str(row.get('sex') or 'Unavailable').strip() or 'Unavailable',
                                clinical_classification=str(row.get('clinical_classification') or 'Unavailable').strip() or 'Unavailable',
                                case_classification=str(row.get('case_classification') or 'Unavailable').strip() or 'Unavailable',
                                sync_status='Synced'
                            )
                            db.session.add(record)
                            imported_records.append(record)
                            existing_case_ids.add(candidate_case_id)
                            added_count += 1

                        db.session.flush()
                        db.session.add_all([
                            RecordAuditLog(
                                record_id=record.id,
                                username=current_user.username,
                                action_type='CREATE',
                                field_changed='record',
                                old_value=None,
                                new_value=record.case_id,
                            )
                            for record in imported_records
                        ])
                        db.session.commit()
                        flash(f'Successfully imported {added_count} new records from {filename}!', 'info')
                        page = 1
                    except Exception as e:
                        db.session.rollback()
                        flash(f'Error processing file: {str(e)}', 'error')
                else:
                    flash('Choose an Excel (.xlsx) or CSV (.csv) file before uploading.', 'error')

    records_page = DengueRecord.query.order_by(DengueRecord.id.desc()).paginate(page=page, per_page=10, error_out=False)
    return render_template('records.html', user=current_user, records=records_page)

# Screen 4: View all ingested dengue surveillance data
REPOSITORY_FILTER_DEFAULTS = {
    'year': 'all',
    'search': '',
    'district': '',
    'barangay': '',
    'morbidity_month': '',
    'morbidity_week': '',
    'case_classification': '',
    'clinical_classification': '',
}


def get_repository_filters(source):
    filters = {
        key: str(source.get(key, default) or default).strip()
        for key, default in REPOSITORY_FILTER_DEFAULTS.items()
    }
    if filters['year'].lower() != 'all':
        try:
            filters['year'] = str(int(filters['year']))
        except ValueError:
            filters['year'] = 'all'

    for key, minimum, maximum in (
        ('morbidity_month', 1, 12),
        ('morbidity_week', 1, 52),
    ):
        try:
            value = int(filters[key]) if filters[key] else None
        except ValueError:
            value = None
        filters[key] = str(value) if value is not None and minimum <= value <= maximum else ''

    if filters['case_classification'].lower() not in {'confirmed', 'suspected'}:
        filters['case_classification'] = ''
    if filters['clinical_classification'].lower() not in {'severe', 'non-severe'}:
        filters['clinical_classification'] = ''
    return filters


def apply_repository_filters(query, filters):
    if filters['year'] != 'all':
        query = query.filter(DengueRecord.year == int(filters['year']))
    if filters['search']:
        search_term = f"%{filters['search']}%"
        query = query.filter(or_(
            DengueRecord.case_id.ilike(search_term),
            DengueRecord.barangay.ilike(search_term),
        ))
    if filters['district']:
        query = query.filter(DengueRecord.district == filters['district'])
    if filters['barangay']:
        query = query.filter(DengueRecord.barangay.ilike(f"%{filters['barangay']}%"))
    if filters['morbidity_month']:
        query = query.filter(DengueRecord.morbidity_month == int(filters['morbidity_month']))
    if filters['morbidity_week']:
        query = query.filter(DengueRecord.morbidity_week == int(filters['morbidity_week']))
    if filters['case_classification']:
        query = query.filter(DengueRecord.case_classification.ilike(
            f"%{filters['case_classification']}%"
        ))
    if filters['clinical_classification'] == 'Severe':
        query = query.filter(
            DengueRecord.clinical_classification.ilike('%severe%'),
            ~DengueRecord.clinical_classification.ilike('%non-severe%'),
        )
    elif filters['clinical_classification'] == 'Non-severe':
        query = query.filter(
            ~DengueRecord.clinical_classification.ilike('%severe%')
            | DengueRecord.clinical_classification.is_(None)
        )
    return query


def get_repository_filter_options():
    available_years = [
        int(year_row[0])
        for year_row in db.session.query(DengueRecord.year)
        .filter(
            DengueRecord.is_archived.is_(False),
            DengueRecord.year.isnot(None),
        )
        .distinct()
        .order_by(DengueRecord.year.asc())
        .all()
    ]
    districts = [
        district[0]
        for district in db.session.query(DengueRecord.district)
        .filter(
            DengueRecord.is_archived.is_(False),
            DengueRecord.district.isnot(None),
            DengueRecord.district != '',
        )
        .distinct()
        .order_by(DengueRecord.district.asc())
        .all()
    ]
    return available_years, districts


def repository_year_options(available_years):
    return ['all'] + [
        str(year) for year in sorted(set(available_years), reverse=True)
    ]


def get_yearly_archive_summary():
    year_rows = db.session.query(
        DengueRecord.year,
        db.func.count(DengueRecord.id),
        db.func.sum(case((DengueRecord.is_archived.is_(True), 1), else_=0)),
    ).filter(
        DengueRecord.year.isnot(None)
    ).group_by(DengueRecord.year).order_by(DengueRecord.year.desc()).all()
    return [
        {
            'year': int(year),
            'record_count': int(record_count),
            'archived_count': int(archived_count or 0),
            'is_archived': int(archived_count or 0) == int(record_count),
            'is_mixed': 0 < int(archived_count or 0) < int(record_count),
            'active_count': int(record_count) - int(archived_count or 0),
        }
        for year, record_count, archived_count in year_rows
        if int(record_count) - int(archived_count or 0) > 0
    ]


@app.route('/repository')
@login_required
def repository():
    filters = get_repository_filters(request.args)
    page = max(request.args.get('page', 1, type=int), 1)
    available_years, districts = get_repository_filter_options()
    if filters['year'] != 'all' and int(filters['year']) not in available_years:
        filters['year'] = 'all'
    query = apply_repository_filters(
        DengueRecord.query.filter(DengueRecord.is_archived.is_(False)),
        filters,
    )
    records_page = query.order_by(DengueRecord.id.desc()).paginate(
        page=page, per_page=50, error_out=False
    )
    return render_template(
        'data_repository.html',
        user=current_user,
        records=records_page,
        filters=filters,
        year=filters['year'],
        search=filters['search'],
        available_years=repository_year_options(available_years),
        districts=districts,
        months=range(1, 13),
        weeks=range(1, 53),
        yearly_availability_summary=get_yearly_archive_summary(),
    )


@app.route('/archive')
@login_required
def archive():
    filters = get_repository_filters(request.args)
    page = max(request.args.get('page', 1, type=int), 1)
    available_years = [
        int(year_row[0])
        for year_row in db.session.query(DengueRecord.year)
        .filter(DengueRecord.is_archived.is_(True), DengueRecord.year.isnot(None))
        .distinct()
        .order_by(DengueRecord.year.asc())
        .all()
    ]
    districts = [
        district[0]
        for district in db.session.query(DengueRecord.district)
        .filter(
            DengueRecord.is_archived.is_(True),
            DengueRecord.district.isnot(None),
            DengueRecord.district != '',
        )
        .distinct()
        .order_by(DengueRecord.district.asc())
        .all()
    ]
    query = apply_repository_filters(
        DengueRecord.query.filter(DengueRecord.is_archived.is_(True)),
        filters,
    )
    archive_data_query = DengueRecord.query.filter(DengueRecord.is_archived.is_(True))
    if filters['year'] != 'all':
        archive_data_query = archive_data_query.filter(
            DengueRecord.year == int(filters['year'])
        )
    records_page = query.order_by(DengueRecord.id.desc()).paginate(
        page=page, per_page=50, error_out=False
    )
    return render_template(
        'archive.html',
        user=current_user,
        records=records_page,
        filters=filters,
        year=filters['year'],
        search=filters['search'],
        available_years=repository_year_options(available_years),
        districts=districts,
        months=range(1, 13),
        weeks=range(1, 53),
        has_archived_data=archive_data_query.count() > 0,
    )


def get_archived_case_export_records(year):
    query = DengueRecord.query.filter(DengueRecord.is_archived.is_(True))
    if year is not None:
        query = query.filter(DengueRecord.year == year)
    return query.order_by(DengueRecord.id).all()


def get_case_export_rows(records):
    return [
        {
            'year': record.year,
            'case_id': record.case_id,
            'morbidity_month': record.morbidity_month,
            'morbidity_week': record.morbidity_week,
            'district': record.district,
            'barangay': record.barangay,
            'age': record.age,
            'sex': record.sex,
            'clinical_classification': record.clinical_classification,
            'case_classification': record.case_classification,
        }
        for record in records
    ]


@app.route('/archive/cases.csv')
@login_required
def download_archived_cases_csv():
    selected_year = request.args.get('year', type=int)
    records = get_archived_case_export_records(selected_year)
    csv_buffer = StringIO()
    pd.DataFrame(get_case_export_rows(records)).to_csv(csv_buffer, index=False)
    csv_file = BytesIO(csv_buffer.getvalue().encode('utf-8'))
    csv_file.seek(0)
    filename = (
        f'lokalhealth-archived-case-data-{selected_year}.csv'
        if selected_year is not None
        else 'lokalhealth-archived-case-data-all-years.csv'
    )
    return send_file(csv_file, mimetype='text/csv', as_attachment=True, download_name=filename)


@app.route('/archive/cases.xlsx')
@login_required
def download_archived_cases_excel():
    selected_year = request.args.get('year', type=int)
    records = get_archived_case_export_records(selected_year)
    excel_buffer = BytesIO()
    pd.DataFrame(get_case_export_rows(records)).to_excel(
        excel_buffer, index=False, engine='openpyxl'
    )
    excel_buffer.seek(0)
    filename = (
        f'lokalhealth-archived-case-data-{selected_year}.xlsx'
        if selected_year is not None
        else 'lokalhealth-archived-case-data-all-years.xlsx'
    )
    return send_file(
        excel_buffer,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=filename,
    )


@app.route('/repository/toggle-archive/<int:year>', methods=['POST'])
@login_required
def archive_year(year):
    if current_user.role != 'Admin' and not current_user.can_delete:
        flash('Access denied: Archive permission is required.', 'error')
        return redirect(url_for('repository'))

    year_records = DengueRecord.query.filter_by(year=year).all()
    if not year_records:
        flash(f'No records found for {year}.', 'error')
        return redirect(url_for('repository'))

    active_records = [record for record in year_records if not record.is_archived]
    if not active_records:
        flash(f'All {year} records are already archived.', 'info')
        return redirect(url_for('repository', year=year))

    for record in active_records:
        previous_value = record.is_archived
        record.is_archived = True
        db.session.add(RecordAuditLog(
            record_id=record.id,
            username=current_user.username,
            action_type='ARCHIVE',
            field_changed='is_archived',
            old_value=str(previous_value),
            new_value='True',
        ))
    db.session.commit()
    flash(f'All {year} records have been archived.', 'success')
    filters = get_repository_filters(request.args)
    filters['year'] = str(year)
    return redirect(url_for('repository', **filters))


@app.route('/records/edit/<int:record_id>', methods=['GET', 'POST'])
@login_required
def edit_record(record_id):
    if current_user.role != 'Admin' and not current_user.can_edit:
        flash('Access denied: Your account does not have permission to edit records.', 'error')
        return redirect(url_for('repository'))

    record = db.session.get(DengueRecord, record_id)
    if record is None:
        abort(404)
    if record.is_archived and current_user.role != 'Admin':
        flash('Archived records can only be edited by an administrator.', 'error')
        return redirect(url_for('repository', year=record.year))

    if request.method == 'POST':
        try:
            month = int(request.form.get('morbidity_month', ''))
            if not 1 <= month <= 12:
                raise ValueError('Morbidity month must be between 1 and 12.')
            week_value = request.form.get('morbidity_week', '').strip()
            week = int(week_value) if week_value else None
            if week is not None and not 1 <= week <= 52:
                raise ValueError('Morbidity week must be between 1 and 52.')
            age = int(request.form.get('age', ''))
            if age < 0:
                raise ValueError('Age cannot be negative.')

            updates = {
                'morbidity_month': month,
                'morbidity_week': week,
                'district': request.form.get('district', '').strip(),
                'barangay': request.form.get('barangay', '').strip(),
                'age': age,
                'sex': request.form.get('sex', '').strip(),
                'clinical_classification': request.form.get('clinical_classification', '').strip(),
                'case_classification': request.form.get('case_classification', '').strip(),
            }
            for field in ('district', 'barangay', 'sex', 'clinical_classification', 'case_classification'):
                if not updates[field]:
                    raise ValueError(f'{field.replace("_", " ").capitalize()} is required.')
            if len(updates['district']) > 100 or len(updates['barangay']) > 100:
                raise ValueError('District and barangay values must be 100 characters or fewer.')
            if len(updates['sex']) > 10:
                raise ValueError('Sex must be 10 characters or fewer.')
            if len(updates['clinical_classification']) > 100 or len(updates['case_classification']) > 100:
                raise ValueError('Classification values must be 100 characters or fewer.')

            audit_entries = []
            for field, new_value in updates.items():
                old_value = getattr(record, field)
                if old_value != new_value:
                    audit_entries.append(RecordAuditLog(
                        record_id=record.id,
                        username=current_user.username,
                        action_type='EDIT',
                        field_changed=field,
                        old_value=None if old_value is None else str(old_value),
                        new_value=None if new_value is None else str(new_value),
                    ))
                    setattr(record, field, new_value)

            if audit_entries:
                db.session.add_all(audit_entries)
            db.session.commit()
            flash('Record updated successfully.', 'success')
            return redirect(url_for('repository', year=record.year))
        except (TypeError, ValueError) as error:
            db.session.rollback()
            flash(str(error), 'error')
            record = db.session.get(DengueRecord, record_id)
        except SQLAlchemyError:
            db.session.rollback()
            flash('Could not save the record changes. No updates were applied.', 'error')
            record = db.session.get(DengueRecord, record_id)

    return render_template('record_edit.html', user=current_user, record=record)

# Screen 4: Automated PDF/CSV Export Hub
@app.route('/reports')
@login_required
def reports():
    available_years = get_available_report_years()
    selected_year = request.args.get('year', type=int)
    if selected_year is None and available_years:
        selected_year = max(available_years)

    if selected_year is not None and selected_year not in available_years:
        selected_year = None

    yearly_availability_summary = get_yearly_data_availability()
    summary_for_year = get_report_summary_for_year(selected_year) if selected_year is not None else None
    year_records = DengueRecord.query.filter(
        DengueRecord.year == selected_year,
        DengueRecord.is_archived.is_(False),
    ).order_by(DengueRecord.id.asc()).all() if selected_year is not None else []
    has_data = bool(summary_for_year and summary_for_year['total_records'] > 0)

    return render_template(
        'reports.html',
        user=current_user,
        available_years=available_years,
        selected_year=selected_year,
        yearly_availability_summary=yearly_availability_summary,
        summary_for_year=summary_for_year,
        year_records=year_records,
        has_data=has_data,
    )

@app.route('/reports/summary.pdf')
@login_required
def download_summary_pdf():
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    requested_year = request.args.get('year', type=int)
    available_years = get_available_report_years()
    selected_year = requested_year
    if selected_year is None:
        selected_year = max(available_years) if available_years else None

    records_query = DengueRecord.query.filter(DengueRecord.is_archived.is_(False))
    if selected_year is not None:
        records_query = records_query.filter(DengueRecord.year == selected_year)
    records_list = (
        records_query.order_by(DengueRecord.morbidity_month).all()
        if selected_year is None or selected_year in available_years
        else []
    )
    if not records_list:
        pdf_buffer = BytesIO()
        document = canvas.Canvas(pdf_buffer, pagesize=letter)
        document.setTitle('LokalHealth Monthly Epidemiological Summary')
        document.setFont('Helvetica-Bold', 12)
        document.drawString(72, 740, 'No data available for the selected year')
        document.save()
        pdf_buffer.seek(0)
        return send_file(pdf_buffer, mimetype='application/pdf', as_attachment=True, download_name='lokalhealth-monthly-summary.pdf')

    summary = get_report_summary_for_year(selected_year)
    month_counts = {}
    for record in records_list:
        month_counts[record.morbidity_month] = month_counts.get(record.morbidity_month, 0) + 1

    pdf_buffer = BytesIO()
    document = canvas.Canvas(pdf_buffer, pagesize=letter)
    document.setTitle('LokalHealth Monthly Epidemiological Summary')
    document.setFont('Helvetica-Bold', 16)
    document.drawString(72, 740, 'LokalHealth Monthly Epidemiological Summary')
    document.setFont('Helvetica', 10)
    document.drawString(72, 720, f'Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    document.drawString(72, 700, f'Year: {selected_year}')
    document.drawString(72, 688, f'Total anonymized cases: {len(records_list)}')
    document.drawString(72, 676, 'Seasonal breakdown (Morbidity Month):')

    y_position = 660
    for month, count in sorted(month_counts.items()):
        document.drawString(90, y_position, f'Month {month}: {count} case(s)')
        y_position -= 18
        if y_position < 72:
            document.showPage()
            y_position = 740

    document.drawString(72, max(y_position - 24, 110), 'Data availability and completeness summary:')
    y_position = max(y_position - 40, 92)
    document.drawString(90, y_position, 'Variable')
    document.drawString(250, y_position, 'Total')
    document.drawString(315, y_position, 'Available')
    document.drawString(390, y_position, 'Unavailable')
    y_position -= 16
    for label, values in summary['availability'].items():
        document.drawString(90, y_position, label)
        document.drawString(250, y_position, str(values['total']))
        document.drawString(315, y_position, str(values['available']))
        document.drawString(390, y_position, str(values['unavailable']))
        y_position -= 16
        if y_position < 72:
            document.showPage()
            y_position = 740

    document.save()
    pdf_buffer.seek(0)
    return send_file(pdf_buffer, mimetype='application/pdf', as_attachment=True,
                     download_name=f'localkhealth-summary-{selected_year}.pdf' if selected_year is not None else 'lokalhealth-monthly-summary.pdf')

@app.route('/reports/summary.csv')
@login_required
def download_summary_csv():
    selected_year = request.args.get('year', type=int)
    available_years = get_available_report_years()
    if selected_year is None:
        selected_year = max(available_years) if available_years else None
    if selected_year not in available_years:
        selected_year = None

    summary = get_report_summary_for_year(selected_year) if selected_year is not None else {'year': None, 'total_records': 0, 'availability': {}}
    rows = []
    for label, values in summary['availability'].items():
        rows.append({
            'year': summary['year'],
            'variable': label,
            'total': values['total'],
            'available': values['available'],
            'unavailable': values['unavailable'],
        })

    csv_buffer = StringIO()
    pd.DataFrame(rows).to_csv(csv_buffer, index=False)
    csv_file = BytesIO(csv_buffer.getvalue().encode('utf-8'))
    csv_file.seek(0)
    return send_file(csv_file, mimetype='text/csv', as_attachment=True,
                     download_name=f'lokalhealth-summary-{selected_year}.csv' if selected_year is not None else 'lokalhealth-summary.csv')

@app.route('/reports/cases.csv')
@login_required
def download_cases_csv():
    selected_year = request.args.get('year', type=int)
    query = DengueRecord.query.filter(
        DengueRecord.is_archived.is_(False)
    ).order_by(DengueRecord.id)
    if selected_year is not None:
        query = query.filter(DengueRecord.year == selected_year)
    records_list = query.all()
    csv_buffer = StringIO()
    pd.DataFrame([
        {
            'year': record.year,
            'case_id': record.case_id,
            'morbidity_month': record.morbidity_month,
            'morbidity_week': record.morbidity_week,
            'district': record.district,
            'barangay': record.barangay,
            'age': record.age,
            'sex': record.sex,
            'clinical_classification': record.clinical_classification,
            'case_classification': record.case_classification,
        }
        for record in records_list
    ]).to_csv(csv_buffer, index=False)
    csv_file = BytesIO(csv_buffer.getvalue().encode('utf-8'))
    csv_file.seek(0)
    return send_file(csv_file, mimetype='text/csv', as_attachment=True,
                     download_name=f'lokalhealth-anonymized-case-data-{selected_year}.csv' if selected_year is not None else 'lokalhealth-anonymized-case-data.csv')

@app.route('/reports/cases.xlsx')
@login_required
def download_cases_excel():
    selected_year = request.args.get('year', type=int)
    query = DengueRecord.query.filter(
        DengueRecord.is_archived.is_(False)
    ).order_by(DengueRecord.id)
    if selected_year is not None:
        query = query.filter(DengueRecord.year == selected_year)
    records_list = query.all()
    excel_buffer = BytesIO()
    pd.DataFrame([
        {
            'year': record.year,
            'case_id': record.case_id,
            'morbidity_month': record.morbidity_month,
            'morbidity_week': record.morbidity_week,
            'district': record.district,
            'barangay': record.barangay,
            'age': record.age,
            'sex': record.sex,
            'clinical_classification': record.clinical_classification,
            'case_classification': record.case_classification,
        }
        for record in records_list
    ]).to_excel(excel_buffer, index=False, engine='openpyxl')
    excel_buffer.seek(0)
    return send_file(excel_buffer,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True, download_name=f'lokalhealth-anonymized-case-data-{selected_year}.xlsx' if selected_year is not None else 'lokalhealth-anonymized-case-data.xlsx')

# Screen 5: Admin Settings & Role Assignment (Restricted to Admin Role)
@app.route('/admin/users/create', methods=['POST'])
@login_required
def create_user():
    if current_user.role != 'Admin':
        flash('Unauthorized access: Admin permissions required.', 'error')
        return redirect(url_for('dashboard'))

    username = request.form.get('username', '').strip()
    role = request.form.get('role', 'Viewer')

    if not username:
        flash('Username is required.', 'error')
        return redirect(url_for('admin'))
    if role not in {'Admin', 'BHW', 'Viewer'}:
        flash('Invalid user role selected.', 'error')
        return redirect(url_for('admin'))
    if User.query.filter_by(username=username).first():
        flash('Username is already taken.', 'error')
        return redirect(url_for('admin'))

    new_user = User(
        username=username,
        role=role,
        can_create=request.form.get('can_create') == 'on',
        can_edit=request.form.get('can_edit') == 'on',
        can_delete=request.form.get('can_delete') == 'on',
        must_change_password=True,
    )
    temporary_password = secrets.token_urlsafe(10)
    new_user.set_password(temporary_password)
    db.session.add(new_user)
    db.session.commit()
    flash(f'Account created for {username}. Temporary password: {temporary_password}', 'info')
    return redirect(url_for('admin'))

@app.route('/admin/reset_password/<int:user_id>', methods=['POST'])
@login_required
def reset_password(user_id):
    if current_user.role != 'Admin':
        flash('Unauthorized access: Admin permissions required.', 'error')
        return redirect(url_for('dashboard'))

    user_item = db.session.get(User, user_id)
    if user_item is None:
        flash('User account not found.', 'error')
        return redirect(url_for('admin'))

    temporary_password = secrets.token_urlsafe(10)
    user_item.password_hash = generate_password_hash(temporary_password)
    user_item.must_change_password = True
    db.session.commit()
    flash(f'Password reset for {user_item.username}. Temporary password: {temporary_password}', 'info')
    return redirect(url_for('admin'))

@app.route('/admin/users/<int:user_id>/toggle-block', methods=['POST'])
@login_required
def toggle_block(user_id):
    if current_user.role != 'Admin':
        flash('Unauthorized access: Admin permissions required.', 'error')
        return redirect(url_for('dashboard'))

    user_item = db.session.get(User, user_id)
    if user_item is None:
        flash('User account not found.', 'error')
        return redirect(url_for('admin'))
    if user_item.id == current_user.id:
        flash('You cannot block your own administrator account.', 'error')
        return redirect(url_for('admin'))

    user_item.is_blocked = not user_item.is_blocked
    db.session.commit()
    status = 'blocked' if user_item.is_blocked else 'unblocked'
    flash(f'{user_item.username} has been {status}.', 'info')
    return redirect(url_for('admin'))

@app.route('/admin/users/<int:user_id>/permissions', methods=['POST'])
@login_required
def update_permissions(user_id):
    if current_user.role != 'Admin':
        flash('Unauthorized access: Admin permissions required.', 'error')
        return redirect(url_for('dashboard'))

    user_item = db.session.get(User, user_id)
    if user_item is None:
        flash('User account not found.', 'error')
        return redirect(url_for('admin'))

    user_item.can_create = request.form.get('can_create') == 'on'
    user_item.can_edit = request.form.get('can_edit') == 'on'
    user_item.can_delete = request.form.get('can_delete') == 'on'
    db.session.commit()
    flash(f'Permissions updated for {user_item.username}.', 'info')
    return redirect(url_for('admin'))

@app.route('/admin')
@login_required
def admin():
    if current_user.role != 'Admin':
        flash('Unauthorized access: Admin permissions required.', 'error')
        return redirect(url_for('dashboard'))
    users = User.query.all()
    action_filter = request.args.get('action', '').strip().upper()
    if action_filter not in {'CREATE', 'EDIT', 'ARCHIVE'}:
        action_filter = ''
    audit_search = request.args.get('search', '').strip()
    audit_query = RecordAuditLog.query
    if action_filter:
        audit_query = audit_query.filter(RecordAuditLog.action_type == action_filter)
    if audit_search:
        search_pattern = f'%{audit_search}%'
        audit_query = audit_query.filter(db.or_(
            RecordAuditLog.username.ilike(search_pattern),
            RecordAuditLog.action_type.ilike(search_pattern),
            RecordAuditLog.field_changed.ilike(search_pattern),
            db.cast(RecordAuditLog.record_id, db.String).ilike(search_pattern),
        ))
    audit_logs = audit_query.order_by(
        RecordAuditLog.timestamp.desc(),
        RecordAuditLog.id.desc(),
    ).paginate(
        page=max(request.args.get('page', 1, type=int), 1),
        per_page=15,
        error_out=False,
    )
    return render_template(
        'admin.html',
        user=current_user,
        users=users,
        audit_logs=audit_logs,
        audit_action=action_filter,
        audit_search=audit_search,
    )

# ==========================================
# DATABASE INITIALIZATION & SEEDING
# ==========================================

def init_db():
    with app.app_context():
        db.create_all()
        migrate_user_table()
        migrate_dengue_record_table()
        ensure_primary_admin_account()
        print("Database initialized and default Admin account ensured.")

def migrate_dengue_record_table():
    inspector = db.inspect(db.engine)
    if not inspector.has_table('dengue_record'):
        DengueRecord.__table__.create(db.engine, checkfirst=True)

    existing_columns = {
        column['name'] for column in db.inspect(db.engine).get_columns('dengue_record')
    }

    if 'year' not in existing_columns:
        with db.engine.begin() as connection:
            connection.execute(text('ALTER TABLE dengue_record ADD COLUMN year INTEGER'))
        existing_columns.add('year')

    if 'is_archived' not in existing_columns:
        with db.engine.begin() as connection:
            connection.execute(
                text('ALTER TABLE dengue_record ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT 0')
            )

    # Backfill year values from current year for legacy rows without explicit data.
    with db.engine.begin() as connection:
        connection.execute(text('UPDATE dengue_record SET year = :year WHERE year IS NULL'), {'year': datetime.now().year})
    RecordAuditLog.__table__.create(db.engine, checkfirst=True)

def migrate_user_table():
    existing_columns = {
        column['name'] for column in db.inspect(db.engine).get_columns('user')
    }

    if 'assigned_barangay' in existing_columns:
        with db.engine.begin() as connection:
            connection.execute(text('ALTER TABLE "user" RENAME TO user_legacy'))
            connection.execute(text('''
                CREATE TABLE "user" (
                    id INTEGER NOT NULL PRIMARY KEY,
                    username VARCHAR(80) NOT NULL UNIQUE,
                    password_hash VARCHAR(120) NOT NULL,
                    role VARCHAR(20),
                    can_create BOOLEAN DEFAULT 0,
                    can_edit BOOLEAN DEFAULT 0,
                    can_delete BOOLEAN DEFAULT 0,
                    is_blocked BOOLEAN DEFAULT 0
                )
            '''))
            connection.execute(text('''
                INSERT INTO "user" (id, username, password_hash, role, can_create, can_edit, can_delete, is_blocked)
                SELECT id, username, password_hash, role, can_create, can_edit, can_delete, is_blocked
                FROM user_legacy
            '''))
            connection.execute(text('DROP TABLE user_legacy'))

    permission_columns = {
        'can_create': 'BOOLEAN DEFAULT 0',
        'can_edit': 'BOOLEAN DEFAULT 0',
        'can_delete': 'BOOLEAN DEFAULT 0',
        'is_blocked': 'BOOLEAN DEFAULT 0',
        'must_change_password': 'BOOLEAN DEFAULT 0',
    }

    with db.engine.begin() as connection:
        for column_name, column_definition in permission_columns.items():
            if column_name not in existing_columns and column_name not in {column['name'] for column in db.inspect(db.engine).get_columns('user')}:
                connection.execute(text(
                    f'ALTER TABLE "user" ADD COLUMN {column_name} {column_definition}'
                ))

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)