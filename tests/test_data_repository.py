import unittest
from io import BytesIO

import pandas as pd
import sqlcipher3
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app import app, db, DengueRecord, RecordAuditLog, User


class DataRepositoryRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with app.app_context():
            cls.original_engine = db.engines[None]
            cls.test_engine = create_engine(
                'sqlite://',
                creator=lambda: sqlcipher3.connect(':memory:', check_same_thread=False),
                poolclass=StaticPool,
            )
            db.session.remove()
            db.engines[None] = cls.test_engine

    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            db.session.remove()
            db.engines[None] = cls.original_engine
        cls.test_engine.dispose()

    def setUp(self):
        with app.app_context():
            db.drop_all()
            db.create_all()
            user = User(username='repoadmin', role='Admin', can_create=True, can_edit=True, can_delete=True)
            user.set_password('pass')
            db.session.add(user)
            db.session.add_all([
                DengueRecord(case_id='R-2023-001', year=2023, morbidity_week=1, morbidity_month=1, district='Talomo', barangay='Bago Aplaya', age=18, sex='F', clinical_classification='Dengue without Warning Signs', case_classification='Confirmed', sync_status='Synced'),
                DengueRecord(case_id='R-2024-001', year=2024, morbidity_week=2, morbidity_month=2, district='Talomo', barangay='Matina', age=22, sex='M', clinical_classification='Dengue with Warning Signs', case_classification='Confirmed', sync_status='Synced'),
                DengueRecord(case_id='R-2025-001', year=2025, morbidity_week=3, morbidity_month=3, district='Baguio', barangay='Baguio', age=10, sex='F', clinical_classification='Severe Dengue', case_classification='Confirmed', sync_status='Synced'),
            ])
            db.session.commit()

    def test_repository_page_supports_year_filter_and_search(self):
        with app.test_client() as client:
            client.post('/login', data={'username': 'repoadmin', 'password': 'pass'}, follow_redirects=False)
            response = client.get('/repository?year=2024&search=Matina')
            body = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn('Dengue Data Repository', body)
            self.assertIn('R-2024-001', body)
            self.assertIn('Page', body)
            self.assertIn('Previous', body)
            self.assertIn('Next', body)

    def test_repository_combines_filters_and_keeps_them_in_pagination(self):
        with app.test_client() as client:
            client.post('/login', data={'username': 'repoadmin', 'password': 'pass'}, follow_redirects=False)
            response = client.get(
                '/repository?year=all&search=Matina&district=Talomo&'
                'morbidity_month=2&morbidity_week=2&case_classification=Confirmed&'
                'clinical_classification=Non-severe'
            )
            body = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn('R-2024-001', body)
            self.assertNotIn('R-2023-001', body)
            self.assertNotIn('R-2025-001', body)
            self.assertIn('name="district"', body)
            self.assertIn('value="Talomo" selected', body)

    def test_repository_page_shows_yearly_archive_controls_without_row_archive_buttons(self):
        with app.app_context():
            db.session.add_all([
                DengueRecord(case_id='R-2023-002', year=2023, morbidity_week=5, morbidity_month=1, district='Talomo', barangay='Bago Aplaya', age=0, sex='F', clinical_classification='Dengue without Warning Signs', case_classification='Confirmed', sync_status='Synced'),
                DengueRecord(case_id='R-2023-003', year=2023, morbidity_week=6, morbidity_month=0, district='Talomo', barangay='Matina', age=35, sex='M', clinical_classification='Dengue with Warning Signs', case_classification='Confirmed', sync_status='Synced'),
            ])
            db.session.commit()

        with app.test_client() as client:
            client.post('/login', data={'username': 'repoadmin', 'password': 'pass'}, follow_redirects=False)
            response = client.get('/repository?year=all')
            body = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn('2023', body)
            self.assertIn('Yearly archive / lock status', body)
            self.assertIn('Archive year', body)
            self.assertNotIn('toggle-archive-record', body)
            self.assertNotIn('>Archive</button>', body)

    def test_reports_page_filters_export_links_by_year(self):
        with app.test_client() as client:
            client.post('/login', data={'username': 'repoadmin', 'password': 'pass'}, follow_redirects=False)
            response = client.get('/reports?year=2024')
            body = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn('year-select', body)
            self.assertIn('/reports/summary.pdf?year=2024', body)
            self.assertIn('/reports/summary.csv?year=2024', body)
            self.assertIn('/reports/cases.csv?year=2024', body)
            self.assertIn('/reports/cases.xlsx?year=2024', body)

    def test_reports_page_shows_yearly_summary_aggregation(self):
        with app.app_context():
            db.session.add_all([
                DengueRecord(case_id='R-2023-004', year=2023, morbidity_week=7, morbidity_month=2, district='Talomo', barangay='Bago Aplaya', age=27, sex='F', clinical_classification='Dengue without Warning Signs', case_classification='Confirmed', sync_status='Synced'),
                DengueRecord(case_id='R-2024-002', year=2024, morbidity_week=8, morbidity_month=3, district='Matina', barangay='Matina', age=31, sex='M', clinical_classification='Dengue with Warning Signs', case_classification='Probable', sync_status='Synced'),
            ])
            db.session.commit()

        with app.test_client() as client:
            client.post('/login', data={'username': 'repoadmin', 'password': 'pass'}, follow_redirects=False)
            response = client.get('/reports?year=2024')
            body = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn('2024 availability summary', body)
            self.assertIn('2 total records', body)
            self.assertIn('2024 summary', body)
            self.assertIn('Case Classification', body)

    def test_admin_can_manage_edit_permission(self):
        with app.app_context():
            editor = User(username='dataset-editor', role='BHW', can_edit=False)
            editor.set_password('pass')
            db.session.add(editor)
            db.session.commit()
            editor_id = editor.id

        with app.test_client() as client:
            client.post('/login', data={'username': 'repoadmin', 'password': 'pass'}, follow_redirects=False)
            response = client.get('/admin')
            body = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn('Archive', body)
            self.assertNotIn('Can Delete Case Records', body)
            self.assertIn('>Edit</label>', body)
            self.assertNotIn('Edit dataset', body)
            self.assertIn('id="new-can-edit"', body)
            self.assertNotIn('<th>Old Value</th>', body)
            self.assertNotIn('<th>New Value</th>', body)

            response = client.post(
                f'/admin/users/{editor_id}/permissions',
                data={'can_edit': 'on'},
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 302)

        with app.app_context():
            self.assertTrue(db.session.get(User, editor_id).can_edit)

    def test_view_only_user_can_view_but_cannot_create_edit_or_archive(self):
        with app.app_context():
            viewer = User(username='view-only-user', role='BHW')
            viewer.set_password('pass')
            db.session.add(viewer)
            db.session.commit()
            record_id = DengueRecord.query.filter_by(case_id='R-2025-001').one().id

        with app.test_client() as client:
            client.post('/login', data={'username': 'view-only-user', 'password': 'pass'}, follow_redirects=False)
            records_response = client.get('/records')
            repository_response = client.get('/repository')
            self.assertEqual(records_response.status_code, 200)
            self.assertEqual(repository_response.status_code, 200)
            self.assertNotIn('manual_entry', records_response.get_data(as_text=True))
            self.assertNotIn(f'/records/edit/{record_id}', repository_response.get_data(as_text=True))

            create_response = client.post('/records', data={
                'manual_entry': '1',
                'case_id': 'VIEWER-CANNOT-CREATE',
                'year': '2025',
            }, follow_redirects=False)
            edit_response = client.post(f'/records/edit/{record_id}', data={
                'morbidity_month': '4',
                'morbidity_week': '10',
                'district': 'Baguio',
                'barangay': 'Unauthorized edit',
                'age': '10',
                'sex': 'F',
                'clinical_classification': 'Severe Dengue',
                'case_classification': 'Confirmed',
            }, follow_redirects=False)
            archive_response = client.post('/repository/toggle-archive/2025', follow_redirects=False)
            self.assertEqual(create_response.status_code, 302)
            self.assertEqual(edit_response.status_code, 302)
            self.assertEqual(archive_response.status_code, 302)

        with app.app_context():
            self.assertIsNone(DengueRecord.query.filter_by(case_id='VIEWER-CANNOT-CREATE').first())
            self.assertEqual(db.session.get(DengueRecord, record_id).barangay, 'Baguio')
            self.assertFalse(DengueRecord.query.filter_by(year=2025).one().is_archived)

    def test_user_with_edit_create_and_archive_permissions_can_use_them(self):
        with app.app_context():
            operator = User(
                username='authorized-operator',
                role='BHW',
                can_edit=True,
                can_create=True,
                can_delete=True,
            )
            operator.set_password('pass')
            db.session.add(operator)
            db.session.commit()
            record_id = DengueRecord.query.filter_by(case_id='R-2025-001').one().id

        with app.test_client() as client:
            client.post('/login', data={'username': 'authorized-operator', 'password': 'pass'}, follow_redirects=False)
            records_response = client.get('/records')
            repository_response = client.get('/repository')
            self.assertEqual(records_response.status_code, 200)
            self.assertIn('manual_entry', records_response.get_data(as_text=True))
            self.assertIn(f'/records/edit/{record_id}', repository_response.get_data(as_text=True))

            create_response = client.post('/records', data={
                'manual_entry': '1',
                'case_id': 'OPERATOR-CREATE-001',
                'year': '2025',
                'morbidity_month': '8',
                'morbidity_week': '32',
                'district': 'Talomo',
                'barangay': 'Matina',
                'age': '20',
                'sex': 'F',
                'clinical_classification': 'Dengue without Warning Signs',
                'case_classification': 'Confirmed',
            }, follow_redirects=False)
            edit_response = client.post(f'/records/edit/{record_id}', data={
                'morbidity_month': '4',
                'morbidity_week': '10',
                'district': 'Baguio',
                'barangay': 'Operator Updated',
                'age': '10',
                'sex': 'F',
                'clinical_classification': 'Severe Dengue',
                'case_classification': 'Confirmed',
            }, follow_redirects=False)
            archive_response = client.post('/repository/toggle-archive/2025', follow_redirects=False)
            self.assertEqual(create_response.status_code, 200)
            self.assertEqual(edit_response.status_code, 302)
            self.assertEqual(archive_response.status_code, 302)

        with app.app_context():
            self.assertIsNotNone(DengueRecord.query.filter_by(case_id='OPERATOR-CREATE-001').first())
            self.assertEqual(db.session.get(DengueRecord, record_id).barangay, 'Operator Updated')
            self.assertTrue(all(
                record.is_archived for record in DengueRecord.query.filter_by(year=2025).all()
            ))

    def test_admin_audit_history_filters_searches_and_paginates(self):
        with app.app_context():
            db.session.add_all([
                RecordAuditLog(
                    record_id=1000 + index,
                    username='audit-user',
                    action_type='EDIT',
                    field_changed='age',
                    old_value='10',
                    new_value='11',
                )
                for index in range(17)
            ])
            db.session.add(RecordAuditLog(
                record_id=999,
                username='different-user',
                action_type='CREATE',
                field_changed='record',
                new_value='created-case',
            ))
            db.session.commit()

        with app.test_client() as client:
            client.post('/login', data={'username': 'repoadmin', 'password': 'pass'}, follow_redirects=False)
            response = client.get('/admin?action=EDIT&search=audit-user')
            body = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn('Page 1 of 2', body)
            self.assertIn('action=EDIT&amp;search=audit-user', body)
            self.assertNotIn('created-case', body)
            self.assertNotIn('<th>Old Value</th>', body)
            self.assertNotIn('<th>New Value</th>', body)

            response = client.get('/admin?action=EDIT&search=audit-user&page=2')
            self.assertEqual(response.status_code, 200)
            self.assertIn('Page 2 of 2', response.get_data(as_text=True))

            response = client.get('/admin?action=CREATE&search=different-user')
            body = response.get_data(as_text=True)
            self.assertIn('Created', body)
            self.assertIn('999', body)
            self.assertNotIn('audit-user', body)
            self.assertNotIn('created-case', body)

    def test_manual_record_creation_is_included_in_audit_history(self):
        with app.test_client() as client:
            client.post('/login', data={'username': 'repoadmin', 'password': 'pass'}, follow_redirects=False)
            response = client.post('/records', data={
                'manual_entry': '1',
                'case_id': 'AUDIT-CREATE-001',
                'year': '2025',
                'morbidity_month': '8',
                'morbidity_week': '32',
                'district': 'Talomo',
                'barangay': 'Matina',
                'age': '20',
                'sex': 'F',
                'clinical_classification': 'Dengue without Warning Signs',
                'case_classification': 'Confirmed',
            }, follow_redirects=False)
            self.assertEqual(response.status_code, 200)

        with app.app_context():
            record = DengueRecord.query.filter_by(case_id='AUDIT-CREATE-001').one()
            audit = RecordAuditLog.query.filter_by(record_id=record.id).one()
            self.assertEqual(audit.action_type, 'CREATE')
            self.assertEqual(audit.username, 'repoadmin')
            self.assertEqual(audit.new_value, 'AUDIT-CREATE-001')

    def test_edit_record_writes_audit_entries(self):
        with app.app_context():
            record = DengueRecord.query.filter_by(case_id='R-2024-001').first()
            record_id = record.id

        with app.test_client() as client:
            client.post('/login', data={'username': 'repoadmin', 'password': 'pass'}, follow_redirects=False)
            response = client.post(f'/records/edit/{record_id}', data={
                'morbidity_month': '4',
                'morbidity_week': '10',
                'district': 'Talomo',
                'barangay': 'Updated Matina',
                'age': '23',
                'sex': 'F',
                'clinical_classification': 'Severe Dengue',
                'case_classification': 'Confirmed',
            }, follow_redirects=False)
            self.assertEqual(response.status_code, 302)
            self.assertIn('/repository', response.headers['Location'])

        with app.app_context():
            record = db.session.get(DengueRecord, record_id)
            self.assertEqual(record.barangay, 'Updated Matina')
            self.assertEqual(record.age, 23)
            audit_rows = RecordAuditLog.query.filter_by(record_id=record_id).all()
            self.assertEqual(
                {entry.field_changed for entry in audit_rows},
                {'morbidity_month', 'morbidity_week', 'barangay', 'age', 'sex', 'clinical_classification'},
            )
            self.assertTrue(all(entry.action_type == 'EDIT' for entry in audit_rows))
            self.assertTrue(all(entry.username == 'repoadmin' for entry in audit_rows))

    def test_non_admin_cannot_edit_archived_records(self):
        with app.app_context():
            record = DengueRecord.query.filter_by(case_id='R-2024-001').first()
            record.is_archived = True
            record_id = record.id
            viewer = User(username='archiveviewer', role='BHW', can_edit=True)
            viewer.set_password('pass')
            db.session.add(viewer)
            db.session.commit()

        with app.test_client() as client:
            client.post('/login', data={'username': 'archiveviewer', 'password': 'pass'}, follow_redirects=False)
            response = client.get(f'/records/edit/{record_id}', follow_redirects=False)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.headers['Location'], '/repository?year=2024')

        with app.app_context():
            self.assertEqual(RecordAuditLog.query.filter_by(record_id=record_id).count(), 0)

    def test_edit_and_archive_permissions_are_enforced(self):
        with app.app_context():
            record = DengueRecord.query.filter_by(case_id='R-2025-001').first()
            record_id = record.id
            viewer = User(username='read-only-user', role='BHW', can_edit=False, can_delete=False)
            viewer.set_password('pass')
            db.session.add(viewer)
            db.session.commit()

        with app.test_client() as client:
            client.post('/login', data={'username': 'read-only-user', 'password': 'pass'}, follow_redirects=False)
            edit_response = client.post(f'/records/edit/{record_id}', data={
                'morbidity_month': '4',
                'morbidity_week': '10',
                'district': 'Baguio',
                'barangay': 'Unauthorized edit',
                'age': '10',
                'sex': 'F',
                'clinical_classification': 'Severe Dengue',
                'case_classification': 'Confirmed',
            }, follow_redirects=False)
            archive_response = client.post('/repository/toggle-archive/2025', follow_redirects=False)
            self.assertEqual(edit_response.status_code, 302)
            self.assertEqual(edit_response.headers['Location'], '/repository')
            self.assertEqual(archive_response.status_code, 302)
            self.assertTrue(archive_response.headers['Location'].startswith('/repository'))

        with app.app_context():
            record = db.session.get(DengueRecord, record_id)
            self.assertEqual(record.barangay, 'Baguio')
            self.assertFalse(record.is_archived)
            self.assertEqual(RecordAuditLog.query.filter_by(record_id=record_id).count(), 0)

    def test_archive_hub_filters_records_by_all_repository_filters(self):
        with app.app_context():
            record = DengueRecord.query.filter_by(case_id='R-2025-001').first()
            record.is_archived = True
            record_id = record.id
            db.session.commit()

        with app.test_client() as client:
            client.post('/login', data={'username': 'repoadmin', 'password': 'pass'}, follow_redirects=False)
            hub = client.get('/archive?year=2025&district=Baguio&search=R-2025')
            self.assertEqual(hub.status_code, 200)
            body = hub.get_data(as_text=True)
            self.assertIn('R-2025-001', body)
            self.assertIn('name="district"', body)
            self.assertIn('value="Baguio" selected', body)
            self.assertNotIn('R-2024-001', body)
            self.assertNotIn('Restore / Unarchive', body)
            self.assertNotIn('Unarchive', body)

        with app.app_context():
            record = db.session.get(DengueRecord, record_id)
            self.assertTrue(record.is_archived)

    def test_year_toggle_archives_every_record_for_the_selected_year(self):
        with app.app_context():
            first_record = DengueRecord.query.filter_by(case_id='R-2025-001').first()
            first_record.is_archived = True
            db.session.add(DengueRecord(
                case_id='R-2025-002',
                year=2025,
                morbidity_week=4,
                morbidity_month=4,
                district='Baguio',
                barangay='Baguio',
                age=12,
                sex='M',
                clinical_classification='Severe Dengue',
                case_classification='Confirmed',
                sync_status='Synced',
            ))
            db.session.commit()

        with app.test_client() as client:
            client.post('/login', data={'username': 'repoadmin', 'password': 'pass'}, follow_redirects=False)
            response = client.post('/repository/toggle-archive/2025', follow_redirects=False)
            self.assertEqual(response.status_code, 302)
            self.assertIn('year=2025', response.headers['Location'])

        with app.app_context():
            year_records = DengueRecord.query.filter_by(year=2025).all()
            self.assertTrue(all(record.is_archived for record in year_records))
            updated_audit = RecordAuditLog.query.filter_by(
                record_id=next(record.id for record in year_records if record.case_id == 'R-2025-002')
            ).one()
            self.assertEqual((updated_audit.old_value, updated_audit.new_value), ('False', 'True'))

        with app.test_client() as client:
            client.post('/login', data={'username': 'repoadmin', 'password': 'pass'}, follow_redirects=False)
            body = client.get('/repository?year=2025').get_data(as_text=True)
            self.assertIn('href="/repository?year=all&amp;search=', body)
            self.assertNotIn('href="/repository?year=2025', body)
            self.assertNotIn('>2025</a>', body)
            self.assertNotIn('>2025</span>', body)
            self.assertNotIn('/repository/toggle-archive/2025', body)
            response = client.post('/repository/toggle-archive/2025', follow_redirects=False)
            self.assertEqual(response.status_code, 302)

        with app.app_context():
            year_records = DengueRecord.query.filter_by(year=2025).all()
            self.assertTrue(all(record.is_archived for record in year_records))

    def test_archive_exports_are_limited_to_archived_records_and_selected_year(self):
        with app.app_context():
            record = DengueRecord.query.filter_by(case_id='R-2025-001').first()
            record.is_archived = True
            db.session.commit()

        with app.test_client() as client:
            client.post('/login', data={'username': 'repoadmin', 'password': 'pass'}, follow_redirects=False)
            hub = client.get('/archive?year=2025')
            hub_body = hub.get_data(as_text=True)
            self.assertIn('/archive/cases.csv?year=2025', hub_body)
            self.assertIn('/archive/cases.xlsx?year=2025', hub_body)

            csv_response = client.get('/archive/cases.csv?year=2025')
            csv_body = csv_response.get_data(as_text=True)
            self.assertEqual(csv_response.status_code, 200)
            self.assertIn('R-2025-001', csv_body)
            self.assertNotIn('R-2024-001', csv_body)

            excel_response = client.get('/archive/cases.xlsx?year=2025')
            excel_records = pd.read_excel(BytesIO(excel_response.data))
            self.assertEqual(excel_response.status_code, 200)
            self.assertEqual(excel_records['case_id'].tolist(), ['R-2025-001'])

    def test_archive_all_filter_offers_and_downloads_all_archived_records(self):
        with app.app_context():
            for case_id in ('R-2023-001', 'R-2025-001'):
                record = DengueRecord.query.filter_by(case_id=case_id).one()
                record.is_archived = True
            db.session.commit()

        with app.test_client() as client:
            client.post('/login', data={'username': 'repoadmin', 'password': 'pass'}, follow_redirects=False)
            hub = client.get('/archive?year=all')
            body = hub.get_data(as_text=True)
            self.assertEqual(hub.status_code, 200)
            self.assertIn('href="/archive/cases.csv"', body)
            self.assertIn('href="/archive/cases.xlsx"', body)

            csv_response = client.get('/archive/cases.csv')
            self.assertEqual(csv_response.status_code, 200)
            self.assertIn('R-2023-001', csv_response.get_data(as_text=True))
            self.assertIn('R-2025-001', csv_response.get_data(as_text=True))
            self.assertNotIn('R-2024-001', csv_response.get_data(as_text=True))

            excel_response = client.get('/archive/cases.xlsx')
            excel_records = pd.read_excel(BytesIO(excel_response.data))
            self.assertEqual(excel_response.status_code, 200)
            self.assertEqual(
                set(excel_records['case_id']),
                {'R-2023-001', 'R-2025-001'},
            )

    def test_archived_year_disappears_from_repository_filters_and_status(self):
        with app.app_context():
            for record in DengueRecord.query.filter_by(year=2025).all():
                record.is_archived = True
            db.session.commit()

        with app.test_client() as client:
            client.post('/login', data={'username': 'repoadmin', 'password': 'pass'}, follow_redirects=False)
            response = client.get('/repository?year=2025')
            body = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn('href="/repository?year=all&amp;search=', body)
            self.assertNotIn('href="/repository?year=2025', body)
            self.assertNotIn('>2025</a>', body)
            self.assertNotIn('>2025</span>', body)
            self.assertNotIn('/repository/toggle-archive/2025', body)
            self.assertIn('R-2024-001', body)
            self.assertNotIn('R-2025-001', body)

    def test_archive_hub_applies_classification_filters(self):
        with app.app_context():
            record = DengueRecord.query.filter_by(case_id='R-2024-001').first()
            record.is_archived = True
            db.session.commit()

        with app.test_client() as client:
            client.post('/login', data={'username': 'repoadmin', 'password': 'pass'}, follow_redirects=False)
            response = client.get(
                '/archive?year=2024&district=Talomo&barangay=Matina&'
                'morbidity_month=2&morbidity_week=2&case_classification=Confirmed&'
                'clinical_classification=Non-severe'
            )
            body = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn('R-2024-001', body)
            self.assertNotIn('R-2025-001', body)


if __name__ == '__main__':
    unittest.main()
