from flask import Flask
from threading import Thread
import os

app = Flask('')

@app.route('/')
def home():
    return "Discord bot is running!"

def run():
    # Use PORT environment variable, not hardcoded 8080
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = Thread(target=run)  # Fixed: Thread not thread
    t.start()

# Actually call the function to start the server
keep_alive()
