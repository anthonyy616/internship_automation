import sqlite3

def init_db(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT,
            title TEXT,
            url TEXT UNIQUE,
            status TEXT DEFAULT 'found',
            contact_email TEXT,
            notes TEXT
        )
    ''')
    conn.commit()
    return conn

def log_job(conn, job_data):
    try:
        conn.execute('''
            INSERT INTO jobs (company, title, url, contact_email, notes)
            VALUES (?, ?, ?, ?, ?)
        ''', (job_data['company'], job_data['title'], job_data['url'], job_data.get('email'), job_data.get('summary')))
        conn.commit()
    except sqlite3.IntegrityError:
        pass # Already exists