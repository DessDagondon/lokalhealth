import os
import re
from io import BytesIO, StringIO
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from sqlalchemy import text, or_
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'lokalhealth-epidemiological-secret-key-2026'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'


@app.before_request
def ensure_admin_on_every_request():
    ensure_primary_admin_account()


# ==========================================
# DATABASE SCHEMAS (RA 10173 & RBAC ALIGNED)
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
            return redirect(url_for('dashboard'))
        elif not user or not user.check_password(password):
            flash('Invalid username or password. Please try again.', 'error')
        
    return render_template('login.html')

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
@app.route('/dashboard')
@login_required
def dashboard():
    page = request.args.get('page', 1, type=int)
    page = max(page, 1)

    records_list = DengueRecord.query.all()
    confirmed_cases = DengueRecord.query.filter(
        db.func.lower(DengueRecord.case_classification).contains('confirmed')
    ).count()
    clusters = sorted({record.barangay for record in records_list if record.barangay})
    per_page = 10
    total_clusters = len(clusters)
    total_pages = max(1, (total_clusters + per_page - 1) // per_page) if clusters else 1
    page = min(page, total_pages)
    start = (page - 1) * per_page
    end = start + per_page
    cluster_page = clusters[start:end]

    week_counts = db.session.query(
        DengueRecord.morbidity_week,
        db.func.count(DengueRecord.id)
    ).filter(DengueRecord.morbidity_week.isnot(None)).group_by(DengueRecord.morbidity_week).all()
    week_counts_map = {int(week): count for week, count in week_counts if week is not None and 1 <= int(week) <= 52}
    morbidity_week_trends = [
        {'week': week, 'count': week_counts_map.get(week, 0)}
        for week in range(1, 53)
    ]

    return render_template(
        'dashboard.html',
        user=current_user,
        total_cases=len(records_list),
        confirmed_cases=confirmed_cases,
        clusters=cluster_page,
        all_clusters=clusters,
        total_clusters=total_clusters,
        page=page,
        total_pages=total_pages,
        morbidity_week_trends=morbidity_week_trends,
    )

# Screen 3: Data Entry, CSV Ingestion, and Offline Sync
import pandas as pd


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
        records = DengueRecord.query.all()

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
            if value == 0:
                return None
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
        .filter(DengueRecord.year.isnot(None))
        .distinct()
        .order_by(DengueRecord.year.asc())
        .all()
    ]


def get_report_summary_for_year(year_value):
    year = int(year_value) if year_value is not None else None
    records_list = DengueRecord.query.filter(DengueRecord.year == year).all() if year is not None else DengueRecord.query.all()
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
            'Classification': {
                'total': total_records,
                'available': sum(1 for record in records_list if record.clinical_classification and str(record.clinical_classification).strip().lower() not in {'', 'nan', 'n/a', 'na', 'null', 'none', 'unavailable'}),
                'unavailable': sum(1 for record in records_list if not record.clinical_classification or str(record.clinical_classification).strip().lower() in {'', 'nan', 'n/a', 'na', 'null', 'none', 'unavailable'}),
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
                'available': sum(1 for record in records_list if record.age is not None and str(record.age).strip().lower() not in {'', '0', '0.0', 'nan', 'n/a', 'na', 'null', 'none', 'unavailable'}),
                'unavailable': sum(1 for record in records_list if record.age is None or str(record.age).strip().lower() in {'', '0', '0.0', 'nan', 'n/a', 'na', 'null', 'none', 'unavailable'}),
            },
        },
    }
    return summary


def estimate_morbidity_week(month_value):
    month = safe_int(month_value, default=None)
    if month is None:
        return None
    if month < 1:
        return None
    return max(1, min(52, (month * 4) - 2))


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
            'age', 'sex', 'clinical_classification'
        ])

    df = df.copy()
    df = df.dropna(how='all').reset_index(drop=True)
    if df.empty:
        return pd.DataFrame(columns=[
            'year', 'case_id', 'morbidity_month', 'morbidity_week', 'district', 'barangay',
            'age', 'sex', 'clinical_classification'
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

    missing_week_mask = df['morbidity_week'].isna() & df['morbidity_month'].notna()
    if missing_week_mask.any():
        df.loc[missing_week_mask, 'morbidity_week'] = df.loc[missing_week_mask, 'morbidity_month'].apply(estimate_morbidity_week)

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

    final_columns = [
        'year', 'case_id', 'morbidity_month', 'morbidity_week', 'district', 'barangay',
        'age', 'sex', 'clinical_classification'
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
                    sync_status='Local'
                )
                db.session.add(record)
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
                                morbidity_week=safe_int(row.get('morbidity_week')) if safe_int(row.get('morbidity_week')) is not None else estimate_morbidity_week(row.get('morbidity_month')),
                                district=str(row.get('district') or 'Unavailable').strip() or 'Unavailable',
                                barangay=str(row.get('barangay') or 'Unavailable').strip() or 'Unavailable',
                                age=safe_int(row.get('age'), 0),
                                sex=str(row.get('sex') or 'Unavailable').strip() or 'Unavailable',
                                clinical_classification=str(row.get('clinical_classification') or 'Unavailable').strip() or 'Unavailable',
                                sync_status='Synced'
                            )
                            db.session.add(record)
                            existing_case_ids.add(candidate_case_id)
                            added_count += 1

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
@app.route('/repository')
@login_required
def repository():
    year_filter = request.args.get('year', 'all', type=str)
    search = request.args.get('search', '', type=str).strip()
    page = request.args.get('page', 1, type=int)
    page = max(page, 1)

    available_years = [
        int(year_row[0])
        for year_row in db.session.query(DengueRecord.year)
        .filter(DengueRecord.year.isnot(None))
        .distinct()
        .order_by(DengueRecord.year.asc())
        .all()
    ]
    if not available_years:
        available_years = [datetime.now().year]

    normalized_year_filter = 'all'
    if year_filter and str(year_filter).lower() != 'all':
        try:
            normalized_year_filter = str(int(year_filter))
        except (TypeError, ValueError):
            normalized_year_filter = 'all'

    if normalized_year_filter != 'all' and int(normalized_year_filter) not in available_years:
        available_years.append(int(normalized_year_filter))
        available_years = sorted(set(available_years))

    year_options = ['all'] + [str(year) for year in sorted(set(available_years), reverse=True)]

    query = DengueRecord.query.order_by(DengueRecord.id.desc())

    if normalized_year_filter and normalized_year_filter != 'all':
        try:
            query = query.filter(DengueRecord.year == int(normalized_year_filter))
        except ValueError:
            pass

    if search:
        filter_term = f'%{search}%'
        query = query.filter(
            or_(
                DengueRecord.case_id.ilike(filter_term),
                DengueRecord.barangay.ilike(filter_term),
            )
        )

    records_page = query.paginate(page=page, per_page=50, error_out=False)
    yearly_availability_summary = get_yearly_data_availability(DengueRecord.query.all())
    return render_template(
        'data_repository.html',
        user=current_user,
        records=records_page,
        year=normalized_year_filter,
        search=search,
        available_years=year_options,
        yearly_availability_summary=yearly_availability_summary,
    )

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

    yearly_availability_summary = get_yearly_data_availability(DengueRecord.query.all())
    summary_for_year = get_report_summary_for_year(selected_year) if selected_year is not None else None
    has_data = bool(summary_for_year and summary_for_year['total_records'] > 0)

    return render_template(
        'reports.html',
        user=current_user,
        available_years=available_years,
        selected_year=selected_year,
        yearly_availability_summary=yearly_availability_summary,
        summary_for_year=summary_for_year,
        has_data=has_data,
    )

@app.route('/reports/summary.pdf')
@login_required
def download_summary_pdf():
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    selected_year = request.args.get('year', type=int)
    available_years = get_available_report_years()
    if selected_year is None:
        selected_year = max(available_years) if available_years else None
    if selected_year not in available_years:
        selected_year = None

    records_list = DengueRecord.query.filter(DengueRecord.year == selected_year).order_by(DengueRecord.morbidity_month).all() if selected_year is not None else DengueRecord.query.order_by(DengueRecord.morbidity_month).all()
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
    query = DengueRecord.query.order_by(DengueRecord.id)
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
    query = DengueRecord.query.order_by(DengueRecord.id)
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
    password = request.form.get('password', '')
    role = request.form.get('role', 'Viewer')

    if not username or not password:
        flash('Username and password are required.', 'error')
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
    )
    new_user.set_password(password)
    db.session.add(new_user)
    db.session.commit()
    flash(f'Account created for {username}.', 'info')
    return redirect(url_for('admin'))

@app.route('/admin/reset_password/<int:user_id>', methods=['POST'])
@login_required
def reset_password(user_id):
    if current_user.role != 'Admin':
        flash('Unauthorized access: Admin permissions required.', 'error')
        return redirect(url_for('dashboard'))

    new_password = request.form.get('new_password', '')
    if not new_password.strip():
        flash('A new password is required.', 'error')
        return redirect(url_for('admin'))

    user_item = db.session.get(User, user_id)
    if user_item is None:
        flash('User account not found.', 'error')
        return redirect(url_for('admin'))

    user_item.password_hash = generate_password_hash(new_password)
    db.session.commit()
    flash(f'Password reset successfully for {user_item.username}.', 'info')
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
    return render_template('admin.html', user=current_user, users=users)

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
    existing_columns = {
        column['name'] for column in db.inspect(db.engine).get_columns('dengue_record')
    }

    if 'year' not in existing_columns:
        with db.engine.begin() as connection:
            connection.execute(text('ALTER TABLE dengue_record ADD COLUMN year INTEGER'))

    # Backfill year values from current year for legacy rows without explicit data.
    with db.engine.begin() as connection:
        connection.execute(text('UPDATE dengue_record SET year = :year WHERE year IS NULL'), {'year': datetime.now().year})

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