import 'package:flutter/widgets.dart';
import 'package:workmanager/workmanager.dart';

import 'sync_service.dart';

const syncTaskName = 'tag-trace-sync';

/// Entry point Android calls in a background isolate for the periodic task.
@pragma('vm:entry-point')
void callbackDispatcher() {
  Workmanager().executeTask((task, input) async {
    WidgetsFlutterBinding.ensureInitialized();
    try {
      await SyncService().sync();
    } catch (_) {
      // Collector not reachable right now (e.g. off home Wi-Fi). The next
      // run picks up whatever was missed, so report success either way.
    }
    return true;
  });
}

/// 15 minutes is Android's minimum period for background work.
Future<void> scheduleBackgroundSync() async {
  await Workmanager().initialize(callbackDispatcher);
  await Workmanager().registerPeriodicTask(
    syncTaskName,
    syncTaskName,
    frequency: const Duration(minutes: 15),
    constraints: Constraints(networkType: NetworkType.connected),
    existingWorkPolicy: ExistingPeriodicWorkPolicy.keep,
  );
}

Future<void> cancelBackgroundSync() => Workmanager().cancelByUniqueName(syncTaskName);
