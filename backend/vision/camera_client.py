import re
import time
import cv2
import numpy as np
import pytesseract
import requests

SERVER_URL = "http://127.0.0.1:5000"

# 1. 카메라 해상도(640x480 기준) 내 A1~A5 주차칸 좌표 (x1, y1, x2, y2)
# 실제 카메라 설치 각도에 맞게 좌표를 수정해 주세요.
PARKING_ROIS = {
    "A1": (20,  150, 130, 350),
    "A2": (140, 150, 250, 350),
    "A3": (260, 150, 370, 350),
    "A4": (380, 150, 490, 350),
    "A5": (500, 150, 610, 350),
}

# 점유 판단 민감도 (구역 내 변화 픽셀 비율, 0.15 = 15% 이상 변화 시 차량 있음으로 판단)
OCCUPANCY_THRESHOLD = 0.15


def send_occupancy(space_id: str, occupied: bool):
    try:
        res = requests.post(
            f"{SERVER_URL}/api/occupancy",
            json={"space_id": space_id, "occupied": occupied},
            timeout=3
        )
        print(f"[API /occupancy] {space_id} -> {occupied} | 응답: {res.status_code}")
    except Exception as e:
        print(f"[API 오류 /occupancy] {e}")


def send_ocr(space_id: str, car_number: str, confidence: float):
    try:
        res = requests.post(
            f"{SERVER_URL}/api/ocr",
            json={
                "space_id": space_id,
                "detected_car_number": car_number,
                "confidence": round(confidence, 2)
            },
            timeout=3
        )
        print(f"[API /ocr] {space_id} -> '{car_number}' (신뢰도 {confidence:.2f}) | 응답: {res.json().get('result_code')}")
    except Exception as e:
        print(f"[API 오류 /ocr] {e}")


def extract_4digit_number(roi_img):
    """ROI 이미지에서 숫자 4자리와 신뢰도를 추출"""
    gray = cv2.cvtColor(roi_img, cv2.COLOR_BGR2GRAY)
    # 대비 향상 및 이진화
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # 숫자만 인식하도록 Tesseract 화이트리스트 설정
    config = "--psm 6 -c tessedit_char_whitelist=0123456789"
    data = pytesseract.image_to_data(thresh, config=config, output_type=pytesseract.Output.DICT)

    best_num = ""
    best_conf = 0.0

    for text, conf in zip(data["text"], data["conf"]):
        cleaned = re.sub(r"\D", "", text)
        conf_val = float(conf) / 100.0 if int(conf) >= 0 else 0.0
        if len(cleaned) == 4 and conf_val > best_conf:
            best_num = cleaned
            best_conf = conf_val

    return best_num, best_conf


def main():
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    if not cap.isOpened():
        print("카메라를 열 수 없습니다. 연결 상태를 확인하세요.")
        return

    print("카메라 초기화 중... (2초간 빈 주차장 기준 프레임을 캡처합니다)")
    time.sleep(2)
    ret, base_frame = cap.read()
    if not ret:
        print("기준 프레임 캡처 실패")
        return

    base_gray = cv2.cvtColor(base_frame, cv2.COLOR_BGR2GRAY)
    base_gray = cv2.GaussianBlur(base_gray, (21, 21), 0)

    # 이전 점유 상태 저장 (상태가 바뀔 때만 API 호출)
    prev_occupied = {space_id: False for space_id in PARKING_ROIS}

    print("스마트 주차장 비전 감시 시작 (종료: Ctrl+C 또는 'q' 키)")

    while True:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.5)
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        for space_id, (x1, y1, x2, y2) in PARKING_ROIS.items():
            # 기준 배경과 현재 프레임의 차이 계산
            diff = cv2.absdiff(base_gray[y1:y2, x1:x2], gray[y1:y2, x1:x2])
            _, thresh = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)

            changed_ratio = np.count_nonzero(thresh) / thresh.size
            is_occupied = changed_ratio > OCCUPANCY_THRESHOLD

            # 상태가 변경된 경우에만 백엔드 서버로 전송
            if is_occupied != prev_occupied[space_id]:
                prev_occupied[space_id] = is_occupied
                send_occupancy(space_id, is_occupied)

                # 차량이 새로 입차한 경우 OCR 실행
                if is_occupied:
                    time.sleep(1.0)  # 차량이 완전히 정차할 때까지 1초 대기
                    _, latest_frame = cap.read()
                    roi_crop = latest_frame[y1:y2, x1:x2]
                    car_num, conf = extract_4digit_number(roi_crop)
                    send_ocr(space_id, car_num, conf)

            # 디버깅용 화면 표시 (모니터 연결 시 확인 가능)
            color = (0, 0, 255) if is_occupied else (0, 255, 0)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(frame, f"{space_id}: {'OCC' if is_occupied else 'EMPTY'}",
                        (x1, y1 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

        # 모니터 없이 SSH로만 실행할 때는 아래 3줄을 주석 처리하세요
        cv2.imshow("Smart Parking Vision", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

        time.sleep(0.3)

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()