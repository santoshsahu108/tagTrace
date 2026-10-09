import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import '../models/location_point.dart';

/// Where the cached session token lives. Shared with AppSettings, which clears
/// it when the collector settings change so the next sync logs in afresh.
const collectorTokenPrefsKey = 'collector_token';

/// Somewhere the app can read fresh tag locations from.
///
/// The locations themselves come from the GoogleFindMyTools collector, which
/// signs in to Google as you and decrypts the tag's reports. This app only
/// reads the JSON it produces, so the collector can live on a PC/Pi (HTTP) or
/// on the phone next to the app (a local file).
abstract class LocationSource {
  /// Points reported after [sinceUtc]. May return older ones too; the store
  /// skips duplicates.
  Future<List<LocationPoint>> fetchSince(DateTime sinceUtc);

  /// A quick check that the source is reachable, for the Settings screen.
  Future<void> probe();
}

/// Reads from a collector exposing GET /locations?since=EPOCH_SECONDS,
/// replying { "points": [ {t,lat,lng,acc,src}, ... ] }.
///
/// The collector now requires a login: POST /login {email,password} returns a
/// session token, sent as `Authorization: Bearer <token>` on every read. The
/// token is cached so repeated syncs (including the headless background one)
/// don't log in each time, and is refreshed automatically on a 401.
class HttpCollectorSource implements LocationSource {
  final Uri base;
  final String email;
  final String password;

  HttpCollectorSource(String url, {this.email = '', this.password = ''})
      : base = Uri.parse(_normalize(url));

  static String _normalize(String url) {
    var u = url.trim();
    if (!u.startsWith('http://') && !u.startsWith('https://')) u = 'http://$u';
    return u.replaceAll(RegExp(r'/+$'), '');
  }

  Uri _at(String path, [Map<String, String>? query]) =>
      base.replace(path: '${base.path}$path', queryParameters: query);

  /// Log in, cache and return a fresh token. Throws a friendly message the
  /// Settings "Test" button and Sync can show as-is.
  Future<String> _login() async {
    if (email.isEmpty || password.isEmpty) {
      throw const HttpException('Add your email and password in Settings to sign in.');
    }
    final res = await http
        .post(
          _at('/login'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({'email': email, 'password': password}),
        )
        .timeout(const Duration(seconds: 20));
    if (res.statusCode == 401) {
      throw const HttpException('Wrong email or password.');
    }
    if (res.statusCode != 200) {
      throw HttpException('Login failed (${res.statusCode}).');
    }
    final token =
        (jsonDecode(res.body) as Map<String, dynamic>)['token'] as String?;
    if (token == null || token.isEmpty) {
      throw const HttpException('Login returned no token.');
    }
    final p = await SharedPreferences.getInstance();
    await p.setString(collectorTokenPrefsKey, token);
    return token;
  }

  /// GET [uri] with the cached token, logging in first if we have none and
  /// retrying once if the token was rejected (expired or cleared).
  Future<http.Response> _authedGet(Uri uri) async {
    final p = await SharedPreferences.getInstance();
    var token = p.getString(collectorTokenPrefsKey) ?? await _login();
    var res = await http
        .get(uri, headers: {'Authorization': 'Bearer $token'})
        .timeout(const Duration(seconds: 20));
    if (res.statusCode == 401) {
      token = await _login();
      res = await http
          .get(uri, headers: {'Authorization': 'Bearer $token'})
          .timeout(const Duration(seconds: 20));
    }
    return res;
  }

  @override
  Future<List<LocationPoint>> fetchSince(DateTime sinceUtc) async {
    final since = sinceUtc.millisecondsSinceEpoch ~/ 1000;
    final res = await _authedGet(_at('/locations', {'since': '$since'}));
    if (res.statusCode != 200) {
      throw HttpException('Collector replied ${res.statusCode}');
    }
    return _parse(res.body);
  }

  @override
  Future<void> probe() async {
    final res = await _authedGet(_at('/locations', {'since': '0'}));
    if (res.statusCode != 200) {
      throw HttpException('Collector replied ${res.statusCode}');
    }
    _parse(res.body);
  }
}

/// Reads a JSON file the phone-side collector keeps updating.
class LocalFileSource implements LocationSource {
  final String path;
  LocalFileSource(this.path);

  @override
  Future<List<LocationPoint>> fetchSince(DateTime sinceUtc) async {
    final file = File(path);
    if (!await file.exists()) {
      throw FileSystemException('File not found', path);
    }
    final points = _parse(await file.readAsString());
    return points.where((p) => p.time.isAfter(sinceUtc)).toList();
  }

  @override
  Future<void> probe() async {
    final file = File(path);
    if (!await file.exists()) {
      throw FileSystemException('File not found', path);
    }
    _parse(await file.readAsString());
  }
}

/// Accepts either {"points": [...]} or a bare [...] of point objects.
List<LocationPoint> _parse(String body) {
  final decoded = jsonDecode(body);
  final list = decoded is Map<String, dynamic>
      ? (decoded['points'] as List<dynamic>? ?? const [])
      : decoded as List<dynamic>;
  return list
      .map((e) => LocationPoint.fromJson(e as Map<String, dynamic>))
      .toList();
}
