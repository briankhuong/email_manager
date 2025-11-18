import sqlite3
import os

def migrate_database():
    """Add new columns to existing database"""
    db_file = 'accounts.db'
    
    if not os.path.exists(db_file):
        print("No database file found - will be created automatically")
        return
    
    conn = sqlite3.connect(db_file)
    c = conn.cursor()
    
    # Check if new columns already exist
    c.execute("PRAGMA table_info(accounts)")
    columns = [column[1] for column in c.fetchall()]
    
    # Add missing columns
    new_columns = [
        'date_added',
        'login_count', 
        'failure_count',
        'proxy_slot',
        'account_status'
    ]
    
    for column in new_columns:
        if column not in columns:
            print(f"Adding column: {column}")
            if column == 'date_added':
                c.execute(f'ALTER TABLE accounts ADD COLUMN {column} DATETIME DEFAULT CURRENT_TIMESTAMP')
            elif column in ['login_count', 'failure_count']:
                c.execute(f'ALTER TABLE accounts ADD COLUMN {column} INTEGER DEFAULT 0')
            elif column == 'proxy_slot':
                c.execute(f'ALTER TABLE accounts ADD COLUMN {column} TEXT')
            elif column == 'account_status':
                c.execute(f'ALTER TABLE accounts ADD COLUMN {column} TEXT DEFAULT "active"')
    
    conn.commit()
    conn.close()
    print("Database migration completed successfully!")

if __name__ == '__main__':
    migrate_database()