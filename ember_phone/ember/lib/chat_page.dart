import 'dart:async';
import 'dart:convert';
import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'live2d_view.dart';
import 'archive_page.dart';
import 'activity_page.dart';
import 'ember_theme.dart';
import 'memory_page.dart';
import 'settings_page.dart';
import 'state_page.dart';
import 'timeline_page.dart';

class EmberChatPage extends StatefulWidget {
  const EmberChatPage({super.key});

  @override
  State<EmberChatPage> createState() => _EmberChatPageState();
}

class _EmberChatPageState extends State<EmberChatPage>
    with WidgetsBindingObserver {
  static const _channel = MethodChannel('com.ember.companion/core');

  final _inputController = TextEditingController();
  final _scrollController = ScrollController();
  final _live2dKey = GlobalKey();
  final List<_ChatItem> _messages = [];
  final List<EmberRuntimeLog> _runtimeLogs = [];

  Map<String, dynamic>? _config;
  Map<String, dynamic>? _runtimeSnapshot;
  int? _activeAssistantIndex;
  bool _engineReady = false;
  bool _sending = false;
  String? _error;
  String? _live2dThought;
  Timer? _thoughtDismissTimer;
  bool _thoughtDismissedForCurrentReply = false;
  Timer? _logicalClockTicker;
  DateTime? _logicalClockAnchor;
  DateTime? _logicalClockWallAnchor;
  double _logicalClockFactor = 1;
  bool _logicalClockRunning = true;
  String? _expandedStateLabel;
  String? _expandedStateValue;

  @override
  void initState() {
    super.initState();
    _log('INFO', 'Flutter 界面已启动');
    WidgetsBinding.instance.addObserver(this);
    _channel.setMethodCallHandler(_handleNativeCall);
    _logicalClockTicker = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted && _logicalClockRunning && _logicalClockAnchor != null) {
        setState(() {});
      }
    });
    _bootstrap();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _channel.setMethodCallHandler(null);
    _thoughtDismissTimer?.cancel();
    _logicalClockTicker?.cancel();
    _inputController.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    _log('INFO', '应用生命周期：${state.name}');
    if (state == AppLifecycleState.resumed && _engineReady) {
      _syncFromRuntime();
    }
  }

  Future<Object?> _handleNativeCall(MethodCall call) async {
    if (call.method != 'llmEvent' || !mounted) return null;
    try {
      final event = _decodeMap(call.arguments);
      final type = event['type']?.toString();
      final index = _activeAssistantIndex;
      if (type == 'thought') {
        final thought = event['text']?.toString().trim() ?? '';
        if (!_thoughtDismissedForCurrentReply) {
          _showLive2dThought(thought);
        }
      } else if (type == 'tool.started') {
        if (event['background'] == true) {
          _thoughtDismissedForCurrentReply = false;
        }
        final calls = event['calls'];
        final names = calls is List
            ? calls
                  .whereType<Map>()
                  .map((item) => item['name']?.toString() ?? '')
                  .where((name) => name.isNotEmpty)
                  .join('、')
            : '';
        if (!_thoughtDismissedForCurrentReply) {
          _showLive2dThought(
            names.isEmpty ? '正在调用工具…' : '正在调用工具：$names…',
          );
        }
        _log('INFO', names.isEmpty ? '正在调用工具' : '正在调用工具：$names');
      } else if (type == 'tool.finished') {
        final results = event['results'];
        final failed = results is List
            ? results
                  .whereType<Map>()
                  .where((item) => item['success'] != true)
                  .length
            : 0;
        if (!_thoughtDismissedForCurrentReply && results is List) {
          final records = results.whereType<Map>().map((item) {
            final name = item['name']?.toString() ?? '工具';
            return item['success'] == true ? '$name ✓' : '$name 失败';
          }).join(' · ');
          _showLive2dThought(
            records.isEmpty ? '工具调用完成' : records,
          );
        }
        _log(
          failed == 0 ? 'INFO' : 'WARN',
          failed == 0 ? '工具调用完成' : '工具调用完成，$failed 项失败',
        );
      } else if (type == 'chunk' && index != null && index < _messages.length) {
        final chunk = event['text']?.toString() ?? '';
        if (chunk.isNotEmpty) {
          setState(() {
            final current = _messages[index];
            _messages[index] = current.copyWith(text: current.text + chunk);
          });
          _scrollToBottom();
        }
      } else if (type == 'finished' &&
          index != null &&
          index < _messages.length) {
        final speech = event['speech']?.toString() ?? _messages[index].text;
        final thought = event['thought']?.toString().trim() ?? '';
        final rawPad = event['speech_pad'];
        setState(() {
          _messages[index] = _messages[index].copyWith(
            text: speech.isEmpty ? '……' : speech,
            pad: rawPad is Map ? Map<String, dynamic>.from(rawPad) : null,
          );
        });
        if (!_thoughtDismissedForCurrentReply) {
          _showLive2dThought(thought, dismissAfterCompletion: true);
        }
        await _refreshState();
        _log('INFO', '对话回复完成');
      } else if (type == 'state.updated') {
        final rawSnapshot = event['snapshot'];
        if (rawSnapshot is Map) {
          setState(
            () => _assignRuntimeSnapshot(
              Map<String, dynamic>.from(rawSnapshot),
            ),
          );
        }
        _log('INFO', '对话后的心理状态已更新');
      } else if (type == 'idle.state.updated') {
        final rawSnapshot = event['snapshot'];
        if (rawSnapshot is Map) {
          setState(
            () => _assignRuntimeSnapshot(
              Map<String, dynamic>.from(rawSnapshot),
            ),
          );
        }
        _log('INFO', '空闲心理状态已演化');
      } else if (type == 'idle.message') {
        final speech = event['speech']?.toString().trim() ?? '';
        final thought = event['thought']?.toString().trim() ?? '';
        if (speech.isNotEmpty) {
          _thoughtDismissedForCurrentReply = false;
          setState(() {
            _messages.add(_ChatItem(role: 'assistant', text: speech));
          });
          _showLive2dThought(thought, dismissAfterCompletion: true);
          _scrollToBottom();
        }
        _log('INFO', '依鸣产生了一条主动消息');
      } else if (type == 'idle.error') {
        setState(
          () => _error = '空闲状态演化失败：${event['message'] ?? '未知错误'}',
        );
        _log('ERROR', '空闲状态演化失败');
      } else if (type == 'state.error') {
        setState(
          () => _error = '回复已完成，但状态更新失败：${event['message'] ?? '未知错误'}',
        );
        _log('ERROR', '对话完成，但心理状态更新失败');
      } else if (type == 'error') {
        setState(() => _error = event['message']?.toString() ?? '流式请求失败');
        _log('ERROR', 'LLM 流式请求失败');
      }
    } catch (error) {
      setState(() => _error = '流事件解析失败：$error');
      _log('ERROR', '原生流事件解析失败');
    }
    return null;
  }

  Map<String, dynamic> _decodeMap(Object? value) {
    final decoded = jsonDecode(value as String);
    if (decoded is! Map<String, dynamic>) {
      throw const FormatException('Expected a JSON object');
    }
    return decoded;
  }

  Future<void> _bootstrap() async {
    try {
      await _channel.invokeMethod<Object?>('startEngine');
      Object? configResult;
      for (var attempt = 0; attempt < 12; attempt++) {
        await Future<void>.delayed(const Duration(milliseconds: 300));
        try {
          configResult = await _channel.invokeMethod<Object?>('getLlmConfig');
          break;
        } on PlatformException {
          // The foreground service may still be starting CPython.
        }
      }
      if (configResult == null) {
        throw StateError('本地核心启动超时');
      }

      final historyResult =
          await _channel.invokeMethod<Object?>('getChatHistory');
      final statusResult =
          await _channel.invokeMethod<Object?>('engineStatus');
      final history = jsonDecode(historyResult as String);
      final restored = <_ChatItem>[];
      if (history is List) {
        for (final raw in history) {
          if (raw is! Map) continue;
          final role = raw['role']?.toString();
          final text = role == 'assistant'
              ? raw['speech']?.toString()
              : raw['content']?.toString();
          if (text != null && text.isNotEmpty) {
            restored.add(_ChatItem(role: role ?? 'system', text: text));
          }
        }
      }

      if (!mounted) return;
      setState(() {
        _config = _decodeMap(configResult);
        _assignRuntimeSnapshot(_decodeMap(statusResult));
        _messages
          ..clear()
          ..addAll(restored);
        _engineReady = true;
        _error = null;
      });
      _log('INFO', '本地 Ember 核心已就绪');
      _scrollToBottom();
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = '启动失败：$error');
      _log('ERROR', '本地 Ember 核心启动失败');
    }
  }

  Future<void> _send() async {
    final text = _inputController.text.trim();
    final configured = _config?['api_key_configured'] == true;
    if (text.isEmpty || _sending || !_engineReady || !configured) return;

    final media = MediaQuery.sizeOf(context);
    final padding = MediaQuery.paddingOf(context);
    final isLandscape =
        MediaQuery.orientationOf(context) == Orientation.landscape;
    final innerWidth = media.width - 22.0; // 两侧边距与间距
    double panelRatio;
    if (isLandscape) {
      final panelWidth = innerWidth * 0.2; // flex 2/10
      final panelHeight = media.height - padding.top - padding.bottom;
      panelRatio = panelWidth / panelHeight;
    } else {
      final panelWidth = innerWidth * 0.4; // flex 4/10
      const panelHeight = 174.0; // 180 - 上2 - 下4
      panelRatio = panelWidth / panelHeight;
    }
    final imageSize = _imageGenSizeFor(panelRatio);

    FocusManager.instance.primaryFocus?.unfocus();
    _inputController.clear();
    _thoughtDismissTimer?.cancel();
    _thoughtDismissedForCurrentReply = false;
    setState(() {
      _sending = true;
      _error = null;
      _live2dThought = null;
      _messages.add(_ChatItem(role: 'user', text: text));
      _messages.add(const _ChatItem(role: 'assistant', text: ''));
      _activeAssistantIndex = _messages.length - 1;
    });
    _log('INFO', '开始生成对话回复');
    _scrollToBottom();

    try {
      final raw = await _channel.invokeMethod<Object?>(
        'sendMessageStream',
        {'text': text, 'imageSize': imageSize},
      );
      final response = _decodeMap(raw);
      if (!mounted) return;
      final index = _activeAssistantIndex;
      final thought = response['thought']?.toString().trim() ?? '';
      if (index != null && index < _messages.length && _messages[index].text.isEmpty) {
        final speech = response['speech']?.toString().trim() ?? '';
        setState(() {
          _messages[index] = _messages[index].copyWith(
            text: speech.isEmpty ? '……' : speech,
          );
        });
        if (!_thoughtDismissedForCurrentReply) {
          _showLive2dThought(thought, dismissAfterCompletion: true);
        }
      }
    } on PlatformException catch (error) {
      if (!mounted) return;
      setState(() => _error = error.message ?? error.code);
      _log('ERROR', '对话请求失败');
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = error.toString());
      _log('ERROR', '对话请求发生未知错误');
    } finally {
      if (mounted) {
        setState(() {
          _sending = false;
          _activeAssistantIndex = null;
        });
      }
      _scrollToBottom();
    }
  }

  String _imageGenSizeFor(double ratio) {
    // 按面板真实比例生成同比例图片，cover 展示时零裁切。
    // 长边 2560 保证画质，比例极端时限制短边不小于 256。
    const maxSide = 2560;
    final clamped = ratio.clamp(0.1, 10.0).toDouble();
    var width = clamped >= 1.0
        ? maxSide
        : (maxSide * clamped).round();
    var height = clamped >= 1.0
        ? (maxSide / clamped).round()
        : maxSide;
    width = ((width ~/ 2) * 2).clamp(256, maxSide).toInt();
    height = ((height ~/ 2) * 2).clamp(256, maxSide).toInt();
    return '$width*$height';
  }

  Future<void> _showSettings() async {
    final updated = await Navigator.of(context).push<Map<String, dynamic>>(
      MaterialPageRoute(
        builder: (_) => EmberSettingsPage(
          initialConfig: _config ?? const <String, dynamic>{},
        ),
      ),
    );
    if (updated != null && mounted) {
      setState(() {
        _config = updated;
        _error = null;
      });
      await _refreshState();
      _log('INFO', '应用设置已保存');
    }
  }

  Future<void> _showArchives() async {
    final loaded = await Navigator.of(context).push<bool>(
      MaterialPageRoute(builder: (_) => const EmberArchivePage()),
    );
    if (loaded == true && mounted) {
      await _syncFromRuntime();
      _log('INFO', '存档已恢复');
    }
  }

  Future<void> _showState() async {
    final snapshot = _runtimeSnapshot;
    if (snapshot == null) return;
    await Navigator.of(context).push<void>(
      MaterialPageRoute(
        builder: (_) => EmberStatePage(initialSnapshot: snapshot),
      ),
    );
    if (mounted) await _refreshState();
  }

  Future<void> _showTimeline() async {
    final snapshot = _runtimeSnapshot;
    if (snapshot == null) return;
    await Navigator.of(context).push<void>(
      MaterialPageRoute(
        builder: (_) => EmberTimelinePage(initialSnapshot: snapshot),
      ),
    );
    if (mounted) await _refreshState();
  }

  Future<void> _showActivity() async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute(
        builder: (_) => EmberActivityPage(logs: _runtimeLogs),
      ),
    );
    if (mounted) await _syncFromRuntime();
  }

  Future<void> _showMemory() async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute(builder: (_) => const EmberMemoryPage()),
    );
  }

  void _log(String level, String message) {
    _runtimeLogs.add(
      EmberRuntimeLog(time: DateTime.now(), level: level, message: message),
    );
    if (_runtimeLogs.length > 200) _runtimeLogs.removeAt(0);
  }

  void _expandStateText(String label, String value) {
    setState(() {
      _expandedStateLabel = label;
      _expandedStateValue = value;
    });
  }

  void _closeExpandedState() {
    if (_expandedStateLabel == null) return;
    setState(() {
      _expandedStateLabel = null;
      _expandedStateValue = null;
    });
  }

  Future<void> _refreshState() async {
    try {
      final raw = await _channel.invokeMethod<Object?>('engineStatus');
      if (!mounted) return;
      setState(() => _assignRuntimeSnapshot(_decodeMap(raw)));
    } catch (_) {
      // A transient refresh failure must not interrupt the chat stream.
    }
  }

  Future<void> _syncFromRuntime() async {
    try {
      final results = await Future.wait<Object?>([
        _channel.invokeMethod<Object?>('getChatHistory'),
        _channel.invokeMethod<Object?>('engineStatus'),
      ]);
      final history = jsonDecode(results[0] as String);
      final restored = <_ChatItem>[];
      if (history is List) {
        for (final raw in history) {
          if (raw is! Map) continue;
          final role = raw['role']?.toString() ?? 'system';
          final text = role == 'assistant'
              ? raw['speech']?.toString()
              : raw['content']?.toString();
          if (text != null && text.isNotEmpty) {
            restored.add(_ChatItem(role: role, text: text));
          }
        }
      }
      if (!mounted) return;
      setState(() {
        _messages
          ..clear()
          ..addAll(restored);
        _assignRuntimeSnapshot(_decodeMap(results[1]));
      });
      _scrollToBottom();
    } catch (_) {
      // The foreground service may be transitioning; the next event will retry.
    }
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scrollController.hasClients) return;
      _scrollController.animateTo(
        _scrollController.position.maxScrollExtent,
        duration: const Duration(milliseconds: 220),
        curve: Curves.easeOut,
      );
    });
  }

  void _showLive2dThought(
    String value, {
    bool dismissAfterCompletion = false,
  }) {
    _thoughtDismissTimer?.cancel();
    final thought = value.trim();
    if (mounted) {
      setState(() => _live2dThought = thought.isEmpty ? null : thought);
    }
    if (!dismissAfterCompletion || thought.isEmpty) return;
    _thoughtDismissTimer = Timer(const Duration(seconds: 4), () {
      if (!mounted || _live2dThought != thought) return;
      setState(() => _live2dThought = null);
    });
  }

  void _dismissLive2dThought() {
    _thoughtDismissTimer?.cancel();
    _thoughtDismissedForCurrentReply = true;
    if (mounted) setState(() => _live2dThought = null);
  }

  void _assignRuntimeSnapshot(Map<String, dynamic> snapshot) {
    _runtimeSnapshot = snapshot;
    final parsed = DateTime.tryParse(snapshot['logical_time']?.toString() ?? '');
    if (parsed == null) return;
    _logicalClockAnchor = parsed;
    _logicalClockWallAnchor = DateTime.now();
    final factor = snapshot['time_accel_factor'];
    _logicalClockFactor = factor is num ? factor.toDouble() : 1;
    _logicalClockRunning = snapshot['time_flow_enabled'] != false;
  }

  String? get _backgroundUrl {
    final state = _runtimeSnapshot?['state'];
    if (state is Map) {
      final url = state['背景图Url'];
      return url is String && url.isNotEmpty ? url : null;
    }
    return null;
  }

  String get _logicalClockLabel {
    final anchor = _logicalClockAnchor;
    final wallAnchor = _logicalClockWallAnchor;
    if (anchor == null || wallAnchor == null) return '--';
    var current = anchor;
    if (_logicalClockRunning) {
      final elapsed = DateTime.now().difference(wallAnchor);
      current = anchor.add(
        Duration(
          microseconds: (elapsed.inMicroseconds * _logicalClockFactor).round(),
        ),
      );
    }
    String two(int value) => value.toString().padLeft(2, '0');
    return '${current.year}年${two(current.month)}月${two(current.day)}日 '
        '${two(current.hour)}:${two(current.minute)}:${two(current.second)}';
  }

  Widget _compactAppBarAction({
    required String tooltip,
    required VoidCallback? onPressed,
    required IconData icon,
  }) {
    return IconButton(
      tooltip: tooltip,
      onPressed: onPressed,
      icon: Icon(icon),
      iconSize: 18,
      padding: EdgeInsets.zero,
      constraints: const BoxConstraints.tightFor(width: 26, height: 28),
      visualDensity: VisualDensity.compact,
    );
  }

  Widget _buildStatePanel() {
    final expanded = _expandedStateLabel != null;
    return AnimatedSwitcher(
      duration: const Duration(milliseconds: 220),
      reverseDuration: const Duration(milliseconds: 170),
      switchInCurve: Curves.easeOutCubic,
      switchOutCurve: Curves.easeInCubic,
      transitionBuilder: (child, animation) => FadeTransition(
        opacity: animation,
        child: ScaleTransition(
          scale: Tween<double>(begin: 0.985, end: 1).animate(animation),
          child: child,
        ),
      ),
      child: _MindStatePanel(
        key: ValueKey(expanded),
        snapshot: _runtimeSnapshot,
        onStateTap: _runtimeSnapshot == null ? null : _showState,
        onDetailTap: _expandStateText,
        expandedLabel: _expandedStateLabel,
        expandedValue: _expandedStateValue,
        onExpandedClose: _closeExpandedState,
      ),
    );
  }

  Widget _buildChatPanel(bool configured) {
    return EmberGlassPanel(
      blur: 16,
      color: Theme.of(context).colorScheme.surface.withValues(alpha: 0.62),
      borderColor: Colors.transparent,
      child: Column(
        children: [
          if (_error != null)
            MaterialBanner(
              content: Text(_error!),
              actions: [
                TextButton(
                  onPressed: () => setState(() => _error = null),
                  child: const Text('关闭'),
                ),
              ],
            ),
          if (!configured && _engineReady)
            InkWell(
              onTap: _showSettings,
              child: const Padding(
                padding: EdgeInsets.all(12),
                child: Text('点击设置 API 后开始对话'),
              ),
            ),
          Expanded(
            child: _messages.isEmpty
                ? const Center(child: Text('依鸣正安静地等你开口。'))
                : ListView.builder(
                    controller: _scrollController,
                    padding: const EdgeInsets.all(9),
                    itemCount: _messages.length,
                    itemBuilder: (context, index) =>
                        _AnimatedMessageEntry(
                          key: ValueKey(index),
                          child: _MessageBubble(item: _messages[index]),
                        ),
                  ),
          ),
          if (_sending) const LinearProgressIndicator(minHeight: 2),
          Padding(
            padding: const EdgeInsets.fromLTRB(8, 5, 8, 7),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Expanded(
                  child: SizedBox(
                    height: 44,
                    child: TextField(
                      controller: _inputController,
                      minLines: 1,
                      maxLines: 1,
                      textInputAction: TextInputAction.send,
                      onSubmitted: (_) => _send(),
                      decoration: const InputDecoration(
                        hintText: '和依鸣说点什么…',
                        isDense: true,
                        contentPadding: EdgeInsets.symmetric(
                          horizontal: 12,
                          vertical: 11,
                        ),
                      ),
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                SizedBox.square(
                  dimension: 44,
                  child: IconButton.filled(
                    padding: EdgeInsets.zero,
                    onPressed: configured && !_sending ? _send : null,
                    icon: const Icon(Icons.arrow_upward),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final configured = _config?['api_key_configured'] == true;
    return Scaffold(
      appBar: AppBar(
        toolbarHeight: 28,
        titleSpacing: 5,
        flexibleSpace: ClipRect(
          child: BackdropFilter(
            filter: ImageFilter.blur(sigmaX: 14, sigmaY: 14),
            child: const ColoredBox(color: Color(0x58FFFFFF)),
          ),
        ),
        title: Row(
          children: [
            Container(
              width: 5,
              height: 5,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: _engineReady ? const Color(0xFF7BCB8B) : Colors.amber,
              ),
            ),
            const SizedBox(width: 5),
            Expanded(
              child: FittedBox(
                fit: BoxFit.scaleDown,
                alignment: Alignment.centerLeft,
                child: Text(
                  _logicalClockLabel,
                  maxLines: 1,
                  softWrap: false,
                  style: const TextStyle(
                    fontSize: 14,
                    fontWeight: FontWeight.w600,
                    fontFeatures: [FontFeature.tabularFigures()],
                  ),
                ),
              ),
            ),
          ],
        ),
        actions: [
          _compactAppBarAction(
            tooltip: '近期综合轨迹',
            onPressed: _engineReady && _runtimeSnapshot != null
                ? _showTimeline
                : null,
            icon: Icons.timeline,
          ),
          _compactAppBarAction(
            tooltip: '对话与运行记录',
            onPressed: _engineReady ? _showActivity : null,
            icon: Icons.history,
          ),
          _compactAppBarAction(
            tooltip: '存档',
            onPressed: _engineReady && !_sending ? _showArchives : null,
            icon: Icons.inventory_2_outlined,
          ),
          _compactAppBarAction(
            tooltip: '记忆图谱',
            onPressed: _engineReady ? _showMemory : null,
            icon: Icons.account_tree_outlined,
          ),
          _compactAppBarAction(
            tooltip: 'LLM 设置',
            onPressed: _engineReady ? _showSettings : null,
            icon: configured ? Icons.settings : Icons.settings_outlined,
          ),
        ],
      ),
      body: Stack(
        children: [
          const Positioned.fill(child: EmberPageBackground()),
          SafeArea(
            child: LayoutBuilder(
              builder: (context, _) {
                final landscape =
                    MediaQuery.orientationOf(context) == Orientation.landscape;
                if (landscape) {
                  return Row(
                    children: [
                      Expanded(
                        flex: 2,
                        child: Padding(
                          padding: const EdgeInsets.fromLTRB(8, 0, 3, 0),
                          child: _Live2DPanel(
                            backgroundUrl: _backgroundUrl,
                            child: EmberLive2DView(key: _live2dKey),
                          ),
                        ),
                      ),
                      Expanded(
                        flex: 8,
                        child: Padding(
                          padding: const EdgeInsets.fromLTRB(3, 3, 8, 6),
                          child: Column(
                            children: [
                              Expanded(flex: 4, child: _buildStatePanel()),
                              const SizedBox(height: 6),
                              Expanded(
                                flex: 6,
                                child: _buildChatPanel(configured),
                              ),
                            ],
                          ),
                        ),
                      ),
                    ],
                  );
                }
                return Column(
                  children: [
                    SizedBox(
                      height: 180,
                      child: Padding(
                        padding: const EdgeInsets.fromLTRB(8, 2, 8, 4),
                        child: Row(
                          children: [
                            Expanded(
                              flex: 4,
                              child: _Live2DPanel(
                                backgroundUrl: _backgroundUrl,
                                child: EmberLive2DView(key: _live2dKey),
                              ),
                            ),
                            const SizedBox(width: 6),
                            Expanded(flex: 6, child: _buildStatePanel()),
                          ],
                        ),
                      ),
                    ),
                    Expanded(flex: 5, child: _buildChatPanel(configured)),
                  ],
                );
              },
            ),
          ),
          if (_live2dThought != null) ...[
            Positioned.fill(
              child: GestureDetector(
                behavior: HitTestBehavior.translucent,
                onTap: _dismissLive2dThought,
              ),
            ),
            Positioned(
              left: 8,
              right: 8,
              top: 184,
              child: GestureDetector(
                onTap: () {},
                child: AnimatedSize(
                  duration: const Duration(milliseconds: 140),
                  alignment: Alignment.topCenter,
                  child: _Live2DThoughtBubble(text: _live2dThought!),
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _Live2DThoughtBubble extends StatelessWidget {
  const _Live2DThoughtBubble({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return ClipRect(
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 10, sigmaY: 10),
        child: DecoratedBox(
          decoration: BoxDecoration(
            color: colors.surface.withOpacity(0.72),
            border: Border.all(color: colors.primary.withOpacity(0.42)),
          ),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 10),
            child: Text(
              text,
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    height: 1.4,
                  ),
            ),
          ),
        ),
      ),
    );
  }
}

class _Live2DPanel extends StatelessWidget {
  const _Live2DPanel({
    required this.backgroundUrl,
    required this.child,
    super.key,
  });

  final String? backgroundUrl;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return ClipRect(
      child: Stack(
        fit: StackFit.expand,
        children: [
          AnimatedSwitcher(
            duration: const Duration(milliseconds: 900),
            switchInCurve: Curves.easeInOutCubic,
            switchOutCurve: Curves.easeInOutCubic,
            child: backgroundUrl == null
                ? const ColoredBox(
                    key: ValueKey('bg-none'),
                    color: Colors.transparent,
                  )
                : Image.network(
                    backgroundUrl!,
                    key: ValueKey(backgroundUrl),
                    fit: BoxFit.cover,
                    width: double.infinity,
                    height: double.infinity,
                    gaplessPlayback: true,
                    errorBuilder: (_, __, ___) => const SizedBox.shrink(),
                  ),
          ),
          child,
        ],
      ),
    );
  }
}

class _MindStatePanel extends StatelessWidget {
  const _MindStatePanel({
    super.key,
    required this.snapshot,
    this.onStateTap,
    this.onDetailTap,
    this.expandedLabel,
    this.expandedValue,
    this.onExpandedClose,
  });

  final Map<String, dynamic>? snapshot;
  final VoidCallback? onStateTap;
  final void Function(String label, String value)? onDetailTap;
  final String? expandedLabel;
  final String? expandedValue;
  final VoidCallback? onExpandedClose;

  @override
  Widget build(BuildContext context) {
    final rawState = snapshot?['state'];
    final state = rawState is Map
        ? Map<String, dynamic>.from(rawState)
        : const <String, dynamic>{};
    final colors = Theme.of(context).colorScheme;

    if (expandedLabel != null) {
      return TapRegion(
        onTapOutside: (_) => onExpandedClose?.call(),
        child: EmberGlassPanel(
          blur: 18,
          color: EmberTheme.panel,
          borderColor: colors.outlineVariant,
          child: Padding(
            padding: const EdgeInsets.all(10),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  expandedLabel!,
                  style: Theme.of(context).textTheme.labelSmall?.copyWith(
                        color: colors.primary,
                      ),
                ),
                const SizedBox(height: 6),
                Expanded(
                  child: Scrollbar(
                    child: SingleChildScrollView(
                      primary: false,
                      physics: const ClampingScrollPhysics(),
                      padding: const EdgeInsets.only(right: 5, bottom: 6),
                      child: Text(
                        expandedValue ?? '',
                        style: Theme.of(context).textTheme.bodySmall?.copyWith(
                              height: 1.45,
                            ),
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      );
    }

    return EmberGlassPanel(
      blur: 18,
      color: EmberTheme.panel,
      borderColor: colors.outlineVariant,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(10, 4, 7, 6),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: _CompactPadBars(state: state),
                ),
                TextButton.icon(
                  onPressed: onStateTap,
                  style: TextButton.styleFrom(
                    visualDensity: VisualDensity.compact,
                    padding: const EdgeInsets.symmetric(horizontal: 4),
                    minimumSize: const Size(0, 30),
                  ),
                  icon: const Icon(Icons.open_in_new, size: 15),
                  label: const Text('全部详情'),
                ),
              ],
            ),
            Expanded(
              child: SingleChildScrollView(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    _StateText(
                      label: '当前情景',
                      value: state['客观情境']?.toString() ?? '暂时没有情境记录。',
                      onTap: onDetailTap,
                    ),
                    const SizedBox(height: 6),
                    _StateText(
                      label: '内心活动',
                      value: state['内心活动']?.toString() ?? '安静地整理着思绪。',
                      onTap: onDetailTap,
                    ),
                    const SizedBox(height: 6),
                    _StateText(
                      label: '近期目标',
                      value: state['近期目标']?.toString() ?? '等待下一次互动。',
                      onTap: onDetailTap,
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _CompactPadBars extends StatelessWidget {
  const _CompactPadBars({required this.state});

  final Map<String, dynamic> state;

  double _value(String key) {
    final value = double.tryParse(state[key]?.toString() ?? '') ?? 0;
    return value.clamp(0, 10).toDouble();
  }

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 28,
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _MiniPadBar(
            label: 'P',
            value: _value('P'),
            color: const Color(0xFFE86D73),
          ),
          const SizedBox(height: 1),
          _MiniPadBar(
            label: 'A',
            value: _value('A'),
            color: const Color(0xFFF0AA3C),
          ),
          const SizedBox(height: 1),
          _MiniPadBar(
            label: 'D',
            value: _value('D'),
            color: const Color(0xFF4F8EDC),
          ),
        ],
      ),
    );
  }
}

class _MiniPadBar extends StatelessWidget {
  const _MiniPadBar({
    required this.label,
    required this.value,
    required this.color,
  });

  final String label;
  final double value;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: '$label ${value.toStringAsFixed(1)}',
      child: SizedBox(
        width: 82,
        height: 8,
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            SizedBox(
              width: 9,
              child: Text(
                label,
                style: Theme.of(context).textTheme.labelSmall?.copyWith(
                      fontSize: 7,
                      height: 1,
                      color: color,
                      fontWeight: FontWeight.w700,
                    ),
              ),
            ),
            const SizedBox(width: 2),
            Expanded(
              child: SizedBox(
                height: 3,
                child: ColoredBox(
                  color: color.withValues(alpha: 0.16),
                  child: Align(
                    alignment: Alignment.centerLeft,
                    child: TweenAnimationBuilder<double>(
                      tween: Tween(begin: 0, end: value / 10),
                      duration: const Duration(milliseconds: 320),
                      curve: Curves.easeOutCubic,
                      builder: (context, width, _) => FractionallySizedBox(
                        widthFactor: width,
                        heightFactor: 1,
                        child: ColoredBox(color: color),
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _StateText extends StatelessWidget {
  const _StateText({
    required this.label,
    required this.value,
    this.onTap,
  });

  final String label;
  final String value;
  final void Function(String label, String value)? onTap;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: onTap == null
          ? null
          : () => onTap!(label, value),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  label,
                  style: Theme.of(context).textTheme.labelSmall?.copyWith(
                        color: colors.primary,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 2),
          Text(
            value,
            // Keep all three rows visible until one is expanded in this panel.
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}

class _AnimatedMessageEntry extends StatelessWidget {
  const _AnimatedMessageEntry({required this.child, super.key});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return TweenAnimationBuilder<double>(
      tween: Tween(begin: 0, end: 1),
      duration: const Duration(milliseconds: 240),
      curve: Curves.easeOutCubic,
      child: child,
      builder: (context, value, child) => Opacity(
        opacity: value,
        child: Transform.translate(
          offset: Offset(0, 8 * (1 - value)),
          child: child,
        ),
      ),
    );
  }
}

class _ChatItem {
  const _ChatItem({required this.role, required this.text, this.pad});

  final String role;
  final String text;
  final Map<String, dynamic>? pad;

  _ChatItem copyWith({String? text, Map<String, dynamic>? pad}) {
    return _ChatItem(role: role, text: text ?? this.text, pad: pad ?? this.pad);
  }
}

class _MessageBubble extends StatelessWidget {
  const _MessageBubble({required this.item});

  final _ChatItem item;

  @override
  Widget build(BuildContext context) {
    final isUser = item.role == 'user';
    final colors = Theme.of(context).colorScheme;
    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        constraints: const BoxConstraints(maxWidth: 310),
        margin: const EdgeInsets.only(bottom: 6),
        child: EmberGlassPanel(
          blur: 10,
          color: isUser
              ? colors.primaryContainer.withOpacity(0.72)
              : Colors.white.withOpacity(0.58),
          borderColor: isUser
              ? colors.primary.withOpacity(0.18)
              : colors.outlineVariant.withOpacity(0.72),
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
          child: Text(item.text.isEmpty ? '……' : item.text),
        ),
      ),
    );
  }
}
