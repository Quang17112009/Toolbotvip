import os
import json
import time
from datetime import datetime
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
# LICHSU_FILE, DUDOAN_FILE, AI_FILE, PATTERN_COUNT_FILE - Cảnh báo: Các file này không bền vững trên Render!
# Để dữ liệu bền vững, bạn cần tích hợp Database.
LICHSU_FILE = "lichsucau.txt" # Sẽ chỉ dùng trong bộ nhớ trong ví dụ này
DUDOAN_FILE = "dudoan.txt" # File chứa các pattern dự đoán VIP (AI 1)
AI_FILE = "ai_1-2.txt"    # File chứa các pattern AI tự học (AI 2)
PATTERN_COUNT_FILE = "pattern_counter.json" # Sẽ chỉ dùng trong bộ nhớ trong ví dụ này
CHECK_INTERVAL_SECONDS = 5 # Thời gian chờ giữa các lần kiểm tra phiên mới (chỉ áp dụng nếu polling, không dùng với webhook)
MIN_PATTERN_LENGTH = 4    # Độ dài tối thiểu của pattern để được xem xét
MAX_PATTERN_LENGTH = 15   # Độ dài tối đa của pattern để được xem xét
AI_LEARN_THRESHOLD_COUNT = 5 # Số lần xuất hiện tối thiểu của pattern để AI 2 xem xét học
AI_LEARN_THRESHOLD_RATE = 75 # Tỷ lệ chính xác tối thiểu (%) để AI 2 học pattern

# --- MÀU SẮC CHO CONSOLE (Không còn dùng trực tiếp cho Telegram, nhưng giữ lại cho debug console) ---
RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, RESET, BOLD = "\033[91m", "\033[92m", "\033[93m", "\033[94m", "\033[95m", "\033[96m", "\033[0m", "\033[1m"

# ==== BIẾN TOÀN CỤC ====
lich_su = [] # Lưu trữ lịch sử cầu (T/X) các phiên gần nhất
pattern_counter = defaultdict(lambda: {"T": 0, "X": 0}) # Đếm số lần mỗi pattern kết thúc bằng T/X
last_processed_phien = None # Lưu phiên cuối cùng đã xử lý
cau_dudoan = {} # Lưu các pattern từ DUDOAN_FILE (AI 1)
cau_ai = {}     # Lưu các pattern từ AI_FILE (AI 2)
win_rate_tracker = defaultdict(list) # Lưu trữ kết quả (True/False cho thắng/thua) của mỗi dự đoán theo nguồn AI

# Biến toàn cục để lưu trữ bot application và Flask app
application: Application = None
flask_app = Flask(__name__)

# Biến toàn cục mới cho logic MD5
md5_giai_doan_counter = 0 # Đếm số lần phân tích MD5 cho kết quả 'Gãy' liên tiếp
md5_analysis_result = "Khác" # Kết quả phân tích MD5 hiện tại, mặc định là 'Khác'

# Để lưu trữ chat_id của người dùng đã kích hoạt bot.
# Cần cơ chế bền vững hơn nếu bot phải gửi tin tự động cho nhiều người dùng.
# Trong ví dụ webhook, bot phản hồi trực tiếp các lệnh.
# Để gửi tin nhắn chủ động, bạn cần lưu trữ chat_id vào DB.
# Trong ví dụ này, chúng ta sẽ gửi tin nhắn phản hồi lệnh /du_doan
# và không dùng vòng lặp tự động gửi tin nhắn mỗi CHECK_INTERVAL_SECONDS.
# Nếu bạn muốn vòng lặp tự động, cần cơ chế Job Queue và lưu chat_id bền vững.
# Hoặc, với webhook, cách thông thường là người dùng chủ động yêu cầu thông tin.
tracked_chat_id = None


# ==== CÁC HÀM TIỆN ÍCH CƠ BẢN ====

def tai_xiu(tong):
    """Xác định kết quả là Tài (T) hay Xỉu (X) dựa trên tổng điểm xúc xắc."""
    return "T" if tong >= 11 else "X"

def load_lich_su():
    """Tải lịch sử cầu từ file LICHSU_FILE. (Không bền vững trên Render nếu không dùng DB)"""
    global lich_su
    # Trên Render, file hệ thống không bền vững. Giữ trong bộ nhớ.
    logger.info("Ignoring loading lichsucau.txt for persistent storage on Render.")
    lich_su = [] # Bắt đầu trống để tránh lỗi nếu không có DB

def cap_nhat_lich_su(kq):
    """Cập nhật lịch sử cầu mới nhất vào bộ nhớ và file. (Không bền vững trên Render nếu không dùng DB)"""
    global lich_su
    lich_su.append(kq)
    lich_su = lich_su[-MAX_PATTERN_LENGTH:]
    logger.info(f"Updated lich_su in memory: {''.join(lich_su)}")
    # logger.info("Ignoring saving lichsucau.txt for persistent storage on Render.")

def load_patterns_from_file(filepath):
    """Tải các pattern dự đoán từ một file cụ thể (dudoan.txt hoặc ai_1-2.txt)."""
    patterns = {}
    # Sử dụng os.path.join để đảm bảo đường dẫn đúng trên mọi OS
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
    """Tải tất cả các pattern từ file dự đoán (VIP - AI 1) và file AI tự học (AI 2)."""
    global cau_dudoan, cau_ai
    cau_dudoan = load_patterns_from_file(DUDOAN_FILE)
    cau_ai = load_patterns_from_file(AI_FILE)
    logger.info(f"Loaded {len(cau_dudoan)} patterns from {DUDOAN_FILE}")
    logger.info(f"Loaded {len(cau_ai)} patterns from {AI_FILE}")


def load_pattern_counter():
    """Tải bộ đếm tần suất xuất hiện của các pattern từ file JSON. (Không bền vững trên Render nếu không dùng DB)"""
    global pattern_counter
    logger.info("Ignoring loading pattern_counter.json for persistent storage on Render.")
    pattern_counter = defaultdict(lambda: {"T": 0, "X": 0}) # Bắt đầu lại bộ đếm


def save_pattern_counter():
    """Lưu bộ đếm tần suất xuất hiện của các pattern vào file JSON. (Không bền vững trên Render nếu không dùng DB)"""
    logger.info("Ignoring saving pattern_counter.json for persistent storage on Render.")


def get_data_from_api():
    """Lấy dữ liệu phiên Tài Xỉu mới nhất từ API."""
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
    """
    Lấy dự đoán từ một nguồn cụ thể (VIP - AI 1 hoặc AI Tự Học - AI 2).
    Ưu tiên pattern dài nhất khớp được trong lịch sử cầu hiện tại.
    """
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
    """
    AI 3: Lấy dự đoán thuần túy từ xác suất thống kê trong pattern_counter.json.
    Dựa trên pattern dài nhất có xác suất thắng cao nhất.
    """
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
    """
    Logic "CHỐT KÈO": Tổng hợp các dự đoán từ các AI để đưa ra khuyến nghị cuối cùng.
    Ưu tiên đồng thuận, sau đó là số đông, cuối cùng là AI có độ chính xác (accuracy) cao nhất.
    """
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

    # Sắp xếp để ưu tiên 'T' nếu số phiếu bằng nhau
    sorted_votes = sorted(votes.items(), key=lambda item: (item[1], item[0]), reverse=True)
    if len(sorted_votes) > 1 and sorted_votes[0][1] == sorted_votes[1][1]:
        # Trường hợp hòa phiếu, ưu tiên AI có độ chính xác cao nhất
        best_pred = max(valid_preds, key=lambda p: p['accuracy'])
        return {
            "ket_qua": best_pred['prediction'],
            "ly_do": f"Hòa phiếu, ưu tiên {best_pred['source']} với độ chính xác cao nhất ({best_pred['accuracy']:.1f}%).",
            "confidence": "Trung Bình"
        }
    else:
        # Trường hợp có số đông rõ ràng
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
    """
    AI học từ kết quả thực tế để cập nhật bộ đếm pattern và tự động thêm pattern mới vào AI_FILE (AI 2).
    Hàm này được điều chỉnh để xem xét kết quả phân tích MD5 trước khi học.
    (Các file pattern và counter không bền vững trên Render nếu không dùng DB)
    """
    global md5_analysis_result

    # Simulate MD5 analysis result
    current_md5_result = simulate_md5_analysis()
    logger.info(f"Kết quả phân tích MD5 mô phỏng: {current_md5_result}") # For debugging/logging

    if current_md5_result == "Gãy":
        logger.warning("MD5 phân tích 'Gãy', AI sẽ KHÔNG học từ phiên này để tránh sai lệch.")
        return # AI does not learn if MD5 analysis is 'Gãy'

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
                    # Ghi vào file AI_FILE (không bền vững trên Render)
                    absolute_ai_filepath = os.path.join(os.path.dirname(__file__), AI_FILE)
                    try:
                        with open(absolute_ai_filepath, "a", encoding="utf-8") as f:
                            f.write(f"\n{potential_pat} => Dự đoán: {prediction_to_learn} - Loại cầu: AI Tự Học")
                        load_all_patterns() # Tải lại pattern sau khi thêm
                        logger.info(f"AI 2 đã học pattern mới: {potential_pat} => {prediction_to_learn} (Lưu ý: Không bền vững)")
                    except IOError as e:
                        logger.error(f"Lỗi khi ghi cầu mới của AI: {e}")
    save_pattern_counter() # Ghi counter (không bền vững trên Render)


# ==== HÀM GỬI TIN NHẮN TELEGRAM ====
async def send_telegram_message(context: ContextTypes.DEFAULT_TYPE, message_text: str, chat_id_to_send: int):
    """Gửi tin nhắn văn bản đến Telegram."""
    if chat_id_to_send:
        try:
            await context.bot.send_message(chat_id=chat_id_to_send, text=message_text, parse_mode='HTML')
            logger.info(f"Đã gửi tin nhắn Telegram tới {chat_id_to_send}")
        except Exception as e:
            logger.error(f"Lỗi khi gửi tin nhắn Telegram tới {chat_id_to_send}: {e}")
    else:
        logger.warning("Chưa có Chat ID để gửi tin nhắn Telegram.")

async def hien_thi_telegram(context: ContextTypes.DEFAULT_TYPE, chat_id_to_send: int, phien, xx, tong, kq_thucte, predictions, final_choice, win_tracker):
    """
    Tạo và gửi tin nhắn dự đoán Tài Xỉu tới Telegram.
    """
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
    # Add MD5 analysis result to the Telegram message
    global md5_analysis_result
    md5_status_color = "red" if md5_analysis_result == "Gãy" else "green"
    message_parts.append(f"<b>Trạng thái MD5: <span style='color:{md5_status_color};'>{md5_analysis_result.upper()}</span></b>")
    message_parts.append("--------------------------------------------------------------------")
    message_parts.append("Powered by <b>TX Pro AI</b> 🤖")

    await send_telegram_message(context, "\n".join(message_parts), chat_id_to_send)

# ==== LOGIC XỬ LÝ PHIÊN ====
async def process_taixiu_prediction(context: ContextTypes.DEFAULT_TYPE, chat_id_to_send: int):
    """
    Thực hiện logic lấy dữ liệu, dự đoán và gửi tin nhắn.
    Được gọi khi người dùng yêu cầu hoặc trong một vòng lặp job (nếu muốn tự động).
    """
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

    # Chỉ xử lý nếu có phiên mới
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

        # Cập nhật win_rate_tracker cho tất cả các dự đoán (nếu có)
        for pred_obj in all_predictions:
            if pred_obj:
                source_key = pred_obj['source']
                predicted_outcome = pred_obj['prediction']
                win_rate_tracker[source_key].append(predicted_outcome == kq_thucte)

        cap_nhat_lich_su(kq_thucte) # Cập nhật lịch sử trong bộ nhớ

        await hien_thi_telegram(context, chat_id_to_send, phien_api, [xx1, xx2, xx3], tong, kq_thucte, all_predictions, final_choice, win_rate_tracker)

        ai_hoc_hoi(history_before, kq_thucte) # AI học
        last_processed_phien = phien_api
        logger.info(f"Phiên {phien_api} đã được xử lý thành công.")
    else:
        logger.info(f"Phiên {phien_api} đã được xử lý trước đó hoặc không có phiên mới.")
        await send_telegram_message(context, "ℹ️ Hiện tại không có phiên mới để phân tích. Vui lòng thử lại sau.", chat_id_to_send)


# ==== XỬ LÝ LỆNH TELEGRAM ====
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gửi tin nhắn chào mừng khi người dùng gửi lệnh /start."""
    global tracked_chat_id
    tracked_chat_id = update.effective_chat.id # Lưu lại chat_id
    logger.info(f"Nhận lệnh /start từ Chat ID: {tracked_chat_id}")
    await update.message.reply_html(
        "Chào mừng bạn đến với <b>TX Pro AI</b>! 🤖\n"
        "Gửi lệnh /du_doan để nhận dự đoán phiên Tài Xỉu mới nhất."
    )

async def du_doan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gửi dự đoán ngay lập tức khi người dùng gửi lệnh /du_doan."""
    global tracked_chat_id
    tracked_chat_id = update.effective_chat.id # Cập nhật chat_id nếu cần
    logger.info(f"Nhận lệnh /du_doan từ Chat ID: {tracked_chat_id}")
    await update.message.reply_text("Đang lấy dữ liệu và phân tích dự đoán...")
    await process_taixiu_prediction(context, tracked_chat_id)

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Phản hồi các lệnh không xác định."""
    await update.message.reply_text("Xin lỗi, tôi không hiểu lệnh đó. Vui lòng sử dụng /start hoặc /du_doan.")

# ==== CẤU HÌNH VÀ CHẠY BOT VỚI WEBHOOKS ====

async def setup_bot():
    """Hàm khởi tạo và cấu hình bot."""
    global application

    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_BOT_TOKEN:
        logger.error("LỖI: Biến môi trường TELEGRAM_BOT_TOKEN chưa được đặt. Bot sẽ không chạy.")
        raise ValueError("TELEGRAM_BOT_TOKEN is not set.")

    # Khởi tạo application với webhook
    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    # Thêm các trình xử lý lệnh
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("du_doan", du_doan_command))
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command)) # Xử lý các lệnh không xác định

    # Tải dữ liệu cần thiết khi khởi động tool (chỉ đọc từ file cố định)
    load_pattern_counter() # Sẽ reset trong bộ nhớ
    load_lich_su()         # Sẽ reset trong bộ nhớ
    load_all_patterns()    # Đọc từ các file dudoan.txt, ai_1-2.txt

    logger.info(f"{BOLD}{GREEN}======================================================================")
    logger.info(f"       TOOL TX - Quangdz /Trung Ngu (Phiên bản Telegram Webhook)        ")
    logger.info(f"======================================================================{RESET}")
    logger.info(f"{GREEN}Bot Telegram đã sẵn sàng.{RESET}")

    # Không cần application.run_polling() ở đây. Flask sẽ lắng nghe.
    # application.run_webhook() sẽ được gọi bên trong route Flask.


# Flask endpoint để nhận updates từ Telegram
@flask_app.route('/', methods=['POST'])
async def webhook():
    if request.method == "POST":
        try:
            # Lấy update từ request body
            update = Update.de_json(request.get_json(force=True), application.bot)
            # Xử lý update bất đồng bộ
            await application.process_update(update)
            return "ok"
        except Exception as e:
            logger.error(f"Error processing webhook update: {e}")
            abort(500)
    return "ok"

@flask_app.route('/')
def hello():
    # Trang chủ đơn giản để kiểm tra xem server có chạy không
    return "TX Pro AI Bot is running!"

async def set_webhook_on_startup():
    """Đặt webhook cho bot."""
    WEBHOOK_URL = os.getenv("WEBHOOK_URL") # URL của ứng dụng Render của bạn
    if not WEBHOOK_URL:
        logger.error("LỖI: Biến môi trường WEBHOOK_URL chưa được đặt. Webhook sẽ không được thiết lập.")
        return

    try:
        await application.bot.set_webhook(url=WEBHOOK_URL)
        logger.info(f"Webhook đã được thiết lập thành công tới: {WEBHOOK_URL}")
    except Exception as e:
        logger.error(f"Lỗi khi thiết lập webhook: {e}")


# Hàm để chạy setup bot và server Flask
async def main():
    await setup_bot()
    # Sau khi application được setup, set webhook.
    # Đây là cách tốt để đảm bảo webhook được set khi bot khởi động
    # nhưng không bị chạy lại liên tục.
    # Với Render, bạn có thể chạy nó một lần thông qua một "start command"
    # hoặc xử lý trong một hàm khởi tạo.
    # Tuy nhiên, cách chuẩn là bot tự set webhook khi khởi động nếu cần.
    # Để đảm bảo nó chỉ chạy một lần, bạn có thể đưa vào một điều kiện.
    # Với gunicorn, bạn có thể dùng một pre-hook hoặc đơn giản là để nó chạy khi server khởi động.
    await set_webhook_on_startup()


# Hàm để chạy bot và server Flask.
# Flask app được chạy bằng gunicorn, không phải trực tiếp từ asyncio.run()
if __name__ == "__main__":
    # Để chạy cục bộ mà không cần gunicorn
    # asyncio.run(main()) # Chạy setup bot
    # flask_app.run(port=5000) # Chạy Flask (sẽ block)

    # Khi deploy trên Render với gunicorn, gunicorn sẽ gọi `main:app`
    # Do đó, hàm `main` (setup bot) cần được gọi trước khi `app` của Flask được sử dụng.
    # Vì `main` là async, chúng ta cần một vòng lặp sự kiện để chạy nó.
    # Nhưng gunicorn không trực tiếp chạy async code.
    # Cách tốt nhất là đảm bảo setup_bot được gọi khi Flask app khởi động.

    # Khởi tạo application trong ngữ cảnh của Flask
    # Điều này đảm bảo Flask có thể truy cập 'application' object.
    # Cần một cách để chạy 'setup_bot' bất đồng bộ trước khi Flask app bắt đầu xử lý request.
    # Sử dụng @flask_app.before_first_request hoặc một hook của gunicorn.

    # Cho mục đích đơn giản, ta sẽ gọi setup_bot (blocking) ở đây,
    # sau đó gunicorn sẽ chạy flask_app. Điều này không lý tưởng cho async.
    # Cách tốt hơn là sử dụng một thư viện như `hypercorn` thay `gunicorn` nếu muốn async end-to-end.
    # Hoặc, với Flask, các hàm handler có thể là async.

    # Để đảm bảo `application` được khởi tạo trước khi Flask server bắt đầu:
    try:
        # Chạy setup_bot bằng một vòng lặp sự kiện riêng biệt
        loop = asyncio.get_event_loop()
        loop.run_until_complete(setup_bot())
        loop.run_until_complete(set_webhook_on_startup())
        # The loop will be closed by gunicorn if it manages it, or needs explicit closure.
    except Exception as e:
        logger.critical(f"Fatal error during bot setup: {e}")
        exit(1)

    # Flask app (được gunicorn gọi)
    app = flask_app # Đặt tên biến là `app` để `gunicorn main:app` có thể tìm thấy.

    logger.info("Flask app đã được cấu hình và sẵn sàng bởi Gunicorn.")

