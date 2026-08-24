import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'animations.dart';
import 'chat_page.dart';
import 'ember_theme.dart';

/// 首次启动引导：选择默认设定或自定义（人设/名字/初始存档），
/// 自定义时可让 AI 辅助生成初始状态。
class EmberSetupPage extends StatefulWidget {
  const EmberSetupPage({super.key});

  @override
  State<EmberSetupPage> createState() => _EmberSetupPageState();
}

class _EmberSetupPageState extends State<EmberSetupPage> {
  static const _channel = MethodChannel('com.ember.companion/core');

  final _name = TextEditingController(text: '依鸣');
  final _user = TextEditingController(text: '用户');
  final _persona = TextEditingController();
  final _scene = TextEditingController();
  final _inner = TextEditingController();
  final _goal = TextEditingController();
  final _trajectory = TextEditingController();

  bool _custom = false;
  bool _loading = true;
  bool _saving = false;
  bool _generating = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadDefaults();
  }

  @override
  void dispose() {
    _name.dispose();
    _user.dispose();
    _persona.dispose();
    _scene.dispose();
    _inner.dispose();
    _goal.dispose();
    _trajectory.dispose();
    super.dispose();
  }

  Future<void> _loadDefaults() async {
    try {
      final raw = await _channel.invokeMethod<Object?>('getInitialSetup');
      final decoded = jsonDecode(raw as String);
      if (decoded is Map) {
        final state = decoded['state'];
        if (state is Map) {
          _scene.text = state['客观情境']?.toString() ?? '';
          _inner.text = state['内心活动']?.toString() ?? '';
          _goal.text = state['近期目标']?.toString() ?? '';
          _trajectory.text = state['近期综合轨迹']?.toString() ?? '';
        }
        _name.text = decoded['character_name']?.toString() ?? '依鸣';
        _user.text = decoded['user_name']?.toString() ?? '用户';
        _persona.text = decoded['persona']?.toString() ?? '';
      }
    } catch (_) {
      // 使用默认值即可。
    }
    if (mounted) setState(() => _loading = false);
  }

  Future<void> _generate() async {
    final persona = _persona.text.trim();
    if (persona.isEmpty) {
      setState(() => _error = '先填写人设，再使用 AI 生成初始存档');
      return;
    }
    setState(() {
      _generating = true;
      _error = null;
    });
    try {
      final raw = await _channel.invokeMethod<Object?>(
        'generateInitialState',
        {
          'persona': persona,
          'characterName': _name.text.trim(),
          'sceneHint': _scene.text.trim(),
        },
      );
      final decoded = jsonDecode(raw as String);
      if (decoded is Map && mounted) {
        setState(() {
          if (decoded['客观情境'] != null) {
            _scene.text = decoded['客观情境'].toString();
          }
          if (decoded['内心活动'] != null) {
            _inner.text = decoded['内心活动'].toString();
          }
          if (decoded['近期目标'] != null) {
            _goal.text = decoded['近期目标'].toString();
          }
          if (decoded['近期综合轨迹'] != null) {
            _trajectory.text = decoded['近期综合轨迹'].toString();
          }
        });
      }
    } on PlatformException catch (error) {
      if (mounted) setState(() => _error = error.message ?? '生成失败');
    } catch (error) {
      if (mounted) setState(() => _error = error.toString());
    } finally {
      if (mounted) setState(() => _generating = false);
    }
  }

  Future<void> _finish() async {
    setState(() {
      _saving = true;
      _error = null;
    });
    final config = <String, dynamic>{
      'character_name': _name.text.trim().isEmpty ? '依鸣' : _name.text.trim(),
      'user_name': _user.text.trim().isEmpty ? '用户' : _user.text.trim(),
      'onboarding_completed': true,
      if (_persona.text.trim().isNotEmpty) 'persona': _persona.text.trim(),
    };
    final state = <String, dynamic>{
      if (_custom) ...{
        if (_scene.text.trim().isNotEmpty) '客观情境': _scene.text.trim(),
        if (_inner.text.trim().isNotEmpty) '内心活动': _inner.text.trim(),
        if (_goal.text.trim().isNotEmpty) '近期目标': _goal.text.trim(),
        if (_trajectory.text.trim().isNotEmpty) '近期综合轨迹': _trajectory.text.trim(),
      },
    };
    try {
      await _channel.invokeMethod<Object?>('saveInitialSetup', {
        'configJson': jsonEncode(config),
        'stateJson': jsonEncode(state),
      });
      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const EmberChatPage()),
      );
    } on PlatformException catch (error) {
      if (mounted) {
        setState(() {
          _error = error.message ?? '保存失败';
          _saving = false;
        });
      }
    } catch (error) {
      if (mounted) {
        setState(() {
          _error = error.toString();
          _saving = false;
        });
      }
    }
  }

  Widget _field(
    String label,
    TextEditingController controller, {
    int lines = 1,
    String? hint,
  }) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: TextField(
        controller: controller,
        minLines: lines,
        maxLines: lines,
        decoration: InputDecoration(
          labelText: label,
          hintText: hint,
          alignLabelWithHint: true,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Stack(
        children: [
          const Positioned.fill(child: EmberPageBackground()),
          SafeArea(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : ListView(
                    padding: const EdgeInsets.fromLTRB(16, 28, 16, 24),
                    children: [
                      EmberReveal(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              '欢迎来到 Ember',
                              style: Theme.of(context).textTheme.headlineSmall,
                            ),
                            const SizedBox(height: 6),
                            Text(
                              '先决定用默认设定，还是从零塑造属于你的依鸣。',
                              style: Theme.of(context).textTheme.bodyMedium,
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(height: 20),
                      EmberReveal(
                        delay: const Duration(milliseconds: 80),
                        child: Row(
                          children: [
                            Expanded(
                              child: _ChoiceCard(
                                selected: !_custom,
                                title: '使用默认设定',
                                subtitle: '依鸣原本的初始人设与场景',
                                icon: Icons.auto_stories_outlined,
                                onTap: () => setState(() => _custom = false),
                              ),
                            ),
                            const SizedBox(width: 10),
                            Expanded(
                              child: _ChoiceCard(
                                selected: _custom,
                                title: '自定义设定',
                                subtitle: '自己填写名字、人设与初始情境',
                                icon: Icons.edit_outlined,
                                onTap: () => setState(() => _custom = true),
                              ),
                            ),
                          ],
                        ),
                      ),
                      if (_custom) ...[
                        const SizedBox(height: 18),
                        EmberReveal(
                          delay: const Duration(milliseconds: 120),
                          child: _field('角色名', _name),
                        ),
                        EmberReveal(
                          delay: const Duration(milliseconds: 150),
                          child: _field('用户称呼', _user),
                        ),
                        EmberReveal(
                          delay: const Duration(milliseconds: 180),
                          child: _field(
                            '人设（性格、背景、说话风格…）',
                            _persona,
                            lines: 5,
                          ),
                        ),
                        const SizedBox(height: 4),
                        EmberReveal(
                          delay: const Duration(milliseconds: 220),
                          child: Align(
                            alignment: Alignment.centerLeft,
                            child: FilledButton.icon(
                              onPressed: _generating ? null : _generate,
                              icon: _generating
                                  ? const SizedBox.square(
                                      dimension: 16,
                                      child: CircularProgressIndicator(
                                        strokeWidth: 2,
                                        color: Colors.white,
                                      ),
                                    )
                                  : const Icon(Icons.auto_awesome, size: 18),
                              label: Text(
                                _generating
                                    ? '生成中…'
                                    : 'AI 辅助生成初始存档',
                              ),
                            ),
                          ),
                        ),
                        const SizedBox(height: 6),
                        EmberReveal(
                          delay: const Duration(milliseconds: 240),
                          child: Text(
                            '根据人设生成：初始情境 / 内心活动 / 近期目标 / 近期综合轨迹。'
                            '生成后可以继续手动修改。',
                            style: Theme.of(context).textTheme.bodySmall,
                          ),
                        ),
                        const SizedBox(height: 16),
                        EmberReveal(
                          delay: const Duration(milliseconds: 260),
                          child: _field('初始情境（客观情境）', _scene, lines: 3),
                        ),
                        EmberReveal(
                          delay: const Duration(milliseconds: 300),
                          child: _field('内心活动', _inner, lines: 3),
                        ),
                        EmberReveal(
                          delay: const Duration(milliseconds: 340),
                          child: _field('近期目标', _goal, lines: 2),
                        ),
                        EmberReveal(
                          delay: const Duration(milliseconds: 380),
                          child: _field('近期综合轨迹（用 -> 连接）', _trajectory, lines: 2),
                        ),
                      ],
                      if (_error != null) ...[
                        const SizedBox(height: 12),
                        Text(
                          _error!,
                          style: TextStyle(
                            color: Theme.of(context).colorScheme.error,
                          ),
                        ),
                      ],
                      const SizedBox(height: 24),
                      EmberReveal(
                        delay: const Duration(milliseconds: 420),
                        child: FilledButton(
                          onPressed: _saving ? null : _finish,
                          style: FilledButton.styleFrom(
                            minimumSize: const Size.fromHeight(50),
                          ),
                          child: Text(_saving ? '保存中…' : '开始对话'),
                        ),
                      ),
                    ],
                  ),
          ),
        ],
      ),
    );
  }
}

class _ChoiceCard extends StatelessWidget {
  const _ChoiceCard({
    required this.selected,
    required this.title,
    required this.subtitle,
    required this.icon,
    required this.onTap,
  });

  final bool selected;
  final String title;
  final String subtitle;
  final IconData icon;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(0),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 220),
        curve: Curves.easeOutCubic,
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: selected
              ? colors.primary.withValues(alpha: 0.14)
              : Colors.white.withValues(alpha: 0.5),
          border: Border.all(
            color: selected
                ? colors.primary.withValues(alpha: 0.7)
                : EmberTheme.border,
            width: selected ? 1.4 : 1,
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(icon, size: 26, color: colors.primary),
            const SizedBox(height: 8),
            Text(title, style: Theme.of(context).textTheme.titleSmall),
            const SizedBox(height: 4),
            Text(
              subtitle,
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ],
        ),
      ),
    );
  }
}
