import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'animations.dart';

class EmberRuntimeLog {
  EmberRuntimeLog({
    required this.time,
    required this.level,
    required this.message,
  });

  final DateTime time;
  final String level;
  final String message;
}

class EmberActivityPage extends StatefulWidget {
  const EmberActivityPage({required this.logs, super.key});

  final List<EmberRuntimeLog> logs;

  @override
  State<EmberActivityPage> createState() => _EmberActivityPageState();
}

class _EmberActivityPageState extends State<EmberActivityPage>
    with WidgetsBindingObserver {
  static const _channel = MethodChannel('com.ember.companion/core');

  List<Map<String, dynamic>> _history = const [];
  Map<String, dynamic> _status = const {};
  Timer? _refreshTimer;
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _reload();
    _refreshTimer = Timer.periodic(const Duration(seconds: 2), (_) {
      if (mounted) {
        setState(() {});
        _refreshStatus();
      }
    });
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _reload();
  }

  Future<void> _reload() async {
    if (mounted) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    try {
      final results = await Future.wait<Object?>([
        _channel.invokeMethod<Object?>('getChatHistory'),
        _channel.invokeMethod<Object?>('engineStatus'),
      ]);
      final historyRaw = jsonDecode(results[0] as String);
      final statusRaw = jsonDecode(results[1] as String);
      if (!mounted) return;
      setState(() {
        _history = historyRaw is List
            ? historyRaw
                .whereType<Map>()
                .map((item) => Map<String, dynamic>.from(item))
                .toList()
            : const [];
        _status = statusRaw is Map
            ? Map<String, dynamic>.from(statusRaw)
            : const {};
      });
    } on PlatformException catch (error) {
      if (mounted) setState(() => _error = error.message ?? error.code);
    } catch (error) {
      if (mounted) setState(() => _error = error.toString());
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _refreshStatus() async {
    try {
      final raw = await _channel.invokeMethod<Object?>('engineStatus');
      final decoded = jsonDecode(raw as String);
      if (decoded is Map && mounted) {
        setState(() => _status = Map<String, dynamic>.from(decoded));
      }
    } catch (_) {
      // 页面会保留最后一次有效状态，手动刷新时再展示具体错误。
    }
  }

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 2,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('记录'),
          actions: [
            IconButton(
              tooltip: '刷新',
              onPressed: _loading ? null : _reload,
              icon: const Icon(Icons.refresh),
            ),
          ],
          bottom: const TabBar(
            tabs: [
              Tab(text: '对话记录', icon: Icon(Icons.forum_outlined)),
              Tab(text: '运行日志', icon: Icon(Icons.description_outlined)),
            ],
          ),
        ),
        body: Column(
          children: [
            if (_loading) const LinearProgressIndicator(minHeight: 2),
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
            Expanded(
              child: TabBarView(
                children: [
                  _ConversationHistory(items: _history, onRefresh: _reload),
                  _RuntimeLogs(
                    logs: widget.logs,
                    status: _status,
                    onRefresh: _reload,
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ConversationHistory extends StatelessWidget {
  const _ConversationHistory({required this.items, required this.onRefresh});

  final List<Map<String, dynamic>> items;
  final Future<void> Function() onRefresh;

  @override
  Widget build(BuildContext context) {
    if (items.isEmpty) {
      return RefreshIndicator(
        onRefresh: onRefresh,
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          children: const [
            SizedBox(height: 180),
            Center(child: Text('暂无对话记录')),
          ],
        ),
      );
    }
    return RefreshIndicator(
      onRefresh: onRefresh,
      child: ListView.builder(
        padding: const EdgeInsets.fromLTRB(14, 14, 14, 28),
        itemCount: items.length,
        itemBuilder: (context, index) {
          final item = items[index];
          final role = item['role']?.toString() ?? 'assistant';
          final isUser = role == 'user';
          final text = isUser
              ? item['content']?.toString() ?? ''
              : item['speech']?.toString() ?? item['content']?.toString() ?? '';
          return EmberReveal(
            delay: Duration(
              milliseconds: (index * 40).clamp(0, 400).toInt(),
            ),
            child: _HistoryMessage(
              isUser: isUser,
              text: text,
              timestamp: item['timestamp']?.toString() ?? '',
            ),
          );
        },
      ),
    );
  }
}

class _HistoryMessage extends StatelessWidget {
  const _HistoryMessage({
    required this.isUser,
    required this.text,
    required this.timestamp,
  });

  final bool isUser;
  final String text;
  final String timestamp;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.only(bottom: 13),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment:
            isUser ? MainAxisAlignment.end : MainAxisAlignment.start,
        children: [
          if (!isUser) ...[
            const CircleAvatar(radius: 15, child: Text('依')),
            const SizedBox(width: 8),
          ],
          Flexible(
            child: Column(
              crossAxisAlignment:
                  isUser ? CrossAxisAlignment.end : CrossAxisAlignment.start,
              children: [
                if (timestamp.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 4),
                    child: Text(
                      timestamp,
                      style: Theme.of(context).textTheme.labelSmall,
                    ),
                  ),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 13, vertical: 10),
                  decoration: BoxDecoration(
                    color: isUser
                        ? colors.primaryContainer
                        : colors.surfaceContainerHigh,
                    borderRadius: BorderRadius.circular(16),
                  ),
                  child: SelectableText(text.isEmpty ? '……' : text),
                ),
              ],
            ),
          ),
          if (isUser) ...[
            const SizedBox(width: 8),
            const CircleAvatar(radius: 15, child: Icon(Icons.person, size: 17)),
          ],
        ],
      ),
    );
  }
}

class _RuntimeLogs extends StatelessWidget {
  const _RuntimeLogs({
    required this.logs,
    required this.status,
    required this.onRefresh,
  });

  final List<EmberRuntimeLog> logs;
  final Map<String, dynamic> status;
  final Future<void> Function() onRefresh;

  @override
  Widget build(BuildContext context) {
    final entries = logs.reversed.toList(growable: false);
    return RefreshIndicator(
      onRefresh: onRefresh,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(14, 14, 14, 28),
        children: [
          EmberReveal(child: _RuntimeSummary(status: status)),
          const SizedBox(height: 12),
          if (entries.isEmpty)
            const Padding(
              padding: EdgeInsets.only(top: 90),
              child: Center(child: Text('暂无本次启动日志')),
            )
          else
            for (var index = 0; index < entries.length; index++)
              EmberReveal(
                delay: Duration(
                  milliseconds: (index * 40).clamp(0, 400).toInt(),
                ),
                offset: 0.02,
                child: _LogRow(entry: entries[index]),
              ),
        ],
      ),
    );
  }
}

class _RuntimeSummary extends StatelessWidget {
  const _RuntimeSummary({required this.status});

  final Map<String, dynamic> status;

  @override
  Widget build(BuildContext context) {
    final running = status['running'] == true;
    final memory = status['memory'] is Map
        ? Map<String, dynamic>.from(status['memory'] as Map)
        : const <String, dynamic>{};
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(
                  Icons.circle,
                  size: 11,
                  color: running ? const Color(0xFF62C77B) : Colors.redAccent,
                ),
                const SizedBox(width: 8),
                Text(
                  running ? '本地核心运行中' : '本地核心未运行',
                  style: Theme.of(context).textTheme.titleSmall,
                ),
              ],
            ),
            const SizedBox(height: 9),
            Text('逻辑时间：${status['logical_time'] ?? '-'}'),
            Text('心跳次数：${status['tick_count'] ?? 0}'),
            if (memory.isNotEmpty)
              Text(
                '记忆：${memory.entries.map((e) => '${e.key} ${e.value}').join(' · ')}',
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
          ],
        ),
      ),
    );
  }
}

class _LogRow extends StatelessWidget {
  const _LogRow({required this.entry});

  final EmberRuntimeLog entry;

  @override
  Widget build(BuildContext context) {
    final isError = entry.level == 'ERROR';
    final time = entry.time;
    final timeText =
        '${_two(time.hour)}:${_two(time.minute)}:${_two(time.second)}';
    return Container(
      margin: const EdgeInsets.only(bottom: 7),
      padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 9),
      decoration: BoxDecoration(
        color: isError
            ? Theme.of(context).colorScheme.errorContainer
            : Theme.of(context).colorScheme.surfaceContainerLow,
        borderRadius: BorderRadius.circular(10),
      ),
      child: SelectableText(
        '$timeText  ${entry.level.padRight(5)}  ${entry.message}',
        style: Theme.of(context).textTheme.bodySmall?.copyWith(
              fontFamily: 'monospace',
              height: 1.35,
            ),
      ),
    );
  }

  static String _two(int value) => value.toString().padLeft(2, '0');
}
