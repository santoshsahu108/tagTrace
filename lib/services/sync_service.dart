import '../models/location_point.dart';
import 'settings.dart';
import 'track_store.dart';

class SyncResult {
  final int added;
  const SyncResult(this.added);
}

/// Pulls new points from the configured source and files them by day.
class SyncService {
  final TrackStore store;
  SyncService([TrackStore? store]) : store = store ?? TrackStore();

  Future<SyncResult> sync() async {
    final settings = await AppSettings.load();
    final source = settings.build();
    if (source == null) {
      throw StateError('No collector set. Open Settings to add one.');
    }

    final since = await AppSettings.lastSyncMark();
    final List<LocationPoint> points = await source.fetchSince(since);
    final added = await store.addAll(points);

    if (points.isNotEmpty) {
      final newest = points
          .map((p) => p.time)
          .reduce((a, b) => a.isAfter(b) ? a : b);
      if (newest.isAfter(since)) await AppSettings.setLastSyncMark(newest);
    }
    return SyncResult(added);
  }
}
