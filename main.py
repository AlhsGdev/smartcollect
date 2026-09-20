import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:device_info_plus/device_info_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:crypto/crypto.dart';

class ActivationService {
  static const String serverUrl = "https://smartcollect.onrender.com";

  static const bool _forceDisableActivation = false;

  static const int defaultTrialDays = 7;

  static const int trialMaxTables = 1;
  static const int trialMaxRowsPerTable = 50;
  static const bool trialAllowExport = false;

  static String? _cachedDeviceId;

  /// Retourne un identifiant d'appareil stable après réinstallation.
  static Future<String> getDeviceId() async {
    if (_cachedDeviceId != null && _cachedDeviceId!.isNotEmpty) {
      return _cachedDeviceId!;
    }

    try {
      final prefs = await SharedPreferences.getInstance();
      final stored = prefs.getString('smartcollect_device_id');
      if (stored != null && stored.isNotEmpty) {
        final currentFingerprint = await _getHardwareFingerprint();
        if (currentFingerprint != null &&
            await _hashToHex(currentFingerprint, length: 32) == stored) {
          _cachedDeviceId = stored;
          return stored;
        }
      }
    } catch (_) {}

    String newId = '';

    try {
      final fingerprint = await _getHardwareFingerprint();
      if (fingerprint != null && fingerprint.isNotEmpty) {
        newId = await _hashToHex(fingerprint, length: 32);
      }
    } catch (_) {}

    if (newId.isEmpty) {
      newId = 'device_${DateTime.now().millisecondsSinceEpoch}';
    }

    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('smartcollect_device_id', newId);
    } catch (_) {}

    _cachedDeviceId = newId;
    return newId;
  }

  static Future<String?> _getHardwareFingerprint() async {
    try {
      final deviceInfo = DeviceInfoPlugin();

      try {
        final androidInfo = await deviceInfo.androidInfo;
        final raw = [
          androidInfo.id,
          androidInfo.fingerprint,
          androidInfo.model,
          androidInfo.manufacturer,
          androidInfo.device,
          androidInfo.hardware,
          androidInfo.board,
          androidInfo.bootloader,
        ].join('|');
        return raw;
      } catch (_) {}

      try {
        final iosInfo = await deviceInfo.iosInfo;
        final raw = [
          iosInfo.identifierForVendor ?? '',
          iosInfo.model,
          iosInfo.utsname.machine,
        ].join('|');

        if (raw.replaceAll('|', '').trim().isNotEmpty) {
          return raw;
        }
      } catch (_) {}

      return null;
    } catch (_) {
      return null;
    }
  }

  static String _hashToHex(String input, {int length = 32}) {
    final bytes = utf8.encode(input);
    final digest = sha256.convert(bytes);
    final hex = digest.toString();
    return hex.substring(0, length.clamp(8, hex.length));
  }

  static Future<void> resetDeviceId() async {
    _cachedDeviceId = null;
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove('smartcollect_device_id');
    } catch (_) {}
  }

  // ═══════════════════════════════════════════════════════════
  // ✅ VÉRIFICATION DE LICENCE AU DÉMARRAGE
  //    Envoie les noms depuis prefs si disponibles
  //    Purge proprement si le serveur refuse
  // ═══════════════════════════════════════════════════════════
  static Future<bool> verifyLicenseStatus() async {
    final prefs = await SharedPreferences.getInstance();

    if (_forceDisableActivation) {
      return true;
    }

    final isActivatedLocally =
        prefs.getBool('is_activated') ?? prefs.getBool('isActivated') ?? false;
    final savedKey =
        prefs.getString('license_key') ?? prefs.getString('licenseKey');
    final trialExpiryStr = prefs.getString('trial_expiry_date');

    final activationHiddenUntil = prefs.getString('activation_hidden_until');
    if (activationHiddenUntil != null) {
      final hiddenUntil = DateTime.tryParse(activationHiddenUntil);
      if (hiddenUntil != null && DateTime.now().isBefore(hiddenUntil)) {
        return true;
      } else {
        await prefs.remove('activation_hidden_until');
      }
    }

    if (trialExpiryStr != null) {
      final trialExpiry = DateTime.tryParse(trialExpiryStr);
      if (trialExpiry != null && DateTime.now().isAfter(trialExpiry)) {
        if (savedKey == null || savedKey.isEmpty) {
          await forcePurgeLicense();
          return false;
        }
      }
    }

    if (!isActivatedLocally && trialExpiryStr == null) {
      return false;
    }

    if (savedKey != null && savedKey.trim().isNotEmpty) {
      try {
        final deviceId = await getDeviceId();

        // ✅ FIX : envoie les noms depuis prefs
        final response = await http
            .post(
          Uri.parse('$serverUrl/api/license/verify/'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({
            'key': savedKey.trim().toUpperCase(),
            'device_id': deviceId,
            'first_name': prefs.getString('userFirstName') ?? '',
            'last_name': prefs.getString('userLastName') ?? '',
            'organization': prefs.getString('userOrganization') ?? '',
          }),
        )
            .timeout(const Duration(seconds: 5));

        if (response.statusCode == 200) {
          final data = jsonDecode(response.body);
          if (data is Map<String, dynamic> && data['status'] == 'valid') {
            final isTrial = data['is_trial'] == true;
            final limits = data['limits'] as Map<String, dynamic>? ?? {};
            await saveLimits({
              'is_trial': isTrial,
              'max_tables': limits['max_tables'] ?? 999999,
              'max_rows_per_table': limits['max_rows_per_table'] ?? 999999,
              'allow_export': limits['allow_export'] ?? true,
            });
            if (!isTrial) {
              await prefs.remove('trial_expiry_date');
            }
            return true;
          } else {
            await forcePurgeLicense();
            return false;
          }
        } else {
          await forcePurgeLicense();
          return false;
        }
      } catch (_) {
        // Hors-ligne → tolérer tant que le cache local est cohérent
        if (trialExpiryStr != null) {
          final trialExpiry = DateTime.tryParse(trialExpiryStr);
          if (trialExpiry != null && DateTime.now().isBefore(trialExpiry)) {
            return true;
          }
        }
        // ✅ FIX : si pas activé localement, refuser
        if (!isActivatedLocally) {
          return false;
        }
        return isActivatedLocally;
      }
    }

    if (trialExpiryStr != null) {
      final trialExpiry = DateTime.tryParse(trialExpiryStr);
      if (trialExpiry != null && DateTime.now().isBefore(trialExpiry)) {
        return true;
      } else {
        await forcePurgeLicense();
        return false;
      }
    }

    return false;
  }

  // ═══════════════════════════════════════════════════════════
  // DÉMARRAGE D'ESSAI
  // ═══════════════════════════════════════════════════════════
  static Future<void> startTrial(String key, {int? trialDays}) async {
    final prefs = await SharedPreferences.getInstance();
    final effectiveDays = trialDays ?? defaultTrialDays;
    final expiry = DateTime.now().add(Duration(days: effectiveDays));

    await prefs.setString('trial_expiry_date', expiry.toIso8601String());
    await prefs.setBool('is_activated', true);
    await prefs.setBool('isActivated', true);
    await prefs.setString('license_key', key);
    await prefs.setString('licenseKey', key);
    await prefs.setInt('trial_days', effectiveDays);

    await saveLimits({
      'is_trial': true,
      'max_tables': trialMaxTables,
      'max_rows_per_table': trialMaxRowsPerTable,
      'allow_export': trialAllowExport,
    });

    print('🎟️ Essai démarré : $effectiveDays jours '
        '(expire le ${expiry.toLocal()})');
  }

  static Future<void> startSevenDayTrial(String key) async {
    await startTrial(key, trialDays: defaultTrialDays);
  }

  static Future<void> forcePurgeLicense() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('is_activated');
    await prefs.remove('isActivated');
    await prefs.remove('license_key');
    await prefs.remove('licenseKey');
    await prefs.remove('trial_expiry_date');
    await prefs.remove('trial_days');
    await prefs.remove('is_trial');
    await prefs.remove('max_tables');
    await prefs.remove('max_rows_per_table');
    await prefs.remove('allow_export');
  }

  static Future<void> hideActivationForOneWeek() async {
    final prefs = await SharedPreferences.getInstance();
    final hiddenUntil = DateTime.now().add(const Duration(days: 7));
    await prefs
        .setString('activation_hidden_until', hiddenUntil.toIso8601String());
  }

  static Future<void> reactivateActivation() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('activation_hidden_until');
  }

  static Future<bool> isActivationHidden() async {
    final prefs = await SharedPreferences.getInstance();
    final activationHiddenUntil = prefs.getString('activation_hidden_until');
    if (activationHiddenUntil == null) return false;
    final hiddenUntil = DateTime.tryParse(activationHiddenUntil);
    if (hiddenUntil == null) return false;
    return DateTime.now().isBefore(hiddenUntil);
  }

  // ═══════════════════════════════════════════════════════════
  // CACHE LOCAL DES LIMITES
  // ═══════════════════════════════════════════════════════════
  static Future<void> saveLimits(Map<String, dynamic> limits) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('is_trial', limits['is_trial'] == true);
    await prefs.setInt('max_tables', (limits['max_tables'] as num?)?.toInt() ?? 999999);
    await prefs.setInt('max_rows_per_table',
        (limits['max_rows_per_table'] as num?)?.toInt() ?? 999999);
    await prefs.setBool('allow_export', limits['allow_export'] == true);
  }

  static Future<Map<String, dynamic>> getCachedLimits() async {
    final prefs = await SharedPreferences.getInstance();
    final isTrial = prefs.getBool('is_trial') ?? await isInTrialMode();
    return {
      'is_trial': isTrial,
      'max_tables': prefs.getInt('max_tables') ?? (isTrial ? trialMaxTables : 999999),
      'max_rows_per_table': prefs.getInt('max_rows_per_table') ??
          (isTrial ? trialMaxRowsPerTable : 999999),
      'allow_export': prefs.getBool('allow_export') ??
          (isTrial ? trialAllowExport : true),
    };
  }

  static Future<bool> canExport() async {
    if (_forceDisableActivation) return true;
    final limits = await getCachedLimits();
    return limits['allow_export'] == true;
  }

  static Future<int> maxTablesAllowed() async {
    if (_forceDisableActivation) return 999999;
    final limits = await getCachedLimits();
    return limits['max_tables'] as int;
  }

  static Future<int> maxRowsAllowed() async {
    if (_forceDisableActivation) return 999999;
    final limits = await getCachedLimits();
    return limits['max_rows_per_table'] as int;
  }

  // ═══════════════════════════════════════════════════════════
  // LIMITATIONS D'ESSAI
  // ═══════════════════════════════════════════════════════════
  static Future<bool> isInTrialMode() async {
    final prefs = await SharedPreferences.getInstance();

    final explicitTrialFlag = prefs.getBool('is_trial');
    if (explicitTrialFlag != null) return explicitTrialFlag;

    final trialExpiry = prefs.getString('trial_expiry_date');
    if (trialExpiry == null) return false;

    final expiry = DateTime.tryParse(trialExpiry);
    if (expiry == null) return false;

    return DateTime.now().isBefore(expiry);
  }

  static Future<Map<String, dynamic>> getTrialLimits() async {
    return getCachedLimits();
  }

  static Future<int> getRemainingTrialDays() async {
    final prefs = await SharedPreferences.getInstance();
    final trialExpiryStr = prefs.getString('trial_expiry_date');
    if (trialExpiryStr == null) return 0;

    final trialExpiry = DateTime.tryParse(trialExpiryStr);
    if (trialExpiry == null) return 0;

    final remaining = trialExpiry.difference(DateTime.now()).inDays;
    return remaining > 0 ? remaining : 0;
  }
}
