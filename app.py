import telebot
import requests
import time
import json
import os
from datetime import datetime, timedelta
from threading import Thread

# --- Cấu hình Bot ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "7658240012:AAFAZSC7ONQ1KRGNtskAUr-Pepuv4n7KjvE") # Lấy từ biến môi trường hoặc đặt trực tiếp
ADMIN_IDS = [6915752059] # Thay thế bằng ID Admin thật của bạn
DATA_FILE = 'user_data.json'
bot = telebot.TeleBot(BOT_TOKEN)

# --- Keep Alive cho Render/UptimeRobot ---
def keep_alive():
    def run():
        while True:
            try:
                # Để giữ cho bot hoạt động trên Render, bạn cần một web server đơn giản.
                # Đây là một placeholder. Nếu bạn dùng Flask, bạn sẽ khởi chạy một app Flask
                # và bot sẽ chạy trong ngữ cảnh đó hoặc như một luồng riêng.
                # Với cấu hình hiện tại, UptimeRobot sẽ cần ping một endpoint HTTP.
                # Nếu bạn không có web server, Render có thể tắt bot khi không có traffic HTTP.
                print("Keeping bot alive...")
                time.sleep(300) # Ping mỗi 5 phút
            except Exception as e:
                print(f"Error in keep_alive thread: {e}")
                time.sleep(60) # Đợi 1 phút nếu có lỗi

    t = Thread(target=run)
    t.start()

# --- Quản lý dữ liệu người dùng và mẫu cầu ---
user_data = {}
# Mẫu cầu xấu và đẹp ban đầu (sẽ được tự động cập nhật)
CAU_XAU = set()
CAU_DEP = set()

def load_user_data():
    global user_data
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            try:
                user_data = json.load(f)
            except json.JSONDecodeError:
                user_data = {}
    else:
        user_data = {}

def save_user_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def is_admin(user_id):
    return user_id in ADMIN_IDS

def is_ctv(user_id):
    return is_admin(user_id) or (str(user_id) in user_data and user_data[str(user_id)].get('is_ctv'))

def check_subscription(user_id):
    user_id_str = str(user_id)
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

# --- Logic dự đoán Tài Xỉu (giữ nguyên) ---
def du_doan_theo_xi_ngau(dice_list):
    if not dice_list:
        return "Đợi thêm dữ liệu"
    d1, d2, d3 = dice_list[-1]
    total = d1 + d2 + d3

    result_list = []
    for d in [d1, d2, d3]:
        tmp = d + total
        if tmp in [4, 5]:
            tmp -= 4
        elif tmp >= 6:
            tmp -= 6
        result_list.append("Tài" if tmp % 2 == 0 else "Xỉu")

    return max(set(result_list), key=result_list.count)

def tinh_tai_xiu(dice):
    total = sum(dice)
    return "Tài" if total >= 11 else "Xỉu", total

# --- Cập nhật mẫu cầu động ---
def update_cau_patterns(new_cau, result):
    global CAU_DEP, CAU_XAU
    if result == "T": # Nếu kết quả thực tế là "Tài"
        CAU_DEP.add(new_cau)
        if new_cau in CAU_XAU:
            CAU_XAU.remove(new_cau)
    elif result == "X": # Nếu kết quả thực tế là "Xỉu"
        CAU_DEP.add(new_cau)
        if new_cau in CAU_XAU:
            CAU_XAU.remove(new_cau)
    else: # Nếu dự đoán sai, coi như là cầu xấu
        CAU_XAU.add(new_cau)
        if new_cau in CAU_DEP:
            CAU_DEP.remove(new_cau)
    
    # Có thể lưu mẫu cầu vào file hoặc database nếu cần duy trì giữa các lần khởi động bot
    # Ví dụ:
    # with open('cau_patterns.json', 'w') as f:
    #     json.dump({'dep': list(CAU_DEP), 'xau': list(CAU_XAU)}, f)
    print(f"Đã cập nhật mẫu cầu: Cầu đẹp: {len(CAU_DEP)}, Cầu xấu: {len(CAU_XAU)}")

def is_cau_xau(cau_str):
    return cau_str in CAU_XAU

def is_cau_dep(cau_str):
    return cau_str in CAU_DEP and cau_str not in CAU_XAU # Đảm bảo không trùng cầu xấu

# --- PHẦN CẬP NHẬT: Lấy dữ liệu từ API mới ---
def lay_du_lieu():
    try:
        response = requests.get("https://1.bot/GetNewLottery/LT_Taixiu")
        response.raise_for_status() # Báo lỗi nếu status code là lỗi HTTP
        data = response.json()
        if data.get("state") != 1:
            print(f"API trả về state không thành công: {data.get('state')}")
            return None
        return data.get("data")
    except requests.exceptions.RequestException as e:
        print(f"Lỗi khi lấy dữ liệu từ API: {e}")
        return None
    except json.JSONDecodeError:
        print("Lỗi giải mã JSON từ API. Phản hồi không phải JSON hợp lệ.")
        return None

# --- Logic chính của Bot dự đoán (chạy trong luồng riêng) ---
def prediction_loop():
    last_id = None
    tx_history = []
    
    # Tải mẫu cầu ban đầu nếu có từ file (ví dụ)
    # global CAU_DEP, CAU_XAU
    # if os.path.exists('cau_patterns.json'):
    #     with open('cau_patterns.json', 'r') as f:
    #         data = json.load(f)
    #         CAU_DEP.update(data.get('dep', []))
    #         CAU_XAU.update(data.get('xau', []))

    while True:
        data = lay_du_lieu()
        if not data:
            print("❌ Không lấy được dữ liệu từ API hoặc dữ liệu không hợp lệ.")
            time.sleep(5)
            continue

        issue_id = data.get("ID")
        expect = data.get("Expect")
        open_code = data.get("OpenCode")

        if not all([issue_id, expect, open_code]):
            print("Dữ liệu API không đầy đủ (thiếu ID, Expect, hoặc OpenCode). Bỏ qua phiên này.")
            time.sleep(5)
            continue

        if issue_id != last_id:
            try:
                dice = tuple(map(int, open_code.split(",")))
            except ValueError:
                print(f"Lỗi phân tích OpenCode: '{open_code}'. Bỏ qua phiên này.")
                last_id = issue_id # Vẫn cập nhật last_id để không lặp lại lỗi
                time.sleep(5)
                continue
            
            ket_qua_tx, tong = tinh_tai_xiu(dice)

            # Lưu lịch sử 5 phiên
            if len(tx_history) >= 5:
                tx_history.pop(0)
            tx_history.append("T" if ket_qua_tx == "Tài" else "X")

            next_expect = str(int(expect) + 1).zfill(len(expect))
            du_doan = du_doan_theo_xi_ngau([dice])

            ly_do = ""
            current_cau = ""

            if len(tx_history) < 5:
                ly_do = "AI Dự đoán theo xí ngầu (chưa đủ mẫu cầu)"
            else:
                current_cau = ''.join(tx_history)
                if is_cau_dep(current_cau):
                    ly_do = f"AI Cầu đẹp ({current_cau}) → Giữ nguyên kết quả"
                elif is_cau_xau(current_cau):
                    du_doan = "Xỉu" if du_doan == "Tài" else "Tài" # Đảo chiều
                    ly_do = f"AI Cầu xấu ({current_cau}) → Đảo chiều kết quả"
                else:
                    ly_do = f"AI Không rõ mẫu cầu ({current_cau}) → Dự đoán theo xí ngầu"
            
            # Cập nhật mẫu cầu dựa trên kết quả thực tế
            if len(tx_history) >= 5:
                if (du_doan == "Tài" and ket_qua_tx == "Tài") or (du_doan == "Xỉu" and ket_qua_tx == "Xỉu"):
                    update_cau_patterns(current_cau, ket_qua_tx[0]) # Gửi "T" hoặc "X"
                else:
                    update_cau_patterns(current_cau, "Wrong") # Nếu dự đoán sai, coi là mẫu "Wrong"

            # Gửi tin nhắn dự đoán tới tất cả người dùng có quyền truy cập
            for user_id_str, user_info in list(user_data.items()): # Dùng list() để tránh lỗi khi user_data thay đổi
                user_id = int(user_id_str)
                is_sub, sub_message = check_subscription(user_id)
                if is_sub:
                    try:
                        prediction_message = (
                            "🎮 **KẾT QUẢ PHIÊN HIỆN TẠI** 🎮\n"
                            f"Phiên: `{expect}` | Kết quả: **{ket_qua_tx}** (Tổng: **{tong}**)\n\n"
                            f"**Dự đoán cho phiên tiếp theo:**\n"
                            f"🔢 Phiên: `{next_expect}`\n"
                            f"🤖 Dự đoán: **{du_doan}**\n"
                            f"📌 Lý do: _{ly_do}_\n"
                            f"⚠️ **Hãy đặt cược sớm trước khi phiên kết thúc!**"
                        )
                        bot.send_message(user_id, prediction_message, parse_mode='Markdown')
                    except telebot.apihelper.ApiTelegramException as e:
                        if "bot was blocked by the user" in str(e) or "user is deactivated" in str(e):
                            print(f"Người dùng {user_id} đã chặn bot hoặc bị vô hiệu hóa. Có thể xóa khỏi danh sách theo dõi.")
                            # Optional: Remove user from user_data if blocked
                            # del user_data[user_id_str] 
                            # save_user_data(user_data)
                        else:
                            print(f"Lỗi gửi tin nhắn cho user {user_id}: {e}")
                    except Exception as e:
                        print(f"Lỗi không xác định khi gửi tin nhắn cho user {user_id}: {e}")

            print("-" * 50)
            print("🎮 Kết quả phiên hiện tại: {} (Tổng: {})".format(ket_qua_tx, tong))
            print("🔢 Phiên: {} → {}".format(expect, next_expect))
            print("🤖 Dự đoán: {}".format(du_doan))
            print("📌 Lý do: {}".format(ly_do))
            print("⚠️ Hãy đặt cược sớm trước khi phiên kết thúc!")
            print("-" * 50)


            last_id = issue_id

        time.sleep(5) # Đợi 5 giây trước khi kiểm tra phiên mới

# --- Xử lý lệnh Telegram ---

@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = str(message.chat.id)
    username = message.from_user.username or message.from_user.first_name
    
    if user_id not in user_data:
        user_data[user_id] = {
            'username': username,
            'expiry_date': None,
            'is_ctv': False
        }
        save_user_data(user_data)
        bot.reply_to(message, 
                     "Chào mừng bạn đến với **BOT DỰ ĐOÁN TÀI XỈU SUNWIN**!\n"
                     "Hãy dùng lệnh /help để xem danh sách các lệnh hỗ trợ.", 
                     parse_mode='Markdown')
    else:
        user_data[user_id]['username'] = username # Cập nhật username nếu có thay đổi
        save_user_data(user_data)
        bot.reply_to(message, "Bạn đã khởi động bot rồi. Dùng /help để xem các lệnh.")

@bot.message_handler(commands=['help'])
def show_help(message):
    help_text = (
        "🤖 **DANH SÁCH LỆNH HỖ TRỢ** 🤖\n\n"
        "**Lệnh người dùng:**\n"
        "🔸 `/start`: Khởi động bot và thêm bạn vào hệ thống.\n"
        "🔸 `/help`: Hiển thị danh sách các lệnh.\n"
        "🔸 `/support`: Thông tin hỗ trợ Admin.\n"
        "🔸 `/gia`: Xem bảng giá dịch vụ.\n"
        "🔸 `/gopy <nội dung>`: Gửi góp ý/báo lỗi cho Admin.\n"
        "🔸 `/nap`: Hướng dẫn nạp tiền.\n"
        "🔸 `/dudoan`: Bắt đầu nhận dự đoán từ bot.\n"
        "🔸 `/maucau`: Hiển thị các mẫu cầu bot đã thu thập (xấu/đẹp).\n\n"
    )
    
    if is_ctv(message.chat.id):
        help_text += (
            "**Lệnh Admin/CTV:**\n"
            "🔹 `/full <id>`: Xem thông tin người dùng (để trống ID để xem của bạn).\n"
            "🔹 `/giahan <id> <số ngày>`: Gia hạn tài khoản người dùng.\n\n"
        )
    
    if is_admin(message.chat.id):
        help_text += (
            "**Lệnh Admin Chính:**\n"
            "👑 `/ctv <id>`: Thêm người dùng làm CTV.\n"
            "👑 `/xoactv <id>`: Xóa người dùng khỏi CTV.\n"
            "👑 `/tb <nội dung>`: Gửi thông báo đến tất cả người dùng.\n"
        )
    
    bot.reply_to(message, help_text, parse_mode='Markdown')

@bot.message_handler(commands=['support'])
def show_support(message):
    bot.reply_to(message, 
        "Để được hỗ trợ, vui lòng liên hệ Admin:\n"
        "@heheviptool hoặc @Besttaixiu999"
    )

@bot.message_handler(commands=['gia'])
def show_price(message):
    price_text = (
        "📊 **BOT LUKYWIN XIN THÔNG BÁO BẢNG GIÁ SUN BOT** 📊\n\n"
        "💸 **20k**: 1 Ngày\n"
        "💸 **50k**: 1 Tuần\n"
        "💸 **80k**: 2 Tuần\n"
        "💸 **130k**: 1 Tháng\n\n"
        "🤖 BOT LUCK TỈ Lệ **85-92%**\n"
        "⏱️ ĐỌC 24/24\n\n"
        "Vui Lòng ib @heheviptool hoặc @Besttaixiu999 Để Gia Hạn"
    )
    bot.reply_to(message, price_text, parse_mode='Markdown')

@bot.message_handler(commands=['gopy'])
def send_feedback(message):
    feedback_text = telebot.util.extract_arguments(message.text)
    if not feedback_text:
        bot.reply_to(message, "Vui lòng nhập nội dung góp ý. Ví dụ: `/gopy Bot dự đoán rất chuẩn!`", parse_mode='Markdown')
        return
    
    admin_id = ADMIN_IDS[0] # Gửi cho Admin đầu tiên trong danh sách
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

@bot.message_handler(commands=['dudoan'])
def start_prediction_command(message):
    user_id = message.chat.id
    is_sub, sub_message = check_subscription(user_id)
    
    if not is_sub:
        bot.reply_to(message, sub_message + "\nVui lòng liên hệ Admin @heheviptool hoặc @Besttaixiu999 để được hỗ trợ.", parse_mode='Markdown')
        return
    
    bot.reply_to(message, "✅ Bạn đang có quyền truy cập. Bot sẽ tự động gửi dự đoán các phiên mới nhất tại đây.")

@bot.message_handler(commands=['maucau'])
def show_cau_patterns(message):
    if not is_ctv(message.chat.id): # Chỉ Admin/CTV mới được xem mẫu cầu chi tiết
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return

    dep_patterns = "\n".join(sorted(list(CAU_DEP))) if CAU_DEP else "Không có"
    xau_patterns = "\n".join(sorted(list(CAU_XAU))) if CAU_XAU else "Không có"

    pattern_text = (
        "📚 **CÁC MẪU CẦU ĐÃ THU THẬP** 📚\n\n"
        "**🟢 Cầu Đẹp:**\n"
        f"```\n{dep_patterns}\n```\n\n"
        "**🔴 Cầu Xấu:**\n"
        f"```\n{xau_patterns}\n```\n"
        "*(Các mẫu cầu này được bot tự động học hỏi theo thời gian.)*"
    )
    bot.reply_to(message, pattern_text, parse_mode='Markdown')

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

    info_text = (
        f"**THÔNG TIN NGƯỜI DÙNG**\n"
        f"**ID:** `{target_user_id_str}`\n"
        f"**Tên:** @{username}\n"
        f"**Ngày hết hạn:** `{expiry_date_str}`\n"
        f"**Là CTV/Admin:** {is_ctv_status}"
    )
    bot.reply_to(message, info_text, parse_mode='Markdown')

@bot.message_handler(commands=['giahan'])
def extend_subscription(message):
    if not is_ctv(message.chat.id):
        bot.reply_to(message, "Bạn không có quyền sử dụng lệnh này.")
        return
    
    args = telebot.util.extract_arguments(message.text).split()
    if len(args) != 2 or not args[0].isdigit() or not args[1].isdigit():
        bot.reply_to(message, "Cú pháp sai. Ví dụ: `/giahan <id_nguoi_dung> <số_ngày>`", parse_mode='Markdown')
        return
    
    target_user_id_str = args[0]
    days_to_add = int(args[1])
    
    if target_user_id_str not in user_data:
        user_data[target_user_id_str] = {
            'username': "UnknownUser",
            'expiry_date': None,
            'is_ctv': False
        }
        bot.send_message(message.chat.id, f"Đã tạo tài khoản mới cho user ID `{target_user_id_str}`.")

    current_expiry_str = user_data[target_user_id_str].get('expiry_date')
    if current_expiry_str:
        current_expiry_date = datetime.strptime(current_expiry_str, '%Y-%m-%d %H:%M:%S')
        if datetime.now() > current_expiry_date:
            new_expiry_date = datetime.now() + timedelta(days=days_to_add)
        else:
            new_expiry_date = current_expiry_date + timedelta(days=days_to_add)
    else:
        new_expiry_date = datetime.now() + timedelta(days=days_to_add)
    
    user_data[target_user_id_str]['expiry_date'] = new_expiry_date.strftime('%Y-%m-%d %H:%M:%S')
    save_user_data(user_data)
    
    bot.reply_to(message, 
                 f"Đã gia hạn thành công cho user ID `{target_user_id_str}` thêm **{days_to_add} ngày**.\n"
                 f"Ngày hết hạn mới: `{user_data[target_user_id_str]['expiry_date']}`",
                 parse_mode='Markdown')
    
    try:
        bot.send_message(int(target_user_id_str), 
                         f"🎉 Tài khoản của bạn đã được gia hạn thêm **{days_to_add} ngày** bởi Admin/CTV!\n"
                         f"Ngày hết hạn mới của bạn là: `{user_data[target_user_id_str]['expiry_date']}`",
                         parse_mode='Markdown')
    except telebot.apihelper.ApiTelegramException as e:
        if "bot was blocked by the user" in str(e):
            print(f"Không thể thông báo gia hạn cho user {target_user_id_str}: Người dùng đã chặn bot.")
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
            'is_ctv': True
        }
    else:
        user_data[target_user_id_str]['is_ctv'] = True
    
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
            bot.send_message(int(user_id_str), f"📢 **THÔNG BÁO TỪ ADMIN** 📢\n\n{broadcast_text}", parse_mode='Markdown')
            success_count += 1
            time.sleep(0.1) # Tránh bị rate limit
        except telebot.apihelper.ApiTelegramException as e:
            print(f"Không thể gửi thông báo cho user {user_id_str}: {e}")
            fail_count += 1
            if "bot was blocked by the user" in str(e) or "user is deactivated" in str(e):
                print(f"Người dùng {user_id_str} đã chặn bot hoặc bị vô hiệu hóa. Có thể xóa khỏi user_data.")
                # Optional: del user_data[user_id_str] 
        except Exception as e:
            print(f"Lỗi không xác định khi gửi thông báo cho user {user_id_str}: {e}")
            fail_count += 1
            
    bot.reply_to(message, f"Đã gửi thông báo đến {success_count} người dùng. Thất bại: {fail_count}.")
    save_user_data(user_data) # Lưu lại nếu có user bị xóa

# --- Khởi động Bot ---
if __name__ == "__main__":
    load_user_data()
    
    # Khởi động luồng keep_alive
    keep_alive()

    # Khởi động luồng dự đoán Tài Xỉu
    prediction_thread = Thread(target=prediction_loop)
    prediction_thread.daemon = True # Cho phép luồng kết thúc khi chương trình chính kết thúc
    prediction_thread.start()

    print("Bot đang chạy...")
    try:
        bot.polling(non_stop=True)
    except Exception as e:
        print(f"Lỗi polling bot: {e}")
        time.sleep(5) # Đợi 5 giây trước khi thử lại
