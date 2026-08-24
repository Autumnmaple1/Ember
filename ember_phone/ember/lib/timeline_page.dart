import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'animations.dart';
import 'ember_theme.dart';

class EmberTimelinePage extends StatefulWidget {
  const EmberTimelinePage({required this.initialSnapshot, super.key});

  final Map<String, dynamic> initialSnapshot;

  @override
  State<EmberTimelinePage> createState() => _EmberTimelinePageState();
}

class _EmberTimelinePageState extends State<EmberTimelinePage> {
  static const _channel = MethodChannel('com.ember.companion/core');

  late Map<String, dynamic> _snapshot;
  final _scrollController = ScrollController();
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _snapshot = Map<String, dynamic>.from(widget.initialSnapshot);
    _timer = Timer.periodic(
      const Duration(seconds: 2),
      (_) => _refresh(),
    );
  }

  @override
  void dispose() {
    _timer?.cancel();
    _scrollController.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    try {
      final raw = await _channel.invokeMethod<Object?>('engineStatus');
      final decoded = jsonDecode(raw as String);
      if (decoded is Map && mounted) {
        setState(() => _snapshot = Map<String, dynamic>.from(decoded));
      }
    } catch (_) {
      // 保留最后一次有效轨迹，避免瞬时后台切换打断页面。
    }
  }

  Map<String, dynamic> get _state {
    final raw = _snapshot['state'];
    return raw is Map
        ? Map<String, dynamic>.from(raw)
        : const <String, dynamic>{};
  }

  @override
  Widget build(BuildContext context) {
    final state = _state;
    final rawTimeline = state['近期综合轨迹']?.toString().trim() ?? '';
    final items = rawTimeline
        .split('->')
        .map((item) => item.trim())
        .where((item) => item.isNotEmpty)
        .toList();
    if (items.isEmpty) items.add('还没有形成近期轨迹。');

    return Scaffold(
      appBar: AppBar(
        automaticallyImplyLeading: false,
        title: const Text('近期综合轨迹'),
        actions: [
          IconButton(
            tooltip: '关闭',
            onPressed: () => Navigator.of(context).pop(),
            icon: const Icon(Icons.close),
          ),
        ],
      ),
      body: Stack(
        children: [
          const Positioned.fill(child: EmberPageBackground()),
          RefreshIndicator(
            onRefresh: _refresh,
            child: Scrollbar(
              controller: _scrollController,
              thumbVisibility: true,
              child: ListView(
                controller: _scrollController,
                physics: const AlwaysScrollableScrollPhysics(),
                padding: const EdgeInsets.fromLTRB(8, 8, 8, 12),
                children: [
                  EmberReveal(
                    child: Container(
                      constraints: BoxConstraints(
                        minHeight: MediaQuery.sizeOf(context).height - 82,
                      ),
                      decoration: BoxDecoration(
                        color: EmberTheme.panel,
                        border: Border.all(color: EmberTheme.border),
                      ),
                      padding: const EdgeInsets.fromLTRB(18, 18, 16, 14),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          for (var index = 0; index < items.length; index++)
                            EmberReveal(
                              delay: Duration(
                                milliseconds: 80 + index * 70,
                              ),
                              offset: 0.02,
                              child: _TimelineEntry(
                                text: items[index],
                                isLast: index == items.length - 1,
                              ),
                            ),
                        ],
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _TimelineEntry extends StatelessWidget {
  const _TimelineEntry({
    required this.text,
    required this.isLast,
  });

  final String text;
  final bool isLast;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return IntrinsicHeight(
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SizedBox(
            width: 24,
            child: Column(
              children: [
                Container(
                  width: 11,
                  height: 11,
                  margin: const EdgeInsets.only(top: 4),
                  decoration: const BoxDecoration(
                    color: EmberTheme.accent,
                    shape: BoxShape.circle,
                  ),
                ),
                if (!isLast)
                  Expanded(
                    child: Padding(
                      padding: const EdgeInsets.symmetric(vertical: 5),
                      child: Container(
                        width: 2,
                        color: colors.outline.withOpacity(0.45),
                      ),
                    ),
                  ),
              ],
            ),
          ),
          Expanded(
            child: Padding(
              padding: EdgeInsets.fromLTRB(10, 0, 8, isLast ? 4 : 26),
              child: Text(
                text,
                style: Theme.of(context).textTheme.bodyLarge?.copyWith(
                      height: 1.5,
                      color: Theme.of(context).colorScheme.onSurface,
                    ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
