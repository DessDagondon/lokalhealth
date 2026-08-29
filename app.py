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
    morbidity_month = db.Column(db.Integer, nullable=False)
    morbidity_week = db.Column(db.Integer, nullable=True)
    district = db.Column(db.String(100), nullable=False, default='Talomo')
    barangay = db.Column(db.String(100), nullable=False)
    age = db.Column(db.Integer, nullable=False)
    sex = db.Column(db.String(10), nullable=False)
    clinical_classification = db.Column(db.String(100), nullable=True)
    case_classification = db.Column(db.String(100), nullable=True)
    sync_status = db.Column(db.String(20), default='Synced')  # 'Local' or 'Synced'

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
    text = text.replace('(', '').replace(')', '').replace('-', '_').replace('/', '_')
    text = text.replace(' ', '_').replace('.', '').replace('&', 'and')
    while '__' in text:
        text = text.replace('__', '_')
    return text.strip('_')


def estimate_morbidity_week(month_value):
    month = safe_int(month_value, default=None)
    if month is None:
        return None
    if month < 1:
        return None
    return max(1, min(52, (month * 4) - 2))


def build_standardized_upload_df(df):
    if df is None or df.empty:
        return df

    normalized_columns = {normalize_upload_header(column): column for column in df.columns}

    age_candidates = ['age_in_years', 'ageyears', 'age']
    age_source = next((normalized_columns[c] for c in age_candidates if c in normalized_columns), None)
    if age_source is not None:
        df['age'] = pd.to_numeric(df[age_source], errors='coerce').fillna(0).astype(int)
    else:
        df['age'] = 0

    rename_map = {
        'case_id': 'case_id',
        'case_code': 'case_id',
        'caseid': 'case_id',
        'morbidity_month': 'morbidity_month',
        'morbidity_week': 'morbidity_week',
        'mw': 'morbidity_week',
        'morbidity_week_number': 'morbidity_week',
        'district': 'district',
        'barangay': 'barangay',
        'current_address_barangay': 'barangay',
        'sex': 'sex',
        'clinical_classification': 'clinical_classification',
        'clinclass': 'clinical_classification',
        'case_classification': 'case_classification',
        'classification': 'case_classification',
    }

    for original_name, standardized_name in rename_map.items():
        if original_name in normalized_columns:
            df = df.rename(columns={normalized_columns[original_name]: standardized_name})

    if 'case_id' not in df.columns:
        df['case_id'] = None
    if 'morbidity_month' not in df.columns:
        df['morbidity_month'] = None
    if 'morbidity_week' not in df.columns:
        df['morbidity_week'] = None
    if 'district' not in df.columns:
        df['district'] = 'Unavailable'
    if 'barangay' not in df.columns:
        df['barangay'] = 'Unavailable'
    if 'sex' not in df.columns:
        df['sex'] = 'Unavailable'
    if 'clinical_classification' not in df.columns:
        df['clinical_classification'] = 'Unavailable'
    if 'case_classification' not in df.columns:
        df['case_classification'] = None

    df['morbidity_week'] = df.apply(
        lambda row: safe_int(row.get('morbidity_week'))
        if row.get('morbidity_week') is not None and not pd.isna(row.get('morbidity_week'))
        else estimate_morbidity_week(row.get('morbidity_month')),
        axis=1,
    )

    final_columns = [
        'case_id', 'morbidity_month', 'morbidity_week', 'district', 'barangay', 'age',
        'sex', 'clinical_classification', 'case_classification'
    ]
    return df[final_columns].copy()


def safe_int(value, default=None):
    if value is None or pd.isna(value):
        return default

    try:
        if isinstance(value, str) and value.strip().upper() in {'#REF!', '#VALUE!', '#N/A', 'NA', 'N/A', ''}:
            return default
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


@app.route('/records', methods=['GET', 'POST'])
@login_required
def records():
    page = request.args.get('page', 1, type=int)
    page = max(page, 1)

    if request.method == 'POST':
        file = request.files.get('file')
        if file is not None:
            filename = (file.filename or '').strip()
            if filename.lower().endswith(('.csv', '.xlsx')):
                try:
                    file.stream.seek(0)
                    # Read either Excel or CSV seamlessly using Pandas
                    if filename.lower().endswith('.xlsx'):
                        df = pd.read_excel(file, engine='openpyxl')
                    else:
                        df = pd.read_csv(file, dtype=str, keep_default_na=True)

                    if df.empty:
                        raise ValueError('The uploaded file contains no data rows.')

                    df = build_standardized_upload_df(df)

                    with db.session.no_autoflush:
                        existing_case_ids = {
                            case_id for (case_id,) in db.session.query(DengueRecord.case_id).all()
                        }

                    # Loop through rows and insert into database
                    added_count = 0
                    for _, row in df.iterrows():
                        if row.isnull().all():
                            continue

                        # Generates fallback anonymized case_id if missing in file
                        case_id_value = row.get('case_id')
                        case_id_val = '' if pd.isna(case_id_value) else str(case_id_value).strip()
                        if not case_id_val or case_id_val.upper() in {'#REF!', '#VALUE!', '#N/A', 'N/A', 'NAN', 'NULL'}:
                            case_id_val = f"GEN-{added_count+1000}"

                        # Preserve real data even if similar case IDs repeat inside the same upload or already exist.
                        candidate_case_id = case_id_val
                        duplicate_index = 1
                        while candidate_case_id in existing_case_ids:
                            candidate_case_id = f"{case_id_val}-{duplicate_index}"
                            duplicate_index += 1

                        record = DengueRecord(
                            case_id=candidate_case_id,
                            morbidity_month=safe_int(row.get('morbidity_month'), 1),
                            morbidity_week=safe_int(row.get('morbidity_week')) or estimate_morbidity_week(row.get('morbidity_month')),
                            district=str(row.get('district') or 'Unavailable').strip() or 'Unavailable',
                            barangay=str(row.get('barangay') or 'Unavailable').strip() or 'Unavailable',
                            age=safe_int(row.get('age'), 0),
                            sex=str(row.get('sex') or 'Unavailable').strip().upper()[:1] if str(row.get('sex') or 'Unavailable').strip() else 'Unavailable',
                            clinical_classification=str(row.get('clinical_classification') or 'Unavailable').strip() or 'Unavailable',
                            case_classification=str(row.get('case_classification') or '').strip() or None,
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
        elif request.form.get('manual_entry'):
            try:
                record = DengueRecord(
                    case_id=request.form['case_id'],
                    morbidity_month=int(request.form['morbidity_month']),
                    morbidity_week=int(request.form['morbidity_week']) if request.form.get('morbidity_week') else None,
                    barangay=request.form['barangay'],
                    age=int(request.form['age']),
                    sex=request.form['sex'],
                    clinical_classification=request.form.get('clinical_classification'),
                    sync_status='Synced'
                )
                db.session.add(record)
                db.session.commit()
                flash('Dengue case saved successfully.', 'info')
                page = 1
            except Exception as e:
                db.session.rollback()
                flash(f'Could not save record: {str(e)}', 'error')

    records_page = DengueRecord.query.order_by(DengueRecord.id.desc()).paginate(page=page, per_page=10, error_out=False)
    return render_template('records.html', user=current_user, records=records_page)

# Screen 4: Automated PDF/CSV Export Hub
@app.route('/reports')
@login_required
def reports():
    return render_template('reports.html', user=current_user)

@app.route('/reports/summary.pdf')
@login_required
def download_summary_pdf():
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    records_list = DengueRecord.query.order_by(DengueRecord.morbidity_month).all()
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
    document.drawString(72, 700, f'Total anonymized cases: {len(records_list)}')
    document.drawString(72, 680, 'Cases by morbidity month:')

    y_position = 660
    for month, count in sorted(month_counts.items()):
        document.drawString(90, y_position, f'Month {month}: {count} case(s)')
        y_position -= 18
        if y_position < 72:
            document.showPage()
            y_position = 740

    document.save()
    pdf_buffer.seek(0)
    return send_file(pdf_buffer, mimetype='application/pdf', as_attachment=True,
                     download_name='lokalhealth-monthly-summary.pdf')

@app.route('/reports/cases.csv')
@login_required
def download_cases_csv():
    records_list = DengueRecord.query.order_by(DengueRecord.id).all()
    csv_buffer = StringIO()
    pd.DataFrame([
        {
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
                     download_name='lokalhealth-anonymized-case-data.csv')

@app.route('/reports/cases.xlsx')
@login_required
def download_cases_excel():
    records_list = DengueRecord.query.order_by(DengueRecord.id).all()
    excel_buffer = BytesIO()
    pd.DataFrame([
        {
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
                     as_attachment=True, download_name='lokalhealth-anonymized-case-data.xlsx')

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
        # Seed default administrative account if not exists
        if not User.query.filter_by(username='admin').first():
            default_admin = User(
                username='admin',
                role='Admin'
            )
            default_admin.set_password('Admin123!')
            db.session.add(default_admin)
            db.session.commit()
            print("Database initialized and default Admin account created.")

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