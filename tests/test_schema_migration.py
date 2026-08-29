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
