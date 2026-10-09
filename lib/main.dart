import 'package:flutter/material.dart';

import 'services/background.dart';
import 'screens/day_list_screen.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // Best-effort: keep syncing in the background. Safe if it fails on a device
  // without the plugin (e.g. a desktop test run).
  try {
    await scheduleBackgroundSync();
  } catch (_) {}
  runApp(const TagTraceApp());
}

class TagTraceApp extends StatelessWidget {
  const TagTraceApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Tag trace',
      theme: ThemeData(
        colorSchemeSeed: Colors.blue,
        useMaterial3: true,
      ),
      darkTheme: ThemeData(
        colorSchemeSeed: Colors.blue,
        brightness: Brightness.dark,
        useMaterial3: true,
      ),
      home: const DayListScreen(),
      debugShowCheckedModeBanner: false,
    );
  }
}
