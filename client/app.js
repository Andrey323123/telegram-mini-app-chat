class TelegramChatApp {
    constructor() {
        this.tg = window.Telegram.WebApp;
        this.user = null;
        this.chatId = 1; // ID общего чата
        this.socket = null;
        this.apiUrl = window.location.origin;
        this.isTyping = false;
        this.typingTimeout = null;
        this.lastMessageId = 0;
        this.emojiPickerVisible = false;
        
        // Инициализация
        this.init();
    }
    
    async init() {
        try {
            // Инициализация Telegram Web App
            this.tg.expand();
            this.tg.enableClosingConfirmation();
            this.tg.setHeaderColor('#212121');
            this.tg.setBackgroundColor('#1a1a1a');
            
            // Авторизация
            await this.authenticate();
            
            // Загружаем сообщения
            await this.loadMessages();
            
            // Подключаемся к WebSocket
            this.connectWebSocket();
            
            // Показываем интерфейс
            this.showInterface();
            
            // Устанавливаем фокус на поле ввода
            setTimeout(() => {
                document.getElementById('message-input').focus();
            }, 500);
            
        } catch (error) {
            console.error('Ошибка инициализации:', error);
            this.showError('Ошибка загрузки чата');
        }
    }
    
    async authenticate() {
        try {
            const initData = this.tg.initData;
            const initDataUnsafe = this.tg.initDataUnsafe;
            
            // Если нет данных от Telegram, используем режим разработки
            if (!initData || !initDataUnsafe.user) {
                console.warn('No Telegram auth data, using dev mode');
                this.user = {
                    id: 1,
                    telegram_id: 123456789,
                    username: 'developer',
                    first_name: 'Разработчик',
                    avatar_url: 'https://via.placeholder.com/150',
                    is_admin: true
                };
                return;
            }
            
            // Авторизация через API
            const response = await fetch(`${this.apiUrl}/api/auth/telegram`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    init_data: initData,
                    user: initDataUnsafe.user
                })
            });
            
            if (!response.ok) {
                throw new Error(`Auth failed: ${response.status}`);
            }
            
            const data = await response.json();
            if (!data.success) {
                throw new Error('Auth response not successful');
            }
            
            this.user = data.user;
            console.log('✅ Авторизован как:', this.user.first_name);
            
            // Обновляем информацию в меню
            this.updateUserInfo();
            
        } catch (error) {
            console.error('Auth error:', error);
            throw error;
        }
    }
    
    updateUserInfo() {
        if (this.user) {
            const avatar = document.getElementById('user-avatar');
            const name = document.getElementById('user-name');
            const username = document.getElementById('user-username');
            
            if (avatar) avatar.src = this.user.avatar_url || 'https://via.placeholder.com/150';
            if (name) name.textContent = this.user.first_name || 'Пользователь';
            if (username) username.textContent = `@${this.user.username || 'username'}`;
        }
    }
    
    async loadMessages() {
        try {
            const response = await fetch(`${this.apiUrl}/api/chat/messages?limit=50`);
            const data = await response.json();
            
            if (!data.success) {
                throw new Error('Failed to load messages');
            }
            
            const container = document.getElementById('messages-container');
            container.innerHTML = '';
            
            if (data.messages.length === 0) {
                container.innerHTML = `
                    <div class="system-message">
                        Чат пустой. Будьте первым, кто напишет сообщение!
                    </div>
                `;
                return;
            }
            
            data.messages.forEach(msg => {
                this.displayMessage(msg);
            });
            
            this.lastMessageId = data.messages.length > 0 ? data.messages[data.messages.length - 1].id : 0;
            
            // Прокручиваем вниз
            this.scrollToBottom();
            
        } catch (error) {
            console.error('Error loading messages:', error);
            this.showError('Ошибка загрузки сообщений');
        }
    }
    
    connectWebSocket() {
        try {
            // Подключаемся к WebSocket серверу
            const wsUrl = this.apiUrl.replace('http', 'ws') + `/ws/${this.chatId}/${this.user.id}`;
            this.socket = io(wsUrl, {
                transports: ['websocket', 'polling'],
                reconnection: true,
                reconnectionAttempts: 5,
                reconnectionDelay: 1000
            });
            
            // Обработчики событий WebSocket
            this.socket.on('connect', () => {
                console.log('✅ WebSocket connected');
                
                // Присоединяемся к чату
                this.socket.emit('join_chat', {
                    chatId: this.chatId,
                    userId: this.user.id,
                    userData: this.user
                });
            });
            
            this.socket.on('new_message', (message) => {
                this.displayMessage(message);
                this.scrollToBottom();
            });
            
            this.socket.on('user_joined', (data) => {
                this.showSystemMessage(`👤 Пользователь присоединился`);
                this.updateOnlineCount(data.onlineCount);
            });
            
            this.socket.on('user_left', (data) => {
                this.showSystemMessage(`👤 Пользователь вышел`);
                this.updateOnlineCount(data.onlineCount);
            });
            
            this.socket.on('online_update', (data) => {
                this.updateOnlineCount(data.count);
            });
            
            this.socket.on('user_typing', (data) => {
                this.showTypingIndicator(data.userId, data.user_name);
            });
            
            this.socket.on('mention', (data) => {
                this.showNotification(`Вас упомянул ${data.mentioned_by}`, data.message.content);
            });
            
            this.socket.on('muted', (data) => {
                this.showNotification('Вы замьючены', `Причина: ${data.reason || 'Не указана'}. До: ${new Date(data.muted_until).toLocaleTimeString()}`);
            });
            
            this.socket.on('user_muted', (data) => {
                this.showSystemMessage(`🔇 ${data.user_name} замьючен на ${data.duration_minutes} минут`);
            });
            
            this.socket.on('user_banned', (data) => {
                this.showSystemMessage(`🚫 ${data.user_name} забанен`);
            });
            
            this.socket.on('disconnect', () => {
                console.log('❌ WebSocket disconnected');
            });
            
            this.socket.on('error', (error) => {
                console.error('WebSocket error:', error);
            });
            
        } catch (error) {
            console.error('WebSocket connection error:', error);
        }
    }
    
    displayMessage(message) {
        const container = document.getElementById('messages-container');
        const isOwn = message.user && message.user.id === this.user.id;
        
        // Удаляем индикатор печатания для этого пользователя
        this.removeTypingIndicator(message.user ? message.user.id : null);
        
        const messageEl = document.createElement('div');
        messageEl.className = `message ${isOwn ? 'own' : ''} ${message.pending ? 'pending' : ''}`;
        messageEl.dataset.messageId = message.id;
        
        const time = new Date(message.created_at || message.timestamp).toLocaleTimeString([], {
            hour: '2-digit',
            minute: '2-digit'
        });
        
        // Форматируем контент с поддержкой переносов строк и упоминаний
        let formattedContent = this.formatMessageContent(message.content, message.mentions);
        
        // Если есть медиа
        let mediaHtml = '';
        if (message.media_url) {
            if (message.type === 'photo' || message.type === 'image') {
                mediaHtml = `
                    <div class="message-media">
                        <img src="${message.media_url}" alt="Изображение" 
                             onclick="chatApp.openMedia('${message.media_url}')"
                             style="cursor: pointer;">
                    </div>
                `;
            } else if (message.type === 'voice' || message.type === 'audio') {
                mediaHtml = `
                    <div class="message-media">
                        <audio controls src="${message.media_url}" style="width: 100%;"></audio>
                    </div>
                `;
            } else if (message.type === 'video') {
                mediaHtml = `
                    <div class="message-media">
                        <video controls src="${message.media_url}" style="width: 100%; border-radius: 12px;"></video>
                    </div>
                `;
            } else {
                mediaHtml = `
                    <div class="message-media" style="padding: 8px; background: rgba(255,255,255,0.1); border-radius: 12px;">
                        <a href="${message.media_url}" target="_blank" style="color: #4dabf7; text-decoration: none;">
                            <i class="fas fa-file"></i> Файл (${this.formatFileSize(message.media_size || 0)})
                        </a>
                    </div>
                `;
            }
        }
        
        messageEl.innerHTML = `
            ${!isOwn ? `
            <div class="message-avatar">
                <img src="${message.user?.avatar_url || 'https://via.placeholder.com/150'}" 
                     alt="${message.user?.first_name || 'Пользователь'}"
                     onclick="chatApp.showUserProfile(${message.user?.id})"
                     style="cursor: pointer;">
            </div>
            ` : ''}
            
            <div class="message-content">
                ${!isOwn && message.user ? `
                <div class="message-header">
                    <span class="message-sender">${message.user.first_name || 'Пользователь'}</span>
                    <span class="message-time">${time}</span>
                </div>
                ` : ''}
                
                ${message.reply_to ? `
                <div class="message-reply" style="padding: 4px 8px; background: rgba(255,255,255,0.05); border-radius: 8px; margin-bottom: 4px; font-size: 13px; color: #8a8a8a; border-left: 3px solid #4dabf7;">
                    Ответ на сообщение
                </div>
                ` : ''}
                
                ${formattedContent ? `<div class="message-text">${formattedContent}</div>` : ''}
                
                ${mediaHtml}
                
                ${message.pending ? `
                <div class="message-status">
                    <div class="status-dots">
                        <div class="dot"></div>
                        <div class="dot"></div>
                        <div class="dot"></div>
                    </div>
                </div>
                ` : ''}
                
                ${isOwn ? `
                <div class="message-time" style="text-align: right; margin-top: 4px; font-size: 11px;">${time}</div>
                ` : ''}
            </div>
        `;
        
        container.appendChild(messageEl);
        
        // Если это свое сообщение, убираем индикатор загрузки после сохранения
        if (isOwn && message.pending) {
            setTimeout(() => {
                if (messageEl.querySelector('.status-dots')) {
                    messageEl.querySelector('.status-dots').style.display = 'none';
                }
            }, 2000);
        }
    }
    
    formatMessageContent(content, mentions = []) {
        if (!content) return '';
        
        // Экранируем HTML
        let formatted = content
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
        
        // Заменяем переносы строк
        formatted = formatted.replace(/\n/g, '<br>');
        
        // Обрабатываем упоминания
        mentions.forEach(mention => {
            const username = mention.username || mention.first_name;
            if (username) {
                const mentionRegex = new RegExp(`@${username}\\b`, 'gi');
                formatted = formatted.replace(mentionRegex, 
                    `<span class="mention" onclick="chatApp.showUserProfile(${mention.user_id})">@${username}</span>`);
            }
        });
        
        // Обрабатываем ссылки
        const urlRegex = /(https?:\/\/[^\s]+)/g;
        formatted = formatted.replace(urlRegex, '<a href="$1" target="_blank">$1</a>');
        
        return formatted;
    }
    
    formatFileSize(bytes) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }
    
    async sendMessage() {
        const input = document.getElementById('message-input');
        const content = input.value.trim();
        
        if (!content) return;
        
        // Очищаем поле ввода
        input.value = '';
        this.adjustTextarea(input);
        
        // Сбрасываем статус печатания
        this.stopTyping();
        
        try {
            const formData = new FormData();
            formData.append('user_id', this.user.id);
            formData.append('content', content);
            formData.append('chat_id', this.chatId);
            
            // Отправляем через API
            const response = await fetch(`${this.apiUrl}/api/chat/send`, {
                method: 'POST',
                body: formData
            });
            
            if (!response.ok) {
                throw new Error(`Send failed: ${response.status}`);
            }
            
            const data = await response.json();
            
            if (!data.success) {
                throw new Error('Send response not successful');
            }
            
            // Сообщение уже отобразится через WebSocket
            
        } catch (error) {
            console.error('Error sending message:', error);
            this.showError('Ошибка отправки сообщения');
            
            // Восстанавливаем текст если ошибка
            input.value = content;
            this.adjustTextarea(input);
        }
    }
    
    handleKeyDown(event) {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            this.sendMessage();
        } else {
            // Отправляем статус "печатает"
            this.startTyping();
        }
    }
    
    startTyping() {
        if (!this.isTyping && this.socket) {
            this.isTyping = true;
            this.socket.emit('typing', {
                chatId: this.chatId,
                userId: this.user.id,
                isTyping: true
            });
        }
        
        // Сбрасываем таймер
        if (this.typingTimeout) {
            clearTimeout(this.typingTimeout);
        }
        
        this.typingTimeout = setTimeout(() => {
            this.stopTyping();
        }, 3000);
    }
    
    stopTyping() {
        if (this.isTyping && this.socket) {
            this.isTyping = false;
            this.socket.emit('typing', {
                chatId: this.chatId,
                userId: this.user.id,
                isTyping: false
            });
        }
        
        if (this.typingTimeout) {
            clearTimeout(this.typingTimeout);
            this.typingTimeout = null;
        }
    }
    
    showTypingIndicator(userId, userName) {
        const container = document.getElementById('messages-container');
        const typingId = `typing-${userId}`;
        
        // Удаляем существующий индикатор
        this.removeTypingIndicator(userId);
        
        const typingEl = document.createElement('div');
        typingEl.id = typingId;
        typingEl.className = 'message';
        typingEl.innerHTML = `
            <div class="message-avatar">
                <img src="https://via.placeholder.com/40" alt="${userName}">
            </div>
            <div class="message-content">
                <div class="message-header">
                    <span class="message-sender">${userName}</span>
                </div>
                <div class="message-text">
                    <div class="status-dots">
                        <div class="dot"></div>
                        <div class="dot"></div>
                        <div class="dot"></div>
                    </div>
                </div>
            </div>
        `;
        
        container.appendChild(typingEl);
        this.scrollToBottom();
        
        // Автоматически убираем через 5 секунд
        setTimeout(() => {
            this.removeTypingIndicator(userId);
        }, 5000);
    }
    
    removeTypingIndicator(userId) {
        const typingEl = document.getElementById(`typing-${userId}`);
        if (typingEl) {
            typingEl.remove();
        }
    }
    
    showSystemMessage(text) {
        const container = document.getElementById('messages-container');
        const systemEl = document.createElement('div');
        systemEl.className = 'system-message';
        systemEl.textContent = text;
        container.appendChild(systemEl);
        this.scrollToBottom();
    }
    
    updateOnlineCount(count) {
        const onlineCountEl = document.getElementById('online-count');
        if (onlineCountEl) {
            onlineCountEl.textContent = `${count} онлайн`;
        }
    }
    
    scrollToBottom() {
        const container = document.getElementById('messages-container');
        if (container) {
            container.scrollTop = container.scrollHeight;
        }
    }
    
    adjustTextarea(textarea) {
        textarea.style.height = 'auto';
        const newHeight = Math.min(textarea.scrollHeight, 120);
        textarea.style.height = newHeight + 'px';
    }
    
    showInterface() {
        document.getElementById('loading').style.display = 'none';
        document.getElementById('chat-interface').style.display = 'block';
    }
    
    // Меню и вспомогательные функции
    toggleMenu() {
        const menu = document.getElementById('menu-overlay');
        menu.classList.toggle('show');
    }
    
    closeMenu() {
        const menu = document.getElementById('menu-overlay');
        menu.classList.remove('show');
    }
    
    showNotification(title, message) {
        const notifications = document.getElementById('notifications');
        const notification = document.createElement('div');
        notification.className = 'notification';
        notification.innerHTML = `
            <div class="notification-title">${title}</div>
            <div class="notification-message">${message}</div>
        `;
        
        notifications.appendChild(notification);
        
        // Автоматически удаляем через 5 секунд
        setTimeout(() => {
            notification.remove();
        }, 5000);
    }
    
    showError(message) {
        this.showNotification('Ошибка', message);
    }
    
    showUserProfile(userId) {
        alert(`Профиль пользователя #${userId}`);
        this.closeMenu();
    }
    
    attachPhoto() {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = 'image/*';
        input.capture = 'environment';
        
        input.onchange = async (e) => {
            const file = e.target.files[0];
            if (!file) return;
            
            if (file.size > 5 * 1024 * 1024) {
                this.showError('Файл слишком большой (макс. 5MB)');
                return;
            }
            
            const formData = new FormData();
            formData.append('user_id', this.user.id);
            formData.append('chat_id', this.chatId);
            formData.append('file', file);
            
            try {
                const response = await fetch(`${this.apiUrl}/api/chat/send`, {
                    method: 'POST',
                    body: formData
                });
                
                if (!response.ok) throw new Error('Upload failed');
                
            } catch (error) {
                console.error('Error uploading photo:', error);
                this.showError('Ошибка загрузки фото');
            }
        };
        
        input.click();
    }
    
    attachVoice() {
        if (!navigator.mediaDevices || !window.MediaRecorder) {
            this.showError('Запись голоса не поддерживается в вашем браузере');
            return;
        }
        
        this.showNotification('Инфо', 'Запись голоса будет добавлена позже');
    }
    
    showEmojiPicker() {
        if (this.emojiPickerVisible) {
            document.getElementById('emoji-picker').style.display = 'none';
            this.emojiPickerVisible = false;
            return;
        }
        
        const picker = document.getElementById('emoji-picker');
        const emojis = ['😀', '😂', '🥰', '😎', '🤔', '😜', '👍', '👋', '🎉', '❤️', '🔥', '💯'];
        
        picker.innerHTML = emojis.map(emoji => 
            `<span style="font-size: 24px; margin: 4px; cursor: pointer;" onclick="chatApp.insertEmoji('${emoji}')">${emoji}</span>`
        ).join('');
        
        picker.style.display = 'block';
        this.emojiPickerVisible = true;
    }
    
    insertEmoji(emoji) {
        const input = document.getElementById('message-input');
        const start = input.selectionStart;
        const end = input.selectionEnd;
        const text = input.value;
        
        input.value = text.substring(0, start) + emoji + text.substring(end);
        input.focus();
        input.setSelectionRange(start + emoji.length, start + emoji.length);
        
        this.adjustTextarea(input);
        document.getElementById('emoji-picker').style.display = 'none';
        this.emojiPickerVisible = false;
    }
    
    openMedia(url) {
        window.open(url, '_blank');
    }
    
    // Методы меню
    showProfile() {
        alert('Профиль пользователя');
        this.closeMenu();
    }
    
    showParticipants() {
        alert('Список участников');
        this.closeMenu();
    }
    
    showSettings() {
        alert('Настройки');
        this.closeMenu();
    }
    
    async clearChat() {
        if (confirm('Очистить весь чат? Это действие нельзя отменить.')) {
            try {
                // Здесь будет API вызов для очистки чата
                this.showNotification('Инфо', 'Очистка чата будет добавлена позже');
            } catch (error) {
                this.showError('Ошибка очистки чата');
            }
        }
        this.closeMenu();
    }
    
    logout() {
        if (confirm('Вы уверены, что хотите выйти?')) {
            this.tg.close();
        }
        this.closeMenu();
    }
}

// Инициализация приложения
let chatApp;
document.addEventListener('DOMContentLoaded', () => {
    chatApp = new TelegramChatApp();
});

// Экспортируем для использования в HTML
window.chatApp = chatApp;