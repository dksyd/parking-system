import os
import sqlite3

# 실행 위치에 상관없이 항상 backend/parking.db를 가리키도록 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "parking.db")


def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    # 동시 읽기/쓰기 성능 향상 및 잠금 최소화
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # ocr_status: NONE(미확인), MATCH(일치), MISMATCH(불일치), UNCERTAIN(인식불확실), UNAUTHORIZED(미예약차량)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS parking_spaces (
            space_id TEXT PRIMARY KEY,
            is_reserved INTEGER DEFAULT 0,
            is_occupied INTEGER DEFAULT 0,
            car_number TEXT,
            ocr_status TEXT DEFAULT 'NONE',
            detected_car_number TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    spaces = ['A1', 'A2', 'A3', 'A4', 'A5']
    for space in spaces:
        cursor.execute('''
            INSERT OR IGNORE INTO parking_spaces 
            (space_id, is_reserved, is_occupied, car_number, ocr_status, detected_car_number)
            VALUES (?, 0, 0, NULL, 'NONE', NULL)
        ''', (space,))

    conn.commit()
    conn.close()
    print(f"Database initialized successfully at: {DB_PATH}")