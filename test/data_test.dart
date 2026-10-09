import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:tag_trace/models/location_point.dart';
import 'package:tag_trace/services/location_source.dart';

void main() {
  test('LocationPoint survives a JSON round-trip', () {
    final p = LocationPoint(
      time: DateTime.utc(2026, 10, 4, 9, 30),
      lat: 19.2345678,
      lng: 72.8765432,
      accuracy: 25.5,
      source: 'crowdsourced',
    );
    final back = LocationPoint.fromJson(jsonDecode(jsonEncode(p.toJson())));
    expect(back.time, p.time);
    expect(back.lat, closeTo(p.lat, 1e-9));
    expect(back.lng, closeTo(p.lng, 1e-9));
    expect(back.accuracy, p.accuracy);
    expect(back.source, p.source);
  });

  test('LocalFileSource reads a bare list and filters by time', () async {
    final tmp = File('${Directory.systemTemp.path}/tt_${DateTime.now().microsecondsSinceEpoch}.json');
    final base = DateTime.utc(2026, 10, 4, 8);
    await tmp.writeAsString(jsonEncode([
      {'t': base.millisecondsSinceEpoch ~/ 1000, 'lat': 1.0, 'lng': 2.0},
      {'t': base.add(const Duration(hours: 2)).millisecondsSinceEpoch ~/ 1000, 'lat': 3.0, 'lng': 4.0},
    ]));
    final src = LocalFileSource(tmp.path);
    final all = await src.fetchSince(DateTime.utc(2026, 10, 4, 0));
    expect(all.length, 2);
    final newer = await src.fetchSince(base.add(const Duration(hours: 1)));
    expect(newer.length, 1);
    expect(newer.single.lat, 3.0);
    await tmp.delete();
  });

  test('LocalFileSource also accepts the {points:[...]} shape', () async {
    final tmp = File('${Directory.systemTemp.path}/tt2_${DateTime.now().microsecondsSinceEpoch}.json');
    await tmp.writeAsString(jsonEncode({
      'points': [
        {'t': 1700000000, 'lat': 10.0, 'lng': 20.0, 'acc': 12.0, 'src': 'own'}
      ]
    }));
    final src = LocalFileSource(tmp.path);
    final pts = await src.fetchSince(DateTime.fromMillisecondsSinceEpoch(0, isUtc: true));
    expect(pts.single.source, 'own');
    expect(pts.single.accuracy, 12.0);
    await tmp.delete();
  });
}
