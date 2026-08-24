import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'chat_page.dart';
import 'ember_theme.dart';
import 'live2d_view.dart';
import 'setup_page.dart';

void main() {
  runApp(const EmberApp());
}

class EmberApp extends StatelessWidget {
  const EmberApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Ember',
      debugShowCheckedModeBanner: false,
      theme: EmberTheme.light,
      home: const EmberRoot(),
      builder: (context, child) => Stack(
        fit: StackFit.expand,
        children: [
          const EmberPageBackground(),
          ?child,
        ],
      ),
    );
  }
}

/// 应用入口：先确保本地核心启动，再决定进首次引导还是聊天页。
class EmberRoot extends StatefulWidget {
  const EmberRoot({super.key});

  @override
  State<EmberRoot> createState() => _EmberRootState();
}

class _EmberRootState extends State<EmberRoot> {
  static const _channel = MethodChannel('com.ember.companion/core');

  bool _loading = true;
  bool _needOnboarding = false;

  @override
  void initState() {
    super.initState();
    _check();
  }

  Future<void> _check() async {
    try {
      await _channel.invokeMethod<Object?>('startEngine');
      Object? setup;
      for (var attempt = 0; attempt < 15; attempt++) {
        await Future<void>.delayed(const Duration(milliseconds: 300));
        try {
          setup = await _channel.invokeMethod<Object?>('getInitialSetup');
          break;
        } on PlatformException {
          // 本地核心还在启动，稍后重试。
        }
      }
      var onboarding = false;
      if (setup != null) {
        final decoded = jsonDecode(setup as String);
        if (decoded is Map) {
          onboarding = decoded['onboarding_completed'] != true;
        }
      }
      if (mounted) {
        setState(() {
          _needOnboarding = onboarding;
          _loading = false;
        });
      }
    } catch (_) {
      if (mounted) {
        setState(() {
          _needOnboarding = false;
          _loading = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const Scaffold(
        body: Center(child: CircularProgressIndicator()),
      );
    }
    return _needOnboarding ? const EmberSetupPage() : const EmberChatPage();
  }
}

class MigrationDiagnosticsPage extends StatefulWidget {
  const MigrationDiagnosticsPage({super.key});

  @override
  State<MigrationDiagnosticsPage> createState() =>
      _MigrationDiagnosticsPageState();
}

class _MigrationDiagnosticsPageState extends State<MigrationDiagnosticsPage> {
  static const _channel = MethodChannel('com.ember.companion/core');

  String _output = '尚未连接本地核心';
  Map<String, dynamic>? _snapshot;
  bool _busy = false;

  Future<void> _invoke(String method, [Map<String, Object?>? arguments]) async {
    setState(() => _busy = true);
    try {
      final result = await _channel.invokeMethod<Object?>(method, arguments);
      final text = result is String ? _prettyJson(result) : result.toString();
      Map<String, dynamic>? snapshot;
      if (result is String) {
        try {
          final decoded = jsonDecode(result);
          if (decoded is Map<String, dynamic> && decoded['state'] is Map) {
            snapshot = decoded;
          }
        } on FormatException {
          // Non-JSON bridge responses remain visible in the diagnostics card.
        }
      }
      if (mounted) {
        setState(() {
          _output = text;
          if (snapshot != null) _snapshot = snapshot;
        });
      }
    } on PlatformException catch (error) {
      if (mounted) {
        setState(() => _output = '${error.code}: ${error.message}');
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  String _prettyJson(String value) {
    try {
      return const JsonEncoder.withIndent('  ').convert(jsonDecode(value));
    } on FormatException {
      return value;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Ember 迁移诊断')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(20),
          children: [
            const SizedBox(
              height: 360,
              child: ClipRRect(
                borderRadius: BorderRadius.all(Radius.circular(20)),
                child: EmberLive2DView(),
              ),
            ),
            const SizedBox(height: 20),
            Text(
              'Flutter → Kotlin → CPython',
              style: Theme.of(context).textTheme.headlineSmall,
            ),
            const SizedBox(height: 8),
            const Text('此页面用于验证嵌入式Python与前台服务，后续将替换为正式伴侣界面。'),
            if (_snapshot != null) ...[
              const SizedBox(height: 16),
              _StateSnapshotCard(snapshot: _snapshot!),
            ],
            const SizedBox(height: 24),
            Wrap(
              spacing: 12,
              runSpacing: 12,
              children: [
                FilledButton(
                  onPressed: _busy ? null : () => _invoke('pythonInfo'),
                  child: const Text('检查Python'),
                ),
                FilledButton(
                  onPressed: _busy
                      ? null
                      : () => _invoke('echo', {'value': '你好，依鸣'}),
                  child: const Text('测试桥接'),
                ),
                FilledButton(
                  onPressed: _busy ? null : () => _invoke('startEngine'),
                  child: const Text('启动核心'),
                ),
                OutlinedButton(
                  onPressed: _busy ? null : () => _invoke('engineStatus'),
                  child: const Text('刷新状态'),
                ),
                OutlinedButton(
                  onPressed: _busy ? null : () => _invoke('stopEngine'),
                  child: const Text('停止核心'),
                ),
              ],
            ),
            const SizedBox(height: 24),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: SelectableText(_output),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _StateSnapshotCard extends StatelessWidget {
  const _StateSnapshotCard({required this.snapshot});

  final Map<String, dynamic> snapshot;

  @override
  Widget build(BuildContext context) {
    final rawState = snapshot['state'];
    final state = rawState is Map ? rawState : const <String, dynamic>{};
    final running = snapshot['running'] == true;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(
                  running ? Icons.favorite : Icons.favorite_border,
                  color: running ? const Color(0xFFE27052) : null,
                ),
                const SizedBox(width: 8),
                Text(running ? 'Ember 核心运行中' : 'Ember 核心已停止'),
                const Spacer(),
                Text('tick ${snapshot['tick_count'] ?? 0}'),
              ],
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              children: [
                Chip(label: Text('P ${state['P'] ?? '-'}')),
                Chip(label: Text('A ${state['A'] ?? '-'}')),
                Chip(label: Text('D ${state['D'] ?? '-'}')),
              ],
            ),
            const SizedBox(height: 8),
            Text('逻辑时间：${snapshot['logical_time'] ?? '-'}'),
            Text('位置：${state['当前位置'] ?? '-'}'),
            Text('行为：${state['当前行为'] ?? '-'}'),
            const SizedBox(height: 8),
            Text('${state['客观情境'] ?? ''}'),
          ],
        ),
      ),
    );
  }
}
