import 'package:ember/main.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('shows migration diagnostics', (tester) async {
    await tester.pumpWidget(const EmberApp());

    expect(find.text('Ember 迁移诊断'), findsOneWidget);
    expect(find.text('Flutter → Kotlin → CPython'), findsOneWidget);
    expect(find.text('启动核心'), findsOneWidget);
  });
}
