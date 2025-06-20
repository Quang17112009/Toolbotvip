from flask import Flask, request
import os
import threading
import time

# Import the main bot logic (assuming your bot's code is in bot.py)
# If your bot's code is in main.py, change this import accordingly
# from main import bot, prediction_loop, load_user_data, save_user_data

# For simplicity, if your main bot logic is in the same file as Flask,
# you can import directly. Otherwise, ensure your bot's core logic
# (like bot.polling, prediction_loop) can be started here.

app = Flask(__name__)

# --- Dummy Keep Alive for Render/UptimeRobot (if Flask is running) ---
# This function is not strictly necessary if Flask itself keeps the process alive
# and UptimeRobot pings the Flask app.
# However, it can be useful for internal checks or if bot.polling is in another thread.
def run_keep_alive():
    while True:
        print("Web server is alive (pinged by Render/UptimeRobot)...")
        time.sleep(300) # Log every 5 minutes

@app.route('/')
def home():
    """Basic endpoint to show the server is alive."""
    return "Bot is alive and running!"

@app.route('/health')
def health_check():
    """A dedicated health check endpoint."""
    return "OK", 200

# You might want to start your Telegram bot's polling and prediction loop here
# in a separate thread, so the Flask app doesn't block it.
# Example:
# def start_bot_and_prediction():
#     load_user_data() # Load user data once
#     # Start the prediction loop in a daemon thread
#     prediction_thread = threading.Thread(target=prediction_loop)
#     prediction_thread.daemon = True
#     prediction_thread.start()
#     print("Prediction loop started.")
#
#     # Start the Telegram bot polling
#     print("Telegram bot polling started.")
#     bot.polling(non_stop=True)
#
# # Start bot and prediction in a new thread if this is the main entry point
# if __name__ == '__main__':
#     # Start the bot and prediction loop in a separate thread
#     # This ensures the Flask app can run and respond to requests
#     bot_thread = threading.Thread(target=start_bot_and_prediction)
#     bot_thread.daemon = True
#     bot_thread.start()
#
#     # Start a dummy keep-alive thread for logging (optional, Flask is already keeping it alive)
#     # keep_alive_logger_thread = threading.Thread(target=run_keep_alive)
#     # keep_alive_logger_thread.daemon = True
#     # keep_alive_logger_thread.start()
#
#     # Run the Flask app
#     port = int(os.environ.get('PORT', 5000)) # Use PORT from environment, default to 5000
#     app.run(host='0.0.0.0', port=port)

# IMPORTANT: For Render, you typically configure Gunicorn to run your Flask app.
# You don't usually run app.run() directly in the production environment.
# Your Start Command on Render will be something like: gunicorn web_server:app
# and your bot logic (polling, prediction_loop) should be initiated when the Flask app starts.

# Placeholder for starting your bot if this Flask file is your primary entry point
# (and your main bot logic is in a different file, e.g., bot_logic.py)
# For the purpose of this example, assuming your original `main.py`
# will be split into a `bot_logic.py` and this `web_server.py`.
# If your whole bot is in `main.py`, you need to adapt how `bot.polling()` is called.
