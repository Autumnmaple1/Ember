import 'package:flutter/foundation.dart';
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';

class EmberLive2DView extends StatefulWidget {
  const EmberLive2DView({super.key});

  static const viewType = 'com.ember.companion/live2d';

  @override
  State<EmberLive2DView> createState() => _EmberLive2DViewState();
}

class _EmberLive2DViewState extends State<EmberLive2DView>
    with WidgetsBindingObserver {
  int _generation = 0;
  bool _leftForeground = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      if (_leftForeground && mounted) {
        setState(() {
          _generation += 1;
          _leftForeground = false;
        });
      }
    } else {
      _leftForeground = true;
    }
  }

  @override
  Widget build(BuildContext context) {
    if (defaultTargetPlatform != TargetPlatform.android) {
      return const Center(
        child: Text('原生Live2D当前仅支持Android'),
      );
    }

    return AndroidView(
      key: ValueKey<int>(_generation),
      viewType: EmberLive2DView.viewType,
      layoutDirection: TextDirection.ltr,
      gestureRecognizers: <Factory<OneSequenceGestureRecognizer>>{
        Factory<OneSequenceGestureRecognizer>(EagerGestureRecognizer.new),
      },
    );
  }
}
