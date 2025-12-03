const express = require('express');
const http = require('http');
const socketIo = require('socket.io');

const app = express();
const server = http.createServer(app);
const io = socketIo(server, {
  cors: {
    origin: "*",
    methods: ["GET", "POST"]
  }
});

// Храним состояние
const chatRooms = new Map(); // chatId -> {users: Map(userId, socketId), messages: []}
const userChats = new Map(); // userId -> Set(chatId)

// Очистка неактивных соединений
setInterval(() => {
  const now = Date.now();
  for (const [chatId, room] of chatRooms.entries()) {
    for (const [userId, socketId] of room.users.entries()) {
      const socket = io.sockets.sockets.get(socketId);
      if (!socket || !socket.connected) {
        room.users.delete(userId);
        console.log(`Cleaned up disconnected user ${userId} from chat ${chatId}`);
        
        // Уведомляем чат
        io.to(`chat_${chatId}`).emit('user_left', {
          userId,
          timestamp: new Date().toISOString(),
          onlineCount: room.users.size
        });
      }
    }
    
    if (room.users.size === 0) {
      chatRooms.delete(chatId);
    }
  }
}, 30000); // Каждые 30 секунд

io.on('connection', (socket) => {
  console.log('New connection:', socket.id);
  
  let currentUserId = null;
  let currentChatId = null;
  
  // Присоединение к чату
  socket.on('join_chat', (data) => {
    const { chatId, userId, userData } = data;
    
    if (!chatId || !userId) {
      socket.emit('error', { message: 'Missing chatId or userId' });
      return;
    }
    
    currentUserId = userId;
    currentChatId = chatId;
    
    // Инициализируем комнату если её нет
    if (!chatRooms.has(chatId)) {
      chatRooms.set(chatId, {
        users: new Map(),
        messages: []
      });
    }
    
    const room = chatRooms.get(chatId);
    
    // Удаляем старые соединения этого пользователя
    if (room.users.has(userId)) {
      const oldSocketId = room.users.get(userId);
      const oldSocket = io.sockets.sockets.get(oldSocketId);
      if (oldSocket) {
        oldSocket.leave(`chat_${chatId}`);
      }
      room.users.delete(userId);
    }
    
    // Добавляем новое соединение
    room.users.set(userId, socket.id);
    socket.join(`chat_${chatId}`);
    
    // Обновляем userChats
    if (!userChats.has(userId)) {
      userChats.set(userId, new Set());
    }
    userChats.get(userId).add(chatId);
    
    // Уведомляем чат о новом пользователе
    socket.to(`chat_${chatId}`).emit('user_joined', {
      userId,
      userData,
      timestamp: new Date().toISOString(),
      onlineCount: room.users.size
    });
    
    // Отправляем текущий онлайн
    io.to(`chat_${chatId}`).emit('online_update', {
      chatId,
      users: Array.from(room.users.keys()),
      count: room.users.size
    });
    
    console.log(`User ${userId} joined chat ${chatId}, online: ${room.users.size}`);
  });
  
  // Отправка сообщения
  socket.on('send_message', async (data) => {
    const { chatId, userId, content, type, media_url, mentions } = data;
    
    if (!chatId || !userId || (!content && !media_url)) {
      socket.emit('error', { message: 'Invalid message data' });
      return;
    }
    
    const room = chatRooms.get(chatId);
    if (!room || !room.users.has(userId)) {
      socket.emit('error', { message: 'Not in chat' });
      return;
    }
    
    const message = {
      id: Date.now(),
      userId,
      content,
      type: type || 'text',
      media_url,
      mentions: mentions || [],
      timestamp: new Date().toISOString(),
      pending: true // Флаг что еще не сохранено в БД
    };
    
    // Сохраняем в истории комнаты (ограничиваем размер)
    room.messages.push(message);
    if (room.messages.length > 1000) {
      room.messages = room.messages.slice(-500);
    }
    
    // Отправляем всем в чате
    io.to(`chat_${chatId}`).emit('new_message', message);
    
    // Уведомляем упомянутых пользователей
    if (mentions && Array.isArray(mentions)) {
      mentions.forEach(mention => {
        const mentionedUserId = mention.user_id;
        if (mentionedUserId && mentionedUserId !== userId) {
          const mentionedSocketId = room.users.get(mentionedUserId);
          if (mentionedSocketId) {
            io.to(mentionedSocketId).emit('mention', {
              message,
              mentioned_by: data.user_name || 'User'
            });
          }
        }
      });
    }
  });
  
  // Пользователь печатает
  socket.on('typing', (data) => {
    const { chatId, userId, isTyping } = data;
    if (chatId && userId) {
      socket.to(`chat_${chatId}`).emit('user_typing', {
        userId,
        isTyping,
        timestamp: new Date().toISOString()
      });
    }
  });
  
  // Прочитал сообщение
  socket.on('read_message', (data) => {
    const { chatId, userId, messageId } = data;
    if (chatId && userId && messageId) {
      socket.to(`chat_${chatId}`).emit('message_read', {
        userId,
        messageId,
        timestamp: new Date().toISOString()
      });
    }
  });
  
  // Отключение
  socket.on('disconnect', () => {
    console.log('Disconnected:', socket.id);
    
    if (currentUserId && currentChatId) {
      const room = chatRooms.get(currentChatId);
      if (room) {
        room.users.delete(currentUserId);
        
        // Уведомляем чат
        io.to(`chat_${currentChatId}`).emit('user_left', {
          userId: currentUserId,
          timestamp: new Date().toISOString(),
          onlineCount: room.users.size
        });
        
        if (room.users.size === 0) {
          chatRooms.delete(currentChatId);
        }
      }
      
      // Удаляем из userChats
      const userChatSet = userChats.get(currentUserId);
      if (userChatSet) {
        userChatSet.delete(currentChatId);
        if (userChatSet.size === 0) {
          userChats.delete(currentUserId);
        }
      }
    }
  });
});

// Статистика
app.get('/stats', (req, res) => {
  res.json({
    chatRooms: chatRooms.size,
    totalUsers: userChats.size,
    connections: io.engine.clientsCount,
    timestamp: new Date().toISOString()
  });
});

const PORT = process.env.PORT || 3001;
server.listen(PORT, () => {
  console.log(`WebSocket server running on port ${PORT}`);
});