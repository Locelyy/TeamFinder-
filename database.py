import os
import sqlite3
import hashlib
import time
import threading

DB_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("CHAT_DB_PATH", os.path.join(DB_DIR, "chat_app.db"))

# Thread safety lock for SQLite on OneDrive synced Windows directories
db_lock = threading.Lock()

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=OFF;")
    conn.row_factory = sqlite3.Row
    return conn

def db_init():
    """Initialize the SQLite tables."""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Create Users table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            mmr INTEGER DEFAULT 1000,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        
        # Create Rooms table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        
        # Create Messages table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT NOT NULL,
            room_name TEXT, -- NULL for Private Messages
            recipient TEXT, -- NULL for Room Messages
            content TEXT,
            msg_type TEXT DEFAULT 'text', -- 'text' or 'file'
            file_path TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        
        # Create Reactions table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            emoji TEXT NOT NULL,
            UNIQUE(message_id, username, emoji),
            FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE
        )
        """)
        
        # Seed default rooms if none exist
        cursor.execute("SELECT COUNT(*) FROM rooms")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO rooms (name, created_by) VALUES (?, ?)", ("General", "system"))
            cursor.execute("INSERT INTO rooms (name, created_by) VALUES (?, ?)", ("Gaming", "system"))
            cursor.execute("INSERT INTO rooms (name, created_by) VALUES (?, ?)", ("College Chat", "system"))
            
        conn.commit()
        conn.close()
        print("[DB] Database initialized successfully.")

def hash_password(password: str) -> str:
    """Helper to hash passwords securely using SHA-256."""
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def register_user(username, password, mmr=1000):
    """Register a new user, returning True if successful."""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        pwd_hash = hash_password(password)
        try:
            cursor.execute("INSERT INTO users (username, password_hash, mmr) VALUES (?, ?, ?)", 
                           (username, pwd_hash, mmr))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()

def check_login(username, password):
    """Check credentials, return (success, mmr) tuple."""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        pwd_hash = hash_password(password)
        cursor.execute("SELECT password_hash, mmr FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        conn.close()
        if row and row['password_hash'] == pwd_hash:
            return True, row['mmr']
        return False, 0

def update_user_mmr(username, new_mmr):
    """Update user MMR rating."""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET mmr = ? WHERE username = ?", (new_mmr, username))
        conn.commit()
        conn.close()

def create_room(room_name, created_by):
    """Create a chat room, return True on success."""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO rooms (name, created_by) VALUES (?, ?)", (room_name, created_by))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()

def get_rooms():
    """List all available rooms."""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name, created_by FROM rooms ORDER BY name ASC")
        rooms = [{"name": r["name"], "created_by": r["created_by"]} for r in cursor.fetchall()]
        conn.close()
        return rooms

def save_message(sender, room_name, recipient, content, msg_type='text', file_path=None):
    """Save message to database, return message ID and timestamp."""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("""
            INSERT INTO messages (sender, room_name, recipient, content, msg_type, file_path, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (sender, room_name, recipient, content, msg_type, file_path, timestamp))
        msg_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return msg_id, timestamp

def get_room_history(room_name, limit=50):
    """Fetch messages and reactions for a room."""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, sender, content, msg_type, file_path, timestamp
            FROM messages 
            WHERE room_name = ? 
            ORDER BY id DESC LIMIT ?
        """, (room_name, limit))
        rows = cursor.fetchall()
        
        messages = []
        for r in reversed(rows):
            msg_id = r["id"]
            # Fetch reactions for this message
            cursor.execute("SELECT username, emoji FROM reactions WHERE message_id = ?", (msg_id,))
            reactions = [{"username": rx["username"], "emoji": rx["emoji"]} for rx in cursor.fetchall()]
            messages.append({
                "id": msg_id,
                "sender": r["sender"],
                "content": r["content"],
                "msg_type": r["msg_type"],
                "file_path": r["file_path"],
                "timestamp": r["timestamp"],
                "reactions": reactions
            })
        conn.close()
        return messages

def get_pm_history(user1, user2, limit=50):
    """Fetch private messages between user1 and user2."""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, sender, content, msg_type, file_path, timestamp
            FROM messages 
            WHERE room_name IS NULL AND (
                (sender = ? AND recipient = ?) OR 
                (sender = ? AND recipient = ?)
            )
            ORDER BY id DESC LIMIT ?
        """, (user1, user2, user2, user1, limit))
        rows = cursor.fetchall()
        
        messages = []
        for r in reversed(rows):
            msg_id = r["id"]
            cursor.execute("SELECT username, emoji FROM reactions WHERE message_id = ?", (msg_id,))
            reactions = [{"username": rx["username"], "emoji": rx["emoji"]} for rx in cursor.fetchall()]
            messages.append({
                "id": msg_id,
                "sender": r["sender"],
                "content": r["content"],
                "msg_type": r["msg_type"],
                "file_path": r["file_path"],
                "timestamp": r["timestamp"],
                "reactions": reactions
            })
        conn.close()
        return messages

def toggle_reaction(message_id, username, emoji):
    """Add or remove an emoji reaction. Returns dict of all active reactions on message."""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if this exact reaction exists
        cursor.execute("SELECT id FROM reactions WHERE message_id = ? AND username = ? AND emoji = ?", 
                       (message_id, username, emoji))
        row = cursor.fetchone()
        
        if row:
            # Remove it
            cursor.execute("DELETE FROM reactions WHERE id = ?", (row["id"],))
        else:
            # Add it
            try:
                cursor.execute("INSERT INTO reactions (message_id, username, emoji) VALUES (?, ?, ?)", 
                               (message_id, username, emoji))
            except sqlite3.IntegrityError:
                pass  # Handled duplicate gracefully
                
        conn.commit()
        
        # Get updated list
        cursor.execute("SELECT username, emoji FROM reactions WHERE message_id = ?", (message_id,))
        reactions = [{"username": r["username"], "emoji": r["emoji"]} for r in cursor.fetchall()]
        conn.close()
        return reactions

def get_users_mmr():
    """Fetch MMR for all users securely inside db_lock."""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT username, mmr FROM users")
        db_users = {r["username"]: r["mmr"] for r in cursor.fetchall()}
        conn.close()
        return db_users

def get_user_mmr(username):
    """Fetch MMR for a specific user securely inside db_lock."""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT mmr FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        conn.close()
        return row["mmr"] if row else 1000

def get_message_recipient_info(message_id):
    """Retrieve room_name, recipient, and sender for a given message ID."""
    with db_lock:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT room_name, recipient, sender FROM messages WHERE id = ?", (message_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {
                "room_name": row["room_name"],
                "recipient": row["recipient"],
                "sender": row["sender"]
            }
        return None

# Run DB initialization when run directly
if __name__ == "__main__":
    db_init()
