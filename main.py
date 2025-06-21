import telebot
import requests
import time
import json
import os
import random
import string
from datetime import datetime, timedelta
from threading import Thread, Event, Lock
from flask import Flask, request
import re # Thêm thư viện regex

# --- Cấu hình Bot (ĐẶT TRỰC TIẾP TẠY ĐÂY) ---
BOT_TOKEN = "7820739987:AAE_eU2JPZH7u6KnDRq31_l4tn64AD_8f6s" 
ADMIN_IDS = [6915752059] 

DATA_FILE = 'user_data.json'
CAU_PATTERNS_FILE = 'cau_patterns.json' # Cấu trúc thủ công, có định dạng "Dự đoán: X - Loại cầu: Cầu Y"
DYNAMIC_LEARNED_PATTERNS_FILE = 'dynamic_learned_patterns.json' # File mới cho mẫu cầu tự học
CODES_FILE = 'codes.json'

# Cấu hình cho nhiều game (Sửa đổi GAME_CONFIGS)
GAME_CONFIGS = {
    "luckywin": { 
        "api_url": "https://1.bot/GetNewLottery/LT_Taixiu",
        "name": "Luckywin",
        "pattern_prefix": "L", 
        "tx_history_length": 13, # TĂNG LÊN 13
        "refresh_interval": 10,
        "is_maintenance": False, # Trạng thái bảo trì
        "maintenance_reason": None # Lý do bảo trì
    },
    "hitclub": {
        "api_url": "https://apihitclub.up.railway.app/api/taixiu", 
        "name": "Hit Club",
        "pattern_prefix": "H", 
        "tx_history_length": 13, # TĂNG LÊN 13
        "refresh_interval": 10,
        "is_maintenance": False,
        "maintenance_reason": None
    },
    "sunwin": { 
        "api_url": "https://wanglinapiws.up.railway.app/api/taixiu", 
        "name": "Sunwin",
        "pattern_prefix": "S", 
        "tx_history_length": 13, # TĂNG LÊN 13
        "refresh_interval": 10,
        "is_maintenance": False,
        "maintenance_reason": None
    }
}

# --- Khởi tạo Flask App và Telegram Bot ---
app = Flask(__name__)
bot = telebot.TeleBot(BOT_TOKEN)

# Global flags và objects
bot_enabled = True
bot_disable_reason = "Không có"
bot_disable_admin_id = None
prediction_stop_event = Event() 
bot_initialized = False 
bot_init_lock = Lock() 

# Global data structures
user_data = {}
CAU_PATTERNS = {} # Mẫu cầu thủ công
DYNAMIC_LEARNED_PATTERNS = {} # Mẫu cầu tự học
GENERATED_CODES = {} 

# Quản lý trạng thái riêng biệt cho mỗi game (last_id, tx_history, last_checked_time)
game_states = {}
for game_id in GAME_CONFIGS.keys():
    game_states[game_id] = {
        "last_id": None,
        "tx_history": [], # Sẽ chứa "T" hoặc "X"
        "last_checked_time": 0 
    }

# --- Quản lý dữ liệu người dùng, mẫu cầu và code ---
def load_user_data():
    global user_data
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            try:
                user_data = json.load(f)
                for user_id_str, user_info in user_data.items():
                    user_info.setdefault('is_paused_prediction', False)
                    current_subscribed_games = user_info.get('subscribed_games', [])
                    if isinstance(current_subscribed_games, str):
                        user_info['subscribed_games'] = [current_subscribed_games] if current_subscribed_games else []
                    elif not isinstance(current_subscribed_games, list):
                        user_info['subscribed_games'] = []
                    user_info.setdefault('is_banned', False) 
                    user_info.setdefault('ban_reason', None) 
                    user_info.setdefault('maintenance_override', False) 
            except json.JSONDecodeError:
                print(f"Lỗi đọc {DATA_FILE}. Khởi tạo lại dữ liệu người dùng.")
                user_data = {}
    else:
        user_data = {}
    print(f"Loaded {len(user_data)} user records from {DATA_FILE}")

def save_user_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def load_cau_patterns():
    global CAU_PATTERNS
    if os.path.exists(CAU_PATTERNS_FILE):
        with open(CAU_PATTERNS_FILE, 'r') as f:
            try:
                CAU_PATTERNS = json.load(f)
            except json.JSONDecodeError:
                print(f"Lỗi đọc {CAU_PATTERNS_FILE}. Khởi tạo lại mẫu cầu thủ công.")
                CAU_PATTERNS = {}
    else:
        CAU_PATTERNS = {}
    
    for game_id in GAME_CONFIGS.keys():
        if game_id not in CAU_PATTERNS:
            CAU_PATTERNS[game_id] = {}

    print(f"Loaded manual patterns for {len(CAU_PATTERNS)} games.")

def save_cau_patterns():
    with open(CAU_PATTERNS_FILE, 'w') as f:
        json.dump(CAU_PATTERNS, f, indent=4)

def load_dynamic_learned_patterns():
    global DYNAMIC_LEARNED_PATTERNS
    if os.path.exists(DYNAMIC_LEARNED_PATTERNS_FILE):
        with open(DYNAMIC_LEARNED_PATTERNS_FILE, 'r') as f:
            try:
                DYNAMIC_LEARNED_PATTERNS = json.load(f)
            except json.JSONDecodeError:
                print(f"Lỗi đọc {DYNAMIC_LEARNED_PATTERNS_FILE}. Khởi tạo lại mẫu cầu tự học.")
                DYNAMIC_LEARNED_PATTERNS = {}
    else:
        DYNAMIC_LEARNED_PATTERNS = {}
    
    for game_id in GAME_CONFIGS.keys():
        if game_id not in DYNAMIC_LEARNED_PATTERNS:
            DYNAMIC_LEARNED_PATTERNS[game_id] = {}

    print(f"Loaded dynamic learned patterns for {len(DYNAMIC_LEARNED_PATTERNS)} games.")

def save_dynamic_learned_patterns():
    with open(DYNAMIC_LEARNED_PATTERNS_FILE, 'w') as f:
        json.dump(DYNAMIC_LEARNED_PATTERNS, f, indent=4)

def load_codes():
    global GENERATED_CODES
    if os.path.exists(CODES_FILE):
        with open(CODES_FILE, 'r') as f:
            try:
                GENERATED_CODES = json.load(f)
            except json.JSONDecodeError:
                print(f"Lỗi đọc {CODES_FILE}. Khởi tạo lại mã code.")
                GENERATED_CODES = {}
    else:
        GENERATED_CODES = {}
    print(f"Loaded {len(GENERATED_CODES)} codes from {CODES_FILE}")

def save_codes():
    with open(CODES_FILE, 'w') as f:
        json.dump(GENERATED_CODES, f, indent=4)

def is_admin(user_id):
    return user_id in ADMIN_IDS

def is_ctv(user_id):
    return is_admin(user_id) or (str(user_id) in user_data and user_data[str(user_id)].get('is_ctv'))

def can_override_maintenance(user_id):
    return is_admin(user_id) or (str(user_id) in user_data and user_data[str(user_id)].get('maintenance_override', False))

def check_subscription(user_id):
    user_id_str = str(user_id)

    if user_id_str in user_data and user_data[user_id_str].get('is_banned'):
        ban_reason = user_data[user_id_str].get('ban_reason', 'Không rõ lý do')
        return False, f"❌ Bạn đã bị cấm sử dụng bot. Lý do: `{ban_reason}`. Vui lòng liên hệ Admin để biết thêm chi tiết."

    if is_admin(user_id) or is_ctv(user_id):
        return True, "Bạn là Admin/CTV, quyền truy cập vĩnh viễn."

    if user_id_str not in user_data or user_data[user_id_str].get('expiry_date') is None:
        return False, "⚠️ Bạn chưa đăng ký hoặc tài khoản chưa được gia hạn."

    expiry_date_str = user_data[user_id_str]['expiry_date']
    expiry_date = datetime.strptime(expiry_date_str, '%Y-%m-%d %H:%M:%S')

    if datetime.now() < expiry_date:
        remaining_time = expiry_date - datetime.now()
        days = remaining_time.days
        hours = remaining_time.seconds // 3600
        minutes = (remaining_time.seconds % 3600) // 60
        return True, f"✅ Tài khoản của bạn còn hạn đến: `{expiry_date_str}` ({days} ngày {hours} giờ {minutes} phút)."
    else:
        return False, "❌ Tài khoản của bạn đã hết hạn."

# --- Logic dự đoán Tài Xỉu ---
def du_doan_theo_xi_ngau(dice_list):
    if not dice_list:
        return "Đợi thêm dữ liệu"
    d1, d2, d3 = dice_list[-1]
    total = d1 + d2 + d3

    results = []
    for d in [d1, d2, d3]:
        tmp = d + total
        while tmp > 6: 
            tmp -= 6
        if tmp % 2 == 0:
            results.append("Tài")
        else:
            results.append("Xỉu")

    tai_count = results.count("Tài")
    xiu_count = results.count("Xỉu")
    if tai_count >= xiu_count:
        return "Tài"
    else:
        return "Xỉu"


def tinh_tai_xiu(dice):
    total = sum(dice)
    if total >= 11:
        return "Tài", total
    else:
        return "Xỉu", total

# --- Lấy dữ liệu từ API ---
def lay_du_lieu(game_id):
    config = GAME_CONFIGS.get(game_id)
    if not config:
        print(f"Lỗi: Cấu hình game '{game_id}' không tồn tại.")
        return None

    api_url = config["api_url"]
    try:
        response = requests.get(api_url)
        response.raise_for_status() 
        data = response.json()
        
        if game_id == "luckywin":
            if data.get("state") != 1:
                return None
            return {
                "ID": data.get("data", {}).get("ID"),
                "Expect": data.get("data", {}).get("Expect"),
                "OpenCode": data.get("data", {}).get("OpenCode")
            }
        elif game_id in ["hitclub", "sunwin"]: 
            if not all(k in data for k in ["Phien", "Xuc_xac_1", "Xuc_xac_2", "Xuc_xac_3"]): 
                 return None
            
            xuc_xac_1 = data.get("Xuc_xac_1")
            xuc_xac_2 = data.get("Xuc_xac_2")
            xuc_xac_3 = data.get("Xuc_xac_3")

            if not all(isinstance(x, int) for x in [xuc_xac_1, xuc_xac_2, xuc_xac_3]):
                return None

            return {
                "ID": data.get("Phien"), 
                "Expect": data.get("Phien"),
                "OpenCode": f"{xuc_xac_1},{xuc_xac_2},{xuc_xac_3}"
            }
        else:
            print(f"Lỗi: Game '{game_id}' không được hỗ trợ trong hàm lay_du_lieu.")
            return None

    except requests.exceptions.RequestException as e:
        print(f"Lỗi khi lấy dữ liệu từ API {api_url} cho {game_id}: {e}")
        return None
    except json.JSONDecodeError:
        print(f"Lỗi giải mã JSON từ API {api_url} cho {game_id}. Phản hồi không phải JSON hợp lệ.")
        return None
    except Exception as e:
        print(f"Lỗi không xác định trong lay_du_lieu cho {game_id}: {e}")
        return None

# Hàm mới để cập nhật mẫu cầu tự học (Dynamic Learned Patterns)
def update_dynamic_learned_patterns(game_id, pattern_str, predicted_result, actual_result):
    if len(pattern_str) != GAME_CONFIGS[game_id]["tx_history_length"]:
        return # Chỉ cập nhật khi đủ chiều dài mẫu cầu

    game_dp = DYNAMIC_LEARNED_PATTERNS.setdefault(game_id, {})
    
    current_confidence = game_dp.get(pattern_str, {}).get("confidence", 2.5) # Mặc định 2.5

    if predicted_result == actual_result:
        # Dự đoán đúng, tăng độ tin cậy
        new_confidence = min(current_confidence + 0.2, 5.0) 
    else:
        # Dự đoán sai, giảm độ tin cậy
        new_confidence = max(current_confidence - 0.5, 0.1) 
    
    game_dp[pattern_str] = {
        "du_doan": predicted_result, # Lưu dự đoán cuối cùng khi cập nhật confidence
        "confidence": new_confidence,
        "last_updated": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    save_dynamic_learned_patterns()

# --- Logic chính của Bot dự đoán (chạy trong luồng riêng) ---
def prediction_loop(stop_event: Event):
    print("Prediction loop started.")
    while not stop_event.is_set():
        if not bot_enabled:
            time.sleep(10) 
            continue

        for game_id, config in GAME_CONFIGS.items():
            current_game_state = game_states[game_id]
            current_time = time.time()

            if current_time - current_game_state["last_checked_time"] < config["refresh_interval"]:
                continue 

            current_game_state["last_checked_time"] = current_time 

            data = lay_du_lieu(game_id)
            if not data:
                continue 

            issue_id = data.get("ID")
            expect = data.get("Expect")
            open_code = data.get("OpenCode")

            if not all([issue_id, expect, open_code]):
                current_game_state["last_id"] = issue_id 
                continue

            if issue_id != current_game_state["last_id"]:
                current_game_state["last_id"] = issue_id 
                print(f"\n--- Xử lý phiên mới cho {config['name']} ({issue_id}) ---") 

                try:
                    dice = tuple(map(int, open_code.split(",")))
                    if len(dice) != 3:
                        raise ValueError("OpenCode không chứa 3 xúc xắc.")
                except ValueError as e:
                    print(f"Lỗi phân tích OpenCode cho {config['name']}: '{open_code}'. Lỗi: {e}. Bỏ qua phiên này.")
                    continue
                
                ket_qua_tx, tong = tinh_tai_xiu(dice) # Kết quả thực tế của phiên vừa qua

                tx_history_for_game = current_game_state["tx_history"]
                tx_history_length = config["tx_history_length"]

                # Cập nhật lịch sử Tài/Xỉu
                if len(tx_history_for_game) >= tx_history_length:
                    tx_history_for_game.pop(0)
                tx_history_for_game.append("T" if ket_qua_tx == "Tài" else "X")
                current_game_state["tx_history"] = tx_history_for_game 

                # Tính next_expect
                if game_id == "luckywin":
                    next_expect = str(int(expect) + 1).zfill(len(expect))
                elif game_id in ["hitclub", "sunwin"]: 
                    next_expect = str(int(expect) + 1) 
                else:
                    next_expect = str(int(expect) + 1) 

                du_doan_cuoi_cung = ""
                loai_cau_text = ""
                ly_do = ""
                predicted_result_for_learning = "" # Dự đoán mà bot thực sự đưa ra (để dùng cho học)

                current_cau_str = ''.join(tx_history_for_game)
                
                if len(current_cau_str) == tx_history_length:
                    # 1. Ưu tiên tìm trong CAU_PATTERNS (thủ công)
                    matched_manual_pattern_info = CAU_PATTERNS.get(game_id, {}).get(current_cau_str)
                    if matched_manual_pattern_info:
                        du_doan_cuoi_cung = matched_manual_pattern_info["du_doan"]
                        loai_cau_text = matched_manual_pattern_info["loai_cau"]
                        ly_do = f"AI Mẫu cầu thủ công khớp: {current_cau_str} - Loại: {loai_cau_text}"
                        predicted_result_for_learning = du_doan_cuoi_cung
                    else:
                        # 2. Không có mẫu thủ công, tìm trong DYNAMIC_LEARNED_PATTERNS (tự học)
                        dynamic_pattern_info = DYNAMIC_LEARNED_PATTERNS.get(game_id, {}).get(current_cau_str)
                        if dynamic_pattern_info:
                            confidence = dynamic_pattern_info["confidence"]
                            base_prediction = du_doan_theo_xi_ngau([dice]) # Dự đoán cơ sở cho phiên tiếp theo
                            
                            if confidence >= 2.5: # Cầu đẹp/đáng tin cậy
                                du_doan_cuoi_cung = base_prediction
                                loai_cau_text = f"Cầu đã học (Tự động, Confidence: {confidence:.1f})"
                                ly_do = f"AI Mẫu cầu tự học khớp: {current_cau_str} - Confidence Cao"
                            elif confidence <= 0.5: # Cầu xấu/không đáng tin cậy
                                du_doan_cuoi_cung = "Tài" if base_prediction == "Xỉu" else "Xỉu" # Đảo chiều
                                loai_cau_text = f"Cầu đã học (Tự động, Confidence: {confidence:.1f})"
                                ly_do = f"AI Mẫu cầu tự học khớp: {current_cau_str} - Confidence Thấp (đảo chiều)"
                            else: # Trung bình, dự đoán theo cơ sở
                                du_doan_cuoi_cung = base_prediction
                                loai_cau_text = f"Cầu đã học (Tự động, Confidence: {confidence:.1f})"
                                ly_do = f"AI Mẫu cầu tự học khớp: {current_cau_str} - Confidence Trung bình"
                            predicted_result_for_learning = du_doan_cuoi_cung # Đây là dự đoán bot đưa ra
                        else:
                            # 3. Không có cả mẫu thủ công lẫn mẫu tự học, dự đoán theo xí ngầu và thêm vào dynamic learned patterns
                            du_doan_cuoi_cung = du_doan_theo_xi_ngau([dice])
                            loai_cau_text = "Chưa có mẫu cầu này (Đang học)"
                            ly_do = f"AI Dự đoán theo xí ngầu (chưa có mẫu cầu {current_cau_str})"
                            predicted_result_for_learning = du_doan_cuoi_cung

                            # Thêm mẫu mới vào DYNAMIC_LEARNED_PATTERNS với confidence ban đầu
                            DYNAMIC_LEARNED_PATTERNS.setdefault(game_id, {})[current_cau_str] = {
                                "du_doan": du_doan_cuoi_cung, # Dự đoán ban đầu của mẫu này
                                "confidence": 2.5, # Confidence ban đầu
                                "last_updated": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            }
                            save_dynamic_learned_patterns()
                else:
                    # Chưa đủ lịch sử để tạo chuỗi 13 phiên
                    du_doan_cuoi_cung = du_doan_theo_xi_ngau([dice])
                    loai_cau_text = "Chưa đủ lịch sử cầu"
                    ly_do = f"AI Dự đoán theo xí ngầu (chưa đủ lịch sử {tx_history_length} ký tự)"
                    predicted_result_for_learning = du_doan_cuoi_cung
                
                # CẬP NHẬT MẪU CẦU TỰ HỌC SAU KHI BIẾT KẾT QUẢ THỰC TẾ
                # Chỉ cập nhật dynamic learned patterns nếu mẫu đó KHÔNG phải là mẫu thủ công
                if len(current_cau_str) == tx_history_length and current_cau_str not in CAU_PATTERNS.get(game_id, {}):
                    update_dynamic_learned_patterns(game_id, current_cau_str, predicted_result_for_learning, ket_qua_tx)


                # Gửi tin nhắn dự đoán
                sent_count = 0
                for user_id_str, user_info in list(user_data.items()): 
                    user_id = int(user_id_str)
                    
                    if user_info.get('is_banned', False): 
                        continue

                    # Kiểm tra trạng thái bảo trì của game
                    if config["is_maintenance"] and not can_override_maintenance(user_id):
                        maintenance_msg = (
                            f"⚙️ **THÔNG BÁO BẢO TRÌ {config['name']}** ⚙️\n"
                            f"Game **{config['name']}** hiện đang tạm thời bảo trì.\n"
                            f"Lý do: `{config['maintenance_reason'] or 'Không rõ'}`\n"
                            f"Vui lòng thử lại sau. Liên hệ Admin nếu cần hỗ trợ."
                        )
                        try:
                            bot.send_message(user_id, maintenance_msg, parse_mode='Markdown')
                        except telebot.apihelper.ApiTelegramException as e:
                            if "bot was blocked by the user" in str(e) or "user is deactivated" in str(e):
                                pass
                            else:
                                print(f"Lỗi gửi tin nhắn bảo trì cho user {user_id} (game {game_id}): {e}")
                        continue # Bỏ qua người dùng này, không gửi dự đoán

                    # Kiểm tra người dùng có muốn nhận dự đoán cho game này không
                    if game_id not in user_info.get('subscribed_games', []):
                        continue 

                    # Kiểm tra xem người dùng đã tạm ngừng nhận dự đoán chưa
                    if user_info.get('is_paused_prediction', False):
                        continue 

                    is_sub, sub_message = check_subscription(user_id)
                    if is_sub:
                        try:
                            prediction_message = (
                                f"🎮 **KẾT QUẢ PHIÊN HIỆN TẠI ({config['name']})** 🎮\n"
                                f"Phiên: `{expect}` | Kết quả: **{ket_qua_tx}** (Tổng: **{tong}**)\n\n"
                                f"**Dự đoán cho phiên tiếp theo:**\n"
                                f"🔢 Phiên: `{next_expect}`\n"
                                f"🤖 Dự đoán: **{du_doan_cuoi_cung}**\n"
                                f"📌 Loại cầu: _{loai_cau_text}_\n"
                                f"⚠️ **Hãy đặt cược sớm trước khi phiên kết thúc!**"
                            )
                            bot.send_message(user_id, prediction_message, parse_mode='Markdown')
                            sent_count += 1
                        except telebot.apihelper.ApiTelegramException as e:
                            if "bot was blocked by the user" in str(e) or "user is deactivated" in str(e):
                                pass 
                            else:
                                print(f"Lỗi gửi tin nhắn cho user {user_id} (game {game_id}): {e}")
                        except Exception as e:
                            print(f"Lỗi không xác định khi gửi tin nhắn cho user {user_id} (game {game_id}): {e}")
                
                print(f"DEBUG: Đã gửi dự đoán cho {config['name']} tới {sent_count} người dùng.")
                print("-" * 50)
                print(f"🎮 KẾT QUẢ VÀ DỰ ĐOÁN CHO {config['name']}")
                print(f"Phiên hiện tại: `{expect}` | Kết quả: {ket_qua_tx} (Tổng: {tong})")
                print(f"🔢 Phiên tiếp theo: `{next_expect}`")
                print(f"🤖 Dự đoán: {du_doan_cuoi_cung}") 
                print(f"📌 Loại cầu: {loai_cau_text}")
                print(f"Lịch sử TX ({tx_history_length} phiên): {current_cau_str}")
                print("-" * 50)
            else:
                pass
        
        time.sleep(5) 
    print("Prediction loop stopped.")

# --- Xử lý lệnh Telegram ---

@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = str(message.chat.id)
    username = message.from_user.username or message.from_user.first_name

    if user_id in user_data and user_data[user_id].get('is_banned'):
        ban_reason = user_data[user_id].get('ban_reason', 'Không rõ lý do')
        bot.reply_to(message, f"❌ Bạn đã bị cấm sử dụng bot. Lý do: `{ban_reason}`. Vui lòng liên hệ Admin để biết thêm chi tiết.", parse_mode='Markdown')
        return
    
    if user_id not in user_data:
        user_data[user_id] = {
            'username': username,
            'expiry_date': None,
            'is_ctv': False,
            'is_paused_prediction': False, 
            'subscribed_games': [], 
            'is_banned': False, 
            'ban_reason': None,
            'maintenance_override': False 
        }
        save_user_data(user_data)
        bot.reply_to(message, 
                     "Chào mừng bạn đến với **BOT DỰ ĐOÁN TÀI XỈU**!\n"
                     "Để nhận dự đoán, hãy dùng lệnh `/dudoan`, `/dudoan_hitclub` hoặc `/dudoan_sunwin`.\n"
                     "Hãy dùng lệnh /help để xem danh sách các lệnh hỗ trợ.", 
                     parse_mode='Markdown')
    else:
        user_data[user_id]['username'] = username 
        user_data[user_id].setdefault('is_paused_prediction', False) 
        user_data[user_id].setdefault('subscribed_games', []) 
        user_data[user_id].setdefault('is_banned', False) 
        user_data[user_id].setdefault('ban_reason', None) 
        user_data[user_id].setdefault('maintenance_override', False)
        save_user_data(user_data)
        bot.reply_to(message, "Bạn đã khởi động bot rồi. Dùng /help để xem các lệnh.")

@bot.message_handler(commands=['help'])
def show_help(message):
    user_id_str = str(message.chat.id)
    if user_id_str in user_data and user_data[user_id_str].get('is_banned'):
        ban_reason = user_data[user_id_str].get('ban_reason', 'Không rõ lý do')
        bot.reply_to(message, f"❌ Bạn đã bị cấm sử dụng bot. Lý do: `{ban_reason}`. Vui lòng liên hệ Admin để biết thêm chi tiết.", parse_mode='Markdown')
        return

    help_text = (
        "🤖 **DANH SÁCH LỆNH HỖ TRỢ** 🤖\n\n"
        "**Lệnh người dùng:**\n"
        "🔸 `/start`: Khởi động bot và thêm bạn vào hệ thống.\n"
        "🔸 `/help`: Hiển thị danh sách các lệnh.\n"
        "🔸 `/support`: Thông tin hỗ trợ Admin.\n"
        "🔸 `/gia`: Xem bảng giá dịch vụ.\n"
        "🔸 `/gopy <nội dung>`: Gửi góp ý/báo lỗi cho Admin.\n"
        "🔸 `/nap`: Hướng dẫn nạp tiền.\n"
        "🔸 `/dudoan`: Bắt đầu nhận dự đoán cho **Luckywin**.\n"
        "🔸 `/dudoan_hitclub`: Bắt đầu nhận dự đoán cho **Hit Club**.\n"
        "🔸 `/dudoan_sunwin`: Bắt đầu nhận dự đoán cho **Sunwin**.\n" 
        "🔸 `/code <mã_code>`: Nhập mã code để gia hạn tài khoản.\n"
        "🔸 `/stop [tên game]`: Tạm ngừng nhận dự đoán (để trống để tạm ngừng tất cả, hoặc chỉ định game).\n" 
        "🔸 `/continue [tên game]`: Tiếp tục nhận dự đoán (để trống để tiếp tục tất cả, hoặc chỉ định game).\n\n" 
    )
    
    if is_ctv(message.chat.id):
        help_text += (
            "**Lệnh Admin/CTV:**\n"
            "🔹 `/full <id>`: Xem thông tin người dùng (để trống ID để xem của bạn).\n"
            "🔹 `/giahan <id> <số ngày/giờ>`: Gia hạn tài khoản người dùng. Ví dụ: `/giahan 12345 1 ngày` hoặc `/giahan 12345 24 giờ`.\n\n"
        )
    
    if is_admin(message.chat.id):
        help_text += (
            "**Lệnh Admin Chính:**\n"
            "👑 `/ctv <id>`: Thêm người dùng làm CTV.\n"
            "👑 `/xoactv <id>`: Xóa người dùng khỏi CTV.\n"
            "👑 `/tb <nội dung>`: Gửi thông báo đến tất cả người dùng.\n"
            "👑 `/tatbot <lý do>`: Tắt mọi hoạt động của bot dự đoán.\n"
            "👑 `/mokbot`: Mở lại hoạt động của bot dự đoán.\n"
            "👑 `/taocode <giá trị> <ngày/giờ> <số lượng>`: Tạo mã code gia hạn. Ví dụ: `/taocode 1 ngày 5`.\n"
            "👑 `/maucau <tên game>`: Hiển thị các mẫu cầu (thủ công và tự học) đã thu thập cho game.\n" 
            "👑 `/nhapcau <tên game>`: Nhập các mẫu cầu từ văn bản cho bot (chỉ cho mẫu thủ công).\n" 
            "👑 `/kiemtra`: Kiểm tra thông tin tất cả người dùng bot.\n"
            "👑 `/xoahan <id>`: Xóa số ngày còn lại của người dùng.\n" 
            "👑 `/ban <id> [lý do]`: Cấm người dùng sử dụng bot.\n" 
            "👑 `/unban <id>`: Bỏ cấm người dùng.\n" 
            "👑 `/baotri <tên game> [lý do]`: Đặt game vào trạng thái bảo trì.\n" 
            "👑 `/mobaochi <tên game>`: Bỏ trạng thái bảo trì cho game.\n" 
            "👑 `/override <id>`: Cấp quyền Admin/CTV vẫn nhận dự đoán khi game bảo trì.\n" 
            "👑 `/unoverride <id>`: Xóa quyền Admin/CTV override bảo trì.\n" 
        )
    
    bot.reply_to(message, help_text, parse_mode='Markdown')

@bot.message_handler(commands=['support'])
def show_support(message):
    user_id_str = str(message.chat.id)
    if user_id_str in user_data and user_data[user_id_str].get('is_banned'):
        ban_reason = user_data[user_id_str].get('ban_reason', 'Không rõ lý do')
        bot.reply_to(message, f"❌ Bạn đã bị cấm sử dụng bot. Lý do: `{ban_reason}`. Vui lòng liên hệ Admin để biết thêm chi tiết.", parse_mode='Markdown')
        return

    bot.reply_to(message, 
        "Để được hỗ trợ, vui lòng liên hệ Admin:\n"
        "@heheviptool hoặc @Besttaixiu999"
    )

@bot.message_handler(commands=['gia'])
def show_price(message):
    user_id_str = str(message.chat.id)
    if user_id_str in user_data and user_data[user_id_str].get('is_banned'):
        ban_reason = user_data[user_id_str].get('ban_reason', 'Không rõ lý do')
        bot.reply_to(message, f"❌ Bạn đã bị cấm sử dụng bot. Lý do: `{ban_reason}`. Vui lòng liên hệ Admin để biết thêm chi tiết.", parse_mode='Markdown')
        return

    price_text = (
        "📊 **BOT LUCKYWIN XIN THÔNG BÁO BẢNG GIÁ LUCKYWIN BOT** 📊\n\n"
        "💸 **20k**: 1 Ngày\n"
        "💸 **50k**: 1 Tuần\n"
        "💸 **80k**: 2 Tuần\n"
        "💸 **130k**: 1 Tháng\n\n"
        "🤖 BOT LUCKYWIN TỈ Lệ **85-92%**\n"
        "⏱️ ĐỌC 24/24\n\n"
        "Vui Lòng ib @heheviptool hoặc @Besttaixiu999 Để Gia Hạn"
    )
    bot.reply_to(message, price_text, parse_mode='Markdown')

@bot.message_handler(commands=['gopy'])
def send_feedback(message):
    user_id_str = str(message.chat.id)
    if user_id_str in user_data and user_data[user_id_str].get('is_banned'):
        ban_reason = user_data[user_id_str].get('ban_reason', 'Không rõ lý do')
        bot.reply_to(message, f"❌ Bạn đã bị cấm sử dụng bot. Lý do: `{ban_reason}`. Vui lòng liên hệ Admin để biết thêm chi tiết.", parse_mode='Markdown')
        return

    feedback_text = telebot.util.extract_arguments(message.text)
    if not feedback_text:
        bot.reply_to(message, "Vui lòng nhập nội dung góp ý. Ví dụ: `/gopy Bot dự đoán rất chuẩn!`", parse_mode='Markdown')
        return
    
    admin_id = ADMIN_IDS[0] 
    user_name = message.from_user.username or message.from_user.first_name
    bot.send_message(admin_id, 
                     f"📢 **GÓP Ý MỚI TỪ NGƯỜI DÙNG** 📢\n\n"
                     f"**ID:** `{message.chat.id}`\n"
                     f"**Tên:** @{user_name}\n\n"
                     f"**Nội dung:**\n`{feedback_text}`",
                     parse_mode='Markdown')
    bot.reply_to(message, "Cảm ơn bạn đã gửi góp ý! Admin đã nhận được.")

@bot.message_handler(commands=['nap'])
def show_deposit_info(message):
    user_id_str = str(message.chat.id)
    if user_id_str in user_data and user_data[user_id_str].get('is_banned'):
        ban_reason = user_data[user_id_str].get('ban_reason', 'Không rõ lý do')
        bot.reply_to(message, f"❌ Bạn đã bị cấm sử dụng bot. Lý do: `{ban_reason}`. Vui lòng liên hệ Admin để biết thêm chi tiết.", parse_mode='Markdown')
        return

    user_id = message.chat.id
    deposit_text = (
        "⚜️ **NẠP TIỀN MUA LƯỢT** ⚜️\n\n"
        "Để mua lượt, vui lòng chuyển khoản đến:\n"
        "- Ngân hàng: **MB BANK**\n"
        "- Số tài khoản: **0939766383**\n"
        "- Tên chủ TK: **Nguyen Huynh Nhut Quang**\n\n"
        "**NỘI DUNG CHUYỂN KHOẢN (QUAN TRỌNG):**\n"
        "`mua luot {user_id}`\n\n"
        f"❗️ Nội dung bắt buộc của bạn là:\n"
        f"`mua luot {user_id}`\n\n"
        "(Vui lòng sao chép đúng nội dung trên để được cộng lượt tự động)\n"
        "Sau khi chuyển khoản, vui lòng chờ 1-2 phút. Nếu có sự cố, hãy dùng lệnh /support."
    )
    bot.reply_to(message, deposit_text, parse_mode='Markdown')

# Hàm trợ giúp để kích hoạt/tắt nhận dự đoán cho một game cụ thể
def toggle_game_subscription(message, game_id_to_toggle, enable: bool):
    user_id = message.chat.id
    user_id_str = str(user_id)

    if user_id_str in user_data and user_data[user_id_str].get('is_banned'):
        ban_reason = user_data[user_id_str].get('ban_reason', 'Không rõ lý do')
        bot.reply_to(message, f"❌ Bạn đã bị cấm sử dụng bot. Lý do: `{ban_reason}`. Vui lòng liên hệ Admin để biết thêm chi tiết.", parse_mode='Markdown')
        return

    game_config = GAME_CONFIGS.get(game_id_to_toggle)
    if game_config and game_config["is_maintenance"] and enable: 
        if not can_override_maintenance(user_id):
            reason = game_config["maintenance_reason"] or "không rõ"
            bot.reply_to(message, 
                         f"⚙️ Game **{game_config['name']}** đang bảo trì với lý do: `{reason}`. Bạn không thể đăng ký nhận dự đoán lúc này. Vui lòng liên hệ Admin nếu cần hỗ trợ.", 
                         parse_mode='Markdown')
            return

    is_sub, sub_message = check_subscription(user_id)
    
    if not is_sub:
        bot.reply_to(message, sub_message + "\nVui lòng liên hệ Admin @heheviptool hoặc @Besttaixiu999 để được hỗ trợ.", parse_mode='Markdown')
        return
    
    if not bot_enabled:
        bot.reply_to(message, f"❌ Bot dự đoán hiện đang tạm dừng bởi Admin. Lý do: `{bot_disable_reason}`", parse_mode='Markdown')
        return

    user_info = user_data.setdefault(user_id_str, {})
    user_info.setdefault('subscribed_games', []) 

    if enable:
        if game_id_to_toggle not in user_info['subscribed_games']:
            user_info['subscribed_games'].append(game_id_to_toggle)
            save_user_data(user_data)
            bot.reply_to(message, f"✅ Bạn đã bắt đầu nhận dự đoán cho **{GAME_CONFIGS[game_id_to_toggle]['name']}**.", parse_mode='Markdown')
        else:
            bot.reply_to(message, f"Bạn đã đang nhận dự đoán cho **{GAME_CONFIGS[game_id_to_toggle]['name']}** rồi.", parse_mode='Markdown')
    else: 
        if game_id_to_toggle in user_info['subscribed_games']:
            user_info['subscribed_games'].remove(game_id_to_toggle)
            save_user_data(user_data)
            bot.reply_to(message, f"❌ Bạn đã ngừng nhận dự đoán cho **{GAME_CONFIGS[game_id_to_toggle]['name']}**.", parse_mode='Markdown')
        else:
            bot.reply_to(message, f"Bạn không đang nhận dự đoán cho **{GAME_CONFIGS[game_id_to_toggle]['name']}**.", parse_mode='Markdown')

@bot.message_handler(commands=['dudoan'])
def start_prediction_luckywin_command(message):
    toggle_game_subscription(message, "luckywin", True)

@bot.message_handler(commands=['dudoan_hitclub'])
def start_prediction_hitclub_command(message):
    toggle_game_subscription(message, "hitclub", True)

@bot.message_handler(commands=['dudoan_sunwin'])
def start_prediction_sunwin_command(message):
    toggle_game_subscription(message, "sunwin", True)


@bot.message_handler(commands=['stop'])
def stop_predictions(message):
    user_id_str = str(message.chat.id)
    if user_id_str in user_data and user_data[user_id_str].get('is_banned'):
        ban_reason = user_data[user_id_str].get('ban_reason', 'Không rõ lý do')
        bot.reply_to(message, f"❌ Bạn đã bị cấm sử dụng bot. Lý do: `{ban_reason}`. Vui lòng liên hệ Admin để biết thêm chi tiết.", parse_mode='Markdown')
        return

    args = telebot.util.extract_arguments(message.text).split()
    
    if user_id_str not in user_data:
        bot.reply_to(message, "Bạn chưa khởi động bot. Vui lòng dùng /start trước.")
        return

    if not args: 
        user_data[user_id_str]['is_paused_prediction'] = True
        save_user_data(user_data)
        bot.reply_to(message, "⏸️ Bạn đã tạm ngừng nhận dự đoán cho **TẤT CẢ CÁC GAME** từ bot. Dùng `/continue` để tiếp tục.", parse_mode='Markdown')
    else: 
        game_id = args[0].lower()
        if game_id not in GAME_CONFIGS:
            bot.reply_to(message, "Tên game không hợp lệ. Vui lòng sử dụng `/stop` hoặc `/stop <luckywin/hitclub/sunwin>`.", parse_mode='Markdown')
            return
        
        toggle_game_subscription(message, game_id, False) 

@bot.message_handler(commands=['continue'])
def continue_predictions(message):
    user_id_str = str(message.chat.id)
    if user_id_str in user_data and user_data[user_id_str].get('is_banned'):
        ban_reason = user_data[user_id_str].get('ban_reason', 'Không rõ lý do')
        bot.reply_to(message, f"❌ Bạn đã bị cấm sử dụng bot. Lý do: `{ban_reason}`. Vui lòng liên hệ Admin để biết thêm chi tiết.", parse_mode='Markdown')
        return

    args = telebot.util.extract_arguments(message.text).split()

    if user_id_str not in user_data:
        bot.reply_to(message, "Bạn chưa khởi động bot. Vui lòng dùng /start trước.")
        return
    
    if not args: 
        if not user_data.get(user_id_str, {}).get('is_paused_prediction', False):
            bot.reply_to(message, "✅ Bạn đang nhận dự đoán rồi (chế độ chung).")
            return

        user_data[user_id_str]['is_paused_prediction'] = False
        save_user_data(user_data)
        bot.reply_to(message, "▶️ Bạn đã tiếp tục nhận dự đoán cho **TẤT CẢ CÁC GAME** từ bot.", parse_mode='Markdown')
    else: 
        game_id = args[0].lower()
        if game_id not in GAME_CONFIGS:
            bot.reply_to(message, "Tên game không hợp lệ. Vui lòng sử dụng `/continue` hoặc `/continue <luckywin/hitclub/sunwin>`.", parse_mode='Markdown')
            return

        toggle_game_subscription(message, game_id, True)


@bot.message_handler(commands=['maucau'])
def show_cau_patterns_command(message):
    if not is_admin(message.chat.id): 
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if not args or args[0].lower() not in GAME_CONFIGS:
        bot.reply_to(message, "Vui lòng chỉ định tên game (luckywin, hitclub hoặc sunwin). Ví dụ: `/maucau luckywin`", parse_mode='Markdown')
        return
    
    game_id = args[0].lower()
    game_name = GAME_CONFIGS[game_id]['name']

    manual_patterns = CAU_PATTERNS.get(game_id, {})
    dynamic_patterns = DYNAMIC_LEARNED_PATTERNS.get(game_id, {})

    pattern_text = f"📚 **CÁC MẪU CẦU CHO {game_name}** 📚\n\n"

    # Hiển thị mẫu cầu thủ công
    if manual_patterns:
        pattern_text += "**--- Mẫu cầu thủ công ---\n**"
        sorted_manual_patterns = sorted(manual_patterns.items(), key=lambda item: item[0])
        for pattern_str, info in sorted_manual_patterns:
            du_doan = info.get("du_doan", "Không rõ")
            loai_cau = info.get("loai_cau", "Không rõ")
            pattern_text += f"`{pattern_str}` => Dự đoán: {du_doan} - Loại cầu: {loai_cau}\n"
    else:
        pattern_text += "**--- Không có mẫu cầu thủ công ---\n**\n"

    # Hiển thị mẫu cầu tự học
    if dynamic_patterns:
        pattern_text += "\n**--- Mẫu cầu tự học (AI) ---\n**"
        sorted_dynamic_patterns = sorted(dynamic_patterns.items(), key=lambda item: item[0])
        for pattern_str, info in sorted_dynamic_patterns:
            du_doan_from_conf = "Tài/Xỉu (chưa rõ)" # Sẽ được suy luận khi dự đoán
            confidence = info.get("confidence", 2.5)
            # Dựa vào confidence để đưa ra dự đoán gợi ý (nhưng dự đoán thực tế sẽ qua hàm)
            if confidence >= 2.5:
                du_doan_from_conf = f"Giữ nguyên (Conf: {confidence:.1f})"
            elif confidence <= 0.5:
                du_doan_from_conf = f"Đảo chiều (Conf: {confidence:.1f})"
            else:
                du_doan_from_conf = f"Theo XN (Conf: {confidence:.1f})"

            last_updated = info.get("last_updated", "Không rõ")
            pattern_text += f"`{pattern_str}` => Dự đoán: {du_doan_from_conf} - Loại cầu: Cầu đã học - Cập nhật: {last_updated}\n"
    else:
        pattern_text += "**--- Không có mẫu cầu tự học ---\n**"

    # Chia tin nhắn nếu quá dài
    if len(pattern_text) > 4000:
        # Nếu quá dài, chia thành nhiều tin nhắn
        chunks = [pattern_text[i:i+4000] for i in range(0, len(pattern_text), 4000)]
        for chunk in chunks:
            bot.reply_to(message, chunk, parse_mode='Markdown')
    else:
        bot.reply_to(message, pattern_text, parse_mode='Markdown')


@bot.message_handler(commands=['nhapcau'])
def prompt_import_patterns(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if not args or args[0].lower() not in GAME_CONFIGS:
        bot.reply_to(message, "Vui lòng chỉ định tên game (luckywin, hitclub hoặc sunwin) để nhập cầu. Ví dụ: `/nhapcau luckywin`", parse_mode='Markdown')
        return
    
    game_id = args[0].lower()
    game_name = GAME_CONFIGS[game_id]['name']

    markup = telebot.types.ForceReply(selective=True)
    msg = bot.reply_to(message, 
                       f"Vui lòng dán văn bản chứa mẫu cầu {game_name} vào đây. Định dạng mỗi dòng:\n"
                       "`TTTTTTTTTTTTT => Dự đoán: T - Loại cầu: Cầu bệt (liên tiếp giống nhau)`\n"
                       "Bot sẽ bỏ qua những dòng không đúng định dạng hoặc có độ dài mẫu cầu không phải 13 ký tự.", 
                       reply_markup=markup, parse_mode='Markdown')
    bot.register_next_step_handler(msg, import_patterns_from_text, game_id)

def import_patterns_from_text(message, game_id):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return

    input_text = message.text
    added_patterns_count = 0
    updated_patterns_count = 0
    ignored_lines_count = 0
    
    pattern_regex = re.compile(r'^([TX]{13})\s*=>\s*Dự đoán:\s*([TX])\s*-\s*Loại cầu:\s*(.+)$')

    lines = input_text.split('\n')
    
    current_game_manual_patterns = CAU_PATTERNS.get(game_id, {})

    for line in lines:
        line = line.strip()
        if not line: # Bỏ qua dòng trống
            continue
        
        match = pattern_regex.match(line) 
        if match:
            pattern_str = match.group(1).strip()
            du_doan = match.group(2).strip()
            loai_cau = match.group(3).strip()
            
            # Đảm bảo mẫu cầu đúng độ dài (13)
            if len(pattern_str) == GAME_CONFIGS[game_id]["tx_history_length"]:
                if pattern_str not in current_game_manual_patterns:
                    added_patterns_count += 1
                else:
                    updated_patterns_count += 1
                current_game_manual_patterns[pattern_str] = {
                    "du_doan": du_doan,
                    "loai_cau": loai_cau
                }
            else:
                ignored_lines_count += 1
                print(f"DEBUG: Bỏ qua dòng '{line}' vì độ dài mẫu cầu ({len(pattern_str)}) không khớp với {GAME_CONFIGS[game_id]['tx_history_length']}.")
        else:
            ignored_lines_count += 1
            print(f"DEBUG: Bỏ qua dòng '{line}' vì không đúng định dạng regex.")
    
    CAU_PATTERNS[game_id] = current_game_manual_patterns
    save_cau_patterns()

    bot.reply_to(message, 
                 f"✅ Đã nhập mẫu cầu thủ công cho **{GAME_CONFIGS[game_id]['name']}** thành công!\n"
                 f"Đã thêm: {added_patterns_count} mẫu.\n"
                 f"Đã cập nhật: {updated_patterns_count} mẫu.\n"
                 f"Bỏ qua: {ignored_lines_count} dòng không hợp lệ/không đúng độ dài.",
                 parse_mode='Markdown')


@bot.message_handler(commands=['code'])
def use_code(message):
    user_id_str = str(message.chat.id)
    if user_id_str in user_data and user_data[user_id_str].get('is_banned'):
        ban_reason = user_data[user_id_str].get('ban_reason', 'Không rõ lý do')
        bot.reply_to(message, f"❌ Bạn đã bị cấm sử dụng bot. Lý do: `{ban_reason}`. Vui lòng liên hệ Admin để biết thêm chi tiết.", parse_mode='Markdown')
        return

    code_str = telebot.util.extract_arguments(message.text)
    user_id = str(message.chat.id)

    if not code_str:
        bot.reply_to(message, "Vui lòng nhập mã code. Ví dụ: `/code ABCXYZ`", parse_mode='Markdown')
        return
    
    if code_str not in GENERATED_CODES:
        bot.reply_to(message, "❌ Mã code không tồn tại hoặc đã hết hạn.")
        return

    code_info = GENERATED_CODES[code_str]
    if code_info.get('used_by') is not None:
        bot.reply_to(message, "❌ Mã code này đã được sử dụng rồi.")
        return

    current_expiry_str = user_data.get(user_id, {}).get('expiry_date')
    if current_expiry_str:
        current_expiry_date = datetime.strptime(current_expiry_str, '%Y-%m-%d %H:%M:%S')
        if datetime.now() > current_expiry_date:
            new_expiry_date = datetime.now()
        else:
            new_expiry_date = current_expiry_date
    else:
        new_expiry_date = datetime.now() 

    value = code_info['value']
    if code_info['type'] == 'ngày':
        new_expiry_date += timedelta(days=value)
    elif code_info['type'] == 'giờ':
        new_expiry_date += timedelta(hours=value)
    
    user_data.setdefault(user_id, {})['expiry_date'] = new_expiry_date.strftime('%Y-%m-%d %H:%M:%S')
    user_data[user_id]['username'] = message.from_user.username or message.from_user.first_name
    user_data[user_id].setdefault('is_paused_prediction', False)
    user_data[user_id].setdefault('subscribed_games', []) 
    user_data[user_id].setdefault('is_banned', False) 
    user_data[user_id].setdefault('ban_reason', None) 
    user_data[user_id].setdefault('maintenance_override', False)


    GENERATED_CODES[code_str]['used_by'] = user_id
    GENERATED_CODES[code_str]['used_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    save_user_data(user_data)
    save_codes()

    bot.reply_to(message, 
                 f"🎉 Bạn đã đổi mã code thành công! Tài khoản của bạn đã được gia hạn thêm **{value} {code_info['type']}**.\n"
                 f"Ngày hết hạn mới: `{user_expiry_date(user_id)}`", 
                 parse_mode='Markdown')

def user_expiry_date(user_id):
    if str(user_id) in user_data and user_data[str(user_id)].get('expiry_date'):
        return user_data[str(user_id)]['expiry_date']
    return "Không có"

# --- Lệnh Admin/CTV ---
@bot.message_handler(commands=['full'])
def get_user_info(message):
    if not is_ctv(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    target_user_id_str = str(message.chat.id)
    if args and args[0].isdigit():
        target_user_id_str = args[0]
    
    if target_user_id_str not in user_data:
        bot.reply_to(message, f"Không tìm thấy thông tin cho người dùng ID `{target_user_id_str}`.")
        return

    user_info = user_data[target_user_id_str]
    expiry_date_str = user_info.get('expiry_date', 'Không có')
    username = user_info.get('username', 'Không rõ')
    is_ctv_status = "Có" if is_ctv(int(target_user_id_str)) else "Không"
    is_paused_status = "Có" if user_info.get('is_paused_prediction', False) else "Không"
    subscribed_games_list = [GAME_CONFIGS[g_id]['name'] for g_id in user_info.get('subscribed_games', []) if g_id in GAME_CONFIGS]
    subscribed_games_str = ", ".join(subscribed_games_list) if subscribed_games_list else "Không có"
    is_banned_status = "Có" if user_info.get('is_banned', False) else "Không" 
    ban_reason_text = user_info.get('ban_reason', 'Không có') if user_info.get('is_banned', False) else 'N/A' 
    maintenance_override_status = "Có" if user_info.get('maintenance_override', False) else "Không"


    info_text = (
        f"**THÔNG TIN NGƯỜI DÙNG**\n"
        f"**ID:** `{target_user_id_str}`\n"
        f"**Tên:** @{username}\n"
        f"**Ngày hết hạn:** `{expiry_date_str}`\n"
        f"**Là CTV/Admin:** {is_ctv_status}\n"
        f"**Tạm ngừng dự đoán (tất cả game):** {is_paused_status}\n"
        f"**Đang nhận dự đoán game:** {subscribed_games_str}\n"
        f"**Bị cấm:** {is_banned_status}\n" 
        f"**Lý do cấm:** `{ban_reason_text}`\n" 
        f"**Quyền Override Bảo trì:** {maintenance_override_status}" 
    )
    bot.reply_to(message, info_text, parse_mode='Markdown')

@bot.message_handler(commands=['giahan'])
def extend_subscription(message):
    if not is_ctv(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if len(args) != 3 or not args[0].isdigit() or not args[1].isdigit() or args[2].lower() not in ['ngày', 'giờ']:
        bot.reply_to(message, "Cú pháp sai. Ví dụ: `/giahan <id_nguoi_dung> <số_lượng> <ngày/giờ>`\n"
                              "Ví dụ: `/giahan 12345 1 ngày` hoặc `/giahan 12345 24 giờ`", parse_mode='Markdown')
        return
    
    target_user_id_str = args[0]
    value = int(args[1])
    unit = args[2].lower() 
    
    if target_user_id_str not in user_data:
        user_data[target_user_id_str] = {
            'username': "UnknownUser",
            'expiry_date': None,
            'is_ctv': False,
            'is_paused_prediction': False,
            'subscribed_games': [],
            'is_banned': False, 
            'ban_reason': None,
            'maintenance_override': False
        }
        bot.send_message(message.chat.id, f"Đã tạo tài khoản mới cho user ID `{target_user_id_str}`.")

    current_expiry_str = user_data[target_user_id_str].get('expiry_date')
    if current_expiry_str:
        current_expiry_date = datetime.strptime(current_expiry_str, '%Y-%m-%d %H:%M:%S')
        if datetime.now() > current_expiry_date:
            new_expiry_date = datetime.now()
        else:
            new_expiry_date = current_expiry_date
    else:
        new_expiry_date = datetime.now() 

    if unit == 'ngày':
        new_expiry_date += timedelta(days=value)
    elif unit == 'giờ':
        new_expiry_date += timedelta(hours=value)
    
    user_data[target_user_id_str]['expiry_date'] = new_expiry_date.strftime('%Y-%m-%d %H:%M:%S')
    user_data[target_user_id_str]['username'] = user_data[target_user_id_str].get('username', 'UnknownUser') 
    save_user_data(user_data)
    
    bot.reply_to(message, 
                 f"Đã gia hạn thành công cho user ID `{target_user_id_str}` thêm **{value} {unit}**.\n"
                 f"Ngày hết hạn mới: `{user_data[target_user_id_str]['expiry_date']}`",
                 parse_mode='Markdown')
    
    try:
        bot.send_message(int(target_user_id_str), 
                         f"🎉 Tài khoản của bạn đã được gia hạn thêm **{value} {unit}** bởi Admin/CTV!\n"
                         f"Ngày hết hạn mới của bạn là: `{user_data[target_user_id_str]['expiry_date']}`",
                         parse_mode='Markdown')
    except telebot.apihelper.ApiTelegramException as e:
        if "bot was blocked by the user" in str(e):
            pass
        else:
            print(f"Không thể thông báo gia hạn cho user {target_user_id_str}: {e}")

# --- Lệnh Admin Chính ---
@bot.message_handler(commands=['ctv'])
def add_ctv(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if not args or not args[0].isdigit():
        bot.reply_to(message, "Cú pháp sai. Ví dụ: `/ctv <id_nguoi_dung>`", parse_mode='Markdown')
        return
    
    target_user_id_str = args[0]
    if target_user_id_str not in user_data:
        user_data[target_user_id_str] = {
            'username': "UnknownUser",
            'expiry_date': None,
            'is_ctv': True,
            'is_paused_prediction': False,
            'subscribed_games': [],
            'is_banned': False, 
            'ban_reason': None,
            'maintenance_override': False
        }
    else:
        user_data[target_user_id_str]['is_ctv'] = True
        user_data[target_user_id_str].setdefault('is_paused_prediction', False)
        user_data[target_user_id_str].setdefault('subscribed_games', [])
        user_data[target_user_id_str].setdefault('is_banned', False) 
        user_data[target_user_id_str].setdefault('ban_reason', None) 
        user_data[target_user_id_str].setdefault('maintenance_override', False)
    
    save_user_data(user_data)
    bot.reply_to(message, f"Đã cấp quyền CTV cho user ID `{target_user_id_str}`.")
    try:
        bot.send_message(int(target_user_id_str), "🎉 Bạn đã được cấp quyền CTV!")
    except Exception:
        pass

@bot.message_handler(commands=['xoactv'])
def remove_ctv(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if not args or not args[0].isdigit():
        bot.reply_to(message, "Cú pháp sai. Ví dụ: `/xoactv <id_nguoi_dung>`", parse_mode='Markdown')
        return
    
    target_user_id_str = args[0]
    if target_user_id_str in user_data:
        user_data[target_user_id_str]['is_ctv'] = False
        save_user_data(user_data)
        bot.reply_to(message, f"Đã xóa quyền CTV của user ID `{target_user_id_str}`.")
        try:
            bot.send_message(int(target_user_id_str), "❌ Quyền CTV của bạn đã bị gỡ bỏ.")
        except Exception:
            pass
    else:
        bot.reply_to(message, f"Không tìm thấy người dùng có ID `{target_user_id_str}`.")

@bot.message_handler(commands=['tb'])
def send_broadcast(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    broadcast_text = telebot.util.extract_arguments(message.text)
    if not broadcast_text:
        bot.reply_to(message, "Vui lòng nhập nội dung thông báo. Ví dụ: `/tb Bot sẽ bảo trì vào 2h sáng mai.`", parse_mode='Markdown')
        return
    
    success_count = 0
    fail_count = 0
    for user_id_str in list(user_data.keys()):
        try:
            if user_data[user_id_str].get('is_paused_prediction', False) or user_data[user_id_str].get('is_banned', False): 
                continue
            
            bot.send_message(int(user_id_str), f"📢 **THÔNG BÁO TỪ ADMIN** 📢\n\n{broadcast_text}", parse_mode='Markdown')
            success_count += 1
            time.sleep(0.1) 
        except telebot.apihelper.ApiTelegramException as e:
            fail_count += 1
            if "bot was blocked by the user" in str(e) or "user is deactivated" in str(e):
                pass
        except Exception as e:
            print(f"Lỗi không xác định khi gửi thông báo cho user {user_id_str}: {e}")
            fail_count += 1
                
    bot.reply_to(message, f"Đã gửi thông báo đến {success_count} người dùng. Thất bại: {fail_count}.")
    save_user_data(user_data) 

@bot.message_handler(commands=['tatbot'])
def disable_bot_command(message):
    global bot_enabled, bot_disable_reason, bot_disable_admin_id
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return

    reason = telebot.util.extract_arguments(message.text)
    if not reason:
        bot.reply_to(message, "Vui lòng nhập lý do tắt bot. Ví dụ: `/tatbot Bot đang bảo trì.`", parse_mode='Markdown')
        return

    bot_enabled = False
    bot_disable_reason = reason
    bot_disable_admin_id = message.chat.id
    bot.reply_to(message, f"✅ Bot dự đoán đã được tắt bởi Admin `{message.from_user.username or message.from_user.first_name}`.\nLý do: `{reason}`", parse_mode='Markdown')
    
@bot.message_handler(commands=['mokbot'])
def enable_bot_command(message):
    global bot_enabled, bot_disable_reason, bot_disable_admin_id
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return

    if bot_enabled:
        bot.reply_to(message, "Bot dự đoán đã và đang hoạt động rồi.")
        return

    bot_enabled = True
    bot_disable_reason = "Không có"
    bot_disable_admin_id = None
    bot.reply_to(message, "✅ Bot dự đoán đã được mở lại bởi Admin.")
    
@bot.message_handler(commands=['taocode'])
def generate_code_command(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if len(args) < 2 or len(args) > 3: 
        bot.reply_to(message, "Cú pháp sai. Ví dụ:\n"
                              "`/taocode <giá_trị> <ngày/giờ> <số_lượng>`\n"
                              "Ví dụ: `/taocode 1 ngày 5` (tạo 5 code 1 ngày)\n"
                              "Hoặc: `/taocode 24 giờ` (tạo 1 code 24 giờ)", parse_mode='Markdown')
        return
    
    try:
        value = int(args[0])
        unit = args[1].lower()
        quantity = int(args[2]) if len(args) == 3 else 1 
        
        if unit not in ['ngày', 'giờ']:
            bot.reply_to(message, "Đơn vị không hợp lệ. Chỉ chấp nhận `ngày` hoặc `giờ`.", parse_mode='Markdown')
            return
        if value <= 0 or quantity <= 0:
            bot.reply_to(message, "Giá trị hoặc số lượng phải lớn hơn 0.", parse_mode='Markdown')
            return

        generated_codes_list = []
        for _ in range(quantity):
            new_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8)) 
            GENERATED_CODES[new_code] = {
                "value": value,
                "type": unit,
                "used_by": None,
                "used_time": None
            }
            generated_codes_list.append(new_code)
        
        save_codes()
        
        response_text = f"✅ Đã tạo thành công {quantity} mã code gia hạn **{value} {unit}**:\n\n"
        response_text += "\n".join([f"`{code}`" for code in generated_codes_list])
        response_text += "\n\n_(Các mã này chưa được sử dụng)_"
        
        bot.reply_to(message, response_text, parse_mode='Markdown')

    except ValueError:
        bot.reply_to(message, "Giá trị hoặc số lượng không hợp lệ. Vui lòng nhập số nguyên.", parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"Đã xảy ra lỗi khi tạo code: {e}", parse_mode='Markdown')

@bot.message_handler(commands=['kiemtra'])
def check_all_users(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    if not user_data:
        bot.reply_to(message, "Chưa có người dùng nào sử dụng bot.")
        return

    response_parts = ["**DANH SÁCH NGƯỜI DÙNG**\n"]
    for user_id_str, info in user_data.items():
        username = info.get('username', 'Không rõ')
        expiry_date = info.get('expiry_date', 'Chưa có hạn')
        is_ctv_status = "CTV" if info.get('is_ctv') else ("Admin" if int(user_id_str) in ADMIN_IDS else "User")
        paused_status = "Tạm ngừng" if info.get('is_paused_prediction') else "Đang nhận"
        is_banned_status = "BỊ CẤM" if info.get('is_banned') else "Bình thường" 
        ban_reason_text = f" (Lý do: {info.get('ban_reason', 'Không rõ')})" if info.get('is_banned') else "" 
        maintenance_override_status = "Có" if info.get('maintenance_override', False) else "Không"

        subscribed_games_list = [GAME_CONFIGS[g_id]['name'] for g_id in info.get('subscribed_games', []) if g_id in GAME_CONFIGS]
        subscribed_games_str = ", ".join(subscribed_games_list) if subscribed_games_list else "Chưa đăng ký game nào"

        user_summary = (
            f"- ID: `{user_id_str}`\n"
            f"  Tên: @{username}\n"
            f"  Hạn: `{expiry_date}`\n"
            f"  Loại: {is_ctv_status}\n"
            f"  Trạng thái bot: {paused_status}, {is_banned_status}{ban_reason_text}\n" 
            f"  Game đăng ký: {subscribed_games_str}\n"
            f"  Override Bảo trì: {maintenance_override_status}\n"
        )
        response_parts.append(user_summary)
        
        if len("\n".join(response_parts)) > 3500:
            bot.send_message(message.chat.id, "\n".join(response_parts), parse_mode='Markdown')
            response_parts = ["\n(Tiếp tục)\n"] 
            time.sleep(0.5) 

    if len(response_parts) > 1: 
        bot.send_message(message.chat.id, "\n".join(response_parts), parse_mode='Markdown')
    else: 
        bot.reply_to(message, "Đã hoàn tất kiểm tra người dùng.")

@bot.message_handler(commands=['xoahan'])
def clear_expiry_date(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if not args or not args[0].isdigit():
        bot.reply_to(message, "Cú pháp sai. Ví dụ: `/xoahan <id_nguoi_dung>`", parse_mode='Markdown')
        return
    
    target_user_id_str = args[0]
    
    if target_user_id_str not in user_data:
        bot.reply_to(message, f"Không tìm thấy thông tin cho người dùng ID `{target_user_id_str}`.")
        return

    user_data[target_user_id_str]['expiry_date'] = None
    save_user_data(user_data)
    
    bot.reply_to(message, f"✅ Đã xóa số ngày còn lại của user ID `{target_user_id_str}`. Tài khoản này hiện đã hết hạn.")
    try:
        bot.send_message(int(target_user_id_str), "❌ Tài khoản của bạn đã bị Admin xóa hạn sử dụng và hiện đã hết hạn.")
    except telebot.apihelper.ApiTelegramException as e:
        if "bot was blocked by the user" in str(e):
            pass
        else:
            print(f"Không thể thông báo xóa hạn cho user {target_user_id_str}: {e}")

@bot.message_handler(commands=['ban'])
def ban_user(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if not args or not args[0].isdigit():
        bot.reply_to(message, "Cú pháp sai. Ví dụ: `/ban <id_nguoi_dung> [lý_do]`", parse_mode='Markdown')
        return
    
    target_user_id_str = args[0]
    ban_reason = " ".join(args[1:]) if len(args) > 1 else "Không có lý do cụ thể."

    if target_user_id_str not in user_data:
        user_data[target_user_id_str] = {
            'username': "UnknownUser",
            'expiry_date': None,
            'is_ctv': False,
            'is_paused_prediction': False,
            'subscribed_games': [],
            'is_banned': True,
            'ban_reason': ban_reason,
            'maintenance_override': False
        }
        bot.send_message(message.chat.id, f"Đã tạo tài khoản mới và ban user ID `{target_user_id_str}`.")
    else:
        user_data[target_user_id_str]['is_banned'] = True
        user_data[target_user_id_str]['ban_reason'] = ban_reason
        user_data[target_user_id_str].setdefault('username', 'UnknownUser') 
        user_data[target_user_id_str].setdefault('expiry_date', None)
        user_data[target_user_id_str].setdefault('is_ctv', False)
        user_data[target_user_id_str].setdefault('is_paused_prediction', False)
        user_data[target_user_id_str].setdefault('subscribed_games', [])
        user_data[target_user_id_str].setdefault('maintenance_override', False)
    
    save_user_data(user_data)
    
    bot.reply_to(message, 
                 f"✅ Đã cấm user ID `{target_user_id_str}`. Lý do: `{ban_reason}`", 
                 parse_mode='Markdown')
    try:
        bot.send_message(int(target_user_id_str), 
                         f"❌ Bạn đã bị cấm sử dụng bot bởi Admin. Lý do: `{ban_reason}`. Vui lòng liên hệ Admin để biết thêm chi tiết.", 
                         parse_mode='Markdown')
    except telebot.apihelper.ApiTelegramException as e:
        if "bot was blocked by the user" in str(e) or "user is deactivated" in str(e):
            pass
        else:
            print(f"Không thể thông báo ban cho user {target_user_id_str}: {e}")

@bot.message_handler(commands=['unban'])
def unban_user(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if not args or not args[0].isdigit():
        bot.reply_to(message, "Cú pháp sai. Ví dụ: `/unban <id_nguoi_dung>`", parse_mode='Markdown')
        return
    
    target_user_id_str = args[0]
    
    if target_user_id_str not in user_data:
        bot.reply_to(message, f"Không tìm thấy thông tin cho người dùng ID `{target_user_id_str}`.")
        return

    if not user_data[target_user_id_str].get('is_banned'):
        bot.reply_to(message, f"User ID `{target_user_id_str}` hiện không bị cấm.")
        return

    user_data[target_user_id_str]['is_banned'] = False
    user_data[target_user_id_str]['ban_reason'] = None
    save_user_data(user_data)
    
    bot.reply_to(message, f"✅ Đã bỏ cấm user ID `{target_user_id_str}`.")
    try:
        bot.send_message(int(target_user_id_str), "✅ Bạn đã được Admin bỏ cấm và có thể sử dụng bot lại bình thường.")
    except telebot.apihelper.ApiTelegramException as e:
        if "bot was blocked by the user" in str(e) or "user is deactivated" in str(e):
            pass
        else:
            print(f"Không thể thông báo unban cho user {target_user_id_str}: {e}")

@bot.message_handler(commands=['baotri'])
def set_game_maintenance(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return

    args = telebot.util.extract_arguments(message.text).split()
    if len(args) < 1 or args[0].lower() not in GAME_CONFIGS:
        bot.reply_to(message, "Cú pháp sai. Ví dụ: `/baotri <tên game> [lý do]`\n"
                              "Ví dụ: `/baotri hitclub Nâng cấp hệ thống`", parse_mode='Markdown')
        return
    
    game_id = args[0].lower()
    reason = " ".join(args[1:]) if len(args) > 1 else "Đang bảo trì."
    
    GAME_CONFIGS[game_id]["is_maintenance"] = True
    GAME_CONFIGS[game_id]["maintenance_reason"] = reason
    
    bot.reply_to(message, 
                 f"⚙️ Đã đặt game **{GAME_CONFIGS[game_id]['name']}** vào trạng thái bảo trì.\n"
                 f"Lý do: `{reason}`", 
                 parse_mode='Markdown')
    
    for user_id_str, user_info in list(user_data.items()):
        if game_id in user_info.get('subscribed_games', []) and not can_override_maintenance(int(user_id_str)):
            try:
                bot.send_message(int(user_id_str), 
                                 f"⚙️ **THÔNG BÁO QUAN TRỌNG: {GAME_CONFIGS[game_id]['name']} ĐANG BẢO TRÌ** ⚙️\n"
                                 f"Game **{GAME_CONFIGS[game_id]['name']}** hiện đã được đặt vào trạng thái bảo trì.\n"
                                 f"Lý do: `{reason}`\n"
                                 f"Bạn sẽ tạm thời không nhận được dự đoán cho game này cho đến khi bảo trì hoàn tất.",
                                 parse_mode='Markdown')
            except telebot.apihelper.ApiTelegramException as e:
                if "bot was blocked by the user" in str(e) or "user is deactivated" in str(e):
                    pass
                else:
                    print(f"Lỗi gửi tin nhắn bảo trì cho user {user_id_str}: {e}")

@bot.message_handler(commands=['mobaochi'])
def unset_game_maintenance(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if len(args) < 1 or args[0].lower() not in GAME_CONFIGS:
        bot.reply_to(message, "Cú pháp sai. Ví dụ: `/mobaochi <tên game>`", parse_mode='Markdown')
        return
    
    game_id = args[0].lower()
    
    if not GAME_CONFIGS[game_id]["is_maintenance"]:
        bot.reply_to(message, f"Game **{GAME_CONFIGS[game_id]['name']}** hiện không ở trạng thái bảo trì.", parse_mode='Markdown')
        return

    GAME_CONFIGS[game_id]["is_maintenance"] = False
    GAME_CONFIGS[game_id]["maintenance_reason"] = None
    
    bot.reply_to(message, 
                 f"✅ Đã bỏ trạng thái bảo trì cho game **{GAME_CONFIGS[game_id]['name']}**.\n"
                 f"Người dùng có thể nhận dự đoán lại bình thường.", 
                 parse_mode='Markdown')
    
    for user_id_str, user_info in list(user_data.items()):
        if game_id in user_info.get('subscribed_games', []):
            try:
                bot.send_message(int(user_id_str), 
                                 f"🎉 **THÔNG BÁO: {GAME_CONFIGS[game_id]['name']} ĐÃ HẾT BẢO TRÌ** 🎉\n"
                                 f"Game **{GAME_CONFIGS[game_id]['name']}** đã hoàn tất bảo trì.\n"
                                 f"Bạn có thể tiếp tục nhận dự đoán cho game này từ bot.",
                                 parse_mode='Markdown')
            except telebot.apihelper.ApiTelegramException as e:
                if "bot was blocked by the user" in str(e) or "user is deactivated" in str(e):
                    pass
                else:
                    print(f"Lỗi gửi tin nhắn hết bảo trì cho user {user_id_str}: {e}")

@bot.message_handler(commands=['override'])
def add_maintenance_override(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if not args or not args[0].isdigit():
        bot.reply_to(message, "Cú pháp sai. Ví dụ: `/override <id_nguoi_dung>`", parse_mode='Markdown')
        return
    
    target_user_id_str = args[0]
    if target_user_id_str not in user_data:
        user_data[target_user_id_str] = {
            'username': "UnknownUser",
            'expiry_date': None,
            'is_ctv': False,
            'is_paused_prediction': False,
            'subscribed_games': [],
            'is_banned': False, 
            'ban_reason': None,
            'maintenance_override': True
        }
        bot.send_message(message.chat.id, f"Đã tạo tài khoản mới và cấp quyền override bảo trì cho user ID `{target_user_id_str}`.")
    else:
        user_data[target_user_id_str]['maintenance_override'] = True
        user_data[target_user_id_str].setdefault('username', 'UnknownUser') 
        user_data[target_user_id_str].setdefault('expiry_date', None)
        user_data[target_user_id_str].setdefault('is_ctv', False)
        user_data[target_user_id_str].setdefault('is_paused_prediction', False)
        user_data[target_user_id_str].setdefault('subscribed_games', [])
        user_data[target_user_id_str].setdefault('is_banned', False) 
        user_data[target_user_id_str].setdefault('ban_reason', None) 

    save_user_data(user_data)
    bot.reply_to(message, f"✅ Đã cấp quyền override bảo trì cho user ID `{target_user_id_str}`.")
    try:
        bot.send_message(int(target_user_id_str), "🎉 Bạn đã được cấp quyền nhận dự đoán ngay cả khi game đang bảo trì!")
    except Exception:
        pass

@bot.message_handler(commands=['unoverride'])
def remove_maintenance_override(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if not args or not args[0].isdigit():
        bot.reply_to(message, "Cú pháp sai. Ví dụ: `/unoverride <id_nguoi_dung>`", parse_mode='Markdown')
        return
    
    target_user_id_str = args[0]
    if target_user_id_str not in user_data:
        bot.reply_to(message, f"Không tìm thấy người dùng có ID `{target_user_id_str}`.")
        return
    
    if not user_data[target_user_id_str].get('maintenance_override'):
        bot.reply_to(message, f"User ID `{target_user_id_str}` hiện không có quyền override bảo trì.")
        return

    user_data[target_user_id_str]['maintenance_override'] = False
    save_user_data(user_data)
    bot.reply_to(message, f"✅ Đã xóa quyền override bảo trì của user ID `{target_user_id_str}`.")
    try:
        bot.send_message(int(target_user_id_str), "❌ Quyền nhận dự đoán khi game bảo trì của bạn đã bị gỡ bỏ.")
    except Exception:
        pass


# --- Flask Routes cho Keep-Alive ---
@app.route('/')
def home():
    return "Bot is alive and running!"

@app.route('/health')
def health_check():
    return "OK", 200

# --- Khởi tạo bot và các luồng khi Flask app khởi động ---
@app.before_request
def start_bot_threads():
    global bot_initialized
    with bot_init_lock:
        if not bot_initialized:
            print("Initializing bot and prediction threads...")
            load_user_data()
            load_cau_patterns()
            load_dynamic_learned_patterns() # Load file mẫu cầu tự học mới
            load_codes()

            prediction_thread = Thread(target=prediction_loop, args=(prediction_stop_event,))
            prediction_thread.daemon = True
            prediction_thread.start()
            print("Prediction loop thread started.")

            polling_thread = Thread(target=bot.infinity_polling, kwargs={'none_stop': True})
            polling_thread.daemon = True
            polling_thread.start()
            print("Telegram bot polling thread started.")
            
            bot_initialized = True

# --- Điểm khởi chạy chính cho Gunicorn/Render ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"Starting Flask app locally on port {port}")
    app.run(host='0.0.0.0', port=port, debug=True)

