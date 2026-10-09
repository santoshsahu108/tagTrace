import 'dart:convert';
import 'dart:io';

import 'package:intl/intl.dart';
import 'package:path_provider/path_provider.dart';

import '../models/location_point.dart';

/// Stores points as one JSON file per local day: tracks/2026-10-04.json.
class TrackStore {
  static final _dayFormat = DateFormat('yyyy-MM-dd');

  Future<Directory> _dir() async {
    final base = await getApplicationDocumentsDirectory();
    final dir = Directory('${base.path}/tracks');
    if (!await dir.exists()) await dir.create(recursive: true);
    return dir;
  }

  static String dayKey(DateTime day) => _dayFormat.format(day.toLocal());

  Future<File> _file(String key) async => File('${(await _dir()).path}/$key.json');

  /// Points for one local day, oldest first.
  Future<List<LocationPoint>> load(DateTime day) async {
    final file = await _file(dayKey(day));
    if (!await file.exists()) return [];
    final data = jsonDecode(await file.readAsString()) as List<dynamic>;
    final points = data
        .map((e) => LocationPoint.fromJson(e as Map<String, dynamic>))
        .toList()
      ..sort((a, b) => a.time.compareTo(b.time));
    return points;
  }

  /// Merges new points into their day files, skipping duplicates.
  /// Returns how many points were actually new.
  Future<int> addAll(Iterable<LocationPoint> points) async {
    final byDay = <String, List<LocationPoint>>{};
    for (final p in points) {
      byDay.putIfAbsent(dayKey(p.time), () => []).add(p);
    }
    var added = 0;
    for (final entry in byDay.entries) {
      final existing = await load(_dayFormat.parse(entry.key));
      final seen = existing.map((p) => p.key).toSet();
      for (final p in entry.value) {
        if (seen.add(p.key)) {
          existing.add(p);
          added++;
        }
      }
      existing.sort((a, b) => a.time.compareTo(b.time));
      final file = await _file(entry.key);
      final tmp = File('${file.path}.tmp');
      await tmp.writeAsString(jsonEncode(existing.map((p) => p.toJson()).toList()));
      await tmp.rename(file.path);
    }
    return added;
  }

  /// Days that have a file, newest first, with their point counts.
  Future<List<(DateTime, int)>> days() async {
    final dir = await _dir();
    final result = <(DateTime, int)>[];
    await for (final f in dir.list()) {
      final name = f.uri.pathSegments.last;
      if (f is! File || !name.endsWith('.json')) continue;
      final day = _dayFormat.tryParse(name.replaceAll('.json', ''));
      if (day == null) continue;
      result.add((day, (await load(day)).length));
    }
    result.sort((a, b) => b.$1.compareTo(a.$1));
    return result;
  }
}
