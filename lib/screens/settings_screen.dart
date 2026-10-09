import 'package:flutter/material.dart';

import '../services/settings.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  SourceKind _kind = SourceKind.http;
  final _httpCtl = TextEditingController();
  final _emailCtl = TextEditingController();
  final _passwordCtl = TextEditingController();
  final _fileCtl = TextEditingController();
  bool _loading = true;
  bool _testing = false;
  bool _showPassword = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final s = await AppSettings.load();
    setState(() {
      _kind = s.kind;
      _httpCtl.text = s.httpUrl;
      _emailCtl.text = s.email;
      _passwordCtl.text = s.password;
      _fileCtl.text = s.filePath;
      _loading = false;
    });
  }

  AppSettings get _current => AppSettings(
        kind: _kind,
        httpUrl: _httpCtl.text,
        email: _emailCtl.text,
        password: _passwordCtl.text,
        filePath: _fileCtl.text,
      );

  Future<void> _save() async {
    await _current.save();
    if (mounted) _snack('Saved.');
  }

  Future<void> _test() async {
    final source = _current.build();
    if (source == null) {
      _snack('Fill in the address first.');
      return;
    }
    setState(() => _testing = true);
    try {
      await source.probe();
      if (mounted) _snack('Signed in and reached the collector. Looks good.');
    } catch (e) {
      if (mounted) _snack('Could not reach it: $e');
    } finally {
      if (mounted) setState(() => _testing = false);
    }
  }

  void _snack(String m) =>
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m)));

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                const Text('Where to read locations from',
                    style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
                const SizedBox(height: 4),
                const Text(
                  'The collector signs in to Google and decrypts the tag, then '
                  'this app reads the locations it produces.',
                  style: TextStyle(color: Colors.grey),
                ),
                const SizedBox(height: 16),
                SegmentedButton<SourceKind>(
                  segments: const [
                    ButtonSegment(
                      value: SourceKind.http,
                      label: Text('Over Wi-Fi'),
                      icon: Icon(Icons.wifi),
                    ),
                    ButtonSegment(
                      value: SourceKind.file,
                      label: Text('Local file'),
                      icon: Icon(Icons.insert_drive_file),
                    ),
                  ],
                  selected: {_kind},
                  onSelectionChanged: (s) => setState(() => _kind = s.first),
                ),
                const SizedBox(height: 20),
                if (_kind == SourceKind.http) ...[
                  TextField(
                    controller: _httpCtl,
                    keyboardType: TextInputType.url,
                    decoration: const InputDecoration(
                      labelText: 'Collector address',
                      hintText: 'http://192.168.1.50:8020',
                      border: OutlineInputBorder(),
                    ),
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    "Your computer or Pi's address on the home network, with "
                    'the port the collector listens on.',
                    style: TextStyle(color: Colors.grey, fontSize: 13),
                  ),
                  const SizedBox(height: 20),
                  TextField(
                    controller: _emailCtl,
                    keyboardType: TextInputType.emailAddress,
                    autocorrect: false,
                    decoration: const InputDecoration(
                      labelText: 'Email',
                      hintText: 'you@example.com',
                      border: OutlineInputBorder(),
                    ),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _passwordCtl,
                    obscureText: !_showPassword,
                    decoration: InputDecoration(
                      labelText: 'Password',
                      border: const OutlineInputBorder(),
                      suffixIcon: IconButton(
                        icon: Icon(_showPassword
                            ? Icons.visibility_off
                            : Icons.visibility),
                        tooltip: _showPassword ? 'Hide' : 'Show',
                        onPressed: () =>
                            setState(() => _showPassword = !_showPassword),
                      ),
                    ),
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'The collector now requires a login. Use the email and '
                    'password set up for it.',
                    style: TextStyle(color: Colors.grey, fontSize: 13),
                  ),
                ] else ...[
                  TextField(
                    controller: _fileCtl,
                    decoration: const InputDecoration(
                      labelText: 'JSON file path',
                      hintText: '/storage/emulated/0/.../locations.json',
                      border: OutlineInputBorder(),
                    ),
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'The file the phone-side collector keeps updating.',
                    style: TextStyle(color: Colors.grey, fontSize: 13),
                  ),
                ],
                const SizedBox(height: 24),
                Row(
                  children: [
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: _testing ? null : _test,
                        icon: _testing
                            ? const SizedBox(
                                width: 16,
                                height: 16,
                                child: CircularProgressIndicator(strokeWidth: 2),
                              )
                            : const Icon(Icons.network_check),
                        label: const Text('Test'),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: FilledButton.icon(
                        onPressed: _save,
                        icon: const Icon(Icons.save),
                        label: const Text('Save'),
                      ),
                    ),
                  ],
                ),
              ],
            ),
    );
  }

  @override
  void dispose() {
    _httpCtl.dispose();
    _emailCtl.dispose();
    _passwordCtl.dispose();
    _fileCtl.dispose();
    super.dispose();
  }
}
