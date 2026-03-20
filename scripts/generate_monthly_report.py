import os
import json
from datetime import datetime
from fpdf import FPDF
import psycopg2
import psycopg2.extras

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPTS_DIR)
DATA_DIR = os.environ.get('DATA_DIR', os.path.join(BASE_DIR, 'data'))
RATES_FILE = os.path.join(DATA_DIR, 'rates.json')
BALANCES_FILE = os.path.join(DATA_DIR, 'balances.json')


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


def reset_profit():
    """Reset cumulative profit in balances.json to 0."""
    if os.path.exists(BALANCES_FILE):
        with open(BALANCES_FILE, 'r') as f:
            balances = json.load(f)
        balances['total_profit_rwf'] = 0.0
        balances['last_updated'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(BALANCES_FILE, 'w') as f:
            json.dump(balances, f, indent=4)
        print("Cumulative profit reset to 0 for the new month.")
    else:
        print("Warning: balances.json not found, profit not reset.")


def generate_report():
    now = datetime.now()

    if not os.path.exists(RATES_FILE):
        print("Rates file not found. Cannot generate report.")
        return

    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        print("Error: DATABASE_URL environment variable not set.")
        return

    conn = psycopg2.connect(database_url)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Get all transactions
    cur.execute('SELECT * FROM transactions ORDER BY timestamp ASC')
    transactions = cur.fetchall()

    if not transactions:
        print("No transactions to report.")
        cur.close()
        conn.close()
        reset_profit()
        return

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
        cur.execute('''
            SELECT SUM(remaining_debt) as total FROM currency_debts
            WHERE currency = %s AND remaining_debt > 0
        ''', (curr,))
        result = cur.fetchone()
        pending_debts[curr] = result['total'] if result['total'] else 0.0

    # ── PDF CREATION ──
    report_month = now.strftime("%B_%Y")
    report_timestamp = now.strftime("%H%M%S")
    reports_dir = os.path.join(BASE_DIR, 'reports', 'monthly_reports')
    os.makedirs(reports_dir, exist_ok=True)
    report_filename = f"HG_Monthly_Report_{report_month}_{report_timestamp}.pdf"
    report_path = os.path.join(reports_dir, report_filename)

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

    pdf.output(report_path)
    print(f"Report generated: {report_path}")

    # ── RESET DATABASE — keep debts, clear everything else ──
    cur.execute('DELETE FROM transactions')
    cur.execute('DELETE FROM currency_batches')
    cur.execute('DELETE FROM batch_consumption_log')
    cur.execute('DELETE FROM debt_payment_log')
    # currency_debts stays — pending debts carry forward to next month
    conn.commit()
    cur.close()
    conn.close()
    print("Database reset for the new month (debts forwarded).")

    # ── RESET CUMULATIVE PROFIT ──
    reset_profit()

    print(f"\nMonthly report complete for {now.strftime('%B %Y')}")
    print(f"   Total Profit : {total_profit_rwf:,.0f} RWF")
    print(f"   Total Fees   : ${total_fees_usd:,.2f} USD")
    if has_debts:
        print("   Pending debts forwarded:")
        for curr, debt in pending_debts.items():
            if debt > 0:
                print(f"      {curr}: {debt:,.4f} {curr} owed")
    print(f"   Report saved : {report_filename}")


if __name__ == "__main__":
    generate_report()
