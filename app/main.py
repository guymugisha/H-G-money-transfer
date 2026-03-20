from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent / '.env')
import os
import io
import json
import psycopg2
import psycopg2.extras
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from fpdf import FPDF

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'hg_money_transfer_secret_key')

# Paths for JSON files — rates and balances stay as flat files
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get('DATA_DIR', os.path.join(BASE_DIR, 'data'))
RATES_FILE = os.path.join(DATA_DIR, 'rates.json')
BALANCES_FILE = os.path.join(DATA_DIR, 'balances.json')

os.makedirs(DATA_DIR, exist_ok=True)

USERS = {
    "admin": {"password": generate_password_hash("admin123"), "role": "admin"},
    "staff": {"password": generate_password_hash("staff123"), "role": "staff"}
}

def get_db():
    url = os.environ.get('DATABASE_URL')
    if not url:
        raise RuntimeError("DATABASE_URL environment variable not set")
    return psycopg2.connect(url)

def get_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# ──────────────────────────────────────────────
# FIFO + DEBT HELPERS
# ──────────────────────────────────────────────

def get_total_debt(conn, currency):
    """Get total remaining debt for a currency."""
    with get_cursor(conn) as cur:
        cur.execute('''
            SELECT SUM(remaining_debt) as total FROM currency_debts
            WHERE currency = %s AND remaining_debt > 0
        ''', (currency,))
        result = cur.fetchone()
    return result['total'] if result['total'] else 0.0


def add_batch(conn, currency, amount, sell_rate, transaction_id):
    """
    Add incoming foreign currency.
    If debt exists, pay it off first before creating a real batch.
    Profit is calculated on debt repayment using the debt's buy rate.
    Returns profit generated from paying off debt.
    """
    profit_from_debt = 0.0
    remaining_to_add = amount

    # Check for existing debts (oldest first)
    with get_cursor(conn) as cur:
        cur.execute('''
            SELECT * FROM currency_debts
            WHERE currency = %s AND remaining_debt > 0
            ORDER BY id ASC
        ''', (currency,))
        debts = cur.fetchall()

    for debt in debts:
        if remaining_to_add <= 0:
            break

        debt_id = debt['id']
        debt_remaining = debt['remaining_debt']
        buy_rate_at_debt = debt['buy_rate_at_debt']

        # How much of this debt we can pay now
        paid = min(debt_remaining, remaining_to_add)

        # Profit = paid × (buy_rate_when_debt_was_created - current_sell_rate)
        profit_from_debt += paid * (buy_rate_at_debt - sell_rate)

        with get_cursor(conn) as cur:
            cur.execute('''
                UPDATE currency_debts SET remaining_debt = %s WHERE id = %s
            ''', (debt_remaining - paid, debt_id))
            cur.execute('''
                INSERT INTO debt_payment_log (transaction_id, debt_id, paid_amount)
                VALUES (%s, %s, %s)
            ''', (transaction_id, debt_id, paid))

        remaining_to_add -= paid

    # Whatever is left after paying debts becomes a real batch
    if remaining_to_add > 0:
        with get_cursor(conn) as cur:
            cur.execute('''
                INSERT INTO currency_batches
                    (transaction_id, timestamp, currency, original_amount, remaining, sell_rate)
                VALUES (%s, %s, %s, %s, %s, %s)
            ''', (transaction_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                  currency, remaining_to_add, remaining_to_add, sell_rate))

    return profit_from_debt


def consume_batches(conn, currency, amount_needed, buy_rate, transaction_id):
    """
    Consume FIFO batches for a given currency.
    If batches run out, create a debt entry instead of using a fallback.
    Returns total profit in RWF calculated across all consumed batches.
    """
    with get_cursor(conn) as cur:
        cur.execute('''
            SELECT * FROM currency_batches
            WHERE currency = %s AND remaining > 0
            ORDER BY id ASC
        ''', (currency,))
        batches = cur.fetchall()

    total_profit = 0.0
    remaining_needed = amount_needed

    for batch in batches:
        if remaining_needed <= 0:
            break

        batch_id = batch['id']
        batch_remaining = batch['remaining']
        batch_sell_rate = batch['sell_rate']

        consumed = min(batch_remaining, remaining_needed)

        # Profit = consumed × (buy_rate - sell_rate of this batch)
        total_profit += consumed * (buy_rate - batch_sell_rate)

        with get_cursor(conn) as cur:
            cur.execute('''
                UPDATE currency_batches SET remaining = %s WHERE id = %s
            ''', (batch_remaining - consumed, batch_id))
            cur.execute('''
                INSERT INTO batch_consumption_log (transaction_id, batch_id, consumed_amount)
                VALUES (%s, %s, %s)
            ''', (transaction_id, batch_id, consumed))

        remaining_needed -= consumed

    # If batches exhausted, create a debt instead of a fallback
    if remaining_needed > 0:
        with get_cursor(conn) as cur:
            cur.execute('''
                INSERT INTO currency_debts
                    (transaction_id, timestamp, currency, debt_amount, buy_rate_at_debt, remaining_debt)
                VALUES (%s, %s, %s, %s, %s, %s)
            ''', (
                transaction_id,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                currency,
                remaining_needed,
                buy_rate,
                remaining_needed
            ))
        # No profit on debt portion — profit will come when debt is repaid

    return total_profit

# ──────────────────────────────────────────────
# JSON HELPERS
# ──────────────────────────────────────────────

def load_json(filepath, defaults):
    if not os.path.exists(filepath):
        with open(filepath, 'w') as f:
            json.dump(defaults, f, indent=4)
        return defaults
    with open(filepath, 'r') as f:
        return json.load(f)

def save_json(filepath, data):
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=4)

def load_rates():
    return load_json(RATES_FILE, {
        "USD": {"sell_rate": 1440.0, "buy_rate": 1485.0},
        "CNY": {"sell_rate": 200.0, "buy_rate": 210.0},
        "CAD": {"sell_rate": 1050.0, "buy_rate": 1080.0},
        "USD_CAD": {"sell_rate": 135.0, "buy_rate": 145.0},
        "USD_CNY": {"sell_rate": 7.2, "buy_rate": 7.6},
        "usd_transfer_fee": 5.0,
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

def load_balances():
    return load_json(BALANCES_FILE, {
        "usd_balance": 10000.0,
        "rwf_balance": 15000000.0,
        "cny_balance": 0.0,
        "cad_balance": 0.0,
        "usd_rwanda_balance": 0.0,
        "total_profit_rwf": 0.0,
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

# ──────────────────────────────────────────────
# ROUTES
# ──────────────────────────────────────────────

@app.route('/')
def index():
    if 'user' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = USERS.get(username)
        if user and check_password_hash(user['password'], password):
            session['user'] = username
            session['role'] = user['role']
            return redirect(url_for('dashboard'))
        flash('Invalid username or password', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ──────────────────────────────────────────────
# DASHBOARD
# ──────────────────────────────────────────────

@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        return redirect(url_for('login'))

    rates = load_rates()
    balances = load_balances()

    spreads = {
        'USD': rates['USD']['buy_rate'] - rates['USD']['sell_rate'],
        'CNY': rates['CNY']['buy_rate'] - rates['CNY']['sell_rate'],
        'CAD': rates['CAD']['buy_rate'] - rates['CAD']['sell_rate'],
        'USD_CAD': rates['USD_CAD']['buy_rate'] - rates['USD_CAD']['sell_rate'],
        'USD_CNY': rates['USD_CNY']['buy_rate'] - rates['USD_CNY']['sell_rate']
    }

    conn = get_db()
    today = datetime.now().strftime("%Y-%m-%d")

    with get_cursor(conn) as cur:
        cur.execute('''
            SELECT SUM(profit) as total_profit, COUNT(id) as total_count
            FROM transactions
            WHERE timestamp LIKE %s
        ''', (f'{today}%',))
        stats = cur.fetchone()

    daily_profit = stats['total_profit'] if stats['total_profit'] else 0

    # Get current debts per currency for dashboard display
    debts = {}
    for currency in ['USD', 'CNY', 'CAD']:
        debts[currency] = get_total_debt(conn, currency)

    with get_cursor(conn) as cur:
        cur.execute('SELECT * FROM transactions ORDER BY id DESC LIMIT 5')
        recent = cur.fetchall()

    conn.close()

    return render_template('dashboard.html',
                           rates=rates,
                           balances=balances,
                           spreads=spreads,
                           daily_profit=daily_profit,
                           debts=debts,
                           recent=recent)

# ──────────────────────────────────────────────
# CALCULATOR
# ──────────────────────────────────────────────

@app.route('/calculator', methods=['GET', 'POST'])
def calculator():
    if 'user' not in session:
        return redirect(url_for('login'))

    rates = load_rates()
    balances = load_balances()

    if request.method == 'POST':
        tx_type = request.form.get('type')
        amount = float(request.form.get('amount'))
        client_name = request.form.get('client_name', 'Walk-in')

        if amount <= 0:
            flash("Amount must be positive", "error")
            return redirect(url_for('calculator'))

        foreign_currency = ""
        foreign_amount = 0.0
        rwf_amount = 0.0
        rate = 0.0
        profit = 0.0
        fee = 0.0

        conn = get_db()

        # ── RWF HUB PAIRS (USD, CNY, CAD ↔ RWF) ──
        if '_TO_' in tx_type and 'RWF' in tx_type:
            parts = tx_type.split('_TO_')
            from_curr = parts[0]
            to_curr = parts[1]

            if to_curr == 'RWF':
                # FOREIGN → RWF: pay debts first, then create batch
                foreign_currency = from_curr
                foreign_amount = amount
                rate = rates[foreign_currency]['sell_rate']
                rwf_amount = foreign_amount * rate

                if balances['rwf_balance'] < rwf_amount:
                    flash(f"Insufficient RWF balance! Need {rwf_amount:,.0f} RWF", "error")
                    conn.close()
                    return redirect(url_for('calculator'))

                # Insert transaction first to get tx_id for FIFO tracking
                with get_cursor(conn) as cur:
                    cur.execute('''
                        INSERT INTO transactions
                            (timestamp, transaction_type, foreign_currency, foreign_amount,
                             rwf_amount, rate_used, profit, fee, client_name)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                    ''', (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), tx_type,
                          foreign_currency, foreign_amount, rwf_amount, rate, 0.0, fee, client_name))
                    tx_id = cur.fetchone()['id']

                profit = add_batch(conn, foreign_currency, foreign_amount, rate, tx_id)

                with get_cursor(conn) as cur:
                    cur.execute('UPDATE transactions SET profit = %s WHERE id = %s', (profit, tx_id))

                balances[f"{foreign_currency.lower()}_balance"] += foreign_amount
                balances['rwf_balance'] -= rwf_amount

            elif from_curr == 'RWF':
                # RWF → FOREIGN: consume FIFO batches, create debt if needed
                foreign_currency = to_curr
                rwf_amount = amount
                rate = rates[foreign_currency]['buy_rate']
                foreign_amount = rwf_amount / rate

                if balances[f"{foreign_currency.lower()}_balance"] < foreign_amount:
                    flash(f"Insufficient {foreign_currency} balance!", "error")
                    conn.close()
                    return redirect(url_for('calculator'))

                # Insert transaction first to get tx_id for FIFO tracking
                with get_cursor(conn) as cur:
                    cur.execute('''
                        INSERT INTO transactions
                            (timestamp, transaction_type, foreign_currency, foreign_amount,
                             rwf_amount, rate_used, profit, fee, client_name)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                    ''', (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), tx_type,
                          foreign_currency, foreign_amount, rwf_amount, rate, 0.0, fee, client_name))
                    tx_id = cur.fetchone()['id']

                profit = consume_batches(conn, foreign_currency, foreign_amount, rate, tx_id)

                with get_cursor(conn) as cur:
                    cur.execute('UPDATE transactions SET profit = %s WHERE id = %s', (profit, tx_id))

                balances['rwf_balance'] += rwf_amount
                balances[f"{foreign_currency.lower()}_balance"] -= foreign_amount

            balances['total_profit_rwf'] = float(balances.get('total_profit_rwf', 0)) + profit
            balances['last_updated'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_json(BALANCES_FILE, balances)
            conn.commit()
            conn.close()

            flash(f"Transaction successful for {client_name}! {tx_type.replace('_', ' ')}", "success")
            return redirect(url_for('dashboard'))

        # ── USD ↔ CAD (inventory only, no RWF profit) ──
        elif tx_type == 'USD_TO_CAD':
            rate = rates['USD_CAD']['sell_rate']
            foreign_amount = amount
            cad_to_deliver = amount * rate
            rwf_amount = cad_to_deliver
            foreign_currency = 'USD_CAD'
            profit = 0.0

            if balances['cad_balance'] < cad_to_deliver:
                flash("Insufficient CAD balance!", "error")
                conn.close()
                return redirect(url_for('calculator'))

            balances['usd_balance'] += amount
            balances['cad_balance'] -= cad_to_deliver

        elif tx_type == 'CAD_TO_USD':
            rate = rates['USD_CAD']['buy_rate']
            foreign_amount = amount
            usd_to_deliver = amount / rate
            rwf_amount = usd_to_deliver
            foreign_currency = 'USD_CAD'
            profit = 0.0

            if balances['usd_balance'] < usd_to_deliver:
                flash("Insufficient USD balance!", "error")
                conn.close()
                return redirect(url_for('calculator'))

            balances['cad_balance'] += amount
            balances['usd_balance'] -= usd_to_deliver

        # ── USD ↔ CNY (inventory only, no RWF profit) ──
        elif tx_type == 'USD_TO_CNY':
            rate = rates['USD_CNY']['sell_rate']
            foreign_amount = amount
            cny_to_deliver = amount * rate
            rwf_amount = cny_to_deliver
            foreign_currency = 'USD_CNY'
            profit = 0.0

            if balances['cny_balance'] < cny_to_deliver:
                flash("Insufficient CNY balance!", "error")
                conn.close()
                return redirect(url_for('calculator'))

            balances['usd_balance'] += amount
            balances['cny_balance'] -= cny_to_deliver

        elif tx_type == 'CNY_TO_USD':
            rate = rates['USD_CNY']['buy_rate']
            foreign_amount = amount
            usd_to_deliver = amount / rate
            rwf_amount = usd_to_deliver
            foreign_currency = 'USD_CNY'
            profit = 0.0

            if balances['usd_balance'] < usd_to_deliver:
                flash("Insufficient USD balance!", "error")
                conn.close()
                return redirect(url_for('calculator'))

            balances['cny_balance'] += amount
            balances['usd_balance'] -= usd_to_deliver

        # ── USD US → USD RWANDA (fee transaction) ──
        elif tx_type == 'USD_US_TO_USD_RWA':
            foreign_currency = 'USD'
            fee_rate = float(rates['usd_transfer_fee'])
            foreign_amount = float(amount)
            usd_sent = foreign_amount + fee_rate
            profit = 0.0
            fee = fee_rate
            rate = 0.0
            rwf_amount = 0.0

            if balances['usd_rwanda_balance'] < foreign_amount:
                flash(f"Insufficient USD (Rwanda) balance! Need {foreign_amount:,.2f} USD", "error")
                conn.close()
                return redirect(url_for('calculator'))

            balances['usd_balance'] = float(balances['usd_balance']) + usd_sent
            balances['usd_rwanda_balance'] = float(balances['usd_rwanda_balance']) - foreign_amount

        balances['last_updated'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_json(BALANCES_FILE, balances)

        # Record transaction for non-RWF pairs (RWF pairs return early above)
        with get_cursor(conn) as cur:
            cur.execute('''
                INSERT INTO transactions
                    (timestamp, transaction_type, foreign_currency, foreign_amount,
                     rwf_amount, rate_used, profit, fee, client_name)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                tx_type,
                foreign_currency,
                foreign_amount,
                rwf_amount,
                rate,
                profit,
                fee,
                client_name
            ))
        conn.commit()
        conn.close()

        flash(f"Transaction successful for {client_name}! {tx_type.replace('_', ' ')}", "success")
        return redirect(url_for('dashboard'))

    # Pass debts to calculator page so staff can see current debt status
    conn = get_db()
    debts = {}
    for currency in ['USD', 'CNY', 'CAD']:
        debts[currency] = get_total_debt(conn, currency)
    conn.close()

    return render_template('calculator.html', rates=rates, balances=balances, debts=debts)

# ──────────────────────────────────────────────
# RATES MANAGEMENT
# ──────────────────────────────────────────────

@app.route('/rates', methods=['GET', 'POST'])
def rates_settings():
    if 'user' not in session or session['role'] != 'admin':
        flash("Admin access required", "error")
        return redirect(url_for('dashboard'))

    rates = load_rates()

    if request.method == 'POST':
        for curr in ['USD', 'CNY', 'CAD', 'USD_CAD', 'USD_CNY']:
            buy = float(request.form.get(f'{curr.lower()}_buy_rate'))
            sell = float(request.form.get(f'{curr.lower()}_sell_rate'))

            if buy < sell:
                flash(f"Warning: {curr} Buy rate is lower than Sell rate. Profit will be negative!", "warning")

            rates[curr]['buy_rate'] = buy
            rates[curr]['sell_rate'] = sell

        rates['usd_transfer_fee'] = float(request.form.get('usd_transfer_fee'))
        rates['last_updated'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_json(RATES_FILE, rates)
        flash("Exchange rates and fees updated", "success")
        return redirect(url_for('rates_settings'))

    # Pass debts and batches to rates page for admin visibility
    conn = get_db()
    debts = {}
    batches = {}
    for currency in ['USD', 'CNY', 'CAD']:
        debts[currency] = get_total_debt(conn, currency)
        with get_cursor(conn) as cur:
            cur.execute('''
                SELECT * FROM currency_batches
                WHERE currency = %s AND remaining > 0
                ORDER BY id ASC
            ''', (currency,))
            batches[currency] = [dict(b) for b in cur.fetchall()]
    conn.close()

    return render_template('rates.html', rates=rates, debts=debts, batches=batches)

# ──────────────────────────────────────────────
# INVENTORY ADJUSTMENT
# ──────────────────────────────────────────────

@app.route('/inventory/adjust', methods=['POST'])
def adjust_inventory():
    if 'user' not in session or session['role'] != 'admin':
        flash("Admin access required", "error")
        return redirect(url_for('dashboard'))

    currency = request.form.get('currency')
    action = request.form.get('action')
    amount = float(request.form.get('amount'))

    if amount <= 0:
        flash("Amount must be positive", "error")
        return redirect(url_for('rates_settings'))

    balances = load_balances()
    rates = load_rates()

    # Handle profit adjustment separately
    if currency == 'PROFIT_RWF':
        current = float(balances.get('total_profit_rwf', 0))
        if action == 'ADD':
            balances['total_profit_rwf'] = current + amount
        else:
            balances['total_profit_rwf'] = current - amount
        balances['last_updated'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_json(BALANCES_FILE, balances)
        flash(f"Profit adjusted: {action} {amount:,.0f} RWF", "success")
        return redirect(url_for('rates_settings'))

    # Handle regular currency inventory
    if currency in ['USD', 'CNY', 'CAD', 'RWF', 'USD_RWANDA']:
        balance_key = f"{currency.lower()}_balance"

        if currency == 'USD_RWANDA':
            balance_key = 'usd_rwanda_balance'

        if action == 'ADD':
            balances[balance_key] += amount

            # If adding foreign currency manually, treat it like an incoming batch
            # so FIFO and debt tracking stay accurate
            if currency in ['USD', 'CNY', 'CAD']:
                conn = get_db()
                sell_rate = rates.get(currency, {}).get('sell_rate', 0)
                add_batch(conn, currency, amount, sell_rate, transaction_id=0)
                conn.commit()
                conn.close()
        else:
            if balances[balance_key] < amount:
                flash(f"Insufficient {currency} in inventory!", "error")
                return redirect(url_for('rates_settings'))
            balances[balance_key] -= amount

    balances['last_updated'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_json(BALANCES_FILE, balances)
    flash(f"Inventory updated: {action} {amount} {currency}", "success")
    return redirect(url_for('rates_settings'))

# ──────────────────────────────────────────────
# TRANSACTION HISTORY
# ──────────────────────────────────────────────

@app.route('/transactions')
def transactions_history():
    if 'user' not in session:
        return redirect(url_for('login'))

    date_filter = request.args.get('date')
    currency_filter = request.args.get('currency')

    conn = get_db()
    query = 'SELECT * FROM transactions WHERE 1=1'
    params = []

    if date_filter:
        query += ' AND timestamp LIKE %s'
        params.append(f'{date_filter}%')

    if currency_filter:
        query += ' AND foreign_currency = %s'
        params.append(currency_filter)

    query += ' ORDER BY id DESC'

    with get_cursor(conn) as cur:
        cur.execute(query, params)
        transactions = cur.fetchall()

    total_profit = sum(t['profit'] for t in transactions)
    conn.close()

    return render_template('transactions.html',
                           transactions=transactions,
                           total_profit=total_profit,
                           selected_date=date_filter,
                           selected_currency=currency_filter)

# ──────────────────────────────────────────────
# REPORTS — PDF generated in-memory, returned as download
# ──────────────────────────────────────────────

class ReportPDF(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 16)
        self.set_text_color(26, 35, 126)
        self.cell(0, 10, 'H&G Money Transfer - Monthly Forex Report', 0, 1, 'C')
        self.ln(10)

    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, f'Page {self.page_no()}', 0, 0, 'C')


@app.route('/reports')
def monthly_reports():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('reports.html', reports=[])


@app.route('/reports/generate', methods=['POST'])
def generate_report():
    if 'user' not in session or session['role'] != 'admin':
        flash("Admin access required", "error")
        return redirect(url_for('monthly_reports'))

    rates = load_rates()
    now = datetime.now()
    conn = get_db()

    with get_cursor(conn) as cur:
        cur.execute('SELECT * FROM transactions ORDER BY timestamp ASC')
        transactions = cur.fetchall()

    if not transactions:
        conn.close()
        flash("No transactions to report.", "warning")
        return redirect(url_for('monthly_reports'))

    # ── CALCULATIONS ──
    currencies = ['USD', 'CNY', 'CAD']
    stats = {curr: {'volume': 0, 'rwf': 0, 'profit': 0} for curr in currencies}

    indep_pairs = ['USD_CAD', 'USD_CNY']
    indep_stats = {pair: {'usd_vol': 0, 'other_vol': 0} for pair in indep_pairs}

    total_profit_rwf = 0
    total_fees_usd = 0

    for tx in transactions:
        curr = tx['foreign_currency']
        if curr in stats:
            stats[curr]['volume'] += tx['foreign_amount']
            stats[curr]['rwf'] += tx['rwf_amount']
            stats[curr]['profit'] += tx['profit']
        elif curr in indep_stats:
            if tx['transaction_type'] in ['USD_TO_CAD', 'USD_TO_CNY']:
                indep_stats[curr]['usd_vol'] += tx['foreign_amount']
                indep_stats[curr]['other_vol'] += tx['rwf_amount']
            elif tx['transaction_type'] in ['CAD_TO_USD', 'CNY_TO_USD']:
                indep_stats[curr]['usd_vol'] += tx['rwf_amount']
                indep_stats[curr]['other_vol'] += tx['foreign_amount']

        total_profit_rwf += tx['profit']
        total_fees_usd += tx['fee']

    # Get pending debts
    pending_debts = {}
    for curr in currencies:
        pending_debts[curr] = get_total_debt(conn, curr)

    # ── PDF CREATION (in-memory) ──
    pdf = ReportPDF()
    pdf.add_page()
    pdf.set_font('Arial', '', 12)

    pdf.cell(0, 10, f"Period: {now.strftime('%B %Y')}", 0, 1)
    pdf.cell(0, 10, f"Generated On: {now.strftime('%Y-%m-%d %H:%M')}", 0, 1)
    pdf.ln(5)

    pdf.set_font('Arial', 'B', 14)
    pdf.cell(0, 10, "Summary by Currency Pair", 0, 1)
    pdf.ln(2)

    pdf.set_fill_color(232, 232, 232)
    pdf.set_font('Arial', 'B', 10)
    pdf.cell(30, 10, "Pair", 1, 0, 'C', True)
    pdf.cell(50, 10, "Foreign Volume", 1, 0, 'C', True)
    pdf.cell(60, 10, "RWF Equivalent", 1, 0, 'C', True)
    pdf.cell(50, 10, "Realized Profit", 1, 1, 'C', True)

    pdf.set_font('Arial', '', 10)
    for curr in currencies:
        pdf.cell(30, 10, f"{curr}-RWF", 1, 0, 'C')
        pdf.cell(50, 10, f"{stats[curr]['volume']:,.2f} {curr}", 1, 0, 'R')
        pdf.cell(60, 10, f"{stats[curr]['rwf']:,.0f} RWF", 1, 0, 'R')
        pdf.cell(50, 10, f"{stats[curr]['profit']:,.0f} RWF", 1, 1, 'R')

    pdf.ln(5)
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(0, 10, "Summary by Independent Pairs", 0, 1)

    pdf.set_fill_color(232, 232, 232)
    pdf.set_font('Arial', 'B', 10)
    pdf.cell(40, 10, "Pair", 1, 0, 'C', True)
    pdf.cell(75, 10, "Total USD Exchanged", 1, 0, 'C', True)
    pdf.cell(75, 10, "Total Other Exchanged", 1, 1, 'C', True)

    pdf.set_font('Arial', '', 10)
    for pair in indep_pairs:
        dest = pair.split('_')[1]
        pdf.cell(40, 10, f"USD <-> {dest}", 1, 0, 'C')
        pdf.cell(75, 10, f"{indep_stats[pair]['usd_vol']:,.2f} USD", 1, 0, 'R')
        pdf.cell(75, 10, f"{indep_stats[pair]['other_vol']:,.2f} {dest}", 1, 1, 'R')

    pdf.ln(5)
    pdf.set_font('Arial', 'B', 12)
    pdf.set_text_color(46, 125, 50)
    pdf.cell(95, 12, "TOTAL COMBINED PROFIT (RWF)", 1, 0, 'L', False)
    pdf.cell(95, 12, f"{total_profit_rwf:,.0f} RWF", 1, 1, 'L', False)

    pdf.set_text_color(26, 35, 126)
    pdf.cell(95, 12, "TOTAL TRANSFER FEES (USD)", 1, 0, 'L', False)
    pdf.cell(95, 12, f"${total_fees_usd:,.2f} USD", 1, 1, 'L', False)
    pdf.set_text_color(0, 0, 0)

    has_debts = any(v > 0 for v in pending_debts.values())
    if has_debts:
        pdf.ln(5)
        pdf.set_font('Arial', 'B', 12)
        pdf.set_text_color(220, 50, 50)
        pdf.cell(0, 10, "Pending Debts Forwarded to Next Month", 0, 1)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font('Arial', 'B', 10)
        pdf.set_fill_color(255, 235, 235)
        pdf.cell(60, 10, "Currency", 1, 0, 'C', True)
        pdf.cell(130, 10, "Amount Owed (Forwarded)", 1, 1, 'C', True)
        pdf.set_font('Arial', '', 10)
        for curr, debt in pending_debts.items():
            if debt > 0:
                pdf.cell(60, 10, curr, 1, 0, 'C')
                pdf.cell(130, 10, f"{debt:,.4f} {curr}", 1, 1, 'R')

    pdf.ln(10)
    pdf.set_font('Arial', 'B', 14)
    pdf.cell(0, 10, "Detailed Transaction Log", 0, 1)
    pdf.ln(2)

    col_widths = [25, 35, 30, 30, 15, 30, 25]
    headers = ["Date", "Type", "Foreign Amt", "RWF Amt", "Rate", "Profit/Fee", "Client"]

    pdf.set_font('Arial', 'B', 8)
    pdf.set_fill_color(26, 35, 126)
    pdf.set_text_color(255, 255, 255)
    for i in range(len(headers)):
        pdf.cell(col_widths[i], 8, headers[i], 1, 0, 'C', True)
    pdf.ln()

    pdf.set_text_color(0, 0, 0)
    pdf.set_font('Arial', '', 7)

    for tx in transactions:
        pdf.cell(col_widths[0], 8, tx['timestamp'].split(' ')[0], 1)
        pdf.cell(col_widths[1], 8, tx['transaction_type'].replace('_', ' ')[:18], 1)

        if tx['transaction_type'] in ['USD_TO_CAD', 'USD_TO_CNY']:
            f_amt = f"{tx['foreign_amount']:,.2f} USD"
            r_amt = f"{tx['rwf_amount']:,.2f} {tx['transaction_type'].split('_')[2]}"
        elif tx['transaction_type'] in ['CAD_TO_USD', 'CNY_TO_USD']:
            f_amt = f"{tx['foreign_amount']:,.2f} {tx['transaction_type'].split('_')[0]}"
            r_amt = f"{tx['rwf_amount']:,.2f} USD"
        else:
            f_amt = f"{tx['foreign_amount']:,.2f} {tx['foreign_currency']}"
            r_amt = f"{tx['rwf_amount']:,.0f}"

        pdf.cell(col_widths[2], 8, f_amt, 1, 0, 'R')
        pdf.cell(col_widths[3], 8, r_amt, 1, 0, 'R')
        pdf.cell(col_widths[4], 8, f"{tx['rate_used'] if tx['rate_used'] > 0 else '-'}", 1, 0, 'R')

        if tx['transaction_type'] == 'USD_US_TO_USD_RWA':
            pf_val = f"${tx['fee']:,.2f}"
        elif tx['transaction_type'] in ['USD_TO_CAD', 'CAD_TO_USD', 'USD_TO_CNY', 'CNY_TO_USD']:
            pf_val = "-"
        else:
            pf_val = f"{tx['profit']:,.0f} R"

        pdf.cell(col_widths[5], 8, pf_val, 1, 0, 'R')
        pdf.cell(col_widths[6], 8, tx['client_name'][:15], 1, 1, 'C')

    # ── RESET DATABASE — keep debts, clear everything else ──
    with conn.cursor() as cur:
        cur.execute('DELETE FROM transactions')
        cur.execute('DELETE FROM currency_batches')
        cur.execute('DELETE FROM batch_consumption_log')
        cur.execute('DELETE FROM debt_payment_log')
        # currency_debts stays — pending debts carry forward to next month

    conn.commit()
    conn.close()

    # Reset cumulative profit
    balances = load_balances()
    balances['total_profit_rwf'] = 0.0
    balances['last_updated'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_json(BALANCES_FILE, balances)

    # Return PDF as immediate download
    report_month = now.strftime("%B_%Y")
    report_timestamp = now.strftime("%H%M%S")
    filename = f"HG_Monthly_Report_{report_month}_{report_timestamp}.pdf"

    pdf_bytes = pdf.output(dest='S')
    if isinstance(pdf_bytes, str):
        pdf_bytes = pdf_bytes.encode('latin-1')

    buf = io.BytesIO(pdf_bytes)
    buf.seek(0)

    return send_file(
        buf,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename
    )


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
