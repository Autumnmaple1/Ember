import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'animations.dart';
import 'ember_theme.dart';

class EmberStatePage extends StatefulWidget {
  const EmberStatePage({required this.initialSnapshot, super.key});

  final Map<String, dynamic> initialSnapshot;

  @override
  State<EmberStatePage> createState() => _EmberStatePageState();
}

class _EmberStatePageState extends State<EmberStatePage>
    with WidgetsBindingObserver {
  static const _channel = MethodChannel('com.ember.companion/core');

  late Map<String, dynamic> _snapshot;
  Timer? _refreshTimer;
  bool _refreshing = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _snapshot = Map<String, dynamic>.from(widget.initialSnapshot);
    _refreshTimer = Timer.periodic(
      const Duration(seconds: 2),
      (_) => _refresh(silent: true),
    );
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _refresh(silent: true);
  }

  Future<void> _refresh({bool silent = false}) async {
    if (_refreshing) return;
    _refreshing = true;
    if (!silent && mounted) setState(() => _error = null);
    try {
      final raw = await _channel.invokeMethod<Object?>('engineStatus');
      final decoded = jsonDecode(raw as String);
      if (decoded is! Map) throw const FormatException('状态数据格式错误');
      if (!mounted) return;
      setState(() {
        _snapshot = Map<String, dynamic>.from(decoded);
        _error = null;
      });
    } on PlatformException catch (error) {
      if (!silent && mounted) {
        setState(() => _error = error.message ?? error.code);
      }
    } catch (error) {
      if (!silent && mounted) setState(() => _error = error.toString());
    } finally {
      _refreshing = false;
    }
  }

  Map<String, dynamic> get _state {
    final value = _snapshot['state'];
    return value is Map
        ? Map<String, dynamic>.from(value)
        : const <String, dynamic>{};
  }

  @override
  Widget build(BuildContext context) {
    final state = _state;
    return Scaffold(
      appBar: AppBar(
        title: const Text('依鸣的状态'),
        actions: [
          IconButton(
            tooltip: '刷新',
            onPressed: _refreshing ? null : _refresh,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: Stack(
        children: [
          const Positioned.fill(child: EmberPageBackground()),
          RefreshIndicator(
            onRefresh: _refresh,
            child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.fromLTRB(8, 8, 8, 16),
              children: [
                if (_error != null)
                  EmberReveal(
                    child: Card(
                      color: Theme.of(context).colorScheme.errorContainer,
                      child: Padding(
                        padding: const EdgeInsets.all(12),
                        child: Text(_error!),
                      ),
                    ),
                  ),
                EmberReveal(
                  child: _TimeCard(snapshot: _snapshot, state: state),
                ),
                const SizedBox(height: 7),
                EmberReveal(
                  delay: const Duration(milliseconds: 60),
                  child: _PadCard(state: state),
                ),
                const SizedBox(height: 7),
                EmberReveal(
                  delay: const Duration(milliseconds: 120),
                  child: _StateSection(
                    title: '此刻',
                    icon: Icons.psychology_alt_outlined,
                    children: [
                      _DetailItem(
                        label: '客观情境',
                        value: _text(state, '客观情境', '暂时没有情境记录。'),
                      ),
                      _DetailItem(
                        label: '内心活动',
                        value: _text(state, '内心活动', '安静地整理着思绪。'),
                        emphasized: true,
                      ),
                      _DetailItem(
                        label: '近期目标',
                        value: _text(state, '近期目标', '等待下一次互动。'),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 7),
                EmberReveal(
                  delay: const Duration(milliseconds: 180),
                  child: _StateSection(
                    title: '当前行动',
                    icon: Icons.location_on_outlined,
                    children: [
                      _DetailItem(
                        label: '位置',
                        value: _text(state, '当前位置', '未知'),
                      ),
                      _DetailItem(
                        label: '行为',
                        value: _text(state, '当前行为', '未知'),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 7),
                EmberReveal(
                  delay: const Duration(milliseconds: 240),
                  child: _TimelineCard(
                    raw: state['近期综合轨迹']?.toString() ?? '',
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  static String _text(
    Map<String, dynamic> state,
    String key,
    String fallback,
  ) {
    final value = state[key]?.toString().trim() ?? '';
    return value.isEmpty ? fallback : value;
  }
}

class _TimeCard extends StatelessWidget {
  const _TimeCard({required this.snapshot, required this.state});

  final Map<String, dynamic> snapshot;
  final Map<String, dynamic> state;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final running = snapshot['running'] == true;
    final timeFlowEnabled = snapshot['time_flow_enabled'] != false;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Row(
          children: [
            Container(
              width: 44,
              height: 44,
              decoration: BoxDecoration(
                color: colors.primaryContainer,
                shape: BoxShape.circle,
              ),
              child: const Icon(Icons.schedule),
            ),
            const SizedBox(width: 9),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    snapshot['logical_time']?.toString() ??
                        state['对应时间']?.toString() ??
                        '正在同步时间',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  const SizedBox(height: 3),
                  Text(
                    '逻辑时间 · ${timeFlowEnabled ? '流逝中' : '已冻结'} · '
                    '${snapshot['time_accel_factor'] ?? 1}×',
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ],
              ),
            ),
            Chip(
              avatar: Icon(
                Icons.circle,
                size: 10,
                color: running ? const Color(0xFF62C77B) : colors.error,
              ),
              label: Text(running ? '运行中' : '未运行'),
            ),
          ],
        ),
      ),
    );
  }
}

class _PadCard extends StatelessWidget {
  const _PadCard({required this.state});

  final Map<String, dynamic> state;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.insights_outlined, size: 20),
                const SizedBox(width: 8),
                Text(
                  'PAD 情绪状态',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
              ],
            ),
            const SizedBox(height: 10),
            _PadLine(label: '愉悦 P', value: _number(state['P'])),
            const SizedBox(height: 8),
            _PadLine(label: '唤醒 A', value: _number(state['A'])),
            const SizedBox(height: 8),
            _PadLine(label: '支配 D', value: _number(state['D'])),
          ],
        ),
      ),
    );
  }

  static double _number(Object? value) {
    final result = value is num
        ? value.toDouble()
        : double.tryParse(value?.toString() ?? '') ?? 5.0;
    return result.clamp(0.0, 10.0).toDouble();
  }
}

class _PadLine extends StatelessWidget {
  const _PadLine({required this.label, required this.value});

  final String label;
  final double value;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        SizedBox(width: 58, child: Text(label)),
        Expanded(
          child: LinearProgressIndicator(value: value / 10, minHeight: 7),
        ),
        const SizedBox(width: 10),
        SizedBox(
          width: 30,
          child: Text(value.toStringAsFixed(1), textAlign: TextAlign.right),
        ),
      ],
    );
  }
}

class _StateSection extends StatelessWidget {
  const _StateSection({
    required this.title,
    required this.icon,
    required this.children,
  });

  final String title;
  final IconData icon;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(icon, size: 20),
                const SizedBox(width: 8),
                Text(title, style: Theme.of(context).textTheme.titleMedium),
              ],
            ),
            const SizedBox(height: 9),
            ...children,
          ],
        ),
      ),
    );
  }
}

class _DetailItem extends StatelessWidget {
  const _DetailItem({
    required this.label,
    required this.value,
    this.emphasized = false,
  });

  final String label;
  final String value;
  final bool emphasized;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.only(bottom: 9),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label,
            style: Theme.of(context).textTheme.labelMedium?.copyWith(
                  color: colors.primary,
                ),
          ),
          const SizedBox(height: 4),
          Text(
            emphasized ? '“$value”' : value,
            style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                  fontStyle: emphasized ? FontStyle.italic : null,
                  height: 1.45,
                ),
          ),
        ],
      ),
    );
  }
}

class _TimelineCard extends StatelessWidget {
  const _TimelineCard({required this.raw});

  final String raw;

  @override
  Widget build(BuildContext context) {
    final items = raw
        .split('->')
        .map((item) => item.trim())
        .where((item) => item.isNotEmpty)
        .toList();
    if (items.isEmpty) items.add('还没有形成近期轨迹。');

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.timeline, size: 20),
                const SizedBox(width: 8),
                Text(
                  '近期综合轨迹',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
              ],
            ),
            const SizedBox(height: 10),
            for (var index = 0; index < items.length; index++)
              _TimelineItem(
                text: items[index],
                isLast: index == items.length - 1,
              ),
          ],
        ),
      ),
    );
  }
}

class _TimelineItem extends StatelessWidget {
  const _TimelineItem({required this.text, required this.isLast});

  final String text;
  final bool isLast;

  @override
  Widget build(BuildContext context) {
    final color = Theme.of(context).colorScheme.primary;
    return IntrinsicHeight(
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SizedBox(
            width: 22,
            child: Column(
              children: [
                Container(
                  width: 10,
                  height: 10,
                  decoration: BoxDecoration(
                    color: color,
                    shape: BoxShape.circle,
                  ),
                ),
                if (!isLast)
                  Expanded(
                    child: Container(
                      width: 2,
                      color: color.withOpacity(0.35),
                    ),
                  ),
              ],
            ),
          ),
          Expanded(
            child: Padding(
              padding: EdgeInsets.only(left: 8, bottom: isLast ? 0 : 18),
              child: Text(text, style: const TextStyle(height: 1.4)),
            ),
          ),
        ],
      ),
    );
  }
}
