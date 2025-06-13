import os
import json
import time
from datetime import datetime
from collections import defaultdict, Counter
import requests
import asyncio
import telebot
from flask import Flask, request, abort # Import Flask

# ==== CẤU HÌNH ====
HTTP_API_URL = "https://apisunwin.up.railway.app/api/taixiu"
LICHSU_FILE = "lichsucau.txt"
DUDOAN_FILE = "dudoan.txt" # File chứa các pattern dự đoán VIP (AI 1)
AI_FILE = "ai_1-2.txt"    # File chứa các pattern AI tự học (AI 2)
PATTERN_COUNT_FILE = "pattern_counter.json" # File lưu trữ tần suất của các pattern (cho AI 3 và AI 2 học)
CHECK_INTERVAL_SECONDS = 5 # Thời gian chờ giữa các lần kiểm tra phiên mới
MIN_PATTERN_LENGTH = 4    # Độ dài tối thiểu của pattern để được xem xét
MAX_PATTERN_LENGTH = 15   # Độ dài tối đa của pattern để được xem xét
AI_LEARN_THRESHOLD_COUNT = 5 # Số lần xuất hiện tối thiểu của pattern để AI 2 xem xét học
AI_LEARN_THRESHOLD_RATE = 75 # Tỷ lệ chính xác tối thiểu (%) để AI 2 học pattern

# --- MÀU SẮC CHO CONSOLE ---
RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, RESET, BOLD = "\033[91m", "\033[92m", "\033[93m", "\033[94m", "\033[95m", "\033[96m", "\033[0m", "\033[1m"

# ==== BIẾN TOÀN CỤC ====
lich_su = []
pattern_counter = defaultdict(lambda: {"T": 0, "X": 0})
last_processed_phien = None
cau_dudoan = {}
cau_ai = {}
win_rate_tracker = defaultdict(list)

bot = None
active_chat_ids = set()

md5_giai_doan_counter = 0
md5_analysis_result = "Khác"

# ==== CÁC HÀM TIỆN ÍCH CƠ BẢN ====

def tai_xiu(tong):
    return "T" if tong >= 11 else "X"

def load_lich_su():
    global lich_su
    try:
        if os.path.exists(LICHSU_FILE):
            with open(LICHSU_FILE, "r", encoding="utf-8") as f:
                lich_su = [line.strip() for line in f if line.strip() in ['T', 'X']]
            lich_su = lich_su[-MAX_PATTERN_LENGTH:]
    except IOError as e:
        print(f"Lỗi khi đọc file lịch sử: {e}")
        lich_su = []

def cap_nhat_lich_su(kq):
    global lich_su
    lich_su.append(kq)
    lich_su = lich_su[-MAX_PATTERN_LENGTH:]
    try:
        with open(LICHSU_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lich_su))
    except IOError as e:
        print(f"Lỗi khi ghi lịch sử vào file: {e}")

def load_patterns_from_file(filepath):
    patterns = {}
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or "=>" not in line: continue
                    try:
                        parts = line.split("=>")
                        pattern = parts[0].strip()
                        prediction = parts[1].split("Dự đoán:")[1].strip()[0]
                        if prediction in ["T", "X"]:
                            patterns[pattern] = prediction
                    except (IndexError, KeyError):
                        continue
        except IOError as e:
            print(f"Lỗi khi đọc file cầu '{filepath}': {e}")
    return patterns

def load_all_patterns():
    global cau_dudoan, cau_ai
    cau_dudoan = load_patterns_from_file(DUDOAN_FILE)
    cau_ai = load_patterns_from_file(AI_FILE)

def load_pattern_counter():
    global pattern_counter
    if os.path.exists(PATTERN_COUNT_FILE):
        try:
            with open(PATTERN_COUNT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                pattern_counter = defaultdict(lambda: {"T": 0, "X": 0}, data)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Cảnh báo: Không thể tải '{PATTERN_COUNT_FILE}'. Bắt đầu lại bộ đếm.")
            pattern_counter = defaultdict(lambda: {"T": 0, "X": 0})

def save_pattern_counter():
    try:
        with open(PATTERN_COUNT_FILE, "w", encoding="utf-8") as f:
            json.dump(pattern_counter, f, ensure_ascii=False, indent=2)
    except IOError as e:
        print(f"Lỗi khi ghi bộ đếm pattern: {e}")

def get_data_from_api():
    try:
        response = requests.get(HTTP_API_URL, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Lỗi khi gọi API: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"Lỗi giải mã JSON từ API: {e}")
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

    if votes['T'] > votes['X']:
        return {"ket_qua": "T", "ly_do": f"Số đông nghiêng về Tài ({votes['T']}/{len(valid_preds)}).", "confidence": "Cao"}
    if votes['X'] > votes['T']:
        return {"ket_qua": "X", "ly_do": f"Số đông nghiêng về Xỉu ({votes['X']}/{len(valid_preds)}).", "confidence": "Cao"}

    best_pred = max(valid_preds, key=lambda p: p['accuracy'])
    return {
        "ket_qua": best_pred['prediction'],
        "ly_do": f"Xung đột, ưu tiên {best_pred['source']} với độ chính xác cao nhất ({best_pred['accuracy']:.1f}%).",
        "confidence": "Trung Bình"
    }

def simulate_md5_analysis():
    global md5_giai_doan_counter, md5_analysis_result

    if md5_giai_doan_counter < 2:
        md5_giai_doan_counter += 1
        md5_analysis_result = "Gãy"
    else:
        md5_giai_doan_counter = 0
        md5_analysis_result = "Khác"
    return md5_analysis_result


def ai_hoc_hoi(history_before_result, actual_result):
    global md5_analysis_result

    current_md5_result = simulate_md5_analysis()
    print(f"Kết quả phân tích MD5 mô phỏng: {current_md5_result}")

    if current_md5_result == "Gãy":
        print("MD5 phân tích 'Gãy', AI sẽ KHÔNG học từ phiên này để tránh sai lệch.")
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
                    try:
                        with open(AI_FILE, "a", encoding="utf-8") as f:
                            f.write(f"\n{potential_pat} => Dự đoán: {prediction_to_learn} - Loại cầu: AI Tự Học")
                        load_all_patterns()
                        print(f"AI 2 đã học pattern mới: {potential_pat} => {prediction_to_learn}")
                    except IOError as e:
                        print(f"Lỗi khi ghi cầu mới của AI: {e}")
    save_pattern_counter()

# ==== HÀM GỬI TIN NHẮN TELEGRAM (sử dụng telebot) ====
async def send_telegram_message(target_chat_id: int, message_text: str):
    if bot:
        try:
            await asyncio.to_thread(bot.send_message, chat_id=target_chat_id, text=message_text, parse_mode='HTML')
            print(f"Đã gửi tin nhắn Telegram tới {target_chat_id}: {message_text.replace('<br>', ' ')}")
        except Exception as e:
            print(f"Lỗi khi gửi tin nhắn Telegram tới {target_chat_id}: {e}")
    else:
        print("Bot chưa được khởi tạo để gửi tin nhắn Telegram.")

async def hien_thi_telegram(target_chat_id: int, phien, xx, tong, kq_thucte, predictions, final_choice, win_tracker):
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

    await send_telegram_message(target_chat_id, "\n".join(message_parts))


# ==== VÒNG LẶP CHÍNH CỦA BOT (ASYNCHRONOUS) ====
async def main_bot_loop():
    global last_processed_phien

    if not active_chat_ids:
        print("Chưa có Chat ID hoạt động, chờ người dùng /start để bắt đầu.")
        return

    data = get_data_from_api()
    if not data or not isinstance(data, dict):
        print("Không lấy được dữ liệu API hoặc dữ liệu không hợp lệ.")
        return

    phien_api = data.get("Phien")
    xx1, xx2, xx3 = data.get("Xuc_xac_1"), data.get("Xuc_xac_2"), data.get("Xuc_xac_3")

    if phien_api is None or not all(isinstance(x, int) for x in [xx1, xx2, xx3]):
        print("Dữ liệu phiên hoặc xúc xắc từ API không hợp lệ.")
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

        for c_id in list(active_chat_ids):
            await hien_thi_telegram(c_id, phien_api, [xx1, xx2, xx3], tong, kq_thucte, all_predictions, final_choice, win_rate_tracker)

        os.system('cls' if os.name == 'nt' else 'clear')
        print(f"Phiên {phien_api} đã được xử lý và gửi Telegram.")
        print(f"Lịch sử cầu hiện tại: {''.join(lich_su)}")
        print(f"Các Chat ID hoạt động: {active_chat_ids}")

        ai_hoc_hoi(history_before, kq_thucte)
        last_processed_phien = phien_api

# ==== XỬ LÝ LỆNH TELEGRAM (sử dụng telebot) ====

def start_command(message):
    global active_chat_ids, bot
    active_chat_ids.add(message.chat.id)
    bot.reply_to(message, "Chào mừng bạn đến với <b>TX Pro AI</b>! 🤖\n"
                            "Tôi sẽ dự đoán Tài Xỉu cho bạn. Vui lòng đợi tôi theo dõi các phiên mới nhất.",
                            parse_mode='HTML')
    print(f"Đã nhận lệnh /start từ Chat ID: {message.chat.id}. Active chat IDs: {active_chat_ids}")

def du_doan_command(message):
    global active_chat_ids, bot
    active_chat_ids.add(message.chat.id)
    bot.reply_to(message, "Đang lấy dữ liệu và phân tích dự đoán...")
    print(f"Đã nhận lệnh /du_doan từ Chat ID: {message.chat.id}")
    asyncio.create_task(process_single_prediction_for_chat_id(message.chat.id))

async def process_single_prediction_for_chat_id(target_chat_id: int):
    data = get_data_from_api()
    if not data or not isinstance(data, dict):
        await send_telegram_message(target_chat_id, "Không lấy được dữ liệu API hoặc dữ liệu không hợp lệ để dự đoán.")
        return

    phien_api = data.get("Phien")
    xx1, xx2, xx3 = data.get("Xuc_xac_1"), data.get("Xuc_xac_2"), data.get("Xuc_xac_3")

    if phien_api is None or not all(isinstance(x, int) for x in [xx1, xx2, xx3]):
        await send_telegram_message(target_chat_id, "Dữ liệu phiên hoặc xúc xắc từ API không hợp lệ để dự đoán.")
        return

    history_before = list(lich_su)
    history_str = "".join(history_before)

    pred_vip = get_prediction_from_source(history_str, cau_dudoan, "VIP")
    pred_ai_file = get_prediction_from_source(history_str, cau_ai, "AI Tự Học")
    pred_stat = get_statistical_prediction(history_str)
    all_predictions = [pred_vip, pred_ai_file, pred_stat]

    final_choice = chot_keo_cuoi_cung(all_predictions)

    tong = xx1 + xx2 + xx3
    kq_thucte = tai_xiu(tong)

    await hien_thi_telegram(target_chat_id, phien_api, [xx1, xx2, xx3], tong, kq_thucte, all_predictions, final_choice, win_rate_tracker)

# ==== FLASK SERVER ĐỂ GIỮ DỊCH VỤ LUÔN CHẠY TRÊN RENDER (NẾU DÙNG WEB SERVICE) ====
app = Flask(__name__)

@app.route('/')
def hello_world():
    # Render sẽ gửi request HTTP đến '/' để kiểm tra dịch vụ có hoạt động không
    # Chỉ cần trả về một chuỗi đơn giản để Render biết rằng ứng dụng đang "sống"
    return 'Bot is running and Flask server is active!'

def run_flask_app():
    # Lấy port từ biến môi trường của Render (mặc định là 10000 nếu không tìm thấy)
    port = int(os.environ.get("PORT", 10000))
    print(f"{YELLOW}Bắt đầu Flask server trên cổng {port} để giữ dịch vụ luôn chạy...{RESET}")
    # app.run là blocking, cần chạy trong một thread riêng hoặc asyncio.to_thread
    app.run(host='0.0.0.0', port=port, debug=False)

# ==== CHẠY BOT TELEGRAM ====
async def main_bot():
    global bot

    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_BOT_TOKEN:
        print(f"{RED}LỖI: Biến môi trường TELEGRAM_BOT_TOKEN chưa được đặt. Bot sẽ không chạy.{RESET}")
        print(f"{YELLOW}Vui lòng đặt biến môi trường TELEGRAM_BOT_TOKEN.{RESET}")
        return

    bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

    bot.register_message_handler(start_command, commands=['start'])
    bot.register_message_handler(du_doan_command, commands=['du_doan'])

    load_pattern_counter()
    load_lich_su()
    load_all_patterns()

    print(f"{BOLD}{GREEN}======================================================================")
    print(f"       TOOL TX - Quangdz /Trung Ngu (Phiên bản Telegram - Telebot)               ")
    print(f"======================================================================{RESET}")
    print(f"{GREEN}Bot Telegram đã sẵn sàng. Đang chờ lệnh /start...{RESET}")
    print(f"Kiểm tra token: {TELEGRAM_BOT_TOKEN[:5]}...{TELEGRAM_BOT_TOKEN[-5:]}")

    # Khởi chạy Flask server trong một thread riêng để nó không block asyncio event loop
    import threading
    flask_thread = threading.Thread(target=run_flask_app)
    flask_thread.daemon = True # Đặt thread là daemon để nó tự tắt khi chương trình chính kết thúc
    flask_thread.start()

    # Khởi chạy vòng lặp chính để kiểm tra phiên mới một cách định kỳ
    asyncio.create_task(run_main_loop_periodically())

    print(f"{YELLOW}Bắt đầu polling Telegram...{RESET}")
    await asyncio.to_thread(bot.polling, none_stop=True, interval=0, timeout=20)


async def run_main_loop_periodically():
    while True:
        await main_bot_loop()
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        os.system('cls' if os.name == 'nt' else 'clear')
        asyncio.run(main_bot())
    except KeyboardInterrupt:
        print(f"\n{RED}{BOLD}[STOP] Đã dừng bot Telegram.{RESET}")
    except Exception as e:
        print(f"\n{RED}{BOLD}[FATAL ERROR] Bot Telegram đã gặp lỗi: {e}{RESET}")
        import traceback
        traceback.print_exc()
