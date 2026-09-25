3.2.6 Version Control, Repository Structure, & Reproducibility
The project repository follows a modular, production-ready directory layout separating configuration, application logic, secure storage, and testing fixtures.

Repository File Structure
Plaintext
lokalhealth-dengue-surveillance/
│
├── .env.example                 # Template for environment variables (DB_PASSPHRASE)
├── .gitignore                   # Excludes instance/ database files and cache from git
├── requirements.txt             # Pinned package dependencies
├── README.md                    # Comprehensive setup and user manual
├── app.py                       # Main application entry point (Flask + SQLCipher + ML API)
│
├── instance/
│   └── database.db              # Encrypted SQLite/SQLCipher database (runtime ignored in git)
│
├── templates/                   # Jinja2 HTML templates
│   ├── login.html
│   ├── dashboard.html
│   └── change_password.html
│
└── tests/                       # Unit and integration test suite
    └── test_surveillance.py
Setup Instructions & Minimal Working Example (MWE)
To clone, configure, and run a Minimal Working Example (MWE) of the LokalHealth surveillance platform locally:

Clone the Repository & Navigate:

Bash
git clone https://github.com/lokalhealth/dengue-surveillance.git
cd dengue-surveillance
Create and Activate a Virtual Environment:

Bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
Install Dependencies (requirements.txt):

Bash
pip install flask flask-sqlalchemy flask-login scikit-learn pandas sqlcipher3 python-dotenv
Configure Environment Variables:
Create a .env file in the root directory and define your secure SQLCipher passphrase:

Code snippet
DB_PASSPHRASE="your-secure-sqlcipher-passphrase-2026"
Run the Minimal Working Example (MWE):

Bash
python app.py
Access the application in your browser at http://127.0.0.1:5000. The system automatically seeds the primary administrator account (admin / Admin123!) on initial launch.