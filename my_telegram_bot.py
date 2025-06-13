import os
import json
import time
import sqlite3 # Import SQLite
from datetime import datetime, timedelta
from collections import defaultdict, Counter
import requests
import asyncio
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters,
    ExtBot
)
from flask import Flask, request, abort
import logging

# Thiết lập logging cơ bản
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==== CẤU HÌNH ====
HTTP_API_URL = "https://apisunwin.up.railway.app/api/taixiu"
# Cảnh báo: Các file này không bền vững trên Render nếu không dùng Database hoặc Persistent Disk!
# Database cho key và user sẽ dùng SQLite cho ví dụ này, nhưng vẫn cần Persistent Disk.
# Với Render, bạn nên dùng Add-ons (PostgreSQL) hoặc Disk/Volume.
DATABASE_FILE = "bot_data.db"
DUDOAN_FILE = "dudoan.txt" # File chứa các pattern dự đoán VIP (AI 1)
AI_FILE = "ai_1-2.txt"    # File chứa các pattern AI tự học (AI 2)
# pattern_counter.json và lichsucau.txt sẽ chỉ dùng trong bộ nhớ trong ví dụ này để tránh mất dữ liệu liên tục trên Render.
# Để bền vững, chúng cũng cần lưu vào DB.
CHECK_INTERVAL_SECONDS = 5
MIN_PATTERN_LENGTH = 4
MAX_PATTERN_LENGTH = 15
AI_LEARN_THRESHOLD_COUNT = 5
AI_LEARN_THRESHOLD_RATE = 75

# --- MÀU SẮC CHO CONSOLE ---
RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, RESET, BOLD = "\033[91m", "\033[92m", "\033[93m", "\033[94m", "\033[95m", "\033[96m", "\033[0m", "\033[1m"

# ==== BIẾN TOÀN CỤC ====
lich_su = []
pattern_counter = defaultdict(lambda: {"T": 0, "X": 0})
last_processed_phien = None
cau_dudoan = {}
cau_ai = {}
win_rate_tracker = defaultdict(list)

application: Application = None
flask_app = Flask(__name__)

md5_giai_doan_counter = 0
md5_analysis_result = "Khác"

# Không cần tracked_chat_id nữa vì user sẽ được lưu trong DB

# ==== HÀM QUẢN LÝ DATABASE ====
def get_db_connection():
    """Thiết lập kết nối tới database."""
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row # Cho phép truy cập cột bằng tên
    return conn

def init_db():
    """Khởi tạo các bảng trong database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            key_name TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            activated_at TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS keys (
            name TEXT PRIMARY KEY,
            expiration_date TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            is_used INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()
    logger.info("Database initialized.")

    # Thêm các key admin mặc định nếu chưa tồn tại
    conn = get_db_connection()
    cursor = conn.cursor()
    admin_keys = ["quangdz", "TrungNgu"]
    # Key admin mặc định có thời hạn rất dài
    default_admin_expiry = (datetime.now() + timedelta(days=365 * 10)).strftime("%d/%m/%Y/%H:%M")

    for key_name in admin_keys:
        cursor.execute("SELECT name FROM keys WHERE name = ?", (key_name,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO keys (name, expiration_date, is_admin, is_used) VALUES (?, ?, ?, ?)",
                           (key_name, default_admin_expiry, 1, 0))
            logger.info(f"Added default admin key: {key_name}")
    conn.commit()
    conn.close()

def add_user(chat_id: int, key_name: str, is_admin: bool):
    """Thêm người dùng mới vào database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT OR REPLACE INTO users (chat_id, key_name, is_admin, activated_at) VALUES (?, ?, ?, ?)",
                       (chat_id, key_name, 1 if is_admin else 0, datetime.now().isoformat()))
        conn.commit()
        logger.info(f"User {chat_id} registered with key {key_name}. Admin: {is_admin}")
    except sqlite3.Error as e:
        logger.error(f"Error adding user {chat_id}: {e}")
    finally:
        conn.close()

def get_user_by_chat_id(chat_id: int):
    """Lấy thông tin người dùng từ chat_id."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def add_key(name: str, expiration_date: datetime, is_admin: bool):
    """Thêm key mới vào database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO keys (name, expiration_date, is_admin, is_used) VALUES (?, ?, ?, ?)",
                       (name, expiration_date.strftime("%d/%m/%Y/%H:%M"), 1 if is_admin else 0, 0))
        conn.commit()
        logger.info(f"Key '{name}' added. Expiration: {expiration_date}, Admin: {is_admin}")
        return True
    except sqlite3.IntegrityError:
        logger.warning(f"Key '{name}' already exists.")
        return False
    except sqlite3.Error as e:
        logger.error(f"Error adding key '{name}': {e}")
        return False
    finally:
        conn.close()

def get_key_details(name: str):
    """Lấy thông tin chi tiết của một key."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM keys WHERE name = ?", (name,))
    key = cursor.fetchone()
    conn.close()
    return key

def deactivate_key(name: str):
    """Đánh dấu một key đã được sử dụng."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE keys SET is_used = 1 WHERE name = ?", (name,))
        conn.commit()
        logger.info(f"Key '{name}' marked as used.")
        return True
    except sqlite3.Error as e:
        logger.error(f"Error deactivating key '{name}': {e}")
        return False
    finally:
        conn.close()

def is_admin(chat_id: int):
    """Kiểm tra xem người dùng có phải admin không."""
    user = get_user_by_chat_id(chat_id)
    return user and user['is_admin'] == 1

def get_active_user_count():
    """Đếm số người dùng đang hoạt động."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(DISTINCT chat_id) FROM users")
    count = cursor.fetchone()[0]
    conn.close()
    return count

def get_all_keys():
    """Lấy danh sách tất cả các key."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name, expiration_date, is_admin, is_used FROM keys")
    keys = cursor.fetchall()
    conn.close()
    return keys

def get_admin_count():
    """Đếm số admin hiện có."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(DISTINCT chat_id) FROM users WHERE is_admin = 1")
    count = cursor.fetchone()[0]
    conn.close()
    return count

# ==== CÁC HÀM TIỆN ÍCH CƠ BẢN ====

def tai_xiu(tong):
    return "T" if tong >= 11 else "X"

def load_lich_su():
    global lich_su
    logger.info("Ignoring loading lichsucau.txt for persistent storage on Render.")
    lich_su = [] # Bắt đầu trống để tránh lỗi nếu không có DB

def cap_nhat_lich_su(kq):
    global lich_su
    lich_su.append(kq)
    lich_su = lich_su[-MAX_PATTERN_LENGTH:]
    logger.info(f"Updated lich_su in memory: {''.join(lich_su)}")

def load_patterns_from_file(filepath):
    patterns = {}
    absolute_filepath = os.path.join(os.path.dirname(__file__), filepath)
    if os.path.exists(absolute_filepath):
        try:
            with open(absolute_filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or "=>" not in line: continue
                    try:
                        parts = line.split("=>")
                        pattern = parts[0].strip()
                        prediction = parts[1].split("Dự đoán:")[1].strip()[0]
                        if prediction in ["T", "X"]:
                            patterns[pattern] = prediction
                    except (IndexError, KeyError) as e:
                        logger.warning(f"Skipping malformed pattern line in {filepath}: {line} ({e})")
                        continue
        except IOError as e:
            logger.error(f"Lỗi khi đọc file cầu '{filepath}': {e}")
    else:
        logger.warning(f"File pattern '{filepath}' không tồn tại tại {absolute_filepath}. Đảm bảo nó được include trong repo.")
    return patterns

def load_all_patterns():
    global cau_dudoan, cau_ai
    cau_dudoan = load_patterns_from_file(DUDOAN_FILE)
    cau_ai = load_patterns_from_file(AI_FILE)
    logger.info(f"Loaded {len(cau_dudoan)} patterns from {DUDOAN_FILE}")
    logger.info(f"Loaded {len(cau_ai)} patterns from {AI_FILE}")

def load_pattern_counter():
    global pattern_counter
    logger.info("Ignoring loading pattern_counter.json for persistent storage on Render.")
    pattern_counter = defaultdict(lambda: {"T": 0, "X": 0})

def save_pattern_counter():
    logger.info("Ignoring saving pattern_counter.json for persistent storage on Render.")

def get_data_from_api():
    try:
        response = requests.get(HTTP_API_URL, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Lỗi khi gọi API: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Lỗi giải mã JSON từ API: {e}")
        return None

# ==== LOGIC DỰ ĐOÁN ĐA NGUỒN ====

def get_prediction_from_source(history_str, source_patterns, source_name):
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
    for length in range(min(len(history_str), MAX_PATTERN_LENGTH), MIN_PATTERN_LENGTH - 1, -1):
        pat = history_str[-length:]
        if pat in pattern_counter:
            counts = pattern_counter[pat]
            total = counts['T'] + counts['X']
            if total > 0:
                rate_T = (counts['T'] / total) * 100
                rate_X = (counts['X'] / total) * 100
                if rate_T > rate_X:
                    return {"prediction": "T", "pattern": pat, "accuracy": rate_T, "source": "Thống Kê"}
                elif rate_X > rate_T:
                    return {"prediction": "X", "pattern": pat, "accuracy": rate_X, "source": "Thống Kê"}
    return None

def chot_keo_cuoi_cung(predictions):
    valid_preds = [p for p in predictions if p is not None]
    if not valid_preds:
        return {"ket_qua": "Bỏ qua", "ly_do": "Không có AI nào đưa ra tín hiệu."}

    votes = Counter(p['prediction'] for p in valid_preds)

    if len(votes) == 1:
        final_prediction = list(votes.keys())[0]
        return {
            "ket_qua": final_prediction,
            "ly_do": f"Đồng thuận {len(valid_preds)}/{len(valid_preds)}.",
            "confidence": "Rất Cao"
        }

    sorted_votes = sorted(votes.items(), key=lambda item: (item[1], item[0]), reverse=True)
    if len(sorted_votes) > 1 and sorted_votes[0][1] == sorted_votes[1][1]:
        best_pred = max(valid_preds, key=lambda p: p['accuracy'])
        return {
            "ket_qua": best_pred['prediction'],
            "ly_do": f"Hòa phiếu, ưu tiên {best_pred['source']} với độ chính xác cao nhất ({best_pred['accuracy']:.1f}%).",
            "confidence": "Trung Bình"
        }
    else:
        final_prediction = sorted_votes[0][0]
        return {
            "ket_qua": final_prediction,
            "ly_do": f"Số đông nghiêng về {final_prediction} ({sorted_votes[0][1]}/{len(valid_preds)}).",
            "confidence": "Cao"
        }

def simulate_md5_analysis():
    """
    Simulates the MD5 analysis result based on the rule:
    For every 2 'Gãy' results, there will be 1 'Khác' result.
    This rule is based on the saved information from 2025-06-03.
    """
    global md5_giai_doan_counter, md5_analysis_result

    logger.info(f"MD5 counter before simulation: {md5_giai_doan_counter}")
    if md5_giai_doan_counter < 2:
        md5_giai_doan_counter += 1
        md5_analysis_result = "Gãy"
    else:
        md5_giai_doan_counter = 0 # Reset counter after 2 'Gãy'
        md5_analysis_result = "Khác"
    logger.info(f"MD5 analysis result simulated: {md5_analysis_result} (Counter: {md5_giai_doan_counter})")
    return md5_analysis_result

def ai_hoc_hoi(history_before_result, actual_result):
    global md5_analysis_result

    current_md5_result = simulate_md5_analysis()
    logger.info(f"Kết quả phân tích MD5 mô phỏng: {current_md5_result}")

    if current_md5_result == "Gãy":
        logger.warning("MD5 phân tích 'Gãy', AI sẽ KHÔNG học từ phiên này để tránh sai lệch.")
        return

    history_str = "".join(history_before_result)
    for length in range(MIN_PATTERN_LENGTH, min(len(history_str), MAX_PATTERN_LENGTH) + 1):
        pat = history_str[-length:]
        pattern_counter[pat][actual_result] += 1

    potential_pat = history_str[-(MIN_PATTERN_LENGTH):]
    if len(potential_pat) >= MIN_PATTERN_LENGTH:
        if potential_pat not in cau_dudoan and potential_pat not in cau_ai:
            counts = pattern_counter[potential_pat]
            total = counts['T'] + counts['X']
            if total >= AI_LEARN_THRESHOLD_COUNT:
                rate_T = (counts['T'] / total) * 100
                rate_X = (counts['X'] / total) * 100
                prediction_to_learn = None
                if rate_T >= AI_LEARN_THRESHOLD_RATE:
                    prediction_to_learn = 'T'
                elif rate_X >= AI_LEARN_THRESHOLD_RATE:
                    prediction_to_learn = 'X'

                if prediction_to_learn:
                    absolute_ai_filepath = os.path.join(os.path.dirname(__file__), AI_FILE)
                    try:
                        with open(absolute_ai_filepath, "a", encoding="utf-8") as f:
                            f.write(f"\n{potential_pat} => Dự đoán: {prediction_to_learn} - Loại cầu: AI Tự Học")
                        load_all_patterns()
                        logger.info(f"AI 2 đã học pattern mới: {potential_pat} => {prediction_to_learn} (Lưu ý: Không bền vững)")
                    except IOError as e:
                        logger.error(f"Lỗi khi ghi cầu mới của AI: {e}")
    save_pattern_counter()

# ==== HÀM GỬI TIN NHẮN TELEGRAM ====
async def send_telegram_message(context: ContextTypes.DEFAULT_TYPE, message_text: str, chat_id_to_send: int):
    if chat_id_to_send:
        try:
            await context.bot.send_message(chat_id=chat_id_to_send, text=message_text, parse_mode='HTML')
            logger.info(f"Đã gửi tin nhắn Telegram tới {chat_id_to_send}")
        except Exception as e:
            logger.error(f"Lỗi khi gửi tin nhắn Telegram tới {chat_id_to_send}: {e}")
    else:
        logger.warning("Chưa có Chat ID để gửi tin nhắn Telegram.")

async def hien_thi_telegram(context: ContextTypes.DEFAULT_TYPE, chat_id_to_send: int, phien, xx, tong, kq_thucte, predictions, final_choice, win_tracker):
    message_parts = []
    message_parts.append(f"<b>===== PHIÊN {phien} ({datetime.now().strftime('%H:%M:%S')}) =====</b>")
    message_parts.append(f"🎲 Xúc xắc      : <b>{xx[0]} - {xx[1]} - {xx[2]}</b>  =>  Tổng: <b>{tong}</b>")

    kq_text = f"<b><span style='color:green;'>TÀI</span></b>" if kq_thucte == 'T' else f"<b><span style='color:red;'>XỈU</span></b>"
    message_parts.append(f"📌 Kết quả phiên : {kq_text} ({kq_thucte})")
    message_parts.append(f"📜 Lịch sử cầu  : {''.join(lich_su)}")
    message_parts.append("--------------------------------------------------------------------")
    message_parts.append("<b>PHÂN TÍCH TỪ CÁC AI:</b>")

    sources_to_display = [
        ("Dự Đoán AI 1 🤖", "VIP"),
        ("Dự Đoán AI 2 🤖", "AI Tự Học"),
        ("Dự Đoán AI 3 🤖", "Thống Kê")
    ]
    predictions_by_source_key = {p['source']: p for p in predictions if p is not None}

    for display_name, internal_source_key in sources_to_display:
        pred = predictions_by_source_key.get(internal_source_key)
        if pred:
            dd_text = f"<b><span style='color:green;'>TÀI</span></b>" if pred['prediction'] == 'T' else f"<b><span style='color:red;'>XỈU</span>"
            acc_color = "green" if pred['accuracy'] >= 75 else "orange" if pred['accuracy'] >= 60 else "red"

            source_wins = win_tracker[internal_source_key].count(True)
            source_total_predictions = len(win_tracker[internal_source_key])

            win_rate_display = ""
            if source_total_predictions > 0:
                win_rate_value = (source_wins / source_total_predictions) * 100
                win_rate_color = "green" if win_rate_value >= 70 else "orange" if win_rate_value >= 50 else "red"
                win_rate_display = f", TL Thắng: <span style='color:{win_rate_color};'>{win_rate_value:.1f}%</span>"
            else:
                win_rate_display = ", TL Thắng: N/A"

            message_parts.append(
                f"  - <b>{display_name}</b>: {dd_text} (Cầu: {pred['pattern']}, CX: <span style='color:{acc_color};'>{pred['accuracy']:.1f}%</span>{win_rate_display})"
            )
        else:
            message_parts.append(f"  - <b>{display_name}</b>: <i>Không có tín hiệu</i>")

    message_parts.append("--------------------------------------------------------------------")
    message_parts.append("<b>DỰ ĐOÁN CUỐI CÙNG:</b>")
    final_kq = final_choice['ket_qua']

    if final_kq == "Bỏ qua":
        message_parts.append(f"  => <b><span style='color:orange;'>KHUYẾN NGHỊ: BỎ QUA PHIÊN NÀY</span></b>")
    else:
        final_text = f"<b><span style='color:green;'>TÀI</span></b>" if final_kq == 'T' else f"<b><span style='color:red;'>XỈU</span>"
        confidence = final_choice.get('confidence', 'Không xác định')

        if confidence == "Rất Cao": conf_color = "green"
        elif confidence == "Cao": conf_color = "orange"
        else: conf_color = "red"

        message_parts.append(f"  => <b>KHUYẾN NGHỊ: {final_text}</b> ({final_choice['ly_do']})")
        message_parts.append(f"  => <b>ĐỘ TIN CẬY: <span style='color:{conf_color};'>{confidence.upper()}</span></b>")

    message_parts.append("--------------------------------------------------------------------")
    global md5_analysis_result
    md5_status_color = "red" if md5_analysis_result == "Gãy" else "green"
    message_parts.append(f"<b>Trạng thái MD5: <span style='color:{md5_status_color};'>{md5_analysis_result.upper()}</span></b>")
    message_parts.append("--------------------------------------------------------------------")
    message_parts.append("Powered by <b>TX Pro AI</b> 🤖")

    await send_telegram_message(context, "\n".join(message_parts), chat_id_to_send)

# ==== LOGIC XỬ LÝ PHIÊN ====
async def process_taixiu_prediction(context: ContextTypes.DEFAULT_TYPE, chat_id_to_send: int):
    user = get_user_by_chat_id(chat_id_to_send)
    if not user:
        await send_telegram_message(context, "⚠️ Bạn chưa kích hoạt bot. Vui lòng sử dụng lệnh /start và nhập key để kích hoạt.", chat_id_to_send)
        return

    # Check key expiration
    key_details = get_key_details(user['key_name'])
    if key_details:
        try:
            expiration_dt = datetime.strptime(key_details['expiration_date'], "%d/%m/%Y/%H:%M")
            if datetime.now() > expiration_dt:
                await send_telegram_message(context, "🚫 Key của bạn đã hết hạn. Vui lòng liên hệ admin để gia hạn hoặc nhận key mới.", chat_id_to_send)
                return
        except ValueError:
            logger.error(f"Invalid date format for key {key_details['name']}: {key_details['expiration_date']}")
            await send_telegram_message(context, "🚫 Key của bạn có định dạng ngày hết hạn không hợp lệ. Vui lòng liên hệ admin.", chat_id_to_send)
            return
    else:
        await send_telegram_message(context, "🚫 Key của bạn không tồn tại trong hệ thống. Vui lòng liên hệ admin.", chat_id_to_send)
        return

    global last_processed_phien, win_rate_tracker

    logger.info("Bắt đầu xử lý dự đoán Tài Xỉu...")
    data = get_data_from_api()
    if not data or not isinstance(data, dict):
        await send_telegram_message(context, "❌ Lỗi: Không lấy được dữ liệu API hoặc dữ liệu không hợp lệ.", chat_id_to_send)
        return

    phien_api = data.get("Phien")
    xx1, xx2, xx3 = data.get("Xuc_xac_1"), data.get("Xuc_xac_2"), data.get("Xuc_xac_3")

    if phien_api is None or not all(isinstance(x, int) for x in [xx1, xx2, xx3]):
        await send_telegram_message(context, "❌ Lỗi: Dữ liệu phiên hoặc xúc xắc từ API không hợp lệ.", chat_id_to_send)
        return

    if last_processed_phien is None or phien_api > last_processed_phien:
        history_before = list(lich_su)
        history_str = "".join(history_before)

        pred_vip = get_prediction_from_source(history_str, cau_dudoan, "VIP")
        pred_ai_file = get_prediction_from_source(history_str, cau_ai, "AI Tự Học")
        pred_stat = get_statistical_prediction(history_str)
        all_predictions = [pred_vip, pred_ai_file, pred_stat]

        final_choice = chot_keo_cuoi_cung(all_predictions)

        tong = xx1 + xx2 + xx3
        kq_thucte = tai_xiu(tong)

        for pred_obj in all_predictions:
            if pred_obj:
                source_key = pred_obj['source']
                predicted_outcome = pred_obj['prediction']
                win_rate_tracker[source_key].append(predicted_outcome == kq_thucte)

        cap_nhat_lich_su(kq_thucte)

        await hien_thi_telegram(context, chat_id_to_send, phien_api, [xx1, xx2, xx3], tong, kq_thucte, all_predictions, final_choice, win_rate_tracker)

        ai_hoc_hoi(history_before, kq_thucte)
        last_processed_phien = phien_api
        logger.info(f"Phiên {phien_api} đã được xử lý thành công.")
    else:
        logger.info(f"Phiên {phien_api} đã được xử lý trước đó hoặc không có phiên mới.")
        await send_telegram_message(context, "ℹ️ Hiện tại không có phiên mới để phân tích. Vui lòng thử lại sau.", chat_id_to_send)


# ==== XỬ LÝ LỆNH TELEGRAM ====
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user_by_chat_id(chat_id)

    if user:
        key_details = get_key_details(user['key_name'])
        if key_details:
            try:
                expiration_dt = datetime.strptime(key_details['expiration_date'], "%d/%m/%Y/%H:%M")
                if datetime.now() < expiration_dt:
                    await update.message.reply_html(
                        f"Chào mừng trở lại <b>{user['key_name']}</b>! 🤖\n"
                        f"Key của bạn hết hạn vào: {key_details['expiration_date']}\n"
                        "Gửi lệnh /du_doan để nhận dự đoán phiên Tài Xỉu mới nhất."
                    )
                    return
                else:
                    await update.message.reply_html(
                        f"Chào mừng trở lại! 🤖 Key <b>{user['key_name']}</b> của bạn đã hết hạn vào {key_details['expiration_date']}.\n"
                        "Vui lòng nhập key mới bằng lệnh /register_key <your_key_here> hoặc liên hệ admin để gia hạn."
                    )
            except ValueError:
                await update.message.reply_html(
                    f"Chào mừng trở lại! 🤖 Key <b>{user['key_name']}</b> của bạn có định dạng ngày hết hạn không hợp lệ.\n"
                    "Vui lòng liên hệ admin để kiểm tra key."
                )
        else:
            await update.message.reply_html(
                f"Chào mừng trở lại! 🤖 Key <b>{user['key_name']}</b> của bạn không còn tồn tại trong hệ thống.\n"
                "Vui lòng nhập key mới bằng lệnh /register_key <your_key_here> hoặc liên hệ admin."
            )
    else:
        await update.message.reply_html(
            "Chào mừng bạn đến với <b>TX Pro AI</b>! 🤖\n"
            "Để sử dụng bot, vui lòng nhập key của bạn bằng lệnh:\n"
            "<code>/register_key &lt;your_key_here&gt;</code>"
        )

async def register_key_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if len(context.args) == 0:
        await update.message.reply_text("Vui lòng cung cấp key của bạn. Ví dụ: `/register_key mysecretkey`")
        return

    user_key = context.args[0]
    key_details = get_key_details(user_key)

    if not key_details:
        await update.message.reply_text("❌ Key này không tồn tại hoặc không hợp lệ. Vui lòng kiểm tra lại.")
        return

    if key_details['is_used'] == 1:
        await update.message.reply_text("❌ Key này đã được sử dụng. Vui lòng sử dụng một key khác hoặc liên hệ admin.")
        return

    try:
        expiration_dt = datetime.strptime(key_details['expiration_date'], "%d/%m/%Y/%H:%M")
        if datetime.now() > expiration_dt:
            await update.message.reply_text("❌ Key này đã hết hạn. Vui lòng liên hệ admin để nhận key mới.")
            return
    except ValueError:
        await update.message.reply_text("❌ Key này có định dạng ngày hết hạn không hợp lệ. Vui lòng liên hệ admin.")
        return

    add_user(chat_id, user_key, key_details['is_admin'] == 1)
    deactivate_key(user_key) # Mark key as used after successful registration
    await update.message.reply_html(
        f"✅ Kích hoạt thành công với key <b>{user_key}</b>!\n"
        f"Key của bạn hết hạn vào: <b>{key_details['expiration_date']}</b>.\n"
        "Bây giờ bạn có thể sử dụng lệnh /du_doan để nhận dự đoán."
    )
    if key_details['is_admin'] == 1:
        await update.message.reply_html("Bạn đã được cấp quyền Admin! Sử dụng lệnh /admin_help để xem các lệnh quản lý.")


async def du_doan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user_by_chat_id(chat_id)

    if not user:
        await update.message.reply_html(
            "⚠️ Bạn chưa kích hoạt bot. Vui lòng sử dụng lệnh /start và nhập key để kích hoạt."
        )
        return

    # User is registered, proceed with prediction
    await update.message.reply_text("Đang lấy dữ liệu và phân tích dự đoán...")
    await process_taixiu_prediction(context, chat_id)

async def admin_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not is_admin(chat_id):
        await update.message.reply_text("Bạn không có quyền admin để sử dụng lệnh này.")
        return
    await update.message.reply_html(
        "<b>Lệnh Admin:</b>\n"
        "  /gen_key &lt;tên_key&gt; &lt;ngày/tháng/năm/giờ&gt; [admin]\n"
        "    Ví dụ: <code>/gen_key vipkey001 31/12/2025/23:59</code>\n"
        "    Ví dụ Admin: <code>/gen_key admin_new 31/12/2026/23:59 admin</code>\n"
        "  /stats - Xem thống kê người dùng và key."
    )

async def generate_key_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not is_admin(chat_id):
        await update.message.reply_text("Bạn không có quyền admin để sử dụng lệnh này.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Sử dụng: `/gen_key <tên_key> <ngày/tháng/năm/giờ> [admin]`")
        return

    key_name = context.args[0]
    expiry_str = context.args[1]
    is_admin_key = False
    if len(context.args) > 2 and context.args[2].lower() == 'admin':
        is_admin_key = True

    try:
        expiration_dt = datetime.strptime(expiry_str, "%d/%m/%Y/%H:%M")
    except ValueError:
        await update.message.reply_text("❌ Định dạng ngày/giờ không hợp lệ. Vui lòng dùng `DD/MM/YYYY/HH:MM`.")
        return

    if add_key(key_name, expiration_dt, is_admin_key):
        await update.message.reply_html(
            f"✅ Key <b>{key_name}</b> đã được tạo thành công.\n"
            f"Hết hạn: {expiry_str}\n"
            f"Quyền Admin: {'Có' if is_admin_key else 'Không'}"
        )
    else:
        await update.message.reply_text(f"❌ Không thể tạo key '{key_name}'. Có thể key đã tồn tại.")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not is_admin(chat_id):
        await update.message.reply_text("Bạn không có quyền admin để sử dụng lệnh này.")
        return

    active_users = get_active_user_count()
    total_admins = get_admin_count()
    all_keys = get_all_keys()

    key_list_str = "<b>Danh sách Keys:</b>\n"
    if all_keys:
        for k in all_keys:
            status = "Đã dùng" if k['is_used'] == 1 else "Chưa dùng"
            admin_status = "Admin" if k['is_admin'] == 1 else "User"
            expiry_date = k['expiration_date']
            key_list_str += f"  - <code>{k['name']}</code> | Hết hạn: {expiry_date} | {status} | Loại: {admin_status}\n"
    else:
        key_list_str += "  Không có key nào trong hệ thống."

    message = (
        f"<b>THỐNG KÊ BOT:</b>\n"
        f"👥 Người dùng đang hoạt động: <b>{active_users}</b>\n"
        f"👑 Admin hiện có: <b>{total_admins}</b>\n"
        f"----------------------------------------\n"
        f"{key_list_str}"
    )
    await update.message.reply_html(message)


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Xin lỗi, tôi không hiểu lệnh đó. Vui lòng sử dụng /start hoặc /du_doan.")

# ==== CẤU HÌNH VÀ CHẠY BOT VỚI WEBHOOKS ====

async def setup_bot():
    global application

    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_BOT_TOKEN:
        logger.error("LỖI: Biến môi trường TELEGRAM_BOT_TOKEN chưa được đặt. Bot sẽ không chạy.")
        raise ValueError("TELEGRAM_BOT_TOKEN is not set.")

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    # Initialize database
    init_db()

    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("register_key", register_key_command))
    application.add_handler(CommandHandler("du_doan", du_doan_command))
    application.add_handler(CommandHandler("admin_help", admin_help_command)) # New admin command
    application.add_handler(CommandHandler("gen_key", generate_key_command))   # New admin command
    application.add_handler(CommandHandler("stats", stats_command))           # New admin command
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    load_pattern_counter()
    load_lich_su()
    load_all_patterns()

    logger.info(f"{BOLD}{GREEN}======================================================================")
    logger.info(f"       TOOL TX - Quangdz /Trung Ngu (Phiên bản Telegram Webhook)        ")
    logger.info(f"======================================================================{RESET}")
    logger.info(f"{GREEN}Bot Telegram đã sẵn sàng.{RESET}")


@flask_app.route('/', methods=['POST'])
async def webhook():
    if request.method == "POST":
        try:
            update = Update.de_json(request.get_json(force=True), application.bot)
            await application.process_update(update)
            return "ok"
        except Exception as e:
            logger.error(f"Error processing webhook update: {e}")
            abort(500)
    return "ok"

@flask_app.route('/')
def hello():
    return "TX Pro AI Bot is running!"

async def set_webhook_on_startup():
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")
    if not WEBHOOK_URL:
        logger.error("LỖI: Biến môi trường WEBHOOK_URL chưa được đặt. Webhook sẽ không được thiết lập.")
        return

    try:
        await application.bot.set_webhook(url=WEBHOOK_URL)
        logger.info(f"Webhook đã được thiết lập thành công tới: {WEBHOOK_URL}")
    except Exception as e:
        logger.error(f"Lỗi khi thiết lập webhook: {e}")

if __name__ == "__main__":
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(setup_bot())
        loop.run_until_complete(set_webhook_on_startup())
    except Exception as e:
        logger.critical(f"Fatal error during bot setup: {e}")
        exit(1)

    app = flask_app
    logger.info("Flask app đã được cấu hình và sẵn sàng bởi Gunicorn.")

