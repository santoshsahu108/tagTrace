import 'package:shared_preferences/shared_preferences.dart';

import 'location_source.dart';

enum SourceKind { http, file }

/// The one place the app's location source is configured.
class AppSettings {
  static const _kindKey = 'source_kind';
  static const _httpUrlKey = 'collector_url';
  static const _emailKey = 'collector_email';
  static const _passwordKey = 'collector_password';
  static const _filePathKey = 'collector_file';
  static const _lastSyncKey = 'last_point_epoch';

  final SourceKind kind;
  final String httpUrl;
  final String email;
  final String password;
  final String filePath;

  const AppSettings({
    this.kind = SourceKind.http,
    this.httpUrl = '',
    this.email = '',
    this.password = '',
    this.filePath = '',
  });

  bool get isConfigured =>
      kind == SourceKind.http ? httpUrl.isNotEmpty : filePath.isNotEmpty;

  /// The source these settings describe, or null when nothing is set.
  LocationSource? build() {
    if (!isConfigured) return null;
    return kind == SourceKind.http
        ? HttpCollectorSource(httpUrl, email: email, password: password)
        : LocalFileSource(filePath);
  }

  static Future<AppSettings> load() async {
    final p = await SharedPreferences.getInstance();
    return AppSettings(
      kind: (p.getString(_kindKey) == 'file') ? SourceKind.file : SourceKind.http,
      httpUrl: p.getString(_httpUrlKey) ?? '',
      email: p.getString(_emailKey) ?? '',
      password: p.getString(_passwordKey) ?? '',
      filePath: p.getString(_filePathKey) ?? '',
    );
  }

  Future<void> save() async {
    final p = await SharedPreferences.getInstance();
    await p.setString(_kindKey, kind == SourceKind.file ? 'file' : 'http');
    await p.setString(_httpUrlKey, httpUrl.trim());
    await p.setString(_emailKey, email.trim());
    await p.setString(_passwordKey, password);
    await p.setString(_filePathKey, filePath.trim());
    // Settings changed -> drop any cached session token so the next sync logs
    // in with the new address/credentials.
    await p.remove(collectorTokenPrefsKey);
  }

  AppSettings copyWith({
    SourceKind? kind,
    String? httpUrl,
    String? email,
    String? password,
    String? filePath,
  }) =>
      AppSettings(
        kind: kind ?? this.kind,
        httpUrl: httpUrl ?? this.httpUrl,
        email: email ?? this.email,
        password: password ?? this.password,
        filePath: filePath ?? this.filePath,
      );

  /// Newest point time we have stored, so a sync only asks for what's new.
  static Future<DateTime> lastSyncMark() async {
    final p = await SharedPreferences.getInstance();
    return DateTime.fromMillisecondsSinceEpoch(
      (p.getInt(_lastSyncKey) ?? 0) * 1000,
      isUtc: true,
    );
  }

  static Future<void> setLastSyncMark(DateTime t) async {
    final p = await SharedPreferences.getInstance();
    await p.setInt(_lastSyncKey, t.millisecondsSinceEpoch ~/ 1000);
  }
}
