import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'animations.dart';

class EmberArchivePage extends StatefulWidget {
  const EmberArchivePage({super.key});

  @override
  State<EmberArchivePage> createState() => _EmberArchivePageState();
}

class _EmberArchivePageState extends State<EmberArchivePage> {
  static const _channel = MethodChannel('com.ember.companion/core');

  final _nameController = TextEditingController();
  List<Map<String, dynamic>> _archives = const [];
  bool _busy = true;
  bool _creating = false;
  String? _confirmingLoadId;
  String? _confirmingDeleteId;
  String? _error;

  Map<String, dynamic>? get _initialArchive {
    for (final archive in _archives) {
      if (archive['is_initial'] == true) return archive;
    }
    return null;
  }

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _reload();
    });
  }

  @override
  void dispose() {
    _nameController.dispose();
    super.dispose();
  }

  Future<void> _reload() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final raw = await _channel.invokeMethod<Object?>('listArchives');
      final decoded = jsonDecode(raw as String);
      final archives = <Map<String, dynamic>>[];
      if (decoded is List) {
        for (final item in decoded) {
          if (item is Map) archives.add(Map<String, dynamic>.from(item));
        }
      }
      if (mounted) setState(() => _archives = archives);
    } on PlatformException catch (error) {
      if (mounted) setState(() => _error = error.message ?? error.code);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _beginCreate() {
    _nameController.clear();
    setState(() {
      _creating = true;
      _confirmingLoadId = null;
      _confirmingDeleteId = null;
    });
  }

  Future<void> _create() async {
    final name = _nameController.text.trim();
    setState(() => _creating = false);
    await _runArchiveAction(
      () => _channel.invokeMethod<Object?>('createArchive', {'name': name}),
    );
    await _reload();
  }

  void _requestLoad(Map<String, dynamic> archive) {
    setState(() {
      _confirmingLoadId = archive['id']?.toString();
      _confirmingDeleteId = null;
      _creating = false;
    });
  }

  Future<void> _load(Map<String, dynamic> archive) async {
    final success = await _runArchiveAction(
      () => _channel.invokeMethod<Object?>('loadArchive', {'id': archive['id']}),
    );
    if (success && mounted) Navigator.pop(context, true);
  }

  void _requestDelete(Map<String, dynamic> archive) {
    setState(() {
      _confirmingDeleteId = archive['id']?.toString();
      _confirmingLoadId = null;
      _creating = false;
    });
  }

  Future<void> _delete(Map<String, dynamic> archive) async {
    setState(() => _confirmingDeleteId = null);
    await _runArchiveAction(
      () => _channel.invokeMethod<Object?>('deleteArchive', {'id': archive['id']}),
    );
    await _reload();
  }

  Future<bool> _runArchiveAction(Future<Object?> Function() action) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await action();
      return true;
    } on PlatformException catch (error) {
      if (mounted) setState(() => _error = error.message ?? error.code);
      return false;
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('存档'),
        actions: [
          TextButton.icon(
            onPressed: _busy || _initialArchive == null
                ? null
                : () => _requestLoad(_initialArchive!),
            icon: const Icon(Icons.restart_alt, size: 17),
            label: const Text('重置'),
          ),
          IconButton(
            tooltip: '创建存档',
            onPressed: _busy ? null : _beginCreate,
            icon: const Icon(Icons.add),
          ),
        ],
      ),
      body: Column(
        children: [
          if (_busy) const LinearProgressIndicator(minHeight: 2),
          if (_creating)
            Card(
              margin: const EdgeInsets.fromLTRB(12, 10, 12, 2),
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: Column(
                  children: [
                    TextField(
                      controller: _nameController,
                      autofocus: true,
                      maxLength: 40,
                      decoration: const InputDecoration(
                        labelText: '存档名称',
                        hintText: '例如：图书馆的下午',
                      ),
                      onSubmitted: (_) => _create(),
                    ),
                    Row(
                      mainAxisAlignment: MainAxisAlignment.end,
                      children: [
                        TextButton(
                          onPressed: () => setState(() => _creating = false),
                          child: const Text('取消'),
                        ),
                        const SizedBox(width: 8),
                        FilledButton(
                          onPressed: _create,
                          child: const Text('保存'),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
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
            child: _archives.isEmpty && !_busy
                ? const Center(child: Text('还没有存档'))
                : ListView.separated(
                    padding: const EdgeInsets.all(12),
                    itemCount: _archives.length,
                    separatorBuilder: (_, _) => const SizedBox(height: 8),
                    itemBuilder: (context, index) {
                      final archive = _archives[index];
                      final preview = archive['preview'] is Map
                          ? Map<String, dynamic>.from(archive['preview'])
                          : const <String, dynamic>{};
                      final id = archive['id']?.toString();
                      final confirmingLoad = _confirmingLoadId == id;
                      final confirmingDelete = _confirmingDeleteId == id;
                      final isInitial = archive['is_initial'] == true;
                      return EmberReveal(
                        delay: Duration(
                          milliseconds: (index * 50).clamp(0, 400).toInt(),
                        ),
                        child: Card(
                          child: Column(
                            children: [
                              ListTile(
                                title: Text(
                                  archive['name']?.toString() ?? '未命名存档',
                                ),
                                subtitle: Text(
                                  '${archive['logical_time'] ?? ''}\n'
                                  '${preview['location'] ?? ''} · ${preview['action'] ?? ''}  '
                                  'P${preview['P'] ?? 5} A${preview['A'] ?? 5} D${preview['D'] ?? 5}',
                                ),
                                isThreeLine: true,
                                onTap: _busy ? null : () => _requestLoad(archive),
                                trailing: isInitial
                                    ? const Icon(Icons.lock_outline)
                                    : IconButton(
                                        tooltip: '删除',
                                        onPressed: _busy
                                            ? null
                                            : () => _requestDelete(archive),
                                        icon: const Icon(Icons.delete_outline),
                                      ),
                              ),
                              if (confirmingLoad || confirmingDelete)
                                Padding(
                                  padding: const EdgeInsets.fromLTRB(16, 0, 12, 10),
                                  child: Row(
                                    children: [
                                      Expanded(
                                        child: Text(
                                          confirmingLoad
                                              ? isInitial
                                                  ? '确认重置到初始存档？当前对话与记忆会被清除。'
                                                  : '确认恢复到这个存档？'
                                              : '确认永久删除这个存档？',
                                        ),
                                      ),
                                      TextButton(
                                        onPressed: () => setState(() {
                                          _confirmingLoadId = null;
                                          _confirmingDeleteId = null;
                                        }),
                                        child: const Text('取消'),
                                      ),
                                      FilledButton.tonal(
                                        onPressed: confirmingLoad
                                            ? () => _load(archive)
                                            : () => _delete(archive),
                                        child: Text(
                                          confirmingLoad
                                              ? isInitial
                                                  ? '重置'
                                                  : '加载'
                                              : '删除',
                                        ),
                                      ),
                                    ],
                                  ),
                                ),
                            ],
                          ),
                        ),
                      );
                    },
                  ),
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _busy || _creating ? null : _beginCreate,
        icon: const Icon(Icons.save_outlined),
        label: const Text('新建存档'),
      ),
    );
  }
}
