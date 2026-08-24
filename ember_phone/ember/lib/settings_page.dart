import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'animations.dart';

class EmberSettingsPage extends StatefulWidget {
  const EmberSettingsPage({required this.initialConfig, super.key});

  final Map<String, dynamic> initialConfig;

  @override
  State<EmberSettingsPage> createState() => _EmberSettingsPageState();
}

class _EmberSettingsPageState extends State<EmberSettingsPage> {
  static const _channel = MethodChannel('com.ember.companion/core');

  final Map<String, TextEditingController> _controllers = {};
  bool _stateUpdatesEnabled = true;
  bool _imageGenerationEnabled = false;
  bool _graphMemoryEnabled = true;
  bool _timeFlowEnabled = true;
  bool _saving = false;

  static const _textFields = [
    'base_url',
    'model',
    'character_name',
    'user_name',
    'persona',
    'small_base_url',
    'small_model',
    'embedding_base_url',
    'embedding_model',
    'image_generation_base_url',
    'image_generation_model',
    'temperature',
    'heartbeat_interval',
    'time_accel_factor',
    'state_idle_min_timeout',
    'state_idle_max_timeout',
    'context_window_size',
    'state_update_interval',
    'memory_encode_threshold',
    'memory_keep_last_lines',
    'memory_decay_factor',
    'recall_top_k',
    'api_key',
    'small_api_key',
    'embedding_api_key',
    'image_generation_api_key',
  ];

  @override
  void initState() {
    super.initState();
    for (final field in _textFields) {
      final isSecret = field.endsWith('api_key');
      _controllers[field] = TextEditingController(
        text: isSecret ? '' : widget.initialConfig[field]?.toString() ?? '',
      );
    }
    _stateUpdatesEnabled =
        widget.initialConfig['state_updates_enabled'] != false;
    _imageGenerationEnabled =
        widget.initialConfig['image_generation_enabled'] == true;
    _graphMemoryEnabled = widget.initialConfig['graph_memory_enabled'] != false;
    _timeFlowEnabled = widget.initialConfig['time_flow_enabled'] != false;
  }

  @override
  void dispose() {
    for (final controller in _controllers.values) {
      controller.dispose();
    }
    super.dispose();
  }

  TextEditingController _controller(String field) => _controllers[field]!;

  Widget _field(
    String field,
    String label, {
    bool secret = false,
    bool number = false,
    int minLines = 1,
    int maxLines = 1,
    String? helper,
  }) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: TextField(
        controller: _controller(field),
        obscureText: secret,
        autocorrect: !secret,
        enableSuggestions: !secret,
        keyboardType: number
            ? const TextInputType.numberWithOptions(decimal: true)
            : maxLines > 1
                ? TextInputType.multiline
                : TextInputType.text,
        minLines: secret ? 1 : minLines,
        maxLines: secret ? 1 : maxLines,
        decoration: InputDecoration(
          labelText: label,
          helperText: helper,
          border: const OutlineInputBorder(),
        ),
      ),
    );
  }

  Widget _section(String title, List<Widget> children) {
    return Card(
      margin: const EdgeInsets.only(bottom: 14),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 14),
            ...children,
          ],
        ),
      ),
    );
  }

  String _secretHelper(String configuredFlag) {
    return widget.initialConfig[configuredFlag] == true
        ? '已配置；留空保持原值'
        : '仅保存在应用私有目录';
  }

  Future<void> _save() async {
    setState(() => _saving = true);
    final values = <String, dynamic>{
      for (final field in _textFields)
        if (field != 'persona' ||
            _controller(field).text.trim().isNotEmpty)
          field: _controller(field).text.trim(),
      'state_updates_enabled': _stateUpdatesEnabled,
      'image_generation_enabled': _imageGenerationEnabled,
      'graph_memory_enabled': _graphMemoryEnabled,
      'time_flow_enabled': _timeFlowEnabled,
    };

    try {
      final raw = await _channel.invokeMethod<Object?>(
        'updateAppConfig',
        {'configJson': jsonEncode(values)},
      );
      final decoded = jsonDecode(raw as String);
      if (decoded is! Map) throw const FormatException('设置返回格式错误');
      if (!mounted) return;
      Navigator.pop(context, Map<String, dynamic>.from(decoded));
    } on PlatformException catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(error.message ?? error.code)),
      );
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('保存失败：$error')),
      );
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Ember 设置'),
        actions: [
          TextButton(
            onPressed: _saving ? null : _save,
            child: const Text('保存'),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(14),
        children: [
          EmberReveal(
            child: _section('对话模型', [
              _field('base_url', 'Base URL'),
              _field('model', '对话模型'),
              _field('character_name', '角色名'),
              _field('user_name', '用户称呼'),
              _field(
                'persona',
                '人设（性格/背景/说话风格，留空使用默认）',
                minLines: 4,
                maxLines: 6,
              ),
              _field(
                'api_key',
                '对话 API Key',
                secret: true,
                helper: _secretHelper('api_key_configured'),
              ),
              _field('temperature', '生成温度', number: true),
            ]),
          ),
          EmberReveal(
            delay: const Duration(milliseconds: 60),
            child: _section('状态模型', [
              _field('small_base_url', '状态模型 Base URL'),
              _field('small_model', '状态模型名称'),
              _field(
                'small_api_key',
                '状态模型 API Key',
                secret: true,
                helper: _secretHelper('small_api_key_configured'),
              ),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('对话后更新心理状态'),
                subtitle: const Text('每轮额外调用一次状态模型'),
                value: _stateUpdatesEnabled,
                onChanged: (value) =>
                    setState(() => _stateUpdatesEnabled = value),
              ),
            ]),
          ),
          EmberReveal(
            delay: const Duration(milliseconds: 120),
            child: _section('向量与图片', [
              _field('embedding_base_url', 'Embedding Base URL'),
              _field('embedding_model', 'Embedding 模型'),
              _field(
                'embedding_api_key',
                'Embedding API Key',
                secret: true,
                helper: _secretHelper('embedding_api_key_configured'),
              ),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('启用图片生成'),
                value: _imageGenerationEnabled,
                onChanged: (value) =>
                    setState(() => _imageGenerationEnabled = value),
              ),
              _field('image_generation_base_url', '图片生成 Base URL'),
              _field('image_generation_model', '图片生成模型'),
              _field(
                'image_generation_api_key',
                '图片生成 API Key',
                secret: true,
                helper: _secretHelper('image_generation_api_key_configured'),
              ),
            ]),
          ),
          EmberReveal(
            delay: const Duration(milliseconds: 180),
            child: _section('运行与时间', [
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('允许时间流逝'),
                subtitle: const Text('关闭后逻辑时间与空闲状态演化会冻结'),
                value: _timeFlowEnabled,
                onChanged: (value) => setState(() => _timeFlowEnabled = value),
              ),
              _field('heartbeat_interval', '心跳间隔（秒）', number: true),
              _field('time_accel_factor', '逻辑时间倍率', number: true),
              _field('state_idle_min_timeout', '空闲最小超时（秒）', number: true),
              _field('state_idle_max_timeout', '空闲最大超时（秒）', number: true),
              _field('context_window_size', '上下文消息数', number: true),
              _field('state_update_interval', '状态更新轮次间隔', number: true),
            ]),
          ),
          EmberReveal(
            delay: const Duration(milliseconds: 240),
            child: _section('本地记忆', [
              _field('memory_encode_threshold', '记忆编码阈值', number: true),
              _field('memory_keep_last_lines', '编码后保留行数', number: true),
              _field('memory_decay_factor', '记忆衰减系数', number: true),
              _field('recall_top_k', '召回数量', number: true),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('启用实体关系记忆'),
                subtitle: const Text('手机端存储于 SQLite'),
                value: _graphMemoryEnabled,
                onChanged: (value) =>
                    setState(() => _graphMemoryEnabled = value),
              ),
            ]),
          ),
          if (_saving) const LinearProgressIndicator(),
          const SizedBox(height: 30),
        ],
      ),
    );
  }
}
