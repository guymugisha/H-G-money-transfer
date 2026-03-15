# H&G Money Transfer System

A simple, secure, and professional local web application for a small money transfer business.

## Features
- **Login System**: Secure access for Admin and Staff.
- **Dashboard**: Real-time stats and current rates.
- **Exchange Calculator**: Multi-currency support with automatic profit calculation.
- **Rate Management**: Admin-only page to update market and business rates.
- **Transaction History**: Searchable and filterable history of all trades.
- **Monthly Reports**: Automatic PDF generator and monthly data archival.

## Installation & Setup
1. **Requirements**:
   - Python 3.x installed.
   - Flask library: `pip install flask`
   - PDF library: `pip install fpdf2`

2. **Run the Application**:
   - Double-click `run_app.bat` OR run `python app/main.py` from the root directory.
   - Open your browser and go to: `http://localhost:5000`

3. **Generate Monthly Reports**:
   - Double-click `generate_report.bat` OR run `python scripts/generate_monthly_report.py`.
   - This will create a PDF in `reports/monthly_reports/`, archive the current database, and reset for the new month.

## Default Accounts
- **Admin**:
  - Username: `admin`
  - Password: `admin123`
- **Staff**:
  - Username: `staff`
  - Password: `staff123`

## File Structure
- `app/`: Contains the Flask application logic, templates, and styling.
- `data/`: Stores the rates JSON and the SQLite database.
- `reports/`: Stores the generated PDF reports.
- `scripts/`: Python scripts for backend processing and reporting.

## Security Features
- **Password Hashing**: Passwords are not stored in plain text.
- **Session Management**: Roles are enforced per user.
- **Input Validation**: Prevents negative amounts and invalid transactions.
- **Offline Integrity**: Runs fully locally without needing an internet connection.
