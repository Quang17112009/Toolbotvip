import os
import json
import time
import asyncio
from datetime import datetime
from collections import defaultdict, Counter
import requests
import telebot
from flask import Flask, request, abort

# ==== CẤU HÌNH ====
# API URL MỚI
HTTP_API_URL = "http://157.10.52.15:3000/api/sunwin?key=Tuantutrum" # <-- Đã thay đổi API URL
# Tên các file dữ liệu
LICHSU_FILE = "lichsucau.txt"
DUDOAN_FILE = "dudoan.txt"          # File cầu VIP ưu tiên (AI 1)
AI_FILE = "ai_1-2.txt"              # File cầu AI tự học (AI 2)
PATTERN_COUNT_FILE = "pattern_counter.json" # File đếm tần suất cho AI 3 và AI 2
DULIEU_AI_FILE = "dulieu_ai.json"   # Tệp nhật ký để ghi lại tất cả các dự đoán và kết quả

# Cài đặt thời gian và pattern
CHECK_INTERVAL_SECONDS = 5          # Thời gian chờ giữa các lần kiểm tra phiên mới
MIN_PATTERN_LENGTH = 4              # Độ dài tối thiểu của pattern
MAX_PATTERN_LENGTH = 15             # Độ dài tối đa của pattern (sử dụng 8 ký tự lịch sử)
# Ngưỡng học cho AI 2
AI_LEARN_THRESHOLD_COUNT = 5
AI_LEARN_THRESHOLD_RATE = 75

# --- MÀU SẮC CHO CONSOLE ---
RED, GREEN, YELLOW, RESET, BOLD = "\033[91m", "\033[92m", "\033[93m", "\033[0m", "\033[1m"

# ==== BIẾN TOÀN CỤC ====
lich_su = []
pattern_counter = defaultdict(lambda: {"T": 0, "X": 0})
last_processed_phien = None
cau_dudoan = {}
cau_ai = {}
win_rate_tracker = defaultdict(list)
pending_predictions = {} # {phien_id: data}

bot = None
active_chat_ids = set()

# BIẾN CHO LOGIC MD5
md5_giai_doan_counter = 0
md5_analysis_result = "Khác"

# ==== CÁC HÀM TIỆN ÍCH & TẢI DỮ LIỆU ====

def tai_xiu(tong):
    return "T" if tong >= 11 else "X"

def load_data():
    """Tải tất cả dữ liệu cần thiết khi khởi động."""
    global lich_su, pattern_counter, cau_dudoan, cau_ai
    try:
        if os.path.exists(LICHSU_FILE):
            with open(LICHSU_FILE, "r", encoding="utf-8") as f:
                lich_su = [line.strip() for line in f if line.strip() in ['T', 'X']]
            # Giới hạn lịch sử theo MAX_PATTERN_LENGTH
            lich_su = lich_su[-MAX_PATTERN_LENGTH:]
    except IOError as e:
        print(f"{RED}Lỗi khi đọc file lịch sử: {e}{RESET}")
        lich_su = []
    
    if os.path.exists(PATTERN_COUNT_FILE):
        try:
            with open(PATTERN_COUNT_FILE, "r", encoding="utf-8") as f:
                pattern_counter = defaultdict(lambda: {"T": 0, "X": 0}, json.load(f))
        except (json.JSONDecodeError, IOError):
            pattern_counter = defaultdict(lambda: {"T": 0, "X": 0})
    
    cau_dudoan = load_patterns_from_file(DUDOAN_FILE)
    cau_ai = load_patterns_from_file(AI_FILE)
    print(f"{GREEN}Đã tải {len(cau_dudoan)} pattern VIP và {len(cau_ai)} pattern AI.{RESET}")

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
            print(f"{RED}Lỗi khi đọc file '{filepath}': {e}{RESET}")
    return patterns

def cap_nhat_lich_su_file():
    """Lưu lịch sử cầu hiện tại vào file."""
    try:
        with open(LICHSU_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lich_su))
    except IOError as e:
        print(f"{RED}Lỗi khi ghi lịch sử vào file: {e}{RESET}")

def save_pattern_counter():
    """Lưu bộ đếm tần suất vào file JSON."""
    try:
        with open(PATTERN_COUNT_FILE, "w", encoding="utf-8") as f:
            json.dump(pattern_counter, f, ensure_ascii=False, indent=2)
    except IOError as e:
        print(f"{RED}Lỗi khi ghi bộ đếm pattern: {e}{RESET}")

def get_data_from_api():
    """Lấy dữ liệu phiên mới nhất từ API mới."""
    try:
        response = requests.get(HTTP_API_URL, timeout=10)
        response.raise_for_status()
        json_data = response.json()
        
        # Phân tích dữ liệu từ API mới
        # Ví dụ định dạng:
        # {
        #   "Cầu": "XTTTXXTT",
        #   "Phiên Trước": "2686392",
        #   "Kết Quả": "Tài",
        #   "Xúc Xắc": "5 5 4",
        #   "Phiên Hiện Tại": "2686393"
        # }
        
        # Lấy thông tin phiên trước để xác định kết quả
        phien_truoc = int(json_data.get("Phiên Trước"))
        ket_qua_truoc = json_data.get("Kết Quả")
        xuc_xac_truoc_str = json_data.get("Xúc Xắc")
        
        # Xử lý chuỗi xúc xắc thành list int
        xx_parts = [int(x) for x in xuc_xac_truoc_str.split(' ') if x.isdigit()]
        
        # Lấy lịch sử cầu và chỉ giữ 8 ký tự cuối
        lich_su_cau_api = json_data.get("Cầu", "")
        # lịch sử API này có thể chứa cả các phiên chưa chốt,
        # chúng ta chỉ quan tâm đến lịch sử đã chốt để AI học
        # và bot tự dự đoán trên đó.
        # Ở đây, mình sẽ chỉ lấy 'T' hoặc 'X' từ 'Kết Quả' và thêm vào lịch sử của bot.
        # Field "Cầu" có thể dùng để đồng bộ lịch sử ban đầu hoặc kiểm tra độ chính xác.
        
        return {
            "phien_truoc": phien_truoc,
            "ket_qua_truoc": "T" if ket_qua_truoc == "Tài" else "X", # Chuyển "Tài"/"Xỉu" sang "T"/"X"
            "xuc_xac_truoc": xx_parts,
            "lich_su_cau_api": lich_su_cau_api # Giữ lại để debug hoặc kiểm tra
        }
    except (requests.exceptions.RequestException, json.JSONDecodeError, KeyError, ValueError) as e:
        print(f"{RED}Lỗi khi gọi API hoặc phân tích dữ liệu từ API mới: {e}{RESET}")
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
        print(f"{YELLOW}MD5 'Gãy', AI bỏ qua việc học phiên này.{RESET}")
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
            if total >= AI_LEARN_THRESHOLD_COUNT:
                rate_T = (counts['T'] / total) * 100
                rate_X = (counts['X'] / total) * 100
                prediction_to_learn = None
                if rate_T >= AI_LEARN_THRESHOLD_RATE: prediction_to_learn = 'T'
                elif rate_X >= AI_LEARN_THRESHOLD_RATE: prediction_to_learn = 'X'

                if prediction_to_learn:
                    try:
                        with open(AI_FILE, "a", encoding="utf-8") as f:
                            f.write(f"\n{potential_pat} => Dự đoán: {prediction_to_learn} - Loại cầu: AI Tự Học")
                        global cau_ai
                        cau_ai = load_patterns_from_file(AI_FILE)
                        print(f"{GREEN}{BOLD}AI 2 đã học pattern mới: {potential_pat} => {prediction_to_learn}{RESET}")
                    except IOError as e:
                        print(f"{RED}Lỗi khi ghi cầu mới của AI: {e}{RESET}")
    save_pattern_counter()

def log_prediction_data(phien_du_doan, history_str, all_preds, final_choice, actual_result=None, is_win=None):
    """Ghi lại toàn bộ dữ liệu của một phiên vào file dulieu_ai.json."""
    log_entry = {
        "phien": phien_du_doan,
        "thoi_gian": datetime.now().isoformat(),
        "lich_su_cau_bot": history_str, # Đổi tên thành lich_su_cau_bot để phân biệt
        "tin_hieu_ai": [{"source": p["source"], "prediction": p["prediction"], "pattern": p["pattern"], "accuracy": p["accuracy"]} for p in all_preds],
        "khuyen_nghi": final_choice,
        "ket_qua_thuc_te": actual_result,
        "thang": is_win
    }
    try:
        logs = []
        if os.path.exists(DULIEU_AI_FILE):
            with open(DULIEU_AI_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
        
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
        print(f"{RED}Lỗi khi ghi file nhật ký {DULIEu_AI_FILE}: {e}{RESET}")


# ==== LOGIC TELEGRAM ====

async def send_telegram_message(message_text):
    """Gửi tin nhắn đến tất cả các chat_id đang hoạt động."""
    for chat_id in list(active_chat_ids):
        try:
            await asyncio.to_thread(bot.send_message, chat_id=chat_id, text=message_text, parse_mode='HTML')
        except Exception as e:
            print(f"{RED}Lỗi khi gửi tin nhắn tới {chat_id}: {e}{RESET}")
            if "bot was blocked by the user" in str(e):
                active_chat_ids.discard(chat_id)

async def send_prediction_notification(phien_du_doan, predictions, final_choice):
    """Gửi thông báo DỰ ĐOÁN cho phiên sắp tới."""
    def format_kq(kq):
        return f"<b><font color='green'>TÀI</font></b>" if kq == 'T' else f"<b><font color='red'>XỈU</font></b>"

    message = [f"<b>🔮 DỰ ĐOÁN CHO PHIÊN #{phien_du_doan} 🔮</b>"]
    message.append(f"<b>Lịch sử cầu hiện tại của Bot:</b> <code>{''.join(lich_su)}</code>") # Đổi tên để rõ ràng
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
        conf_color = "green" if confidence == "Rất Cao" else "orange" if "Cao" in confidence else "red"
        message.append(f"  ▶️ <b>KHUYẾN NGHỊ: {format_kq(final_kq)}</b> (Độ tin cậy: <font color='{conf_color}'>{confidence.upper()}</font>)")
    
    message.append(f"<i>Lý do: {final_choice['ly_do']}</i>")
    await send_telegram_message("\n".join(message))


async def send_result_notification(phien, xx, tong, kq_thucte, prediction_data):
    """Gửi thông báo KẾT QUẢ của phiên vừa rồi và so sánh với dự đoán."""
    final_choice = prediction_data['final_choice']
    is_win = (final_choice['ket_qua'] == kq_thucte) if final_choice['ket_qua'] != "Bỏ qua" else None

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

    await send_telegram_message("\n".join(message))


# ==== VÒNG LẶP CHÍNH CỦA BOT ====
async def main_bot_loop():
    global last_processed_phien, lich_su, md5_giai_doan_counter, md5_analysis_result

    data = get_data_from_api()
    if not data: return

    phien_hien_tai_api = data.get("phien_truoc") # Sử dụng phien_truoc từ API làm phien_hien_tai_api
    kq_thuc_te_api = data.get("ket_qua_truoc")
    xuc_xac_api = data.get("xuc_xac_truoc")
    # lich_su_cau_api = data.get("lich_su_cau_api") # Có thể dùng để đồng bộ hoặc kiểm tra

    if phien_hien_tai_api is None or not xuc_xac_api:
        print(f"{YELLOW}Dữ liệu API chưa đầy đủ cho phiên hiện tại.{RESET}")
        return

    # Đảm bảo chỉ xử lý khi có phiên mới thực sự
    if last_processed_phien is None:
        # Lần chạy đầu tiên, lấy lịch sử từ API để khởi tạo
        # (Chỉ lấy 8 ký tự cuối cùng từ 'Cầu' của API để đồng bộ)
        if data.get("lich_su_cau_api"):
            lich_su = list(data["lich_su_cau_api"][-MAX_PATTERN_LENGTH:])
            cap_nhat_lich_su_file()
            print(f"{GREEN}Khởi tạo lịch sử bot từ API: {''.join(lich_su)}{RESET}")
        else:
            print(f"{YELLOW}Không có lịch sử cầu từ API để khởi tạo. Bắt đầu với lịch sử trống.{RESET}")
            lich_su = [] # Đảm bảo lich_su không rỗng nếu API không cung cấp

        # Giờ xử lý phiên hiện tại mà API vừa trả về kết quả
        tong_hien_tai = sum(xuc_xac_api)
        kq_thucte_phien_hien_tai = tai_xiu(tong_hien_tai)

        if not lich_su or lich_su[-1] != kq_thucte_phien_hien_tai:
            # Nếu lịch sử chưa có hoặc kết quả cuối cùng không khớp, thêm vào
            print(f"{YELLOW}Đang đồng bộ lịch sử bot với kết quả phiên {phien_hien_tai_api} từ API ({kq_thucte_phien_hien_tai}).{RESET}")
            lich_su.append(kq_thucte_phien_hien_tai)
            lich_su = lich_su[-MAX_PATTERN_LENGTH:] # Giới hạn độ dài
            cap_nhat_lich_su_file()

        # Cập nhật trạng thái MD5 cho phiên tiếp theo
        simulate_md5_analysis()
        last_processed_phien = phien_hien_tai_api

        # Log phiên đầu tiên và chuẩn bị dự đoán cho phiên sau đó
        current_history_str_for_prediction = "".join(lich_su)
        all_predictions = get_all_predictions(current_history_str_for_prediction)
        final_choice = chot_keo_cuoi_cung(all_predictions)

        phien_tiep_theo = phien_hien_tai_api + 1 # Phiên mà bot sẽ dự đoán
        await send_prediction_notification(phien_tiep_theo, all_predictions, final_choice)
        pending_predictions[phien_tiep_theo] = {
            "history_str": current_history_str_for_prediction,
            "all_predictions": all_predictions,
            "final_choice": final_choice
        }
        log_prediction_data(phien_tiep_theo, current_history_str_for_prediction, all_predictions, final_choice)
        print(f"{BOLD}Đã khởi tạo và dự đoán phiên #{phien_tiep_theo}.{RESET}")
        print(f"Lịch sử cầu bot: {''.join(lich_su)}")
        os.system('cls' if os.name == 'nt' else 'clear')
        return # Thoát để chờ phiên tiếp theo


    if phien_hien_tai_api > last_processed_phien:
        # Xử lý kết quả của phiên mà API vừa trả về (là phiên trước của API)
        # Phiên này chính là phiên mà bot đã dự đoán TRƯỚC ĐÓ (last_processed_phien + 1)
        # Hoặc là phiên mà bot CHƯA HỀ DỰ ĐOÁN nếu đây là phiên nhảy cóc
        
        phien_expected_result = last_processed_phien + 1
        
        if phien_hien_tai_api != phien_expected_result:
            print(f"{YELLOW}Cảnh báo: Phát hiện phiên nhảy cóc từ {last_processed_phien} lên {phien_hien_tai_api}.{RESET}")
            # Xử lý các phiên bị bỏ lỡ nếu cần, ở đây ta chỉ cập nhật lịch sử
            # và bỏ qua việc học/thông báo chi tiết cho các phiên nhảy cóc.
            # Lấy kết quả thực tế từ API để cập nhật lịch sử
            tong_hien_tai = sum(xuc_xac_api)
            kq_thucte_phien_hien_tai = tai_xiu(tong_hien_tai)
            lich_su.append(kq_thucte_phien_hien_tai)
            lich_su = lich_su[-MAX_PATTERN_LENGTH:]
            cap_nhat_lich_su_file()
            
            # Cập nhật last_processed_phien và MD5 cho phiên tiếp theo
            simulate_md5_analysis()
            last_processed_phien = phien_hien_tai_api
            print(f"{YELLOW}Đã cập nhật lịch sử bot đến phiên {phien_hien_tai_api} do nhảy cóc.{RESET}")
            # Không có dự đoán trước đó, nên bỏ qua phần kết quả và học hỏi chi tiết cho phiên này
            
        else: # Phiên liên tiếp, xử lý như bình thường
            if phien_expected_result in pending_predictions:
                prediction_data = pending_predictions.pop(phien_expected_result)
                
                # Cập nhật kết quả thực tế cho phiên này
                tong_hien_tai = sum(xuc_xac_api)
                kq_thucte_phien_hien_tai = tai_xiu(tong_hien_tai)

                # Gửi thông báo kết quả
                await send_result_notification(phien_expected_result, xuc_xac_api, tong_hien_tai, kq_thucte_phien_hien_tai, prediction_data)
                
                # Cập nhật lịch sử và cho AI học hỏi
                lich_su.append(kq_thucte_phien_hien_tai)
                lich_su = lich_su[-MAX_PATTERN_LENGTH:]
                cap_nhat_lich_su_file()
                
                is_win = (prediction_data['final_choice']['ket_qua'] == kq_thucte_phien_hien_tai) if prediction_data['final_choice']['ket_qua'] != "Bỏ qua" else None
                log_prediction_data(phien_expected_result, prediction_data['history_str'], prediction_data['all_predictions'], prediction_data['final_choice'], kq_thucte_phien_hien_tai, is_win)

                ai_hoc_hoi(list(prediction_data['history_str']), kq_thucte_phien_hien_tai) # history_str.split() nếu là chuỗi đã có khoảng trắng

                # Cập nhật trạng thái MD5 cho phiên tiếp theo
                simulate_md5_analysis()
                last_processed_phien = phien_hien_tai_api
                
                print(f"{BOLD}Đã xử lý kết quả phiên #{phien_expected_result}.{RESET}")
                print(f"Lịch sử cầu bot: {''.join(lich_su)}")
            else:
                # Trường hợp bot vừa khởi động và bỏ lỡ phiên dự đoán trước đó
                # hoặc có lỗi trong pending_predictions. Chỉ cập nhật lịch sử.
                print(f"{YELLOW}Cảnh báo: Không tìm thấy dự đoán cho phiên {phien_expected_result} trong pending_predictions. Chỉ cập nhật lịch sử.{RESET}")
                tong_hien_tai = sum(xuc_xac_api)
                kq_thucte_phien_hien_tai = tai_xiu(tong_hien_tai)
                lich_su.append(kq_thucte_phien_hien_tai)
                lich_su = lich_su[-MAX_PATTERN_LENGTH:]
                cap_nhat_lich_su_file()
                simulate_md5_analysis() # Cập nhật MD5
                last_processed_phien = phien_hien_tai_api


        # === DỰ ĐOÁN CHO PHIÊN TIẾP THEO ===
        # Phiên tiếp theo luôn là phiên_hien_tai_api + 1
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
        
        os.system('cls' if os.name == 'nt' else 'clear')
        print(f"{BOLD}Đã xử lý kết quả phiên #{phien_hien_tai_api}, dự đoán cho phiên #{phien_tiep_theo}.{RESET}")
        print(f"Lịch sử cầu bot: {''.join(lich_su)}")
        print(f"Dự đoán chờ xử lý: {list(pending_predictions.keys())}")


def simulate_md5_analysis():
    """Mô phỏng kết quả MD5: 2 Gãy -> 1 Khác."""
    global md5_giai_doan_counter, md5_analysis_result
    if md5_giai_doan_counter < 2:
        md5_analysis_result = "Gãy"
        md5_giai_doan_counter += 1
    else:
        md5_analysis_result = "Khác"
        md5_giai_doan_counter = 0

# ==== HÀM KHỞI CHẠY BOT ====
def start_command_handler(message):
    active_chat_ids.add(message.chat.id)
    bot.reply_to(message, "✅ <b>Bot đã được kích hoạt!</b>\nTôi sẽ tự động gửi dự đoán cho các phiên sắp tới.", parse_mode='HTML')
    print(f"{GREEN}Đã nhận /start từ {message.chat.id}{RESET}")

def stop_command_handler(message):
    active_chat_ids.discard(message.chat.id)
    bot.reply_to(message, "❌ <b>Bot đã tạm dừng.</b>\nGõ /start để nhận lại dự đoán.", parse_mode='HTML')
    print(f"{YELLOW}Đã nhận /stop từ {message.chat.id}{RESET}")

# ==== FLASK SERVER ĐỂ GIỮ DỊCH VỤ LUÔN CHẠY TRÊN RENDER (NẾU DÙNG WEB SERVICE) ====
app = Flask(__name__)

@app.route('/')
def hello_world():
    return 'Bot is running and Flask server is active!'

def run_flask_app():
    port = int(os.environ.get("PORT", 10000))
    print(f"{YELLOW}Bắt đầu Flask server trên cổng {port} để giữ dịch vụ luôn chạy...{RESET}")
    app.run(host='0.0.0.0', port=port, debug=False)


async def run_main_loop_periodically():
    while True:
        try:
            if active_chat_ids: # Chỉ chạy vòng lặp chính nếu có ít nhất 1 chat_id hoạt động
                await main_bot_loop()
            else:
                print(f"{YELLOW}Không có Chat ID hoạt động, bot tạm dừng kiểm tra phiên mới.{RESET}")
        except Exception as e:
            print(f"{RED}Lỗi trong vòng lặp chính: {e}{RESET}")
            import traceback
            traceback.print_exc() # In chi tiết lỗi để debug
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)

async def main():
    global bot
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_BOT_TOKEN:
        print(f"{RED}{BOLD}LỖI: Biến môi trường TELEGRAM_BOT_TOKEN chưa được đặt.{RESET}")
        return

    bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
    bot.register_message_handler(start_command_handler, commands=['start'])
    bot.register_message_handler(stop_command_handler, commands=['stop'])

    load_data() # Tải dữ liệu ban đầu
    print(f"{BOLD}{GREEN}=== TOOL TX PRO AI V3 (CHỦ ĐỘNG) ===")
    print(f"Bot đã sẵn sàng. Đang chờ lệnh /start...{RESET}")

    # Khởi chạy Flask server trong một thread riêng
    import threading
    flask_thread = threading.Thread(target=run_flask_app)
    flask_thread.daemon = True
    flask_thread.start()
    
    # Bắt đầu vòng lặp chính để kiểm tra API và xử lý phiên
    asyncio.create_task(run_main_loop_periodically())
    
    print(f"{YELLOW}Bắt đầu polling Telegram...{RESET}")
    # Chạy polling trong một thread khác để không chặn event loop chính
    await asyncio.to_thread(bot.polling, none_stop=True, interval=0, timeout=20)

if __name__ == "__main__":
    try:
        os.system('cls' if os.name == 'nt' else 'clear')
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{RED}{BOLD}Đã dừng bot.{RESET}")
    except Exception as e:
        print(f"\n{RED}{BOLD}Lỗi nghiêm trọng: {e}{RESET}")
        import traceback
        traceback.print_exc()

