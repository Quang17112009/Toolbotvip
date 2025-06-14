import os
import json
import time
import asyncio
import threading
import logging
from datetime import datetime, timedelta
from collections import defaultdict, Counter
import requests
import telebot
from flask import Flask, request, abort

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==== CẤU HÌNH ====
# KHUYẾN NGHỊ: ĐẶT TOKEN BOT TRƯỚC TẤT CẢ CÁC DECORATOR @bot.message_handler
# Để thử nghiệm cục bộ, bạn có thể gán token cứng ở đây.
# KHI TRIỂN KHAI LÊN SERVER NHƯ RENDER, HÃY BỎ COMMENT DÒNG os.getenv() BÊN DƯỚI
# VÀ COMMENT DÒNG GÁN TRỰC TIẾP TOKEN.
TELEGRAM_BOT_TOKEN_HARDCODED = "8080593458:AAFfIN0hVbZBflDCFAb-pJ51cysDoWRcsZU" # <--- THAY THẾ BẰNG TOKEN BOT CỦA BẠN

# Khởi tạo bot ngay lập tức
bot = telebot.TeleBot("8080593458:AAFfIN0hVbZBflDCFAb-pJ51cysDoWRcsZU", parse_mode='HTML')


# API URL MỚI
HTTP_API_URL = "http://157.10.52.15:3000/api/sunwin?key=Tuantutrum"
# Tên các file dữ liệu
LICHSU_FILE = "lichsucau.txt"
DUDOAN_FILE = "dudoan.txt"          # File cầu VIP ưu tiên (AI 1)
AI_FILE = "ai_1-2.txt"              # File cầu AI tự học (AI 2)
PATTERN_COUNT_FILE = "pattern_counter.json" # File đếm tần suất cho AI 3 và AI 2
DULIEU_AI_FILE = "dulieu_ai.json"   # Tệp nhật ký để ghi lại tất cả các dự đoán và kết quả
USER_DATA_FILE = "user_data.json"   # File dữ liệu người dùng mới (key là khóa chính)

# Cài đặt thời gian và pattern
CHECK_INTERVAL_SECONDS = 5          # Thời gian chờ giữa các lần kiểm tra phiên mới
MIN_PATTERN_LENGTH = 4              # Độ dài tối thiểu của pattern
MAX_PATTERN_LENGTH = 15             # Độ dài tối đa của pattern (sử dụng 8 ký tự lịch sử)
# Ngưỡng học cho AI 2
AI_LEARN_THRESHOLD_COUNT = 5
AI_LEARN_THRESHOLD_RATE = 75

# --- Cấu hình quyền hạn ---
# THAY THẾ BẰNG CHAT_ID CỦA ADMIN MẶC ĐỊNH (là ID của bạn)
ADMIN_CHAT_ID = "6915752059" # <-- VUI LÒNG THAY THẾ BẰNG ID TELEGRAM CỦA BẠN
DEFAULT_ADMIN_KEY = "quangdz" # Key admin mặc định khi admin đăng nhập lần đầu

# --- MÀU SẮC CHO CONSOLE (chỉ dùng cho console, không hiển thị trong logs Render) ---
RED, GREEN, YELLOW, RESET, BOLD = "\033[91m", "\033[92m", "\033[93m", "\033[0m", "\033[1m"

# ==== BIẾN TOÀN CỤC ====
lich_su = []
pattern_counter = defaultdict(lambda: {"T": 0, "X": 0})
last_processed_phien = None
cau_dudoan = {}
cau_ai = {}
win_rate_tracker = defaultdict(list)
pending_predictions = {} # {phien_id: data}

# user_data: Bây giờ sẽ dùng key làm khóa chính
user_data = {} # {key_string: {chat_id: int, expiry_time: timestamp, role: "user/ctv/admin", username: str, current_chat_id: int, max_devices: int, assigned_chat_ids: list, associated_chat_id: int}}

# BIẾN CHO LOGIC MD5
md5_giai_doan_counter = 0
md5_analysis_result = "Khác"

# ==== CÁC HÀM TIỆN ÍCH & TẢI DỮ LIỆU ====

def tai_xiu(tong):
    return "T" if tong >= 11 else "X"

def load_data():
    """Tải tất cả dữ liệu cần thiết khi khởi động."""
    global lich_su, pattern_counter, cau_dudoan, cau_ai, user_data
    try:
        if os.path.exists(LICHSU_FILE):
            with open(LICHSU_FILE, "r", encoding="utf-8") as f:
                lich_su = [line.strip() for line in f if line.strip() in ['T', 'X']]
            lich_su = lich_su[-MAX_PATTERN_LENGTH:]
    except IOError as e:
        logger.error(f"Lỗi khi đọc file lịch sử: {e}")
        lich_su = []

    if os.path.exists(PATTERN_COUNT_FILE):
        try:
            with open(PATTERN_COUNT_FILE, "r", encoding="utf-8") as f:
                pattern_counter = defaultdict(lambda: {"T": 0, "X": 0}, json.load(f))
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Lỗi khi đọc file đếm pattern hoặc file rỗng: {e}. Khởi tạo lại.")
            pattern_counter = defaultdict(lambda: {"T": 0, "X": 0})

    cau_dudoan = load_patterns_from_file(DUDOAN_FILE)
    cau_ai = load_patterns_from_file(AI_FILE)
    logger.info(f"Đã tải {len(cau_dudoan)} pattern VIP và {len(cau_ai)} pattern AI.")

    # Tải dữ liệu người dùng
    if os.path.exists(USER_DATA_FILE):
        try:
            with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
                user_data = json.load(f)
                # Đảm bảo các trường mới có mặt nếu thiếu (cho các key cũ)
                for key, info in user_data.items():
                    if 'current_chat_id' not in info: info['current_chat_id'] = None
                    if 'max_devices' not in info: info['max_devices'] = 1 # Mặc định 1
                    if 'assigned_chat_ids' not in info: info['assigned_chat_ids'] = []
                    if 'associated_chat_id' not in info: info['associated_chat_id'] = None # Dùng cho /capkey
                logger.info(f"Đã tải {len(user_data)} key người dùng.")
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Lỗi khi đọc file dữ liệu người dùng: {e}")
            user_data = {}

    # Đảm bảo admin mặc định được khởi tạo nếu ADMIN_CHAT_ID được cấu hình
    admin_exists_by_key = user_data.get(DEFAULT_ADMIN_KEY)
    if admin_exists_by_key and admin_exists_by_key.get('role') == 'admin':
        logger.info(f"Key Admin mặc định '{DEFAULT_ADMIN_KEY}' đã tồn tại và là admin.")
    else:
        user_data[DEFAULT_ADMIN_KEY] = {
            "key": DEFAULT_ADMIN_KEY,
            "expiry_time": "never",
            "role": "admin",
            "username": "Admin (Mặc định)",
            "current_chat_id": None, # Sẽ được gán khi admin đăng nhập bằng /key
            "max_devices": -1, # Không giới hạn thiết bị cho admin
            "assigned_chat_ids": [],
            "associated_chat_id": None # Không liên kết trực tiếp với chat_id cụ thể
        }
        save_user_data()
        logger.info(f"Đã đảm bảo key Admin mặc định '{DEFAULT_ADMIN_KEY}' tồn tại.")


def load_patterns_from_file(filepath):
    """Tải các pattern dự đoán từ một file cụ thể."""
    patterns = {}
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or "=>" not in line: continue
                    try:
                        parts = line.split("=>")
                        pattern, prediction_part = parts[0].strip(), parts[1]
                        prediction = prediction_part.split("Dự đoán:")[1].strip()[0]
                        if prediction in ["T", "X"]:
                            patterns[pattern] = prediction
                    except IndexError:
                        continue
        except IOError as e:
            logger.error(f"Lỗi khi đọc file '{filepath}': {e}")
    return patterns

def cap_nhat_lich_su_file():
    """Lưu lịch sử cầu hiện tại vào file."""
    try:
        with open(LICHSU_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lich_su))
    except IOError as e:
        logger.error(f"Lỗi khi ghi lịch sử vào file: {e}")

def save_pattern_counter():
    """Lưu bộ đếm tần suất vào file JSON."""
    try:
        with open(PATTERN_COUNT_FILE, "w", encoding="utf-8") as f:
            json.dump(pattern_counter, f, ensure_ascii=False, indent=2)
    except IOError as e:
        logger.error(f"Lỗi khi ghi bộ đếm pattern: {e}")

def save_user_data():
    """Lưu dữ liệu người dùng vào file JSON."""
    try:
        with open(USER_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(user_data, f, ensure_ascii=False, indent=2)
    except IOError as e:
        logger.error(f"Lỗi khi ghi dữ liệu người dùng: {e}")

def get_data_from_api():
    """Lấy dữ liệu phiên mới nhất từ API mới."""
    try:
        response = requests.get(HTTP_API_URL, timeout=10)
        response.raise_for_status()
        json_data = response.json()

        phien_truoc = int(json_data.get("Phiên Trước"))
        ket_qua_truoc = json_data.get("Kết Quả")
        xuc_xac_truoc_str = json_data.get("Xúc Xắc")

        xx_parts = [int(x) for x in xuc_xac_truoc_str.split(' ') if x.isdigit()]

        lich_su_cau_api = json_data.get("Cầu", "")

        return {
            "phien_truoc": phien_truoc,
            "ket_qua_truoc": "T" if ket_qua_truoc == "Tài" else "X",
            "xuc_xac_truoc": xx_parts,
            "lich_su_cau_api": lich_su_cau_api
        }
    except (requests.exceptions.RequestException, json.JSONDecodeError, KeyError, ValueError) as e:
        logger.error(f"Lỗi khi gọi API hoặc phân tích dữ liệu từ API mới: {e}")
        return None

# ==== LOGIC DỰ ĐOÁN & HỌC HỎI ====

def get_all_predictions(history_str):
    """
    Tập hợp dự đoán từ tất cả các nguồn AI.
    Ưu tiên AI 1 (VIP), sau đó đến AI 2 (Tự học) và AI 3 (Thống kê).
    """
    pred_vip = get_prediction_from_source(history_str, cau_dudoan, "AI 1 (VIP)")
    pred_ai_file = get_prediction_from_source(history_str, cau_ai, "AI 2 (Tự Học)")
    pred_stat = get_statistical_prediction(history_str)

    return [p for p in [pred_vip, pred_ai_file, pred_stat] if p is not None]

def get_prediction_from_source(history_str, source_patterns, source_name):
    """Lấy dự đoán từ một nguồn pattern cụ thể, ưu tiên cầu dài nhất."""
    for length in range(min(len(history_str), MAX_PATTERN_LENGTH), MIN_PATTERN_LENGTH - 1, -1):
        pat = history_str[-length:]
        if pat in source_patterns:
            prediction = source_patterns[pat]
            counts = pattern_counter.get(pat, {"T": 0, "X": 0})
            total = counts['T'] + counts['X']
            accuracy = (counts[prediction] / total * 100) if total > 0 else 100.0
            return {"prediction": prediction, "pattern": pat, "accuracy": accuracy, "source": source_name}
    return None

def get_statistical_prediction(history_str):
    """AI 3: Dự đoán dựa trên tần suất xuất hiện trong quá khứ."""
    for length in range(min(len(history_str), MAX_PATTERN_LENGTH), MIN_PATTERN_LENGTH - 1, -1):
        pat = history_str[-length:]
        if pat in pattern_counter:
            counts = pattern_counter[pat]
            total = counts['T'] + counts['X']
            if total > 0:
                rate_T = (counts['T'] / total) * 100
                rate_X = (counts['X'] / total) * 100
                if rate_T >= AI_LEARN_THRESHOLD_RATE:
                    return {"prediction": "T", "pattern": pat, "accuracy": rate_T, "source": "AI 3 (Thống Kê)"}
                elif rate_X >= AI_LEARN_THRESHOLD_RATE:
                    return {"prediction": "X", "pattern": pat, "accuracy": rate_X, "source": "AI 3 (Thống Kê)"}
    return None

def chot_keo_cuoi_cung(predictions):
    """Tổng hợp các dự đoán để đưa ra khuyến nghị cuối cùng."""
    if not predictions:
        return {"ket_qua": "Bỏ qua", "ly_do": "Không có tín hiệu.", "confidence": "Thấp"}

    votes = Counter(p['prediction'] for p in predictions)
    num_votes = len(predictions)

    if len(votes) == 1:
        final_prediction = list(votes.keys())[0]
        return {"ket_qua": final_prediction, "ly_do": f"Đồng thuận {num_votes}/{num_votes}", "confidence": "Rất Cao"}

    # Ưu tiên AI 1 nếu có tín hiệu
    if any(p['source'] == "AI 1 (VIP)" for p in predictions):
        vip_pred = next(p for p in predictions if p['source'] == "AI 1 (VIP)")
        return {"ket_qua": vip_pred['prediction'], "ly_do": f"Ưu tiên AI 1 (VIP)", "confidence": "Cao"}

    # Nếu không có AI 1, chọn theo số đông
    if votes['T'] > votes['X']:
        return {"ket_qua": "T", "ly_do": f"Số đông nghiêng về Tài ({votes['T']}/{num_votes})", "confidence": "Trung Bình"}
    if votes['X'] > votes['T']:
        return {"ket_qua": "X", "ly_do": f"Số đông nghiêng về Xỉu ({votes['X']}/{num_votes})", "confidence": "Trung Bình"}

    # Nếu xung đột, chọn AI có accuracy cao nhất
    best_pred = max(predictions, key=lambda p: p['accuracy'])
    return {
        "ket_qua": best_pred['prediction'],
        "ly_do": f"Xung đột, ưu tiên {best_pred['source']} (CX: {best_pred['accuracy']:.1f}%)",
        "confidence": "Trung Bình"
    }

def ai_hoc_hoi(history_before_result, actual_result):
    """AI học từ kết quả thực tế để cập nhật bộ đếm và tự học cầu mới."""
    global md5_analysis_result, cau_dudoan, cau_ai
    if md5_analysis_result == "Gãy":
        logger.info(f"MD5 'Gãy', AI bỏ qua việc học phiên này.")
        return

    history_str = "".join(history_before_result)
    for length in range(MIN_PATTERN_LENGTH, min(len(history_str), MAX_PATTERN_LENGTH) + 1):
        pat = history_str[-length:]
        pattern_counter[pat][actual_result] += 1

    potential_pat = history_str[-MIN_PATTERN_LENGTH:]
    if len(potential_pat) == MIN_PATTERN_LENGTH:
        if potential_pat not in cau_dudoan and potential_pat not in cau_ai:
            counts = pattern_counter[potential_pat]
            total = counts['T'] + counts['X']
            if total > 0: # Đảm bảo có dữ liệu để tính tỷ lệ
                rate_T = (counts['T'] / total) * 100
                rate_X = (counts['X'] / total) * 100
                prediction_to_learn = None
                if rate_T >= AI_LEARN_THRESHOLD_RATE: prediction_to_learn = 'T'
                elif rate_X >= AI_LEARN_THRESHOLD_RATE: prediction_to_learn = 'X'

                if prediction_to_learn:
                    try:
                        # Kiểm tra xem pattern đã tồn tại trong AI_FILE chưa trước khi ghi
                        current_ai_patterns = load_patterns_from_file(AI_FILE)
                        if potential_pat not in current_ai_patterns:
                            with open(AI_FILE, "a", encoding="utf-8") as f:
                                f.write(f"\n{potential_pat} => Dự đoán: {prediction_to_learn} - Loại cầu: AI Tự Học")
                            cau_ai = load_patterns_from_file(AI_FILE) # Tải lại để cập nhật bộ nhớ
                            logger.info(f"AI 2 đã học pattern mới: {potential_pat} => {prediction_to_learn}")
                        else:
                            logger.info(f"Pattern {potential_pat} đã tồn tại trong AI_FILE, không ghi lại.")
                    except IOError as e:
                        logger.error(f"Lỗi khi ghi cầu mới của AI: {e}")
    save_pattern_counter()

def log_prediction_data(phien_du_doan, history_str, all_preds, final_choice, actual_result=None, is_win=None):
    """Ghi lại toàn bộ dữ liệu của một phiên vào file dulieu_ai.json."""
    log_entry = {
        "phien": phien_du_doan,
        "thoi_gian": datetime.now().isoformat(),
        "lich_su_cau_bot": history_str,
        "tin_hieu_ai": [{"source": p["source"], "prediction": p["prediction"], "pattern": p["pattern"], "accuracy": p["accuracy"]} for p in all_preds],
        "khuyen_nghi": final_choice,
        "ket_qua_thuc_te": actual_result,
        "thang": is_win
    }
    try:
        logs = []
        if os.path.exists(DULIEU_AI_FILE):
            with open(DULIEU_AI_FILE, "r", encoding="utf-8") as f:
                try:
                    logs = json.load(f)
                except json.JSONDecodeError:
                    logs = [] # Nếu file bị hỏng, bắt đầu với list rỗng

        updated = False
        for i, log in enumerate(logs):
            if log["phien"] == phien_du_doan:
                logs[i] = log_entry
                updated = True
                break
        if not updated:
            logs.append(log_entry)

        with open(DULIEU_AI_FILE, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
    except (IOError, json.JSONDecodeError) as e:
        logger.error(f"Lỗi khi ghi file nhật ký {DULIEU_AI_FILE}: {e}")


# ==== LOGIC TELEGRAM ====

async def send_telegram_message(chat_id, message_text):
    """Gửi tin nhắn đến một chat_id cụ thể."""
    try:
        # bot.send_message là blocking, cần chạy trong thread riêng
        await asyncio.to_thread(bot.send_message, chat_id=chat_id, text=message_text, parse_mode='HTML')
        logger.info(f"Đã gửi tin nhắn đến {chat_id}.")
    except telebot.apihelper.ApiTelegramException as e:
        logger.warning(f"Lỗi Telegram API khi gửi tin nhắn tới {chat_id}: {e}")
        # Xử lý các lỗi phổ biến khi bot bị chặn/không tìm thấy chat
        if "bot was blocked by the user" in str(e).lower() or \
           "chat not found" in str(e).lower() or \
           "user is deactivated" in str(e).lower():
            logger.warning(f"Bot bị chặn/không tìm thấy chat cho {chat_id}. Đang loại bỏ khỏi active_chat_ids (nếu có).")
            # Tìm và loại bỏ chat_id này khỏi các user_data entries
            for user_key, user_info in list(user_data.items()): # Dùng list() để tránh lỗi khi sửa đổi dict
                if user_info.get('current_chat_id') == chat_id:
                    user_info['current_chat_id'] = None # Đặt về None để người dùng phải kích hoạt lại
                    # Remove chat_id from assigned_chat_ids as well
                    user_info['assigned_chat_ids'] = [cid for cid in user_info['assigned_chat_ids'] if cid != chat_id]
                    save_user_data()
                    logger.warning(f"Đã cập nhật trạng thái cho key '{user_key}' (ID: {chat_id}).")
                    break
    except Exception as e:
        logger.error(f"Lỗi không xác định khi gửi tin nhắn tới {chat_id}: {e}")


async def send_prediction_notification(phien_du_doan, predictions, final_choice):
    """Gửi thông báo DỰ ĐOÁN cho các người dùng hợp lệ."""
    def format_kq(kq):
        return f"<b><font color='green'>TÀI</font></b>" if kq == 'T' else f"<b><font color='red'>XỈU</font></b>"

    message = [f"<b>🔮 DỰ ĐOÁN CHO PHIÊN #{phien_du_doan} 🔮</b>"]
    message.append(f"<b>Lịch sử cầu hiện tại của Bot:</b> <code>{''.join(lich_su)}</code>")
    message.append("─" * 25)
    message.append("<b>Tín hiệu từ các AI:</b>")

    if predictions:
        for p in predictions:
            message.append(f"  - <b>{p['source']}</b>: {format_kq(p['prediction'])} (Cầu: <code>{p['pattern']}</code>, CX: {p['accuracy']:.1f}%)")
    else:
        message.append("  <i>- Không có tín hiệu rõ ràng từ AI.</i>")

    message.append("─" * 25)
    final_kq = final_choice['ket_qua']
    if final_kq == "Bỏ qua":
        message.append(f"  ▶️ <b>KHUYẾN NGHỊ: <font color='orange'>BỎ QUA</font></b>")
    else:
        confidence = final_choice.get('confidence', 'Không xác định')
        conf_color = "green" if confidence == "Rất Cao" else ("orange" if "Cao" in confidence else "red")
        message.append(f"  ▶️ <b>KHUYẾN NGHỊ: {format_kq(final_kq)}</b> (Độ tin cậy: <font color='{conf_color}'>{confidence.upper()}</font>)")

    message.append(f"<i>Lý do: {final_choice['ly_do']}</i>")

    # Gửi đến TẤT CẢ người dùng có key hợp lệ và đang hoạt động trên thiết bị của họ
    for user_key, user_info in list(user_data.items()):
        if is_key_valid(user_info) and user_info.get('current_chat_id'): # Chỉ gửi nếu có chat_id được gán
            await send_telegram_message(user_info['current_chat_id'], "\n".join(message))


async def send_result_notification(phien, xx, tong, kq_thucte, prediction_data):
    """Gửi thông báo KẾT QUẢ của phiên vừa rồi và so sánh với dự đoán."""
    final_choice = prediction_data['final_choice']
    is_win = (final_choice['ket_qua'] == kq_thucte) if final_choice['ket_qua'] != "Bỏ qua" else None

    # Update win rate tracker
    for pred_obj in prediction_data['all_predictions']:
        source_key = pred_obj['source']
        win_rate_tracker[source_key].append(pred_obj['prediction'] == kq_thucte)

    def format_kq(kq):
        return f"<b><font color='green'>TÀI</font></b>" if kq == 'T' else f"<b><font color='red'>XỈU</font></b>"

    title = "✅ KẾT QUẢ PHIÊN" if is_win is not False else "❌ KẾT QUẢ PHIÊN"
    message = [f"<b>{title} #{phien}</b>"]
    message.append(f"🎲 Xúc xắc: <b>{xx[0]}-{xx[1]}-{xx[2]}</b> (Tổng: {tong}) => {format_kq(kq_thucte)}")

    if is_win is True:
        message.append(f"🎉 <b>THẮNG!</b> - Dự đoán <b>{format_kq(final_choice['ket_qua'])}</b> đã chính xác.")
    elif is_win is False:
        message.append(f"😭 <b>THUA!</b> - Dự đoán <b>{format_kq(final_choice['ket_qua'])}</b>, kết quả là <b>{format_kq(kq_thucte)}</b>.")
    else: # Bỏ qua
        message.append(f"⚪️ <b>BỎ QUA</b> - Bot đã không đưa ra khuyến nghị cho phiên này.")

    md5_status_color = "red" if md5_analysis_result == "Gãy" else "green"
    message.append(f"⛓️ Trạng thái MD5: <font color='{md5_status_color}'>{md5_analysis_result.upper()}</font>")

    # Gửi đến TẤT CẢ người dùng có key hợp lệ và đang hoạt động trên thiết bị của họ
    for user_key, user_info in list(user_data.items()):
        if is_key_valid(user_info) and user_info.get('current_chat_id'): # Chỉ gửi nếu có chat_id được gán
            await send_telegram_message(user_info['current_chat_id'], "\n".join(message))


# ==== LOGIC QUẢN LÝ KEY & NGƯỜI DÙNG ====

def is_admin_by_chat_id(chat_id):
    """Kiểm tra xem một chat_id có phải là admin không (dựa trên cấu hình ban đầu ADMIN_CHAT_ID)."""
    # So sánh trực tiếp với ID admin cấu hình
    return str(chat_id) == ADMIN_CHAT_ID

def get_user_info_by_chat_id(chat_id):
    """Tìm thông tin key của người dùng dựa trên chat_id đang được gán."""
    for key, info in user_data.items():
        # Kiểm tra cả current_chat_id và assigned_chat_ids
        if info.get('current_chat_id') == chat_id or (chat_id in info.get('assigned_chat_ids', [])):
            return key, info
    return None, None

def is_key_valid(user_info):
    """Kiểm tra xem key có hợp lệ và còn hạn không."""
    if not user_info:
        return False

    if user_info['role'] in ['admin', 'ctv']:
        return True # Admin và CTV có key vĩnh viễn

    if user_info['expiry_time'] == "never":
        return True

    try:
        expiry_dt = datetime.fromisoformat(user_info['expiry_time'])
        return datetime.now() < expiry_dt
    except (TypeError, ValueError):
        logger.error(f"Lỗi định dạng thời gian hết hạn cho key: {user_info.get('key', 'N/A')}")
        return False # Lỗi định dạng thời gian

async def authenticate_user_key(chat_id, user_key_input):
    """
    Xử lý việc người dùng nhập key để kích hoạt bot.
    Kiểm tra key hợp lệ, hạn sử dụng, và số lượng thiết bị.
    """
    user_key_input = user_key_input.lower()

    if user_key_input not in user_data:
        await send_telegram_message(chat_id, "❌ **Key không hợp lệ hoặc không tồn tại.** Vui lòng kiểm tra lại hoặc liên hệ Admin.")
        logger.warning(f"Chat ID {chat_id} nhập key không tồn tại: '{user_key_input}'.")
        return False

    user_info = user_data[user_key_input]

    # Nếu đây là key được cấp qua /capkey và chat_id không khớp
    if user_info.get('associated_chat_id') is not None and user_info['associated_chat_id'] != chat_id:
        await send_telegram_message(chat_id, "❌ **Key này chỉ được kích hoạt bởi ID Telegram đã được chỉ định.** Vui lòng liên hệ Admin.")
        logger.warning(f"Chat ID {chat_id} cố gắng dùng key '{user_key_input}' được gán cho ID khác.")
        return False

    if not is_key_valid(user_info):
        await send_telegram_message(chat_id, "❌ **Key này đã hết hạn.** Vui lòng liên hệ Admin để gia hạn.")
        logger.warning(f"Chat ID {chat_id} nhập key hết hạn: '{user_key_input}'.")
        return False

    max_devices = user_info.get('max_devices', 1)
    assigned_chat_ids = set(user_info.get('assigned_chat_ids', []))

    if max_devices != -1 and chat_id not in assigned_chat_ids:
        if len(assigned_chat_ids) >= max_devices:
            await send_telegram_message(chat_id, f"⚠️ **Key '{user_key_input}' đã đạt số lượng thiết bị tối đa ({max_devices}).**\nVui lòng ngắt kết nối key khỏi thiết bị cũ hoặc liên hệ Admin.")
            logger.warning(f"Key '{user_key_input}' đã vượt quá giới hạn thiết bị cho Chat ID {chat_id}.")
            return False

    # Nếu chat_id đã nằm trong danh sách assigned_chat_ids và là current_chat_id
    if chat_id in assigned_chat_ids and user_info.get('current_chat_id') == chat_id:
        await send_telegram_message(chat_id, f"✅ **Key '{user_key_input}' đã được kích hoạt trên thiết bị này.** Bot sẽ tiếp tục gửi dự đoán.")
        logger.info(f"Chat ID {chat_id} đã kích hoạt lại key '{user_key_input}'.")
        return True

    # Nếu key đang được dùng bởi chat_id khác và là single device (max_devices = 1)
    if max_devices == 1 and user_info.get('current_chat_id') and user_info['current_chat_id'] != chat_id:
        await send_telegram_message(user_info['current_chat_id'], f"⚠️ **Key của bạn ('{user_key_input}') đã được đăng nhập trên một thiết bị khác.**\nBạn sẽ không còn nhận được dự đoán trên thiết bị này.")
        logger.warning(f"Key '{user_key_input}' đã bị chuyển từ {user_info['current_chat_id']} sang {chat_id}.")
        # Xóa chat_id cũ khỏi assigned_chat_ids nếu nó chỉ cho phép 1 thiết bị
        if user_info['current_chat_id'] in assigned_chat_ids:
            assigned_chat_ids.discard(user_info['current_chat_id'])
            user_info['assigned_chat_ids'] = list(assigned_chat_ids) # Cập nhật lại list

    # Cập nhật thông tin key
    user_info['current_chat_id'] = chat_id
    if chat_id not in assigned_chat_ids:
        assigned_chat_ids.add(chat_id)
        user_info['assigned_chat_ids'] = list(assigned_chat_ids)

    # Cập nhật username (nếu có và khác)
    try:
        member = await asyncio.to_thread(bot.get_chat_member, chat_id, chat_id)
        if member.user.username:
            user_info['username'] = member.user.username
        elif member.user.first_name:
            user_info['username'] = member.user.first_name
    except Exception as e:
        logger.warning(f"Không thể lấy username cho chat_id {chat_id}: {e}")

    # Cập nhật và lưu lại user_data
    user_data[user_key_input] = user_info
    save_user_data()

    await send_telegram_message(chat_id, f"✅ **Key '{user_key_input}' của bạn đã được kích hoạt thành công!**\nBot sẽ bắt đầu gửi dự đoán cho bạn.")
    logger.info(f"Chat ID {chat_id} đã kích hoạt key '{user_key_input}'.")
    return True

# Admin commands
async def add_update_key(admin_chat_id, key_name, duration_hours=None, role="user", max_devices=1):
    """Thêm hoặc cập nhật key người dùng (dùng cho /addkey và /ctv)."""
    key_name_lower = key_name.lower()

    if key_name_lower == DEFAULT_ADMIN_KEY and role != "admin":
        await send_telegram_message(admin_chat_id, f"❌ **Không thể thay đổi vai trò hoặc xóa quyền Admin của key mặc định '{DEFAULT_ADMIN_KEY}'.**")
        return False

    expiry_time_str = "never"
    if duration_hours is not None:
        expiry_dt = datetime.now() + timedelta(hours=duration_hours)
        expiry_time_str = expiry_dt.isoformat()

    old_info = user_data.get(key_name_lower, {})

    user_data[key_name_lower] = {
        "key": key_name_lower,
        "expiry_time": expiry_time_str,
        "role": role,
        "username": old_info.get("username", key_name),
        "current_chat_id": old_info.get("current_chat_id"),
        "max_devices": max_devices,
        "assigned_chat_ids": old_info.get("assigned_chat_ids", []),
        "associated_chat_id": old_info.get("associated_chat_id") # Giữ lại nếu đã có
    }
    save_user_data()
    return True

async def add_key_by_chat_id(admin_chat_id, target_chat_id_str, duration_hours=None):
    """Cấp key trực tiếp cho một chat_id (dùng cho /capkey)."""
    try:
        target_chat_id = int(target_chat_id_str)
    except ValueError:
        await send_telegram_message(admin_chat_id, "❌ **Lỗi:** ID Telegram không hợp lệ. Vui lòng nhập một số.")
        return False

    # Tạo một key mặc định dựa trên chat_id
    key_name = f"key_{target_chat_id}"
    expiry_time_str = "never"
    if duration_hours is not None:
        expiry_dt = datetime.now() + timedelta(hours=duration_hours)
        expiry_time_str = expiry_dt.isoformat()

    # Tìm xem chat_id này đã có key nào được cấp trực tiếp rồi không
    existing_key_for_chat_id = None
    for k, info in user_data.items():
        if info.get('associated_chat_id') == target_chat_id:
            existing_key_for_chat_id = k
            break

    if existing_key_for_chat_id:
        # Cập nhật key đã có
        user_info = user_data[existing_key_for_chat_id]
        user_info['expiry_time'] = expiry_time_str
        user_info['role'] = "user" # Đảm bảo là user
        user_info['max_devices'] = -1 # Luôn là đa thiết bị cho loại key này
        user_info['current_chat_id'] = target_chat_id # Gán ngay
        if target_chat_id not in user_info['assigned_chat_ids']:
            user_info['assigned_chat_ids'].append(target_chat_id)

        # Cập nhật username nếu có
        try:
            member = await asyncio.to_thread(bot.get_chat_member, target_chat_id, target_chat_id)
            if member.user.username:
                user_info['username'] = member.user.username
            elif member.user.first_name:
                user_info['username'] = member.user.first_name
        except Exception:
            pass # Bỏ qua nếu không lấy được username

        save_user_data()
        await send_telegram_message(admin_chat_id, f"✅ Đã cập nhật key `{existing_key_for_chat_id}` cho ID `{target_chat_id}` (hiệu lực: {duration_hours} giờ)." + \
                                                    f"\n_Người dùng sẽ nhận thông báo dự đoán tự động._")
        await send_telegram_message(target_chat_id, f"🎉 Key của bạn đã được Admin kích hoạt/gia hạn!\nThời hạn: {duration_hours} giờ.\nBot sẽ bắt đầu gửi dự đoán cho bạn.")
        logger.info(f"Admin {admin_chat_id} đã cập nhật key '{existing_key_for_chat_id}' cho chat_id {target_chat_id}.")
        return True
    else:
        # Tạo key mới
        user_data[key_name] = {
            "key": key_name,
            "expiry_time": expiry_time_str,
            "role": "user",
            "username": f"User_ID_{target_chat_id}", # Username mặc định
            "current_chat_id": target_chat_id, # Gán ngay lập tức
            "max_devices": -1, # Luôn là đa thiết bị
            "assigned_chat_ids": [target_chat_id],
            "associated_chat_id": target_chat_id # Đánh dấu là key được gán cho chat_id này
        }

        # Cập nhật username nếu có
        try:
            member = await asyncio.to_thread(bot.get_chat_member, target_chat_id, target_chat_id)
            if member.user.username:
                user_data[key_name]['username'] = member.user.username
            elif member.user.first_name:
                user_data[key_name]['username'] = member.user.first_name
        except Exception:
            pass # Bỏ qua nếu không lấy được username

        save_user_data()
        await send_telegram_message(admin_chat_id, f"✅ Đã cấp key `{key_name}` cho ID `{target_chat_id}` (hiệu lực: {duration_hours} giờ).\n_Người dùng sẽ nhận thông báo dự đoán tự động._")
        await send_telegram_message(target_chat_id, f"🎉 Key của bạn đã được Admin kích hoạt!\nThời hạn: {duration_hours} giờ.\nBot sẽ bắt đầu gửi dự đoán cho bạn.")
        logger.info(f"Admin {admin_chat_id} đã cấp key mới '{key_name}' cho chat_id {target_chat_id}.")
        return True

async def remove_user_key(admin_chat_id, key_name):
    """Xóa key người dùng."""
    key_name_lower = key_name.lower()

    if key_name_lower == DEFAULT_ADMIN_KEY:
        await send_telegram_message(admin_chat_id, f"❌ **Không thể xóa key Admin mặc định '{DEFAULT_ADMIN_KEY}'.**")
        return False

    if key_name_lower in user_data:
        user_info = user_data[key_name_lower]
        if user_info.get('current_chat_id'):
            # Gửi thông báo đến người dùng trước khi xóa key
            try:
                await send_telegram_message(user_info['current_chat_id'], f"⚠️ **Key của bạn ('{key_name}') đã bị Admin xóa.** Bạn sẽ không còn nhận được dự đoán.")
            except Exception as e:
                logger.warning(f"Không thể gửi thông báo xóa key tới {user_info['current_chat_id']}: {e}")

        del user_data[key_name_lower]
        save_user_data()
        return True
    return False

def check_expired_keys():
    """Kiểm tra và xử lý các key hết hạn."""
    expired_count = 0
    keys_to_remove = []

    # Lấy event loop hiện tại
    loop = asyncio.get_event_loop()

    for user_key, user_info in list(user_data.items()): # Tạo bản sao để tránh lỗi khi sửa đổi
        if user_info['role'] not in ['admin', 'ctv'] and not is_key_valid(user_info):
            keys_to_remove.append(user_key)
            expired_count += 1
            logger.info(f"Key '{user_key}' của người dùng {user_info.get('username', 'N/A')} (ID: {user_info.get('current_chat_id', 'N/A')}) đã hết hạn.")
            if user_info.get('current_chat_id'):
                # Chạy coroutine trong event loop hiện tại
                asyncio.run_coroutine_threadsafe(
                    send_telegram_message(user_info['current_chat_id'], "⚠️ **Key của bạn đã hết hạn!**\nVui lòng liên hệ admin để gia hạn hoặc mua key mới."),
                    loop
                )

    for user_key in keys_to_remove:
        del user_data[user_key]

    if expired_count > 0:
        save_user_data()
        logger.warning(f"Đã xóa {expired_count} key hết hạn.")

# ==== VÒNG LẶP CHÍNH CỦA BOT ====
async def main_bot_loop():
    global last_processed_phien, lich_su, md5_giai_doan_counter, md5_analysis_result

    check_expired_keys() # Kiểm tra và xóa key hết hạn trước mỗi vòng lặp chính

    # Chỉ chạy vòng lặp chính nếu có ít nhất 1 key hợp lệ đang được sử dụng
    has_active_valid_keys = any(is_key_valid(info) and info.get('current_chat_id') for info in user_data.values())
    if not has_active_valid_keys:
        logger.info(f"Không có key hợp lệ đang hoạt động, bot tạm dừng kiểm tra phiên mới.")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS) # Chờ trước khi thử lại
        return

    data = get_data_from_api()
    if not data:
        logger.warning("Không lấy được dữ liệu từ API hoặc dữ liệu trống. Đang chờ...")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS) # Chờ trước khi thử lại
        return

    phien_hien_tai_api = data.get("phien_truoc")
    kq_thuc_te_api = data.get("ket_qua_truoc")
    xuc_xac_api = data.get("xuc_xac_truoc")
    lich_su_cau_api = data.get("lich_su_cau_api")

    if phien_hien_tai_api is None or not xuc_xac_api:
        logger.warning(f"Dữ liệu API chưa đầy đủ cho phiên hiện tại. Phien: {phien_hien_tai_api}, XX: {xuc_xac_api}")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
        return

    if last_processed_phien is None:
        logger.info(f"Lần chạy đầu tiên: Đang khởi tạo lịch sử bot và dự đoán phiên kế tiếp.")

        if lich_su_cau_api:
            lich_su = list(lich_su_cau_api[-MAX_PATTERN_LENGTH:])
            cap_nhat_lich_su_file()
            logger.info(f"Khởi tạo lịch sử bot từ API: {''.join(lich_su)}")
        else:
            logger.warning(f"Không có lịch sử cầu từ API để khởi tạo. Bắt đầu với lịch sử trống.")
            lich_su = []

        last_processed_phien = phien_hien_tai_api
        simulate_md5_analysis()

        phien_tiep_theo = phien_hien_tai_api + 1
        current_history_str_for_prediction = "".join(lich_su)
        all_predictions = get_all_predictions(current_history_str_for_prediction)
        final_choice = chot_keo_cuoi_cung(all_predictions)

        await send_prediction_notification(phien_tiep_theo, all_predictions, final_choice)
        pending_predictions[phien_tiep_theo] = {
            "history_str": current_history_str_for_prediction,
            "all_predictions": all_predictions,
            "final_choice": final_choice
        }
        log_prediction_data(phien_tiep_theo, current_history_str_for_prediction, all_predictions, final_choice)

        logger.info(f"Đã khởi tạo bot. Dự đoán phiên #{phien_tiep_theo}. Lịch sử cầu bot: {''.join(lich_su)}")
        return

    if phien_hien_tai_api > last_processed_phien:
        phien_expected_result = last_processed_phien + 1

        if phien_hien_tai_api != phien_expected_result:
            logger.warning(f"Cảnh báo: Phát hiện phiên nhảy cóc từ {last_processed_phien} lên {phien_hien_tai_api}.")

            # Xử lý các phiên bị bỏ lỡ
            for missed_phien in range(phien_expected_result, phien_hien_tai_api):
                if missed_phien in pending_predictions:
                    pending_predictions.pop(missed_phien, None)
                    logger.warning(f"Đã xóa dự đoán chờ xử lý cho phiên {missed_phien} (phiên bị bỏ lỡ).")

            # Cập nhật lịch sử với kết quả phiên hiện tại API
            lich_su.append(kq_thuc_te_api)
            lich_su = lich_su[-MAX_PATTERN_LENGTH:]
            cap_nhat_lich_su_file()

            last_processed_phien = phien_hien_tai_api
            simulate_md5_analysis()
            logger.info(f"Đã cập nhật lịch sử bot đến phiên {phien_hien_tai_api} do nhảy cóc.")

        else: # Phiên liên tiếp, xử lý như bình thường
            if phien_expected_result in pending_predictions:
                prediction_data = pending_predictions.pop(phien_expected_result)

                tong_hien_tai = sum(xuc_xac_api)
                await send_result_notification(phien_expected_result, xuc_xac_api, tong_hien_tai, kq_thuc_te_api, prediction_data)

                lich_su.append(kq_thuc_te_api)
                lich_su = lich_su[-MAX_PATTERN_LENGTH:]
                cap_nhat_lich_su_file()

                is_win = (prediction_data['final_choice']['ket_qua'] == kq_thuc_te_api) if prediction_data['final_choice']['ket_qua'] != "Bỏ qua" else None
                log_prediction_data(phien_expected_result, prediction_data['history_str'], prediction_data['all_predictions'], prediction_data['final_choice'], kq_thuc_te_api, is_win)

                ai_hoc_hoi(list(prediction_data['history_str']), kq_thuc_te_api)

                simulate_md5_analysis()
                last_processed_phien = phien_hien_tai_api

                logger.info(f"Đã xử lý kết quả phiên #{phien_expected_result}. Lịch sử cầu bot: {''.join(lich_su)}")
            else:
                logger.warning(f"Cảnh báo: Không tìm thấy dự đoán cho phiên {phien_expected_result} trong pending_predictions. Chỉ cập nhật lịch sử.")
                lich_su.append(kq_thuc_te_api)
                lich_su = lich_su[-MAX_PATTERN_LENGTH:]
                cap_nhat_lich_su_file()
                simulate_md5_analysis()
                last_processed_phien = phien_hien_tai_api


        phien_tiep_theo = phien_hien_tai_api + 1
        current_history_str_for_prediction = "".join(lich_su)

        all_predictions = get_all_predictions(current_history_str_for_prediction)
        final_choice = chot_keo_cuoi_cung(all_predictions)

        await send_prediction_notification(phien_tiep_theo, all_predictions, final_choice)

        pending_predictions[phien_tiep_theo] = {
            "history_str": current_history_str_for_history_str,
            "all_predictions": all_predictions,
            "final_choice": final_choice
        }
        log_prediction_data(phien_tiep_theo, current_history_str_for_prediction, all_predictions, final_choice)

        logger.info(f"Đã xử lý kết quả phiên #{phien_hien_tai_api}, dự đoán cho phiên #{phien_tiep_theo}. Lịch sử cầu bot: {''.join(lich_su)}. Dự đoán chờ xử lý: {list(pending_predictions.keys())}")


def simulate_md5_analysis():
    """Mô phỏng kết quả MD5: Cứ 2 lần phân tích MD5 cho kết quả 'Gãy' thì sẽ có 1 lần cho kết quả khác."""
    global md5_giai_doan_counter, md5_analysis_result
    # Dựa trên thông tin đã lưu trữ: "cứ 2 lần phân tích MD5 cho kết quả 'Gãy' thì sẽ có 1 lần cho kết quả khác."
    # (Đã lưu ý từ ngày 2025-06-03)
    if md5_giai_doan_counter < 2:
        md5_analysis_result = "Gãy"
        md5_giai_doan_counter += 1
    else:
        md5_analysis_result = "Khác"
        md5_giai_doan_counter = 0

# ==== HÀM XỬ LÝ LỆNH TELEGRAM ====

@bot.message_handler(commands=['start'])
async def start_command_handler(message):
    chat_id = message.chat.id
    _key, info = get_user_info_by_chat_id(chat_id)
    if info and info.get('associated_chat_id') == chat_id and is_key_valid(info):
        await send_telegram_message(chat_id, "✅ **Chào mừng bạn quay lại!**\nKey của bạn đã được kích hoạt. Bot sẽ tiếp tục gửi dự đoán.")
        logger.info(f"Người dùng {chat_id} (key: {_key}) đã bấm /start và key đã được kích hoạt tự động.")
    else:
        await send_telegram_message(chat_id, "🤖 **Chào mừng bạn đến với Tool TX Pro AI!**\nĐể sử dụng bot, vui lòng nhập key của bạn theo cú pháp: `/key [tên_key_của_bạn]`\n\nNếu bạn là Admin hoặc CTV của Quangdz, hãy nhập key mặc định của bạn (ví dụ: `/key quangdz`).")
        logger.info(f"Người dùng mới {chat_id} đã bấm /start. Đang chờ key.")


@bot.message_handler(commands=['key'])
async def process_key_command(message):
    chat_id = message.chat.id
    parts = message.text.split(maxsplit=1)

    if len(parts) < 2:
        await send_telegram_message(chat_id, "❌ **Sai cú pháp.** Vui lòng nhập key theo mẫu: `/key [tên_key_của_bạn]`")
        return

    user_key_input = parts[1].strip()
    await authenticate_user_key(chat_id, user_key_input)

@bot.message_handler(commands=['help']) # Xử lý lệnh /help cho người dùng thông thường
async def help_command_handler(message):
    chat_id = message.chat.id
    help_message = """
    **📚 CÁC LỆNH HỖ TRỢ:**

    - `/start`
      • Bắt đầu tương tác với Bot.

    - `/key [tên_key_của_bạn]`
      • Dùng để kích hoạt key đã được cấp.
      • _Ví dụ:_ `/key my_awesome_key`

    - `/help`
      • Hiển thị danh sách các lệnh hỗ trợ này.

    Nếu bạn là Admin, vui lòng sử dụng lệnh `/adminhelp` để xem các lệnh quản lý.
    """
    await send_telegram_message(chat_id, help_message)
    logger.info(f"Người dùng {chat_id} đã yêu cầu lệnh /help.")


@bot.message_handler(func=lambda message: True) # Xử lý tất cả các tin nhắn văn bản còn lại (phải đặt cuối cùng)
async def handle_text_messages(message):
    chat_id = message.chat.id
    text = message.text.strip()

    is_admin_user = is_admin_by_chat_id(chat_id)

    if is_admin_user: # Chỉ admin mới có quyền sử dụng các lệnh này
        parts = text.split()
        command = parts[0].lower()

        if command == '/addkey' and len(parts) >= 3:
            key_name = parts[1]
            duration_str = parts[2].lower()
            max_devices_str = parts[3].lower() if len(parts) >= 4 else "1"

            duration_hours = None
            if duration_str != "never":
                try:
                    duration_hours = float(duration_str.replace('h', ''))
                except ValueError:
                    await send_telegram_message(chat_id, "❌ Lỗi: Thời gian sử dụng không hợp lệ. Ví dụ: `24h` hoặc `never`.")
                    return

            max_devices = 1
            if max_devices_str == "multi":
                max_devices = -1
            elif max_devices_str.isdigit():
                max_devices = int(max_devices_str)
                if max_devices <= 0:
                     await send_telegram_message(chat_id, "❌ Lỗi: Số lượng thiết bị phải là số dương hoặc 'multi'.")
                     return
            else:
                await send_telegram_message(chat_id, "❌ Lỗi: Số lượng thiết bị không hợp lệ. Ví dụ: `1` hoặc `multi`.")
                return

            if await add_update_key(chat_id, key_name, duration_hours, "user", max_devices):
                expiry_display = f"{duration_hours} giờ" if duration_hours is not None else "Vĩnh viễn"
                devices_display = f"{max_devices} thiết bị" if max_devices != -1 else "Đa thiết bị"
                await send_telegram_message(chat_id, f"✅ Đã tạo/cập nhật key '{key_name}' có hiệu lực trong {expiry_display} cho {devices_display}.")
                logger.info(f"Admin {chat_id} đã cấp key '{key_name}' cho {expiry_display}, {devices_display}.")
            else:
                await send_telegram_message(chat_id, f"❌ Không thể tạo/cập nhật key '{key_name}'.")
            return

        elif command == '/ctv' and len(parts) >= 2:
            key_name = parts[1]
            if await add_update_key(chat_id, key_name, duration_hours=None, role="ctv", max_devices=-1):
                await send_telegram_message(chat_id, f"✅ Đã cấp quyền CTV vĩnh viễn cho key '{key_name}'.")
                logger.info(f"Admin {chat_id} đã cấp quyền CTV cho '{key_name}'.")
            else:
                await send_telegram_message(chat_id, f"❌ Không thể cấp quyền CTV cho '{key_name}'.")
            return

        elif command == '/delkey' and len(parts) >= 2:
            key_name_to_delete = parts[1]
            if await remove_user_key(chat_id, key_name_to_delete):
                await send_telegram_message(chat_id, f"✅ Đã xóa key '{key_name_to_delete}' khỏi hệ thống.")
                logger.info(f"Admin {chat_id} đã xóa key '{key_name_to_delete}'.")
            else:
                await send_telegram_message(chat_id, f"❌ Không tìm thấy key '{key_name_to_delete}' hoặc không thể xóa.")
            return

        elif command == '/listkeys':
            key_list_message = ["📋 **DANH SÁCH KEY ĐANG HOẠT ĐỘNG** 📋"]
            if not user_data:
                key_list_message.append("Không có key nào trong hệ thống.")
            else:
                for key_name, info in user_data.items():
                    status = "Đang hoạt động" if is_key_valid(info) else "Hết hạn"
                    expiry = "Vĩnh viễn" if info['expiry_time'] == "never" else datetime.fromisoformat(info['expiry_time']).strftime("%Y-%m-%d %H:%M:%S")
                    current_dev = f"Đang dùng bởi: <code>{info['current_chat_id']}</code>" if info['current_chat_id'] else "Chưa kích hoạt"
                    devices_limit = f"({info['max_devices']} thiết bị)" if info['max_devices'] != -1 else "(Đa thiết bị)"

                    associated_id = f"ID liên kết: <code>{info['associated_chat_id']}</code>" if info['associated_chat_id'] else ""

                    key_list_message.append(f"• <b>Key: {info['key']}</b> - Username: {info.get('username', 'N/A')}\n  Vai trò: {info['role'].upper()}, Hạn: {expiry}, TB: {devices_limit}\n  Trạng thái: {status}, {current_dev} {associated_id}")
            await send_telegram_message(chat_id, "\n".join(key_list_message))
            logger.info(f"Admin {chat_id} đã yêu cầu danh sách key.")
            return

        elif command == '/capkey' and len(parts) >= 3:
            # Cú pháp: /capkey [chat_id] [duration_hours]
            target_chat_id_str = parts[1]
            duration_str = parts[2].lower()

            duration_hours = None
            if duration_str != "never":
                try:
                    duration_hours = float(duration_str.replace('h', ''))
                except ValueError:
                    await send_telegram_message(chat_id, "❌ Lỗi: Thời gian sử dụng không hợp lệ. Ví dụ: `24h` hoặc `never`.")
                    return

            await add_key_by_chat_id(chat_id, target_chat_id_str, duration_hours)
            return

        elif command == '/adminhelp':
            help_message = """
            **LỆNH ADMIN:**
            - `/addkey [key_name] [duration] [devices]`
              • `key_name`: Tên key (ví dụ: `user123`)
              • `duration`: Thời gian (ví dụ: `24h`, `720h`) hoặc `never` (vĩnh viễn)
              • `devices`: Số lượng thiết bị (ví dụ: `1`, `2`) hoặc `multi` (không giới hạn)
              _Ví dụ:_ `/addkey newuser 48h 1` (tạo key `newuser` 48h, 1 thiết bị)
              _Ví dụ:_ `/addkey vipuser never multi` (tạo key `vipuser` vĩnh viễn, đa thiết bị)

            - `/ctv [key_name]`
              • Tạo key cho CTV (vĩnh viễn, đa thiết bị).
              _Ví dụ:_ `/ctv ctv_team_A`

            - `/capkey [telegram_chat_id] [duration]`
              • Cấp key trực tiếp cho một ID Telegram. Key sẽ là đa thiết bị.
              • `telegram_chat_id`: ID số của người chơi (ví dụ: `123456789`)
              • `duration`: Thời gian (ví dụ: `24h`, `never`)
              _Ví dụ:_ `/capkey 123456789 24h` (cấp key cho ID 123456789 dùng 24h, tự động kích hoạt)

            - `/delkey [key_name]`
              • Xóa một key khỏi hệ thống.
              _Ví dụ:_ `/delkey olduser`

            - `/listkeys`
              • Liệt kê tất cả các key đang có trong hệ thống.

            - `/adminhelp`
              • Hiển thị các lệnh admin này.
            """
            await send_telegram_message(chat_id, help_message)
            return

    # Nếu không phải admin hoặc không phải lệnh admin, chỉ là tin nhắn thường
    _key, user_info_by_chat_id = get_user_info_by_chat_id(chat_id)
    # Nếu chat_id này có key được gán trực tiếp qua /capkey VÀ key đó còn hạn, thì thông báo đã kích hoạt
    if user_info_by_chat_id and user_info_by_chat_id.get('associated_chat_id') == chat_id and is_key_valid(user_info_by_chat_id):
        await send_telegram_message(chat_id, "🤖 Bạn đã kích hoạt bot. Vui lòng chờ dự đoán phiên mới hoặc sử dụng `/key [tên_key_của_bạn]` để kích hoạt lại nếu muốn.")
    elif user_info_by_chat_id and is_key_valid(user_info_by_chat_id):
        # Người dùng có key (kiểu /addkey) và đã đăng nhập
        await send_telegram_message(chat_id, "🤖 Bạn đã kích hoạt bot. Vui lòng chờ dự đoán phiên mới hoặc sử dụng `/key [tên_key_của_bạn]` để kích hoạt lại nếu muốn.")
    else:
        # Nếu chưa đăng nhập hoặc key hết hạn
        await send_telegram_message(chat_id, "🤖 **Chào mừng bạn đến với Tool TX Pro AI!**\nĐể sử dụng bot, vui lòng nhập key của bạn theo cú pháp: `/key [tên_key_của_bạn]`\nSử dụng `/help` để xem các lệnh hỗ trợ.")

    logger.info(f"Chat ID {chat_id} gửi tin nhắn không phải lệnh: '{text}'.")


# ==== FLASK SERVER ĐỂ GIỮ DỊCH VỤ LUÔN CHẠY TRÊN RENDER (NẾU DÙNG WEB SERVICE) ====
app = Flask(__name__)

@app.route('/')
def hello_world():
    status_msg = "Bot is running"
    if not any(is_key_valid(info) and info.get('current_chat_id') for info in user_data.values()):
        status_msg += ", No active valid keys"

    return f'{status_msg} and Flask server is active!'

def run_flask_app():
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"Bắt đầu Flask server trên cổng {port} để giữ dịch vụ luôn chạy...")
    # Flask sẽ blocking ở đây, nên chạy nó trong một thread riêng
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False) # use_reloader=False để tránh chạy 2 lần

async def run_main_loop_periodically():
    while True:
        try:
            await main_bot_loop()
        except Exception as e:
            logger.error(f"Lỗi trong vòng lặp chính: {e}", exc_info=True)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)

async def main():
    # Không cần dòng global bot nữa vì bot đã được khởi tạo ở phạm vi global
    # và không gán lại giá trị cho nó ở đây.

    # === CẤU HÌNH TOKEN BOT ===
    # KHUYẾN NGHỊ KHI TRIỂN KHAI LÊN RENDER:
    # BỎ COMMENT DÒNG NÀY:
    # TELEGRAM_BOT_TOKEN_ENV = os.getenv("TELEGRAM_BOT_TOKEN")
    # if TELEGRAM_BOT_TOKEN_ENV:
    #     global bot
    #     bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN_ENV, parse_mode='HTML')
    #     logger.info("Bot đã được khởi tạo bằng biến môi trường TELEGRAM_BOT_TOKEN.")
    # else:
    #     logger.warning("Không tìm thấy biến môi trường TELEGRAM_BOT_TOKEN. Sẽ sử dụng token cứng đã cấu hình.")
    #     # Nếu bạn muốn bot ngừng chạy nếu không có biến môi trường, hãy uncomment dòng dưới:
    #     # logger.critical("LỖI: Biến môi trường TELEGRAM_BOT_TOKEN chưa được đặt. Bot sẽ không chạy.")
    #     # return
    # === KẾT THÚC CẤU HÌNH TOKEN BOT ===

    load_data() # Tải dữ liệu ban đầu (bao gồm user_data và khởi tạo key admin mặc định)
    logger.info("=== TOOL TX PRO AI V3 (CHỦ ĐỘNG) ===")
    logger.info("Bot đã sẵn sàng.")

    # Khởi chạy Flask server trong một thread riêng
    flask_thread = threading.Thread(target=run_flask_app)
    flask_thread.daemon = True # Daemon thread sẽ tự động kết thúc khi main program kết thúc
    flask_thread.start()
    logger.info("Flask server thread đã khởi chạy.")

    # Bắt đầu vòng lặp chính để kiểm tra API và xử lý phiên
    asyncio.create_task(run_main_loop_periodically())
    logger.info("Vòng lặp bot chính đã được lên lịch.")

    logger.info("Bắt đầu polling Telegram...")
    try:
        # Sử dụng polling của pyTelegramBotAPI để nhận tin nhắn
        # Đảm bảo none_stop=True để bot tiếp tục chạy ngay cả khi có lỗi nhỏ
        # timeout được đặt để tránh block quá lâu nếu không có cập nhật
        # Long polling giúp bot phản hồi nhanh hơn
        await asyncio.to_thread(bot.infinity_polling, none_stop=True, interval=0, timeout=60)
    except Exception as e:
        logger.critical(f"Lỗi nghiêm trọng trong polling bot Telegram: {e}", exc_info=True)

if __name__ == "__main__":
    try:
        # Nếu chạy trên Render, các lệnh os.system('cls') sẽ không cần thiết
        # và có thể gây lỗi. Đã loại bỏ.
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Đã dừng bot do người dùng ngắt (Ctrl+C).")
    except Exception as e:
        logger.critical(f"Lỗi nghiêm trọng khi khởi động bot: {e}", exc_info=True)
