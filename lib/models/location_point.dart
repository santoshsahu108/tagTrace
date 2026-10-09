/// One location report for the tag.
class LocationPoint {
  /// Time Google says the tag was seen there (UTC).
  final DateTime time;
  final double lat;
  final double lng;

  /// Accuracy radius in metres, 0 when unknown.
  final double accuracy;

  /// Where the report came from, e.g. "own", "crowdsourced", "aggregated".
  final String source;

  const LocationPoint({
    required this.time,
    required this.lat,
    required this.lng,
    this.accuracy = 0,
    this.source = '',
  });

  factory LocationPoint.fromJson(Map<String, dynamic> json) => LocationPoint(
        time: DateTime.fromMillisecondsSinceEpoch(
          (json['t'] as num).toInt() * 1000,
          isUtc: true,
        ),
        lat: (json['lat'] as num).toDouble(),
        lng: (json['lng'] as num).toDouble(),
        accuracy: (json['acc'] as num?)?.toDouble() ?? 0,
        source: json['src'] as String? ?? '',
      );

  Map<String, dynamic> toJson() => {
        't': time.millisecondsSinceEpoch ~/ 1000,
        'lat': lat,
        'lng': lng,
        'acc': accuracy,
        'src': source,
      };

  /// Two reports with the same timestamp and position are the same sighting.
  String get key => '${time.millisecondsSinceEpoch}_${lat}_$lng';
}
