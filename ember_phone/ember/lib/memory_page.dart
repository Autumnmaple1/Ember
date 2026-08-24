import 'dart:convert';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'animations.dart';
import 'ember_theme.dart';

/// 图数据库 + 结构化记忆查看页。
class EmberMemoryPage extends StatefulWidget {
  const EmberMemoryPage({super.key});

  @override
  State<EmberMemoryPage> createState() => _EmberMemoryPageState();
}

class _EmberMemoryPageState extends State<EmberMemoryPage> {
  static const _channel = MethodChannel('com.ember.companion/core');

  Map<String, dynamic>? _data;
  bool _loading = true;
  String? _error;
  String? _selectedEntity;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _reload());
  }

  Future<void> _reload() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final raw = await _channel.invokeMethod<Object?>('getMemoryOverview');
      final decoded = jsonDecode(raw as String);
      if (decoded is! Map) throw const FormatException('记忆数据格式错误');
      if (!mounted) return;
      setState(() {
        _data = Map<String, dynamic>.from(decoded);
        _loading = false;
      });
    } on PlatformException catch (error) {
      if (mounted) {
        setState(() {
          _error = error.message ?? error.code;
          _loading = false;
        });
      }
    } catch (error) {
      if (mounted) {
        setState(() {
          _error = error.toString();
          _loading = false;
        });
      }
    }
  }

  List<Map<String, dynamic>> _list(String key) {
    final raw = _data?[key];
    if (raw is List) {
      return raw
          .whereType<Map>()
          .map((item) => Map<String, dynamic>.from(item))
          .toList();
    }
    return const [];
  }

  Map<String, dynamic> _stats() {
    final raw = _data?['stats'];
    return raw is Map
        ? Map<String, dynamic>.from(raw)
        : const <String, dynamic>{};
  }

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 2,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('记忆图谱'),
          actions: [
            IconButton(
              tooltip: '刷新',
              onPressed: _loading ? null : _reload,
              icon: const Icon(Icons.refresh),
            ),
          ],
          bottom: const TabBar(
            tabs: [
              Tab(text: '图谱', icon: Icon(Icons.account_tree_outlined)),
              Tab(text: '结构化记忆', icon: Icon(Icons.memory_outlined)),
            ],
          ),
        ),
        body: Stack(
          children: [
            const Positioned.fill(child: EmberPageBackground()),
            Column(
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
                Expanded(child: _buildBody()),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildBody() {
    if (_data == null) {
      return const Center(child: Text('记忆数据加载中…'));
    }
    final entities = _list('entities');
    final relationships = _list('relationships');
    final episodes = _list('episodes');
    final hasContent =
        entities.isNotEmpty || relationships.isNotEmpty || episodes.isNotEmpty;
    if (!hasContent) {
      return Center(
        child: EmberReveal(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.hub_outlined, size: 42, color: Color(0xFF8C7A6D)),
              const SizedBox(height: 12),
              const Text('还没有形成结构化记忆'),
              const SizedBox(height: 4),
              Text(
                '聊过几轮或空闲演化后，记忆会自动沉淀到这里',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
          ),
        ),
      );
    }
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(8, 8, 8, 0),
          child: EmberReveal(
            child: _StatsCard(stats: _stats()),
          ),
        ),
        Expanded(
          child: TabBarView(
            children: [
              _GraphTab(
                entities: entities,
                relationships: relationships,
                selected: _selectedEntity,
                onSelect: (name) => setState(() => _selectedEntity = name),
              ),
              _EpisodesTab(episodes: episodes),
            ],
          ),
        ),
      ],
    );
  }
}

class _StatsCard extends StatelessWidget {
  const _StatsCard({required this.stats});

  final Map<String, dynamic> stats;

  @override
  Widget build(BuildContext context) {
    final items = [
      ('实体', stats['entity_count'] ?? 0),
      ('关系', stats['relationship_count'] ?? 0),
      ('情景记忆', stats['episodic_count'] ?? 0),
      ('待编码', stats['pending_messages'] ?? 0),
    ];
    return Card(
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 10, horizontal: 6),
        child: Row(
          children: [
            for (final item in items)
              Expanded(
                child: Column(
                  children: [
                    Text(
                      '${item.$2}',
                      style: Theme.of(context).textTheme.titleMedium?.copyWith(
                            color: EmberTheme.accent,
                          ),
                    ),
                    const SizedBox(height: 2),
                    Text(item.$1, style: Theme.of(context).textTheme.labelSmall),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _GraphTab extends StatefulWidget {
  const _GraphTab({
    required this.entities,
    required this.relationships,
    required this.selected,
    required this.onSelect,
    super.key,
  });

  final List<Map<String, dynamic>> entities;
  final List<Map<String, dynamic>> relationships;
  final String? selected;
  final ValueChanged<String?> onSelect;

  @override
  State<_GraphTab> createState() => _GraphTabState();
}

class _GraphTabState extends State<_GraphTab> {
  Map<String, Offset> _positions = {};
  Size _lastSize = Size.zero;
  String? _dragNode;

  List<Map<String, dynamic>> get _nodes =>
      widget.entities.take(40).toList();

  List<Map<String, dynamic>> get _edges {
    final nodeNames = _nodes.map((e) => e['name']).toSet();
    return widget.relationships
        .where(
          (r) =>
              nodeNames.contains(r['source']) &&
              nodeNames.contains(r['target']),
        )
        .take(80)
        .toList();
  }

  void _ensurePositions(Size size) {
    if (size != _lastSize || _positions.isEmpty) {
      _lastSize = size;
      _positions = _GraphLayout.initialPositions(size, _nodes);
      return;
    }
    var missing = false;
    for (final node in _nodes) {
      if (!_positions.containsKey(node['name'])) {
        missing = true;
        break;
      }
    }
    if (!missing) return;
    // 增量补充新实体，保留用户拖动过的布局。
    final updated = Map<String, Offset>.of(_positions);
    _GraphLayout.initialPositions(size, _nodes).forEach((name, position) {
      updated.putIfAbsent(name, () => position);
    });
    _positions = updated;
  }

  String? _nodeAt(Offset position) {
    String? best;
    var bestDistance = double.infinity;
    _positions.forEach((name, nodePosition) {
      final distance = (nodePosition - position).distance;
      if (distance < 30 && distance < bestDistance) {
        best = name;
        bestDistance = distance;
      }
    });
    return best;
  }

  @override
  Widget build(BuildContext context) {
    final nodes = _nodes;
    final edges = _edges;
    final selectedEntity =
        nodes.where((e) => e['name'] == widget.selected).firstOrNull;

    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(8, 6, 8, 0),
          child: Text(
            nodes.isEmpty ? '还没有实体，聊过几轮后会自动沉淀' : '拖动节点调整布局 · 点击查看详情',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ),
        Expanded(
          child: Padding(
            padding: const EdgeInsets.all(10),
            child: EmberReveal(
              child: LayoutBuilder(
                builder: (context, constraints) {
                  final size = Size(
                    constraints.maxWidth,
                    constraints.maxHeight,
                  );
                  _ensurePositions(size);
                  return GestureDetector(
                    onPanStart: (details) {
                      final hit = _nodeAt(details.localPosition);
                      if (hit != null) {
                        setState(() => _dragNode = hit);
                      }
                    },
                    onPanUpdate: (details) {
                      final node = _dragNode;
                      if (node == null) return;
                      setState(() {
                        _positions = Map<String, Offset>.of(_positions)
                          ..[node] = Offset(
                            details.localPosition.dx.clamp(0.0, size.width),
                            details.localPosition.dy.clamp(0.0, size.height),
                          );
                      });
                    },
                    onPanEnd: (_) {
                      if (_dragNode != null) {
                        setState(() => _dragNode = null);
                      }
                    },
                    onTapUp: (details) {
                      if (_dragNode != null) return;
                      widget.onSelect(_nodeAt(details.localPosition));
                    },
                    child: CustomPaint(
                      size: size,
                      painter: _GraphPainter(
                        nodes: nodes,
                        edges: edges,
                        selected: widget.selected,
                        positions: _positions,
                        dragNode: _dragNode,
                      ),
                    ),
                  );
                },
              ),
            ),
          ),
        ),
        AnimatedSize(
          duration: const Duration(milliseconds: 260),
          curve: Curves.easeOutCubic,
          alignment: Alignment.topCenter,
          child: selectedEntity == null
              ? Padding(
                  padding: const EdgeInsets.fromLTRB(8, 0, 8, 8),
                  child: Text(
                    nodes.isEmpty
                        ? '还没有实体，聊过几轮后会自动沉淀'
                        : '拖动节点调整布局 · 点击查看详情',
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                )
              : Padding(
                  padding: const EdgeInsets.fromLTRB(8, 0, 8, 8),
                  child: _EntityDetailCard(
                    entity: selectedEntity,
                    edges: edges,
                  ),
                ),
        ),
      ],
    );
  }
}

class _EntityDetailCard extends StatelessWidget {
  const _EntityDetailCard({required this.entity, required this.edges});

  final Map<String, dynamic> entity;
  final List<Map<String, dynamic>> edges;

  @override
  Widget build(BuildContext context) {
    final name = entity['name']?.toString() ?? '未知实体';
    final type = entity['entity_type']?.toString() ?? 'Entity';
    final aliases = _jsonList(entity['aliases_json']);
    final properties = _jsonMap(entity['properties_json']);
    final related = edges.where(
      (edge) => edge['source'] == name || edge['target'] == name,
    );

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(
              children: [
                Container(
                  width: 9,
                  height: 9,
                  decoration: BoxDecoration(
                    color: _typeColor(type),
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 7),
                Expanded(
                  child: Text(
                    name,
                    style: Theme.of(context).textTheme.titleMedium,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                Text(type, style: Theme.of(context).textTheme.labelSmall),
              ],
            ),
            if (aliases.isNotEmpty) ...[
              const SizedBox(height: 6),
              Wrap(
                spacing: 6,
                children: [
                  for (final alias in aliases)
                    Chip(label: Text(alias.toString())),
                ],
              ),
            ],
            if (properties.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(
                properties.entries
                    .map((entry) => '${entry.key}: ${entry.value}')
                    .join('\n'),
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
            if (related.isNotEmpty) ...[
              const SizedBox(height: 8),
              const Divider(),
              for (final edge in related.take(6))
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 2),
                  child: Text(
                    '${edge['source']} —${edge['relation']}→ ${edge['target']}',
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ),
            ],
          ],
        ),
      ),
    );
  }
}

class _EpisodesTab extends StatelessWidget {
  const _EpisodesTab({required this.episodes});

  final List<Map<String, dynamic>> episodes;

  @override
  Widget build(BuildContext context) {
    if (episodes.isEmpty) {
      return Center(
        child: EmberReveal(
          child: Text(
            '还没有情景记忆',
            style: Theme.of(context).textTheme.bodyMedium,
          ),
        ),
      );
    }
    return ListView.builder(
      padding: const EdgeInsets.fromLTRB(8, 8, 8, 16),
      itemCount: episodes.length,
      itemBuilder: (context, index) {
        return EmberReveal(
          delay: Duration(milliseconds: math.min(index * 45, 450)),
          child: _EpisodeCard(episode: episodes[index]),
        );
      },
    );
  }
}

class _EpisodeCard extends StatelessWidget {
  const _EpisodeCard({required this.episode});

  final Map<String, dynamic> episode;

  @override
  Widget build(BuildContext context) {
    final content = episode['content']?.toString() ?? '';
    final insight = episode['insight']?.toString() ?? '';
    final time = episode['occurred_at']?.toString() ?? '';
    final importance = (episode['importance'] as num?)?.toDouble() ?? 1.0;
    final keywords = _jsonList(episode['keywords_json']);
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    time,
                    style: Theme.of(context).textTheme.labelSmall,
                  ),
                ),
                Text(
                  '重要性 ${importance.toStringAsFixed(1)}',
                  style: Theme.of(context).textTheme.labelSmall?.copyWith(
                        color: EmberTheme.accent,
                      ),
                ),
              ],
            ),
            const SizedBox(height: 6),
            Text(content, style: Theme.of(context).textTheme.bodyMedium),
            if (insight.isNotEmpty) ...[
              const SizedBox(height: 6),
              Text(
                '理解：$insight',
                style: Theme.of(context).textTheme.bodySmall?.copyWith(
                      fontStyle: FontStyle.italic,
                    ),
              ),
            ],
            if (keywords.isNotEmpty) ...[
              const SizedBox(height: 8),
              Wrap(
                spacing: 6,
                runSpacing: 4,
                children: [
                  for (final keyword in keywords)
                    Chip(label: Text(keyword.toString())),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// 图布局：把节点按圆周摆放，作为初始位置；之后由用户拖动调整。
class _GraphLayout {
  static Map<String, Offset> initialPositions(
    Size size,
    List<Map<String, dynamic>> nodes,
  ) {
    if (nodes.isEmpty) return const {};
    final center = size.center(Offset.zero);
    final radius = math.min(size.width, size.height) / 2 - 40;
    final result = <String, Offset>{};
    for (var index = 0; index < nodes.length; index++) {
      final angle = -math.pi / 2 + 2 * math.pi * index / nodes.length;
      result[nodes[index]['name'].toString()] =
          center + Offset(math.cos(angle), math.sin(angle)) * radius;
    }
    return result;
  }
}

class _GraphPainter extends CustomPainter {
  _GraphPainter({
    required this.nodes,
    required this.edges,
    required this.selected,
    required this.positions,
    this.dragNode,
  });

  final List<Map<String, dynamic>> nodes;
  final List<Map<String, dynamic>> edges;
  final String? selected;
  final Map<String, Offset> positions;
  final String? dragNode;

  @override
  void paint(Canvas canvas, Size size) {
    if (nodes.isEmpty) return;
    final defaultEdge = Paint()
      ..color = const Color(0x55687986)
      ..strokeWidth = 1.2;
    final accentEdge = Paint()
      ..color = EmberTheme.accent
      ..strokeWidth = 2;
    for (final edge in edges) {
      final source = positions[edge['source']?.toString()];
      final target = positions[edge['target']?.toString()];
      if (source == null || target == null) continue;
      final isRelated =
          edge['source'] == selected || edge['target'] == selected;
      canvas.drawLine(source, target, isRelated ? accentEdge : defaultEdge);
    }

    final labelPainter = TextPainter(
      textDirection: TextDirection.ltr,
    );
    for (final node in nodes) {
      final name = node['name']?.toString() ?? '';
      final position = positions[name];
      if (position == null) continue;
      final isSelected = name == selected;
      final isDragged = name == dragNode;
      final type = node['entity_type']?.toString() ?? 'Entity';
      canvas.drawCircle(
        position,
        isDragged ? 18 : isSelected ? 15 : 12,
        Paint()..color = _typeColor(type),
      );
      if (isSelected) {
        canvas.drawCircle(
          position,
          22,
          Paint()
            ..style = PaintingStyle.stroke
            ..strokeWidth = 2
            ..color = EmberTheme.accent,
        );
      }
      labelPainter
        ..text = TextSpan(
          text: name,
          style: const TextStyle(fontSize: 9, color: Color(0xFF3B2F29)),
        )
        ..layout(maxWidth: 96);
      labelPainter.paint(
        canvas,
        position + Offset(-labelPainter.width / 2, 17),
      );
    }
  }

  @override
  bool shouldRepaint(covariant _GraphPainter oldDelegate) {
    return oldDelegate.nodes != nodes ||
        oldDelegate.edges != edges ||
        oldDelegate.selected != selected ||
        oldDelegate.positions != positions ||
        oldDelegate.dragNode != dragNode;
  }
}

Color _typeColor(String type) {
  const palette = [
    Color(0xFFE8644B),
    Color(0xFFF2A65A),
    Color(0xFF6E9B93),
    Color(0xFF7A9E6E),
    Color(0xFFB07BA1),
    Color(0xFF6E8BB0),
  ];
  final hash = type.codeUnits.fold<int>(0, (sum, unit) => sum + unit);
  return palette[hash % palette.length];
}

List<dynamic> _jsonList(Object? raw) {
  if (raw is List) return raw;
  if (raw is String && raw.isNotEmpty) {
    try {
      final decoded = jsonDecode(raw);
      if (decoded is List) return decoded;
    } on FormatException {
      // 忽略无法解析的字段。
    }
  }
  return const [];
}

Map<String, dynamic> _jsonMap(Object? raw) {
  if (raw is Map) return Map<String, dynamic>.from(raw);
  if (raw is String && raw.isNotEmpty) {
    try {
      final decoded = jsonDecode(raw);
      if (decoded is Map) return Map<String, dynamic>.from(decoded);
    } on FormatException {
      // 忽略无法解析的字段。
    }
  }
  return const {};
}
