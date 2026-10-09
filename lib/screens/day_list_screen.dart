import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../services/settings.dart';
import '../services/sync_service.dart';
import '../services/track_store.dart';
import 'day_map_screen.dart';
import 'settings_screen.dart';

/// Home: the days that have points. Tap one to see its trace.
class DayListScreen extends StatefulWidget {
  const DayListScreen({super.key});

  @override
  State<DayListScreen> createState() => _DayListScreenState();
}

class _DayListScreenState extends State<DayListScreen> {
  final _store = TrackStore();
  final _dayLabel = DateFormat('EEE, d MMM yyyy');

  List<(DateTime, int)> _days = [];
  bool _loading = true;
  bool _syncing = false;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    setState(() => _loading = true);
    final days = await _store.days();
    if (!mounted) return;
    setState(() {
      _days = days;
      _loading = false;
    });
  }

  Future<void> _syncNow() async {
    final settings = await AppSettings.load();
    if (!settings.isConfigured) {
      if (!mounted) return;
      _snack('Add a collector in Settings first.');
      await _openSettings();
      return;
    }
    setState(() => _syncing = true);
    try {
      final result = await SyncService().sync();
      if (!mounted) return;
      _snack(result.added == 0
          ? 'Up to date, no new points.'
          : 'Added ${result.added} new point${result.added == 1 ? '' : 's'}.');
      await _refresh();
    } catch (e) {
      if (mounted) _snack('Sync failed: $e');
    } finally {
      if (mounted) setState(() => _syncing = false);
    }
  }

  Future<void> _openSettings() async {
    await Navigator.of(context).push(
      MaterialPageRoute(builder: (_) => const SettingsScreen()),
    );
    await _refresh();
  }

  void _snack(String msg) => ScaffoldMessenger.of(context)
      .showSnackBar(SnackBar(content: Text(msg)));

  @override
  Widget build(BuildContext context) {
    final today = DateTime.now();
    return Scaffold(
      appBar: AppBar(
        title: const Text('Tag trace'),
        actions: [
          IconButton(
            icon: const Icon(Icons.settings),
            tooltip: 'Settings',
            onPressed: _openSettings,
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _syncing ? null : _syncNow,
        icon: _syncing
            ? const SizedBox(
                width: 18,
                height: 18,
                child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
              )
            : const Icon(Icons.sync),
        label: Text(_syncing ? 'Syncing' : 'Sync now'),
      ),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _days.isEmpty
                ? _empty(today)
                : ListView.separated(
                    itemCount: _days.length,
                    separatorBuilder: (context, index) => const Divider(height: 1),
                    itemBuilder: (context, i) {
                      final (day, count) = _days[i];
                      final isToday = TrackStore.dayKey(day) ==
                          TrackStore.dayKey(today);
                      return ListTile(
                        leading: CircleAvatar(child: Text('$count')),
                        title: Text(_dayLabel.format(day)),
                        subtitle: Text(isToday
                            ? 'Today, so far'
                            : '$count location${count == 1 ? '' : 's'}'),
                        trailing: const Icon(Icons.chevron_right),
                        onTap: () => Navigator.of(context).push(
                          MaterialPageRoute(
                            builder: (_) => DayMapScreen(day: day),
                          ),
                        ),
                      );
                    },
                  ),
      ),
    );
  }

  Widget _empty(DateTime today) => ListView(
        children: [
          const SizedBox(height: 120),
          const Icon(Icons.location_off, size: 64, color: Colors.grey),
          const SizedBox(height: 16),
          const Center(
            child: Text('No locations yet', style: TextStyle(fontSize: 18)),
          ),
          const SizedBox(height: 8),
          const Padding(
            padding: EdgeInsets.symmetric(horizontal: 32),
            child: Text(
              'Add your collector in Settings, then tap Sync now.',
              textAlign: TextAlign.center,
              style: TextStyle(color: Colors.grey),
            ),
          ),
        ],
      );
}
