import React, { useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Alert, TextInput, ScrollView } from 'react-native';
import { PairingError, transferService } from '../services/transfer';
import { qrScanner } from '../services/nativeModules';
import { PCConfig } from '../types';

interface QRPairingScreenProps {
  onPaired: (config: PCConfig) => void;
  onCancel: () => void;
}

/** Parses the host's /api/pair QR payload: {"ips": [...], "port": 8000, "token": "..."}. */
function parsePairingPayload(text: string): PCConfig | null {
  try {
    const data = JSON.parse(text);
    const ips: unknown = data.ips ?? (data.ip ? [data.ip] : []);
    if (!Array.isArray(ips) || ips.length === 0 || typeof data.token !== 'string' || !data.port) {
      return null;
    }
    return { ips: ips.map(String), port: Number(data.port), token: data.token, pairedAt: Date.now() };
  } catch {
    return null;
  }
}

export const QRPairingScreen: React.FC<QRPairingScreenProps> = ({ onPaired, onCancel }) => {
  const [busy, setBusy] = useState(false);
  const [ip, setIp] = useState('');
  const [port, setPort] = useState('8000');
  const [token, setToken] = useState('');

  const pair = async (config: PCConfig) => {
    setBusy(true);
    try {
      const reachableIp = await transferService.findReachableIp(config);
      const paired = { ...config, lastReachableIp: reachableIp };
      await transferService.setPCConfig(paired);
      onPaired(paired);
    } catch (error) {
      if (error instanceof PairingError && error.reason === 'unreachable') {
        // Pairing while away from the PC's network is legitimate; uploads start once it's reachable.
        Alert.alert('PC not reachable right now', `${error.message}\n\nSave the pairing anyway?`, [
          { text: 'Cancel', style: 'cancel' },
          {
            text: 'Save anyway',
            onPress: async () => {
              await transferService.setPCConfig(config);
              onPaired(config);
            },
          },
        ]);
      } else {
        Alert.alert('Pairing failed', error instanceof Error ? error.message : String(error));
      }
    } finally {
      setBusy(false);
    }
  };

  const handleScan = async () => {
    let text: string | null;
    try {
      text = await qrScanner.scan();
    } catch (error) {
      Alert.alert(
        'Scanner unavailable',
        `${error instanceof Error ? error.message : String(error)}\n\nEnter the details from the pairing page instead.`,
      );
      return;
    }
    if (text === null) {
      return;
    }
    const config = parsePairingPayload(text);
    if (!config) {
      Alert.alert('Invalid QR Code', 'Scan the code on the PC pairing page (http://localhost:8000/api/pair).');
      return;
    }
    await pair(config);
  };

  const handleManualPair = async () => {
    if (!ip.trim() || !token.trim()) {
      Alert.alert('Missing details', 'Enter the IP address and pair token shown on the PC pairing page.');
      return;
    }
    await pair({
      ips: [ip.trim()],
      port: parseInt(port, 10) || 8000,
      token: token.trim(),
      pairedAt: Date.now(),
    });
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
      <View style={styles.header}>
        <TouchableOpacity style={styles.closeButton} onPress={onCancel}>
          <Text style={styles.closeText}>✕</Text>
        </TouchableOpacity>
      </View>

      <Text style={styles.title}>Pair with PC</Text>
      <Text style={styles.subtitle}>
        On the PC, open http://localhost:8000/api/pair and scan the code shown there.
      </Text>

      <TouchableOpacity style={[styles.button, busy && styles.buttonDisabled]} onPress={handleScan} disabled={busy}>
        <Text style={styles.buttonText}>{busy ? 'Connecting...' : 'Scan QR code'}</Text>
      </TouchableOpacity>

      <View style={styles.manualEntry}>
        <Text style={styles.manualLabel}>Or enter manually:</Text>
        <View style={styles.inputRow}>
          <Text style={styles.inputLabel}>IP Address</Text>
          <TextInput
            style={styles.input}
            value={ip}
            onChangeText={setIp}
            placeholder="192.168.x.x"
            placeholderTextColor="#475569"
            keyboardType="decimal-pad"
            autoCorrect={false}
          />
        </View>
        <View style={styles.inputRow}>
          <Text style={styles.inputLabel}>Port</Text>
          <TextInput
            style={styles.input}
            value={port}
            onChangeText={setPort}
            placeholder="8000"
            placeholderTextColor="#475569"
            keyboardType="numeric"
          />
        </View>
        <View style={styles.inputRow}>
          <Text style={styles.inputLabel}>Token</Text>
          <TextInput
            style={styles.input}
            value={token}
            onChangeText={setToken}
            placeholder="From the pairing page"
            placeholderTextColor="#475569"
            autoCapitalize="none"
            autoCorrect={false}
          />
        </View>
        <TouchableOpacity
          style={[styles.button, styles.secondaryButton, busy && styles.buttonDisabled]}
          onPress={handleManualPair}
          disabled={busy}
        >
          <Text style={styles.buttonText}>{busy ? 'Testing...' : 'Test & Pair'}</Text>
        </TouchableOpacity>
      </View>

      <TouchableOpacity style={styles.cancelButton} onPress={onCancel}>
        <Text style={styles.cancelButtonText}>Cancel</Text>
      </TouchableOpacity>
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0F172A' },
  content: { paddingHorizontal: 16, paddingBottom: 32 },
  header: { paddingVertical: 16, alignItems: 'flex-end' },
  closeButton: { padding: 8 },
  closeText: { fontSize: 24, color: '#94A3B8' },
  title: { fontSize: 28, fontWeight: '700', color: '#F8FAFC', textAlign: 'center', marginTop: 8 },
  subtitle: { fontSize: 16, color: '#64748B', textAlign: 'center', marginTop: 8, marginBottom: 24 },
  cancelButton: { alignSelf: 'center', marginTop: 16, paddingHorizontal: 24, paddingVertical: 12 },
  cancelButtonText: { color: '#94A3B8', fontSize: 16 },
  manualEntry: { marginTop: 32, paddingTop: 16, borderTopWidth: 1, borderTopColor: '#1E293B', gap: 12 },
  manualLabel: { color: '#94A3B8', fontSize: 14, textAlign: 'center' },
  inputRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  inputLabel: { color: '#E2E8F0', fontSize: 16, width: 80 },
  input: { flex: 1, backgroundColor: '#1E293B', color: '#F8FAFC', padding: 12, borderRadius: 8, fontSize: 16 },
  button: { backgroundColor: '#2563EB', paddingVertical: 14, borderRadius: 8, alignItems: 'center', marginTop: 8 },
  secondaryButton: { backgroundColor: '#1E40AF' },
  buttonDisabled: { backgroundColor: '#1E3A5F' },
  buttonText: { color: '#F8FAFC', fontSize: 16, fontWeight: '600' },
});
