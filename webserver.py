from flask import Flask
import threading
import os

# Import your bot
from bot_new import bot, TOKEN

app = Flask(__name__)

@app.route('/')
def health_check():
    return "Discord Bot is running!", 200

@app.route('/health')
def health():
    return {"status": "healthy", "bot": "online"}, 200

def run_bot():
    """Run the Discord bot in a separate thread"""
    bot.run(TOKEN)

if __name__ == "__main__":
    # Start bot in background thread
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.daemon = True
    bot_thread.start()
    
    # Start web server
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
