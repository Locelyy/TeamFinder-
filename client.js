let socket = null;
let currentUsername = "";
let currentUserMmr = 1000;

// Chat context: { type: 'room' | 'pm', target: 'RoomName' | 'Username' }
let activeChat = null;

// Left rooms and full room list tracking
let leftRooms = new Set();
let allRooms = [];

// Matchmaking state
let isMatchmaking = false;
let matchmakerTimer = null;
let matchmakerSeconds = 0;

// Elements
const authSection = document.getElementById("auth-section");
const appSection = document.getElementById("app-section");
const authForm = document.getElementById("auth-form");
const usernameInput = document.getElementById("username-input");
const passwordInput = document.getElementById("password-input");
const authSubmitBtn = document.getElementById("auth-submit-btn");
const authTitle = document.getElementById("auth-title");
const authSubtitle = document.getElementById("auth-subtitle");
const authToggleLink = document.getElementById("auth-toggle-link");
const authToggleMsg = document.getElementById("auth-toggle-msg");
const authAlert = document.getElementById("auth-alert");

const userAvatar = document.getElementById("user-avatar");
const userDisplayName = document.getElementById("user-display-name");
const userDisplayMmr = document.getElementById("user-display-mmr");
const matchmakerUserMmr = document.getElementById("matchmaker-user-mmr");

const roomListContainer = document.getElementById("room-list-container");
const onlineUsersContainer = document.getElementById("online-users-container");
const chatMessagesContainer = document.getElementById("chat-messages-container");
const activeChatTitle = document.getElementById("active-chat-title");
const activeChatStatus = document.getElementById("active-chat-status");

const chatMessageInput = document.getElementById("chat-message-input");
const sendMessageBtn = document.getElementById("send-message-btn");
const leaveRoomBtn = document.getElementById("leave-room-btn");

const matchmakingActionBtn = document.getElementById("matchmaking-action-btn");
const matchmakingStatusCard = document.getElementById("matchmaking-status-card");
const queueTimerText = matchmakingStatusCard.querySelector(".queue-timer");
const queueStatusText = document.getElementById("queue-status-text");

const matchFoundOverlay = document.getElementById("match-found-overlay");
const matchedOpponentInfo = document.getElementById("matched-opponent-info");

const createRoomModal = document.getElementById("create-room-modal");
const openCreateRoomBtn = document.getElementById("open-create-room-modal");
const closeCreateRoomBtn = document.getElementById("close-create-room-modal");
const createRoomSubmitBtn = document.getElementById("create-room-submit-btn");
const newRoomNameInput = document.getElementById("new-room-name-input");

const emojiPickerOverlay = document.getElementById("emoji-picker-overlay");
const logoutBtn = document.getElementById("logout-btn");

let activeReactionMessageId = null;
let activeReactionChatType = null; // 'room' or 'pm'

// Toggle Auth screen mode (Login vs Register)
let isRegisterMode = false;
authToggleLink.addEventListener("click", (e) => {
    e.preventDefault();
    isRegisterMode = !isRegisterMode;
    hideAlert();
    if (isRegisterMode) {
        authTitle.innerText = "Create Account";
        authSubtitle.innerText = "Register a new username and join the match queue";
        authSubmitBtn.querySelector("span").innerText = "Register";
        authToggleMsg.innerText = "Already have an account?";
        authToggleLink.innerText = "Login here";
    } else {
        authTitle.innerText = "Veloce Chat";
        authSubtitle.innerText = "Login to connect with rooms and matchmaking lobbies";
        authSubmitBtn.querySelector("span").innerText = "Login";
        authToggleMsg.innerText = "Don't have an account?";
        authToggleLink.innerText = "Register here";
    }
});

function showAlert(message, type = "error") {
    authAlert.innerText = message;
    authAlert.className = `alert-box alert-${type}`;
    authAlert.style.display = "block";
}

function hideAlert() {
    authAlert.style.display = "none";
}

// Connect to socket and login/register
authForm.addEventListener("submit", () => {
    const username = usernameInput.value.trim();
    const password = passwordInput.value;

    if (!username || !password) return;

    hideAlert();
    authSubmitBtn.disabled = true;
    authSubmitBtn.querySelector("span").innerText = isRegisterMode ? "Registering..." : "Logging in...";

    initWebSocket(() => {
        const payload = {
            type: isRegisterMode ? "register" : "login",
            username: username,
            password: password
        };
        socket.send(JSON.stringify(payload));
    });
});

// Setup WebSocket Client Connection
function initWebSocket(onOpenCallback) {
    if (socket && socket.readyState === WebSocket.OPEN) {
        if (onOpenCallback) onOpenCallback();
        return;
    }

    const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${wsProtocol}//${window.location.host}/ws`;

    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
        console.log("[WS] Connected to Server.");
        if (onOpenCallback) onOpenCallback();
    };

    socket.onmessage = (event) => {
        handleServerMessage(JSON.parse(event.data));
    };

    socket.onerror = (err) => {
        console.error("[WS] Socket error:", err);
        showAlert("Connection error. Is the server running?");
        resetAuthButton();
    };

    socket.onclose = (e) => {
        console.log("[WS] Connection closed:", e);
        handleDisconnect();
    };
}

function resetAuthButton() {
    authSubmitBtn.disabled = false;
    authSubmitBtn.querySelector("span").innerText = isRegisterMode ? "Register" : "Login";
}

function handleDisconnect() {
    socket = null;
    currentUsername = "";
    activeChat = null;
    isMatchmaking = false;
    clearInterval(matchmakerTimer);

    // Clear room tracking states
    leftRooms.clear();
    allRooms = [];

    // Switch UI panels
    appSection.style.display = "none";
    authSection.style.display = "flex";
    resetAuthButton();
    showAlert("Disconnected from server.", "error");
}

logoutBtn.addEventListener("click", () => {
    if (socket) {
        socket.close();
    }
});

// Process Incoming Protocols
function handleServerMessage(msg) {
    switch (msg.type) {
        case "auth_response":
            resetAuthButton();
            if (msg.success) {
                if (isRegisterMode) {
                    showAlert(msg.message, "success");
                    isRegisterMode = false;
                    authTitle.innerText = "Veloce Chat";
                    authSubtitle.innerText = "Login to connect with rooms and matchmaking lobbies";
                    authSubmitBtn.querySelector("span").innerText = "Login";
                    authToggleMsg.innerText = "Don't have an account?";
                    authToggleLink.innerText = "Register here";
                    passwordInput.value = "";
                } else {
                    // Login Successful
                    currentUsername = msg.username;
                    currentUserMmr = msg.mmr;

                    userDisplayName.innerText = currentUsername;
                    userAvatar.innerText = currentUsername.charAt(0).toUpperCase();
                    userDisplayMmr.innerText = `MMR: ${currentUserMmr}`;
                    matchmakerUserMmr.innerText = currentUserMmr;

                    authSection.style.display = "none";
                    appSection.style.display = "grid";

                    // Join General Room by default
                    joinRoom("General");
                }
            } else {
                showAlert(msg.message, "error");
                if (socket && !currentUsername) {
                    socket.close();
                }
            }
            break;

        case "force_logout":
            alert(msg.message);
            handleDisconnect();
            break;

        case "room_list":
            allRooms = msg.rooms;
            renderRooms(allRooms);
            break;

        case "online_users":
            renderOnlineUsers(msg.users);
            break;

        case "chat_history":
            if (activeChat && activeChat.type === "room" && activeChat.target === msg.room_name) {
                chatMessagesContainer.innerHTML = "";
                if (msg.history.length === 0) {
                    chatMessagesContainer.innerHTML = `<div class="message-system">No messages yet in #${msg.room_name}. Say hello!</div>`;
                } else {
                    msg.history.forEach(appendMessage);
                }
                scrollChatToBottom();
            }
            break;

        case "pm_history":
            if (activeChat && activeChat.type === "pm" && activeChat.target === msg.target_user) {
                chatMessagesContainer.innerHTML = "";
                if (msg.history.length === 0) {
                    chatMessagesContainer.innerHTML = `<div class="message-system">This is the start of your private conversation with ${msg.target_user}.</div>`;
                } else {
                    msg.history.forEach(appendMessage);
                }
                scrollChatToBottom();
            }
            break;

        case "message":
            // Check if this message belongs in our active conversation
            const isRelevantRoom = activeChat && activeChat.type === "room" && msg.room_name === activeChat.target;
            const isRelevantPM = activeChat && activeChat.type === "pm" && (
                (msg.sender === currentUsername && msg.recipient === activeChat.target) ||
                (msg.sender === activeChat.target && msg.recipient === currentUsername)
            );

            if (isRelevantRoom || isRelevantPM) {
                // Remove placeholder system message if empty
                const placeholder = chatMessagesContainer.querySelector(".message-system");
                if (placeholder && placeholder.innerText.includes("No messages yet") || placeholder && placeholder.innerText.includes("This is the start")) {
                    placeholder.remove();
                }
                appendMessage(msg);
                scrollChatToBottom();
            }
            break;

        case "reaction_update":
            updateMessageReactions(msg.message_id, msg.reactions);
            break;

        case "user_joined":
            if (activeChat && activeChat.type === "room" && activeChat.target === msg.room_name) {
                appendSystemMessage(`${msg.username} joined the room.`);
            }
            break;

        case "user_left":
            if (activeChat && activeChat.type === "room" && activeChat.target === msg.room_name) {
                appendSystemMessage(`${msg.username} left the room.`);
            }
            break;

        case "matchmaking_status":
            handleMatchmakingStatus(msg);
            break;

        case "error":
            alert(`Error: ${msg.message}`);
            break;
    }
}

// Sidebars & Conversational Switchers
function renderRooms(rooms) {
    roomListContainer.innerHTML = "";
    rooms.forEach(room => {
        if (leftRooms.has(room.name)) return; // Filter out rooms the user has left

        const item = document.createElement("div");
        item.className = "list-item";
        if (activeChat && activeChat.type === "room" && activeChat.target === room.name) {
            item.classList.add("active");
        }
        item.innerText = `# ${room.name}`;
        item.addEventListener("click", () => joinRoom(room.name));
        roomListContainer.appendChild(item);
    });
}

function renderOnlineUsers(users) {
    onlineUsersContainer.innerHTML = "";
    users.forEach(user => {
        if (user.username === currentUsername) return; // Don't show ourselves

        const item = document.createElement("div");
        item.className = "list-item";
        if (activeChat && activeChat.type === "pm" && activeChat.target === user.username) {
            item.classList.add("active");
        }

        const dot = document.createElement("div");
        dot.className = "online-dot";

        const nameSpan = document.createElement("span");
        nameSpan.innerText = `${user.username} (MMR: ${user.mmr})`;

        item.appendChild(dot);
        item.appendChild(nameSpan);
        item.addEventListener("click", () => startPM(user.username));
        onlineUsersContainer.appendChild(item);
    });
}

function joinRoom(roomName) {
    activeChat = { type: "room", target: roomName };
    activeChatTitle.innerText = `# ${roomName}`;
    activeChatStatus.innerText = "Public Room Channel";

    // Remove from leftRooms in case they are re-joining it
    leftRooms.delete(roomName);
    renderRooms(allRooms);

    // Show leave button
    leaveRoomBtn.style.display = "block";

    // Highlight list item
    document.querySelectorAll("#room-list-container .list-item").forEach(el => {
        el.classList.toggle("active", el.innerText === `# ${roomName}`);
    });
    document.querySelectorAll("#online-users-container .list-item").forEach(el => el.classList.remove("active"));

    // Enable inputs
    chatMessageInput.disabled = false;
    sendMessageBtn.disabled = false;
    chatMessageInput.placeholder = `Message #${roomName}`;

    socket.send(JSON.stringify({
        type: "join_room",
        room_name: roomName
    }));
}

function startPM(targetUser) {
    activeChat = { type: "pm", target: targetUser };
    activeChatTitle.innerText = `@ ${targetUser}`;
    activeChatStatus.innerText = "Private Message Session";

    // Hide leave button
    leaveRoomBtn.style.display = "none";

    // Highlight user item
    document.querySelectorAll("#online-users-container .list-item").forEach(el => {
        const text = el.querySelector("span")?.innerText || "";
        el.classList.toggle("active", text.startsWith(targetUser));
    });
    document.querySelectorAll("#room-list-container .list-item").forEach(el => el.classList.remove("active"));

    // Enable inputs
    chatMessageInput.disabled = false;
    sendMessageBtn.disabled = false;
    attachFileBtn.disabled = false;
    chatMessageInput.placeholder = `Message @${targetUser}`;

    socket.send(JSON.stringify({
        type: "request_pm_history",
        target_user: targetUser
    }));
}

function leaveRoom() {
    if (activeChat && activeChat.type === "room") {
        const roomName = activeChat.target;
        socket.send(JSON.stringify({
            type: "leave_room"
        }));

        // Add to leftRooms and immediately update sidebar
        leftRooms.add(roomName);
        renderRooms(allRooms);

        // Reset active chat state
        activeChat = null;
        activeChatTitle.innerText = "Select a room or user";
        activeChatStatus.innerText = "Join a conversation to start messaging";
        chatMessagesContainer.innerHTML = `<div class="message-system">Select a channel from the left sidebar to start typing.</div>`;

        // Disable inputs
        chatMessageInput.disabled = true;
        chatMessageInput.value = "";
        sendMessageBtn.disabled = true;
        chatMessageInput.placeholder = "Type a message...";

        // Hide button
        leaveRoomBtn.style.display = "none";
    }
}

leaveRoomBtn.addEventListener("click", leaveRoom);

// Chat UI Appenders
function appendMessage(msg) {
    const isSentByMe = msg.sender === currentUsername;

    const wrapper = document.createElement("div");
    wrapper.className = `message-wrapper ${isSentByMe ? 'sent' : 'received'}`;
    wrapper.dataset.messageId = msg.id;

    const info = document.createElement("div");
    info.className = "message-info";

    const sender = document.createElement("span");
    sender.className = "sender-name";
    sender.innerText = msg.sender;

    const timeSpan = document.createElement("span");
    timeSpan.innerText = msg.timestamp.split(" ")[1] || msg.timestamp; // HH:MM:SS format

    info.appendChild(sender);
    info.appendChild(timeSpan);

    const msgBox = document.createElement("div");
    msgBox.className = "message-box";

    if (msg.msg_type === "file") {
        const lowerName = msg.content.toLowerCase();
        const isImage = /\.(jpg|jpeg|png|gif|webp|svg)$/.test(lowerName);

        if (isImage) {
            const imgContainer = document.createElement("div");
            imgContainer.className = "image-attachment-container";

            const link = document.createElement("a");
            link.href = msg.file_path;
            link.target = "_blank";

            const img = document.createElement("img");
            img.src = msg.file_path;
            img.alt = msg.content;
            img.className = "chat-image-preview";

            link.appendChild(img);
            imgContainer.appendChild(link);

            const caption = document.createElement("div");
            caption.className = "image-caption";
            caption.innerText = msg.content;
            imgContainer.appendChild(caption);

            msgBox.appendChild(imgContainer);
        } else {
            const link = document.createElement("a");
            link.href = msg.file_path;
            link.target = "_blank";
            link.className = "file-attachment";

            let icon = "📄";
            if (/\.(zip|rar|7z|tar|gz)$/.test(lowerName)) icon = "📦";
            else if (/\.(pdf)$/.test(lowerName)) icon = "📕";
            else if (/\.(doc|docx)$/.test(lowerName)) icon = "📘";

            link.innerHTML = `
                <span class="file-icon">${icon}</span>
                <div class="file-details">
                    <span class="file-name">${msg.content}</span>
                    <span class="file-size">Click to view/download</span>
                </div>
            `;
            msgBox.appendChild(link);
        }
    } else {
        const textNode = document.createElement("span");
        textNode.innerText = msg.content;
        msgBox.appendChild(textNode);
    }

    // Add reaction trigger button
    const rxBtn = document.createElement("button");
    rxBtn.className = "reaction-trigger-btn";
    rxBtn.innerHTML = "☺";
    rxBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        showEmojiPicker(msg.id, e.clientX, e.clientY);
    });
    msgBox.appendChild(rxBtn);

    wrapper.appendChild(info);
    wrapper.appendChild(msgBox);

    // Reactions container
    const reactionsBox = document.createElement("div");
    reactionsBox.className = "message-reactions";
    wrapper.appendChild(reactionsBox);

    chatMessagesContainer.appendChild(wrapper);
    renderReactionsList(reactionsBox, msg.id, msg.reactions);
}

function appendSystemMessage(content) {
    const el = document.createElement("div");
    el.className = "message-system";
    el.innerText = content;
    chatMessagesContainer.appendChild(el);
    scrollChatToBottom();
}

function scrollChatToBottom() {
    chatMessagesContainer.scrollTop = chatMessagesContainer.scrollHeight;
}

// Sending Messages
function dispatchSendMessage() {
    const text = chatMessageInput.value.trim();
    if (!text || !activeChat) return;

    const payload = {
        type: activeChat.type === "room" ? "send_msg" : "send_pm",
        content: text
    };

    if (activeChat.type === "room") {
        payload.room_name = activeChat.target;
    } else {
        payload.recipient = activeChat.target;
    }

    socket.send(JSON.stringify(payload));
    chatMessageInput.value = "";
    chatMessageInput.focus();
}

sendMessageBtn.addEventListener("click", dispatchSendMessage);
chatMessageInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        dispatchSendMessage();
    }
});

// Emoji Picker & Reactions Logic
function showEmojiPicker(messageId, x, y) {
    activeReactionMessageId = messageId;
    emojiPickerOverlay.style.left = `${Math.min(x, window.innerWidth - 180)}px`;
    emojiPickerOverlay.style.top = `${Math.min(y, window.innerHeight - 50)}px`;
    emojiPickerOverlay.style.display = "grid";

    // Listen click outside to close
    document.addEventListener("click", closeEmojiPicker);
}

function closeEmojiPicker() {
    emojiPickerOverlay.style.display = "none";
    document.removeEventListener("click", closeEmojiPicker);
}

document.querySelectorAll(".emoji-option").forEach(el => {
    el.addEventListener("click", (e) => {
        const emoji = el.getAttribute("data-emoji");
        if (activeReactionMessageId && activeChat) {
            socket.send(JSON.stringify({
                type: "add_reaction",
                message_id: activeReactionMessageId,
                emoji: emoji,
                room_name: activeChat.type === "room" ? activeChat.target : null,
                recipient: activeChat.type === "pm" ? activeChat.target : null
            }));
        }
        closeEmojiPicker();
    });
});

function updateMessageReactions(messageId, reactions) {
    const msgWrapper = document.querySelector(`.message-wrapper[data-message-id="${messageId}"]`);
    if (msgWrapper) {
        const box = msgWrapper.querySelector(".message-reactions");
        if (box) {
            renderReactionsList(box, messageId, reactions);
        }
    }
}

function renderReactionsList(container, messageId, reactions) {
    container.innerHTML = "";

    // Group reactions by emoji
    const grouped = {};
    reactions.forEach(r => {
        if (!grouped[r.emoji]) grouped[r.emoji] = [];
        grouped[r.emoji].push(r.username);
    });

    Object.keys(grouped).forEach(emoji => {
        const users = grouped[emoji];
        const badge = document.createElement("div");
        badge.className = `reaction-badge ${users.includes(currentUsername) ? 'active' : ''}`;
        badge.innerHTML = `<span>${emoji}</span><span>${users.length}</span>`;
        badge.title = `Reacted by: ${users.join(', ')}`;

        badge.addEventListener("click", () => {
            socket.send(JSON.stringify({
                type: "add_reaction",
                message_id: messageId,
                emoji: emoji,
                room_name: activeChat.type === "room" ? activeChat.target : null,
                recipient: activeChat.type === "pm" ? activeChat.target : null
            }));
        });
        container.appendChild(badge);
    });
}


// Room Creation Modal Action
openCreateRoomBtn.addEventListener("click", () => {
    createRoomModal.style.display = "flex";
    newRoomNameInput.value = "";
    newRoomNameInput.focus();
});

closeCreateRoomBtn.addEventListener("click", () => {
    createRoomModal.style.display = "none";
});

createRoomSubmitBtn.addEventListener("click", () => {
    const roomName = newRoomNameInput.value.trim();
    if (!roomName) return;

    socket.send(JSON.stringify({
        type: "create_room",
        room_name: roomName
    }));
    createRoomModal.style.display = "none";
});

// Matchmaking System Orchestrator
matchmakingActionBtn.addEventListener("click", () => {
    if (isMatchmaking) {
        // Cancel matchmaking
        socket.send(JSON.stringify({
            type: "cancel_matchmaking"
        }));
    } else {
        // Start matchmaking
        socket.send(JSON.stringify({
            type: "start_matchmaking"
        }));
    }
});

function handleMatchmakingStatus(msg) {
    if (msg.status === "queued") {
        isMatchmaking = true;
        matchmakingActionBtn.querySelector("span").innerText = "Cancel Search";
        matchmakingActionBtn.className = "btn btn-secondary";
        matchmakingStatusCard.style.display = "flex";

        queueStatusText.innerText = `Lobby queue size: ${msg.queue_size}`;

        // Timer trigger
        if (!matchmakerTimer) {
            matchmakerSeconds = 0;
            updateQueueTimerUI();
            matchmakerTimer = setInterval(() => {
                matchmakerSeconds++;
                updateQueueTimerUI();
            }, 1000);
        }
    }
    else if (msg.status === "cancelled") {
        teardownMatchmakingUI();
    }
    else if (msg.status === "matched") {
        teardownMatchmakingUI();

        // Show matching alert
        matchedOpponentInfo.innerText = `VS ${msg.opponent} (MMR: ${msg.opponent_mmr})`;
        matchFoundOverlay.style.display = "flex";

        // Automatically transfer both into match chat after 3 seconds
        setTimeout(() => {
            matchFoundOverlay.style.display = "none";
            joinRoom(msg.room_name);
        }, 3000);
    }
}

function updateQueueTimerUI() {
    const mins = String(Math.floor(matchmakerSeconds / 60)).padStart(2, '0');
    const secs = String(matchmakerSeconds % 60).padStart(2, '0');
    queueTimerText.innerText = `${mins}:${secs}`;
}

function teardownMatchmakingUI() {
    isMatchmaking = false;
    clearInterval(matchmakerTimer);
    matchmakerTimer = null;

    matchmakingActionBtn.querySelector("span").innerText = "Find Match";
    matchmakingActionBtn.className = "btn";
    matchmakingStatusCard.style.display = "none";
}
