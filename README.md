# LokalHealth Dengue Surveillance

LokalHealth is a Flask web application for dengue case surveillance. It provides
case-record management, a surveillance dashboard, weekly trend and forecast
views, barangay summaries, reports, and basic user administration.

This guide covers cloning the repository, setting up a local environment, and
starting the application.

## Requirements

- Python 3.10 or newer
- Git
- Internet access for the first-time Python package installation
- A SQLCipher-enabled `sqlcipher3` installation if you use an encrypted database

The pinned Python packages are listed in `requirements.txt`. The app creates its
database and tables on startup. A new installation has no case records; add
records through the application or load an authorized dataset after signing in.

## Clone the repository

```powershell
git clone https://github.com/DessDagondon/lokalhealth.git
cd lokalhealth
```

## Create a Python environment

Using a virtual environment keeps the app's packages separate from other Python
projects.

### Windows PowerShell

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell cannot find `py`, install Python from python.org and ensure the
Python launcher is available. In Command Prompt, activate the environment with:

```bat
.\.venv\Scripts\activate.bat
```

### macOS or Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## Install dependencies

With the virtual environment activated:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The project includes `sqlcipher3` for encrypted database support. If its
installation fails on your operating system, install the platform's required
SQLCipher development library and compiler, then retry the requirements
installation.

## Configure the database

The database is stored at `instance/database.db`. Database files and `.env` are
excluded from Git.

For a fresh local database without encryption, no environment file is required:
the app creates a regular SQLite database when it starts.

To use SQLCipher encryption, create a `.env` file in the project root and set a
strong, private passphrase:

```dotenv
DB_PASSPHRASE=replace-this-with-a-long-unique-passphrase
```

Keep the passphrase safe and do not commit or share `.env`. The same passphrase
is required every time the encrypted database is opened. Do not change it after
creating the database unless you deliberately migrate/re-encrypt the database.
An existing encrypted database cannot be opened without its original
`DB_PASSPHRASE`.

## Start the application

From the project root, with the virtual environment activated:

```bash
python app.py
```

On first startup, the app creates the database tables and seeds an administrator
account if one does not already exist:

- **Username:** `admin`
- **Password:** `Admin123!`

These are development seed credentials. Run the application only in a trusted
local environment; it is not configured for public or production deployment.
Open <http://127.0.0.1:5000> in your browser. Stop the development server with
`Ctrl+C`.

## Tests

Run the test suite from the project root with the virtual environment activated:

```bash
python -m unittest discover -s tests -v
```

## Troubleshooting

- **`DB_PASSPHRASE` is missing:** If `instance/database.db` is encrypted, set
  the original passphrase in the root `.env` file before starting the app.
- **SQLCipher driver error:** Confirm `sqlcipher3` installed successfully in the
  active virtual environment and that the platform's SQLCipher build
  prerequisites are installed.
- **Port 5000 is already in use:** Stop the other local process using that port
  before starting the app.
- **Dashboard has no case data:** A new database starts empty. Sign in and add
  or import an authorized dataset.

## Main project files

```text
app.py                  Flask application and database models
requirements.txt        Pinned Python dependencies
templates/              HTML templates for the application
instance/database.db    Local runtime database (created on startup)
tests/                  Automated tests
models/                 Trained model and training script
```
