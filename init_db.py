"""
init_db.py — Run this once to create all tables on your Supabase/PostgreSQL database.

Usage:
    DATABASE_URL=postgresql://... python init_db.py
"""

import os
import psycopg2

def init_db():
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not set.")

    conn = psycopg2.connect(database_url)
    cur = conn.cursor()

    # Main transactions table
    cur.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id                SERIAL PRIMARY KEY,
            timestamp         TEXT NOT NULL,
            transaction_type  TEXT NOT NULL,
            foreign_currency  TEXT,
            foreign_amount    REAL NOT NULL,
            rwf_amount        REAL NOT NULL,
            rate_used         REAL NOT NULL,
            profit            REAL NOT NULL,
            fee               REAL NOT NULL DEFAULT 0,
            client_name       TEXT NOT NULL
        )
    ''')

    # FIFO batches table
    cur.execute('''
        CREATE TABLE IF NOT EXISTS currency_batches (
            id               SERIAL PRIMARY KEY,
            transaction_id   INTEGER NOT NULL,
            timestamp        TEXT NOT NULL,
            currency         TEXT NOT NULL,
            original_amount  REAL NOT NULL,
            remaining        REAL NOT NULL,
            sell_rate        REAL NOT NULL
        )
    ''')

    # Debt table — tracks debt per currency when batches are empty
    cur.execute('''
        CREATE TABLE IF NOT EXISTS currency_debts (
            id               SERIAL PRIMARY KEY,
            transaction_id   INTEGER NOT NULL,
            timestamp        TEXT NOT NULL,
            currency         TEXT NOT NULL,
            debt_amount      REAL NOT NULL,
            buy_rate_at_debt REAL NOT NULL,
            remaining_debt   REAL NOT NULL
        )
    ''')

    # Batch consumption log — records how each transaction consumed batches
    cur.execute('''
        CREATE TABLE IF NOT EXISTS batch_consumption_log (
            id               SERIAL PRIMARY KEY,
            transaction_id   INTEGER NOT NULL,
            batch_id         INTEGER NOT NULL,
            consumed_amount  REAL NOT NULL
        )
    ''')

    # Debt payment log — records how each batch addition paid off debts
    cur.execute('''
        CREATE TABLE IF NOT EXISTS debt_payment_log (
            id               SERIAL PRIMARY KEY,
            transaction_id   INTEGER NOT NULL,
            debt_id          INTEGER NOT NULL,
            paid_amount      REAL NOT NULL
        )
    ''')

    conn.commit()
    cur.close()
    conn.close()
    print("All tables created successfully.")


if __name__ == '__main__':
    init_db()
