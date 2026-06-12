import socket
import threading
import os
import sys
import json
import base64
import hashlib
import time
import urllib.parse
import ssl

import database as db
import matchmaker

# Configurations
PORT = 8000
HOST = "0.0.0.0"
USE_TLS = False  # Set to True to enable SSL/TLS wrapping

# Base paths
DIR_PATH = os.path.dirname(os.path.abspath(__file__))
UPLOADS_DIR = os.path.join(DIR_PATH, "uploads")
CERT_DIR = os.path.join(DIR_PATH, "cert")

# Ensure directories exist
os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(CERT_DIR, exist_ok=True)

# State management
state_lock = threading.Lock()
online_clients = {}  # username -> socket
client_users = {}    # socket -> username
client_rooms = {}    # socket -> current_room_name
rooms_users = {}     # room_name -> set(username)

# File upload tracking: (username, filename) -> {chunk_index: data}
ongoing_uploads = {}

# Matchmaker controller stop event
matchmaker_stop = threading.Event()

# Structured Console Logging
class Logger:
    @staticmethod
    def log(level, module, message):
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        log_entry = f"[{timestamp}] [{level}] [{module}] {message}"
        
        # Write to log file
        try:
            with open(os.path.join(DIR_PATH, "server.log"), "a") as f:
                f.write(log_entry + "\n")
        except Exception:
            pass
            
        # Color Console printing (Windows support)
        # Using simple ANSI escape sequences
        color = ""
        if level == "INFO":
            color = "\033[92m"  # Green
        elif level == "WARNING":
            color = "\033[93m"  # Yellow
        elif level == "ERROR":
            color = "\033[91m"  # Red
        elif level == "DB":
            color = "\033[96m"  # Cyan
        elif level == "NET":
            color = "\033[95m"  # Magenta
            
        reset = "\033[0m"
        print(f"{color}{log_entry}{reset}")

    @staticmethod
    def info(module, msg): Logger.log("INFO", module, msg)
    @staticmethod
    def warn(module, msg): Logger.log("WARNING", module, msg)
    @staticmethod
    def error(module, msg): Logger.log("ERROR", module, msg)
    @staticmethod
    def db(module, msg): Logger.log("DB", module, msg)
    @staticmethod
    def net(module, msg): Logger.log("NET", module, msg)


# --- HTTP Static Server Helper ---
MIME_TYPES = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "application/javascript",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".txt": "text/plain"
}

def handle_http_request(client_socket, request_text):
    """Serve files under the Final Project root directory or uploads folder."""
    try:
        lines = request_text.split("\r\n")
        req_line = lines[0].split()
        if len(req_line) < 2:
            return
            
        method, raw_path = req_line[0], req_line[1]
        if method != "GET":
            send_http_error(client_socket, 405, "Method Not Allowed")
            return

        # Parse url to avoid query parameters
        parsed_url = urllib.parse.urlparse(raw_path)
        decoded_path = urllib.parse.unquote(parsed_url.path)
        
        # Sanitize path to prevent directory traversal
        rel_path = decoded_path.lstrip("/")
        if not rel_path or rel_path == "":
            rel_path = "index.html"
            
        # Check if path inside uploads or main directory
        filepath = os.path.normpath(os.path.join(DIR_PATH, rel_path))
        if not filepath.startswith(DIR_PATH):
            send_http_error(client_socket, 403, "Forbidden")
            return
            
        if not os.path.exists(filepath) or os.path.isdir(filepath):
            send_http_error(client_socket, 404, "Not Found")
            return

        # Determine MIME type
        _, ext = os.path.splitext(filepath)
        mime = MIME_TYPES.get(ext.lower(), "application/octet-stream")
        
        # Read and send file
        with open(filepath, "rb") as f:
            file_data = f.read()
            
        response = (
            f"HTTP/1.1 200 OK\r\n"
            f"Content-Type: {mime}\r\n"
            f"Content-Length: {len(file_data)}\r\n"
            f"Connection: close\r\n\r\n"
        ).encode('utf-8') + file_data
        
        client_socket.sendall(response)
        Logger.net("HTTP", f"Served {rel_path} -> 200 OK")
    except Exception as e:
        Logger.error("HTTP", f"Error serving HTTP request: {e}")
        send_http_error(client_socket, 500, "Internal Server Error")
    finally:
        client_socket.close()

def send_http_error(sock, code, status):
    body = f"<html><body><h1>{code} {status}</h1></body></html>".encode('utf-8')
    response = (
        f"HTTP/1.1 {code} {status}\r\n"
        f"Content-Type: text/html\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n\r\n"
    ).encode('utf-8') + body
    try:
        sock.sendall(response)
    except Exception:
        pass


# --- WebSocket Protocol Implementation ---
def perform_websocket_handshake(sock, headers):
    """Handles WebSocket Upgrade handshake (RFC 6455)."""
    sec_key = None
    for line in headers:
        if line.lower().startswith("sec-websocket-key:"):
            sec_key = line.split(":", 1)[1].strip()
            break
            
    if not sec_key:
        return False
        
    # Standard WS GUID
    guid = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    sha1 = hashlib.sha1((sec_key + guid).encode('utf-8')).digest()
    accept_key = base64.b64encode(sha1).decode('utf-8')
    
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept_key}\r\n\r\n"
    )
    sock.sendall(response.encode('utf-8'))
    return True

def recv_exact(sock, n):
    """Receive exactly n bytes from the socket, blocking if necessary."""
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            return None
        data += chunk
    return data

def recv_ws_frame(sock):
    """Receive and parse a single WebSocket frame (RFC 6455)."""
    try:
        header = recv_exact(sock, 2)
        if not header:
            return None, None
            
        byte0, byte1 = header[0], header[1]
        fin = (byte0 & 0x80) != 0
        opcode = byte0 & 0x0F
        masked = (byte1 & 0x80) != 0
        payload_len = byte1 & 0x7F
        
        if payload_len == 126:
            ext_len = recv_exact(sock, 2)
            if not ext_len:
                return None, None
            payload_len = int.from_bytes(ext_len, byteorder='big')
        elif payload_len == 127:
            ext_len = recv_exact(sock, 8)
            if not ext_len:
                return None, None
            payload_len = int.from_bytes(ext_len, byteorder='big')
            
        mask_key = b""
        if masked:
            mask_key = recv_exact(sock, 4)
            if not mask_key:
                return None, None
                
        # Read payload data
        payload = recv_exact(sock, payload_len)
        if payload is None:
            return None, None
            
        if masked:
            unmasked = bytearray(payload_len)
            for i in range(payload_len):
                unmasked[i] = payload[i] ^ mask_key[i % 4]
            payload = bytes(unmasked)
            
        return opcode, payload
    except Exception:
        return None, None

def send_ws_frame(sock, opcode, payload):
    """Encode and send a WebSocket frame (RFC 6455)."""
    try:
        if isinstance(payload, str):
            payload = payload.encode('utf-8')
            
        header = bytearray()
        header.append(0x80 | opcode)  # FIN=1, Opcode
        
        payload_len = len(payload)
        if payload_len < 126:
            header.append(payload_len)
        elif payload_len < 65536:
            header.append(126)
            header.extend(payload_len.to_bytes(2, byteorder='big'))
        else:
            header.append(127)
            header.extend(payload_len.to_bytes(8, byteorder='big'))
            
        # Use a temporary send timeout to avoid blocking on slow or full client buffers
        old_timeout = sock.gettimeout()
        sock.settimeout(1.0)
        try:
            sock.sendall(header + payload)
        finally:
            sock.settimeout(old_timeout)
        return True
    except Exception as e:
        Logger.error("NET", f"Error sending frame: {e}")
        return False


# --- Broadcast / Routing Helpers ---
def send_to_user(username, data):
    """Send JSON message to specific online user."""
    with state_lock:
        sock = online_clients.get(username)
    if sock:
        return send_ws_frame(sock, 1, json.dumps(data))
    return False

def broadcast_to_room(room_name, data):
    """Broadcast JSON message to all online users in a room."""
    with state_lock:
        members = rooms_users.get(room_name, set())
        sockets = [online_clients.get(m) for m in members if m in online_clients]
    
    payload_str = json.dumps(data)
    for sock in sockets:
        if sock:
            send_ws_frame(sock, 1, payload_str)

def broadcast_global(data):
    """Broadcast JSON message to all online users."""
    with state_lock:
        sockets = list(online_clients.values())
        
    payload_str = json.dumps(data)
    for sock in sockets:
        try:
            send_ws_frame(sock, 1, payload_str)
        except Exception:
            pass

def broadcast_online_users():
    """Send the current list of online users to all clients."""
    with state_lock:
        online_usernames = list(online_clients.keys())
        
    db_users = db.get_users_mmr()
    
    online_list = []
    for u in online_usernames:
        online_list.append({
            "username": u,
            "mmr": db_users.get(u, 1000)
        })
        
    broadcast_global({
        "type": "online_users",
        "users": online_list
    })

def broadcast_rooms():
    """Send the list of rooms to all clients."""
    rooms = db.get_rooms()
    broadcast_global({
        "type": "room_list",
        "rooms": rooms
    })


# --- Matchmaking Callback Handler ---
def on_matchmaking_complete(user_a, user_b, room_name):
    """Invoked by matchmaker thread when a match is found."""
    # Create the match room in database
    db.create_room(room_name, "matchmaker")
    
    # Broadcast room updates
    broadcast_rooms()
    
    # Send match status to user A
    send_to_user(user_a["username"], {
        "type": "matchmaking_status",
        "status": "matched",
        "room_name": room_name,
        "opponent": user_b["username"],
        "opponent_mmr": user_b["mmr"]
    })
    
    # Send match status to user B
    send_to_user(user_b["username"], {
        "type": "matchmaking_status",
        "status": "matched",
        "room_name": room_name,
        "opponent": user_a["username"],
        "opponent_mmr": user_a["mmr"]
    })


# --- Client Session Logic Thread ---
def handle_client_connection(client_socket):
    """Core socket runner. Interrogates HTTP packets then manages websocket sessions."""
    username = None
    try:
        # Read the HTTP header to detect upgrade request
        request_data = b""
        while b"\r\n\r\n" not in request_data:
            chunk = client_socket.recv(1024)
            if not chunk:
                break
            request_data += chunk
            if len(request_data) > 8192:  # Prevent buffer flooding
                break
                
        if not request_data:
            client_socket.close()
            return
            
        request_text = request_data.decode('utf-8', errors='ignore')
        lines = request_text.split("\r\n")
        
        # Check if WebSocket handshake request
        is_ws = False
        for l in lines:
            if l.lower().startswith("upgrade: websocket"):
                is_ws = True
                break
                
        if not is_ws:
            # Serve Static HTTP content
            handle_http_request(client_socket, request_text)
            return

        # Run Handshake
        if not perform_websocket_handshake(client_socket, lines):
            send_http_error(client_socket, 400, "Bad Request")
            client_socket.close()
            return
            
        Logger.net("SERVER", "WebSocket connection handshake completed.")
        
        # WebSocket main frame processing loop
        while True:
            opcode, payload = recv_ws_frame(client_socket)
            if opcode is None:
                # Connection error/close
                break
                
            if opcode == 8: # Close connection
                Logger.net("SERVER", f"Received close frame for {username or 'anonymous'}")
                break
                
            if opcode == 9: # Ping
                send_ws_frame(client_socket, 10, payload)  # Pong
                continue
                
            if opcode == 1: # Text Payload
                message_text = payload.decode('utf-8', errors='ignore')
                if len(message_text) > 500:
                    Logger.info("NET", f"Received frame (truncated): {message_text[:500]}... [total {len(message_text)} chars]")
                else:
                    Logger.info("NET", f"Received frame: {message_text}")
                try:
                    data = json.loads(message_text)
                except Exception:
                    continue
                    
                msg_type = data.get("type")
                
                # --- Actions requiring NO AUTH ---
                if msg_type == "register":
                    u, p = data.get("username", "").strip(), data.get("password", "")
                    if not u or not p:
                        send_ws_frame(client_socket, 1, json.dumps({
                            "type": "auth_response", "success": False, "message": "Username and password required."
                        }))
                        continue
                        
                    db_success = db.register_user(u, p)
                    if db_success:
                        Logger.db("DB", f"Registered user: {u}")
                        send_ws_frame(client_socket, 1, json.dumps({
                            "type": "auth_response", "success": True, "message": "Registration successful. Please login."
                        }))
                    else:
                        send_ws_frame(client_socket, 1, json.dumps({
                            "type": "auth_response", "success": False, "message": "Username already taken."
                        }))
                    continue
                    
                elif msg_type == "login":
                    u, p = data.get("username", "").strip(), data.get("password", "")
                    login_ok, mmr = db.check_login(u, p)
                    if login_ok:
                        username = u
                        # Initialize states
                        old_sock = None
                        with state_lock:
                            # Evict old connection if username already online
                            old_sock = online_clients.get(username)
                            online_clients[username] = client_socket
                            client_users[client_socket] = username
                            
                        if old_sock:
                            try:
                                send_ws_frame(old_sock, 1, json.dumps({
                                    "type": "force_logout", "message": "Logged in from another location."
                                }))
                                old_sock.close()
                            except Exception: pass
                            
                        Logger.info("AUTH", f"User logged in: {username} (MMR: {mmr})")
                        
                        # Send auth approval
                        send_ws_frame(client_socket, 1, json.dumps({
                            "type": "auth_response",
                            "success": True,
                            "username": username,
                            "mmr": mmr
                        }))
                        
                        # Send initial lists
                        send_ws_frame(client_socket, 1, json.dumps({
                            "type": "room_list",
                            "rooms": db.get_rooms()
                        }))
                        broadcast_online_users()
                    else:
                        send_ws_frame(client_socket, 1, json.dumps({
                            "type": "auth_response",
                            "success": False,
                            "message": "Invalid username or password."
                        }))
                    continue
                    
                # --- Guard All Subsequent Actions for Logged in Users ---
                if not username:
                    send_ws_frame(client_socket, 1, json.dumps({
                        "type": "error", "message": "Unauthorized. Please login."
                    }))
                    continue
                    
                if msg_type == "join_room":
                    room_name = data.get("room_name")
                    # Leave current room first
                    leave_current_room(client_socket, username)
                    
                    with state_lock:
                        client_rooms[client_socket] = room_name
                        if room_name not in rooms_users:
                            rooms_users[room_name] = set()
                        rooms_users[room_name].add(username)
                        
                    Logger.info("ROOM", f"{username} joined room: {room_name}")
                    
                    # Fetch chat history
                    history = db.get_room_history(room_name)
                    send_ws_frame(client_socket, 1, json.dumps({
                        "type": "chat_history",
                        "room_name": room_name,
                        "history": history
                    }))
                    
                    # Notify room members
                    broadcast_to_room(room_name, {
                        "type": "user_joined",
                        "username": username,
                        "room_name": room_name
                    })
                    
                elif msg_type == "leave_room":
                    leave_current_room(client_socket, username)
                    
                elif msg_type == "create_room":
                    room_name = data.get("room_name", "").strip()
                    if not room_name or len(room_name) > 30:
                        send_ws_frame(client_socket, 1, json.dumps({
                            "type": "error", "message": "Invalid room name."
                        }))
                        continue
                        
                    if db.create_room(room_name, username):
                        Logger.db("DB", f"Room '{room_name}' created by {username}")
                        broadcast_rooms()
                    else:
                        send_ws_frame(client_socket, 1, json.dumps({
                            "type": "error", "message": "Room already exists."
                        }))
                        
                elif msg_type == "send_msg":
                    room_name = data.get("room_name")
                    content = data.get("content", "").strip()
                    if not room_name or not content:
                        continue
                        
                    msg_id, timestamp = db.save_message(username, room_name, None, content, 'text')
                    broadcast_to_room(room_name, {
                        "type": "message",
                        "id": msg_id,
                        "sender": username,
                        "room_name": room_name,
                        "recipient": None,
                        "content": content,
                        "msg_type": "text",
                        "timestamp": timestamp,
                        "reactions": []
                    })
                    
                elif msg_type == "send_pm":
                    recipient = data.get("recipient")
                    content = data.get("content", "").strip()
                    if not recipient or not content:
                        continue
                        
                    msg_id, timestamp = db.save_message(username, None, recipient, content, 'text')
                    pm_payload = {
                        "type": "message",
                        "id": msg_id,
                        "sender": username,
                        "room_name": None,
                        "recipient": recipient,
                        "content": content,
                        "msg_type": "text",
                        "timestamp": timestamp,
                        "reactions": []
                    }
                    
                    # Deliver to recipient
                    delivered = send_to_user(recipient, pm_payload)
                    # Deliver back to sender to confirm sync
                    send_ws_frame(client_socket, 1, json.dumps(pm_payload))
                    
                    Logger.info("PM", f"PM {username} -> {recipient} (delivered={delivered})")
                    
                elif msg_type == "request_pm_history":
                    target_user = data.get("target_user")
                    history = db.get_pm_history(username, target_user)
                    send_ws_frame(client_socket, 1, json.dumps({
                        "type": "pm_history",
                        "target_user": target_user,
                        "history": history
                    }))
                    
                elif msg_type == "add_reaction":
                    msg_id = data.get("message_id")
                    emoji = data.get("emoji")
                    if not msg_id or not emoji:
                        continue
                        
                    updated_reactions = db.toggle_reaction(msg_id, username, emoji)
                    reaction_update = {
                        "type": "reaction_update",
                        "message_id": msg_id,
                        "reactions": updated_reactions
                    }
                    
                    # Resolve destination from DB to guarantee reliable routing
                    msg_info = db.get_message_recipient_info(msg_id)
                    if msg_info:
                        r_name = msg_info.get("room_name")
                        if r_name:
                            broadcast_to_room(r_name, reaction_update)
                        else:
                            # PM: deliver to both recipient and sender
                            send_to_user(msg_info.get("recipient"), reaction_update)
                            send_to_user(msg_info.get("sender"), reaction_update)
                        
                elif msg_type == "start_matchmaking":
                    # Fetch rating
                    mmr = db.get_user_mmr(username)
                    
                    success, size = matchmaker.join_queue(username, mmr, client_socket)
                    if success:
                        Logger.info("MATCH", f"{username} entered matchmaking queue (MMR: {mmr})")
                        send_ws_frame(client_socket, 1, json.dumps({
                            "type": "matchmaking_status",
                            "status": "queued",
                            "queue_size": size
                        }))
                        # Broadcast size to all matchmaking users
                        broadcast_matchmaking_size()
                    else:
                        send_ws_frame(client_socket, 1, json.dumps({
                            "type": "error", "message": "Already in matchmaking queue."
                        }))
                        
                elif msg_type == "cancel_matchmaking":
                    if matchmaker.leave_queue(username):
                        Logger.info("MATCH", f"{username} left matchmaking queue.")
                        send_ws_frame(client_socket, 1, json.dumps({
                            "type": "matchmaking_status",
                            "status": "cancelled"
                        }))
                        broadcast_matchmaking_size()
                        
                elif msg_type == "upload_file_chunk":
                    filename = data.get("filename")
                    chunk_idx = data.get("chunk_index")
                    total_chunks = data.get("total_chunks")
                    chunk_data_b64 = data.get("data")
                    room_name = data.get("room_name")
                    recipient = data.get("recipient")

                    if not filename or chunk_idx is None or total_chunks is None or chunk_data_b64 is None:
                        continue

                    upload_key = (username, filename)
                    with state_lock:
                        if upload_key not in ongoing_uploads:
                            ongoing_uploads[upload_key] = {}
                        ongoing_uploads[upload_key][chunk_idx] = chunk_data_b64
                        received_count = len(ongoing_uploads[upload_key])

                    # Progress based on highest contiguous chunk received, not raw count
                    progress = int((received_count / total_chunks) * 100)
                    is_complete = (
                        received_count == total_chunks and
                        all(i in ongoing_uploads[upload_key] for i in range(total_chunks))
                    )

                    # Send ACK before reassembly so client can show 100% immediately
                    send_ws_frame(client_socket, 1, json.dumps({
                        "type": "file_upload_status",
                        "success": True,
                        "filename": filename,
                        "progress": progress if not is_complete else 99  # hold at 99 until file is ready
                    }))

                    if is_complete:
                        with state_lock:
                            chunks = ongoing_uploads.pop(upload_key)

                        try:
                            file_bytes = b""
                            for idx in range(total_chunks):
                                file_bytes += base64.b64decode(chunks[idx])

                            safe_name = f"{int(time.time())}_{filename.replace(' ', '_')}"
                            out_filepath = os.path.join(UPLOADS_DIR, safe_name)
                            with open(out_filepath, "wb") as f:
                                f.write(file_bytes)

                            file_url = f"/uploads/{safe_name}"
                            Logger.info("FILE", f"File reassembled: {safe_name} ({len(file_bytes)} bytes)")

                            msg_id, timestamp = db.save_message(
                                sender=username,
                                room_name=room_name,
                                recipient=recipient,
                                content=filename,
                                msg_type='file',
                                file_path=file_url
                            )

                            msg_payload = {
                                "type": "message",
                                "id": msg_id,
                                "sender": username,
                                "room_name": room_name,
                                "recipient": recipient,
                                "content": filename,
                                "msg_type": "file",
                                "file_path": file_url,
                                "timestamp": timestamp,
                                "reactions": []
                            }

                            if room_name:
                                broadcast_to_room(room_name, msg_payload)
                            elif recipient:
                                send_to_user(recipient, msg_payload)
                                send_ws_frame(client_socket, 1, json.dumps(msg_payload))

                            # Now send the true 100% completion ACK
                            send_ws_frame(client_socket, 1, json.dumps({
                                "type": "file_upload_status",
                                "success": True,
                                "filename": filename,
                                "progress": 100
                            }))

                        except Exception as ex:
                            Logger.error("FILE", f"Failed reassembling upload: {ex}")
                            send_ws_frame(client_socket, 1, json.dumps({
                                "type": "error", "message": f"File upload failed: {ex}"
                            }))
                            
    except Exception as e:
        Logger.error("SERVER", f"Error in handler thread: {e}")
    finally:
        # Client disconnect teardown
        if username:
            Logger.info("SERVER", f"Teardown session for online user: {username}")
            leave_current_room(client_socket, username)
            matchmaker.leave_queue(username)
            
            with state_lock:
                if username in online_clients:
                    del online_clients[username]
                if client_socket in client_users:
                    del client_users[client_socket]
                # Clear ongoing uploads for this user to release memory and reset chunk state
                keys_to_remove = [k for k in ongoing_uploads if k[0] == username]
                for k in keys_to_remove:
                    ongoing_uploads.pop(k, None)
                    
            broadcast_online_users()
            broadcast_matchmaking_size()
        try:
            client_socket.close()
        except Exception:
            pass

def leave_current_room(client_socket, username):
    """Clean states when user exits room."""
    curr_room = None
    with state_lock:
        curr_room = client_rooms.pop(client_socket, None)
        if curr_room:
            if curr_room in rooms_users:
                rooms_users[curr_room].discard(username)
                if not rooms_users[curr_room]:
                    del rooms_users[curr_room]
            
    if curr_room:
        # Run actions outside lock
        Logger.info("ROOM", f"{username} left room: {curr_room}")
        broadcast_to_room(curr_room, {
            "type": "user_left",
            "username": username,
            "room_name": curr_room
        })

def broadcast_matchmaking_size():
    """Update active queue length to waiting candidates."""
    size = matchmaker.get_queue_size()
    # Find sockets waiting in matchmaking queue under lock
    with state_lock:
        sockets = [online_clients.get(u["username"]) for u in matchmaker.matchmaking_queue]
        
    payload = json.dumps({
        "type": "matchmaking_status",
        "status": "queued",
        "queue_size": size
    })
    for sock in sockets:
        if sock:
            try:
                send_ws_frame(sock, 1, payload)
            except Exception: pass


# --- Server Setup Bootstrapping ---
def start_server():
    db.db_init()
    
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server_socket.bind((HOST, PORT))
        server_socket.listen(5)
    except Exception as e:
        Logger.error("SERVER", f"Failed binding server to {HOST}:{PORT} - {e}")
        sys.exit(1)

    # Wrap in TLS if requested
    active_socket = server_socket
    if USE_TLS:
        try:
            cert_file = os.path.join(CERT_DIR, "cert.pem")
            key_file = os.path.join(CERT_DIR, "key.pem")
            
            if not os.path.exists(cert_file) or not os.path.exists(key_file):
                Logger.warn("TLS", "Certificates not found. Please run generate_certs.py first.")
                Logger.info("TLS", "Proceeding with standard TCP (Non-TLS) server fallback.")
            else:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(certfile=cert_file, keyfile=key_file)
                active_socket = context.wrap_socket(server_socket, server_side=True)
                Logger.info("TLS", "SSL/TLS wrapper established.")
        except Exception as e:
            Logger.error("TLS", f"Failed establishing SSL/TLS: {e}")
            Logger.info("TLS", "Proceeding with standard TCP (Non-TLS) server fallback.")
            active_socket = server_socket

    # Spin matchmaker thread
    matchmaker_thread = threading.Thread(
        target=matchmaker.run_matchmaker, 
        args=(on_matchmaking_complete, matchmaker_stop),
        daemon=True
    )
    matchmaker_thread.start()

    protocol_desc = "HTTPS/WSS" if USE_TLS else "HTTP/WS"
    Logger.info("SERVER", f"Server running on port {PORT} ({protocol_desc})")
    Logger.info("SERVER", f"Open in browser: http://localhost:{PORT}/" if not USE_TLS else f"Open in browser: https://localhost:{PORT}/")

    try:
        while True:
            try:
                client_sock, client_addr = active_socket.accept()
                threading.Thread(
                    target=handle_client_connection, 
                    args=(client_sock,), 
                    daemon=True
                ).start()
            except socket.error:
                break
    except KeyboardInterrupt:
        pass
    finally:
        Logger.warn("SERVER", "Shutting down server...")
        matchmaker_stop.set()
        active_socket.close()

if __name__ == "__main__":
    # Check for CLI args to override configs
    if "--tls" in sys.argv:
        USE_TLS = True
    if "--port" in sys.argv:
        try:
            idx = sys.argv.index("--port")
            PORT = int(sys.argv[idx + 1])
        except Exception:
            pass
            
    start_server()
