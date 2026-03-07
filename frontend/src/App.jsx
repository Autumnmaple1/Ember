import React, { useState, useEffect, useRef } from 'react'
import { Radar, RadarChart, PolarGrid, PolarAngleAxis, ResponsiveContainer } from 'recharts';
import './App.css'
import Live2DViewer from './Live2DViewer';

function App() {
  const [messages, setMessages] = useState([]);
  const [currentEmotion, setCurrentEmotion] = useState("normal");
  const [config, setConfig] = useState(null);
  const [currentState, setCurrentState] = useState({});
  const [currentThought, setCurrentThought] = useState("");
  const [showThought, setShowThought] = useState(false);
  const [logicalTime, setLogicalTime] = useState("");
  const [showTimeline, setShowTimeline] = useState(false);
  const thoughtTimerRef = useRef(null);
  const thoughtScrollRef = useRef(null);
  const ws = useRef(null);
  const listRef = useRef(null);
  const [showLogs, setShowLogs] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(420);
  const [isResizing, setIsResizing] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [activeTab, setActiveTab] = useState("chat");

  // 轮询 EventBus 逻辑时间
  useEffect(() => {
    const fetchTime = () => {
      fetch("http://localhost:8000/config")
        .then(res => res.json())
        .then(data => {
          if (data.logical_time) setLogicalTime(data.logical_time);
        })
        .catch(() => { });
    };
    const timer = setInterval(fetchTime, 2000);
    return () => clearInterval(timer);
  }, []);

  const getEmotionColor = () => {
    return "#ffffff";
  };

  const radarData = [
    { subject: 'P', value: currentState.P || 5, fullMark: 10 },
    { subject: 'A', value: currentState.A || 5, fullMark: 10 },
    { subject: 'D', value: currentState.D || 5, fullMark: 10 },
  ];

  // 当切换到对话标签页时，自动滚动到底部
  useEffect(() => {
    if (activeTab === 'chat') {
      setTimeout(scrollToBottom, 50);
    }
  }, [activeTab]);

  // 自动滚动想法窗口到底部
  useEffect(() => {
    if (thoughtScrollRef.current) {
      thoughtScrollRef.current.scrollTop = thoughtScrollRef.current.scrollHeight;
    }
  }, [currentThought]);

  // 处理侧边栏拖拽调大小
  const startResizing = (e) => {
    e.preventDefault();
    setIsResizing(true);
  };

  const stopResizing = () => {
    setIsResizing(false);
  };

  const resize = (e) => {
    if (isResizing) {
      // 限制最小宽度和最大宽度（最大也别超过屏幕一半）
      const newWidth = Math.max(280, Math.min(e.clientX - 10, window.innerWidth * 0.7));
      setSidebarWidth(newWidth);
    }
  };

  useEffect(() => {
    if (isResizing) {
      window.addEventListener('mousemove', resize);
      window.addEventListener('mouseup', stopResizing);
      // 禁止文字选中，提升拖拽体验
      document.body.style.userSelect = 'none';
      document.body.style.cursor = 'col-resize';
    } else {
      window.removeEventListener('mousemove', resize);
      window.removeEventListener('mouseup', stopResizing);
      document.body.style.userSelect = 'auto';
      document.body.style.cursor = 'default';
    }
    return () => {
      window.removeEventListener('mousemove', resize);
      window.removeEventListener('mouseup', stopResizing);
    };
  }, [isResizing]);

  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [inputValue, setInputValue] = useState("");
  const [isRecording, setIsRecording] = useState(false);
  const mediaRecorder = useRef(null);
  const audioChunks = useRef([]);
  const [currentAudio, setCurrentAudio] = useState(null);
  const audioQueue = useRef({}); // 改为对象以存储 index -> data
  const nextPlayIndex = useRef(0);
  const isPlaying = useRef(false);

  // 获取后端角色配置
  useEffect(() => {
    fetch("http://localhost:8000/config")
      .then(res => res.json())
      .then(data => {
        if (data.error) {
          console.error("加载配置失败:", data.error);
        } else {
          console.log("已加载角色配置:", data);
          setConfig(data);
          if (data.state) setCurrentState(data.state);
        }
      })
      .catch(err => console.error("获取后端配置异常:", err));

    // 初始获取历史记录
    loadHistory();
  }, []);

  // 加载分页历史记录
  const loadHistory = async (before = null) => {
    if (isLoadingHistory || (!hasMore && before)) return;

    setIsLoadingHistory(true);
    const url = `http://localhost:8000/history?limit=20${before ? `&before=${before}` : ""}`;
    try {
      const res = await fetch(url);
      const data = await res.json();
      if (Array.isArray(data)) {
        if (data.length < 20) {
          setHasMore(false);
        }

        // 统一处理历史记录：数据库返回 DESC，我们需要将其反转为时间正序显示
        const historyData = [...data].reverse();

        if (before) {
          // 向上加载更多，需要保持滚动位置
          const originalHeight = listRef.current?.scrollHeight || 0;
          setMessages(prev => [...historyData, ...prev]);

          // 加载完成后恢复滚动位置（会在渲染后处理）
          setTimeout(() => {
            if (listRef.current) {
              const newHeight = listRef.current.scrollHeight;
              listRef.current.scrollTop = newHeight - originalHeight;
            }
          }, 0);
        } else {
          // 初始加载，滚到底部
          setMessages(historyData);
          // 增加延迟和多次尝试，确保在内容完全渲染后滚动到底部
          setTimeout(() => scrollToBottom(), 100);
          setTimeout(() => scrollToBottom(), 300);
          setTimeout(() => scrollToBottom(), 500);
        }
      }
    } catch (err) {
      console.error("加载历史失败:", err);
    } finally {
      setIsLoadingHistory(false);
    }
  };

  // 监听滚动到顶部
  const handleScroll = (e) => {
    if (e.target.scrollTop === 0 && hasMore && !isLoadingHistory && !showLogs) {
      // 找到目前最早的一条聊天记录的时间（非系统日志）
      const chatMessages = messages.filter(m => m.role !== 'system');
      if (chatMessages.length > 0) {
        const oldestTimestamp = chatMessages[0].timestamp;
        loadHistory(oldestTimestamp);
      }
    }
  };

  // 自动滚动到底部
  const scrollToBottom = () => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  };

  // 监听消息更新自动滚到底部（仅限新消息进入时）
  useEffect(() => {
    // 如果消息变长且不是在加载历史，则滚动到底
    if (!isLoadingHistory && messages.length > 0) {
      scrollToBottom();
    }
  }, [messages, isLoadingHistory]);

  // 播放音频队列
  const playNextAudio = () => {
    if (isPlaying.current) return;

    const audioObj = audioQueue.current[nextPlayIndex.current];
    if (audioObj) {
      isPlaying.current = true;
      delete audioQueue.current[nextPlayIndex.current]; // 取出后删除

      // 【音画同步】在播放开始前同步显示文本和表情
      if (audioObj.text) {
        const messageId = audioObj.id || Date.now();
        setMessages(prev => {
          const existingIndex = prev.findIndex(m => m.id === messageId && m.role === 'ai');
          if (existingIndex !== -1) {
            const newMessages = [...prev];
            newMessages[existingIndex] = {
              ...newMessages[existingIndex],
              content: newMessages[existingIndex].content + audioObj.text + " ",
              timestamp: Date.now() // 更新时间戳为最后一段话的时间
            };
            return newMessages;
          } else {
            return [...prev, { role: 'ai', content: audioObj.text + " ", id: messageId, timestamp: Date.now() }];
          }
        });
      }

      if (audioObj.live2d_emotion) {
        // 直接使用小写的标签，匹配 LSS.model3.json 中的 Name 段
        const emotion = audioObj.live2d_emotion.toLowerCase();
        setCurrentEmotion(emotion);
      }

      const audio = new Audio(`data:audio/wav;base64,${audioObj.content}`);
      setCurrentAudio(audio);

      audio.onended = () => {
        isPlaying.current = false;
        setCurrentAudio(null);
        nextPlayIndex.current += 1; // 播放下一号
        playNextAudio();
      };

      audio.play().catch(err => {
        console.error("播放音频失败:", err);
        isPlaying.current = false;
        nextPlayIndex.current += 1;
        playNextAudio();
      });
    }
  };

  useEffect(() => {
    // 延迟连接以避开 React Strict Mode 的重复初始化干扰
    const connectWS = () => {
      console.log("正在尝试建立 WebSocket 连接...");
      ws.current = new WebSocket("ws://localhost:8000/ws/chat");

      ws.current.onopen = () => {
        console.log("WebSocket 连接已打开");
        setMessages(prev => [...prev.filter(m => m.id !== 'ws-status'), { role: "system", content: "WebSocket 连接已建立", id: 'ws-status' }]);
      };

      ws.current.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          console.log("收到 WebSocket 数据对象:", data);

          if (data.type === 'message') {
            const messageId = data.id || Date.now();
            setMessages(prev => {
              const mappedRole = (data.sender === 'ai' || data.sender === 'assistant' || data.sender === 'bot') ? 'ai' : 'user';
              const existingIndex = prev.findIndex(m => m.id === messageId && m.role === mappedRole);

              let updatedFullContent = "";
              if (existingIndex !== -1) {
                const oldFullContent = prev[existingIndex].rawContent || prev[existingIndex].content;
                updatedFullContent = data.mode === 'append' ? oldFullContent + data.content : data.content;
              } else {
                updatedFullContent = data.content;
              }

              // 解析 thought 部分
              let displayContent = updatedFullContent;
              let extractedThought = "";

              // 查找第一个 <thought> 到其后的第 一个 </thought> 之间的内容
              const thoughtStartTag = "<thought>";
              const thoughtEndTag = "</thought>";
              const firstThoughtStart = updatedFullContent.indexOf(thoughtStartTag);
              const firstThoughtEnd = updatedFullContent.indexOf(thoughtEndTag, firstThoughtStart);

              if (firstThoughtStart !== -1) {
                if (firstThoughtEnd !== -1) {
                  extractedThought = updatedFullContent.slice(firstThoughtStart + thoughtStartTag.length, firstThoughtEnd).trim();
                } else {
                  // 如果还没收录完第一个 </thought>，则显示目前收到的所有 thought 内容
                  extractedThought = updatedFullContent.slice(firstThoughtStart + thoughtStartTag.length).trim();
                }
                setCurrentThought(extractedThought);
                setShowThought(true);
                if (thoughtTimerRef.current) clearTimeout(thoughtTimerRef.current);
              }

              // 查找最后一个 </thought>
              const lastThoughtEnd = updatedFullContent.lastIndexOf(thoughtEndTag);
              if (lastThoughtEnd !== -1) {
                displayContent = updatedFullContent.slice(lastThoughtEnd + thoughtEndTag.length).trim();
              } else if (updatedFullContent.includes(thoughtStartTag)) {
                // 如果还在 thought 中，对话框显示为空
                displayContent = "";
              }

              if (existingIndex !== -1) {
                const newMessages = [...prev];
                newMessages[existingIndex] = {
                  ...newMessages[existingIndex],
                  content: displayContent,
                  rawContent: updatedFullContent,
                  timestamp: data.timestamp || newMessages[existingIndex].timestamp
                };
                return newMessages;
              } else {
                return [...prev, {
                  role: mappedRole,
                  content: displayContent,
                  rawContent: updatedFullContent,
                  id: messageId,
                  timestamp: data.timestamp || Date.now()
                }];
              }
            });

            if (data.live2d_emotion) {
              const emotion = data.live2d_emotion.toLowerCase();
              setCurrentEmotion(emotion);
            }

            // 如果收到的是非 append 模式的最终消息，或者检测到输出结束标识（如果有的话）
            // 这里为了通用性，在每次收到非 thought 内容更新时也可以重置计时
          }
          else if (data.type === 'llm.done') {
            console.log("收到 llm.done, 准备倒计时隐藏想法窗");
            // 当 AI 彻底说话结束，且当前存在想法内容时，启动消失倒计时
            if (thoughtTimerRef.current) clearTimeout(thoughtTimerRef.current);

            // 注意：这里不要判断 if (showThought)，因为状态更新是异步的，
            // 直接设置定时器，确保在 4 秒后执行 setShowThought(false)
            thoughtTimerRef.current = setTimeout(() => {
              console.log("执行隐藏想法窗动作");
              setShowThought(false);
              // 彻底消失后清空文字内容，避免下次对话时瞬间闪现旧内容
              setTimeout(() => {
                setCurrentThought("");
                console.log("已彻底清空想法文字");
              }, 1500);
            }, 4000);
          }
          else if (data.type === 'voice') {
            audioQueue.current[data.index] = data;
            playNextAudio();
          }
          else if (data.type === 'state_update') {
            console.log("更新实时状态:", data.state);
            setCurrentState(data.state);
          }
        } catch (err) {
          console.error("解析 WebSocket 消息失败:", err, event.data);
        }
      };

      ws.current.onclose = (e) => {
        console.log("WebSocket 连接已关闭", e.reason);
        setMessages(prev => [...prev.filter(m => m.id !== 'ws-status'), { role: "system", content: "WebSocket 连接断开，尝试重连中...", id: 'ws-status' }]);
        // 5秒后尝试重连
        setTimeout(connectWS, 5000);
      };

      ws.current.onerror = (err) => {
        console.error("WebSocket 错误:", err);
      };
    };

    connectWS();

    return () => {
      if (ws.current) {
        // 关闭时清除重连相关的副作用
        ws.current.onclose = null;
        ws.current.close();
      }
    };
  }, []);

  const formatTime = (ts) => {
    if (!ts) return "";
    const date = new Date(Number(ts));
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
  };

  const formatDateLabel = (ts) => {
    if (!ts) return "";
    const date = new Date(Number(ts));
    const now = new Date();

    // 如果是今年且是同一天
    if (date.toDateString() === now.toDateString()) {
      return "今天";
    }

    // 昨天
    const yesterday = new Date();
    yesterday.setDate(now.getDate() - 1);
    if (date.toDateString() === yesterday.toDateString()) {
      return "昨天";
    }

    // 跨年显示年份，否则只显示月日
    const options = date.getFullYear() === now.getFullYear()
      ? { month: 'long', day: 'numeric' }
      : { year: 'numeric', month: 'long', day: 'numeric' };

    return date.toLocaleDateString('zh-CN', options);
  };

  const handleSend = () => {
    if (currentAudio) {
      currentAudio.pause();
      setCurrentAudio(null);
    }
    audioQueue.current = {};
    nextPlayIndex.current = 0;
    isPlaying.current = false;

    // 清除旧的想法气泡，为新对话腾出空间
    if (thoughtTimerRef.current) clearTimeout(thoughtTimerRef.current);
    setShowThought(false);
    setTimeout(() => setCurrentThought(""), 1000); // 在淡出动画后彻底清空内容

    if (!inputValue.trim()) return;
    if (ws.current && ws.current.readyState === WebSocket.OPEN) {
      const messageId = Date.now();
      // 不再在前端本地直接 setMessages，而是通过 WebSocket 发送
      // 后端会通过广播发回带有 logic_now 时间戳的消息，前端再渲染
      ws.current.send(JSON.stringify({
        sender: "user",
        format: "text",
        content: inputValue,
        id: messageId
      }));
      setInputValue("");
    }
  };

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

      mediaRecorder.current = new MediaRecorder(stream);
      audioChunks.current = [];

      mediaRecorder.current.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunks.current.push(event.data);
        }
      };
      mediaRecorder.current.onstop = () => {
        const audioBlob = new Blob(audioChunks.current, { type: 'audio/wav' });
        const reader = new FileReader();
        reader.readAsDataURL(audioBlob);
        reader.onloadend = () => {
          const base64String = reader.result.split(',')[1];
          if (ws.current && ws.current.readyState === WebSocket.OPEN) {
            ws.current.send(JSON.stringify({
              sender: "user",
              type: "voice",
              format: "audio",
              content: base64String,
              time: new Date().toISOString()
            }));
            setMessages(prev => [...prev, { role: "system", content: "[语音消息已发送]" }]);
          }
        };
      };

      mediaRecorder.current.start();
      if (currentAudio) {
        currentAudio.pause();
        setCurrentAudio(null);
      }
      audioQueue.current = {};
      nextPlayIndex.current = 0;
      isPlaying.current = false;
      setIsRecording(true);
    }
    catch (err) {
      console.error("获取麦克风失败:", err);
    }
  };

  const stopRecording = () => {
    if (mediaRecorder.current && isRecording) {
      mediaRecorder.current.stop();
      setIsRecording(false);
    }
  };

  const handleKeyPress = (e) => {
    if (e.key === 'Enter') handleSend();
  };

  return (
    <div className={`app-container ${isSidebarOpen ? 'sidebar-open' : ''}`}>
      <Live2DViewer
        currentEmotion={currentEmotion}
        audio={currentAudio}
        modelPath={config?.live2d?.model_path}
      />

      {/* 状态面板 - 放置在右上方 */}
      <div className="status-overlay">
        <div className="status-header">
          <div className="logic-clock-mini">当前时间: {logicalTime || "SYNCING..."}</div>
        </div>
        <div className="status-radar">
          <ResponsiveContainer width="100%" height="100%">
            <RadarChart cx="50%" cy="50%" outerRadius="85%" data={radarData}>
              <PolarGrid stroke="rgba(255,255,255,0.15)" />
              <PolarAngleAxis dataKey="subject" tick={{ fill: 'rgba(255,255,255,0.7)', fontSize: 12, fontWeight: 'bold' }} />
              <Radar
                name="State"
                dataKey="value"
                stroke={getEmotionColor()}
                fill={getEmotionColor()}
                fillOpacity={0.45}
              />
            </RadarChart>
          </ResponsiveContainer>
        </div>
        <div className="status-content">
          <div className="status-scroll-box">
            <div className="status-text-item">
              <span className="label">客观情境</span>
              <p>{currentState["客观情境"] || "未知"}</p>
            </div>
            <div className="status-text-item">
              <span className="label">内心活动</span>
              <p className="inner-monologue">“{currentState["内心活动"] || "平静"}”</p>
            </div>
            <div className="status-text-item">
              <span className="label">近期目标</span>
              <p>{currentState["近期目标"] || "无"}</p>
            </div>
          </div>
          <div className="status-actions">
            <button className="action-btn timeline-trigger" onClick={() => setShowTimeline(true)}>
              <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
                <path d="M13 3c-4.97 0-9 4.03-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42C8.27 19.99 10.51 21 13 21c4.97 0 9-4.03 9-9s-4.03-9-9-9zm-1 5v5l4.28 2.54.72-1.18-3.5-2.08V8H12z" />
              </svg>
              近期轨迹
            </button>
          </div>
        </div>
      </div>

      {/* 轨迹时间轴模态框 */}
      {showTimeline && (
        <div className="timeline-modal-overlay" onClick={() => setShowTimeline(false)}>
          <div className="timeline-modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3>近期综合轨迹</h3>
              <button className="action-btn" onClick={() => setShowTimeline(false)}>
                <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
                  <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
                </svg>

              </button>
            </div>
            <div className="timeline-container">
              {(currentState["近期综合轨迹"] || "").split("->").map((item, idx) => (
                <div key={idx} className="timeline-item">
                  <div className="timeline-dot-wrapper">
                    <div className="timeline-dot"></div>
                    {idx < (currentState["近期综合轨迹"] || "").split("->").length - 1 && <div className="timeline-line"></div>}
                  </div>
                  <div className="timeline-text">{item.trim()}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* 想法窗口 - 悬停在 Live2D 模型上方，支持淡入淡出 */}
      {currentThought && (
        <div className={`floating-thought ${showThought ? 'fade-in' : 'fade-out'}`}>
          <div className="thought-tag">思考过程</div>
          <div className="thought-text" ref={thoughtScrollRef}>{currentThought}</div>
        </div>
      )}

      <div
        className={`sidebar ${isSidebarOpen ? 'open' : ''} ${isResizing ? 'resizing' : ''}`}
        style={{ width: isSidebarOpen ? `${sidebarWidth}px` : '0' }}
      >
        <div className="sidebar-header">
          <div className="sidebar-title">对话回复</div>
          <button
            className="icon-btn"
            onClick={() => setShowLogs(!showLogs)}
            title={showLogs ? "返回对话" : "查看日志"}
          >
            <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
              <path d="M14 2H6c-1.1 0-1.99.9-1.99 2L4 20c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6zm2 16H8v-2h8v2zm0-4H8v-2h8v2zm-3-5V3.5L18.5 9H13z" />
            </svg>
          </button>
        </div>
        <div className="message-list" ref={listRef} onScroll={handleScroll}>
          <>
            {isLoadingHistory && (
              <div className="loading-history">加载历史记录中...</div>
            )}
            {!hasMore && !showLogs && messages.length > 10 && (
              <div className="no-more">—— 没有更多记录了 ——</div>
            )}
            {messages
              .filter(msg => showLogs ? msg.role === "system" : msg.role !== "system")
              .map((msg, index, array) => {
                const isFirstInGroup = index === 0 || array[index - 1].role !== msg.role;

                // 检查是否日期发生了跨变
                const showDateSeparator = index === 0 ||
                  new Date(Number(array[index - 1].timestamp)).toDateString() !== new Date(Number(msg.timestamp)).toDateString();

                return (
                  <React.Fragment key={msg.id || index}>
                    {showDateSeparator && !showLogs && msg.timestamp && (
                      <div className="date-separator">
                        <span>{formatDateLabel(msg.timestamp)}</span>
                      </div>
                    )}
                    <div className={`message-row ${msg.role} ${isFirstInGroup ? 'first-in-group' : 'consecutive'}`}>
                      {msg.role === 'ai' && !showLogs && (
                        <div className="avatar ai">
                          {isFirstInGroup ? (config?.display_name ? config.display_name[0] : 'A') : ''}
                        </div>
                      )}
                      <div className="message-wrapper">
                        {isFirstInGroup && !showLogs && msg.timestamp && (
                          <div className="message-time-top">{formatTime(msg.timestamp)}</div>
                        )}
                        <div
                          className={`message-item ${msg.role}`}
                          style={showLogs ? { fontSize: '12px', color: '#666', fontFamily: 'monospace' } : {}}
                        >
                          <div className="message-content">{msg.content}</div>
                        </div>
                      </div>
                      {msg.role === 'user' && !showLogs && (
                        <div className="avatar user">
                          {isFirstInGroup ? 'U' : ''}
                        </div>
                      )}
                    </div>
                  </React.Fragment>
                );
              })}
            {messages.filter(msg => showLogs ? msg.role === "system" : msg.role !== "system").length === 0 && (
              <div style={{ textAlign: 'center', color: '#999', marginTop: '20px', fontSize: '12px' }}>
                {showLogs ? "暂无系统日志" : "暂无对话记录"}
              </div>
            )}
          </>
        </div>
        {isSidebarOpen && (
          <div className="resize-handle" onMouseDown={startResizing} />
        )}
      </div>
      <div className="main-area">
        <div className="input-area">
          <button
            className={`toggle-sidebar-btn ${isSidebarOpen ? 'active' : ''}`}
            onClick={() => setIsSidebarOpen(!isSidebarOpen)}
            title={isSidebarOpen ? "关闭历史" : "打开历史"}
          >
            <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
              <path d="M13 3c-4.97 0-9 4.03-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42C8.27 19.99 10.51 21 13 21c4.97 0 9-4.03 9-9s-4.03-9-9-9zm-1 5v5l4.28 2.54.72-1.18-3.5-2.08V8H12z" />
            </svg>
            <span className="btn-label">{isSidebarOpen ? "收起" : "历史"}</span>
          </button>
          <input
            type="text"
            placeholder={config ? `和${config.display_name}聊会儿吧...` : "和我说点什么吧..."}
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyPress}
          />
          <button
            className={`action-btn mic-btn ${isRecording ? 'active recording' : ''}`}
            onClick={isRecording ? stopRecording : startRecording}
            title={isRecording ? "停止录音" : "点击开始说话"}
            disabled={true}
            style={{ opacity: 0.5, cursor: 'not-allowed' }}
          >
            <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
              <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z" />
              <path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z" />
            </svg>
            <span className="btn-label">禁用</span>
          </button>
          <button className="action-btn send-btn primary" onClick={handleSend} title={"单击或enter发送消息"}>
            <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
              <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
            </svg>
            <span className="btn-label">发送</span>
          </button>
        </div>
      </div>
    </div>
  )
}

export default App