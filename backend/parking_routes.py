import re
from datetime import datetime
from flask import Blueprint, jsonify, request
from database import get_db_connection

parking_bp = Blueprint('parking', __name__)

VALID_SPACES = {'A1', 'A2', 'A3', 'A4', 'A5'}


def get_now_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def normalize_car_number(car_number) -> str:
    """차량번호 공백 제거 및 문자열 변환 (예: ' 1234 ' -> '1234')"""
    return re.sub(r'\s+', '', str(car_number)).strip()


def is_valid_car_number(car_number: str) -> bool:
    """차량번호 형식 검증: 숫자 4자리만 허용 (예: '0123', '4567')"""
    return bool(re.match(r'^\d{4}$', car_number))


# 1. 전체 주차면 상태 조회 API (GET /api/parking)
@parking_bp.route('/api/parking', methods=['GET'])
def get_parking_status():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT 
                space_id,
                is_reserved,
                is_occupied,
                car_number,
                ocr_status,
                detected_car_number,
                updated_at
            FROM parking_spaces
            ORDER BY space_id ASC
        ''')
        rows = cursor.fetchall()

        spaces = []
        for row in rows:
            spaces.append({
                "space_id": row["space_id"],
                "is_reserved": bool(row["is_reserved"]),
                "is_occupied": bool(row["is_occupied"]),
                "car_number": row["car_number"],
                "ocr_status": row["ocr_status"],
                "detected_car_number": row["detected_car_number"],
                "updated_at": row["updated_at"]
            })

        return jsonify({
            "status": "success",
            "data": spaces
        }), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"주차면 상태 조회 실패: {str(e)}"
        }), 500
    finally:
        if conn:
            conn.close()


# 2. 주차 예약 API (POST /api/reservations)
@parking_bp.route('/api/reservations', methods=['POST'])
def create_reservation():
    data = request.get_json(silent=True) or {}
    space_id = str(data.get('space_id', '')).strip().upper()
    raw_car_number = data.get('car_number')

    if not space_id or raw_car_number is None or str(raw_car_number).strip() == '':
        return jsonify({
            "status": "error",
            "message": "space_id와 car_number를 모두 입력해야 합니다."
        }), 400

    if space_id not in VALID_SPACES:
        return jsonify({
            "status": "error",
            "message": f"유효하지 않은 주차면 번호입니다: {space_id} (허용: A1~A5)"
        }), 400

    car_number = normalize_car_number(raw_car_number)
    if not is_valid_car_number(car_number):
        return jsonify({
            "status": "error",
            "message": "차량번호는 숫자 4자리여야 합니다. (예: 1234)"
        }), 400

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 동시 예약 방지를 위한 즉시 쓰기 잠금 트랜잭션
        cursor.execute("BEGIN IMMEDIATE")

        cursor.execute(
            "SELECT is_reserved, is_occupied FROM parking_spaces WHERE space_id = ?",
            (space_id,)
        )
        space = cursor.fetchone()

        if not space:
            conn.rollback()
            return jsonify({"status": "error", "message": f"존재하지 않는 주차면입니다: {space_id}"}), 404

        if space["is_reserved"]:
            conn.rollback()
            return jsonify({"status": "error", "message": f"이미 예약된 주차면입니다 ({space_id})."}), 409

        if space["is_occupied"]:
            conn.rollback()
            return jsonify({"status": "error", "message": f"현재 차량이 주차되어 있어 예약할 수 없습니다 ({space_id})."}), 409

        cursor.execute(
            "SELECT space_id FROM parking_spaces WHERE car_number = ? AND is_reserved = 1",
            (car_number,)
        )
        existing_res = cursor.fetchone()
        if existing_res:
            conn.rollback()
            return jsonify({
                "status": "error",
                "message": f"해당 차량({car_number})은 이미 {existing_res['space_id']} 구역에 예약되어 있습니다."
            }), 409

        now = get_now_str()
        cursor.execute('''
            UPDATE parking_spaces
            SET is_reserved = 1,
                car_number = ?,
                ocr_status = 'NONE',
                detected_car_number = NULL,
                updated_at = ?
            WHERE space_id = ? AND is_reserved = 0
        ''', (car_number, now, space_id))

        if cursor.rowcount == 0:
            conn.rollback()
            return jsonify({"status": "error", "message": "동시 요청으로 인해 예약에 실패했습니다. 다시 시도해주세요."}), 409

        conn.commit()

        return jsonify({
            "status": "success",
            "message": f"{space_id} 주차면 예약이 완료되었습니다.",
            "data": {
                "space_id": space_id,
                "car_number": car_number,
                "is_reserved": True,
                "updated_at": now
            }
        }), 201

    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"status": "error", "message": f"예약 처리 중 오류 발생: {str(e)}"}), 500
    finally:
        if conn:
            conn.close()


# 3. 예약 취소 API (DELETE /api/reservations/<space_id>)
@parking_bp.route('/api/reservations/<space_id>', methods=['DELETE'])
def cancel_reservation(space_id):
    space_id = space_id.strip().upper()

    if space_id not in VALID_SPACES:
        return jsonify({"status": "error", "message": f"유효하지 않은 주차면 번호입니다: {space_id}"}), 400

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute(
            "SELECT is_reserved, is_occupied FROM parking_spaces WHERE space_id = ?",
            (space_id,)
        )
        space = cursor.fetchone()

        if not space:
            conn.rollback()
            return jsonify({"status": "error", "message": f"존재하지 않는 주차면입니다: {space_id}"}), 404

        if not space["is_reserved"]:
            conn.rollback()
            return jsonify({"status": "error", "message": f"{space_id} 주차면은 예약되어 있지 않습니다."}), 400

        now = get_now_str()
        cursor.execute('''
            UPDATE parking_spaces
            SET is_reserved = 0,
                car_number = NULL,
                ocr_status = 'NONE',
                detected_car_number = NULL,
                updated_at = ?
            WHERE space_id = ?
        ''', (now, space_id))

        conn.commit()

        return jsonify({
            "status": "success",
            "message": f"{space_id} 주차면의 예약이 취소되었습니다.",
            "data": {
                "space_id": space_id,
                "is_reserved": False,
                "updated_at": now
            }
        }), 200

    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"status": "error", "message": f"예약 취소 중 오류 발생: {str(e)}"}), 500
    finally:
        if conn:
            conn.close()


# 4. OpenCV 차량 점유 결과 수신 API (POST /api/occupancy)
@parking_bp.route('/api/occupancy', methods=['POST'])
def update_occupancy():
    data = request.get_json(silent=True) or {}
    space_id = str(data.get('space_id', '')).strip().upper()
    occupied = data.get('occupied')

    if not space_id or not isinstance(occupied, bool):
        return jsonify({
            "status": "error",
            "message": "space_id(문자열)와 occupied(true/false 불리언) 필드가 필요합니다."
        }), 400

    if space_id not in VALID_SPACES:
        return jsonify({"status": "error", "message": f"유효하지 않은 주차면 번호입니다: {space_id}"}), 400

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        now = get_now_str()
        # 차량이 빠져나간 경우(occupied == False) OCR 상태도 함께 초기화
        if not occupied:
            cursor.execute('''
                UPDATE parking_spaces
                SET is_occupied = 0,
                    ocr_status = 'NONE',
                    detected_car_number = NULL,
                    updated_at = ?
                WHERE space_id = ?
            ''', (now, space_id))
        else:
            cursor.execute('''
                UPDATE parking_spaces
                SET is_occupied = 1,
                    updated_at = ?
                WHERE space_id = ?
            ''', (now, space_id))

        conn.commit()

        return jsonify({
            "status": "success",
            "message": f"{space_id} 구역 차량 점유 상태 업데이트 완료 ({occupied})",
            "data": {
                "space_id": space_id,
                "is_occupied": occupied,
                "updated_at": now
            }
        }), 200

    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"status": "error", "message": f"점유 상태 업데이트 오류: {str(e)}"}), 500
    finally:
        if conn:
            conn.close()


# 5. OCR 번호판 인식 결과 수신 및 검증 API (POST /api/ocr)
@parking_bp.route('/api/ocr', methods=['POST'])
def verify_ocr():
    data = request.get_json(silent=True) or {}
    space_id = str(data.get('space_id', '')).strip().upper()
    raw_detected = data.get('detected_car_number')
    confidence = data.get('confidence', 1.0)

    if not space_id:
        return jsonify({"status": "error", "message": "space_id가 필요합니다."}), 400

    if space_id not in VALID_SPACES:
        return jsonify({"status": "error", "message": f"유효하지 않은 주차면 번호입니다: {space_id}"}), 400

    try:
        confidence = float(confidence)
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "confidence는 0.0~1.0 사이의 숫자여야 합니다."}), 400

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT is_reserved, car_number FROM parking_spaces WHERE space_id = ?",
            (space_id,)
        )
        space = cursor.fetchone()

        if not space:
            return jsonify({"status": "error", "message": f"존재하지 않는 주차면입니다: {space_id}"}), 404

        now = get_now_str()
        detected_car_number = normalize_car_number(raw_detected) if raw_detected is not None else None

        # 1) 인식 실패, 신뢰도 부족(0.70 미만), 또는 숫자 4자리가 아닌 경우 -> 확인 필요 (단정 금지)
        if confidence < 0.70 or not detected_car_number or not is_valid_car_number(detected_car_number):
            ocr_status = "UNCERTAIN"
            cursor.execute('''
                UPDATE parking_spaces
                SET ocr_status = ?, detected_car_number = ?, updated_at = ?
                WHERE space_id = ?
            ''', (ocr_status, detected_car_number, now, space_id))
            conn.commit()

            return jsonify({
                "status": "warning",
                "result_code": ocr_status,
                "message": "번호판(숫자 4자리) 인식 결과가 불확실합니다. 확인이 필요합니다.",
                "data": {
                    "space_id": space_id,
                    "detected_car_number": detected_car_number,
                    "confidence": confidence
                }
            }), 20