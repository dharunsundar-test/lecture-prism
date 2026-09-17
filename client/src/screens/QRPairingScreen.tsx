import React, { useEffect, useRef, useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Alert, ActivityIndicator } from 'react-native';
import QRCodeScanner from 'react-native-qrcode-scanner';
import { transferService } from '../services/transfer';

interface QRPairingScreenProps {
  onPaired: (ip: string, port: number) => void;
  onCancel: () => void;
}

export const QRPairingScreen: React.FC<QRPairingScreenProps> = ({ onPaired, onCancel }) => {
  const [hasPermission, setHasPermission] = useState<boolean | null>(null);
  const [scanning, setScanning] = useState(true);
  const [flashOn, setFlashOn] = useState(false);
  const scannerRef = useRef<QRCodeScanner>(null);

  useEffect(() => {
    setHasPermission(null);
    setScanning(true);
  }, []);

  const onSuccess = (e: { data: string }) => {
    if (!scanning) return;

    try {
      const config = JSON.parse(e.data);
      if (config.ip && config.port) {
        setScanning(false);
        transferService.setPCConfig({ ip: config.ip, port: config.port, pairedAt: Date.now() });
        onPaired(config.ip, config.port);
      } else {
        Alert.alert('Invalid QR Code', 'QR code does not contain valid PC configuration');
        setScanning(true);
      }
    } catch {
      Alert.alert('Invalid QR Code', 'Could not parse QR code data');
      setScanning(true);
    }
  };

  const toggleFlash = () => {
    setFlashOn((prev) => !prev);
  };

  if (hasPermission === false) {
    return (
      <View style={styles.container}>
        <Text style={styles.errorText}>Camera permission denied</Text>
        <TouchableOpacity style={styles.button} onPress={onCancel}>
          <Text style={styles.buttonText}>Go Back</Text>
        </TouchableOpacity>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <TouchableOpacity style={styles.closeButton} onPress={onCancel}>
          <Text style={styles.closeText}>✕</Text>
        </TouchableOpacity>
      </View>

      <Text style={styles.title}>Pair with PC</Text>
      <Text style={styles.subtitle}>Scan the QR code shown on your desktop app</Text>

      <View style={styles.scannerContainer}>
        {hasPermission === null ? (
          <View style={styles.loadingContainer}>
            <ActivityIndicator size="large" />
            <Text style={styles.loadingText}>Requesting camera permission...</Text>
          </View>
        ) : (
          <QRCodeScanner
            ref={scannerRef}
            onRead={onSuccess}
            flashMode={flashOn ? 'on' : 'off'}
            topContent={
              <View style={styles.flashButtonContainer}>
                <TouchableOpacity style={styles.flashButton} onPress={toggleFlash}>
                  <Text style={styles.flashButtonText}>{flashOn ? 'Flash: ON' : 'Flash: OFF'}</Text>
                </TouchableOpacity>
              </View>
            }
            bottomContent={
              <TouchableOpacity style={styles.cancelButton} onPress={onCancel}>
                <Text style={styles.cancelButtonText}>Cancel</Text>
              </TouchableOpacity>
            }
            cameraStyle={styles.scanner}
            showMarker={true}
            customMarker={null}
          />
        )}
      </View>

      <View style={styles.manualEntry}>
        <Text style={styles.manualLabel}>Or enter manually:</Text>
        <ManualEntryForm onSubmit={onPaired} onCancel={onCancel} />
      </View>
    </View>
  );
};

const ManualEntryForm: React.FC<{
  onSubmit: (ip: string, port: number) => void;
  onCancel: () => void;
}> = ({ onSubmit, onCancel }) => {
  const [ip, setIp] = useState('');
  const [port, setPort] = useState('8000');
  const [testing, setTesting] = useState(false);

  const handleTest = async () => {
    if (!ip) return;
    setTesting(true);
    try {
      await transferService.setPCConfig({ ip, port: parseInt(port, 10), pairedAt: Date.now() });
      onSubmit(ip, parseInt(port, 10));
    } catch {
      Alert.alert('Connection Failed', 'Could not reach PC at this address');
    } finally {
      setTesting(false);
    }
  };

  return (
    <View style={styles.manualForm}>
      <View style={styles.inputRow}>
        <Text style={styles.inputLabel}>IP Address</Text>
        <TextInput
          style={styles.input}
          value={ip}
          onChangeText={setIp}
          placeholder="192.168.x.x"
          keyboardType="decimal-pad"
        />
      </View>
      <View style={styles.inputRow}>
        <Text style={styles.inputLabel}>Port</Text>
        <TextInput
          style={styles.input}
          value={port}
          onChangeText={setPort}
          placeholder="8000"
          keyboardType="numeric"
        />
      </View>
      <TouchableOpacity style={[styles.button, testing && styles.buttonDisabled]} onPress={handleTest} disabled={testing}>
        <Text style={styles.buttonText}>{testing ? 'Testing...' : 'Test & Pair'}</Text>
      </TouchableOpacity>
    </View>
  );
};

import { TextInput } from 'react-native';

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0F172A' },
  header: { padding: 16, alignItems: 'flex-end' },
  closeButton: { padding: 8 },
  closeText: { fontSize: 24, color: '#94A3B8' },
  title: { fontSize: 28, fontWeight: '700', color: '#F8FAFC', textAlign: 'center', marginTop: 8 },
  subtitle: { fontSize: 16, color: '#64748B', textAlign: 'center', marginBottom: 24 },
  scannerContainer: { flex: 1, marginHorizontal: 16, borderRadius: 12, overflow: 'hidden' },
  scanner: { width: '100%', height: '100%' },
  flashButtonContainer: { alignItems: 'center', padding: 12 },
  flashButton: { backgroundColor: 'rgba(15, 23, 42, 0.8)', paddingHorizontal: 16, paddingVertical: 8, borderRadius: 20 },
  flashButtonText: { color: '#F8FAFC', fontSize: 14 },
  cancelButton: { alignSelf: 'center', marginBottom: 16, paddingHorizontal: 24, paddingVertical: 12 },
  cancelButtonText: { color: '#94A3B8', fontSize: 16 },
  loadingContainer: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  loadingText: { color: '#94A3B8', marginTop: 12, fontSize: 16 },
  manualEntry: { padding: 16, borderTopWidth: 1, borderTopColor: '#1E293B' },
  manualLabel: { color: '#94A3B8', fontSize: 14, marginBottom: 12, textAlign: 'center' },
  manualForm: { gap: 12 },
  inputRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  inputLabel: { color: '#E2E8F0', fontSize: 16, width: 80 },
  input: { flex: 1, backgroundColor: '#1E293B', color: '#F8FAFC', padding: 12, borderRadius: 8, fontSize: 16 },
  button: { backgroundColor: '#2563EB', paddingVertical: 14, borderRadius: 8, alignItems: 'center', marginTop: 8 },
  buttonDisabled: { backgroundColor: '#1E3A5F' },
  buttonText: { color: '#F8FAFC', fontSize: 16, fontWeight: '600' },
  errorText: { color: '#EF4444', fontSize: 16, textAlign: 'center', marginBottom: 16 },
});