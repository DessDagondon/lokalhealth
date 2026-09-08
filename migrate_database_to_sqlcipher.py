import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import sqlcipher3

source_path = Path('instance/database.db').resolve()
timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
encrypted_path = source_path.with_name('database.db.sqlcipher-new')
backup_path = source_path.with_name(f'database.db.plaintext-backup-{timestamp}')
passphrase = os.environ.get('DB_PASSPHRASE')
if not passphrase:
    raise RuntimeError('DB_PASSPHRASE is not set.')
if not source_path.exists():
    raise FileNotFoundError(source_path)

plain = sqlite3.connect(source_path)
plain_counts = {
    'users': plain.execute('SELECT COUNT(*) FROM user').fetchone()[0],
    'admins': plain.execute("SELECT COUNT(*) FROM user WHERE username = 'admin'").fetchone()[0],
    'records': plain.execute('SELECT COUNT(*) FROM dengue_record').fetchone()[0],
}
dump = '\n'.join(plain.iterdump())
plain.close()

if encrypted_path.exists():
    encrypted_path.unlink()
encrypted = sqlcipher3.connect(encrypted_path)
encrypted.execute(f"PRAGMA key = '{passphrase.replace(chr(39), chr(39) * 2)}'")
encrypted.executescript(dump)
encrypted.commit()
encrypted_counts = {
    'users': encrypted.execute('SELECT COUNT(*) FROM user').fetchone()[0],
    'admins': encrypted.execute("SELECT COUNT(*) FROM user WHERE username = 'admin'").fetchone()[0],
    'records': encrypted.execute('SELECT COUNT(*) FROM dengue_record').fetchone()[0],
}
cipher_version = encrypted.execute('PRAGMA cipher_version').fetchone()[0]
encrypted.close()

if plain_counts != encrypted_counts or plain_counts['admins'] != 1 or not cipher_version:
    encrypted_path.unlink(missing_ok=True)
    raise RuntimeError(f'Verification failed: plaintext={plain_counts}, encrypted={encrypted_counts}, cipher={cipher_version!r}')

shutil.copy2(source_path, backup_path)
os.replace(encrypted_path, source_path)
print(f'Migrated database safely. Backup: {backup_path}')
print(f'Verified counts: {encrypted_counts}; SQLCipher: {cipher_version}')
