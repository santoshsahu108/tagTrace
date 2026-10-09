import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:intl/intl.dart';
import 'package:latlong2/latlong.dart';

import '../models/location_point.dart';
import '../services/track_store.dart';

/// The trace for one day, from 00:00 up to the latest point (for today, "now").
class DayMapScreen extends StatefulWidget {
  final DateTime day;
  const DayMapScreen({super.key, required this.day});

  @override
  State<DayMapScreen> createState() => _DayMapScreenState();
}

class _DayMapScreenState extends State<DayMapScreen> {
  final _store = TrackStore();
  final _mapController = MapController();
  final _time = DateFormat('HH:mm');
  final _dayLabel = DateFormat('d MMM yyyy');

  List<LocationPoint> _points = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final all = await _store.load(widget.day);
    // Keep 00:00 of the day up to the end of the day (for today, up to now).
    final start = DateTime(widget.day.year, widget.day.month, widget.day.day);
    final end = start.add(const Duration(days: 1));
    final points = all
        .where((p) {
          final local = p.time.toLocal();
          return !local.isBefore(start) && local.isBefore(end);
        })
        .toList();
    if (!mounted) return;
    setState(() {
      _points = points;
      _loading = false;
    });
    if (points.isNotEmpty) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _fitBounds(points));
    }
  }

  void _fitBounds(List<LocationPoint> points) {
    final coords = points.map((p) => LatLng(p.lat, p.lng)).toList();
    if (coords.length == 1) {
      _mapController.move(coords.first, 16);
      return;
    }
    _mapController.fitCamera(
      CameraFit.coordinates(
        coordinates: coords,
        padding: const EdgeInsets.all(48),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final coords = _points.map((p) => LatLng(p.lat, p.lng)).toList();
    return Scaffold(
      appBar: AppBar(
        title: Text(_dayLabel.format(widget.day)),
        bottom: _points.isEmpty
            ? null
            : PreferredSize(
                preferredSize: const Size.fromHeight(24),
                child: Padding(
                  padding: const EdgeInsets.only(bottom: 6),
                  child: Text(
                    '${_points.length} points · '
                    '${_time.format(_points.first.time.toLocal())}'
                    ' – ${_time.format(_points.last.time.toLocal())}',
                    style: const TextStyle(color: Colors.white70, fontSize: 13),
                  ),
                ),
              ),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _points.isEmpty
              ? const Center(
                  child: Text('No locations recorded for this day.'),
                )
              : Column(
                  children: [
                    Expanded(flex: 3, child: _map(coords)),
                    const Divider(height: 1),
                    Expanded(flex: 2, child: _list()),
                  ],
                ),
    );
  }

  Widget _map(List<LatLng> coords) => FlutterMap(
        mapController: _mapController,
        options: MapOptions(
          initialCenter: coords.first,
          initialZoom: 14,
        ),
        children: [
          TileLayer(
            urlTemplate: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
            userAgentPackageName: 'com.santosh.tag_trace',
            maxZoom: 19,
          ),
          if (coords.length > 1)
            PolylineLayer(
              polylines: [
                Polyline(
                  points: coords,
                  strokeWidth: 4,
                  color: Colors.blue.withValues(alpha: 0.8),
                ),
              ],
            ),
          MarkerLayer(markers: _markers(coords)),
        ],
      );

  List<Marker> _markers(List<LatLng> coords) {
    final markers = <Marker>[];
    for (var i = 0; i < coords.length; i++) {
      final isFirst = i == 0;
      final isLast = i == coords.length - 1;
      markers.add(
        Marker(
          point: coords[i],
          width: isFirst || isLast ? 36 : 14,
          height: isFirst || isLast ? 36 : 14,
          child: isFirst
              ? const Icon(Icons.trip_origin, color: Colors.green, size: 32)
              : isLast
                  ? const Icon(Icons.place, color: Colors.red, size: 36)
                  : Container(
                      decoration: BoxDecoration(
                        color: Colors.blue,
                        shape: BoxShape.circle,
                        border: Border.all(color: Colors.white, width: 2),
                      ),
                    ),
        ),
      );
    }
    return markers;
  }

  Widget _list() => ListView.separated(
        itemCount: _points.length,
        separatorBuilder: (context, index) => const Divider(height: 1),
        itemBuilder: (context, i) {
          final p = _points[i];
          final local = p.time.toLocal();
          return ListTile(
            dense: true,
            leading: Text(_time.format(local),
                style: const TextStyle(fontWeight: FontWeight.w600)),
            title: Text('${p.lat.toStringAsFixed(5)}, ${p.lng.toStringAsFixed(5)}'),
            subtitle: Text([
              if (p.accuracy > 0) '±${p.accuracy.round()} m',
              if (p.source.isNotEmpty) p.source,
            ].join(' · ')),
            onTap: () => _mapController.move(LatLng(p.lat, p.lng), 17),
          );
        },
      );
}
