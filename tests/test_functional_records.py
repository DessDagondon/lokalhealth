from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd

from app import DengueRecord, RecordAuditLog, User, app, db
from tests.functional_test_support import IsolatedDatabaseTestCase


class FunctionalRecordManagementTest(IsolatedDatabaseTestCase):
    def test_manual_csv_excel_edit_archive_and_audit_persistence(self):
        with app.app_context():
            operator = User(
                username='records-operator',
                role='BHW',
                can_create=True,
                can_edit=True,
                can_delete=True,
            )
            operator.set_password('operator-password')
            db.session.add(operator)
            db.session.commit()

        anonymous = app.test_client()
        unauthorized = anonymous.post(
            '/records',
            data={'manual_entry': '1', 'case_id': 'ANONYMOUS-CASE'},
            follow_redirects=False,
        )
        self.assertEqual(unauthorized.status_code, 302)
        self.assertTrue(unauthorized.headers['Location'].startswith('/login'))

        client = app.test_client()
        login = client.post(
            '/login',
            data={'username': 'records-operator', 'password': 'operator-password'},
            follow_redirects=False,
        )
        self.assertEqual(login.status_code, 302)

        year = datetime.now().year
        manual = client.post(
            '/records',
            data={
                'manual_entry': '1',
                'case_id': 'MANUAL-FLOW-001',
                'year': str(year),
                'morbidity_month': '4',
                'morbidity_week': '14',
                'district': 'Talomo',
                'barangay': 'Matina',
                'age': '24',
                'sex': 'F',
                'clinical_classification': 'Dengue without Warning Signs',
                'case_classification': 'Confirmed',
            },
            follow_redirects=True,
        )
        self.assertIn('Dengue case saved successfully', manual.get_data(as_text=True))

        csv_upload = client.post(
            '/records',
            data={
                'file': (
                    BytesIO(
                        (Path(__file__).parent / 'fixtures' / 'functional-cases.csv')
                        .read_bytes()
                        .replace(b'2026,CSV-FLOW-001', f'{year},CSV-FLOW-001'.encode('ascii'))
                    ),
                    'functional-cases.csv',
                )
            },
            content_type='multipart/form-data',
            follow_redirects=True,
        )
        self.assertIn('Successfully imported 1 new records', csv_upload.get_data(as_text=True))

        excel_buffer = BytesIO()
        pd.DataFrame([{
            'Year': year,
            'Case ID': 'EXCEL-FLOW-001',
            'Morbidity Month': 6,
            'Morbidity Week': 22,
            'District': 'Baguio',
            'Barangay': 'Baguio Proper',
            'Age': 19,
            'Sex': 'F',
            'Clinical Classification': 'Severe Dengue',
            'Case Classification': 'Confirmed',
        }]).to_excel(excel_buffer, index=False, engine='openpyxl')
        excel_buffer.seek(0)
        excel_upload = client.post(
            '/records',
            data={'file': (excel_buffer, 'functional-cases.xlsx')},
            content_type='multipart/form-data',
            follow_redirects=True,
        )
        self.assertIn('Successfully imported 1 new records', excel_upload.get_data(as_text=True))

        with app.app_context():
            records = {
                record.case_id: record
                for record in DengueRecord.query.filter(
                    DengueRecord.case_id.in_(
                        ['MANUAL-FLOW-001', 'CSV-FLOW-001', 'EXCEL-FLOW-001']
                    )
                ).all()
            }
            self.assertEqual(set(records), {
                'MANUAL-FLOW-001', 'CSV-FLOW-001', 'EXCEL-FLOW-001',
            })
            self.assertEqual(records['MANUAL-FLOW-001'].year, year)
            self.assertEqual(records['CSV-FLOW-001'].barangay, 'Bago Aplaya')
            self.assertEqual(records['EXCEL-FLOW-001'].clinical_classification, 'Severe Dengue')
            manual_record_id = records['MANUAL-FLOW-001'].id

        edit = client.post(
            f'/records/edit/{manual_record_id}',
            data={
                'morbidity_month': '7',
                'morbidity_week': '30',
                'district': 'Talomo',
                'barangay': 'Updated Matina',
                'age': '25',
                'sex': 'F',
                'clinical_classification': 'Dengue with Warning Signs',
                'case_classification': 'Confirmed',
            },
            follow_redirects=False,
        )
        self.assertEqual(edit.status_code, 302)

        archive = client.post(
            f'/repository/toggle-archive/{year}',
            follow_redirects=False,
        )
        self.assertEqual(archive.status_code, 302)

        with app.app_context():
            edited = db.session.get(DengueRecord, manual_record_id)
            self.assertEqual(edited.barangay, 'Updated Matina')
            self.assertTrue(edited.is_archived)
            audit_rows = RecordAuditLog.query.filter(
                RecordAuditLog.record_id.in_([record.id for record in records.values()])
            ).all()
            self.assertIn('EDIT', {entry.action_type for entry in audit_rows})
            self.assertIn('ARCHIVE', {entry.action_type for entry in audit_rows})
            self.assertIn('CREATE', {entry.action_type for entry in audit_rows})
            self.assertEqual(
                {entry.username for entry in audit_rows},
                {'records-operator'},
            )
