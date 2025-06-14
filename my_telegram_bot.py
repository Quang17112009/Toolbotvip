import os
import telebot
from telebot import types
import json
import asyncio
import threading
import time
from datetime import datetime, timedelta
import logging
import random
from flask import Flask, request, abort

# ==============================================================================
# 1. CẤU HÌNH BAN ĐẦU & LOGGING
# ==============================================================================

# Cấu hình Logging
# Lưu ý: Log file này cũng sẽ bị reset khi bot khởi động lại vì không dùng Persistent Disk
LOG_FILE = "bot_logs.log"
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.FileHandler(LOG_FILE, encoding='utf-8'),
                        logging.StreamHandler() # Để xuất log ra console/Render logs
                    ])
logger = logging.getLogger(__name__)

# Tên các file dữ liệu (Sẽ được lưu tạm thời, KHÔNG BỀN VỮNG)
# CHÚ Ý: MỌI DỮ LIỆU SẼ BỊ MẤT KHI BOT KHỞI ĐỘNG LẠI HOẶC TRIỂN KHAI MỚI
LICHSU_FILE = "lichsucau.txt"
DUDOAN_FILE = "dudoan.txt"
AI_FILE = "ai_1-2.txt"
PATTERN_COUNT_FILE = "pattern_counter.json"
DULIEU_AI_FILE = "dulieu_ai.json"
USER_DATA_FILE = "user_data.json"

# Cấu hình Token Bot (Sẽ ưu tiên lấy từ biến môi trường)
TELEGRAM_BOT_TOKEN_HARDCODED = "" # ĐỂ TRỐNG TOKEN CỨNG NẾU DÙNG BIẾN MÔI TRƯỜNG
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN_HARDCODED)

if not TELEGRAM_BOT_TOKEN:
    logger.critical("LỖI: TELEGRAM_BOT_TOKEN chưa được cấu hình. Bot sẽ không thể khởi động.")
    exit() # Thoát nếu không có token

# Khởi tạo Bot
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, parse_mode='HTML')

# Dữ liệu toàn cục (sẽ được tải từ file hoặc khởi tạo rỗng nếu file không tồn tại/bị mất)
user_data = {}  # Lưu thông tin key và user_id/chat_id
dulieu_ai = {}  # Dữ liệu AI để phân tích
pattern_counter = {} # Đếm số lần xuất hiện của các pattern

# ==============================================================================
# 2. HÀM TIỆN ÍCH CHO FILE DỮ LIỆU (Giờ sẽ tải/lưu tạm thời)
# ==============================================================================

def load_json_data(file_path, default_value={}):
    """Tải dữ liệu từ file JSON. Sẽ trả về giá trị mặc định nếu file không tồn tại."""
    if not os.path.exists(file_path):
        logger.warning(f"File {file_path} không tồn tại. Trả về giá trị mặc định.")
        return default_value
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError:
        logger.error(f"Lỗi đọc JSON từ file: {file_path}. Trả về giá trị mặc định.")
        return default_value
    except Exception as e:
        logger.error(f"Lỗi khi tải dữ liệu từ {file_path}: {e}")
        return default_value

def save_json_data(data, file_path):
    """Lưu dữ liệu vào file JSON. Dữ liệu này sẽ mất khi bot khởi động lại."""
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        logger.info(f"Đã lưu dữ liệu vào {file_path} (tạm thời).")
    except Exception as e:
        logger.error(f"Lỗi khi lưu dữ liệu vào {file_path}: {e}")

def load_text_data(file_path):
    """Tải dữ liệu từ file text. Sẽ trả về danh sách rỗng nếu file không tồn tại."""
    if not os.path.exists(file_path):
        logger.warning(f"File {file_path} không tồn tại. Trả về danh sách rỗng.")
        return []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return [line.strip() for line in f if line.strip()]
    except Exception as e:
        logger.error(f"Lỗi khi tải dữ liệu từ {file_path}: {e}")
        return []

def save_text_data(data_list, file_path):
    """Lưu dữ liệu danh sách vào file text, mỗi phần tử một dòng. Dữ liệu này sẽ mất khi bot khởi động lại."""
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            for item in data_list:
                f.write(f"{item}\n")
        logger.info(f"Đã lưu dữ liệu vào {file_path} (tạm thời).")
    except Exception as e:
        logger.error(f"Lỗi khi lưu dữ liệu vào {file_path}: {e}")

def save_user_data():
    """Lưu dữ liệu user_data vào file."""
    save_json_data(user_data, USER_DATA_FILE)

def get_user_info_by_chat_id(chat_id):
    """Tìm thông tin key và user_info dựa trên chat_id."""
    for key_name, info in user_data.items():
        if info.get('current_chat_id') == chat_id or chat_id in info.get('assigned_chat_ids', []):
            return key_name, info
    return None, None

def get_user_info_by_key(key_name):
    """Tìm thông tin user_info dựa trên tên key."""
    return user_data.get(key_name.lower(), None)

# ==============================================================================
# 3. CHỨC NĂNG CHÍNH CỦA BOT
# ==============================================================================

async def send_telegram_message(chat_id, message_text, disable_notification=False):
    """Gửi tin nhắn đến một chat_id cụ thể."""
    try:
        await asyncio.to_thread(bot.send_message,
                                chat_id=chat_id,
                                text=message_text,
                                parse_mode='HTML',
                                disable_notification=disable_notification)
        logger.info(f"Đã gửi tin nhắn đến {chat_id} thành công.")
    except telebot.apihelper.ApiTelegramException as e:
        logger.warning(f"Lỗi Telegram API khi gửi tin nhắn tới {chat_id}: {e}")
        if "bot was blocked by the user" in str(e) or "chat not found" in str(e):
            logger.warning(f"Người dùng {chat_id} đã chặn bot hoặc chat không tồn tại. Đang hủy kích hoạt key nếu tìm thấy.")
            key_name, user_info = get_user_info_by_chat_id(chat_id)
            if user_info:
                user_info['is_receiving_predictions'] = False
                if chat_id in user_info.get('assigned_chat_ids', []):
                    user_info['assigned_chat_ids'].remove(chat_id)
                if user_info.get('current_chat_id') == chat_id:
                    user_info['current_chat_id'] = None
                save_user_data() # Lưu lại trạng thái tạm thời
                logger.info(f"Đã hủy kích hoạt key '{key_name}' cho chat_id {chat_id} do lỗi gửi tin nhắn.")
        elif "Too Many Requests" in str(e):
            logger.warning(f"Đạt giới hạn Rate Limit khi gửi tin nhắn tới {chat_id}. Thử lại sau.")
    except Exception as e:
        logger.error(f"Lỗi không xác định khi gửi tin nhắn tới {chat_id}: {e}", exc_info=True)

async def check_and_send_predictions():
    """Kiểm tra và gửi dự đoán cho các key đang hoạt động."""
    global dulieu_ai # Đảm bảo cập nhật dulieu_ai toàn cục
    # dulieu_ai sẽ được load mỗi khi bot khởi động, nhưng không được lưu giữ

    now = datetime.now()
    current_minute = now.minute

    # Logic thời gian cụ thể của bạn (ví dụ: mỗi 5 phút)
    if current_minute % 5 == 0 and current_minute != 0:
        logger.info(f"Đang kiểm tra và gửi dự đoán vào phút {current_minute}.")
        for key_name, info in list(user_data.items()): # Dùng list() để tránh lỗi khi xóa phần tử
            if info.get('is_receiving_predictions') and info.get('current_chat_id'):
                # Kiểm tra thời hạn của key nếu không phải admin
                if not info.get('is_admin'):
                    expiry_time_str = info.get('expiry_time')
                    if expiry_time_str:
                        expiry_time = datetime.fromisoformat(expiry_time_str)
                        if now < expiry_time:
                            await send_prediction_to_user(key_name, info['current_chat_id'])
                        else:
                            info['is_receiving_predictions'] = False
                            save_user_data() # Lưu lại trạng thái tạm thời
                            await send_telegram_message(info['current_chat_id'],
                                                        "⚠️ **Thông báo:**\nKey của bạn đã hết hạn. Vui lòng liên hệ Admin để gia hạn.")
                            logger.info(f"Key '{key_name}' của người dùng {info['current_chat_id']} đã hết hạn.")
                    else:
                        # Nếu là user và không có expiry_time (do không dùng disks, hoặc admin không set)
                        info['is_receiving_predictions'] = False
                        save_user_data() # Lưu lại trạng thái tạm thời
                        await send_telegram_message(info['current_chat_id'],
                                                    "⚠️ **Thông báo:**\nKey của bạn không có thông tin thời hạn hoặc đã hết hạn. Vui lòng liên hệ Admin.")
                        logger.warning(f"Key '{key_name}' của người dùng {info['current_chat_id']} không có thời hạn hoặc thông tin hết hạn bị thiếu.")
                else: # Admin luôn nhận dự đoán nếu đang bật
                    await send_prediction_to_user(key_name, info['current_chat_id'])


async def send_prediction_to_user(key_name, chat_id):
    """Tạo và gửi một tin nhắn dự đoán tới người dùng."""
    prediction_message = await create_prediction_message(key_name)
    if prediction_message:
        await send_telegram_message(chat_id, prediction_message)
        logger.info(f"Đã gửi dự đoán tới key '{key_name}' (chat_id: {chat_id}).")
    else:
        logger.warning(f"Không thể tạo tin nhắn dự đoán cho key '{key_name}'.")
        await send_telegram_message(chat_id, "⚠️ Hiện tại không thể tạo dự đoán. Vui lòng thử lại sau.", disable_notification=True)


async def create_prediction_message(key_name):
    """Tạo nội dung tin nhắn dự đoán dựa trên logic AI của bạn."""
    # Dữ liệu AI sẽ bị reset khi bot khởi động lại
    # Cần đảm bảo dulieu_ai có dữ liệu nếu bạn không dùng disks và muốn bot luôn có dự đoán
    # Ví dụ: bạn có thể khởi tạo dulieu_ai với một số dự đoán mặc định ngay trong code
    if not dulieu_ai:
        # Nếu không có dữ liệu AI từ file (do không dùng disks),
        # bạn có thể khởi tạo một số dự đoán mặc định ở đây.
        # Ví dụ:
        dulieu_ai['default'] = {'predictions': ["Tài", "Xỉu", "Tài", "Xỉu"]}
        logger.warning("Không có dữ liệu AI, sử dụng dự đoán mặc định.")

    ai_data_for_key = dulieu_ai.get(key_name, dulieu_ai.get('default', {}))

    if not ai_data_for_key or not ai_data_for_key.get('predictions'):
        return f"⚠️ Không có dữ liệu AI được cấu hình cho key '{key_name}'."

    prediction_text = random.choice(ai_data_for_key['predictions'])

    now = datetime.now()
    formatted_time = now.strftime("%H:%M:%S")

    next_open_minute = ((now.minute // 5) * 5 + 5) % 60
    next_open_hour = now.hour
    if next_open_minute == 0:
        if now.minute >= 55:
             next_open_hour = (now.hour + 1) % 24
    
    next_open_time = datetime(now.year, now.month, now.day, next_open_hour, next_open_minute, 0)
    if next_open_time < now:
        next_open_time += timedelta(minutes=5)
    
    next_open_formatted_time = next_open_time.strftime("%H:%M")

    message = (
        f"🤖 **TOOL TX PRO AI**\n"
        f"⏳ **Thời gian hiện tại:** `{formatted_time}`\n"
        f"⏰ **Phiên tới lúc:** `{next_open_formatted_time}`\n\n"
        f"📊 **Dự đoán:** `{prediction_text}`\n\n"
        f"**Chúc bạn may mắn!**\n"
        f"💡 Lưu ý: Đây chỉ là dự đoán dựa trên AI, không đảm bảo thắng 100%."
    )
    return message


# ==============================================================================
# 4. HANDLERS LỆNH TELEGRAM
# ==============================================================================

@bot.message_handler(commands=['start'])
async def start_command_handler(message):
    chat_id = message.chat.id
    key_name, user_info = get_user_info_by_chat_id(chat_id)

    if user_info:
        user_info['is_receiving_predictions'] = True
        user_info['current_chat_id'] = chat_id
        if chat_id not in user_info.get('assigned_chat_ids', []):
            user_info.setdefault('assigned_chat_ids', []).append(chat_id)
        save_user_data()
        await send_telegram_message(chat_id, "✅ **Chào mừng bạn quay lại!**\nBạn đã bắt đầu nhận dự đoán từ Bot. Sử dụng `/stop` để tạm dừng.")
        logger.info(f"Người dùng {chat_id} (key: {key_name}) đã bấm /start. Đã bật nhận dự đoán.")
    else:
        await send_telegram_message(chat_id, "🤖 **Chào mừng bạn đến với Tool TX Pro AI!**\nĐể sử dụng bot, vui lòng nhập key của bạn theo cú pháp: `/key [tên_key_của_bạn]`\n\nNếu bạn là Admin hoặc CTV của Quangdz, hãy nhập key mặc định của bạn (ví dụ: `/key quangdz`).\n\nSử dụng `/help` để xem các lệnh hỗ trợ.")
        logger.info(f"Người dùng mới {chat_id} đã bấm /start. Đang chờ key.")

@bot.message_handler(commands=['help'])
async def help_command_handler(message):
    chat_id = message.chat.id
    key_name, user_info = get_user_info_by_chat_id(chat_id)

    help_message = (
        "📚 **Hướng dẫn sử dụng Tool TX Pro AI**\n\n"
        "Các lệnh phổ biến:\n"
        "• `/start`: Bắt đầu/tiếp tục nhận dự đoán.\n"
        "• `/stop`: Tạm dừng nhận dự đoán.\n"
        "• `/key [tên_key]`: Nhập key để sử dụng bot. Ví dụ: `/key quangdz`\n"
    )

    if user_info and user_info.get('is_admin'):
        help_message += (
            "\n👑 **Lệnh Admin:**\n"
            "• `/viewkeys`: Xem danh sách tất cả các key.\n"
            "• `/addkey [tên_key] [Admin/User] [thời_hạn_giờ]`: Tạo key mới. Ví dụ: `/addkey testkey User 72` (key dùng 3 ngày).\n"
            "• `/delkey [tên_key]`: Xóa một key.\n"
            "• `/capkey [chat_id] [tên_key] [thời_hạn_giờ]`: Gán key có sẵn cho một chat_id. Ví dụ: `/capkey 123456789 testkey 24` (gán key 'testkey' cho chat_id '123456789' dùng 1 ngày).\n"
            "• `/adminkey [tên_key]`: Cấp quyền admin cho một key.\n"
            "• `/unadminkey [tên_key]`: Hủy quyền admin của một key.\n"
            "• `/statuskey [tên_key]`: Xem trạng thái chi tiết của một key.\n"
            "• `/kick [chat_id]`: Gỡ key khỏi một chat_id và hủy nhận dự đoán.\n"
            "• `/resetai`: Xóa dữ liệu AI đã học (pattern_counter và dulieu_ai).\n"
            "• `/captime [tên_key] [thời_gian_giờ]`: Gia hạn thời gian cho key. Ví dụ: `/captime testkey 24` (gia hạn thêm 24 giờ).\n"
        )
    help_message += "\nNếu có bất kỳ thắc mắc nào, vui lòng liên hệ Admin."
    await send_telegram_message(chat_id, help_message)
    logger.info(f"Người dùng {chat_id} đã yêu cầu trợ giúp.")

@bot.message_handler(commands=['key'])
async def key_command_handler(message):
    chat_id = message.chat.id
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await send_telegram_message(chat_id, "❌ **Sai cú pháp.**\nVui lòng nhập key theo cú pháp: `/key [tên_key_của_bạn]`")
        return

    input_key = args[1].strip().lower()
    user_info = get_user_info_by_key(input_key)

    if user_info:
        current_linked_chat_id = user_info.get('current_chat_id')
        if current_linked_chat_id and current_linked_chat_id != chat_id and not user_info.get('is_admin'):
            await send_telegram_message(chat_id, "⚠️ **Key này đang được sử dụng bởi một thiết bị khác.**\nVui lòng liên hệ Admin nếu bạn tin đây là lỗi.")
            logger.warning(f"Người dùng {chat_id} cố gắng sử dụng key '{input_key}' đang được dùng bởi {current_linked_chat_id}.")
            return

        expiry_time_str = user_info.get('expiry_time')
        if expiry_time_str:
            expiry_time = datetime.fromisoformat(expiry_time_str)
            if datetime.now() >= expiry_time:
                await send_telegram_message(chat_id, "⚠️ **Key của bạn đã hết hạn.**\nVui lòng liên hệ Admin để gia hạn.")
                user_info['is_receiving_predictions'] = False
                save_user_data()
                logger.info(f"Key '{input_key}' của người dùng {chat_id} đã hết hạn khi cố gắng đăng nhập.")
                return

        user_info['is_receiving_predictions'] = True
        user_info['current_chat_id'] = chat_id
        if chat_id not in user_info.get('assigned_chat_ids', []):
            user_info.setdefault('assigned_chat_ids', []).append(chat_id)
        save_user_data()
        await send_telegram_message(chat_id, "✅ **Xác thực key thành công!**\nBạn đã bắt đầu nhận dự đoán từ Bot. Sử dụng `/stop` để tạm dừng.")
        logger.info(f"Người dùng {chat_id} đã đăng nhập thành công với key: {input_key}.")
    else:
        await send_telegram_message(chat_id, "❌ **Key không hợp lệ hoặc không tồn tại.**\nVui lòng kiểm tra lại key của bạn hoặc liên hệ Admin.")
        logger.warning(f"Người dùng {chat_id} đã nhập key không hợp lệ: '{input_key}'.")

@bot.message_handler(commands=['stop'])
async def stop_command_handler(message):
    chat_id = message.chat.id
    key_name, user_info = get_user_info_by_chat_id(chat_id)

    if user_info:
        user_info['is_receiving_predictions'] = False
        save_user_data()
        await send_telegram_message(chat_id, "⏸️ **Đã tạm dừng nhận dự đoán.**\nSử dụng `/start` để tiếp tục.")
        logger.info(f"Người dùng {chat_id} (key: {key_name}) đã bấm /stop. Đã tắt nhận dự đoán.")
    else:
        await send_telegram_message(chat_id, "Bạn chưa đăng nhập bằng key nào. Không có dự đoán nào để dừng.")
        logger.info(f"Người dùng {chat_id} đã bấm /stop nhưng chưa đăng nhập.")

# ==============================================================================
# 5. LỆNH ADMIN (Chỉ xử lý khi người dùng là Admin)
# ==============================================================================

def is_admin(chat_id):
    """Kiểm tra xem người dùng có phải là admin hay không."""
    for key_name, info in user_data.items():
        if (info.get('current_chat_id') == chat_id or chat_id in info.get('assigned_chat_ids', [])) and info.get('is_admin'):
            return True
    return False

@bot.message_handler(commands=['addkey'])
async def addkey_command_handler(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        await send_telegram_message(chat_id, "🚫 **Bạn không có quyền sử dụng lệnh này.**")
        return

    args = message.text.split()
    if len(args) < 3:
        await send_telegram_message(chat_id, "❌ **Sai cú pháp.**\nSử dụng: `/addkey [tên_key] [Admin/User] [thời_hạn_giờ]`\nVí dụ: `/addkey testkey User 72` (key dùng 3 ngày)")
        return

    new_key = args[1].strip().lower()
    key_type = args[2].strip().lower()
    duration_hours = 0
    if len(args) >= 4:
        try:
            duration_hours = int(args[3])
        except ValueError:
            await send_telegram_message(chat_id, "❌ **Thời hạn phải là số nguyên (giờ).**")
            return

    if new_key in user_data:
        await send_telegram_message(chat_id, f"⚠️ Key `{new_key}` đã tồn tại. Vui lòng chọn tên key khác.")
        return

    is_admin_key = False
    if key_type == 'admin':
        is_admin_key = True
        expiry_time = None
        expiry_display = "Vĩnh viễn (Admin)"
    elif key_type == 'user':
        is_admin_key = False
        if duration_hours > 0:
            expiry_time = datetime.now() + timedelta(hours=duration_hours)
            expiry_display = expiry_time.strftime("%d-%m-%Y %H:%M:%S")
        else:
            expiry_time = None
            expiry_display = "Vĩnh viễn (Không khuyến khích cho User)"
    else:
        await send_telegram_message(chat_id, "❌ **Loại key không hợp lệ.** Vui lòng dùng `Admin` hoặc `User`.")
        return

    user_data[new_key] = {
        'is_admin': is_admin_key,
        'is_receiving_predictions': False,
        'current_chat_id': None,
        'assigned_chat_ids': [],
        'created_at': datetime.now().isoformat(),
        'expiry_time': expiry_time.isoformat() if expiry_time else None
    }
    save_user_data()
    await send_telegram_message(chat_id,
                                f"✅ **Đã thêm key mới:** `{new_key}`\n"
                                f"Loại: {'👑 Admin' if is_admin_key else '👤 User'}\n"
                                f"Thời hạn: {expiry_display}")
    logger.info(f"Admin {chat_id} đã thêm key mới: {new_key} (Type: {key_type}, Duration: {duration_hours}h).")

@bot.message_handler(commands=['delkey'])
async def delkey_command_handler(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        await send_telegram_message(chat_id, "🚫 **Bạn không có quyền sử dụng lệnh này.**")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await send_telegram_message(chat_id, "❌ **Sai cú pháp.**\nSử dụng: `/delkey [tên_key]`")
        return

    target_key = args[1].strip().lower()
    if target_key in user_data:
        if user_data[target_key].get('current_chat_id'):
            await send_telegram_message(user_data[target_key]['current_chat_id'],
                                        "⚠️ **Thông báo:**\nKey của bạn đã bị Admin gỡ bỏ. Bạn sẽ không nhận được dự đoán nữa.")
            logger.info(f"Đã thông báo cho người dùng {user_data[target_key]['current_chat_id']} về việc key '{target_key}' bị xóa.")

        del user_data[target_key]
        save_user_data()
        await send_telegram_message(chat_id, f"✅ **Đã xóa key:** `{target_key}`")
        logger.info(f"Admin {chat_id} đã xóa key: {target_key}.")
    else:
        await send_telegram_message(chat_id, f"⚠️ Key `{target_key}` không tồn tại.")

@bot.message_handler(commands=['viewkeys'])
async def viewkeys_command_handler(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        await send_telegram_message(chat_id, "🚫 **Bạn không có quyền sử dụng lệnh này.**")
        return

    if not user_data:
        await send_telegram_message(chat_id, "📋 **Hiện không có key nào trong hệ thống.**")
        return

    response = "📋 **Danh sách các Key hiện có:**\n\n"
    for key, info in user_data.items():
        status = "🟢 Đang hoạt động" if info.get('is_receiving_predictions') else "🔴 Đang dừng"
        admin_status = "👑 Admin" if info.get('is_admin') else "👤 User"
        linked_chat_id = info.get('current_chat_id', 'N/A')
        assigned_ids = ', '.join(map(str, info.get('assigned_chat_ids', []))) if info.get('assigned_chat_ids') else 'N/A'

        expiry_time_str = info.get('expiry_time')
        expiry_display = "Vĩnh viễn"
        if expiry_time_str:
            expiry_time = datetime.fromisoformat(expiry_time_str)
            if expiry_time < datetime.now():
                expiry_display = f"Đã hết hạn ({expiry_time.strftime('%d/%m %H:%M')})"
            else:
                remaining_time = expiry_time - datetime.now()
                days = remaining_time.days
                hours, remainder = divmod(remaining_time.seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                expiry_display = f"Còn {days}d {hours}h {minutes}m ({expiry_time.strftime('%d/%m %H:%M')})"

        response += (
            f"🔑 `{key}`\n"
            f"  - Loại: {admin_status}\n"
            f"  - Trạng thái: {status}\n"
            f"  - Chat ID đang dùng: `{linked_chat_id}`\n"
            f"  - Các Chat ID đã gán: `{assigned_ids}`\n"
            f"  - Hạn dùng: {expiry_display}\n\n"
        )
    await send_telegram_message(chat_id, response)
    logger.info(f"Admin {chat_id} đã xem danh sách key.")


@bot.message_handler(commands=['capkey'])
async def capkey_command_handler(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        await send_telegram_message(chat_id, "🚫 **Bạn không có quyền sử dụng lệnh này.**")
        return

    args = message.text.split()
    if len(args) < 4:
        await send_telegram_message(chat_id, "❌ **Sai cú pháp.**\nSử dụng: `/capkey [chat_id] [tên_key] [thời_hạn_giờ]`\nVí dụ: `/capkey 123456789 testkey 24`")
        return

    try:
        target_chat_id = int(args[1])
        target_key = args[2].strip().lower()
        duration_hours = int(args[3])
    except ValueError:
        await send_telegram_message(chat_id, "❌ **Chat ID hoặc thời hạn không hợp lệ.**")
        return

    user_info = get_user_info_by_key(target_key)
    if not user_info:
        await send_telegram_message(chat_id, f"⚠️ Key `{target_key}` không tồn tại. Vui lòng tạo key trước.")
        return

    user_info['is_receiving_predictions'] = True
    user_info['current_chat_id'] = target_chat_id
    if target_chat_id not in user_info.get('assigned_chat_ids', []):
        user_info.setdefault('assigned_chat_ids', []).append(target_chat_id)

    if duration_hours > 0:
        expiry_time = datetime.now() + timedelta(hours=duration_hours)
        user_info['expiry_time'] = expiry_time.isoformat()
        expiry_display = expiry_time.strftime("%d-%m-%Y %H:%M:%S")
    else:
        user_info['expiry_time'] = None
        expiry_display = "Vĩnh viễn"

    save_user_data()

    await send_telegram_message(chat_id,
                                f"✅ **Đã cấp key `{target_key}` cho chat ID:** `{target_chat_id}`\n"
                                f"Thời hạn: {expiry_display}")
    await send_telegram_message(target_chat_id,
                                f"🎉 **Chúc mừng!**\nBạn đã được Admin cấp key `{target_key}` để sử dụng Tool TX Pro AI.\n"
                                f"Key của bạn có hạn đến: {expiry_display}\n"
                                "Bot sẽ bắt đầu gửi dự đoán cho bạn. Sử dụng `/stop` để tạm dừng.")
    logger.info(f"Admin {chat_id} đã cấp key '{target_key}' cho {target_chat_id} với thời hạn {duration_hours}h.")


@bot.message_handler(commands=['adminkey'])
async def adminkey_command_handler(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        await send_telegram_message(chat_id, "🚫 **Bạn không có quyền sử dụng lệnh này.**")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await send_telegram_message(chat_id, "❌ **Sai cú pháp.**\nSử dụng: `/adminkey [tên_key]`")
        return

    target_key = args[1].strip().lower()
    user_info = get_user_info_by_key(target_key)

    if user_info:
        if user_info['is_admin']:
            await send_telegram_message(chat_id, f"⚠️ Key `{target_key}` đã là Admin.")
        else:
            user_info['is_admin'] = True
            user_info['expiry_time'] = None
            save_user_data()
            await send_telegram_message(chat_id, f"✅ **Đã cấp quyền Admin cho key:** `{target_key}`")
            logger.info(f"Admin {chat_id} đã cấp quyền admin cho key: {target_key}.")
            if user_info.get('current_chat_id'):
                await send_telegram_message(user_info['current_chat_id'], "🎉 **Bạn đã được cấp quyền Admin!**")
    else:
        await send_telegram_message(chat_id, f"⚠️ Key `{target_key}` không tồn tại.")

@bot.message_handler(commands=['unadminkey'])
async def unadminkey_command_handler(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        await send_telegram_message(chat_id, "🚫 **Bạn không có quyền sử dụng lệnh này.**")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await send_telegram_message(chat_id, "❌ **Sai cú pháp.**\nSử dụng: `/unadminkey [tên_key]`")
        return

    target_key = args[1].strip().lower()
    user_info = get_user_info_by_key(target_key)

    if user_info:
        if not user_info['is_admin']:
            await send_telegram_message(chat_id, f"⚠️ Key `{target_key}` không phải là Admin.")
        else:
            user_info['is_admin'] = False
            user_info['expiry_time'] = None
            save_user_data()
            await send_telegram_message(chat_id, f"✅ **Đã hủy quyền Admin của key:** `{target_key}`")
            logger.info(f"Admin {chat_id} đã hủy quyền admin của key: {target_key}.")
            if user_info.get('current_chat_id'):
                await send_telegram_message(user_info['current_chat_id'], "⚠️ **Quyền Admin của bạn đã bị gỡ bỏ.**")
    else:
        await send_telegram_message(chat_id, f"⚠️ Key `{target_key}` không tồn tại.")

@bot.message_handler(commands=['statuskey'])
async def statuskey_command_handler(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        await send_telegram_message(chat_id, "🚫 **Bạn không có quyền sử dụng lệnh này.**")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await send_telegram_message(chat_id, "❌ **Sai cú pháp.**\nSử dụng: `/statuskey [tên_key]`")
        return

    target_key = args[1].strip().lower()
    user_info = get_user_info_by_key(target_key)

    if user_info:
        status = "🟢 Đang hoạt động" if user_info.get('is_receiving_predictions') else "🔴 Đang dừng"
        admin_status = "👑 Admin" if user_info.get('is_admin') else "👤 User"
        linked_chat_id = user_info.get('current_chat_id', 'N/A')
        assigned_ids = ', '.join(map(str, user_info.get('assigned_chat_ids', []))) if user_info.get('assigned_chat_ids') else 'N/A'
        created_at_str = user_info.get('created_at', 'N/A')
        created_at_display = datetime.fromisoformat(created_at_str).strftime("%d-%m-%Y %H:%M:%S") if created_at_str != 'N/A' else 'N/A'

        expiry_time_str = user_info.get('expiry_time')
        expiry_display = "Vĩnh viễn"
        if expiry_time_str:
            expiry_time = datetime.fromisoformat(expiry_time_str)
            if expiry_time < datetime.now():
                expiry_display = f"Đã hết hạn ({expiry_time.strftime('%d/%m %H:%M')})"
            else:
                remaining_time = expiry_time - datetime.now()
                days = remaining_time.days
                hours, remainder = divmod(remaining_time.seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                expiry_display = f"Còn {days}d {hours}h {minutes}m ({expiry_time.strftime('%d/%m %H:%M')})"

        response = (
            f"🔍 **Thông tin Key:** `{target_key}`\n"
            f"  - Loại: {admin_status}\n"
            f"  - Trạng thái: {status}\n"
            f"  - Chat ID đang dùng: `{linked_chat_id}`\n"
            f"  - Các Chat ID đã gán: `{assigned_ids}`\n"
            f"  - Thời gian tạo: {created_at_display}\n"
            f"  - Hạn dùng: {expiry_display}\n"
        )
        await send_telegram_message(chat_id, response)
        logger.info(f"Admin {chat_id} đã xem trạng thái key: {target_key}.")
    else:
        await send_telegram_message(chat_id, f"⚠️ Key `{target_key}` không tồn tại.")


@bot.message_handler(commands=['kick'])
async def kick_command_handler(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        await send_telegram_message(chat_id, "🚫 **Bạn không có quyền sử dụng lệnh này.**")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await send_telegram_message(chat_id, "❌ **Sai cú pháp.**\nSử dụng: `/kick [chat_id]`")
        return

    try:
        target_chat_id = int(args[1])
    except ValueError:
        await send_telegram_message(chat_id, "❌ **Chat ID không hợp lệ.**")
        return

    found_key = False
    for key_name, info in list(user_data.items()):
        if info.get('current_chat_id') == target_chat_id or target_chat_id in info.get('assigned_chat_ids', []):
            info['is_receiving_predictions'] = False
            if info.get('current_chat_id') == target_chat_id:
                info['current_chat_id'] = None
            if target_chat_id in info.get('assigned_chat_ids', []):
                info['assigned_chat_ids'].remove(target_chat_id)
            save_user_data()
            await send_telegram_message(chat_id, f"✅ **Đã gỡ key của chat ID:** `{target_chat_id}` (key: `{key_name}`).")
            await send_telegram_message(target_chat_id, "⚠️ **Thông báo:**\nKey của bạn đã bị Admin gỡ bỏ khỏi thiết bị này. Bạn sẽ không nhận được dự đoán nữa.")
            logger.info(f"Admin {chat_id} đã kick chat_id {target_chat_id} (key: {key_name}).")
            found_key = True
            break

    if not found_key:
        await send_telegram_message(chat_id, f"⚠️ Không tìm thấy key nào liên kết với chat ID: `{target_chat_id}`.")


@bot.message_handler(commands=['resetai'])
async def resetai_command_handler(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        await send_telegram_message(chat_id, "🚫 **Bạn không có quyền sử dụng lệnh này.**")
        return

    global pattern_counter, dulieu_ai
    pattern_counter = {}
    dulieu_ai = {}
    save_json_data(pattern_counter, PATTERN_COUNT_FILE)
    save_json_data(dulieu_ai, DULIEU_AI_FILE)

    save_text_data([], LICHSU_FILE)
    save_text_data([], DUDOAN_FILE)
    save_text_data([], AI_FILE)

    await send_telegram_message(chat_id, "✅ **Đã reset toàn bộ dữ liệu AI và các file lịch sử/dự đoán.**")
    logger.info(f"Admin {chat_id} đã reset toàn bộ dữ liệu AI.")

@bot.message_handler(commands=['captime'])
async def captime_command_handler(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        await send_telegram_message(chat_id, "🚫 **Bạn không có quyền sử dụng lệnh này.**")
        return

    args = message.text.split()
    if len(args) < 3:
        await send_telegram_message(chat_id, "❌ **Sai cú pháp.**\nSử dụng: `/captime [tên_key] [thời_gian_giờ]`\nVí dụ: `/captime testkey 24` (gia hạn thêm 24 giờ).")
        return

    target_key = args[1].strip().lower()
    try:
        add_hours = int(args[2])
    except ValueError:
        await send_telegram_message(chat_id, "❌ **Thời gian gia hạn phải là số giờ nguyên.**")
        return

    user_info = get_user_info_by_key(target_key)
    if not user_info:
        await send_telegram_message(chat_id, f"⚠️ Key `{target_key}` không tồn tại.")
        return

    if user_info.get('is_admin'):
        await send_telegram_message(chat_id, f"⚠️ Key Admin `{target_key}` không có thời hạn, không cần gia hạn.")
        return

    current_expiry_time_str = user_info.get('expiry_time')
    if current_expiry_time_str:
        current_expiry_time = datetime.fromisoformat(current_expiry_time_str)
        if current_expiry_time < datetime.now():
            new_expiry_time = datetime.now() + timedelta(hours=add_hours)
        else:
            new_expiry_time = current_expiry_time + timedelta(hours=add_hours)
    else:
        new_expiry_time = datetime.now() + timedelta(hours=add_hours)

    user_info['expiry_time'] = new_expiry_time.isoformat()
    save_user_data()

    await send_telegram_message(chat_id,
                                f"✅ **Đã gia hạn key `{target_key}` thêm {add_hours} giờ.**\n"
                                f"Thời hạn mới: {new_expiry_time.strftime('%d-%m-%Y %H:%M:%S')}")
    if user_info.get('current_chat_id'):
        await send_telegram_message(user_info['current_chat_id'],
                                    f"🎉 **Key của bạn đã được gia hạn thêm {add_hours} giờ!**\n"
                                    f"Thời hạn mới: {new_expiry_time.strftime('%d-%m-%Y %H:%M:%S')}")
    logger.info(f"Admin {chat_id} đã gia hạn key '{target_key}' thêm {add_hours} giờ.")


# ==============================================================================
# 6. CÁC HÀM XỬ LÝ KHÁC (nếu có - ví dụ: xử lý tin nhắn không phải lệnh)
# ==============================================================================

@bot.message_handler(func=lambda message: True)
async def echo_all(message):
    chat_id = message.chat.id
    key_name, user_info = get_user_info_by_chat_id(chat_id)

    if user_info:
        if not user_info.get('is_admin'):
            await send_telegram_message(chat_id, "Tôi chỉ hiểu các lệnh bắt đầu bằng `/`. Sử dụng `/help` để xem danh sách lệnh.")
            logger.info(f"Người dùng {chat_id} (key: {key_name}) gửi tin nhắn không phải lệnh: '{message.text}'")
        else:
            pass
    else:
        await send_telegram_message(chat_id, "Bạn cần nhập key để sử dụng bot. Vui lòng nhập `/key [tên_key_của_bạn]` hoặc `/help` để biết thêm.")
        logger.info(f"Người dùng chưa xác thực {chat_id} gửi tin nhắn: '{message.text}'")


# ==============================================================================
# 7. CHẠY BOT VÀ SERVER FLASK
# ==============================================================================

app = Flask(__name__)

@app.route('/')
def index():
    return "Bot Telegram đang chạy!", 200

@app.route(f'/{TELEGRAM_BOT_TOKEN}', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        asyncio.run_coroutine_threadsafe(bot.process_new_updates([update]), loop)
        return '!', 200
    else:
        abort(403)

async def start_polling():
    """Khởi động polling của telebot trong một vòng lặp sự kiện riêng."""
    logger.info("Bắt đầu polling Telegram...")
    while True:
        try:
            bot.polling(non_stop=True, interval=0)
        except Exception as e:
            logger.error(f"Lỗi polling Telegram: {e}", exc_info=True)
            await asyncio.sleep(5)
        await asyncio.sleep(1)

async def periodic_tasks():
    """Chạy các tác vụ định kỳ như gửi dự đoán."""
    while True:
        await check_and_send_predictions()
        await asyncio.sleep(60)

def run_flask_app():
    """Chạy ứng dụng Flask trên một luồng riêng."""
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
    logger.info(f"Flask server đang lắng nghe trên cổng {port}")

async def main():
    logger.info("=== TOOL TX PRO AI V3 (CHỦ ĐỘNG) ===")

    global user_data, dulieu_ai, pattern_counter
    # Khi không dùng disks, dữ liệu sẽ không được tải bền vững.
    # Các biến này sẽ được khởi tạo rỗng mỗi lần bot khởi động.
    # Để có dữ liệu AI ban đầu, bạn có thể hardcode nó vào dulieu_ai
    # hoặc thông báo cho người dùng rằng AI đang được "huấn luyện lại".

    # VÍ DỤ: Bạn có thể thêm dữ liệu mặc định cho dulieu_ai ở đây
    # dulieu_ai['default'] = {'predictions': ["Tài", "Xỉu", "Tài", "Xỉu"]}
    # user_data['admin_key_mac_dinh'] = {'is_admin': True, 'is_receiving_predictions': False, 'current_chat_id': None, 'assigned_chat_ids': [], 'created_at': datetime.now().isoformat(), 'expiry_time': None}
    
    logger.info("Dữ liệu được khởi tạo (không bền vững).")

    flask_thread = threading.Thread(target=run_flask_app, daemon=True)
    flask_thread.start()
    logger.info("Flask server thread đã khởi chạy.")

    asyncio.create_task(periodic_tasks())
    logger.info("Vòng lặp bot chính đã được lên lịch.")

    await start_polling()

if __name__ == "__main__":
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    loop.run_until_complete(main())
