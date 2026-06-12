import time
import threading

# Matchmaking queue configuration
BASE_TOLERANCE = 50      # Initial MMR difference tolerance
EXPANSION_RATE = 10      # Tolerance expansion per second in queue
CHECK_INTERVAL = 2.0     # Check queue every 2 seconds

# Thread-safe queue storage
queue_lock = threading.Lock()
matchmaking_queue = []  # List of dicts: {"username", "mmr", "joined_at", "client_conn"}

def join_queue(username, mmr, client_conn):
    """Add a user to the matchmaking queue if not already inside."""
    with queue_lock:
        # Check if already in queue
        for user in matchmaking_queue:
            if user["username"] == username:
                return False, len(matchmaking_queue)
        
        matchmaking_queue.append({
            "username": username,
            "mmr": mmr,
            "joined_at": time.time(),
            "client_conn": client_conn
        })
        return True, len(matchmaking_queue)

def leave_queue(username):
    """Remove a user from the matchmaking queue."""
    with queue_lock:
        for user in matchmaking_queue:
            if user["username"] == username:
                matchmaking_queue.remove(user)
                return True
        return False

def is_in_queue(username):
    """Check if a user is currently matchmaking."""
    with queue_lock:
        for user in matchmaking_queue:
            if user["username"] == username:
                return True
        return False

def get_queue_size():
    with queue_lock:
        return len(matchmaking_queue)

def run_matchmaker(on_match_found_callback, stop_event):
    """
    Background matchmaking thread runner.
    Takes a callback `on_match_found_callback(user_a, user_b, room_name)`
    and a `stop_event` to gracefully exit the thread.
    """
    print("[Matchmaker] Skill-based matchmaking thread started.")
    while not stop_event.is_set():
        time.sleep(CHECK_INTERVAL)
        
        with queue_lock:
            if len(matchmaking_queue) < 2:
                continue
                
            now = time.time()
            matched_pairs = []
            
            # Identify matches
            # We will search pairs and match if they fall within their combined dynamic tolerance
            i = 0
            while i < len(matchmaking_queue):
                user_a = matchmaking_queue[i]
                elapsed_a = now - user_a["joined_at"]
                tolerance_a = BASE_TOLERANCE + (EXPANSION_RATE * elapsed_a)
                
                match_found = False
                for j in range(i + 1, len(matchmaking_queue)):
                    user_b = matchmaking_queue[j]
                    elapsed_b = now - user_b["joined_at"]
                    tolerance_b = BASE_TOLERANCE + (EXPANSION_RATE * elapsed_b)
                    
                    mmr_diff = abs(user_a["mmr"] - user_b["mmr"])
                    max_allowed_diff = max(tolerance_a, tolerance_b)
                    
                    # Match condition: MMR difference is within the larger of their two tolerances
                    if mmr_diff <= max_allowed_diff:
                        matched_pairs.append((user_a, user_b))
                        # Remove from queue (j first to preserve index of i)
                        matchmaking_queue.pop(j)
                        matchmaking_queue.pop(i)
                        match_found = True
                        break
                
                if not match_found:
                    i += 1
            
            # Fire callbacks outside of the locked queue to avoid blocking
            for user_a, user_b in matched_pairs:
                room_name = f"Match_{user_a['username']}_vs_{user_b['username']}"
                print(f"[Matchmaker] Match found! {user_a['username']} ({user_a['mmr']}) vs {user_b['username']} ({user_b['mmr']}) -> Room: {room_name}")
                
                # Run server callback to setup room, broadcast join notifications, etc.
                threading.Thread(
                    target=on_match_found_callback, 
                    args=(user_a, user_b, room_name), 
                    daemon=True
                ).start()
