import unittest

from app import app, db, migrate_user_table


class AssignedBarangayMigrationTest(unittest.TestCase):
    def test_assigned_barangay_removed_from_user_table(self):
        with app.app_context():
            conn = db.engine.raw_connection()
            try:
                conn.execute('DROP TABLE IF EXISTS user')
                conn.execute(
                    '''
                    CREATE TABLE user (
                        id INTEGER PRIMARY KEY,
                        username VARCHAR(80),
                        password_hash VARCHAR(120),
                        role VARCHAR(20),
                        assigned_barangay VARCHAR(100),
                        can_create BOOLEAN DEFAULT 0,
                        can_edit BOOLEAN DEFAULT 0,
                        can_delete BOOLEAN DEFAULT 0,
                        is_blocked BOOLEAN DEFAULT 0
                    )
                    '''
                )
                conn.commit()

                migrate_user_table()

                columns = [column['name'] for column in db.inspect(db.engine).get_columns('user')]
                self.assertNotIn('assigned_barangay', columns)
            finally:
                conn.close()


class UploadHeaderCompatibilityTest(unittest.TestCase):
    def test_upload_accepts_common_header_variants(self):
        from app import DengueRecord
        import io

        with app.app_context():
            db.drop_all()
            db.create_all()
            from app import User
            user = User(username='headeruser', role='Admin', can_create=True, can_edit=True, can_delete=True)
            user.set_password('pass')
            db.session.add(user)
            db.session.commit()

        with app.test_client() as client:
            client.post('/login', data={'username': 'headeruser', 'password': 'pass'}, follow_redirects=False)
            csv_data = (
                'Case ID,Morbidity Month,Morbidity Week,District,Barangay,Age,Sex,Clinical Classification,Case Classification\n'
                'A-100,1,1,Talomo,Talomo Proper,25,M,Dengue without Warning Signs,Confirmed\n'
            )
            response = client.post(
                '/records',
                data={'file': (io.BytesIO(csv_data.encode('utf-8')), 'sample.csv')},
                content_type='multipart/form-data',
                follow_redirects=False,
            )
            body = response.get_data(as_text=True)
            self.assertIn('Successfully imported', body)
            with app.app_context():
                self.assertEqual(DengueRecord.query.count(), 1)

    def test_upload_keeps_new_rows_when_case_id_repeats(self):
        from app import DengueRecord
        import io

        with app.app_context():
            db.drop_all()
            db.create_all()
            from app import User
            user = User(username='dupuser', role='Admin', can_create=True, can_edit=True, can_delete=True)
            user.set_password('pass')
            db.session.add(user)
            db.session.commit()

        with app.test_client() as client:
            client.post('/login', data={'username': 'dupuser', 'password': 'pass'}, follow_redirects=False)
            csv_data = (
                'Case ID,Morbidity Month,Morbidity Week,District,Barangay,Age,Sex,Clinical Classification\n'
                'DENGUE,1,1,Talomo,Talomo Proper,25,M,Dengue without Warning Signs\n'
                'DENGUE,1,2,Talomo,Matina,30,F,Dengue with Warning Signs\n'
            )
            response = client.post(
                '/records',
                data={'file': (io.BytesIO(csv_data.encode('utf-8')), 'sample.csv')},
                content_type='multipart/form-data',
                follow_redirects=False,
            )
            body = response.get_data(as_text=True)
            self.assertIn('Successfully imported', body)
            with app.app_context():
                self.assertEqual(DengueRecord.query.count(), 2)
                case_ids = {row.case_id for row in DengueRecord.query.all()}
                self.assertTrue(any(case_id.startswith('DENGUE') for case_id in case_ids))

    def test_upload_estimates_missing_morbidity_week_from_month(self):
        from app import DengueRecord
        import io

        with app.app_context():
            db.drop_all()
            db.create_all()
            from app import User
            user = User(username='weekuser', role='Admin', can_create=True, can_edit=True, can_delete=True)
            user.set_password('pass')
            db.session.add(user)
            db.session.commit()

        with app.test_client() as client:
            client.post('/login', data={'username': 'weekuser', 'password': 'pass'}, follow_redirects=False)
            csv_data = (
                'Case ID,Morbidity Month,District,Barangay,Age,Sex,Clinical Classification,Case Classification\n'
                'W-100,2,Talomo,Talomo Proper,25,M,Dengue without Warning Signs,Confirmed\n'
            )
            response = client.post(
                '/records',
                data={'file': (io.BytesIO(csv_data.encode('utf-8')), 'sample.csv')},
                content_type='multipart/form-data',
                follow_redirects=False,
            )
            self.assertIn('Successfully imported', response.get_data(as_text=True))
            with app.app_context():
                record = DengueRecord.query.filter_by(case_id='W-100').first()
                self.assertIsNotNone(record)
                self.assertEqual(record.morbidity_week, 6)

    def test_manual_entry_saves_selected_year(self):
        from app import DengueRecord

        with app.app_context():
            db.drop_all()
            db.create_all()
            from app import User
            user = User(username='yearuser', role='Admin', can_create=True, can_edit=True, can_delete=True)
            user.set_password('pass')
            db.session.add(user)
            db.session.commit()

        with app.test_client() as client:
            client.post('/login', data={'username': 'yearuser', 'password': 'pass'}, follow_redirects=False)
            response = client.post(
                '/records',
                data={
                    'manual_entry': '1',
                    'case_id': 'M-2025-001',
                    'year': '2025',
                    'district': 'Talomo',
                    'morbidity_month': '3',
                    'morbidity_week': '10',
                    'barangay': 'Talomo Proper',
                    'age': '28',
                    'sex': 'F',
                    'clinical_classification': 'Dengue with Warning Signs',
                },
                follow_redirects=False,
            )
            self.assertIn('Dengue case saved successfully', response.get_data(as_text=True))
            self.assertIn('alert-success', response.get_data(as_text=True))
            with app.app_context():
                record = DengueRecord.query.filter_by(case_id='M-2025-001').first()
                self.assertIsNotNone(record)
                self.assertEqual(record.year, 2025)
                self.assertEqual(record.district, 'Talomo')
                self.assertEqual(record.sync_status, 'Local')

            repository_response = client.get('/repository?year=2025&search=M-2025-001')
            self.assertEqual(repository_response.status_code, 200)
            self.assertIn('M-2025-001', repository_response.get_data(as_text=True))

    def test_manual_entry_supports_other_or_not_specified_sex(self):
        from app import DengueRecord

        with app.app_context():
            db.drop_all()
            db.create_all()
            from app import User
            user = User(username='othersexuser', role='Admin', can_create=True, can_edit=True, can_delete=True)
            user.set_password('pass')
            db.session.add(user)
            db.session.commit()

        with app.test_client() as client:
            client.post('/login', data={'username': 'othersexuser', 'password': 'pass'}, follow_redirects=False)
            response = client.post(
                '/records',
                data={
                    'manual_entry': '1',
                    'case_id': 'M-OTHER-001',
                    'year': '2028',
                    'district': 'Talomo',
                    'morbidity_month': '3',
                    'morbidity_week': '10',
                    'barangay': 'Barangay-A',
                    'age': '28',
                    'sex': 'Other / Not Specified',
                    'clinical_classification': 'Dengue without Warning Signs',
                },
                follow_redirects=True,
            )
            self.assertIn('Dengue case saved successfully', response.get_data(as_text=True))
            with app.app_context():
                record = DengueRecord.query.filter_by(case_id='M-OTHER-001').first()
                self.assertIsNotNone(record)
                self.assertEqual(record.sex, 'Other / Not Specified')

            repository_response = client.get('/repository?year=2028&search=M-OTHER-001')
            self.assertIn('Other / Not Specified', repository_response.get_data(as_text=True))

    def test_repository_includes_new_years_from_database(self):
        from app import DengueRecord

        with app.app_context():
            db.drop_all()
            db.create_all()
            from app import User
            user = User(username='yearfilteruser', role='Admin', can_create=True, can_edit=True, can_delete=True)
            user.set_password('pass')
            db.session.add(user)
            db.session.add(DengueRecord(
                case_id='M-2028-001',
                year=2028,
                district='Talomo',
                morbidity_month=5,
                morbidity_week=18,
                barangay='Talomo Proper',
                age=30,
                sex='M',
                clinical_classification='Dengue without Warning Signs',
                sync_status='Synced',
            ))
            db.session.commit()

        with app.test_client() as client:
            client.post('/login', data={'username': 'yearfilteruser', 'password': 'pass'}, follow_redirects=False)
            response = client.get('/repository?year=all')
            self.assertIn('/repository?year=2028', response.get_data(as_text=True))

    def test_uploaded_data_uses_dataset_year(self):
        from app import DengueRecord
        import io

        with app.app_context():
            db.drop_all()
            db.create_all()
            from app import User
            user = User(username='fileyearuser', role='Admin', can_create=True, can_edit=True, can_delete=True)
            user.set_password('pass')
            db.session.add(user)
            db.session.commit()

        with app.test_client() as client:
            client.post('/login', data={'username': 'fileyearuser', 'password': 'pass'}, follow_redirects=False)
            csv_data = (
                'Year,Case ID,Morbidity Month,Morbidity Week,District,Barangay,Age,Sex,Clinical Classification\n'
                '2024,A-204,5,18,Talomo,Talomo Proper,19,M,Dengue without Warning Signs\n'
            )
            response = client.post(
                '/records',
                data={'file': (io.BytesIO(csv_data.encode('utf-8')), 'sample.csv')},
                content_type='multipart/form-data',
                follow_redirects=False,
            )
            self.assertIn('Successfully imported', response.get_data(as_text=True))
            with app.app_context():
                record = DengueRecord.query.filter_by(case_id='A-204').first()
                self.assertIsNotNone(record)
                self.assertEqual(record.year, 2024)

    def test_upload_accepts_semicolon_delimited_csv(self):
        from app import DengueRecord
        import io

        with app.app_context():
            db.drop_all()
            db.create_all()
            from app import User
            user = User(username='semicolonuser', role='Admin', can_create=True, can_edit=True, can_delete=True)
            user.set_password('pass')
            db.session.add(user)
            db.session.commit()

        with app.test_client() as client:
            client.post('/login', data={'username': 'semicolonuser', 'password': 'pass'}, follow_redirects=False)
            csv_data = (
                'Case ID;Morbidity Month;Morbidity Week;District;Barangay;Age;Sex;Clinical Classification\n'
                'S-900;6;23;Talomo;Talomo Proper;31;F;Dengue with Warning Signs\n'
            )
            response = client.post(
                '/records',
                data={'file': (io.BytesIO(csv_data.encode('utf-8')), 'sample.csv')},
                content_type='multipart/form-data',
                follow_redirects=False,
            )
            self.assertIn('Successfully imported', response.get_data(as_text=True))
            with app.app_context():
                record = DengueRecord.query.filter_by(case_id='S-900').first()
                self.assertIsNotNone(record)
                self.assertEqual(record.morbidity_month, 6)
                self.assertEqual(record.morbidity_week, 23)
                self.assertEqual(record.district, 'Talomo')
                self.assertEqual(record.barangay, 'Talomo Proper')
                self.assertEqual(record.age, 31)
                self.assertEqual(record.sex, 'F')

    def test_upload_accepts_excel_files(self):
        from app import DengueRecord
        import io
        import pandas as pd

        with app.app_context():
            db.drop_all()
            db.create_all()
            from app import User
            user = User(username='exceluser', role='Admin', can_create=True, can_edit=True, can_delete=True)
            user.set_password('pass')
            db.session.add(user)
            db.session.commit()

        with app.test_client() as client:
            client.post('/login', data={'username': 'exceluser', 'password': 'pass'}, follow_redirects=False)
            df = pd.DataFrame([
                {'Case ID': 'X-100', 'Morbidity Month': 7, 'Morbidity Week': 28, 'District': 'Talomo', 'Barangay': 'Bago Aplaya', 'Age': 22, 'Sex': 'M', 'Clinical Classification': 'Dengue without Warning Signs'}
            ])
            excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            excel_buffer.seek(0)
            response = client.post(
                '/records',
                data={'file': (excel_buffer, 'sample.xlsx')},
                content_type='multipart/form-data',
                follow_redirects=False,
            )
            self.assertIn('Successfully imported', response.get_data(as_text=True))
            with app.app_context():
                self.assertEqual(DengueRecord.query.count(), 1)

    def test_primary_admin_account_is_restored_when_missing(self):
        from app import User, ensure_primary_admin_account

        with app.app_context():
            db.drop_all()
            db.create_all()
            User.query.delete()
            db.session.commit()
            self.assertTrue(ensure_primary_admin_account())
            admin = User.query.filter_by(username='admin').first()
            self.assertIsNotNone(admin)
            self.assertEqual(admin.role, 'Admin')
            self.assertTrue(admin.can_create)
            self.assertTrue(admin.can_edit)
            self.assertTrue(admin.can_delete)
            self.assertFalse(admin.is_blocked)

    def test_dashboard_uses_weekly_trend_json(self):
        from app import DengueRecord

        with app.app_context():
            db.drop_all()
            db.create_all()
            from app import User
            user = User(username='trenduser', role='Admin', can_create=True, can_edit=True, can_delete=True)
            user.set_password('pass')
            db.session.add(user)
            db.session.add_all([
                DengueRecord(case_id='T-1', morbidity_month=1, morbidity_week=1, district='Talomo', barangay='A', age=20, sex='M', clinical_classification='Dengue without Warning Signs', case_classification='Confirmed', sync_status='Synced'),
                DengueRecord(case_id='T-2', morbidity_month=1, morbidity_week=1, district='Talomo', barangay='B', age=22, sex='F', clinical_classification='Dengue without Warning Signs', case_classification='Confirmed', sync_status='Synced'),
                DengueRecord(case_id='T-3', morbidity_month=1, morbidity_week=3, district='Talomo', barangay='C', age=30, sex='M', clinical_classification='Dengue without Warning Signs', case_classification='Confirmed', sync_status='Synced'),
            ])
            db.session.commit()

        with app.test_client() as client:
            client.post('/login', data={'username': 'trenduser', 'password': 'pass'}, follow_redirects=False)
            response = client.get('/dashboard')
            body = response.get_data(as_text=True)
            self.assertIn('morbidity_week_trends', body)
            self.assertIn('week', body)
            self.assertIn('count', body)
            self.assertIn('1', body)


if __name__ == '__main__':
    unittest.main()
