# LokalHealth Dengue Surveillance

LokalHealth is a Flask web application for dengue case surveillance. It provides
case-record management, a surveillance dashboard, weekly trend and forecast
views, barangay summaries, reports, and basic user administration.

This guide covers downloading the source, installing the required software and
Python packages, and starting the application. Downloading the ZIP gives you
the source files only; it does not install Python, Flask, or the other packages
the application uses.

## Requirements

- 64-bit Python 3.10 or newer (Python 3.12 is recommended)
- Git, only if you use the clone instructions instead of downloading a ZIP
- Internet access for the first-time Python package installation

Install Python from [python.org](https://www.python.org/downloads/windows/). In
the installer, enable **Add python.exe to PATH**. The Python launcher (`py`) is
also used in the commands below. If `py` is not recognized, rerun the Python
installer and enable the launcher, or use the `python` command if it is
available.

The pinned Python packages, including Flask, are listed in `requirements.txt`.
They must be installed in the virtual environment before the app is started.
The app creates its database and tables on startup. A new installation has no
case records; add records through the application or load an authorized dataset
after signing in.

## Download and extract the source

Choose one method:

- **ZIP (no Git required):** Open the
  [LokalHealth GitHub repository](https://github.com/DessDagondon/lokalhealth),
  select **Code** > **Download ZIP**, then extract the ZIP using File Explorer.
  Do not run the app from inside the ZIP preview.
- **Git:** Clone the repository using the commands below.

Open PowerShell in the extracted project folder (or cloned folder). It must be
the folder containing `app.py` and `requirements.txt`. For example, if the ZIP
was extracted to Downloads:

```powershell
cd "$HOME\Downloads\lokalhealth-main"
Get-ChildItem app.py, requirements.txt
```

If extraction created a second nested `lokalhealth-main` folder, change into
that inner folder instead. `Get-ChildItem` should list both files before you
continue.

### About `.gitignore` in a ZIP download

`.gitignore` is a tracked project file and is not excluded by its own rules.
The GitHub ZIP normally includes it. If it is not visible in the extracted
folder, you can download the
[`.gitignore` file directly](https://raw.githubusercontent.com/DessDagondon/lokalhealth/main/.gitignore).
The file only tells Git which local files not to track; it is not needed to
install dependencies or run the application. In particular, it does not contain
Flask or any other program dependency.

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

If PowerShell prevents activation because script execution is disabled, allow
activation for this PowerShell process and try again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Alternatively, in Command Prompt (not PowerShell), create and activate the
environment with:

```bat
py -3 -m venv .venv
.\.venv\Scripts\activate.bat
```

### macOS or Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## Install dependencies

With the virtual environment activated:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip show Flask
```

The `pip show Flask` command should print the installed package details. Use
`python -m pip` (rather than a bare `pip`) so packages are installed for the
same Python interpreter that will run the app. The `requirements.txt` install
includes Flask and all other application dependencies, including `sqlcipher3`
for encrypted database support.

If installation reports an error, do not continue to start the app: resolve the
package installation error first. Keep the complete error output, especially if
it names `sqlcipher3`, as its native build prerequisites vary by operating
system.

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

From the project root, with the virtual environment activated and dependencies
installed:

```powershell
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

```powershell
python -m unittest discover -s tests -v
```

## Troubleshooting

- **`ModuleNotFoundError: No module named 'flask'`:** Flask is not installed
  into the Python environment running the app. From the project folder, activate
  the environment and install the requirements:

  ```powershell
  .\.venv\Scripts\Activate.ps1
  python -m pip install -r requirements.txt
  python -m pip show Flask
  python app.py
  ```

  If you already see `(.venv)` in the prompt but still get this error, the
  environment may simply not have had its packages installed. `python -m pip`
  ensures installation uses the active `python`. Check which interpreter is
  active with `python -c "import sys; print(sys.executable)"`; its path should
  be inside this project's `.venv` directory. If the `pip install` command
  fails, fix that installation error before retrying `python app.py`.
- **`py` or `python` is not recognized:** Install 64-bit Python from
  [python.org](https://www.python.org/downloads/windows/), enable the Python
  launcher and **Add python.exe to PATH**, then close and reopen PowerShell.
- **`requirements.txt` is not found:** Change directory to the extracted
  project folder containing both `app.py` and `requirements.txt`. Do not run
  setup commands from the ZIP file itself or an outer folder.
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
