import os
from io import BytesIO, StringIO
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from sqlalchemy import text
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
    return User.query.get(int(user_id))

# ==========================================
# AUTHENTICATION ROUTES (LOGIN & SIGN UP)
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
        
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid username or password. Please try again.', 'error')
        
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'GET':
        return render_template('signup.html')

    username = request.form.get('username')
    password = request.form.get('password')
    confirm_password = request.form.get('confirm_password')

    if not username or not password:
        flash('Username and password are required.', 'error')
        return redirect(url_for('signup'))

    if password != confirm_password:
        flash('Passwords do not match. Please try again.', 'error')
        return redirect(url_for('signup'))

    existing_user = User.query.filter_by(username=username).first()
    if existing_user:
        flash('Username is already taken. Please choose another.', 'error')
        return redirect(url_for('signup'))

    # Public sign-ups default to 'Viewer' (Admins can promote via /admin)
    new_user = User(
        username=username, 
        role='Viewer', 
        assigned_barangay='Pending Assignment'
    )
    new_user.set_password(password)
    
    db.session.add(new_user)
    db.session.commit()

    flash('Account registered successfully! You can now sign in.', 'info')
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
    records_list = DengueRecord.query.all()
    confirmed_cases = sum(
        1 for record in records_list
        if (record.case_classification or '').strip().lower() == 'confirmed'
    )
    clusters = sorted({record.barangay for record in records_list if record.barangay})
    return render_template(
        'dashboard.html',
        user=current_user,
        total_cases=len(records_list),
        confirmed_cases=confirmed_cases,
        clusters=clusters,
    )

# Screen 3: Data Entry, CSV Ingestion, and Offline Sync
import pandas as pd

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
    if request.method == 'POST':
        file = request.files.get('file')
        if file is not None:
            filename = file.filename or ''
            if filename.lower().endswith(('.csv', '.xlsx')):
                try:
                    # Read either Excel or CSV seamlessly using Pandas
                    if filename.lower().endswith('.xlsx'):
                        df = pd.read_excel(file)
                    else:
                        df = pd.read_csv(file)

                    # Normalize column names to lowercase/trimmed to prevent minor template errors
                    df.columns = [str(c).strip().lower().replace(' ', '_') for c in df.columns]

                    # Loop through rows and insert into database
                    added_count = 0
                    for _, row in df.iterrows():
                        # Generates fallback anonymized case_id if missing in file
                        case_id_value = row.get('case_id')
                        case_id_val = str(case_id_value).strip() if pd.notnull(case_id_value) else ''
                        if not case_id_val or case_id_val.upper() in {'#REF!', '#VALUE!', '#N/A', 'N/A', 'NAN'}:
                            case_id_val = f"GEN-{added_count+1000}"
                        
                        # Prevent duplicate case records
                        if not DengueRecord.query.filter_by(case_id=case_id_val).first():
                            record = DengueRecord(
                                case_id=case_id_val,
                                morbidity_month=safe_int(row.get('morbidity_month'), 1),
                                morbidity_week=safe_int(row.get('morbidity_week')),
                                district=str(row.get('district', 'Talomo')),
                                barangay=str(row.get('barangay', 'Unknown')),
                                age=safe_int(row.get('age'), 0),
                                sex=str(row.get('sex', 'U')),
                                clinical_classification=str(row.get('clinical_classification', 'Unspecified')),
                                sync_status='Synced'
                            )
                            db.session.add(record)
                            added_count += 1

                    db.session.commit()
                    flash(f'Successfully imported {added_count} new records from {filename}!', 'info')
                except Exception as e:
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
            except Exception as e:
                db.session.rollback()
                flash(f'Could not save record: {str(e)}', 'error')

    records_list = DengueRecord.query.all()
    return render_template('records.html', user=current_user, records=records_list)

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
                role='Admin', 
                assigned_barangay='Citywide'
            )
            default_admin.set_password('Admin123!')
            db.session.add(default_admin)
            db.session.commit()
            print("Database initialized and default Admin account created.")

def migrate_user_table():
    existing_columns = {
        column['name'] for column in db.inspect(db.engine).get_columns('user')
    }
    permission_columns = {
        'can_create': 'BOOLEAN DEFAULT 0',
        'can_edit': 'BOOLEAN DEFAULT 0',
        'can_delete': 'BOOLEAN DEFAULT 0',
        'is_blocked': 'BOOLEAN DEFAULT 0',
    }

    with db.engine.begin() as connection:
        for column_name, column_definition in permission_columns.items():
            if column_name not in existing_columns:
                connection.execute(text(
                    f'ALTER TABLE "user" ADD COLUMN {column_name} {column_definition}'
                ))

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)