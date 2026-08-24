import 'dart:ui';

import 'package:flutter/material.dart';

class EmberTheme {
  static const accent = Color(0xFF4B9CFF);
  static const canvasTop = Color(0xFFF9FBFC);
  static const canvasBottom = Color(0xFFEAF0F4);
  static const panel = Color(0xB8FFFFFF);
  static const panelStrong = Color(0xFFF0F4F7);
  static const border = Color(0x50687986);

  static ThemeData get light {
    final scheme = ColorScheme.fromSeed(
      seedColor: accent,
      brightness: Brightness.light,
    ).copyWith(
      primary: const Color(0xFF247FDB),
      secondary: const Color(0xFF4B9CFF),
      surface: const Color(0xFFF7F9FA),
      surfaceContainerLow: const Color(0xEFFFFFFF),
      surfaceContainerHigh: const Color(0xFFE4EAEF),
      outline: const Color(0xFF687986),
      outlineVariant: border,
    );

    return ThemeData(
      brightness: Brightness.light,
      colorScheme: scheme,
      scaffoldBackgroundColor: const Color(0xB8EAF0F4),
      useMaterial3: true,
      pageTransitionsTheme: const PageTransitionsTheme(
        builders: {
          TargetPlatform.android: EmberPageTransitionsBuilder(),
          TargetPlatform.iOS: EmberPageTransitionsBuilder(),
          TargetPlatform.windows: EmberPageTransitionsBuilder(),
          TargetPlatform.macOS: EmberPageTransitionsBuilder(),
          TargetPlatform.linux: EmberPageTransitionsBuilder(),
          TargetPlatform.fuchsia: EmberPageTransitionsBuilder(),
        },
      ),
      appBarTheme: const AppBarTheme(
        backgroundColor: Color(0xA8FFFFFF),
        foregroundColor: Color(0xFF17232C),
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        toolbarHeight: 44,
        titleSpacing: 10,
        actionsPadding: EdgeInsets.symmetric(horizontal: 2),
        titleTextStyle: TextStyle(
          color: Color(0xFF17232C),
          fontSize: 18,
          fontWeight: FontWeight.w600,
        ),
      ),
      cardTheme: CardThemeData(
        color: panel,
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: const RoundedRectangleBorder(
          side: BorderSide(color: border),
        ),
      ),
      dividerTheme: const DividerThemeData(color: border, thickness: 1),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: const Color(0xB8FFFFFF),
        border: const OutlineInputBorder(
          borderSide: BorderSide(color: border),
        ),
        enabledBorder: const OutlineInputBorder(
          borderSide: BorderSide(color: border),
        ),
        focusedBorder: const OutlineInputBorder(
          borderSide: BorderSide(color: accent, width: 1.4),
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: accent,
          foregroundColor: Colors.white,
          shape: const RoundedRectangleBorder(),
        ),
      ),
      iconButtonTheme: IconButtonThemeData(
        style: IconButton.styleFrom(
          foregroundColor: const Color(0xFF34444F),
          minimumSize: const Size(36, 36),
          padding: const EdgeInsets.all(6),
          visualDensity: VisualDensity.compact,
          shape: const RoundedRectangleBorder(),
        ),
      ),
      floatingActionButtonTheme: const FloatingActionButtonThemeData(
        shape: RoundedRectangleBorder(),
      ),
      chipTheme: ChipThemeData(
        shape: const RoundedRectangleBorder(
          side: BorderSide(color: border),
        ),
        padding: const EdgeInsets.symmetric(horizontal: 4),
        backgroundColor: panelStrong,
      ),
      textTheme: const TextTheme(
        titleLarge: TextStyle(fontWeight: FontWeight.w700),
        titleMedium: TextStyle(fontWeight: FontWeight.w600),
        titleSmall: TextStyle(fontWeight: FontWeight.w600),
        bodyMedium: TextStyle(height: 1.4),
      ),
    );
  }
}

/// 流畅的页面切换：淡入 + 轻微上浮 + 极轻缩放，easeOutCubic 缓动。
class EmberPageTransitionsBuilder extends PageTransitionsBuilder {
  const EmberPageTransitionsBuilder();

  @override
  Widget buildTransitions<T>(
    PageRoute<T> route,
    BuildContext context,
    Animation<double> animation,
    Animation<double> secondaryAnimation,
    Widget child,
  ) {
    final curved = CurvedAnimation(
      parent: animation,
      curve: Curves.easeOutCubic,
      reverseCurve: Curves.easeInCubic,
    );
    return FadeTransition(
      opacity: curved,
      child: SlideTransition(
        position: Tween<Offset>(
          begin: const Offset(0, 0.03),
          end: Offset.zero,
        ).animate(curved),
        child: ScaleTransition(
          scale: Tween<double>(begin: 0.995, end: 1).animate(curved),
          child: child,
        ),
      ),
    );
  }
}

class EmberGlassPanel extends StatelessWidget {
  const EmberGlassPanel({
    required this.child,
    this.padding = EdgeInsets.zero,
    this.blur = 14,
    this.color = EmberTheme.panel,
    this.borderColor = EmberTheme.border,
    super.key,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;
  final double blur;
  final Color color;
  final Color borderColor;

  @override
  Widget build(BuildContext context) {
    return ClipRect(
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: blur, sigmaY: blur),
        child: DecoratedBox(
          decoration: BoxDecoration(
            color: color,
            border: Border.all(color: borderColor),
          ),
          child: Padding(padding: padding, child: child),
        ),
      ),
    );
  }
}

class EmberPageBackground extends StatelessWidget {
  const EmberPageBackground({super.key});

  @override
  Widget build(BuildContext context) {
    return Stack(
      fit: StackFit.expand,
      children: const [
        DecoratedBox(
          decoration: BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
              colors: [
                EmberTheme.canvasTop,
                Color(0xFFF1F6FA),
                EmberTheme.canvasBottom,
              ],
              stops: [0, 0.48, 1],
            ),
          ),
        ),
        Positioned(
          left: -90,
          top: -110,
          width: 300,
          height: 300,
          child: DecoratedBox(
            decoration: BoxDecoration(
              gradient: RadialGradient(
                colors: [Color(0x384B9CFF), Color(0x004B9CFF)],
              ),
            ),
          ),
        ),
        Positioned(
          right: -120,
          bottom: -100,
          width: 340,
          height: 340,
          child: DecoratedBox(
            decoration: BoxDecoration(
              gradient: RadialGradient(
                colors: [Color(0x2F72D7B0), Color(0x0072D7B0)],
              ),
            ),
          ),
        ),
      ],
    );
  }
}
